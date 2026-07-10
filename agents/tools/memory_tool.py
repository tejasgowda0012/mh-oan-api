from pydantic_ai import RunContext

from agents.deps import FarmerContext
from helpers.utils import get_logger

logger = get_logger(__name__)


async def recall_farmer_memory(ctx: RunContext[FarmerContext], query: str) -> str:
    """Search stored memories about this farmer from past chats.

    Use when the farmer refers to earlier conversations, their farm context, or
    facts you may have saved before. Do NOT use for live mandi, weather, or schemes.

    Args:
        query: What to look up, e.g. "cotton pest advice" or "village and crops".
    """
    user_id = ctx.deps.memory_user_id
    if not user_id:
        return "No farmer memory available for this session."

    from app.services.memory import memory_service

    result = await memory_service.search(
        query=query,
        user_id=user_id,
        top_k=5,
        threshold=0.3,
    )
    logger.info("recall_farmer_memory user=%s query=%r -> %d chars", user_id, query, len(result))

    if result and not result.startswith("No relevant"):
        return result

    items = await memory_service.get_all(user_id)
    if items:
        items.sort(key=lambda m: m.get("created_at") or "", reverse=True)
        recent = [m["memory"] for m in items[:3] if m.get("memory")]
        if recent:
            return (
                "No exact match; most recent saved memories:\n"
                + "\n---\n".join(recent)
            )

    return "No relevant past memories found."


async def save_farmer_memory(ctx: RunContext[FarmerContext], memory: str) -> str:
    """Save an episodic note about the farmer for future chats (mem0).

    Use for past topics, open questions, or chat notes — not for village/district/crop/land/irrigation
    (use `update_farmer_profile` for those). Do NOT save mandi prices, weather, or scheme details.

    Args:
        memory: Clear factual sentence(s) to store, in English or Marathi.
    """
    user_id = ctx.deps.memory_user_id
    if not user_id:
        return "No farmer memory available for this session."

    from app.services.memory import memory_service

    return await memory_service.add_fact(user_id, memory, source="save_farmer_memory", infer=False)