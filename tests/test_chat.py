"""Tests for the chat harness."""

from __future__ import annotations

import json

import pytest
from botocore.exceptions import ClientError

from contact_center._internal import chat


class _FakeBody:
    """Fake StreamingBody with a read() method."""

    def __init__(self, data: bytes) -> None:
        """Store the payload bytes."""
        self._data = data

    def read(self) -> bytes:
        """Return the payload bytes."""
        return self._data


class _FakeRuntime:
    """Fake bedrock-agentcore client recording invocations."""

    def __init__(self, payload: dict) -> None:
        """Store the canned agent response."""
        self._payload = payload
        self.calls: list[dict] = []

    def invoke_agent_runtime(self, **kwargs: object) -> dict:
        """Record the call and return a canned response."""
        self.calls.append(kwargs)
        return {
            "contentType": "application/json",
            "response": _FakeBody(json.dumps(self._payload).encode()),
        }


def test_ask_returns_contract_and_sends_customer() -> None:
    """ask() returns the contract dict and forwards customer_id in the payload."""
    client = _FakeRuntime({"answer": "Saldo: 890,00 €", "escalate": False, "reason": None})
    response = chat.ask(
        "Kontostand?", runtime_arn="arn:x", session_id="s" * 33, client=client, customer_id="KND-1003",
    )
    assert response["answer"] == "Saldo: 890,00 €"
    assert response["escalate"] is False
    sent = json.loads(client.calls[0]["payload"])
    assert sent == {"prompt": "Kontostand?", "customer_id": "KND-1003"}


def test_ask_wraps_legacy_plain_payload() -> None:
    """Non-contract payloads are wrapped into the contract shape."""
    client = _FakeRuntime({"unexpected": "shape"})
    response = chat.ask("hi", runtime_arn="arn:x", session_id="s" * 33, client=client)
    assert response["escalate"] is False
    assert "unexpected" in response["answer"]


def test_render_adds_escalation_banner() -> None:
    """render() appends the handoff banner when escalate is true."""
    text = chat.render({"answer": "Ich verbinde Sie.", "escalate": True, "reason": "Kundenwunsch"})
    assert text == "Ich verbinde Sie.\n⚠ Übergabe an Mitarbeiter: Kundenwunsch"


def test_render_plain_answer_without_banner() -> None:
    """render() returns just the answer when escalate is false."""
    assert chat.render({"answer": "4,90 €", "escalate": False, "reason": None}) == "4,90 €"


def test_run_chat_names_remediation_when_parameter_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_chat() fails fast with a named remediation if the runtime ARN is absent."""

    def _boom(_name: str) -> str:
        raise ClientError({"Error": {"Code": "ParameterNotFound", "Message": "missing"}}, "GetParameter")

    monkeypatch.setattr(chat.aws, "get_parameter", _boom)
    with pytest.raises(SystemExit, match="agentcore deploy"):
        chat.run_chat("hi")
