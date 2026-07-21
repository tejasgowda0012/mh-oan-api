"""
Marqo client implementation for vector search.

Flow for crop/advisory (agent + prompts):
  search_terms (glossary) → English topic
  search_documents (hybrid, type:document)
  search_videos   (hybrid, type:video) then light re-rank / drop zero lexical match

No hand-maintained crop synonym tables — English query tokens come from the agent
(and search_terms / glossary), same idea as documents.
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

# Max videos attached to AG-UI for inline playback.
AGUI_VIDEO_LIMIT = 2

# Filler words stripped when scoring video hits against the query.
# Not a crop dictionary — only drops words that appear in every Digital Farm intro.
#
# TEMP (video-index quality): stopwords + rank_and_filter_video_hits re-rank can mostly
# go away after ingesting more (and better) videos — when the video index looks more
# like the document index (diverse titles/topics, not a handful of shared Digital Farm
# workshop templates). Until then, Marqo alone often ranks toor/cotton above rice for
# "digital rice farming…" because of shared boilerplate. Keep URL dedupe + empty/no-play
# handling even after that cleanup.
_QUERY_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "with", "by",
    "how", "what", "when", "where", "why", "is", "are", "about", "tell", "me",
    "any", "video", "videos", "guidance", "related", "please", "from", "into",
    "digital", "farm", "farming", "farmer", "school", "workshop", "session",
    "technique", "techniques", "tool", "tools", "method", "methods", "practice",
    "practices", "modern", "improved", "smart", "using", "use", "best", "good",
    "high", "low", "better", "management", "cultivation", "production",
    "information", "requirement", "requirements",
})


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
    """Playable AG-UI payloads from hits that have an http(s) media URL."""
    resources: list[VideoResource] = []
    for hit in hits:
        resource = hit.to_video_resource()
        if resource:
            resources.append(resource)
    return dedupe_videos(resources)[:limit]


def _query_content_tokens(query: str) -> list[str]:
    """Content tokens from the English query (order preserved, unique)."""
    tokens = re.findall(r"[a-z0-9]+", (query or "").lower())
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if len(t) < 3 or t in _QUERY_STOPWORDS or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _lexical_match_score(hit: SearchHit, tokens: list[str]) -> tuple[int, int, int]:
    """
    Score how well a hit matches query tokens.

    Returns (title_hits, text_hits, total) for sorting.
    Title matches are what fix "rice" ranking above "toor digital farm".
    """
    if not tokens:
        return (0, 0, 0)
    name = (hit.name or "").lower().replace("_", " ").replace("-", " ")
    # Score title/name only for ranking first; text used for keep/drop
    text = (hit.text or "").lower().replace("_", " ").replace("-", " ")
    title_hits = sum(1 for t in tokens if t in name)
    text_hits = sum(1 for t in tokens if t in text)
    return (title_hits, text_hits, title_hits + text_hits)


def rank_and_filter_video_hits(hits: list[SearchHit], query: str) -> list[SearchHit]:
    """
    Re-rank Marqo video hits so retrieval behaves sensibly on a small, similar corpus.

    1. Require at least one query content token in name or text (else drop).
       → no random cotton/toor when query is "tuberose".
    2. Sort by title token matches, then text matches, then Marqo score.
       → "digital rice farming…" ranks Rice* above Toor/Cotton.
    3. Dedupe by YouTube URL (keep best-ranked chunk per video).

    TEMP: this whole post-Marqo re-rank/filter (and _QUERY_STOPWORDS) can mostly be
    removed after ingesting more and better videos — when the video index looks more
    like the document index. Prefer pure Marqo ranking then (same spirit as
    search_documents). Still keep URL dedupe and "no playable hit → no videos".
    """
    tokens = _query_content_tokens(query)
    if not tokens:
        # No distinctive tokens — cannot verify relevance; return nothing.
        logger.info("search_videos empty: no content tokens in query=%r", query)
        return []

    scored: list[tuple[tuple[int, int, float], SearchHit]] = []
    for hit in hits:
        title_hits, text_hits, total = _lexical_match_score(hit, tokens)
        if total == 0:
            logger.info(
                "search_videos drop zero-lexical name=%s score=%.4f tokens=%s",
                hit.name,
                hit.score,
                tokens,
            )
            continue
        # Sort key: title matches dominate, then text, then Marqo score
        key = (title_hits, text_hits, hit.score)
        scored.append((key, hit))

    scored.sort(key=lambda x: x[0], reverse=True)

    # One chunk per video URL (ingest splits one YouTube into many rows)
    seen_urls: set[str] = set()
    seen_names: set[str] = set()
    ranked: list[SearchHit] = []
    for _key, hit in scored:
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
        ranked.append(hit)

    logger.info(
        "search_videos ranked query=%r tokens=%s order=%s",
        query,
        tokens,
        [(h.name, round(h.score, 4)) for h in ranked[:5]],
    )
    return ranked


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
    Semantic search for videos — same Marqo hybrid call as search_documents
    (type:video), then re-rank so title/query token match wins over template noise.

    Why re-rank: FSS video ingest is a few Digital Farm series with shared intros.
    Raw Marqo order often puts "Toor digital farm" above "Rice" for a rice query.
    We do not invent crop synonyms here; we use tokens already in the English query
    (from search_terms / agent), require at least one match, and sort by match quality.

    Empty after re-rank → no videos for agent or AG-UI.

    Args:
        query: English topic (same as documents when used together)
        top_k: Marqo candidate limit (default 10)

    Returns:
        Ranked on-topic videos, or no-videos message
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

        try:
            results = _marqo_hybrid_search(
                client=client,
                index_name=index_name,
                query=query,
                top_k=top_k,
                type_filter="video",
            )
        except Exception as hybrid_err:
            logger.warning("search_videos hybrid error (%s); trying tensor", hybrid_err)
            results = client.index(index_name).search(
                q=query,
                limit=top_k,
                filter_string="type:video",
                search_method="tensor",
            )["hits"]

        if len(results) == 0:
            return _no_videos_message(query)

        lang_code = ctx.deps.lang_code
        search_hits = [SearchHit(**hit, lang_code=lang_code) for hit in results]
        logger.info(
            "search_videos marqo_order=%s query=%r",
            [(h.name, round(h.score, 4)) for h in search_hits[:5]],
            query,
        )

        ranked = rank_and_filter_video_hits(search_hits, query)
        if not ranked:
            return _no_videos_message(query)

        resources = collect_video_resources(ranked, limit=AGUI_VIDEO_LIMIT)
        if not resources:
            # Lexical match but no playable URL — still no UI videos
            return _no_videos_message(query)

        payload: list[dict[str, Any]] = [r.to_ag_ui_dict() for r in resources]
        ctx.deps.add_related_videos(payload)
        logger.info(
            "search_videos AG-UI=%s session=%s",
            [r.title for r in resources],
            getattr(ctx.deps, "session_id", ""),
        )

        video_string = "\n\n----\n\n".join(str(document) for document in ranked)
        return "> Videos for `" + query + "`\n\n" + video_string

    except Exception as e:
        logger.error(f"Error searching videos: {e} for query: {query}")
        raise ModelRetry(f"Error searching videos, please try again")


def _no_videos_message(query: str) -> str:
    return (
        f"No videos found for `{query}`.\n"
        "Do not show or invent videos. If the farmer asks for videos on this topic, "
        "say no videos are available."
    )
