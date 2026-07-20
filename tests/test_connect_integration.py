"""Integration tests through the Amazon Connect front door (marker: integration)."""

from __future__ import annotations

import contextlib
import os
import time

import boto3
import pytest

from contact_center._internal import aws, connect_chat

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_INTEGRATION") != "1",
        reason="live AWS integration tests; set RUN_INTEGRATION=1 to run",
    ),
]


@pytest.fixture(scope="module")
def clients() -> dict:
    """Real connect + connectparticipant clients and resolved ids."""
    return {
        "connect": boto3.client("connect", region_name=aws.REGION),
        "participant": boto3.client("connectparticipant", region_name=aws.REGION),
        "instance_id": aws.get_parameter(aws.CONNECT_INSTANCE_PARAM),
        "flow_id": aws.get_parameter(aws.CONTACT_FLOW_PARAM).rsplit("/", 1)[-1],
        "queue_arn": aws.get_parameter(aws.ESCALATION_QUEUE_PARAM),
    }


def _chat_turn(clients: dict, question: str, customer_id: str = "KND-1001", timeout: float = 90.0) -> tuple[list[str], bool, str]:
    """Run one question through Connect; return (new_messages, transferred, contact_id).

    The flow's Lex input block arms asynchronously; a message sent before it
    listens is silently dropped. So we first drain and settle the initial
    transcript (mirrors `connect_chat._drain`'s settle=3.0s pattern) before
    sending, then only report messages that arrive after the send. The Lex
    block re-emits its "Wie kann ich Ihnen helfen?" prompt around answers on
    loop turns, so that prompt text is ignored when deciding the turn is done.
    """
    session = connect_chat.start_session(
        customer_id,
        connect=clients["connect"],
        participant=clients["participant"],
        instance_id=clients["instance_id"],
        flow_id=clients["flow_id"],
    )
    try:
        seen: set[str] = set()
        messages: list[str] = []
        transferred = False

        settle_deadline = time.monotonic() + 45.0
        last_new: float | None = None
        while time.monotonic() < settle_deadline:
            events = connect_chat.poll_events(session, participant=clients["participant"], seen=seen)
            if events:
                last_new = time.monotonic()
            for kind, text in events:
                if kind == "message":
                    messages.append(text)
                elif kind == "transfer":
                    transferred = True
            if messages and last_new is not None and time.monotonic() - last_new >= 3.0:
                break
            time.sleep(1)

        pre_send_count = len(messages)
        connect_chat.send(session, question, participant=clients["participant"])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for kind, text in connect_chat.poll_events(session, participant=clients["participant"], seen=seen):
                if kind == "message":
                    messages.append(text)
                elif kind == "transfer":
                    transferred = True
            new_messages = messages[pre_send_count:]
            if transferred or any(m for m in new_messages if m and "Wie kann ich Ihnen helfen" not in m):
                break
            time.sleep(1)
        return messages[pre_send_count:], transferred, session.contact_id
    finally:
        if session.socket is not None:
            with contextlib.suppress(Exception):
                session.socket.close()


def test_balance_through_connect(clients: dict) -> None:
    """A balance question survives the whole Connect round trip."""
    start = time.monotonic()
    messages, transferred, _ = _chat_turn(clients, "Wie ist mein Kontostand?")
    elapsed = time.monotonic() - start
    joined = " ".join(messages)
    assert "2.543,17" in joined
    assert transferred is False
    assert elapsed < 60, f"round trip took {elapsed:.1f}s"


def test_knowledge_through_connect(clients: dict) -> None:
    """A knowledge question keeps its citation through Connect."""
    messages, transferred, _ = _chat_turn(clients, "Was kostet das Girokonto im Monat?")
    joined = " ".join(messages)
    assert "4,90" in joined
    assert "[Quelle:" in joined
    assert transferred is False


def test_escalation_reaches_queue(clients: dict) -> None:
    """An explicit human request transfers the contact to the escalation queue.

    DescribeContact is the authoritative signal (per the M3 spec): the
    participant-transcript transfer event only fires once a human agent
    accepts the chat, and this sandbox has no staffed agent.
    """
    _messages, _transferred, contact_id = _chat_turn(clients, "Ich möchte mit einem Menschen sprechen.")
    deadline = time.monotonic() + 30
    queue_info: dict = {}
    while time.monotonic() < deadline and not queue_info:
        described = clients["connect"].describe_contact(
            InstanceId=clients["instance_id"], ContactId=contact_id,
        )
        queue_info = described.get("Contact", {}).get("QueueInfo") or {}
        if not queue_info:
            time.sleep(2)
    assert queue_info, "contact never reached a queue"
    assert clients["queue_arn"].endswith(queue_info.get("Id", "")), "contact landed in the wrong queue"
