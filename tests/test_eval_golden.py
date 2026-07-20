"""Schema-validation of the committed golden set (offline)."""

from __future__ import annotations

from pathlib import Path

from contact_center._internal import eval_runner

_GOLDEN = str(Path(__file__).parent.parent / "docs" / "eval" / "golden.json")


def test_golden_set_loads_and_validates() -> None:
    items = eval_runner.load_golden(_GOLDEN)
    assert len(items) >= 12
    ids = [i.id for i in items]
    assert len(ids) == len(set(ids))


def test_golden_set_covers_all_categories() -> None:
    items = eval_runner.load_golden(_GOLDEN)
    covered = {i.category for i in items}
    assert covered == eval_runner.CATEGORIES


def test_golden_balance_items_cover_all_synthetic_customers() -> None:
    items = eval_runner.load_golden(_GOLDEN)
    balance_customers = {i.customer_id for i in items if i.category == "balance"}
    assert {"KND-1001", "KND-1002", "KND-1003"} <= balance_customers


def test_golden_reason_tokens_are_valid() -> None:
    items = eval_runner.load_golden(_GOLDEN)
    for item in items:
        if item.expect_reason is not None:
            assert item.expect_reason in eval_runner.REASON_TOKENS
