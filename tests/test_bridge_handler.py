"""Tests for the Lex V2 bridge Lambda handler (loaded by file path)."""

from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pytest

_HANDLER_PATH = Path(__file__).parent.parent / "infra" / "lambda" / "bridge" / "handler.py"


def _load_handler() -> Any:
    """Load the bridge handler without packaging it (SSM read is lazy)."""
    spec = importlib.util.spec_from_file_location("bridge_handler", _HANDLER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeBody:
    """Fake StreamingBody."""

    def __init__(self, data: bytes) -> None:
        """Store payload bytes."""
        self._data = data

    def read(self) -> bytes:
        """Return payload bytes."""
        return self._data


class _FakeRuntime:
    """Fake bedrock-agentcore client recording calls."""

    def __init__(self, payload: dict | Exception) -> None:
        """Store the canned response or exception."""
        self._payload = payload
        self.calls: list[dict] = []

    def invoke_agent_runtime(self, **kwargs: object) -> dict:
        """Record the call; return canned response or raise."""
        self.calls.append(kwargs)
        if isinstance(self._payload, Exception):
            raise self._payload
        return {"response": _FakeBody(json.dumps(self._payload).encode())}


def _lex_event(text: str, customer_id: str | None = "KND-1001") -> dict[str, Any]:
    """Build a Lex V2 dialog code hook event, as Connect sends it per utterance."""
    session_attributes = {"customer_id": customer_id} if customer_id else {}
    return {
        "sessionId": "11111111-2222-3333-4444-555555555555",
        "inputTranscript": text,
        "invocationSource": "DialogCodeHook",
        "sessionState": {
            "sessionAttributes": session_attributes,
            "intent": {"name": "FallbackIntent", "state": "InProgress"},
        },
    }


def test_answer_turn_maps_contract_to_elicit_intent() -> None:
    """A normal answer becomes ElicitIntent with escalate false in session attributes."""
    module = _load_handler()
    module._agentcore = _FakeRuntime({"answer": "2.543,17 EUR", "escalate": False, "reason": None})
    module._RUNTIME_ARN = "arn:runtime"
    result = module.handler(_lex_event("Kontostand?"), None)
    assert result["sessionState"]["dialogAction"] == {"type": "ElicitIntent"}
    assert result["sessionState"]["sessionAttributes"] == {
        "customer_id": "KND-1001",
        "escalate": "false",
        "reason": "",
    }
    assert result["messages"] == [{"contentType": "PlainText", "content": "2.543,17 EUR"}]
    sent = module._agentcore.calls[0]
    assert sent["runtimeSessionId"] == "connect-11111111-2222-3333-4444-555555555555"
    assert json.loads(sent["payload"]) == {"prompt": "Kontostand?", "customer_id": "KND-1001"}


def test_escalation_turn_closes_with_fulfilled_intent() -> None:
    """Escalations close the dialog, mark the intent fulfilled, and stringify reason."""
    module = _load_handler()
    module._agentcore = _FakeRuntime({"answer": "Ich verbinde Sie.", "escalate": True, "reason": "Kundenwunsch"})
    module._RUNTIME_ARN = "arn:runtime"
    result = module.handler(_lex_event("Mensch bitte"), None)
    assert result["sessionState"]["dialogAction"] == {"type": "Close"}
    assert result["sessionState"]["intent"] == {"name": "FallbackIntent", "state": "Fulfilled"}
    assert result["sessionState"]["sessionAttributes"]["escalate"] == "true"
    assert result["sessionState"]["sessionAttributes"]["reason"] == "Kundenwunsch"
    assert result["messages"] == [{"contentType": "PlainText", "content": "Ich verbinde Sie."}]


def test_runtime_failure_fails_toward_human() -> None:
    """Any runtime exception yields the Close escalation fallback, never a raise."""
    module = _load_handler()
    module._agentcore = _FakeRuntime(RuntimeError("boom"))
    module._RUNTIME_ARN = "arn:runtime"
    result = module.handler(_lex_event("Kontostand?"), None)
    assert result["sessionState"]["dialogAction"] == {"type": "Close"}
    assert result["sessionState"]["intent"] == {"name": "FallbackIntent", "state": "Fulfilled"}
    assert result["sessionState"]["sessionAttributes"]["escalate"] == "true"
    assert result["sessionState"]["sessionAttributes"]["reason"] == "Systemfehler"
    assert result["messages"][0]["content"]


def test_malformed_runtime_payload_fails_toward_human() -> None:
    """A non-contract runtime payload yields the Close escalation fallback."""
    module = _load_handler()
    module._agentcore = _FakeRuntime({"unexpected": "shape"})
    module._RUNTIME_ARN = "arn:runtime"
    result = module.handler(_lex_event("Kontostand?"), None)
    assert result["sessionState"]["dialogAction"] == {"type": "Close"}
    assert result["sessionState"]["sessionAttributes"]["escalate"] == "true"
    assert result["sessionState"]["sessionAttributes"]["reason"] == "Systemfehler"


def test_missing_customer_attribute_still_answers() -> None:
    """Without customer_id the payload simply omits it (agent handles NO_CUSTOMER)."""
    module = _load_handler()
    module._agentcore = _FakeRuntime({"answer": "4,90 €", "escalate": False, "reason": None})
    module._RUNTIME_ARN = "arn:runtime"
    module.handler(_lex_event("Gebühren?", customer_id=None), None)
    assert json.loads(module._agentcore.calls[0]["payload"]) == {"prompt": "Gebühren?"}


def test_null_intent_still_fails_toward_human() -> None:
    """Null intent (key present, None value) does not raise; returns Close escalation response."""
    module = _load_handler()
    module._agentcore = _FakeRuntime(RuntimeError("boom"))
    module._RUNTIME_ARN = "arn:runtime"
    event = {
        "sessionId": "11111111-2222-3333-4444-555555555555",
        "inputTranscript": "test",
        "invocationSource": "DialogCodeHook",
        "sessionState": {
            "sessionAttributes": {"customer_id": "KND-1001"},
            "intent": None,
        },
    }
    result = module.handler(event, None)
    assert result["sessionState"]["dialogAction"] == {"type": "Close"}
    assert result["sessionState"]["intent"] == {"name": "FallbackIntent", "state": "Fulfilled"}
    assert result["sessionState"]["sessionAttributes"]["escalate"] == "true"
    assert result["sessionState"]["sessionAttributes"]["reason"] == "Systemfehler"


def test_empty_answer_fails_toward_human() -> None:
    """Empty or whitespace-only answer is rejected; returns Close escalation response."""
    module = _load_handler()
    module._agentcore = _FakeRuntime({"answer": "  ", "escalate": False, "reason": None})
    module._RUNTIME_ARN = "arn:runtime"
    result = module.handler(_lex_event("Kontostand?"), None)
    assert result["sessionState"]["dialogAction"] == {"type": "Close"}
    assert result["sessionState"]["intent"] == {"name": "FallbackIntent", "state": "Fulfilled"}
    assert result["sessionState"]["sessionAttributes"]["escalate"] == "true"
    assert result["sessionState"]["sessionAttributes"]["reason"] == "Systemfehler"


def test_log_record_has_no_answer_and_keys_present() -> None:
    module = _load_handler()
    record = module._log_record(
        session_id="connect-abc", customer_id="KND-1001",
        escalate=False, reason="", outcome="answer", latency_ms=42,
    )
    assert set(record) == {"session_id", "customer_id", "escalate", "reason", "outcome", "latency_ms"}
    assert record["session_id"] == "connect-abc"
    # The answer text must never appear in a log record.
    assert "answer" not in record
    assert "2.543,17" not in json.dumps(record)


def test_handler_emits_correlation_log(caplog: pytest.LogCaptureFixture) -> None:
    module = _load_handler()
    module._agentcore = _FakeRuntime({"answer": "2.543,17 EUR", "escalate": False, "reason": None})
    module._RUNTIME_ARN = "arn:runtime"
    with caplog.at_level(logging.INFO):
        module.handler(_lex_event("Kontostand?"), None)
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "connect-11111111-2222-3333-4444-555555555555" in logged
    assert "2.543,17" not in logged  # no answer body in logs
