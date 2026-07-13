from fastapi import APIRouter, Depends, BackgroundTasks, Request
from fastapi.responses import StreamingResponse

from app.auth.jwt_auth import get_current_user
from app.models.requests import ChatRequest
from app.services.chat import stream_chat_messages
from app.services.ag_ui import stream_ag_ui_events
from app.utils import _get_message_history
from app.core.limiter import limiter
from helpers.utils import get_logger
import uuid

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
    """Expose the existing chat pipeline through an AG-UI-style SSE endpoint."""
    session_id = chat_request.session_id or str(uuid.uuid4())

    history = await _get_message_history(session_id)

    async def event_stream():
        async for chunk in stream_chat_messages(
            query=chat_request.query,
            session_id=session_id,
            source_lang=chat_request.source_lang,
            target_lang=chat_request.target_lang,
            user_id=chat_request.user_id,
            history=history,
            user_info=user_info,
            background_tasks=background_tasks,
        ):
            yield chunk

    return StreamingResponse(
        stream_ag_ui_events(
            chunk_source=event_stream(),
            session_id=session_id,
            user_id=chat_request.user_id,
            query=chat_request.query,
        ),
        media_type="text/event-stream",
    )
