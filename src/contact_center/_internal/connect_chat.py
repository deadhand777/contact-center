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


def _print_turn(result: TurnResult) -> bool:
    """Print one turn and return whether the conversation can continue."""
    for message in result.messages:
        print(message)  # noqa: T201
    if result.transferred:
        print("⚠ Chat wurde an die Warteschlange übergeben.")  # noqa: T201
    if result.ended:
        print("(Chat beendet.)")  # noqa: T201
    return not (result.transferred or result.ended)


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
    with ConnectConversation.start(
        customer_id,
        connect=connect,
        participant=participant,
        instance_id=instance_id,
        flow_id=flow_id,
    ) as conversation:
        _print_turn(conversation.wait())  # greeting
        if question is not None:
            conversation.send(question)
            _print_turn(conversation.wait())
            return 0
        while True:  # pragma: no cover (interactive loop)
            try:
                prompt = input("you> ")
            except (EOFError, KeyboardInterrupt):
                return 0
            if prompt.strip().lower() in {"exit", "quit"}:
                return 0
            conversation.send(prompt)
            if not _print_turn(conversation.wait()):
                return 0
