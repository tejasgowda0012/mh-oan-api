from pydantic_ai import RunContext
from langfuse import observe

from agents.deps import FarmerContext
from helpers.utils import get_logger

logger = get_logger(__name__)


@observe(name="tool:recall_farmer_memory", as_type="tool")
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
        recent = [
            f"- Memory ID: {m.get('id')}\n  Memory: {m['memory']}"
            for m in items[:3]
            if m.get("id") and m.get("memory")
        ]
        if recent:
            return (
                "No exact match; most recent saved memories:\n"
                + "\n".join(recent)
            )

    return "No relevant past memories found."


@observe(name="tool:save_farmer_memory", as_type="tool")
async def save_farmer_memory(ctx: RunContext[FarmerContext], memory: str) -> str:
    """Save an episodic note about the farmer for future chats (mem0).

    First call `recall_farmer_memory` with the candidate topic. Call this only when
    recall shows no equivalent memory and the farmer explicitly provided useful,
    durable context that does not fit the structured profile, such as an ongoing
    farm problem, open follow-up, durable preference, or past advice topic. If an
    existing memory is equivalent, do nothing. If one memory is clearly superseded,
    call `edit_farmer_memory` instead. Do not use this for structured profile facts,
    inferred facts, secrets, OTPs, identifiers, live data, or general knowledge.

    Args:
        memory: Clear factual sentence(s) to store, in English or Marathi.
    """
    user_id = ctx.deps.memory_user_id
    if not user_id:
        return "No farmer memory available for this session."

    from app.services.memory import memory_service

    return await memory_service.add_fact(user_id, memory, source="save_farmer_memory", infer=False)


@observe(name="tool:edit_farmer_memory", as_type="tool")
async def edit_farmer_memory(
    ctx: RunContext[FarmerContext], memory_id: str, new_memory: str
) -> str:
    """Correct one previously saved episodic farmer memory.

    First call `recall_farmer_memory` to obtain the exact memory ID. Use only when
    the farmer explicitly corrects a saved note. If multiple memories could match,
    ask the farmer which one they mean before calling this tool. Structured farm
    facts belong in `update_farmer_profile` instead.

    Args:
        memory_id: Opaque ID returned by `recall_farmer_memory`; never guess it.
        new_memory: Complete corrected memory text that should replace the old text.
    """
    user_id = ctx.deps.memory_user_id
    if not user_id:
        return "No farmer memory available for this session."

    from app.services.memory import memory_service

    return await memory_service.update_memory(user_id, memory_id, new_memory)


@observe(name="tool:delete_farmer_memory", as_type="tool")
async def delete_farmer_memory(
    ctx: RunContext[FarmerContext], memory_id: str
) -> str:
    """Delete one previously saved episodic farmer memory.

    First call `recall_farmer_memory` to obtain the exact memory ID. Use only after
    the farmer explicitly asks to forget or delete that memory. If multiple memories
    could match, ask which one they mean. Never guess an ID or delete unrelated facts.

    Args:
        memory_id: Opaque ID returned by `recall_farmer_memory`; never guess it.
    """
    user_id = ctx.deps.memory_user_id
    if not user_id:
        return "No farmer memory available for this session."

    from app.services.memory import memory_service

    return await memory_service.delete_memory(user_id, memory_id)
