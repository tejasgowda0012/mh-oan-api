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
from langfuse import observe

logger = get_logger(__name__)

DocumentType = Literal['video', 'document']

# Max videos attached to AG-UI for inline playback (agent still sees full top_k).
AGUI_VIDEO_LIMIT = 2


class SearchHit(BaseModel):
    """Individual search hit from elasticsearch"""
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
    """Build de-duplicated structured video resources from search hits (for AG-UI)."""
    resources: list[VideoResource] = []
    for hit in hits:
        resource = hit.to_video_resource()
        if resource:
            resources.append(resource)
    return dedupe_videos(resources)[:limit]


@observe(name="tool:search_documents", as_type="tool")
async def search_documents(
    ctx: RunContext[FarmerContext],
    query: str,
    top_k: int = 10,
) -> str:
    """
    Semantic search for documents. Use this tool to search for relevant documents.
    
    Args:
        query: The search query in *English* (required)
        top_k: Maximum number of results to return (default: 10)
        
    Returns:
        search_results: Formatted list of documents
    """
    try:
        # Initialize Marqo client
        endpoint_url = os.getenv('MARQO_ENDPOINT_URL')
        if not endpoint_url:
            raise ValueError("Marqo endpoint URL is required")
        
        index_name = os.getenv('MARQO_INDEX_NAME', 'mh-new-index')
        if not index_name:
            raise ValueError("Marqo index name is required")
        
        client = marqo.Client(url=endpoint_url)
        logger.info(f"Searching for '{query}' in index '{index_name}'")

        search_params = {
            "q": query,
            "limit": top_k,
            "filter_string": "type:document",
            "search_method": "hybrid",
            "hybrid_parameters": {
                "retrievalMethod": "disjunction",
                "rankingMethod": "rrf",
                "alpha": 0.5,
                "rrfK": 60,
            },        
        }
        
        results = client.index(index_name).search(**search_params)['hits']
        
        if len(results) == 0:
            return f"No results found for `{query}`"
        else:
            lang_code = ctx.deps.lang_code
            search_hits = [SearchHit(**hit, lang_code=lang_code) for hit in results]
            document_string = '\n\n----\n\n'.join([str(document) for document in search_hits])
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
    Semantic search for videos — same Marqo pattern as search_documents.

    Call **after** search_documents in the same turn with the same English topic.
    filter_string is type:video; retrieval is hybrid (identical params to documents).
    If hybrid returns no hits, falls back to tensor once (video index quirk).

    Stores up to AGUI_VIDEO_LIMIT playable hits on FarmerContext.related_videos
    for AG-UI. If Marqo returns no hits, returns a short empty message only
    (never invents substitute videos).

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

        # Identical hybrid setup to search_documents; only filter_string differs.
        hybrid_params = {
            "q": query,
            "limit": top_k,
            "filter_string": "type:video",
            "search_method": "hybrid",
            "hybrid_parameters": {
                "retrievalMethod": "disjunction",
                "rankingMethod": "rrf",
                "alpha": 0.5,
                "rrfK": 60,
            },
        }

        results: list = []
        try:
            results = client.index(index_name).search(**hybrid_params)["hits"]
        except Exception as hybrid_err:
            logger.warning("search_videos hybrid error (%s); trying tensor", hybrid_err)

        # Some video-only indexes return empty for hybrid; tensor matches documents' recall better there.
        if not results:
            logger.info("search_videos hybrid empty; falling back to tensor query=%r", query)
            results = client.index(index_name).search(
                q=query,
                limit=top_k,
                filter_string="type:video",
                search_method="tensor",
            )["hits"]

        if len(results) == 0:
            return f"No videos found for `{query}`"

        lang_code = ctx.deps.lang_code
        search_hits = [SearchHit(**hit, lang_code=lang_code) for hit in results]
        logger.info(
            "search_videos hits=%s top=%s query=%r",
            len(search_hits),
            [(h.name, round(h.score, 4)) for h in search_hits[:3]],
            query,
        )

        # AG-UI: same ranking as agent sees; only need playable http(s) URLs.
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
