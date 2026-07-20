"""Contact-center supervisor agent on Bedrock AgentCore Runtime."""

from __future__ import annotations

import json
import logging

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent

import banking
from banking import ask_banking_agent
from contract import escalation_log_record, parse_supervisor_output
from knowledge import ask_knowledge_agent
from shared import build_model

_LOGGER = logging.getLogger(__name__)

SUPERVISOR_PROMPT = """\
Du bist der digitale Assistent der Musterbank und koordinierst zwei
Spezialisten. Antworte auf Deutsch, oder auf Englisch wenn die Frage auf
Englisch gestellt wurde.

Routing:
- Fragen zu Konten, Salden, Umsätzen: nutze ask_banking_agent.
- Fragen zu Produkten, Gebühren, Krediten, Abläufen: nutze ask_knowledge_agent.

Eskalation — setze "escalate" auf true mit kurzem deutschen "reason", wenn:
- die Kundin oder der Kunde ausdrücklich einen Menschen sprechen möchte
  (reason: "Kundenwunsch"),
- es um Details einer konkreten Kreditablehnung geht, die über die
  dokumentierten Ablehnungsgründe hinausgehen (reason: "Sensibles Thema
  Kreditablehnung"),
- ein Spezialist NO_RELEVANT_DOCUMENTS, KNOWLEDGE_BASE_UNAVAILABLE,
  BANKING_UNAVAILABLE, UNKNOWN_CUSTOMER oder NO_CUSTOMER meldet
  (reason: "Systemfehler Kontodienst" bei BANKING_UNAVAILABLE,
  "Kunde nicht identifiziert" bei UNKNOWN_CUSTOMER/NO_CUSTOMER,
  sonst "Keine gesicherte Antwort möglich"),
- keiner der Spezialisten die Frage beantworten kann.

Antworte IMMER ausschließlich mit einem JSON-Objekt dieser Form:
{"answer": "<Antworttext für den Kunden>", "escalate": true/false, "reason": "<kurzer Grund>" oder null}
Bei Eskalation enthält "answer" eine höfliche Übergabe-Formulierung.
Übernimm Quellenangaben [Quelle: ...] des Wissens-Spezialisten unverändert in "answer".
"""

_supervisor = Agent(
    model=build_model(),
    system_prompt=SUPERVISOR_PROMPT,
    tools=[ask_knowledge_agent, ask_banking_agent],
)

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: dict, context: object = None) -> dict:
    """Handle one runtime invocation.

    Args:
        payload: Request payload; expects "prompt" and optionally "customer_id".
        context: Runtime context; its session id is logged when available.

    Returns:
        The response contract: {"answer", "escalate", "reason"}.
    """
    banking.set_customer(payload.get("customer_id"))
    prompt = payload.get("prompt", "")
    customer = payload.get("customer_id")
    context_line = f"Authentifizierter Kunde: {customer}\n" if customer else "Kein Kunde authentifiziert.\n"
    result = _supervisor(context_line + prompt)
    response = parse_supervisor_output(str(result))
    session_id = getattr(context, "session_id", None)
    _LOGGER.info(json.dumps(escalation_log_record(session_id, customer, response), ensure_ascii=False))
    return response


if __name__ == "__main__":
    app.run()
