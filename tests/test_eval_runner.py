"""Offline unit tests for the eval harness."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from contact_center._internal import eval_runner

if TYPE_CHECKING:
    from pathlib import Path


def _write_golden(tmp_path: Path, items: list[dict]) -> str:
    path = tmp_path / "golden.json"
    path.write_text(json.dumps(items), encoding="utf-8")
    return str(path)


def test_load_golden_parses_all_fields(tmp_path: Path) -> None:
    path = _write_golden(tmp_path, [
        {
            "id": "kb-1", "category": "knowledge", "prompt": "Gebühr?",
            "expected_facts": ["4,90"], "needs_citation": True,
        },
    ])
    items = eval_runner.load_golden(path)
    assert len(items) == 1
    item = items[0]
    assert item.id == "kb-1"
    assert item.category == "knowledge"
    assert item.expected_facts == ("4,90",)
    assert item.needs_citation is True
    assert item.customer_id is None
    assert item.expect_escalate is False


def test_load_golden_rejects_unknown_category(tmp_path: Path) -> None:
    path = _write_golden(tmp_path, [{"id": "x", "category": "weather", "prompt": "?"}])
    with pytest.raises(ValueError, match="category"):
        eval_runner.load_golden(path)


def test_load_golden_rejects_unknown_reason_token(tmp_path: Path) -> None:
    path = _write_golden(tmp_path, [
        {"id": "x", "category": "escalation", "prompt": "?", "expect_reason": "Nope"},
    ])
    with pytest.raises(ValueError, match="reason"):
        eval_runner.load_golden(path)


def test_load_golden_rejects_missing_required_field(tmp_path: Path) -> None:
    path = _write_golden(tmp_path, [{"category": "knowledge", "prompt": "?"}])
    with pytest.raises(ValueError, match="id"):
        eval_runner.load_golden(path)


def test_load_golden_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = _write_golden(tmp_path, [
        {"id": "dup", "category": "knowledge", "prompt": "a"},
        {"id": "dup", "category": "knowledge", "prompt": "b"},
    ])
    with pytest.raises(ValueError, match="duplicate"):
        eval_runner.load_golden(path)


def test_load_golden_rejects_unknown_field(tmp_path: Path) -> None:
    path = _write_golden(tmp_path, [{"id": "x", "category": "knowledge", "prompt": "?", "bogus": 1}])
    with pytest.raises(ValueError, match="unknown golden field"):
        eval_runner.load_golden(path)


def test_load_golden_rejects_non_list(tmp_path: Path) -> None:
    path = tmp_path / "golden.json"
    path.write_text('{"id": "x"}', encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON list"):
        eval_runner.load_golden(str(path))


def _item(**kw: Any) -> eval_runner.GoldenItem:
    base: dict[str, Any] = {"id": "t", "category": "knowledge", "prompt": "?"}
    base.update(kw)
    return eval_runner.GoldenItem(**base)  # type: ignore[arg-type]


def test_check_knowledge_all_pass() -> None:
    item = _item(expected_facts=("4,90",), needs_citation=True)
    resp = {"answer": "Das kostet 4,90 € [Quelle: girokonto-gebuehren.md]", "escalate": False, "reason": None}
    result = eval_runner.check_item(item, resp)
    assert result.outcome == "pass"
    assert result.checks == {
        "expected_facts": True, "citation": True, "number_format": True,
        "escalate_flag": True, "reason_token": True,
    }


def test_check_missing_fact_fails() -> None:
    item = _item(expected_facts=("4,90",))
    resp = {"answer": "Das kostet 5,90 €", "escalate": False, "reason": None}
    result = eval_runner.check_item(item, resp)
    assert result.outcome == "fail"
    assert result.checks["expected_facts"] is False


def test_check_missing_citation_fails() -> None:
    item = _item(expected_facts=("4,90",), needs_citation=True)
    resp = {"answer": "Das kostet 4,90 €", "escalate": False, "reason": None}
    result = eval_runner.check_item(item, resp)
    assert result.checks["citation"] is False
    assert result.outcome == "fail"


def test_check_number_format_rejects_us_decimal() -> None:
    item = _item()
    resp = {"answer": "Das kostet 4.90 EUR", "escalate": False, "reason": None}
    result = eval_runner.check_item(item, resp)
    assert result.checks["number_format"] is False


def test_check_number_format_accepts_grouped_german() -> None:
    item = _item()
    resp = {"answer": "Saldo 2.543,17 und 15.000,00", "escalate": False, "reason": None}
    result = eval_runner.check_item(item, resp)
    assert result.checks["number_format"] is True


def test_check_number_format_rejects_long_us_decimal() -> None:
    item = _item()
    resp = {"answer": "Saldo 2543.17 EUR", "escalate": False, "reason": None}
    result = eval_runner.check_item(item, resp)
    assert result.checks["number_format"] is False


def test_check_number_format_ignores_german_date() -> None:
    item = _item()
    resp = {"answer": "Gültig ab 01.07.2026 kostet es 4,90 €", "escalate": False, "reason": None}
    result = eval_runner.check_item(item, resp)
    assert result.checks["number_format"] is True


def test_check_number_format_still_rejects_us_money() -> None:
    item = _item()
    resp = {"answer": "Das kostet 4.90 EUR", "escalate": False, "reason": None}
    result = eval_runner.check_item(item, resp)
    assert result.checks["number_format"] is False


def test_check_escalation_flag_and_reason() -> None:
    item = _item(category="escalation", expect_escalate=True, expect_reason="Kundenwunsch")
    resp = {"answer": "Ich verbinde Sie.", "escalate": True, "reason": "Kundenwunsch"}
    result = eval_runner.check_item(item, resp)
    assert result.outcome == "pass"
    assert result.checks["escalate_flag"] is True
    assert result.checks["reason_token"] is True


def test_check_wrong_reason_token_fails() -> None:
    item = _item(category="escalation", expect_escalate=True, expect_reason="Kundenwunsch")
    resp = {"answer": "…", "escalate": True, "reason": "Systemfehler Kontodienst"}
    result = eval_runner.check_item(item, resp)
    assert result.checks["reason_token"] is False


def test_check_refusal_pass_and_fail() -> None:
    item = _item(
        category="guardrail", expect_refusal=True,
        refusal_markers=("keine Anlageberatung",), forbidden_substrings=("kaufen Sie",),
    )
    good = {"answer": "Wir bieten keine Anlageberatung an.", "escalate": False, "reason": None}
    bad = {"answer": "kaufen Sie Aktie X", "escalate": False, "reason": None}
    assert eval_runner.check_item(item, good).checks["refusal"] is True
    assert eval_runner.check_item(item, bad).checks["refusal"] is False


def test_pass_rate() -> None:
    results = [
        eval_runner.ItemResult("a", "knowledge", {"number_format": True}, "pass"),
        eval_runner.ItemResult("b", "knowledge", {"number_format": False}, "fail"),
    ]
    assert eval_runner.pass_rate(results) == 0.5
    assert eval_runner.pass_rate([]) == 0.0


def test_format_report_contains_overall_and_dimensions() -> None:
    results = [
        eval_runner.ItemResult("a", "knowledge", {"expected_facts": True, "citation": True}, "pass"),
        eval_runner.ItemResult("b", "balance", {"expected_facts": False}, "fail"),
    ]
    report = eval_runner.format_report(results)
    assert "OVERALL: 1/2 passed (50" in report
    assert "knowledge" in report
    assert "balance" in report
    assert "expected_facts" in report


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeClient:
    """Fake bedrock-agentcore client returning canned contract payloads by prompt substring."""

    def __init__(self, answers: dict[str, dict]) -> None:
        self._answers = answers
        self.calls: list[dict] = []

    def invoke_agent_runtime(self, **kwargs: Any) -> dict:
        self.calls.append(kwargs)
        payload = json.loads(kwargs["payload"])
        for needle, contract in self._answers.items():
            if needle in payload["prompt"]:
                return {"response": _FakeBody(json.dumps(contract).encode())}
        return {"response": _FakeBody(json.dumps({"answer": "?", "escalate": False, "reason": None}).encode())}


def test_run_eval_gated_without_run_eval_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUN_EVAL", raising=False)
    path = _write_golden(tmp_path, [{"id": "kb", "category": "knowledge", "prompt": "Gebühr?"}])
    with pytest.raises(SystemExit, match="gated"):
        eval_runner.run_eval(path)


def test_run_eval_fails_closed_on_missing_runtime_arn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUN_EVAL", "1")

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("no such parameter")

    monkeypatch.setattr(eval_runner.aws, "get_parameter", _boom)
    path = _write_golden(tmp_path, [{"id": "kb", "category": "knowledge", "prompt": "Gebühr?"}])
    with pytest.raises(SystemExit, match="Cannot read"):
        eval_runner.run_eval(path)


def test_run_eval_all_pass_returns_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _write_golden(tmp_path, [
        {"id": "kb", "category": "knowledge", "prompt": "Gebühr?", "expected_facts": ["4,90"], "needs_citation": True},
    ])
    client = _FakeClient({"Gebühr": {"answer": "4,90 € [Quelle: x.md]", "escalate": False, "reason": None}})
    code = eval_runner.run_eval(path, threshold=1.0, client=client, runtime_arn="arn:runtime")
    assert code == 0
    assert "OVERALL: 1/1 passed" in capsys.readouterr().out


def test_run_eval_below_threshold_returns_one(tmp_path: Path) -> None:
    path = _write_golden(tmp_path, [
        {"id": "kb", "category": "knowledge", "prompt": "Gebühr?", "expected_facts": ["4,90"]},
    ])
    client = _FakeClient({"Gebühr": {"answer": "5,90 €", "escalate": False, "reason": None}})
    code = eval_runner.run_eval(path, threshold=1.0, client=client, runtime_arn="arn:runtime")
    assert code == 1


def test_run_eval_isolates_item_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _write_golden(tmp_path, [
        {"id": "boom", "category": "knowledge", "prompt": "explode", "expected_facts": ["4,90"]},
        {"id": "ok", "category": "knowledge", "prompt": "Gebühr?", "expected_facts": ["4,90"]},
    ])

    class _PartlyBroken:
        def invoke_agent_runtime(self, **kwargs: Any) -> dict:
            prompt = json.loads(kwargs["payload"])["prompt"]
            if "explode" in prompt:
                raise RuntimeError("network down")
            return {"response": _FakeBody(json.dumps({"answer": "4,90 €", "escalate": False, "reason": None}).encode())}

    code = eval_runner.run_eval(path, threshold=1.0, client=_PartlyBroken(), runtime_arn="arn:runtime")
    out = capsys.readouterr().out
    assert code == 1                      # boom errored -> below threshold
    assert "ERRORED items: boom" in out
    assert "OVERALL: 1/2 passed" in out   # proves 'ok' still ran and passed after boom errored


def test_run_eval_binds_customer_id(tmp_path: Path) -> None:
    path = _write_golden(tmp_path, [
        {"id": "bal", "category": "balance", "prompt": "Kontostand?", "customer_id": "KND-1002", "expected_facts": ["-127,45"]},
    ])
    client = _FakeClient({"Kontostand": {"answer": "-127,45 €", "escalate": False, "reason": None}})
    eval_runner.run_eval(path, threshold=1.0, client=client, runtime_arn="arn:runtime")
    assert json.loads(client.calls[0]["payload"])["customer_id"] == "KND-1002"
