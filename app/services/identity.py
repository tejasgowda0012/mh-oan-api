import hashlib
import os
import re
from typing import Optional

# JWT claims that carry a phone number (not generic `sub` — use sub only as opaque id fallback).
_PHONE_CLAIM_CANDIDATES = [
    "phone",
    "phone_number",
    "phoneNumber",
    "mobile",
    "msisdn",
]

_GUEST_SENTINELS = frozenset(
    {
        "",
        "anonymous",
        "guest",
        "guest_user",
        "guest-user",
        "unauthenticated",
        "unknown",
    }
)

_CLAIM_ID_KEYS = ("user_id", "sub", "farmer_id", "farmerid", "uid", "id")


def normalize_phone(phone: str) -> Optional[str]:
    """Normalize Indian phone number to +91XXXXXXXXXX format."""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        digits = "91" + digits
    elif len(digits) == 11 and digits.startswith("0"):
        digits = "91" + digits[1:]
    if len(digits) != 12 or not digits.startswith("91"):
        return None
    return "+" + digits


def phone_to_memory_user_id(phone: str) -> Optional[str]:
    """Stable mem0 key: sha256 of normalized phone. Same phone → same id always."""
    normalized = normalize_phone(phone)
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode()).hexdigest()


def _is_guest_value(value: Optional[str]) -> bool:
    if value is None:
        return True
    s = str(value).strip()
    if not s:
        return True
    lowered = s.lower()
    if lowered in _GUEST_SENTINELS:
        return True
    if lowered.startswith("guest") or lowered.startswith("guest_") or lowered.startswith("guest-"):
        return True
    return False


def is_guest_user(
    request_user_id: Optional[str],
    user_claims: Optional[dict],
) -> bool:
    """Guests never use long-term memory (matches chat default `anonymous` and JWT guest users)."""
    if _is_guest_value(request_user_id):
        return True
    if not user_claims:
        return False
    user_type = user_claims.get("user_type") or user_claims.get("type")
    if user_type is not None and str(user_type).strip().lower() == "guest":
        return True
    for key in ("role", "account_type"):
        raw = user_claims.get(key)
        if raw and str(raw).strip().lower() in ("guest", "anonymous", "unauthenticated"):
            return True
    if user_claims.get("is_guest") is True or user_claims.get("guest") is True:
        return True
    claim_uid = user_claims.get("user_id") or user_claims.get("sub")
    if claim_uid is not None:
        return _is_guest_value(str(claim_uid))
    return False


def extract_phone_from_claims(claims: dict) -> Optional[str]:
    if not claims:
        return None
    pinned = os.getenv("JWT_PHONE_CLAIM")
    if pinned and claims.get(pinned):
        return str(claims[pinned])
    for key in _PHONE_CLAIM_CANDIDATES:
        if claims.get(key):
            return str(claims[key])
    return None


def opaque_id_to_memory_user_id(value: Optional[str]) -> Optional[str]:
    """Use a stable opaque id when no phone is available (not for guests)."""
    if _is_guest_value(value):
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    return cleaned


def to_memory_user_id(value: Optional[str]) -> Optional[str]:
    """Phone → hashed id; opaque id → as-is; guest/anonymous → None."""
    if _is_guest_value(value):
        return None
    phone_id = phone_to_memory_user_id(str(value))
    if phone_id:
        return phone_id
    return opaque_id_to_memory_user_id(value)


def resolve_memory_user_id(
    request_user_id: str,
    user_claims: Optional[dict],
) -> Optional[str]:
    """
    Single stable mem0 user key per farmer.

    Priority: guest skip → JWT phone hash → request phone hash → JWT opaque ids →
    request opaque id only when authenticated (JWT) or in development.
    """
    from app.config import settings

    if is_guest_user(request_user_id, user_claims):
        return None

    claims = user_claims if isinstance(user_claims, dict) else {}
    has_auth_claims = bool(claims)

    phone = extract_phone_from_claims(claims)
    if phone:
        uid = phone_to_memory_user_id(phone)
        if uid:
            return uid

    uid = phone_to_memory_user_id(request_user_id)
    if uid:
        return uid

    for key in _CLAIM_ID_KEYS:
        raw = claims.get(key)
        if raw is None or raw == "":
            continue
        mapped = to_memory_user_id(str(raw))
        if mapped:
            return mapped

    if not has_auth_claims and settings.environment != "development":
        return None

    return opaque_id_to_memory_user_id(request_user_id)