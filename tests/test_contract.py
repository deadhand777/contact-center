"""Tests for the supervisor response contract (loaded by file path)."""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.contract_cases import INVALID_CONTRACT_PAYLOADS, VALID_CONTRACT_PAYLOADS

if TYPE_CHECKING:
    from types import ModuleType

_CONTRACT_PATH = Path(__file__).parent.parent / "contactcenter" / "app" / "knowledge_agent" / "contract.py"


def _load_contract() -> ModuleType:
    """Load the contract module without installing the agent project."""
    spec = importlib.util.spec_from_file_location("contract", _CONTRACT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("payload", VALID_CONTRACT_PAYLOADS)
def test_valid_contract_payload_is_preserved(payload: dict[str, object]) -> None:
    """Every canonical valid payload survives strict construction."""
    contract = _load_contract()
    response = contract.SupervisorResponse.from_supervisor_output(json.dumps(payload))
    assert response.to_payload() == payload


def test_json_wrapped_in_prose_is_extracted() -> None:
    """A valid object embedded in model framing remains accepted."""
    contract = _load_contract()
    text = 'Hier ist das Ergebnis:\n```json\n{"answer": "Ok.", "escalate": true, "reason": "Kundenwunsch"}\n```'
    response = contract.SupervisorResponse.from_supervisor_output(text)
    assert response.to_payload() == {"answer": "Ok.", "escalate": True, "reason": "Kundenwunsch"}


@pytest.mark.parametrize("payload", INVALID_CONTRACT_PAYLOADS)
def test_invalid_contract_payload_fails_toward_human(payload: dict[str, object]) -> None:
    """Every canonical invalid payload becomes the fixed safe fallback."""
    contract = _load_contract()
    response = contract.SupervisorResponse.from_supervisor_output(json.dumps(payload))
    assert response.to_payload() == {
        "answer": contract.FALLBACK_ANSWER,
        "escalate": True,
        "reason": "Keine gesicherte Antwort möglich",
    }


@pytest.mark.parametrize("text", ["not json", "[]", "null", "```json\n{broken}\n```"])
def test_non_contract_text_fails_toward_human(text: str) -> None:
    """Malformed or non-object model output never reaches the customer."""
    contract = _load_contract()
    response = contract.SupervisorResponse.from_supervisor_output(text)
    assert response.to_payload()["answer"] == contract.FALLBACK_ANSWER
    assert response.escalate is True


def test_invalid_direct_construction_is_rejected() -> None:
    """Callers cannot construct an invalid contract value."""
    contract = _load_contract()
    with pytest.raises(ValueError, match="reason must be null"):
        contract.SupervisorResponse(answer="Antwort", escalate=False, reason="Kundenwunsch")


def test_response_is_immutable() -> None:
    """A validated response cannot drift after construction."""
    contract = _load_contract()
    response = contract.SupervisorResponse(answer="Antwort", escalate=False, reason=None)
    with pytest.raises(FrozenInstanceError):
        setattr(response, "answer", "Geändert")  # noqa: B010 -- must go through setattr to hit frozen-dataclass enforcement


def test_log_projection_omits_answer() -> None:
    """The log projection carries correlation fields but no customer answer."""
    contract = _load_contract()
    response = contract.SupervisorResponse(answer="Ihr Saldo ist 2.543,17 €", escalate=False, reason=None)
    record = response.to_log_record("connect-abc", "KND-1001")
    assert record == {
        "session_id": "connect-abc",
        "customer_id": "KND-1001",
        "escalate": False,
        "reason": None,
    }
    assert "2.543,17" not in json.dumps(record, ensure_ascii=False)
