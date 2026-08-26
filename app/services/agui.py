"""AG-UI protocol orchestration — same pipeline as /chat, real protocol transport.

``POST /api/agui`` runs the identical steps as ``stream_chat_messages``
(moderation → FarmerContext → agrinet agent → history persist), but streams via
pydantic-ai's :class:`AGUIAdapter` instead of the hand-rolled emitter in
``app/services/ag_ui.py``. Tool calls therefore appear as real
``TOOL_CALL_START/ARGS/END/RESULT`` events at the moment the model makes them,
interleaved with ``TEXT_MESSAGE_CONTENT`` — not replayed after the text ends.

Two payloads reach the client without any custom transport code:

* ``present_video`` / ``present_suggestions`` return JSON, which the adapter
  surfaces verbatim as the tool's ``TOOL_CALL_RESULT`` content.
* ``related_documents`` (unfiltered, for grounding validation) is emitted as a
  ``CUSTOM`` event yielded from ``on_complete``.

Conversation history stays server-side in Redis: the client sends only the newest
user turn and the trimmed history is passed as ``message_history``.

Everything from moderation onwards runs *inside* the streaming generator so the
Langfuse chain span stays open across the whole run — the same reason
``stream_chat_messages`` is an async generator rather than a coroutine.
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

from ag_ui.core import (
    BaseEvent,
    CustomEvent,
    RunErrorEvent,
    RunStartedEvent,
    TextMessageContentEvent,
    ToolCallResultEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    UserMessage,
)
from fastapi import BackgroundTasks, Request
from langfuse import get_client, propagate_attributes
from pydantic_ai.ui.ag_ui import AGUIAdapter
from starlette.responses import Response

from agents.agrinet import agrinet_agent, build_agrinet_system_prompt
from agents.deps import FarmerContext
from agents.suggestions import suggestions_agent
from app.services.suggestion_history import get_past_suggestions, add_to_blacklist

from app.services.chat import (
    CHAT_CHAIN_SPAN_NAME,
    CHAT_TRACE_NAME,
    MODEL_NAME,
    _run_moderation,
    _translate_paragraph,
)
from app.services.identity import resolve_memory_user_id
from app.services.memory_context import preload_farmer_profile
# from app.tasks.suggestions import create_suggestions
from app.utils import (
    _get_message_history,
    filter_thinking_from_history,
    format_message_pairs,
    trim_history,
    update_message_history,
)
from helpers import langfuse_helper  # noqa: F401 — initialises Langfuse env vars
from helpers.langfuse_trace_schema import chat_trace_metadata_strings
from helpers.langfuse_tracing import lf_set_trace_io
from helpers.translation import translation_service
from helpers.utils import get_logger

logger = get_logger(__name__)


def _state_value(state: dict[str, Any] | None, *keys: str, default: Any = None) -> Any:
    """First non-empty value among `keys` in the AG-UI client state."""
    if not state:
        return default
    for key in keys:
        if key in state and state[key] not in (None, ""):
            return state[key]
    return default


def _latest_user_text(run_input) -> str:
    """Newest user turn as the client sent it, before any rewriting."""
    for message in reversed(run_input.messages or []):
        if getattr(message, "role", None) != "user":
            continue
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            texts = [getattr(part, "text", "") for part in content]
            return "\n".join(t for t in texts if t).strip()
    return ""


def _rewrite_latest_user_content(run_input, formatted: str) -> None:
    """Swap the client's raw question for the /chat prompt shape.

    The agent expects the query, selected language, moderation verdict and saved
    profile in one user turn (``FarmerContext.get_user_message``). The client only
    sends the raw question, so it is rewritten here. This never reaches the user's
    transcript — the client keeps its own message list and ignores the run's.
    """
    for message in reversed(run_input.messages or []):
        if getattr(message, "role", None) == "user" or isinstance(message, UserMessage):
            message.content = formatted
            return
    run_input.messages = [
        *(run_input.messages or []),
        UserMessage(id=str(uuid.uuid4()), content=formatted),
    ]


# Text that is internal tool output, not an answer. After the present_* tools
# return, the agent loop asks the model for one more turn; a small model
# sometimes fills it by echoing a tool string or inventing an exception
# ("FileNotFound: No videos found for `…`") instead of staying silent. The
# prompts tell it to call the present tools *before* writing, which removes the
# extra turn — this is the backstop for when it does it anyway.
#
# Patterns are deliberately tight and anchored to our own tool strings, so a
# legitimate answer that merely talks about videos is never dropped. Note the
# backticks: the tool says "No videos found for `query`" while the farmer-facing
# line is "No videos are available for this topic."
_INTERNAL_ECHO_PATTERNS = (
    re.compile(r"^\s*(FileNotFound(Error)?|ValueError|KeyError|TypeError|AttributeError"
               r"|IndexError|Exception|Traceback|ModelRetry)\b"),
    re.compile(r"^\s*No videos found for\s*`"),
    re.compile(r"^\s*No matching terms found for\s*`"),
    re.compile(r"^\s*>\s*(Search Results|Videos) for\s*`"),
    re.compile(r"^\s*Error (searching|fetching|retrieving)\b"),
    re.compile(r"^\s*No farmer memory available\b"),
)

# How much of a text message to hold before deciding. Long enough to classify,
# short enough that the delay is invisible.
_ECHO_SNIFF_CHARS = 60


def _is_internal_echo(text: str) -> bool:
    return any(pattern.search(text) for pattern in _INTERNAL_ECHO_PATTERNS)


async def _drop_internal_echo_messages(
    stream: AsyncIterator[BaseEvent],
) -> AsyncIterator[BaseEvent]:
    """Drop assistant text messages that are really internal tool output.

    Each text message is held for its first ``_ECHO_SNIFF_CHARS`` characters and
    classified once: junk is dropped whole (START/CONTENT/END), anything else is
    released and then streamed live with no further buffering.
    """
    held_start: BaseEvent | None = None
    buffer = ""
    decided = False
    dropping = False

    def reset() -> None:
        nonlocal held_start, buffer, decided, dropping
        held_start, buffer, decided, dropping = None, "", False, False

    async for event in stream:
        if isinstance(event, TextMessageStartEvent):
            held_start = event
            buffer = ""
            decided = False
            dropping = False
            continue

        if isinstance(event, TextMessageContentEvent) and not decided:
            buffer += event.delta
            if len(buffer) < _ECHO_SNIFF_CHARS:
                continue
            decided = True
            dropping = _is_internal_echo(buffer)
            if dropping:
                logger.warning("Dropped internal tool echo from answer: %r", buffer[:120])
                continue
            if held_start is not None:
                yield held_start
                held_start = None
            yield TextMessageContentEvent(message_id=event.message_id, delta=buffer)
            buffer = ""
            continue

        if isinstance(event, TextMessageContentEvent):
            if not dropping:
                yield event
            continue

        if isinstance(event, TextMessageEndEvent):
            # Short message: decide on whatever was buffered.
            if not decided:
                decided = True
                dropping = _is_internal_echo(buffer)
                if dropping:
                    logger.warning("Dropped internal tool echo from answer: %r", buffer[:120])
                elif buffer:
                    if held_start is not None:
                        yield held_start
                        held_start = None
                    yield TextMessageContentEvent(message_id=event.message_id, delta=buffer)
            if not dropping and held_start is None:
                yield event
            reset()
            continue

        yield event

    # Stream ended mid-message (client disconnect): flush anything still held.
    if not decided and buffer and not _is_internal_echo(buffer):
        if held_start is not None:
            yield held_start
        yield TextMessageContentEvent(message_id="", delta=buffer)


async def _translate_stream_to_bhili(
    stream: AsyncIterator[BaseEvent],
) -> AsyncIterator[BaseEvent]:
    """Re-translate assistant text deltas back to Bhili, paragraph by paragraph.

    Mirrors the Bhili branch of ``_run_agrinet_stream``: the agent answers in
    English, and each complete paragraph is translated before it is emitted so
    Bhashini receives whole sentences. Non-text events pass straight through,
    with any buffered tail flushed first so ordering is preserved.
    """
    buffer = ""
    message_id: str | None = None

    async def flush() -> AsyncIterator[BaseEvent]:
        nonlocal buffer
        if buffer.strip() and message_id:
            translated = await _translate_paragraph(buffer, "en", "bhb")
            yield TextMessageContentEvent(message_id=message_id, delta=translated)
        buffer = ""

    async for event in stream:
        if isinstance(event, TextMessageContentEvent):
            message_id = event.message_id
            buffer += event.delta
            while "\n\n" in buffer:
                paragraph, buffer = buffer.split("\n\n", 1)
                translated = await _translate_paragraph(paragraph, "en", "bhb")
                yield TextMessageContentEvent(message_id=message_id, delta=translated + "\n\n")
            continue

        async for tail in flush():
            yield tail
        yield event

    async for tail in flush():
        yield tail


async def handle_agui_request(
    request: Request,
    *,
    user_info: dict,
    background_tasks: BackgroundTasks,
) -> Response:
    """Run one AG-UI turn and return the streaming protocol response."""
    body = await request.body()
    run_input = AGUIAdapter.build_run_input(body)

    state = run_input.state if isinstance(run_input.state, dict) else {}
    thread_id = run_input.thread_id or str(uuid.uuid4())
    run_input.thread_id = thread_id

    session_id = str(_state_value(state, "session_id", "thread_id", default=thread_id) or thread_id)
    source_lang = str(_state_value(state, "source_lang", "lang_code", default="mr") or "mr")
    target_lang = str(_state_value(state, "target_lang", "lang_code", default=source_lang) or source_lang)

    user_claims = user_info if isinstance(user_info, dict) else {}
    user_id = str(
        _state_value(state, "user_id", default=None)
        or user_claims.get("sub")
        or user_claims.get("user_id")
        or "anonymous"
    )
    query = (_latest_user_text(run_input) or str(_state_value(state, "query", default="") or "")).strip()

    # State is transport-level config (session, languages, user), already read
    # above into FarmerContext. Clearing it stops AGUIAdapter trying to hand it
    # to `deps` — FarmerContext is a pydantic model, not a StateHandler
    # dataclass, so leaving it set only produces a "state was ignored" warning.
    run_input.state = None

    memory_user_id = resolve_memory_user_id(user_id, user_claims)
    logger.info(
        "AG-UI request session=%s user=%s memory_user_id=%s source_lang=%s target_lang=%s query=%r",
        session_id,
        user_id,
        memory_user_id,
        source_lang,
        target_lang,
        query[:200],
    )

    adapter = AGUIAdapter(
        agent=agrinet_agent,
        run_input=run_input,
        accept=request.headers.get("accept"),
    )

    async def traced_stream() -> AsyncIterator[BaseEvent]:
        """Preflight + agent run, wrapped in the same Langfuse trace as /chat."""
        lf_env = os.getenv("LANGFUSE_TRACING_ENVIRONMENT", "development")
        trace_tags = [
            f"env:{lf_env}",
            "transport:ag-ui",
            *([f"model:{MODEL_NAME}"] if MODEL_NAME else []),
        ]

        with propagate_attributes(
            user_id=user_id,
            session_id=session_id,
            metadata=chat_trace_metadata_strings(
                source_lang=source_lang,
                target_lang=target_lang,
                environment=lf_env,
                query=query,
            ),
            tags=trace_tags,
            trace_name=CHAT_TRACE_NAME,
        ):
            lf_client = get_client()
            with lf_client.start_as_current_observation(
                as_type="chain",
                name=CHAT_CHAIN_SPAN_NAME,
            ) as chain_span:
                lf_set_trace_io(input=query)

                try:
                    # Bhili: run the agent in English, translate the answer back out.
                    is_bhili = source_lang == "bhb"
                    effective_query = query
                    effective_target_lang = target_lang
                    if is_bhili and query:
                        effective_query = await translation_service.translate_text(query, source_lang, "en")
                        effective_target_lang = "en"
                        logger.info("Bhili query translated to English: %s", effective_query)

                    history = await _get_message_history(session_id)

                    deps = FarmerContext(
                        query=effective_query,
                        lang_code=effective_target_lang,
                        display_lang=target_lang,
                        session_id=session_id,
                        farmer_id=user_claims.get("farmer_id"),
                        unique_id=user_claims.get("unique_id"),
                        user_info=user_claims,
                        memory_user_id=memory_user_id,
                    )

                    message_pairs = "\n\n".join(format_message_pairs(history, 3))
                    last_response = (
                        f"**Conversation**\n\n{message_pairs}\n\n---\n\n" if message_pairs else ""
                    )
                    moderation_data = await _run_moderation(
                        user_message=f"{last_response}{deps.get_moderation_message()}",
                        session_id=session_id,
                    )
                    logger.info("AG-UI moderation: %s", moderation_data)
                    deps.update_moderation_str(str(moderation_data))

                    if memory_user_id and not history:
                        deps.saved_farmer_context = await preload_farmer_profile(memory_user_id)

                    trimmed_history = filter_thinking_from_history(
                        trim_history(
                            history,
                            max_tokens=80_000,
                            include_system_prompts=True,
                            include_tool_calls=True,
                        )
                    )

                    _rewrite_latest_user_content(run_input, deps.get_user_message())
                except Exception as exc:
                    # Preflight failed after headers were sent — report in-band.
                    logger.error("AG-UI preflight failed for session %s", session_id, exc_info=True)
                    yield RunStartedEvent(thread_id=thread_id, run_id=run_input.run_id)
                    yield RunErrorEvent(message=str(exc), code="preflight_error")
                    return

                full_output = ""

                async def on_complete(result) -> AsyncIterator[BaseEvent]:
                    # Persist against the *untrimmed* history so trimming never
                    # truncates what is stored (same contract as /chat).
                    try:
                        new_messages = filter_thinking_from_history(list(result.new_messages()))
                        await update_message_history(session_id, [*history, *new_messages])
                    except Exception:
                        logger.error(
                            "AG-UI history update failed for session %s", session_id, exc_info=True
                        )

                    if deps.related_documents:
                        yield CustomEvent(
                            name="related_documents",
                            value={"documents": deps.related_documents},
                        )

                    try:
                        # Extract assistant text
                        assistant_text = full_output
                        
                        # 1. Get complete session blacklist from Redis
                        blacklist = await get_past_suggestions(session_id)
                        
                        # 2. Extract the assistant's text follow-up question (last question mark sentence)
                        questions_in_text = re.findall(r'[^.!?\n]+\?', assistant_text)
                        assistant_followup = questions_in_text[-1].strip() if questions_in_text else None
                        
                        if assistant_followup:
                            blacklist.add(assistant_followup)
                            
                        # 3. Add current user query to blacklist dynamically for this turn
                        blacklist.add(effective_query)
                        
                        # 4. Build strict context
                        context_str = f"**Current User Query:** {effective_query}\n\n**Assistant Response:** {assistant_text}"
                        
                        if assistant_followup:
                            context_str += f"\n\n**THE ASSISTANT JUST ASKED THE USER:** \"{assistant_followup}\"\n"
                            context_str += "CRITICAL: You MUST NOT suggest a question that means the same thing as the assistant's question. However, you MUST STAY ON THE SAME TOPIC (e.g. if the topic is weather, suggest a different weather question)."

                        past_items = blacklist - {assistant_followup} if assistant_followup else blacklist
                        if past_items:
                            context_str += "\n\n**ALSO DO NOT REPEAT THESE PAST TOPICS:**\n"
                            for item in past_items:
                                context_str += f"- {item}\n"
                        
                        logger.info("Running suggestions_agent with context: %s", context_str)
                        
                        def is_duplicate(c: str, b_list: set) -> bool:
                            import re
                            c_norm = re.sub(r'[^\w\s]', '', c.lower()).strip()
                            if not c_norm: return True
                            for item in b_list:
                                i_norm = re.sub(r'[^\w\s]', '', item.lower()).strip()
                                if c_norm in i_norm or i_norm in c_norm:
                                    return True
                            return False

                        max_retries = 3
                        chip = None
                        
                        for attempt in range(max_retries):
                            sugg_res = await suggestions_agent.run(
                                context_str,
                                deps=deps
                            )
                            temp_chip = sugg_res.output.question
                            
                            if not is_duplicate(temp_chip, blacklist):
                                chip = temp_chip
                                break
                            else:
                                logger.warning("suggestions_agent generated duplicate chip on attempt %d: %s", attempt+1, temp_chip)
                                context_str += f"\n\n[SYSTEM: Your last generation '{temp_chip}' was a DUPLICATE of the blacklist. Please suggest a DIFFERENT question, but STAY ON THE SAME TOPIC.]"

                        if chip:
                            logger.info("suggestions_agent successfully generated unique chip: %s", chip)
                            
                            if is_bhili:
                                chip = await translation_service.translate_text(chip, "en", "bhb")
                                logger.info("suggestions_agent translated chip to bhili: %s", chip)
                            
                            # 5. Persist to Redis for future turns
                            await add_to_blacklist(session_id, effective_query)
                            await add_to_blacklist(session_id, chip)
                            if assistant_followup:
                                await add_to_blacklist(session_id, assistant_followup)
                                
                            yield CustomEvent(
                                name="suggestions",
                                value={"questions": [chip]}
                            )
                    except Exception as e:
                        logger.error("AG-UI suggestions_agent failed: %s", str(e), exc_info=True)

                # The system prompt MUST be passed as run instructions here.
                # `@agrinet_agent.system_prompt` only fires when pydantic-ai
                # builds a request from a `user_prompt`; the adapter instead
                # appends the user turn to `message_history`, so the hook never
                # runs and the model would answer with no persona and no tool
                # rules at all.
                stream: AsyncIterator[BaseEvent] = adapter.run_stream(
                    deps=deps,
                    message_history=trimmed_history,
                    instructions=build_agrinet_system_prompt(effective_target_lang),
                    on_complete=on_complete,
                )
                # Filter before translating: never pay Bhashini for junk text.
                stream = _drop_internal_echo_messages(stream)
                if is_bhili:
                    stream = _translate_stream_to_bhili(stream)

                with propagate_attributes(tags=[moderation_data.category]):
                    try:
                        async for event in stream:
                            if isinstance(event, TextMessageContentEvent):
                                full_output += event.delta
                            yield event
                    finally:
                        chain_span.update(output=full_output)
                        lf_set_trace_io(output=full_output)
                        lf_client.flush()

    response = adapter.streaming_response(traced_stream())

    # SSE must not be buffered by nginx / other reverse proxies.
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response
