"""
AG-UI event streaming adapter.

Emits SSE events compatible with the AG-UI protocol shape so clients can:
  - stream assistant text
  - render structured related videos inline (same search_videos tool, no second tool)

Event types used:
  RUN_STARTED, TEXT_MESSAGE_START, TEXT_MESSAGE_CONTENT, TEXT_MESSAGE_END,
  TOOL_CALL_START, TOOL_CALL_ARGS, TOOL_CALL_END, TOOL_CALL_RESULT,
  CUSTOM (name=related_videos), RUN_FINISHED, RUN_ERROR
"""

from __future__ import annotations

import json
import uuid
from typing import Any, AsyncGenerator, Optional


def format_sse(payload: dict[str, Any]) -> str:
    """Serialize one AG-UI event as an SSE data frame (type field in JSON body)."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _tool_call_id() -> str:
    return f"call_{uuid.uuid4().hex[:16]}"


async def stream_ag_ui_events(
    chunk_source: AsyncGenerator[str, None],
    *,
    session_id: str,
    user_id: str,
    query: str,
    related_videos: Optional[list[dict[str, Any]]] = None,
    run_id: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """
    Adapt a text chunk stream into AG-UI SSE events.

    `related_videos` may be filled asynchronously while `chunk_source` runs
    (e.g. a shared list mutated by the chat service). It is read after the
    text stream completes.
    """
    run_id = run_id or str(uuid.uuid4())
    message_id = f"msg_{uuid.uuid4().hex[:16]}"
    videos_holder = related_videos if related_videos is not None else []
    accumulated = ""

    yield format_sse(
        {
            "type": "RUN_STARTED",
            "threadId": session_id,
            "runId": run_id,
            "input": {
                "query": query,
                "user_id": user_id,
            },
        }
    )
    yield format_sse(
        {
            "type": "TEXT_MESSAGE_START",
            "messageId": message_id,
            "role": "assistant",
        }
    )

    try:
        async for chunk in chunk_source:
            if not chunk:
                continue
            accumulated += chunk
            yield format_sse(
                {
                    "type": "TEXT_MESSAGE_CONTENT",
                    "messageId": message_id,
                    "delta": chunk,
                }
            )
    except Exception as exc:
        yield format_sse(
            {
                "type": "RUN_ERROR",
                "message": str(exc),
                "code": "stream_error",
            }
        )
        return

    yield format_sse(
        {
            "type": "TEXT_MESSAGE_END",
            "messageId": message_id,
        }
    )

    videos = list(videos_holder or [])
    if videos:
        async for event in _emit_related_videos(
            videos=videos,
            message_id=message_id,
        ):
            yield event

    yield format_sse(
        {
            "type": "RUN_FINISHED",
            "threadId": session_id,
            "runId": run_id,
            "result": {
                "text": accumulated,
                "video_count": len(videos),
            },
        }
    )


async def _emit_related_videos(
    *,
    videos: list[dict[str, Any]],
    message_id: str,
) -> AsyncGenerator[str, None]:
    """Emit tool-call lifecycle + CUSTOM related_videos for frontend players."""
    tool_call_id = _tool_call_id()
    tool_name = "search_videos"
    args_json = json.dumps({"videos": videos}, ensure_ascii=False)

    yield format_sse(
        {
            "type": "TOOL_CALL_START",
            "toolCallId": tool_call_id,
            "toolCallName": tool_name,
            "parentMessageId": message_id,
        }
    )
    yield format_sse(
        {
            "type": "TOOL_CALL_ARGS",
            "toolCallId": tool_call_id,
            "delta": args_json,
        }
    )
    yield format_sse(
        {
            "type": "TOOL_CALL_END",
            "toolCallId": tool_call_id,
        }
    )
    yield format_sse(
        {
            "type": "TOOL_CALL_RESULT",
            "messageId": message_id,
            "toolCallId": tool_call_id,
            "content": args_json,
            "role": "tool",
        }
    )
    # Convenience event for clients that bind a player to a custom name.
    yield format_sse(
        {
            "type": "CUSTOM",
            "name": "related_videos",
            "value": {"videos": videos},
        }
    )
