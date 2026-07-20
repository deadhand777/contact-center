"""Structured response contract between the supervisor agent and the harness."""

from __future__ import annotations

import json


def _extract_json(text: str) -> str:
    """Return the outermost JSON object substring, or the text unchanged."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return text
    return text[start : end + 1]


def parse_supervisor_output(text: str) -> dict:
    """Parse supervisor output into the response contract.

    Parameters:
        text: Raw supervisor output (ideally contract JSON, possibly prose).

    Returns:
        `{"answer": str, "escalate": bool, "reason": str | None}` — falls back
        to the raw text with `escalate=False` when no valid contract is found.
    """
    fallback = {"answer": text, "escalate": False, "reason": None}
    try:
        data = json.loads(_extract_json(text))
    except ValueError:
        return fallback
    if not isinstance(data, dict) or "answer" not in data:
        return fallback
    reason = data.get("reason")
    return {
        "answer": str(data["answer"]),
        "escalate": bool(data.get("escalate", False)),
        "reason": str(reason) if reason is not None else None,
    }


def escalation_log_record(session_id: str | None, customer_id: str | None, response: dict) -> dict:
    """Build a PII-safe agent-side turn log record (no answer text).

    Parameters:
        session_id: The runtime session id, when the entrypoint context exposes it.
        customer_id: The authenticated customer id, if any.
        response: The parsed response contract.

    Returns:
        `{"session_id", "customer_id", "escalate", "reason"}`.
    """
    return {
        "session_id": session_id,
        "customer_id": customer_id,
        "escalate": bool(response.get("escalate", False)),
        "reason": response.get("reason"),
    }
