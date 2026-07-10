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

    Use when the farmer explicitly states durable context — village, district, crop they grow,
    acres, drip/canal irrigation. Do NOT use for mandi/weather/scheme answers or one-off questions.

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