"""AG-UI protocol endpoint — the agentic transport for the Vistaar agent.

``POST /api/agui`` takes an AG-UI ``RunAgentInput`` body and streams AG-UI
protocol events. Same agent, tools, moderation and ``FarmerContext`` as
``/api/chat/``; only the wire format differs.

Supersedes ``POST /api/ag-ui/chat`` (``app/routers/ag_ui.py``), which stays in
place as a fallback for clients that have not migrated yet.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from app.auth.jwt_auth import get_current_user
from app.core.limiter import limiter
from app.services.agui import handle_agui_request
from helpers.utils import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/agui", tags=["agui"])


@router.post("")
@router.post("/")
@limiter.limit("1000/hour")
async def agui_endpoint(
    request: Request,
    background_tasks: BackgroundTasks,
    user_info: dict = Depends(get_current_user),
) -> Response:
    """
    Run the Vistaar agent and stream AG-UI protocol events.

    Request body: AG-UI ``RunAgentInput``. `session_id`, `source_lang`,
    `target_lang` and `user_id` are read from ``state`` (falling back to
    ``threadId`` for the session).

    Response: ``text/event-stream`` of ``RUN_STARTED`` / ``TOOL_CALL_*`` /
    ``TEXT_MESSAGE_*`` / ``CUSTOM`` / ``RUN_FINISHED`` events.
    """
    return await handle_agui_request(
        request,
        user_info=user_info or {},
        background_tasks=background_tasks,
    )


@router.options("")
@router.options("/")
async def agui_options() -> JSONResponse:
    return JSONResponse({"status": "ok"})
