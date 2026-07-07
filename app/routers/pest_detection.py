import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from agents.deps import FarmerContext
from agents.tools.pest_detection import _authenticate_pest_service
from app.auth.jwt_auth import get_current_user
from app.config import settings
from app.core.limiter import limiter
from helpers.utils import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/pest-detection", tags=["pest-detection"])

DEFAULT_PEST_DETECTION_CROPS_URL = (
    "https://stage-farmers-app-api.mahapocra.gov.in/"
    "pestdetectionServices/get-crops-for-pest-detection"
)


class _PestToolContext:
    def __init__(self, deps: FarmerContext):
        self.deps = deps


@router.get("/crops")
@limiter.limit("1000/hour")
async def get_crops_for_pest_detection(
    request: Request,
    user_info: dict = Depends(get_current_user),
):
    """Relay crop-list lookup for pest detection UI."""
    url = os.getenv("PEST_DETECTION_CROPS_URL", DEFAULT_PEST_DETECTION_CROPS_URL)
    farmer_context = FarmerContext(
        query="get crops for pest detection",
        farmer_id=user_info.get("farmer_id") if isinstance(user_info, dict) else None,
        unique_id=user_info.get("unique_id") if isinstance(user_info, dict) else None,
        user_info=user_info if isinstance(user_info, dict) else {},
    )

    try:
        auth_headers = await _authenticate_pest_service(_PestToolContext(farmer_context))
    except Exception as exc:
        logger.exception("Pest detection crops relay login failed")
        raise HTTPException(
            status_code=502,
            detail="Pest detection crops service authentication failed.",
        ) from exc

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://vistaar-dev.mahapocra.gov.in",
        "Referer": "https://vistaar-dev.mahapocra.gov.in/",
        "User-Agent": "MahaVistaar AI API",
        **auth_headers,
    }

    try:
        async with httpx.AsyncClient(
            timeout=settings.pest_detection_http_timeout
        ) as client:
            upstream_response = await client.get(url, headers=headers)
            upstream_response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.exception("Pest detection crops relay returned upstream HTTP error")
        raise HTTPException(
            status_code=exc.response.status_code,
            detail="Pest detection crops service returned an error.",
        ) from exc
    except httpx.HTTPError as exc:
        logger.exception("Pest detection crops relay failed")
        raise HTTPException(
            status_code=502,
            detail="Pest detection crops service is temporarily unavailable.",
        ) from exc

    content_type = upstream_response.headers.get("content-type", "")
    if "application/json" in content_type.lower():
        payload: Any = upstream_response.json()
        return JSONResponse(content=payload, status_code=upstream_response.status_code)

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        media_type=content_type or "text/plain",
    )
