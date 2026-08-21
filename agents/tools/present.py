"""Presentation-only tools — the agent's explicit "show this in the UI" calls.

These have no external side effect. Each writes to a ``FarmerContext`` side
channel and returns the same payload as JSON, so the AG-UI transport surfaces it
to the client as the tool's ``TOOL_CALL_RESULT`` content with no extra plumbing.

Splitting *find* from *show* is the point: ``search_videos`` returns candidates
and the model decides, in the same reasoning pass that produced the answer,
whether any of them is actually worth attaching.
"""

from __future__ import annotations

import json
from typing import Any

from langfuse import observe
from pydantic_ai import ModelRetry, RunContext

from agents.deps import FarmerContext
from helpers.utils import get_logger
from helpers.translation import translation_service

logger = get_logger(__name__)

# Guard rails for present_suggestions — the chip UI shows a single question,
# so cap to one and keep it short.
MAX_SUGGESTIONS = 1
MAX_SUGGESTION_CHARS = 150


@observe(name="tool:present_video", as_type="tool")
async def present_video(ctx: RunContext[FarmerContext], video_id: str) -> str:
    """Attach one video from a previous `search_videos` result to this reply.

    Call this when a candidate is genuinely about the same crop and the same
    general problem area (pest, disease, fertilizer, irrigation, etc.) as the
    farmer's question — it does not need to name the exact same pest/disease
    the farmer mentioned. A video covering one specific instance of that
    general problem (e.g. one particular pest) still counts as a match for a
    broader question about that same problem; call this tool for it. Only
    skip this tool when every candidate is a different crop, a genuinely
    unrelated topic, or a generic intro/promotional video with no real
    answer in it.

    Never invent an id. Use only the `v1` / `v2` / … ids listed in the most
    recent `search_videos` result of this conversation.

    Args:
        video_id: Short id of the video to show, e.g. "v1", from search_videos.
    """
    payload = ctx.deps.find_video_candidate(video_id)
    if payload is None:
        known = ", ".join(ctx.deps.video_candidates.keys()) or "none"
        raise ModelRetry(
            f"No video with id {video_id!r}. Available ids: {known}. "
            "Run search_videos first, then pass one of the listed ids — "
            "or skip present_video if none of them fit."
        )

    ctx.deps.add_related_videos([payload])
    logger.info(
        "present_video id=%s title=%r session=%s",
        video_id,
        payload.get("title"),
        getattr(ctx.deps, "session_id", ""),
    )
    return json.dumps({"videos": [payload]}, ensure_ascii=False)


@observe(name="tool:present_suggestions", as_type="tool")
async def present_suggestions(ctx: RunContext[FarmerContext], questions: list[str]) -> str:
    """Offer exactly one short follow-up question as a tappable chip under your answer.

    Call this in your VERY FIRST step/turn for every message, before or alongside any other tools (like search_terms or fetch_agristack_data). Do not wait for search results to generate a suggestion.
    CRITICAL: You MUST call this tool on EVERY turn, even for greetings, declined/out-of-scope queries, or if other tools fail.
    The chip topic must differ from the follow-up question at the end of your text — never duplicate.
    Try to suggest a fresh topic based on the user's initial query. For greetings or out-of-scope queries, suggest a general agricultural question (e.g., "What is the weather forecast?").

    Write it the way a farmer would type it: short (4-7 words), concrete,
    about a farm action, and in the same language as your answer. No "you"/"your",
    no "in your area".

    Args:
        questions: The follow-up question to show, as a single-item list. Only
            the first item is used; extras are discarded.
    """
    if ctx.deps.suggested_questions:
        return "Suggestion already added. Ignoring."

    cleaned = [q.strip() for q in (questions or []) if q and q.strip()]
    cleaned = [q for q in cleaned if len(q) <= MAX_SUGGESTION_CHARS][:MAX_SUGGESTIONS]

    if getattr(ctx.deps, "display_lang", "") == "bhb" and cleaned:
        translated_questions = []
        for q in cleaned:
            # translate from LLM's working language back to Bhili
            translated = await translation_service.translate_text(q, ctx.deps.lang_code, "bhb")
            translated_questions.append(translated)
        cleaned = translated_questions

    if not cleaned:
        return "No suggestions shown (empty or over-long questions were discarded)."

    added = ctx.deps.add_suggested_questions(cleaned)
    logger.info(
        "present_suggestions count=%s session=%s",
        len(added),
        getattr(ctx.deps, "session_id", ""),
    )
    payload: dict[str, Any] = {"questions": ctx.deps.suggested_questions}
    return json.dumps(payload, ensure_ascii=False)
