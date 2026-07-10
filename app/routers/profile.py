"""Structured farmer profile lookup (Qdrant vistaar_farmer_profiles)."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException

from app.routers.memories import _require_internal_access
from app.services.identity import phone_to_memory_user_id
from app.services.profile import profile_store, render_snapshot

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("", dependencies=[Depends(_require_internal_access)])
@router.get("/", dependencies=[Depends(_require_internal_access)])
async def get_profile(phone: str):
    user_id = phone_to_memory_user_id(phone)
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid phone number")
    profile = await profile_store.get(user_id)
    return {
        "user_id": user_id,
        "profile": profile.model_dump() if profile else None,
        "snapshot": render_snapshot(profile) if profile else None,
        "qdrant_collection": os.getenv("QDRANT_PROFILE_COLLECTION", "vistaar_farmer_profiles"),
        "qdrant_host": os.getenv("QDRANT_HOST", "localhost"),
    }