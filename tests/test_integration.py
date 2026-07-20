"""Integration tests against the deployed knowledge agent (marker: integration)."""

from __future__ import annotations

import os
import uuid

import boto3
import pytest

from contact_center._internal import aws, chat

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_INTEGRATION") != "1",
        reason="live AWS integration tests; set RUN_INTEGRATION=1 to run",
    ),
]

HANDOFF_HINTS = ("kolleg", "mitarbeiter", "colleague", "human", "nicht beantworten", "can't answer", "cannot answer", "not able")


@pytest.fixture(scope="module")
def client() -> object:
    """A real bedrock-agentcore client."""
    return boto3.client("bedrock-agentcore", region_name=aws.REGION)


@pytest.fixture(scope="module")
def runtime_arn() -> str:
    """The deployed runtime ARN from SSM."""
    return aws.get_parameter(aws.RUNTIME_ARN_PARAM)


def _session_id() -> str:
    """A fresh AgentCore session ID (33+ chars)."""
    return uuid.uuid4().hex + "-integration"


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Was kostet das Girokonto im Monat?", "4,90"),
        ("What does a replacement debit card cost?", "10,00"),
        ("Warum wurde mein Kredit abgelehnt?", "SCHUFA"),
    ],
)
def test_golden_questions_are_grounded(client: object, runtime_arn: str, question: str, expected: str) -> None:
    """Golden corpus questions get answers with the pinned fact and a citation."""
    response = chat.ask(question, runtime_arn=runtime_arn, session_id=_session_id(), client=client)
    # Special case for "10,00" which may appear as "10.00" in response
    if expected == "10,00":
        assert "10,00" in response["answer"] or "10.00" in response["answer"]
    else:
        assert expected.lower() in response["answer"].lower()
    assert "quelle" in response["answer"].lower() or "source" in response["answer"].lower()


def test_out_of_corpus_question_is_refused(client: object, runtime_arn: str) -> None:
    """Questions outside the corpus are declined with a handoff offer."""
    response = chat.ask(
        "What will the Bitcoin price be tomorrow?",
        runtime_arn=runtime_arn,
        session_id=_session_id(),
        client=client,
    )
    assert any(hint in response["answer"].lower() for hint in HANDOFF_HINTS)


def test_balance_lookup_for_authenticated_customer(client: object, runtime_arn: str) -> None:
    """The authenticated customer's balances come back through the gateway."""
    response = chat.ask(
        "Wie ist mein Kontostand?",
        runtime_arn=runtime_arn,
        session_id=_session_id(),
        client=client,
        customer_id="KND-1001",
    )
    assert "2.543,17" in response["answer"]
    assert response["escalate"] is False


def test_explicit_human_request_escalates(client: object, runtime_arn: str) -> None:
    """Asking for a human sets the escalation flag with a reason."""
    response = chat.ask(
        "Ich möchte mit einem Menschen sprechen.",
        runtime_arn=runtime_arn,
        session_id=_session_id(),
        client=client,
        customer_id="KND-1001",
    )
    assert response["escalate"] is True
    assert response["reason"]


def test_unknown_customer_escalates(client: object, runtime_arn: str) -> None:
    """An unknown customer id on a banking question escalates."""
    response = chat.ask(
        "Wie ist mein Kontostand?",
        runtime_arn=runtime_arn,
        session_id=_session_id(),
        client=client,
        customer_id="KND-9999",
    )
    assert response["escalate"] is True
