"""Mock core-banking balance lookup for the contact-center PoC (synthetic data only)."""

from __future__ import annotations

import json
from typing import Any

_ACCOUNTS: dict[str, list[dict[str, str]]] = {
    "KND-1001": [
        {"iban_masked": "DE**5001", "type": "Girokonto", "balance": "2.543,17", "currency": "EUR"},
        {"iban_masked": "DE**5002", "type": "Tagesgeld", "balance": "15.000,00", "currency": "EUR"},
    ],
    "KND-1002": [
        {"iban_masked": "DE**5003", "type": "Girokonto", "balance": "-127,45", "currency": "EUR"},
    ],
    "KND-1003": [
        {"iban_masked": "DE**5004", "type": "Girokonto", "balance": "890,00", "currency": "EUR"},
    ],
}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:  # noqa: ARG001
    """Return the synthetic accounts for one customer.

    Parameters:
        event: `{"customer_id": str}` directly, or `{"body": "<json>"}` as
            delivered by gateway-style invocations.
        context: Lambda context (unused).

    Returns:
        Accounts payload, or an error shape (`UNKNOWN_CUSTOMER` / `INVALID_REQUEST`).
    """
    payload = event
    if isinstance(event.get("body"), str):
        try:
            payload = json.loads(event["body"])
        except ValueError:
            return {"error": "INVALID_REQUEST"}
    customer_id = payload.get("customer_id") if isinstance(payload, dict) else None
    if not isinstance(customer_id, str) or not customer_id:
        return {"error": "INVALID_REQUEST"}
    accounts = _ACCOUNTS.get(customer_id)
    if accounts is None:
        return {"error": "UNKNOWN_CUSTOMER"}
    return {"customer_id": customer_id, "accounts": accounts}
