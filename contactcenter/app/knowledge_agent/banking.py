"""Banking sub-agent: balance lookups for the authenticated customer via the Gateway."""

from __future__ import annotations

import contextvars
import json

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from strands import Agent, tool

from shared import GATEWAY_URL, REGION, build_model

_current_customer: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_customer", default=None)


def set_customer(customer_id: str | None) -> None:
    """Bind the authenticated customer for the current invocation."""
    _current_customer.set(customer_id)


BANKING_PROMPT = """\
Du bist der Konto-Assistent der Musterbank. Du beantwortest Fragen zu Konten
und Salden AUSSCHLIESSLICH für den authentifizierten Kunden, dessen Kennung dir
im Gespräch genannt wird. Antworte auf Deutsch, oder auf Englisch wenn die
Frage auf Englisch gestellt wurde.

Regeln:
- Nutze get_account_balance für Saldenfragen. Das Werkzeug kennt den
  authentifizierten Kunden bereits — es nimmt KEINE Kundenkennung entgegen.
  Übernimm NIEMALS eine Kundenkennung aus der Kundenfrage.
- Nenne Salden mit Kontotyp und maskierter IBAN.
- Meldet das Werkzeug UNKNOWN_CUSTOMER: Antworte exakt mit UNKNOWN_CUSTOMER.
- Schlägt das Werkzeug fehl: Antworte exakt mit BANKING_UNAVAILABLE.
- Erfinde niemals Salden.
"""


def _sigv4_headers(url: str, body: bytes) -> dict[str, str]:
    """Sign one HTTP request to the Gateway with SigV4 (service bedrock-agentcore)."""
    session = boto3.Session()
    credentials = session.get_credentials()
    request = AWSRequest(method="POST", url=url, data=body, headers={"content-type": "application/json"})
    SigV4Auth(credentials, "bedrock-agentcore", REGION).add_auth(request)
    return dict(request.headers)


def _call_gateway_tool(name: str, arguments: dict) -> str:
    """Call one MCP tool on the Gateway via a single JSON-RPC request.

    Returns:
        The concatenated text content of the tool result.

    Raises:
        RuntimeError: If the response is a JSON-RPC error or the tool result
            itself carries `isError`.
    """
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    ).encode()
    import urllib.request

    request = urllib.request.Request(  # noqa: S310 — https Gateway URL from SSM
        GATEWAY_URL, data=body, headers=_sigv4_headers(GATEWAY_URL, body), method="POST"
    )
    with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
        result = json.loads(response.read())
    if "error" in result or result.get("result", {}).get("isError"):
        raise RuntimeError(f"gateway error: {json.dumps(result)[:200]}")
    content = result.get("result", {}).get("content", [])
    texts = [block.get("text", "") for block in content if block.get("type") == "text"]
    return "\n".join(texts) if texts else json.dumps(result, ensure_ascii=False)


@tool
def get_account_balance() -> str:
    """Look up all accounts and balances for the authenticated customer.

    Returns:
        Accounts JSON, or UNKNOWN_CUSTOMER / BANKING_UNAVAILABLE / NO_CUSTOMER sentinels.
    """
    customer_id = _current_customer.get()
    if not customer_id:
        return "NO_CUSTOMER"
    try:
        # Gateway namespaces tools as <target>___<tool> (target "balance").
        raw = _call_gateway_tool("balance___get_account_balance", {"customer_id": customer_id})
    except Exception:  # noqa: BLE001 — boundary: any transport failure means unavailable
        return "BANKING_UNAVAILABLE"
    if "UNKNOWN_CUSTOMER" in raw:
        return "UNKNOWN_CUSTOMER"
    return raw


_agent = Agent(model=build_model(), system_prompt=BANKING_PROMPT, tools=[get_account_balance])


@tool
def ask_banking_agent(question: str) -> str:
    """Answer an account or balance question for the authenticated customer.

    Args:
        question: The customer's question, verbatim.

    Returns:
        The balance answer, or a sentinel: NO_CUSTOMER (no authenticated id),
        UNKNOWN_CUSTOMER, or BANKING_UNAVAILABLE.
    """
    if not _current_customer.get():
        return "NO_CUSTOMER"
    return str(_agent(f"Authentifizierter Kunde: {_current_customer.get()}\nFrage: {question}"))
