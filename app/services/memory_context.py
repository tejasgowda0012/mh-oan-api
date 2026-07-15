"""Pre-load the structured farmer profile at the start of a conversation."""

from __future__ import annotations

from typing import Optional

from app.services.profile import profile_store, render_snapshot


async def preload_farmer_profile(memory_user_id: Optional[str]) -> Optional[str]:
    """Return profile context only; episodic memories are recalled by tools on demand."""
    if not memory_user_id:
        return None

    profile = await profile_store.get(memory_user_id)
    if not profile or profile.is_empty():
        return None
    return render_snapshot(profile)
