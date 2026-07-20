"""Tests for the Connect chat harness."""

from __future__ import annotations

from typing import Any

import pytest

from contact_center._internal import connect_chat


class _FakeConnect:
    """Fake connect client."""

    def __init__(self) -> None:
        """Record calls."""
        self.calls: list[dict] = []

    def start_chat_contact(self, **kwargs: Any) -> dict:
        """Record and return chat identifiers."""
        self.calls.append(kwargs)
        return {"ContactId": "contact-1", "ParticipantToken": "ptoken"}


class _FakeParticipant:
    """Fake connectparticipant client with a scripted transcript."""

    def __init__(self, items: list[dict] | None = None) -> None:
        """Store scripted transcript items."""
        self.items = items or []
        self.sent: list[str] = []

    def create_participant_connection(self, **_kwargs: Any) -> dict:
        """Return a connection token."""
        return {"ConnectionCredentials": {"ConnectionToken": "ctoken"}}

    def send_message(self, **kwargs: Any) -> dict:
        """Record sent text."""
        self.sent.append(kwargs["Content"])
        return {}

    def get_transcript(self, **_kwargs: Any) -> dict:
        """Return the scripted transcript."""
        return {"Transcript": self.items}


def test_start_session_passes_customer_attribute() -> None:
    """start_session forwards customer_id as a contact attribute."""
    connect = _FakeConnect()
    session = connect_chat.start_session(
        "KND-1001", connect=connect, participant=_FakeParticipant(), instance_id="inst", flow_id="flow",
    )
    assert session.contact_id == "contact-1"
    assert session.connection_token == "ctoken"  # noqa: S105
    assert connect.calls[0]["Attributes"] == {"customer_id": "KND-1001"}
    assert session.socket is None


def test_start_session_opens_websocket_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """start_session opens the chat websocket when the response includes one."""

    class _FakeParticipantWithSocket(_FakeParticipant):
        def create_participant_connection(self, **_kwargs: Any) -> dict:
            return {
                "ConnectionCredentials": {"ConnectionToken": "ctoken"},
                "Websocket": {"Url": "wss://x"},
            }

    sentinel = object()
    captured: dict = {}

    def _fake_open(url: str) -> object:
        captured["url"] = url
        return sentinel

    monkeypatch.setattr(connect_chat, "_open_chat_socket", _fake_open)
    session = connect_chat.start_session(
        "KND-1001",
        connect=_FakeConnect(),
        participant=_FakeParticipantWithSocket(),
        instance_id="inst",
        flow_id="flow",
    )
    assert session.socket is sentinel
    assert captured["url"] == "wss://x"


def test_poll_events_returns_new_agent_messages_and_transfer() -> None:
    """poll_events yields unseen agent messages and transfer events, skipping customer echoes."""
    items = [
        {"Id": "1", "Type": "MESSAGE", "ParticipantRole": "CUSTOMER", "Content": "hi"},
        {"Id": "2", "Type": "MESSAGE", "ParticipantRole": "SYSTEM", "Content": "Willkommen"},
        {"Id": "3", "Type": "EVENT", "ContentType": "application/vnd.amazonaws.connect.event.transfer.succeeded"},
    ]
    participant = _FakeParticipant(items)
    session = connect_chat.ChatSession(contact_id="c", connection_token="t")  # noqa: S106
    seen: set[str] = set()
    events = connect_chat.poll_events(session, participant=participant, seen=seen)
    assert ("message", "Willkommen") in events
    assert ("transfer", "") in events
    assert all(kind != "message" or text != "hi" for kind, text in events)
    assert connect_chat.poll_events(session, participant=participant, seen=seen) == []


def test_flow_id_extracted_from_arn(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_connect_chat passes the bare flow id even when SSM stores an ARN."""

    def _param(name: str) -> str:
        if name == connect_chat.aws.CONTACT_FLOW_PARAM:
            return "arn:aws:connect:eu-central-1:1:instance/i-1/contact-flow/flow-42"
        return "instance-1"

    captured: dict = {}

    def _fake_start_session(_customer_id: str, **kwargs: object) -> connect_chat.ChatSession:
        captured.update(kwargs)
        raise SystemExit(0)

    monkeypatch.setattr(connect_chat.aws, "get_parameter", _param)
    monkeypatch.setattr(connect_chat, "start_session", _fake_start_session)
    monkeypatch.setattr(connect_chat.boto3, "client", lambda *a, **k: object())
    with pytest.raises(SystemExit):
        connect_chat.run_connect_chat("hi")
    assert captured["flow_id"] == "flow-42"
    assert captured["instance_id"] == "instance-1"
