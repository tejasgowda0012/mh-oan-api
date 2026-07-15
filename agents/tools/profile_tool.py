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

    You MUST call this in the same turn for every durable structured fact the farmer
    explicitly states or corrects, even when the farmer did not ask you to remember
    it or the message contains no question. Call once per fact: name, village,
    district, state, preferred mandi, crop grown, acres, irrigation, soil type,
    livestock, language, preferred call time, scheme participation, or a durable
    note. Do NOT save facts inferred from a question or returned by another tool.
    Do NOT use for live mandi/weather/scheme answers, secrets, identifiers, or OTPs.

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
