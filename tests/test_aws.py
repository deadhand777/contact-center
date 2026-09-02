"""Tests for the AWS parameter helpers."""

from __future__ import annotations

from contact_center._internal import aws


class _FakeSSM:
    """Fake SSM client returning a value derived from the parameter name."""

    def get_parameter(self, Name: str) -> dict:  # noqa: N803
        """Return a fake GetParameter response."""
        return {"Parameter": {"Value": f"value-of-{Name}"}}


def test_get_parameter_returns_value() -> None:
    """The helper unwraps the SSM GetParameter response."""
    assert aws.get_parameter(aws.RUNTIME_ARN_PARAM, client=_FakeSSM()) == "value-of-/contact-center/runtime-arn"
