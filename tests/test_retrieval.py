"""Tests for the knowledge agent's retrieval logic (loaded by file path)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from types import ModuleType

_RETRIEVAL_PATH = Path(__file__).parent.parent / "contactcenter" / "app" / "knowledge_agent" / "retrieval.py"


def _load_retrieval() -> ModuleType:
    """Load the agent's retrieval module without installing the agent project."""
    spec = importlib.util.spec_from_file_location("retrieval", _RETRIEVAL_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeKB:
    """Fake bedrock-agent-runtime client with canned retrieve results."""

    def __init__(self, results: list[dict[str, Any]]) -> None:
        """Store the canned retrieval results."""
        self._results = results

    def retrieve(self, **_: object) -> dict:
        """Return the canned retrieve response."""
        return {"retrievalResults": self._results}


def _result(uri: str, text: str, score: float) -> dict[str, Any]:
    """Build one retrieve result in the Bedrock response shape."""
    return {
        "content": {"text": text},
        "location": {"s3Location": {"uri": uri}},
        "score": score,
    }


def test_search_formats_chunks_with_source_names() -> None:
    """Chunks are rendered with the source file name as citation."""
    retrieval = _load_retrieval()
    client = _FakeKB([_result("s3://bucket/girokonto-gebuehren.md", "4,90 € pro Monat", 0.8)])
    context = retrieval.search("Was kostet das Konto?", kb_id="kb", client=client)
    assert "[Quelle: girokonto-gebuehren.md]" in context
    assert "4,90 € pro Monat" in context


def test_search_below_score_floor_returns_sentinel() -> None:
    """Low-relevance results yield the no-documents sentinel."""
    retrieval = _load_retrieval()
    client = _FakeKB([_result("s3://bucket/faq-english.md", "irrelevant", 0.1)])
    assert retrieval.search("bitcoin?", kb_id="kb", client=client) == retrieval.NO_RELEVANT_DOCUMENTS


def test_search_empty_results_returns_sentinel() -> None:
    """No results yield the no-documents sentinel."""
    retrieval = _load_retrieval()
    assert retrieval.search("?", kb_id="kb", client=_FakeKB([])) == retrieval.NO_RELEVANT_DOCUMENTS
