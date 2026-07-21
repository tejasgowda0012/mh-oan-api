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

# Max videos exposed to AG-UI players per tool call (prompt also caps display at 2).
AGUI_VIDEO_LIMIT = 2
# Default retrieval size before relevance filtering (keep small — more noise above this).
DEFAULT_VIDEO_TOP_K = 5
# Drop weak tensor matches so unrelated popular videos (e.g. cotton) do not fill SMAM queries.
# Override with MARQO_VIDEO_MIN_SCORE (0–1 typical for Marqo tensor scores).
DEFAULT_VIDEO_MIN_SCORE = float(os.getenv("MARQO_VIDEO_MIN_SCORE", "0.55"))


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
            source=None,  # never pass slug names like Groundnut_Pest_Control
        )

    def __str__(self) -> str:
        body = "```\n" + self.processed_text + "\n```\n"
        lines = [f"**{self.name}**"]

        if self.type == 'video':
            # Human title for agent context only — never present slug as "Source".
            title = humanize_video_title(self.name)
            url = self.video_url
            if url:
                return f"**{title}**\nPlayback URL (do not show URL or slug names to farmer): {url}\n" + body
            return f"**{title}**\n" + body

        if self.citation_source:
            lines.append(f"Source: {self.citation_source}")

        return "\n".join(lines) + "\n" + body


def _query_keywords(query: str) -> set[str]:
    """Content words from the video search query (English)."""
    stop = {
        "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "with",
        "how", "what", "when", "where", "why", "is", "are", "about", "tell",
        "me", "any", "video", "videos", "guidance", "related", "please",
        "scheme", "government", "application", "status", "information",
    }
    tokens = re.findall(r"[a-z0-9]+", (query or "").lower())
    return {t for t in tokens if len(t) >= 3 and t not in stop}


def hit_matches_query(hit: SearchHit, query: str) -> bool:
    """
    True only when a content keyword from the query appears in title/transcript.

    Policy: prefer returning *no* videos over off-topic ones. Vague queries with
    no content keywords never match (caller returns empty).
    """
    keywords = _query_keywords(query)
    if not keywords:
        return False
    haystack = f"{hit.name} {hit.text}".lower().replace("_", " ").replace("-", " ")
    return any(kw in haystack for kw in keywords)


def filter_relevant_video_hits(
    hits: list[SearchHit],
    query: str,
    min_score: float = DEFAULT_VIDEO_MIN_SCORE,
) -> list[SearchHit]:
    """
    Keep only hits that are both strong enough and on-topic.

    If nothing passes, return [] — never fall back to raw top-K.
    """
    keywords = _query_keywords(query)
    if not keywords:
        logger.info(
            "search_videos empty result: query has no content keywords query=%r",
            query,
        )
        return []

    kept: list[SearchHit] = []
    for hit in hits:
        if hit.score < min_score:
            logger.info(
                "search_videos drop low score name=%s score=%.4f min=%.4f query=%s",
                hit.name,
                hit.score,
                min_score,
                query,
            )
            continue
        if not hit_matches_query(hit, query):
            logger.info(
                "search_videos drop off-topic name=%s score=%.4f query=%s",
                hit.name,
                hit.score,
                query,
            )
            continue
        kept.append(hit)
    return kept


def collect_video_resources(hits: list[SearchHit], limit: int = AGUI_VIDEO_LIMIT) -> list[VideoResource]:
    """Build de-duplicated structured video resources from *already filtered* hits."""
    resources: list[VideoResource] = []
    for hit in hits:
        resource = hit.to_video_resource()
        if resource:
            resources.append(resource)
    return dedupe_videos(resources)[:limit]


_NO_RELEVANT_VIDEOS_MSG = (
    "No relevant videos found for this topic.\n"
    "Do NOT write 'For more information, watch the videos below.'\n"
    "Do NOT invent, guess, or recommend any other videos.\n"
    "Continue with the text answer only (no video section)."
)


def format_videos_for_agent(query: str, hits: list[SearchHit]) -> str:
    """Agent-facing tool return. Empty hits ⇒ no videos for UI or farmer text."""
    if not hits:
        return f"No relevant videos found for `{query}`.\n{_NO_RELEVANT_VIDEOS_MSG}"
    video_string = "\n\n----\n\n".join(str(document) for document in hits)
    return (
        f"> Relevant videos for `{query}`\n\n"
        f"{video_string}\n\n"
        "IMPORTANT for the farmer-facing answer:\n"
        "- These videos already passed relevance checks; still do not list titles/URLs "
        "(UI plays them inline).\n"
        "- Never write a Source line for videos.\n"
        "- Order: answer → **Source:** (document only) → "
        "'For more information, watch the videos below.' → follow-up question.\n"
        "- Cue line only when videos were found; never if tool said no relevant videos.\n"
    )

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
    top_k: int = DEFAULT_VIDEO_TOP_K,
) -> str:
    """
    Semantic search for guidance videos closely matching the farmer's topic.

    Pass a **specific English topic query** (crop + practice), e.g.
    "proso millet weed management" or "tomato leaf curl control" —
    not vague strings like "farming" or scheme portal names alone.

    Stores only *relevant* hits on FarmerContext.related_videos for AG-UI.
    Returns "no relevant videos" (and stores nothing) when matches are weak
    or off-topic so the UI does not show cotton/rice clips for SMAM, etc.

    Args:
        query: Specific search query in *English* (required)
        top_k: Max candidates to retrieve before relevance filtering (default: 5)

    Returns:
        Formatted list of relevant videos, or an explicit no-relevant-videos message
    """
    try:
        endpoint_url = os.getenv('MARQO_ENDPOINT_URL')
        if not endpoint_url:
            raise ValueError("Marqo endpoint URL is required")

        index_name = os.getenv('MARQO_INDEX_NAME', 'mh-new-index')
        if not index_name:
            raise ValueError("Marqo index name is required")

        min_score = float(os.getenv("MARQO_VIDEO_MIN_SCORE", str(DEFAULT_VIDEO_MIN_SCORE)))
        client = marqo.Client(url=endpoint_url)
        logger.info(
            "search_videos query=%r index=%s top_k=%s min_score=%s",
            query,
            index_name,
            top_k,
            min_score,
        )

        # Hybrid: lexical match helps scheme/crop names; tensor adds semantic recall.
        # Falls back to tensor-only if the index rejects hybrid parameters.
        search_params = {
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

        try:
            results = client.index(index_name).search(**search_params)["hits"]
        except Exception as hybrid_err:
            logger.warning(
                "search_videos hybrid failed (%s); falling back to tensor",
                hybrid_err,
            )
            results = client.index(index_name).search(
                q=query,
                limit=top_k,
                filter_string="type:video",
                search_method="tensor",
            )["hits"]

        if len(results) == 0:
            # Nothing in index — do not invent or substitute other clips.
            logger.info("search_videos marqo empty query=%r", query)
            return format_videos_for_agent(query, [])

        lang_code = ctx.deps.lang_code
        search_hits = [SearchHit(**hit, lang_code=lang_code) for hit in results]
        for h in search_hits:
            logger.info(
                "search_videos candidate name=%s score=%.4f",
                h.name,
                h.score,
            )

        # Strict gate: only on-topic + min score. Never use unfiltered top-K.
        relevant = filter_relevant_video_hits(search_hits, query, min_score=min_score)
        relevant = relevant[:AGUI_VIDEO_LIMIT]
        resources = collect_video_resources(relevant, limit=AGUI_VIDEO_LIMIT)

        if not resources:
            # Off-topic / low score / no playable URL — return nothing to agent & UI.
            logger.info(
                "search_videos returning empty (no relevant) query=%r raw_candidates=%s",
                query,
                len(search_hits),
            )
            return format_videos_for_agent(query, [])

        payload: list[dict[str, Any]] = [r.to_ag_ui_dict() for r in resources]
        ctx.deps.add_related_videos(payload)
        logger.info(
            "search_videos stored %s relevant AG-UI video(s) session=%s query=%r titles=%s",
            len(payload),
            getattr(ctx.deps, "session_id", ""),
            query,
            [r.title for r in resources],
        )
        return format_videos_for_agent(query, relevant)

    except Exception as e:
        logger.error(f"Error searching videos: {e} for query: {query}")
        raise ModelRetry(f"Error searching videos, please try again")
