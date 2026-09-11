"""Structured response contract between the supervisor agent and its adapters."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Self

_LOGGER = logging.getLogger(__name__)

AGENT_REASONS: frozenset[str] = frozenset({
    "Kundenwunsch",
    "Sensibles Thema Kreditablehnung",
    "Systemfehler Kontodienst",
    "Kunde nicht identifiziert",
    "Keine gesicherte Antwort möglich",
})
FALLBACK_ANSWER = (
    "Es tut mir leid, ich kann gerade keine gesicherte Antwort geben. "
    "Ich verbinde Sie mit einer Mitarbeiterin oder einem Mitarbeiter."
)
_FIELDS = frozenset({"answer", "escalate", "reason"})


def _extract_json(text: str) -> str:
    """Return the outermost JSON object substring, or the text unchanged."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return text
    return text[start : end + 1]


def _rejection_record(error: Exception, data: object) -> str:
    """Return a PII-safe log line naming why a response was rejected."""
    return json.dumps(
        {
            "event": "contract_rejected",
            "cause": str(error),
            "fields": sorted(data) if isinstance(data, dict) else None,
        },
        ensure_ascii=False,
    )


@dataclass(frozen=True)
class SupervisorResponse:
    """One validated supervisor outcome."""

    answer: str
    escalate: bool
    reason: str | None

    def __post_init__(self) -> None:
        """Reject values outside the response contract."""
        if not isinstance(self.answer, str) or not self.answer.strip():
            raise ValueError("answer must be a non-empty string")
        if type(self.escalate) is not bool:
            raise ValueError("escalate must be a boolean")
        if not self.escalate and self.reason is not None:
            raise ValueError("reason must be null when escalate is false")
        if self.escalate and self.reason not in AGENT_REASONS:
            raise ValueError("reason must be a known agent reason when escalate is true")

    @classmethod
    def from_supervisor_output(cls, text: str) -> Self:
        """Parse untrusted model text, returning the safe fallback when invalid."""
        data: object = None
        try:
            data = json.loads(_extract_json(text))
            if not isinstance(data, dict) or set(data) != _FIELDS:
                raise ValueError("response fields do not match the contract")
            return cls(
                answer=data["answer"],
                escalate=data["escalate"],
                reason=data["reason"],
            )
        except (KeyError, TypeError, ValueError) as error:
            _LOGGER.warning(_rejection_record(error, data))
            return cls.fallback()

    @classmethod
    def guardrail_refusal(cls, text: str) -> Self:
        """Return a guardrail's own blocked message as a non-escalating refusal."""
        message = text.strip()
        if not message:
            return cls.fallback()
        return cls(answer=message, escalate=False, reason=None)

    @classmethod
    def fallback(cls) -> Self:
        """Return the fixed human-escalation fallback."""
        return cls(
            answer=FALLBACK_ANSWER,
            escalate=True,
            reason="Keine gesicherte Antwort möglich",
        )

    def to_payload(self) -> dict[str, str | bool | None]:
        """Return the AgentCore runtime payload."""
        return {"answer": self.answer, "escalate": self.escalate, "reason": self.reason}

    def to_log_record(
        self,
        session_id: str | None,
        customer_id: str | None,
    ) -> dict[str, str | bool | None]:
        """Return a PII-safe record without answer text."""
        return {
            "session_id": session_id,
            "customer_id": customer_id,
            "escalate": self.escalate,
            "reason": self.reason,
        }
