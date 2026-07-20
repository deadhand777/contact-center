"""Chat harness that talks to the deployed AgentCore runtime."""

from __future__ import annotations

import json
import uuid
from typing import Any

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

from contact_center._internal import aws


def ask(
    prompt: str,
    *,
    runtime_arn: str,
    session_id: str,
    client: Any,
    customer_id: str | None = None,
) -> dict:
    """Send one prompt to the deployed agent and return the response contract.

    Parameters:
        prompt: The user question.
        runtime_arn: ARN of the AgentCore runtime.
        session_id: Session identifier (33+ characters, per AgentCore).
        client: A `bedrock-agentcore` boto3 client.
        customer_id: Authenticated customer id forwarded to the agent.

    Returns:
        `{"answer": str, "escalate": bool, "reason": str | None}`.
    """
    payload: dict[str, Any] = {"prompt": prompt}
    if customer_id is not None:
        payload["customer_id"] = customer_id
    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        runtimeSessionId=session_id,
        qualifier="DEFAULT",
        payload=json.dumps(payload),
    )
    body = response["response"]
    raw = body.read() if hasattr(body, "read") else b"".join(body)
    data = json.loads(raw)
    if isinstance(data, dict) and "answer" in data:
        return {
            "answer": str(data["answer"]),
            "escalate": bool(data.get("escalate", False)),
            "reason": data.get("reason"),
        }
    return {"answer": json.dumps(data, ensure_ascii=False), "escalate": False, "reason": None}


def render(response: dict) -> str:
    """Format a response contract for the terminal.

    Parameters:
        response: The contract dict returned by `ask`.

    Returns:
        The answer, plus a handoff banner line when escalation was requested.
    """
    if response.get("escalate"):
        return f"{response['answer']}\n⚠ Übergabe an Mitarbeiter: {response.get('reason') or 'unbekannt'}"
    return str(response["answer"])


def run_chat(question: str | None = None, customer_id: str = "KND-1001") -> int:
    """Run a one-shot question or an interactive chat loop.

    Parameters:
        question: If given, ask once and exit; otherwise start a REPL.
        customer_id: Authenticated customer id for banking questions.

    Returns:
        An exit code.

    Raises:
        SystemExit: With a named remediation when AWS credentials or the
            runtime ARN parameter are missing.
    """
    try:
        runtime_arn = aws.get_parameter(aws.RUNTIME_ARN_PARAM)
    except NoCredentialsError as error:
        raise SystemExit("No AWS credentials resolved — set AWS_PROFILE for the dev account.") from error
    except ClientError as error:
        message = (
            f"Cannot read {aws.RUNTIME_ARN_PARAM} — run 'cdk deploy' (infra/), deploy the agent "
            "with 'agentcore deploy', then publish the runtime ARN (see plan Task 7)."
        )
        raise SystemExit(message) from error
    client = boto3.client("bedrock-agentcore", region_name=aws.REGION)
    session_id = uuid.uuid4().hex + uuid.uuid4().hex[:8]
    if question is not None:
        print(  # noqa: T201
            render(ask(question, runtime_arn=runtime_arn, session_id=session_id, client=client, customer_id=customer_id)),
        )
        return 0
    while True:  # pragma: no cover (interactive loop)
        try:
            prompt = input("you> ")
        except (EOFError, KeyboardInterrupt):
            return 0
        if prompt.strip().lower() in {"exit", "quit"}:
            return 0
        print(  # noqa: T201
            render(ask(prompt, runtime_arn=runtime_arn, session_id=session_id, client=client, customer_id=customer_id)),
        )
