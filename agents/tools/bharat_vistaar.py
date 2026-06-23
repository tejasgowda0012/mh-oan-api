"""
Cross-network proxy to Bharat Vistaar.

For now, only scheme status checks and grievances are routed to the BH network.
All other capabilities (advisory, scheme info, MahaDBT status, etc.) stay on MH.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urljoin

import httpx
from langfuse import observe
from pydantic import BaseModel, Field
from pydantic_ai import ModelRetry, RunContext

from agents.deps import FarmerContext
from helpers.utils import get_logger

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 120.0
TOKEN_REFRESH_BUFFER_SECONDS = 60

_cached_auth_token: Optional[str] = None
_cached_auth_token_expires_at: float = 0.0

# Central schemes for which Bharat Vistaar supports live status / grievance (not scheme info).
# MahaVistaar serves scheme *information* for all 103 schemes locally via get_scheme_info.
# Source of truth: assets/scheme_list.json (type == "bharat_vistaar")
with open("assets/scheme_list.json", "r", encoding="utf-8") as _f:
    _scheme_list = json.load(_f)
BHARAT_VISTAAR_OPERATIONAL_SCHEMES = frozenset(
    s["scheme_code"] for s in _scheme_list if s.get("type") == "bharat_vistaar"
)


def _require_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise ValueError(f"{name} is not configured")
    return value


def _bharat_vistaar_bpp_id() -> str:
    return _require_env("BHARAT_VISTAAR_BPP_ID")


def _bharat_vistaar_bpp_uri() -> str:
    return _require_env("BHARAT_VISTAAR_BPP_URI")


def _beckn_context(*, transaction_id: str, action: str) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "domain": "schemes:vistaar",
        "action": action,
        "version": "1.1.0",
        "bap_id": os.getenv("BHARAT_VISTAAR_BAP_ID") or os.getenv("BAP_ID"),
        "bap_uri": os.getenv("BHARAT_VISTAAR_BAP_URI") or os.getenv("BAP_URI"),
        "bpp_id": _bharat_vistaar_bpp_id(),
        "bpp_uri": _bharat_vistaar_bpp_uri(),
        "transaction_id": transaction_id,
        "message_id": str(uuid.uuid4()),
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "ttl": "PT10M",
        "location": {"country": {"code": "IND"}, "city": {"code": "*"}},
    }


def _cross_network_query_payload(query: str, session_id: str) -> Dict[str, Any]:
    transaction_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"mh-xnet:{session_id}"))
    return {
        "context": _beckn_context(transaction_id=transaction_id, action="search"),
        "message": {
            "intent": {
                "category": {"descriptor": {"code": "cross-network-advisory"}},
                "item": {
                    "descriptor": {"code": "farmer-query", "name": query},
                    "tags": [
                        {
                            "descriptor": {"code": "session_id"},
                            "value": session_id,
                        }
                    ],
                },
            }
        },
    }


class CrossNetworkResponse(BaseModel):
    """Minimal Beckn aggregator response for formatting."""

    responses: list = Field(default_factory=list)

    def __str__(self) -> str:
        if not self.responses:
            return "Bharat Vistaar network returned no response."

        parts: list[str] = []
        for block in self.responses:
            message = block.get("message") if isinstance(block, dict) else None
            if not message:
                continue
            catalog = message.get("catalog") or {}
            for provider in catalog.get("providers") or []:
                for item in provider.get("items") or []:
                    desc = (item.get("descriptor") or {}).get("long_desc") or (
                        item.get("descriptor") or {}
                    ).get("short_desc")
                    if desc:
                        parts.append(str(desc).strip())
                    for tag in item.get("tags") or []:
                        for entry in tag.get("list") or []:
                            name = (entry.get("descriptor") or {}).get("name") or (
                                entry.get("descriptor") or {}
                            ).get("code")
                            value = entry.get("value")
                            if name and value:
                                parts.append(f"{name}: {value}")

            order = message.get("order") or {}
            for tag in order.get("tags") or []:
                for entry in tag.get("list") or []:
                    name = (entry.get("descriptor") or {}).get("name") or (
                        entry.get("descriptor") or {}
                    ).get("code")
                    value = entry.get("value")
                    if name and value:
                        parts.append(f"{name}: {value}")

        if parts:
            return "\n".join(parts)
        return json.dumps(self.responses, ensure_ascii=False, indent=2)


def _bharat_session_id(mh_session_id: str) -> str:
    """Stable BH-side session for OTP / multi-turn flows."""
    if not mh_session_id:
        return f"mh-xnet-{uuid.uuid4()}"
    return f"mh-xnet-{mh_session_id}"


def _cache_auth_token(token: str, expires_in: Optional[int]) -> str:
    global _cached_auth_token, _cached_auth_token_expires_at
    _cached_auth_token = token
    if expires_in and expires_in > TOKEN_REFRESH_BUFFER_SECONDS:
        _cached_auth_token_expires_at = time.time() + expires_in - TOKEN_REFRESH_BUFFER_SECONDS
    else:
        _cached_auth_token_expires_at = time.time() + 14 * 60
    return token


async def _fetch_bharat_guest_token(api_base: str) -> str:
    token_url = urljoin(api_base.rstrip("/") + "/", "api/token/")
    async with httpx.AsyncClient() as client:
        response = await client.post(token_url, json={}, timeout=DEFAULT_TIMEOUT)

    if response.status_code != 200:
        logger.error(
            "Bharat Vistaar guest token request returned %s: %s",
            response.status_code,
            response.text[:500],
        )
        raise ValueError(
            f"Could not obtain Bharat Vistaar auth token (HTTP {response.status_code}). "
            "Set BHARAT_VISTAAR_API_TOKEN or BHARAT_VISTAAR_API_KEY."
        )

    data = response.json()
    token = (data.get("token") or "").strip()
    if not token:
        raise ValueError("Bharat Vistaar guest token response did not include a token.")
    return _cache_auth_token(token, data.get("expires_in"))


async def _fetch_bharat_api_key_token(api_base: str, api_key: str, client_code: str) -> str:
    token_url = urljoin(api_base.rstrip("/") + "/", "api/token/api-key")
    async with httpx.AsyncClient() as client:
        response = await client.post(
            token_url,
            headers={"X-API-Key": api_key},
            json={"client_code": client_code},
            timeout=DEFAULT_TIMEOUT,
        )

    if response.status_code != 200:
        logger.error(
            "Bharat Vistaar API-key token request returned %s: %s",
            response.status_code,
            response.text[:500],
        )
        raise ValueError(
            f"Could not obtain Bharat Vistaar auth token via API key (HTTP {response.status_code})."
        )

    data = response.json()
    token = (data.get("token") or "").strip()
    if not token:
        raise ValueError("Bharat Vistaar API-key token response did not include a token.")
    return _cache_auth_token(token, data.get("expires_in"))


async def _get_bharat_vistaar_auth_token(api_base: str) -> Optional[str]:
    """Resolve Bearer token for Bharat Vistaar chat API calls."""
    global _cached_auth_token, _cached_auth_token_expires_at

    static_token = (os.getenv("BHARAT_VISTAAR_API_TOKEN") or "").strip()
    if static_token:
        return static_token

    if _cached_auth_token and time.time() < _cached_auth_token_expires_at:
        return _cached_auth_token

    api_key = (os.getenv("BHARAT_VISTAAR_API_KEY") or "").strip()
    client_code = (os.getenv("BHARAT_VISTAAR_CLIENT_CODE") or "MahaVistaar").strip()
    if api_key:
        return await _fetch_bharat_api_key_token(api_base, api_key, client_code)

    return await _fetch_bharat_guest_token(api_base)


async def _call_bharat_vistaar_chat_api(
    *,
    query: str,
    session_id: str,
    lang_code: str,
) -> str:
    api_base = _require_env("BHARAT_VISTAAR_API_URL").rstrip("/")
    chat_url = urljoin(api_base + "/", "api/chat/")

    params = {
        "query": query,
        "session_id": session_id,
        "source_lang": lang_code,
        "target_lang": lang_code,
        "user_id": "mh-vistaar-cross-network",
    }
    headers: Dict[str, str] = {
        "X-Cross-Network-BPP-ID": _bharat_vistaar_bpp_id(),
        "X-Cross-Network-BPP-URI": _bharat_vistaar_bpp_uri(),
    }
    try:
        token = await _get_bharat_vistaar_auth_token(api_base)
    except ValueError as e:
        logger.error("Bharat Vistaar auth token error: %s", e)
        return (
            "Bharat Vistaar authentication failed. "
            "Set BHARAT_VISTAAR_API_TOKEN or BHARAT_VISTAAR_API_KEY in the environment."
        )

    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient() as client:
        response = await client.get(
            chat_url,
            params=params,
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
        )

    if response.status_code == 401:
        global _cached_auth_token, _cached_auth_token_expires_at
        _cached_auth_token = None
        _cached_auth_token_expires_at = 0.0

    if response.status_code != 200:
        logger.error(
            "Bharat Vistaar chat API returned %s: %s",
            response.status_code,
            response.text[:500],
        )
        if response.status_code == 401:
            return (
                "Bharat Vistaar authentication failed (HTTP 401). "
                "Check BHARAT_VISTAAR_API_TOKEN or BHARAT_VISTAAR_API_KEY."
            )
        return (
            f"Bharat Vistaar service is unavailable (HTTP {response.status_code}). "
            "Please try again later."
        )

    text = response.text.strip()
    return text or "Bharat Vistaar returned an empty response."


async def _call_bharat_vistaar_beckn(
    *,
    query: str,
    session_id: str,
) -> str:
    bap_base = _require_env("BHARAT_VISTAAR_BAP_ENDPOINT").rstrip("/")
    url = f"{bap_base}/search"
    payload = _cross_network_query_payload(query, session_id)

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, timeout=DEFAULT_TIMEOUT)

    if response.status_code != 200:
        logger.error(
            "Bharat Vistaar Beckn search returned %s: %s",
            response.status_code,
            response.text[:500],
        )
        return (
            f"Bharat Vistaar network is unavailable (HTTP {response.status_code}). "
            "Please try again later."
        )

    try:
        data = response.json()
    except json.JSONDecodeError:
        return response.text.strip() or "Bharat Vistaar returned a non-JSON response."

    return str(CrossNetworkResponse.model_validate({"responses": data.get("responses", data)}))


@observe(name="tool:call_bharat_vistaar_network", as_type="tool")
async def call_bharat_vistaar_network(ctx: RunContext[FarmerContext], query: str) -> str:
    """Route scheme status checks and grievances to Bharat Vistaar (cross-network).

    **Current scope:** use this tool only for (1) live scheme status checks and
    (2) grievance filing/tracking. All other queries stay on MahaVistaar.

    Examples that belong here:
    - PM Kisan installment/beneficiary status, PMFBY policy/claim status, SMAM
      application status, Soil Health Card status
    - PM-Kisan or PMFBY grievance submit or ticket tracking

    Do NOT use for scheme information, advisory, weather, mandi, MahaDBT status,
    or anything else — use the corresponding MH tools.

    Args:
        query: Clear English request for Bharat Vistaar. Include every detail the
            farmer already shared (mobile, registration number, OTP, ticket number,
            season, year, grievance description, etc.) so BH can run its status or
            grievance flow without re-asking.

    Returns:
        Bharat Vistaar response (live status, grievance outcome, or next step).
    """
    query = (query or "").strip()
    if not query:
        raise ModelRetry(
            "Provide a clear English query for Bharat Vistaar with all farmer details "
            "already collected in this conversation."
        )

    bh_session = _bharat_session_id(ctx.deps.session_id)
    lang_code = ctx.deps.lang_code or "en"

    try:
        if os.getenv("BHARAT_VISTAAR_API_URL"):
            return await _call_bharat_vistaar_chat_api(
                query=query,
                session_id=bh_session,
                lang_code=lang_code,
            )
        return await _call_bharat_vistaar_beckn(query=query, session_id=bh_session)

    except ValueError as e:
        logger.error("Bharat Vistaar cross-network config error: %s", e)
        return (
            "Bharat Vistaar cross-network is not configured. "
            "Set BHARAT_VISTAAR_BPP_ID, BHARAT_VISTAAR_BPP_URI, and either "
            "BHARAT_VISTAAR_API_URL or BHARAT_VISTAAR_BAP_ENDPOINT."
        )

    except httpx.TimeoutException:
        logger.error("Bharat Vistaar cross-network request timed out")
        return "Bharat Vistaar request timed out. Please try again later."

    except httpx.RequestError as e:
        logger.error("Bharat Vistaar cross-network request failed: %s", e)
        return f"Bharat Vistaar request failed: {e!s}"

    except Exception as e:
        logger.error("Unexpected Bharat Vistaar cross-network error: %s", e)
        raise ModelRetry(f"Unexpected error calling Bharat Vistaar network. {e!s}") from e
