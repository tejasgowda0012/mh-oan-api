import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import JSONResponse, Response

from agents.deps import FarmerContext
from agents.tools.pest_detection import (
    _authenticate_pest_service,
    _extract_first_value,
)
from app.auth.jwt_auth import get_current_user
from app.config import settings
from helpers.utils import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/pest-detection", tags=["pest-detection"])

DEFAULT_PEST_DETECTION_CROPS_URL = (
    "https://stage-farmers-app-api.mahapocra.gov.in/"
    "pestdetectionServices/get-crops-for-pest-detection"
)
DEFAULT_PEST_DETECTION_FEEDBACK_URL = (
    "https://farmers-app-api.mahapocra.gov.in/"
    "pestdetectionServices/store-feedback"
)


class _PestToolContext:
    def __init__(self, deps: FarmerContext):
        self.deps = deps


def _farmer_context(user_info: dict, query: str) -> FarmerContext:
    return FarmerContext(
        query=query,
        farmer_id=user_info.get("farmer_id"),
        unique_id=user_info.get("unique_id"),
        user_info=user_info,
    )


@router.get("/crops")
async def get_crops_for_pest_detection(
    user_info: dict = Depends(get_current_user),
):
    """Relay crop-list lookup for pest detection UI."""
    url = os.getenv("PEST_DETECTION_CROPS_URL", DEFAULT_PEST_DETECTION_CROPS_URL)
    farmer_context = _farmer_context(user_info, "get crops for pest detection")

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
        try:
            payload: Any = upstream_response.json()
        except ValueError as exc:
            logger.exception("Pest detection crops relay returned invalid JSON")
            raise HTTPException(
                status_code=502,
                detail="Pest detection crops service returned an invalid response.",
            ) from exc
        return JSONResponse(content=payload, status_code=upstream_response.status_code)

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        media_type=content_type or "text/plain",
    )


@router.post("/feedback")
async def store_pest_detection_feedback(
    upload_id: str = Form(...),
    feedback: str = Form(...),
    user_info: dict = Depends(get_current_user),
):
    """Relay feedback using the upstream response ID saved during analysis."""
    from app.routers.upload import get_pest_upload

    upload_id = upload_id.strip()
    feedback = feedback.strip()
    if not upload_id or not feedback:
        raise HTTPException(status_code=400, detail="upload_id and feedback are required.")
    if len(feedback) > 2000:
        raise HTTPException(status_code=400, detail="feedback must be at most 2000 characters.")

    upload_record = await get_pest_upload(upload_id)
    if not upload_record:
        raise HTTPException(status_code=404, detail="Pest analysis upload not found or expired.")

    analysis = upload_record.get("analysis")
    stored_response = analysis.get("stored_response") if isinstance(analysis, dict) else None
    upstream_id = _extract_first_value(
        stored_response,
        "id",
        "response_id",
        "responseId",
        "feedback_id",
        "feedbackId",
    )
    if not upstream_id:
        raise HTTPException(
            status_code=409,
            detail="Pest analysis has not been stored upstream, so feedback cannot be submitted.",
        )

    context = _PestToolContext(_farmer_context(user_info, "store pest detection feedback"))
    try:
        auth_headers = await _authenticate_pest_service(context)
        async with httpx.AsyncClient(timeout=settings.pest_detection_http_timeout) as client:
            upstream_response = await client.post(
                os.getenv("PEST_DETECTION_FEEDBACK_URL", DEFAULT_PEST_DETECTION_FEEDBACK_URL),
                headers=auth_headers,
                data={"id": upstream_id, "feedback": feedback},
            )
            upstream_response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.exception("Pest detection feedback relay failed")
        raise HTTPException(
            status_code=502,
            detail="Pest detection feedback service is temporarily unavailable.",
        ) from exc
    except Exception as exc:
        logger.exception("Pest detection feedback authentication failed")
        raise HTTPException(
            status_code=502,
            detail="Pest detection feedback service authentication failed.",
        ) from exc

    if not upstream_response.content:
        return {"status": "success"}
    try:
        return JSONResponse(
            content=upstream_response.json(),
            status_code=upstream_response.status_code,
        )
    except ValueError:
        return Response(
            content=upstream_response.content,
            status_code=upstream_response.status_code,
            media_type=upstream_response.headers.get("content-type", "text/plain"),
        )
