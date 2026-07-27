"""AG-UI chat endpoint for generative UI (inline video playback)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import StreamingResponse

from app.auth.jwt_auth import get_current_user
from app.core.limiter import limiter
from app.models.requests import ChatRequest
from app.services.ag_ui import stream_ag_ui_events
from app.services.chat import stream_chat_messages
from app.utils import _get_message_history
from helpers.utils import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/ag-ui", tags=["ag-ui"])


@router.post("/chat")
@limiter.limit("1000/hour")
async def ag_ui_chat_endpoint(
    request: Request,
    background_tasks: BackgroundTasks,
    chat_request: ChatRequest,
    user_info: dict = Depends(get_current_user),
):
    """
    Stream the Vistaar chat pipeline as AG-UI SSE events.

    When `search_videos` runs during the turn, structured video resources are
    emitted as TOOL_CALL_* / CUSTOM related_videos events for inline players.
    When `search_documents` runs, the retrieved documents are emitted as
    TOOL_CALL_* / CUSTOM related_documents events for grounding validation.
    """
    session_id = chat_request.session_id or str(uuid.uuid4())

    logger.info(
        "AG-UI chat request - session_id=%s user_id=%s query=%s",
        session_id,
        chat_request.user_id,
        chat_request.query,
    )

    history = await _get_message_history(session_id)

    # Clear stale suggestions cache (same as classic /chat).
    from app.core.cache import cache

    for lang in ["mr", "en", "hi", "bhb"]:
        try:
            await cache.delete(f"suggestions_{session_id}_{lang}")
        except Exception:
            pass

    # Shared lists filled by stream_chat_messages while the agent runs.
    related_videos: list = []
    related_documents: list = []

    async def text_chunks():
        async for chunk in stream_chat_messages(
            query=chat_request.query,
            session_id=session_id,
            source_lang=chat_request.source_lang,
            target_lang=chat_request.target_lang,
            user_id=chat_request.user_id,
            history=history,
            user_info=user_info,
            background_tasks=background_tasks,
            related_videos_out=related_videos,
            related_documents_out=related_documents,
        ):
            yield chunk

    return StreamingResponse(
        stream_ag_ui_events(
            chunk_source=text_chunks(),
            session_id=session_id,
            user_id=chat_request.user_id,
            query=chat_request.query,
            related_videos=related_videos,
            related_documents=related_documents,
        ),
        media_type="text/event-stream",
    )
