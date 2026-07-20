"""AWS configuration shared by the chat harness and integration tests."""

from __future__ import annotations

from typing import Any

import boto3

REGION = "eu-central-1"
KB_ID_PARAM = "/contact-center/kb-id"
GUARDRAIL_ID_PARAM = "/contact-center/guardrail-id"
GUARDRAIL_VERSION_PARAM = "/contact-center/guardrail-version"
RUNTIME_ARN_PARAM = "/contact-center/runtime-arn"
CONNECT_INSTANCE_PARAM = "/contact-center/connect-instance-id"
CONTACT_FLOW_PARAM = "/contact-center/contact-flow-id"
ESCALATION_QUEUE_PARAM = "/contact-center/escalation-queue-id"


def get_parameter(name: str, client: Any | None = None) -> str:
    """Read a string parameter from SSM Parameter Store.

    Parameters:
        name: Full parameter name, e.g. `/contact-center/kb-id`.
        client: Optional pre-built SSM client (used by tests).

    Returns:
        The parameter value.
    """
    ssm = client or boto3.client("ssm", region_name=REGION)
    return ssm.get_parameter(Name=name)["Parameter"]["Value"]
