"""Canonical decoded payloads for the distributed response contract."""

from __future__ import annotations

AGENT_REASONS: tuple[str, ...] = (
    "Kundenwunsch",
    "Sensibles Thema Kreditablehnung",
    "Systemfehler Kontodienst",
    "Kunde nicht identifiziert",
    "Keine gesicherte Antwort möglich",
)

VALID_CONTRACT_PAYLOADS: tuple[dict[str, object], ...] = (
    {"answer": "Alles in Ordnung.", "escalate": False, "reason": None},
    *({"answer": "Ich verbinde Sie.", "escalate": True, "reason": reason} for reason in AGENT_REASONS),
)

INVALID_CONTRACT_PAYLOADS: tuple[dict[str, object], ...] = (
    {"escalate": False, "reason": None},
    {"answer": "Antwort", "escalate": False},
    {"answer": "Antwort", "escalate": False, "reason": None, "extra": "field"},
    {"answer": "", "escalate": False, "reason": None},
    {"answer": "   ", "escalate": False, "reason": None},
    {"answer": 42, "escalate": False, "reason": None},
    {"answer": "Antwort", "escalate": "false", "reason": None},
    {"answer": "Antwort", "escalate": 0, "reason": None},
    {"answer": "Antwort", "escalate": 1, "reason": None},
    {"answer": "Antwort", "escalate": False, "reason": "Kundenwunsch"},
    {"answer": "Ich verbinde Sie.", "escalate": True, "reason": None},
    {"answer": "Ich verbinde Sie.", "escalate": True, "reason": 42},
    {"answer": "Ich verbinde Sie.", "escalate": True, "reason": "Unbekannt"},
)
