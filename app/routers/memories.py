"""Read-only mem0 memory lookup by phone (memory viewer)."""

from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.config import settings
from app.services.identity import phone_to_memory_user_id
from app.services.memory import memory_service

router = APIRouter(prefix="/memories", tags=["memories"])


def _require_internal_access(x_internal_token: str | None = Header(default=None)) -> None:
    if settings.environment == "development":
        return
    expected = os.getenv("INTERNAL_ADMIN_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Memory lookup disabled (set INTERNAL_ADMIN_TOKEN)",
        )
    if not x_internal_token or not secrets.compare_digest(x_internal_token, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Internal access required")

@router.get("", dependencies=[Depends(_require_internal_access)])
@router.get("/", dependencies=[Depends(_require_internal_access)])
async def list_memories(phone: str):
    user_id = phone_to_memory_user_id(phone)
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid phone number")
    items = await memory_service.get_all(user_id)
    items.sort(key=lambda m: m.get("created_at") or "", reverse=True)
    return {
        "user_id": user_id,
        "count": len(items),
        "memories": items,
        "qdrant_collection": os.getenv("QDRANT_COLLECTION", "vistaar_chat_farmer_memories"),
        "qdrant_host": os.getenv("QDRANT_HOST", "localhost"),
    }

@router.delete("", dependencies=[Depends(_require_internal_access)])
@router.delete("/", dependencies=[Depends(_require_internal_access)])
async def delete_memories(phone: str):
    user_id = phone_to_memory_user_id(phone)
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid phone number")
    deleted = await memory_service.delete_all(user_id)
    return {"user_id": user_id, "deleted": deleted}