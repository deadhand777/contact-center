"""Lex V2 dialog code hook bridge: forwards chat turns to the AgentCore runtime."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import boto3
from botocore.config import Config

REGION = "eu-central-1"

_LOGGER = logging.getLogger(__name__)

# Lex dialog code hooks allow up to 30 s (unlike Connect flow Lambda blocks,
# which cap at 8 s — that constraint died with the Lex pivot). Budget: agentcore
# 2 s connect + 20 s read, plus a once-per-container SSM read (2 s + 3 s) for the
# runtime ARN on cold start; the compound worst case stays under the 25 s function
# timeout. No retries (an aborted attempt leaves the runtime session busy and a
# retry would 500 with "already processing"). A timeout fails safe: no Lex
# response routes the flow to the escalation queue.
_agentcore = boto3.client(
    "bedrock-agentcore",
    region_name=REGION,
    config=Config(connect_timeout=2, read_timeout=20, retries={"total_max_attempts": 1}),
)
_RUNTIME_ARN: str | None = None

_FALLBACK = {
    "answer": "Es tut mir leid, es gibt gerade ein technisches Problem. Ich verbinde Sie mit einer Mitarbeiterin oder einem Mitarbeiter.",
    "escalate": "true",
    "reason": "Systemfehler",
}


def _runtime_arn() -> str:
    """Read the AgentCore runtime ARN from SSM once per container."""
    global _RUNTIME_ARN  # noqa: PLW0603 — cold-start cache
    if _RUNTIME_ARN is None:
        ssm = boto3.client(
            "ssm",
            region_name=REGION,
            config=Config(connect_timeout=2, read_timeout=3, retries={"total_max_attempts": 1}),
        )
        _RUNTIME_ARN = ssm.get_parameter(Name="/contact-center/runtime-arn")["Parameter"]["Value"]
    return _RUNTIME_ARN


def _response(
    session_attributes: dict[str, Any],
    intent_name: str,
    *,
    answer: str,
    escalate: bool,
    reason: str,
) -> dict[str, Any]:
    """Build a Lex V2 dialog code hook response.

    Parameters:
        session_attributes: Incoming session attributes to carry forward.
        intent_name: Intent name to echo back when closing the dialog.
        answer: Text to show the customer.
        escalate: Whether to close the dialog and hand off to a human.
        reason: Escalation reason (empty string when not escalating).

    Returns:
        The full Lex V2 response: ElicitIntent to keep looping, or Close plus
        a Fulfilled intent when escalating.
    """
    attributes = {**session_attributes, "escalate": "true" if escalate else "false", "reason": reason}
    session_state: dict[str, Any] = {"sessionAttributes": attributes}
    if escalate:
        session_state["dialogAction"] = {"type": "Close"}
        session_state["intent"] = {"name": intent_name, "state": "Fulfilled"}
    else:
        session_state["dialogAction"] = {"type": "ElicitIntent"}
    return {
        "sessionState": session_state,
        "messages": [{"contentType": "PlainText", "content": answer}],
    }


def _log_record(
    *,
    session_id: str,
    customer_id: str | None,
    escalate: bool,
    reason: str,
    outcome: str,
    latency_ms: int,
) -> dict[str, Any]:
    """Build a structured, PII-safe turn log record (no answer text)."""
    return {
        "session_id": session_id,
        "customer_id": customer_id,
        "escalate": escalate,
        "reason": reason,
        "outcome": outcome,
        "latency_ms": latency_ms,
    }


def _fallback_response(session_attributes: dict[str, Any], intent_name: str) -> dict[str, Any]:
    """Build the Close escalation fallback response (fail toward human)."""
    return _response(
        session_attributes,
        intent_name,
        answer=_FALLBACK["answer"],
        escalate=True,
        reason=_FALLBACK["reason"],
    )


def _log_turn(
    *,
    session_id: str,
    customer_id: str | None,
    escalate: bool,
    reason: str,
    outcome: str,
    start: float,
) -> None:
    """Emit the structured turn log; guarded so a logging failure can never raise."""
    try:
        latency_ms = int((time.monotonic() - start) * 1000)
        _LOGGER.info(
            json.dumps(
                _log_record(
                    session_id=session_id,
                    customer_id=customer_id,
                    escalate=escalate,
                    reason=reason,
                    outcome=outcome,
                    latency_ms=latency_ms,
                )
            )
        )
    except Exception:  # noqa: BLE001 — logging must never break the never-raise contract
        pass


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:  # noqa: ARG001
    """Handle one Lex V2 dialog code hook invocation.

    Parameters:
        event: Lex V2 event; utterance in inputTranscript, session state in
            sessionState (sessionAttributes, intent).
        context: Lambda context (unused).

    Returns:
        A Lex V2 dialog code hook response. Never raises — failures return the
        Close escalation fallback.
    """
    # Set safe defaults before try; overwrite inside if extraction succeeds.
    session_attributes: dict[str, Any] = {}
    intent_name = "FallbackIntent"
    runtime_session_id = "unknown"
    customer_id: str | None = None
    start = time.monotonic()
    try:
        session_state = event.get("sessionState") or {}
        session_attributes = session_state.get("sessionAttributes") or {}
        intent_name = (session_state.get("intent") or {}).get("name") or "FallbackIntent"
        session_id = event["sessionId"]
        runtime_session_id = f"connect-{session_id}"
        text = event.get("inputTranscript", "")
        payload: dict[str, Any] = {"prompt": text}
        customer_id = session_attributes.get("customer_id")
        if customer_id:
            payload["customer_id"] = customer_id
        response = _agentcore.invoke_agent_runtime(
            agentRuntimeArn=_runtime_arn(),
            runtimeSessionId=runtime_session_id,
            qualifier="DEFAULT",
            payload=json.dumps(payload),
        )
        body = response["response"]
        raw = body.read() if hasattr(body, "read") else b"".join(body)
        data = json.loads(raw)
        if not isinstance(data, dict) or "answer" not in data or not str(data["answer"]).strip():
            _log_turn(
                session_id=runtime_session_id,
                customer_id=customer_id,
                escalate=True,
                reason=_FALLBACK["reason"],
                outcome="fallback",
                start=start,
            )
            return _fallback_response(session_attributes, intent_name)
        escalate = data.get("escalate") is True
        reason = str(data.get("reason") or "")
        _log_turn(
            session_id=runtime_session_id,
            customer_id=customer_id,
            escalate=escalate,
            reason=reason,
            outcome="answer",
            start=start,
        )
        return _response(
            session_attributes,
            intent_name,
            answer=str(data["answer"]),
            escalate=escalate,
            reason=reason,
        )
    except Exception:  # noqa: BLE001 — boundary: the caller must always get a response back
        _LOGGER.exception("bridge failed; returning escalation fallback")
        _log_turn(
            session_id=runtime_session_id,
            customer_id=customer_id,
            escalate=True,
            reason=_FALLBACK["reason"],
            outcome="error",
            start=start,
        )
        return _fallback_response(session_attributes, intent_name)
