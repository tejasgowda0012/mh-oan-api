"""
Marqo client implementation for vector search.
"""
import os
import re
import marqo
from typing import Any, Optional, Literal
from pydantic import BaseModel, Field
from pydantic_ai import ModelRetry, RunContext
from agents.deps import FarmerContext
from helpers.utils import get_logger
from agents.tools.terms import normalize_text_with_glossary
from agents.tools.video_payload import (
    VideoResource,
    dedupe_videos,
    humanize_video_title,
    is_http_url,
    video_resource_from_hit,
)
from agents.tools.document_payload import (
    DocumentResource,
    chunk_resource_from_hit,
)
from langfuse import observe

logger = get_logger(__name__)

DocumentType = Literal['video', 'document']

# Max videos attached to AG-UI for inline playback (agent still sees full top_k).
AGUI_VIDEO_LIMIT = 2

# Max documents (grouped by doc_id) attached to AG-UI for grounding validation
# (agent still sees full top_k). Chunk count per document is unbounded (None) so
# every retrieved chunk for a shown document is visible for grounding validation.
AGUI_DOCUMENT_LIMIT = 5
AGUI_CHUNK_LIMIT = None


class SearchHit(BaseModel):
    """Individual search hit from elasticsearch / Marqo"""
    name: str
    text: str
    doc_id: str
    type: str
    source: str
    source_mr: Optional[str] = None
    score: float = Field(alias="_score")
    id: str = Field(alias="_id")
    lang_code: str = Field(default="mr")

    @property
    def processed_text(self) -> str:
        """Returns the text with cleaned up whitespace and newlines"""
        cleaned = re.sub(r'\n{2,}', '\n\n', self.text)
        cleaned = re.sub(r'\t+', '\t', cleaned)
        if self.lang_code != "en":
            glossary_lang = "hi" if self.lang_code == "bhb" else self.lang_code
            cleaned = normalize_text_with_glossary(cleaned, target_lang=glossary_lang)
        return cleaned

    @property
    def citation_source(self) -> Optional[str]:
        """Language-specific source name passed to the agent — never cross-language."""
        if self.lang_code == "en":
            return self.source or None
        return self.source_mr or None

    @property
    def video_url(self) -> Optional[str]:
        """Playback URL for video hits (prefer real http(s) links only)."""
        if self.type != "video":
            return None
        for candidate in (self.source, self.source_mr, self.citation_source):
            if is_http_url(candidate):
                return candidate
        return None

    def to_video_resource(self) -> Optional[VideoResource]:
        url = self.video_url
        if not url:
            return None
        return video_resource_from_hit(
            doc_id=self.doc_id or self.id,
            title=self.name,
            url=url,
            description=self.processed_text,
            source=None,
        )

    def __str__(self) -> str:
        body = "```\n" + self.processed_text + "\n```\n"
        lines = [f"**{self.name}**"]

        if self.type == 'video':
            title = humanize_video_title(self.name)
            url = self.video_url
            if url:
                return f"**[{title}]({url})**\n" + body
            return f"**{title}**\n" + body

        if self.citation_source:
            lines.append(f"Source: {self.citation_source}")

        return "\n".join(lines) + "\n" + body


def collect_video_resources(hits: list[SearchHit], limit: int = AGUI_VIDEO_LIMIT) -> list[VideoResource]:
    """
    Playable AG-UI payloads from Marqo hits (http(s) URLs only).
    Dedupes by URL so one YouTube video is not shown many times from chunk rows.
    """
    resources: list[VideoResource] = []
    for hit in hits:
        resource = hit.to_video_resource()
        if resource:
            resources.append(resource)
    return dedupe_videos(resources)[:limit]


def dedupe_video_hits(hits: list[SearchHit]) -> list[SearchHit]:
    """Keep Marqo order; one chunk per playable URL (or name if no URL)."""
    seen_urls: set[str] = set()
    seen_names: set[str] = set()
    out: list[SearchHit] = []
    for hit in hits:
        url = (hit.video_url or "").strip()
        name_key = (hit.name or "").strip().lower()
        if url:
            if url in seen_urls:
                continue
            seen_urls.add(url)
        elif name_key:
            if name_key in seen_names:
                continue
            seen_names.add(name_key)
        out.append(hit)
    return out


def collect_document_resources(
    hits: list[SearchHit],
    doc_limit: int = AGUI_DOCUMENT_LIMIT,
    chunk_limit: Optional[int] = AGUI_CHUNK_LIMIT,
) -> list[DocumentResource]:
    """
    Group Marqo document hits into documents, each carrying its retrieved chunks.

    One document (doc_id) may come back as several chunk rows; those are grouped
    under a single DocumentResource so a reviewer can see the exact passages the
    model received. Marqo relevance order is preserved; ``doc_limit`` caps distinct
    documents. ``chunk_limit`` of ``None`` keeps every chunk retrieved for a shown
    document. Each document's ``score`` is its best chunk score.
    """
    docs: dict[str, DocumentResource] = {}
    for hit in hits:
        key = hit.doc_id or hit.id
        if key not in docs:
            if len(docs) >= doc_limit:
                continue  # document cap reached; skip new documents (keep top ones)
            docs[key] = DocumentResource(
                id=key,
                title=(hit.name or "Document").strip(),
                source=hit.citation_source,
                chunks=[],
                score=None,
            )
        doc = docs[key]
        if chunk_limit is None or len(doc.chunks) < chunk_limit:
            doc.chunks.append(
                chunk_resource_from_hit(chunk_id=hit.id, text=hit.processed_text, score=hit.score)
            )
        if hit.score is not None and (doc.score is None or hit.score > doc.score):
            doc.score = round(hit.score, 4)
    return list(docs.values())


def _marqo_hybrid_search(
    *,
    client: marqo.Client,
    index_name: str,
    query: str,
    top_k: int,
    type_filter: Literal["document", "video"],
) -> list[dict]:
    """Shared hybrid search used by search_documents and search_videos."""
    search_params = {
        "q": query,
        "limit": top_k,
        "filter_string": f"type:{type_filter}",
        "search_method": "hybrid",
        "hybrid_parameters": {
            "retrievalMethod": "disjunction",
            "rankingMethod": "rrf",
            "alpha": 0.5,
            "rrfK": 60,
        },
    }
    return client.index(index_name).search(**search_params)["hits"]


@observe(name="tool:search_documents", as_type="tool")
async def search_documents(
    ctx: RunContext[FarmerContext],
    query: str,
    top_k: int = 10,
) -> str:
    """
    Semantic search for documents. Use this tool to search for relevant documents.

    Typical flow (same as crop advisory prompts):
      search_terms → English query → search_documents → search_videos

    Args:
        query: The search query in *English* (required)
        top_k: Maximum number of results to return (default: 10)

    Returns:
        search_results: Formatted list of documents
    """
    try:
        endpoint_url = os.getenv('MARQO_ENDPOINT_URL')
        if not endpoint_url:
            raise ValueError("Marqo endpoint URL is required")

        index_name = os.getenv('MARQO_INDEX_NAME', 'mh-new-index')
        if not index_name:
            raise ValueError("Marqo index name is required")

        client = marqo.Client(url=endpoint_url)
        logger.info(f"Searching documents for '{query}' in index '{index_name}'")

        results = _marqo_hybrid_search(
            client=client,
            index_name=index_name,
            query=query,
            top_k=top_k,
            type_filter="document",
        )

        if len(results) == 0:
            return f"No results found for `{query}`"

        lang_code = ctx.deps.lang_code
        search_hits = [SearchHit(**hit, lang_code=lang_code) for hit in results]

        # AG-UI: store the retrieved documents (grouped by doc_id, each carrying its
        # retrieved chunks) for grounding validation. The agent still receives the
        # full text below.
        resources = collect_document_resources(
            search_hits, doc_limit=AGUI_DOCUMENT_LIMIT, chunk_limit=AGUI_CHUNK_LIMIT
        )
        if resources:
            ctx.deps.add_related_documents([r.to_ag_ui_dict() for r in resources])
            logger.info(
                "search_documents AG-UI documents=%s chunks=%s session=%s",
                [r.title for r in resources],
                [len(r.chunks) for r in resources],
                getattr(ctx.deps, "session_id", ""),
            )

        document_string = "\n\n----\n\n".join(str(document) for document in search_hits)
        return "> Search Results for `" + query + "`\n\n" + document_string
    except Exception as e:
        logger.error(f"Error searching documents: {e} for query: {query}")
        raise ModelRetry(f"Error searching documents, please try again")


@observe(name="tool:search_videos", as_type="tool")
async def search_videos(
    ctx: RunContext[FarmerContext],
    query: str,
    top_k: int = 10,
) -> str:
    """
    Semantic search for videos — same Marqo hybrid path as search_documents.

    Differences from search_documents:
      - filter_string is type:video
      - URL dedupe (ingest may split one YouTube into many chunk rows)
      - up to AGUI_VIDEO_LIMIT playable hits stored on FarmerContext for AG-UI

    Typical flow: search_terms → English query → search_documents → search_videos
    (same English topic). FAQ / app-help may call search_videos alone.

    Args:
        query: The search query in *English* (required) — same topic as documents
        top_k: Maximum number of results to return (default: 10)

    Returns:
        Formatted list of videos, or "No videos found for `query`"
    """
    try:
        endpoint_url = os.getenv('MARQO_ENDPOINT_URL')
        if not endpoint_url:
            raise ValueError("Marqo endpoint URL is required")

        index_name = os.getenv('MARQO_INDEX_NAME', 'mh-new-index')
        if not index_name:
            raise ValueError("Marqo index name is required")

        client = marqo.Client(url=endpoint_url)
        logger.info(f"Searching videos for '{query}' in index '{index_name}' limit={top_k}")

        results = _marqo_hybrid_search(
            client=client,
            index_name=index_name,
            query=query,
            top_k=top_k,
            type_filter="video",
        )

        if len(results) == 0:
            return (
                f"No videos found for `{query}`.\n"
                "Do not show or invent videos. If the farmer asks for videos on this "
                "topic, say no videos are available."
            )

        lang_code = ctx.deps.lang_code
        search_hits = dedupe_video_hits(
            [SearchHit(**hit, lang_code=lang_code) for hit in results]
        )
        logger.info(
            "search_videos hits=%s top=%s query=%r",
            len(search_hits),
            [(h.name, round(h.score, 4)) for h in search_hits[:5]],
            query,
        )

        resources = collect_video_resources(search_hits, limit=AGUI_VIDEO_LIMIT)
        if resources:
            payload: list[dict[str, Any]] = [r.to_ag_ui_dict() for r in resources]
            ctx.deps.add_related_videos(payload)
            logger.info(
                "search_videos AG-UI videos=%s session=%s",
                [r.title for r in resources],
                getattr(ctx.deps, "session_id", ""),
            )

        video_string = "\n\n----\n\n".join(str(document) for document in search_hits)
        return "> Videos for `" + query + "`\n\n" + video_string

    except Exception as e:
        logger.error(f"Error searching videos: {e} for query: {query}")
        raise ModelRetry(f"Error searching videos, please try again")
