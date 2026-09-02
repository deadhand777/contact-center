"""Integration tests through the Amazon Connect front door (marker: integration)."""

from __future__ import annotations

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


def _chat_turn(
    clients: dict,
    question: str,
    customer_id: str = "KND-1001",
    timeout: float = 90.0,
) -> tuple[list[str], bool, str]:
    """Run one question through Connect; return messages, transfer state, and contact id."""
    with connect_chat.ConnectConversation.start(
        customer_id,
        connect=clients["connect"],
        participant=clients["participant"],
        instance_id=clients["instance_id"],
        flow_id=clients["flow_id"],
    ) as conversation:
        conversation.wait()
        conversation.send(question)
        result = conversation.wait(timeout=timeout)
        return list(result.messages), result.transferred, conversation.contact_id



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
