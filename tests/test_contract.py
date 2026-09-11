"""Tests for the supervisor response contract (loaded by file path)."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from tests import load_module
from tests.contract_cases import INVALID_CONTRACT_PAYLOADS, VALID_CONTRACT_PAYLOADS


@pytest.mark.parametrize("payload", VALID_CONTRACT_PAYLOADS)
def test_valid_contract_payload_is_preserved(payload: dict[str, object]) -> None:
    """Every canonical valid payload survives strict construction."""
    contract = load_module("contract")
    response = contract.SupervisorResponse.from_supervisor_output(json.dumps(payload))
    assert response.to_payload() == payload


def test_json_wrapped_in_prose_is_extracted() -> None:
    """A valid object embedded in model framing remains accepted."""
    contract = load_module("contract")
    text = 'Hier ist das Ergebnis:\n```json\n{"answer": "Ok.", "escalate": true, "reason": "Kundenwunsch"}\n```'
    response = contract.SupervisorResponse.from_supervisor_output(text)
    assert response.to_payload() == {"answer": "Ok.", "escalate": True, "reason": "Kundenwunsch"}


@pytest.mark.parametrize("payload", INVALID_CONTRACT_PAYLOADS)
def test_invalid_contract_payload_fails_toward_human(payload: dict[str, object]) -> None:
    """Every canonical invalid payload becomes the fixed safe fallback."""
    contract = load_module("contract")
    response = contract.SupervisorResponse.from_supervisor_output(json.dumps(payload))
    assert response.to_payload() == {
        "answer": contract.FALLBACK_ANSWER,
        "escalate": True,
        "reason": "Keine gesicherte Antwort möglich",
    }


@pytest.mark.parametrize("text", ["not json", "[]", "null", "```json\n{broken}\n```"])
def test_non_contract_text_fails_toward_human(text: str) -> None:
    """Malformed or non-object model output never reaches the customer."""
    contract = load_module("contract")
    response = contract.SupervisorResponse.from_supervisor_output(text)
    assert response.to_payload()["answer"] == contract.FALLBACK_ANSWER
    assert response.escalate is True


def test_invalid_direct_construction_is_rejected() -> None:
    """Callers cannot construct an invalid contract value."""
    contract = load_module("contract")
    with pytest.raises(ValueError, match="reason must be null"):
        contract.SupervisorResponse(answer="Antwort", escalate=False, reason="Kundenwunsch")


def test_response_is_immutable() -> None:
    """A validated response cannot drift after construction."""
    contract = load_module("contract")
    response = contract.SupervisorResponse(answer="Antwort", escalate=False, reason=None)
    with pytest.raises(FrozenInstanceError):
        setattr(response, "answer", "Geändert")  # noqa: B010 -- must go through setattr to hit frozen-dataclass enforcement


def test_log_projection_omits_answer() -> None:
    """The log projection carries correlation fields but no customer answer."""
    contract = load_module("contract")
    response = contract.SupervisorResponse(answer="Ihr Saldo ist 2.543,17 €", escalate=False, reason=None)
    record = response.to_log_record("connect-abc", "KND-1001")
    assert record == {
        "session_id": "connect-abc",
        "customer_id": "KND-1001",
        "escalate": False,
        "reason": None,
    }
    assert "2.543,17" not in json.dumps(record, ensure_ascii=False)


def test_guardrail_refusal_reaches_the_customer() -> None:
    """A guardrail's blocked message is an answer, not a contract violation."""
    contract = load_module("contract")
    blocked = "Diese Anfrage kann ich aus Compliance-Gründen nicht bearbeiten. Ich verbinde Sie gerne mit einem Mitarbeiter."
    response = contract.SupervisorResponse.guardrail_refusal(blocked)
    assert response.to_payload() == {"answer": blocked, "escalate": False, "reason": None}


def test_empty_guardrail_message_falls_back_to_human() -> None:
    """An empty blocked message cannot be shown, so it escalates."""
    contract = load_module("contract")
    response = contract.SupervisorResponse.guardrail_refusal("   ")
    assert response.to_payload() == {
        "answer": contract.FALLBACK_ANSWER,
        "escalate": True,
        "reason": "Keine gesicherte Antwort möglich",
    }


def test_rejection_is_logged_without_answer_text(caplog: pytest.LogCaptureFixture) -> None:
    """A rejected response logs its cause and field names, never the model text."""
    contract = load_module("contract")
    text = '{"answer": "Ihr Saldo ist -127,45 €", "escalate": false, "reason": ""}'
    with caplog.at_level("WARNING"):
        response = contract.SupervisorResponse.from_supervisor_output(text)
    assert response.escalate is True
    record = json.loads(caplog.records[0].message)
    assert record["event"] == "contract_rejected"
    assert record["fields"] == ["answer", "escalate", "reason"]
    assert "reason must be null" in record["cause"]
    assert "-127,45" not in caplog.text
