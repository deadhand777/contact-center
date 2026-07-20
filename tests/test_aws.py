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
    assert aws.get_parameter("/contact-center/kb-id", client=_FakeSSM()) == "value-of-/contact-center/kb-id"


def test_parameter_name_constants() -> None:
    """Parameter names match the CDK stack outputs."""
    assert aws.KB_ID_PARAM == "/contact-center/kb-id"
    assert aws.RUNTIME_ARN_PARAM == "/contact-center/runtime-arn"
