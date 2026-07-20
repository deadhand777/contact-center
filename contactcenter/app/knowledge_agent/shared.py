"""Shared configuration for the contact-center agents (cold-start SSM reads)."""

from __future__ import annotations

import boto3
from strands.models import BedrockModel

REGION = "eu-central-1"
MODEL_ID = "eu.amazon.nova-2-lite-v1:0"

_ssm = boto3.client("ssm", region_name=REGION)


def _required_param(name: str) -> str:
    """Read one required SSM parameter, letting ParameterNotFound propagate at cold start."""
    return _ssm.get_parameter(Name=name)["Parameter"]["Value"]


def _param(name: str, default: str | None = None) -> str | None:
    """Read one optional SSM parameter, returning a default when it is absent."""
    try:
        return _ssm.get_parameter(Name=name)["Parameter"]["Value"]
    except _ssm.exceptions.ParameterNotFound:
        return default


KB_ID = _required_param("/contact-center/kb-id")
GUARDRAIL_ID = _required_param("/contact-center/guardrail-id")
GUARDRAIL_VERSION = _required_param("/contact-center/guardrail-version")
BALANCE_FN_ARN = _param("/contact-center/balance-fn-arn")
GATEWAY_URL = _param("/contact-center/gateway-url")


def build_model() -> BedrockModel:
    """Build the shared guardrailed Bedrock model."""
    return BedrockModel(
        model_id=MODEL_ID,
        region_name=REGION,
        guardrail_id=GUARDRAIL_ID,
        guardrail_version=GUARDRAIL_VERSION,
    )
