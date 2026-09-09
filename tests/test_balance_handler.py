"""Tests for the balance Lambda handler (loaded by file path)."""

from __future__ import annotations

from tests import load_module


def test_known_customer_returns_accounts() -> None:
    """A known customer gets all their accounts with masked IBANs."""
    handler = load_module("balance_handler")
    result = handler.handler({"customer_id": "KND-1001"}, None)
    assert result["customer_id"] == "KND-1001"
    types = {account["type"] for account in result["accounts"]}
    assert types == {"Girokonto", "Tagesgeld"}
    assert all(account["iban_masked"].startswith("DE**") for account in result["accounts"])
    assert all(account["currency"] == "EUR" for account in result["accounts"])


def test_negative_balance_customer() -> None:
    """KND-1002 has a single Girokonto with a negative balance."""
    handler = load_module("balance_handler")
    result = handler.handler({"customer_id": "KND-1002"}, None)
    assert result["accounts"][0]["balance"] == "-127,45"


def test_unknown_customer_returns_error() -> None:
    """Unknown ids yield the UNKNOWN_CUSTOMER error shape, not an exception."""
    handler = load_module("balance_handler")
    assert handler.handler({"customer_id": "KND-9999"}, None) == {"error": "UNKNOWN_CUSTOMER"}


def test_malformed_input_returns_error() -> None:
    """Missing customer_id yields INVALID_REQUEST."""
    handler = load_module("balance_handler")
    assert handler.handler({}, None) == {"error": "INVALID_REQUEST"}


def test_nested_body_event_is_unwrapped() -> None:
    """Gateway-style events with a JSON string body are handled."""
    handler = load_module("balance_handler")
    result = handler.handler({"body": '{"customer_id": "KND-1003"}'}, None)
    assert result["customer_id"] == "KND-1003"
