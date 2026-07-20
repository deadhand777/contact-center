"""Tests for the balance Lambda handler (loaded by file path)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

_HANDLER_PATH = Path(__file__).parent.parent / "infra" / "lambda" / "balance" / "handler.py"


def _load_handler() -> ModuleType:
    """Load the Lambda handler module without packaging it."""
    spec = importlib.util.spec_from_file_location("balance_handler", _HANDLER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_known_customer_returns_accounts() -> None:
    """A known customer gets all their accounts with masked IBANs."""
    handler = _load_handler()
    result = handler.handler({"customer_id": "KND-1001"}, None)
    assert result["customer_id"] == "KND-1001"
    types = {account["type"] for account in result["accounts"]}
    assert types == {"Girokonto", "Tagesgeld"}
    assert all(account["iban_masked"].startswith("DE**") for account in result["accounts"])
    assert all(account["currency"] == "EUR" for account in result["accounts"])


def test_negative_balance_customer() -> None:
    """KND-1002 has a single Girokonto with a negative balance."""
    handler = _load_handler()
    result = handler.handler({"customer_id": "KND-1002"}, None)
    assert result["accounts"][0]["balance"] == "-127,45"


def test_unknown_customer_returns_error() -> None:
    """Unknown ids yield the UNKNOWN_CUSTOMER error shape, not an exception."""
    handler = _load_handler()
    assert handler.handler({"customer_id": "KND-9999"}, None) == {"error": "UNKNOWN_CUSTOMER"}


def test_malformed_input_returns_error() -> None:
    """Missing customer_id yields INVALID_REQUEST."""
    handler = _load_handler()
    assert handler.handler({}, None) == {"error": "INVALID_REQUEST"}


def test_nested_body_event_is_unwrapped() -> None:
    """Gateway-style events with a JSON string body are handled."""
    handler = _load_handler()
    result = handler.handler({"body": '{"customer_id": "KND-1003"}'}, None)
    assert result["customer_id"] == "KND-1003"
