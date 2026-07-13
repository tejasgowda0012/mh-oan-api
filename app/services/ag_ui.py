import json
from typing import Any, AsyncGenerator


def format_sse_event(event_name: str, payload: dict[str, Any]) -> str:
    """Format an AG-UI-compatible SSE event as a plain text payload."""
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {event_name}\ndata: {data}\n\n"


async def stream_ag_ui_events(
    chunk_source: AsyncGenerator[str, None],
    session_id: str,
    user_id: str,
    query: str,
) -> AsyncGenerator[str, None]:
    """Adapt a text chunk stream into a simple AG-UI style event stream."""
    accumulated = ""

    yield format_sse_event(
        "session_started",
        {
            "session_id": session_id,
            "user_id": user_id,
            "query": query,
        },
    )

    async for chunk in chunk_source:
        accumulated += chunk
        yield format_sse_event(
            "message_delta",
            {
                "session_id": session_id,
                "delta": chunk,
                "partial_text": accumulated,
            },
        )

    yield format_sse_event(
        "message_completed",
        {
            "session_id": session_id,
            "user_id": user_id,
            "text": accumulated,
        },
    )
