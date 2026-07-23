"""Structured document payloads for AG-UI grounding validation.

Mirrors ``video_payload.py`` but for ``search_documents`` results. The purpose
is *validation*: the frontend renders each retrieved document so a reviewer can
check whether the assistant's answer was actually grounded in the documents and,
if so, which one. Each card therefore carries the full retrieved text (not just a
teaser), plus title, source citation, and relevance score.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, Field


class DocumentResource(BaseModel):
    """A single retrieved document the frontend can render for validation."""

    id: str
    title: str
    source: Optional[str] = None       # language-specific citation source
    text: str = ""                     # full retrieved document text (for grounding checks)
    score: Optional[float] = Field(default=None, description="Relevance score")

    def to_ag_ui_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


def _clean_text(text: Optional[str]) -> str:
    """Collapse excessive blank lines/tabs but keep the full content for validation."""
    if not text:
        return ""
    cleaned = re.sub(r"\n{3,}", "\n\n", text)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    return cleaned.strip()


def document_resource_from_hit(
    *,
    doc_id: str,
    title: str,
    source: Optional[str] = None,
    text: Optional[str] = None,
    score: Optional[float] = None,
) -> DocumentResource:
    return DocumentResource(
        id=doc_id or title,
        title=(title or "Document").strip(),
        source=source or None,
        text=_clean_text(text),
        score=round(score, 4) if isinstance(score, (int, float)) else None,
    )


def dedupe_documents(documents: list[DocumentResource]) -> list[DocumentResource]:
    """Dedupe by doc id (ingest may split one document into many chunk rows)."""
    seen: set[str] = set()
    out: list[DocumentResource] = []
    for doc in documents:
        key = doc.id or doc.title
        if key in seen:
            continue
        seen.add(key)
        out.append(doc)
    return out
