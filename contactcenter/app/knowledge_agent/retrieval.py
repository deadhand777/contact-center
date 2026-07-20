"""Knowledge-base retrieval for the contact-center knowledge agent."""

from __future__ import annotations

from typing import Any

NO_RELEVANT_DOCUMENTS = "NO_RELEVANT_DOCUMENTS"


def _format_chunk(result: dict[str, Any]) -> str:
    """Render one retrieve result as a cited context block."""
    uri = result.get("location", {}).get("s3Location", {}).get("uri", "unknown")
    name = uri.rsplit("/", 1)[-1]
    text = result.get("content", {}).get("text", "")
    return f"[Quelle: {name}]\n{text}"


def search(query: str, *, kb_id: str, client: Any, top_k: int = 5, score_floor: float = 0.4) -> str:
    """Query the knowledge base and return cited context blocks.

    Parameters:
        query: Natural-language search query.
        kb_id: Bedrock Knowledge Base ID.
        client: A `bedrock-agent-runtime` boto3 client.
        top_k: Number of chunks to retrieve.
        score_floor: Below this best score, report no relevant documents.

    Returns:
        Context blocks separated by blank lines, or `NO_RELEVANT_DOCUMENTS`.
    """
    response = client.retrieve(
        knowledgeBaseId=kb_id,
        retrievalQuery={"text": query},
        retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": top_k}},
    )
    results = response.get("retrievalResults", [])
    if not results or max(r.get("score", 0.0) for r in results) < score_floor:
        return NO_RELEVANT_DOCUMENTS
    return "\n\n".join(_format_chunk(r) for r in results)
