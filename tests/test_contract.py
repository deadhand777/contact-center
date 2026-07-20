"""Tests for the supervisor response contract parser (loaded by file path)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

_CONTRACT_PATH = Path(__file__).parent.parent / "contactcenter" / "app" / "knowledge_agent" / "contract.py"


def _load_contract() -> ModuleType:
    """Load the contract module without installing the agent project."""
    spec = importlib.util.spec_from_file_location("contract", _CONTRACT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_valid_contract_json_is_parsed() -> None:
    """Well-formed contract JSON maps straight through."""
    contract = _load_contract()
    result = contract.parse_supervisor_output(
        '{"answer": "Ihr Kontostand beträgt 890,00 €.", "escalate": false, "reason": null}',
    )
    assert result == {"answer": "Ihr Kontostand beträgt 890,00 €.", "escalate": False, "reason": None}


def test_json_wrapped_in_prose_is_extracted() -> None:
    """JSON embedded in surrounding text (or code fences) is still found."""
    contract = _load_contract()
    text = 'Hier ist das Ergebnis:\n```json\n{"answer": "Ok.", "escalate": true, "reason": "Kundenwunsch"}\n```'
    result = contract.parse_supervisor_output(text)
    assert result["escalate"] is True
    assert result["reason"] == "Kundenwunsch"


def test_plain_text_falls_back_without_escalation() -> None:
    """Unparseable output becomes a plain answer with escalate false."""
    contract = _load_contract()
    result = contract.parse_supervisor_output("Das Girokonto kostet 4,90 €.")
    assert result == {"answer": "Das Girokonto kostet 4,90 €.", "escalate": False, "reason": None}


def test_missing_answer_key_falls_back() -> None:
    """A JSON object without an answer key is treated as plain text."""
    contract = _load_contract()
    result = contract.parse_supervisor_output('{"escalate": true}')
    assert result["answer"] == '{"escalate": true}'
    assert result["escalate"] is False


def test_escalation_log_record_omits_answer() -> None:
    """The log record carries correlation fields only, never the answer text."""
    contract = _load_contract()
    record = contract.escalation_log_record(
        "connect-abc", "KND-1001",
        {"answer": "Ihr Saldo ist 2.543,17 €", "escalate": False, "reason": None},
    )
    assert record == {
        "session_id": "connect-abc", "customer_id": "KND-1001",
        "escalate": False, "reason": None,
    }
    assert "2.543,17" not in json.dumps(record, ensure_ascii=False)
