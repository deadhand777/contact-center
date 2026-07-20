"""Knowledge sub-agent: grounded product/policy answers with citations."""

from __future__ import annotations

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from strands import Agent, tool

from retrieval import search
from shared import REGION, build_model

_kb_client = boto3.client(
    "bedrock-agent-runtime",
    region_name=REGION,
    config=Config(retries={"mode": "standard", "max_attempts": 2}),
)

KNOWLEDGE_PROMPT = """\
Du bist der Wissens-Assistent der Musterbank. Du beantwortest Kundenfragen
ausschließlich auf Basis der Wissensdatenbank. Antworte auf Deutsch, oder auf
Englisch wenn die Frage auf Englisch gestellt wurde.

Regeln:
- Nutze für jede fachliche Frage das Werkzeug search_knowledge_base.
- Zitiere deine Quellen im Format [Quelle: <dateiname>] am Ende jeder Aussage,
  die auf der Wissensdatenbank beruht. Übernimm die Quellenangaben aus dem
  Werkzeug-Ergebnis.
- Wenn das Werkzeug NO_RELEVANT_DOCUMENTS meldet: Antworte exakt mit
  NO_RELEVANT_DOCUMENTS.
- Wenn das Werkzeug KNOWLEDGE_BASE_UNAVAILABLE meldet: Antworte exakt mit
  KNOWLEDGE_BASE_UNAVAILABLE.
- Keine Anlageberatung, keine Rechtsberatung, keine Auskünfte zu konkreten
  Kundenkonten.
"""


@tool
def search_knowledge_base(query: str) -> str:
    """Search the bank knowledge base for passages relevant to the query.

    Args:
        query: The customer question or a search phrase derived from it.

    Returns:
        Cited context blocks, NO_RELEVANT_DOCUMENTS if nothing relevant, or
        KNOWLEDGE_BASE_UNAVAILABLE on a retrieval outage.
    """
    from shared import KB_ID  # late import: resolved once at cold start in shared

    try:
        return search(query, kb_id=KB_ID, client=_kb_client)
    except ClientError:
        return "KNOWLEDGE_BASE_UNAVAILABLE"


_agent = Agent(model=build_model(), system_prompt=KNOWLEDGE_PROMPT, tools=[search_knowledge_base])


@tool
def ask_knowledge_agent(question: str) -> str:
    """Answer a product, fee, or policy question from the bank knowledge base.

    Args:
        question: The customer's question, verbatim.

    Returns:
        A grounded answer with [Quelle: ...] citations, or a failure sentinel
        (NO_RELEVANT_DOCUMENTS / KNOWLEDGE_BASE_UNAVAILABLE).
    """
    return str(_agent(question))
