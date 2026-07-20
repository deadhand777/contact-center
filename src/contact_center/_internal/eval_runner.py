"""Offline-scored eval harness for the deployed contact-center agent."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import boto3

from contact_center._internal import aws, chat

CATEGORIES: frozenset[str] = frozenset({"knowledge", "balance", "escalation", "guardrail"})
REASON_TOKENS: frozenset[str] = frozenset({
    "Kundenwunsch",
    "Sensibles Thema Kreditablehnung",
    "Systemfehler Kontodienst",
    "Kunde nicht identifiziert",
    "Keine gesicherte Antwort möglich",
})
DEFAULT_GOLDEN_PATH = "docs/eval/golden.json"

_CITATION_RE = re.compile(r"\[Quelle:[^\]]+\]")
# A US-style two-place decimal (period as decimal separator): "4.90", "0.50", "2543.17".
# Grouped German thousands like "2.543" are period-then-3-digits, so the
# exactly-two-digits guard skips them. The lookahead `(?![\d.])` also rejects a
# following period, so German dates ("01.07.2026" -> "01.07.") don't match.
# Known limitation: German clock times ("14.30 Uhr") still match; acceptable
# because the golden set contains no such values.
_US_DECIMAL_RE = re.compile(r"(?<!\d)\d+\.\d{2}(?![\d.])")


@dataclass(frozen=True)
class GoldenItem:
    """One golden question and its declared deterministic expectations."""

    id: str
    category: str
    prompt: str
    customer_id: str | None = None
    expected_facts: tuple[str, ...] = ()
    needs_citation: bool = False
    expect_escalate: bool = False
    expect_reason: str | None = None
    expect_refusal: bool = False
    refusal_markers: tuple[str, ...] = ()
    forbidden_substrings: tuple[str, ...] = ()


def _coerce_item(raw: dict) -> GoldenItem:
    """Validate one raw dict and build a GoldenItem.

    Raises:
        ValueError: With a field-specific message on any schema violation.
    """
    for required in ("id", "category", "prompt"):
        if not raw.get(required):
            raise ValueError(f"golden item missing required field: {required} ({raw!r})")
    if raw["category"] not in CATEGORIES:
        raise ValueError(f"unknown category {raw['category']!r} (allowed: {sorted(CATEGORIES)})")
    reason = raw.get("expect_reason")
    if reason is not None and reason not in REASON_TOKENS:
        raise ValueError(f"unknown reason token {reason!r} (allowed: {sorted(REASON_TOKENS)})")
    allowed = {f.name for f in fields(GoldenItem)}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown golden field(s): {sorted(unknown)}")
    return GoldenItem(
        id=raw["id"],
        category=raw["category"],
        prompt=raw["prompt"],
        customer_id=raw.get("customer_id"),
        expected_facts=tuple(raw.get("expected_facts", ())),
        needs_citation=bool(raw.get("needs_citation", False)),
        expect_escalate=bool(raw.get("expect_escalate", False)),
        expect_reason=reason,
        expect_refusal=bool(raw.get("expect_refusal", False)),
        refusal_markers=tuple(raw.get("refusal_markers", ())),
        forbidden_substrings=tuple(raw.get("forbidden_substrings", ())),
    )


def load_golden(path: str) -> list[GoldenItem]:
    """Load and validate the golden set.

    Parameters:
        path: Path to the golden-set JSON (a list of item objects).

    Returns:
        The parsed golden items.

    Raises:
        ValueError: On a malformed file, a duplicate id, or any item that
            violates the schema.
    """
    with Path(path).open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError("golden set must be a JSON list of items")  # noqa: TRY004
    items = [_coerce_item(raw) for raw in data]
    seen: set[str] = set()
    for item in items:
        if item.id in seen:
            raise ValueError(f"duplicate golden item id: {item.id}")
        seen.add(item.id)
    return items


@dataclass(frozen=True)
class ItemResult:
    """The scored outcome of one golden item."""

    item_id: str
    category: str
    checks: dict[str, bool]
    outcome: str
    error: str | None = None


def check_item(item: GoldenItem, response: dict) -> ItemResult:
    """Score one agent response against a golden item's declared expectations.

    Parameters:
        item: The golden item.
        response: The response contract `{"answer","escalate","reason"}`.

    Returns:
        An `ItemResult` whose `outcome` is `"pass"` iff every active check
        passes. `escalate_flag`, `reason_token`, and `number_format` are always
        active; `expected_facts`, `citation`, and `refusal` are active only when
        the item declares them.
    """
    answer = str(response.get("answer", ""))
    checks: dict[str, bool] = {
        "number_format": not _US_DECIMAL_RE.search(answer),
        "escalate_flag": bool(response.get("escalate")) == item.expect_escalate,
        "reason_token": response.get("reason") == item.expect_reason,
    }
    if item.expected_facts:
        checks["expected_facts"] = all(fact in answer for fact in item.expected_facts)
    if item.needs_citation:
        checks["citation"] = bool(_CITATION_RE.search(answer))
    if item.expect_refusal:
        has_marker = any(marker in answer for marker in item.refusal_markers)
        has_forbidden = any(bad in answer for bad in item.forbidden_substrings)
        checks["refusal"] = has_marker and not has_forbidden
    outcome = "pass" if all(checks.values()) else "fail"
    return ItemResult(item_id=item.id, category=item.category, checks=checks, outcome=outcome)


def pass_rate(results: list[ItemResult]) -> float:
    """Fraction of items whose outcome is 'pass' (0.0 for an empty list)."""
    if not results:
        return 0.0
    return sum(1 for r in results if r.outcome == "pass") / len(results)


def format_report(results: list[ItemResult]) -> str:
    """Render a per-dimension pass-rate report with an overall summary line.

    Parameters:
        results: The scored items.

    Returns:
        A multi-line string: one line per (category, check) with pass counts
        and a list of failing item ids, plus errors and an OVERALL summary.
    """
    lines: list[str] = ["Eval report", "==========="]
    counts: dict[tuple[str, str], list[int]] = {}
    for result in results:
        for name, passed in result.checks.items():
            bucket = counts.setdefault((result.category, name), [0, 0])
            bucket[1] += 1
            if passed:
                bucket[0] += 1
    for (category, name) in sorted(counts):
        passed, total = counts[(category, name)]
        lines.append(f"  {category:<11} {name:<14} {passed}/{total}")
    failures = [r.item_id for r in results if r.outcome == "fail"]
    errors = [f"{r.item_id}: {r.error}" for r in results if r.outcome == "error"]
    if failures:
        lines.append(f"FAILED items: {', '.join(failures)}")
    if errors:
        lines.append(f"ERRORED items: {'; '.join(errors)}")
    passed = sum(1 for r in results if r.outcome == "pass")
    total = len(results)
    pct = round(pass_rate(results) * 100, 1)
    lines.append(f"OVERALL: {passed}/{total} passed ({pct}%)")
    return "\n".join(lines)


def _resolve_live() -> tuple[Any, str]:
    """Resolve a live client + runtime ARN, gated by RUN_EVAL.

    Raises:
        SystemExit: When the gate is unset or the runtime ARN is unavailable.
    """
    if os.environ.get("RUN_EVAL") != "1":
        raise SystemExit("Live eval is gated — set RUN_EVAL=1 to run against the deployed runtime.")
    try:
        runtime_arn = aws.get_parameter(aws.RUNTIME_ARN_PARAM)
    except Exception as error:  # boundary: turn any config failure into a named exit
        raise SystemExit(
            f"Cannot read {aws.RUNTIME_ARN_PARAM} — deploy the agent and publish its runtime ARN.",
        ) from error
    return boto3.client("bedrock-agentcore", region_name=aws.REGION), runtime_arn


def run_eval(
    golden_path: str | None = None,
    *,
    threshold: float = 1.0,
    client: Any = None,
    runtime_arn: str | None = None,
) -> int:
    """Run the golden set against the deployed agent and print a scored report.

    Parameters:
        golden_path: Path to the golden set (defaults to DEFAULT_GOLDEN_PATH).
        threshold: Minimum item pass-rate; the run exits nonzero below it.
        client: Injected `bedrock-agentcore` client (tests); when None the live
            path resolves a real client, gated by RUN_EVAL=1.
        runtime_arn: Injected runtime ARN (tests); resolved from SSM when None
            on the live path.

    Returns:
        0 when `pass_rate(results) >= threshold`, else 1.
    """
    items = load_golden(golden_path or DEFAULT_GOLDEN_PATH)
    if client is None or runtime_arn is None:
        client, runtime_arn = _resolve_live()
    results: list[ItemResult] = []
    for item in items:
        session_id = uuid.uuid4().hex + uuid.uuid4().hex[:8]
        try:
            response = chat.ask(
                item.prompt,
                runtime_arn=runtime_arn,
                session_id=session_id,
                client=client,
                customer_id=item.customer_id,
            )
        except Exception as error:  # noqa: BLE001 — isolate one item's failure from the run
            results.append(ItemResult(item.id, item.category, {}, "error", error=str(error)))
            continue
        results.append(check_item(item, response))
    print(format_report(results))  # noqa: T201
    return 0 if pass_rate(results) >= threshold else 1
