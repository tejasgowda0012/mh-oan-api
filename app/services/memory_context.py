"""Pre-load farmer context: structured profile first, then mem0 episodic facts."""

from __future__ import annotations

from typing import Optional

from app.services.memory import memory_service
from app.services.profile import profile_store, render_snapshot


def _format_episodic_block(items: list[dict], *, max_items: int = 6) -> str:
    lines: list[str] = []
    for m in items[:max_items]:
        text = (m.get("memory") or "").strip()
        if not text:
            continue
        when = (m.get("created_at") or m.get("updated_at") or "").strip()
        if when and len(when) >= 10:
            when = when[:10]
            lines.append(f"- ({when}) {text}")
        else:
            lines.append(f"- {text}")
    if not lines:
        return ""
    return (
        "Episodic memories (past chat notes — topics/questions, not live data):\n"
        + "\n".join(lines)
    )


async def preload_saved_farmer_context(
    memory_user_id: Optional[str],
    query: str,
) -> Optional[str]:
    _ = query
    if not memory_user_id:
        return None

    parts: list[str] = []

    profile = await profile_store.get(memory_user_id)
    if profile and not profile.is_empty():
        snap = render_snapshot(profile)
        if snap:
            parts.append(snap)

    items = await memory_service.get_all(memory_user_id)
    if items:
        items.sort(key=lambda m: m.get("created_at") or "", reverse=True)
        episodic = _format_episodic_block(items)
        if episodic:
            parts.append(episodic)

    if not parts:
        return None

    return (
        "Use structured profile for location/crops/farm setup. "
        "Do not ask for details already in the profile. "
        "Episodic lines are supplementary.\n\n" + "\n\n".join(parts)
    )