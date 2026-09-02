"""Chat harness path that goes through Amazon Connect instead of direct invoke.

Starting a Connect chat contact is not enough on its own: the flow only runs
once the customer opens the chat websocket and subscribes to the chat topic.
Transcript reads/writes still go over REST; the websocket exists solely to
mark the customer connected.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from dataclasses import dataclass
from typing import Any, Self

import boto3
import websocket

from contact_center._internal import aws

_TRANSFER_EVENT = "application/vnd.amazonaws.connect.event.transfer.succeeded"
_ENDED_EVENT = "application/vnd.amazonaws.connect.event.chat.ended"


@dataclass
class ChatSession:
    """Identifiers for one Connect chat session."""

    contact_id: str
    connection_token: str
    socket: Any = None


def _open_chat_socket(url: str) -> websocket.WebSocket:
    """Open the chat websocket and subscribe; this marks the customer connected."""
    socket = websocket.create_connection(url, timeout=10)
    socket.send('{"topic":"aws/subscribe","content":{"topics":["aws/chat"]}}')
    return socket


@dataclass(frozen=True)
class TurnResult:
    """New customer-visible output and terminal state from one wait."""

    messages: tuple[str, ...]
    transferred: bool = False
    ended: bool = False
    timed_out: bool = False


class ConnectConversation:
    """One Amazon Connect chat conversation and its owned transport state."""

    def __init__(
        self,
        *,
        contact_id: str,
        connection_token: str,
        participant: Any,
        socket: Any = None,
    ) -> None:
        """Store the participant connection and optional websocket."""
        self.contact_id = contact_id
        self._connection_token = connection_token
        self._participant = participant
        self.socket = socket
        self._seen: set[str] = set()

    @classmethod
    def start(
        cls,
        customer_id: str,
        *,
        connect: Any,
        participant: Any,
        instance_id: str,
        flow_id: str,
    ) -> ConnectConversation:
        """Start a Connect contact and open its participant connection."""
        started = connect.start_chat_contact(
            InstanceId=instance_id,
            ContactFlowId=flow_id,
            Attributes={"customer_id": customer_id},
            ParticipantDetails={"DisplayName": "PoC Customer"},
            ClientToken=uuid.uuid4().hex,
        )
        connection = participant.create_participant_connection(
            ParticipantToken=started["ParticipantToken"],
            Type=["WEBSOCKET", "CONNECTION_CREDENTIALS"],
        )
        websocket_info = connection.get("Websocket")
        socket = _open_chat_socket(websocket_info["Url"]) if websocket_info else None
        return cls(
            contact_id=started["ContactId"],
            connection_token=connection["ConnectionCredentials"]["ConnectionToken"],
            participant=participant,
            socket=socket,
        )

    def __enter__(self) -> Self:
        """Return this open conversation."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Close this conversation when leaving its context."""
        self.close()

    def send(self, text: str) -> None:
        """Send one customer message into the chat."""
        self._participant.send_message(
            ConnectionToken=self._connection_token,
            ContentType="text/plain",
            Content=text,
        )

    def close(self) -> None:
        """Close the websocket, suppressing cleanup failures."""
        if self.socket is not None:
            with contextlib.suppress(Exception):
                self.socket.close()

    def wait(self, *, timeout: float = 45.0, settle: float = 3.0) -> TurnResult:
        """Wait for new transcript output to settle or reach a terminal state."""
        deadline = time.monotonic() + timeout
        messages: list[str] = []
        last_new: float | None = None
        while time.monotonic() < deadline:
            transcript = self._participant.get_transcript(ConnectionToken=self._connection_token)
            new_items: list[dict[str, Any]] = []
            for item in transcript.get("Transcript", []):
                item_id = item.get("Id", "")
                if item_id in self._seen:
                    continue
                self._seen.add(item_id)
                new_items.append(item)
            transferred = False
            ended = False
            relevant_event = False
            for item in new_items:
                if item.get("Type") == "MESSAGE" and item.get("ParticipantRole") in {"SYSTEM", "AGENT"}:
                    messages.append(item.get("Content", ""))
                    relevant_event = True
                elif item.get("Type") == "EVENT" and item.get("ContentType") == _TRANSFER_EVENT:
                    transferred = True
                    relevant_event = True
                elif item.get("Type") == "EVENT" and item.get("ContentType") == _ENDED_EVENT:
                    ended = True
                    relevant_event = True
            if relevant_event:
                last_new = time.monotonic()
            if transferred or ended:
                return TurnResult(tuple(messages), transferred=transferred, ended=ended)
            if last_new is not None and time.monotonic() - last_new >= settle:
                return TurnResult(tuple(messages))
            time.sleep(1)
        return TurnResult(tuple(messages), timed_out=True)


def start_session(
    customer_id: str, *, connect: Any, participant: Any, instance_id: str, flow_id: str,
) -> ChatSession:
    """Start a Connect chat and open a participant connection.

    Parameters:
        customer_id: Authenticated customer id, passed as a contact attribute.
        connect: A `connect` boto3 client.
        participant: A `connectparticipant` boto3 client.
        instance_id: Connect instance id (or ARN).
        flow_id: Contact flow id (or ARN).

    Returns:
        The chat session identifiers.
    """
    started = connect.start_chat_contact(
        InstanceId=instance_id,
        ContactFlowId=flow_id,
        Attributes={"customer_id": customer_id},
        ParticipantDetails={"DisplayName": "PoC Customer"},
        ClientToken=uuid.uuid4().hex,
    )
    connection = participant.create_participant_connection(
        ParticipantToken=started["ParticipantToken"],
        Type=["WEBSOCKET", "CONNECTION_CREDENTIALS"],
    )
    websocket_info = connection.get("Websocket")
    socket = _open_chat_socket(websocket_info["Url"]) if websocket_info else None
    return ChatSession(
        contact_id=started["ContactId"],
        connection_token=connection["ConnectionCredentials"]["ConnectionToken"],
        socket=socket,
    )


def send(session: ChatSession, text: str, *, participant: Any) -> None:
    """Send one customer message into the chat."""
    participant.send_message(
        ConnectionToken=session.connection_token, ContentType="text/plain", Content=text,
    )


def poll_events(session: ChatSession, *, participant: Any, seen: set[str]) -> list[tuple[str, str]]:
    """Fetch unseen transcript items.

    Parameters:
        session: The chat session.
        participant: A `connectparticipant` boto3 client.
        seen: Mutable set of already-reported item ids.

    Returns:
        New events as (kind, text): kind is "message" (agent/system text),
        "transfer", or "ended".
    """
    transcript = participant.get_transcript(ConnectionToken=session.connection_token)
    events: list[tuple[str, str]] = []
    for item in transcript.get("Transcript", []):
        item_id = item.get("Id", "")
        if item_id in seen:
            continue
        seen.add(item_id)
        if item.get("Type") == "MESSAGE" and item.get("ParticipantRole") in {"SYSTEM", "AGENT"}:
            events.append(("message", item.get("Content", "")))
        elif item.get("Type") == "EVENT" and item.get("ContentType") == _TRANSFER_EVENT:
            events.append(("transfer", ""))
        elif item.get("Type") == "EVENT" and item.get("ContentType") == _ENDED_EVENT:
            events.append(("ended", ""))
    return events


def _drain(
    session: ChatSession, participant: Any, seen: set[str], timeout: float = 45.0, settle: float = 3.0,
) -> bool:
    """Poll until messages arrive and the transcript settles, then print them.

    The flow arms its input block asynchronously; a message sent before the
    block listens is silently dropped. Waiting `settle` seconds after the last
    new item ensures the flow is ready before the caller sends the next turn.

    Returns:
        False if the chat transferred or ended (stop the REPL), else True.
    """
    deadline = time.monotonic() + timeout
    keep_going = True
    last_new: float | None = None
    while time.monotonic() < deadline:
        events = poll_events(session, participant=participant, seen=seen)
        if events:
            last_new = time.monotonic()
        for kind, text in events:
            if kind == "message":
                print(text)  # noqa: T201
            elif kind == "transfer":
                print("⚠ Chat wurde an die Warteschlange übergeben.")  # noqa: T201
                keep_going = False
            else:
                print("(Chat beendet.)")  # noqa: T201
                keep_going = False
        if not keep_going:
            return keep_going
        if last_new is not None and time.monotonic() - last_new >= settle:
            return keep_going
        time.sleep(1)
    return keep_going


def run_connect_chat(question: str | None = None, customer_id: str = "KND-1001") -> int:
    """Run a chat through Amazon Connect (one-shot or REPL).

    Parameters:
        question: If given, send once, print replies, and exit.
        customer_id: Authenticated customer id (contact attribute).

    Returns:
        An exit code.

    Raises:
        SystemExit: With a named remediation when the Connect SSM parameters
            are missing.
    """
    try:
        instance_id = aws.get_parameter(aws.CONNECT_INSTANCE_PARAM)
        flow_id = aws.get_parameter(aws.CONTACT_FLOW_PARAM).rsplit("/", 1)[-1]
    except Exception as error:
        raise SystemExit(
            "Cannot read Connect parameters — deploy ContactCenterConnect (cd infra && npx cdk deploy).",
        ) from error
    connect = boto3.client("connect", region_name=aws.REGION)
    participant = boto3.client("connectparticipant", region_name=aws.REGION)
    session = start_session(
        customer_id, connect=connect, participant=participant, instance_id=instance_id, flow_id=flow_id,
    )
    try:
        seen: set[str] = set()
        _drain(session, participant, seen)  # greeting
        if question is not None:
            send(session, question, participant=participant)
            _drain(session, participant, seen)
            return 0
        while True:  # pragma: no cover (interactive loop)
            try:
                prompt = input("you> ")
            except (EOFError, KeyboardInterrupt):
                return 0
            if prompt.strip().lower() in {"exit", "quit"}:
                return 0
            send(session, prompt, participant=participant)
            if not _drain(session, participant, seen):
                return 0
    finally:
        if session.socket is not None:
            with contextlib.suppress(Exception):
                session.socket.close()
