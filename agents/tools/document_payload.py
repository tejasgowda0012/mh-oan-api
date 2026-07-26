"""Structured document payloads for AG-UI grounding validation.

Mirrors ``video_payload.py`` but for ``search_documents`` results. The purpose
is *validation*: the frontend renders the documents a turn retrieved so a reviewer
can check whether the assistant's answer was actually grounded in them.

Unlike videos, documents are shown at **chunk level, grouped by document**: each
document card carries the individual retrieved chunks (the exact passages passed
to the model), so a reviewer can pinpoint the supporting text — not just a whole
document.
"""

from __future__ import annotations

import re
from typing import Any, List, Optional

from pydantic import BaseModel, Field


class ChunkResource(BaseModel):
    """A single retrieved chunk (one Marqo row) shown under its document."""

    id: str                              # chunk row id (Marqo _id)
    text: str = ""                       # chunk text passed to the model
    score: Optional[float] = Field(default=None, description="Chunk relevance score")

    def to_ag_ui_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class DocumentResource(BaseModel):
    """A retrieved document plus the chunks of it that came back this turn."""

    id: str                              # doc_id
    title: str                           # document name
    source: Optional[str] = None         # language-specific citation source
    chunks: List[ChunkResource] = Field(default_factory=list)
    score: Optional[float] = Field(default=None, description="Best (max) chunk score")

    def to_ag_ui_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


def _clean_text(text: Optional[str]) -> str:
    """Collapse excessive blank lines/tabs but keep the full content for validation."""
    if not text:
        return ""
    cleaned = re.sub(r"\n{3,}", "\n\n", text)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    return cleaned.strip()


def chunk_resource_from_hit(
    *,
    chunk_id: str,
    text: Optional[str] = None,
    score: Optional[float] = None,
) -> ChunkResource:
    return ChunkResource(
        id=chunk_id,
        text=_clean_text(text),
        score=round(score, 4) if isinstance(score, (int, float)) else None,
    )
