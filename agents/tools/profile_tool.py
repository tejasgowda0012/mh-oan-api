from typing import Literal

from pydantic_ai import RunContext

from agents.deps import FarmerContext
from helpers.utils import get_logger

logger = get_logger(__name__)

ProfileField = Literal[
    "name",
    "village",
    "district",
    "state",
    "preferred_mandi",
    "crop",
    "land_area_acres",
    "irrigation",
    "soil_type",
    "livestock",
    "language",
    "preferred_call_time",
    "scheme",
    "note",
]

_FIELD_MAP = {
    "crop": "crops",
    "livestock": "livestock",
    "scheme": "schemes",
    "note": "notes",
}


def _to_partial(field: str, value: str) -> dict:
    if field == "crop":
        return {"crops": [{"name": value}]}
    target = _FIELD_MAP.get(field, field)
    if target in ("livestock", "schemes", "notes"):
        return {target: [value]}
    if field == "land_area_acres":
        try:
            return {"land_area_acres": float("".join(c for c in value if c.isdigit() or c == "."))}
        except ValueError:
            return {"notes": [f"land area: {value}"]}
    return {field: value}


async def update_farmer_profile(
    ctx: RunContext[FarmerContext], field: ProfileField, value: str
) -> str:
    """Save stable farm facts to the structured profile (location, crops, irrigation, land).

    Call this only when the farmer explicitly states a new structured fact or changes
    a value already present in saved profile context. Do not call it for an unchanged
    duplicate. Call once per changed fact: name, village, district, state, preferred
    mandi, crop grown, acres, irrigation, soil type, livestock, language, preferred
    call time, scheme participation, or a durable note. Do NOT save facts inferred
    from a question or returned by another tool. Do NOT use for live data, secrets,
    identifiers, or OTPs. Use `remove_farmer_profile_value` for explicit retractions.

    Args:
        field: Profile field (e.g. village, district, crop, irrigation, land_area_acres).
        value: Value to store, e.g. Bhadgaon, Jalgaon, cotton, drip, 5.
    """
    user_id = ctx.deps.memory_user_id
    if not user_id:
        return "No farmer profile available for this session."

    from app.services.profile import profile_store

    await profile_store.apply_update(user_id, _to_partial(field, value))
    logger.info("update_farmer_profile user=%s field=%s value=%r", user_id, field, value)
    return f"Saved profile {field}: {value}."


async def remove_farmer_profile_value(
    ctx: RunContext[FarmerContext], field: ProfileField, value: str
) -> str:
    """Remove one explicitly retracted structured fact from the farmer profile.

    Call only when the farmer clearly says an existing value is no longer true or
    asks to remove it, for example "I no longer grow cotton." For a replacement,
    remove the old list/crop value and then call `update_farmer_profile` with the new
    value. Never remove a merely absent, inferred, or ambiguous fact.

    Args:
        field: Profile field whose old value should be removed, e.g. crop or livestock.
        value: Exact old value the farmer retracted, e.g. cotton.
    """
    user_id = ctx.deps.memory_user_id
    if not user_id:
        return "No farmer profile available for this session."

    from app.services.profile import profile_store

    target = _FIELD_MAP.get(field, field)
    changed = await profile_store.apply_removal(user_id, target, value)
    logger.info(
        "remove_farmer_profile_value user=%s field=%s value=%r changed=%s",
        user_id,
        field,
        value,
        changed,
    )
    if not changed:
        return "No matching profile value found."
    return f"Removed profile {field}: {value}."
