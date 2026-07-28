"""
Pest & disease detection: Mahapocra/TIH API calls via agent tools.

Upload (temp file + Redis) lives in app.routers.upload.
"""

import json
import os
import base64
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic_ai import ModelRetry, RunContext

from app.config import settings
from agents.deps import FarmerContext
from helpers.utils import get_logger

logger = get_logger(__name__)

DEFAULT_PEST_AUTH_URL = "https://stage-farmers-app-api.mahapocra.gov.in/jwtServices/LoginCheckChatbot"
REQUIRED_PEST_ENDPOINTS = (
    "PEST_DETECTION_PREDICT_URL",
    "PEST_DETECTION_ADVISORY_URL",
    "PEST_DETECTION_STORE_RESPONSE_URL",
)
PEST_GUEST_USER_ID = "3"
POSTGRES_INTEGER_MAX = 2_147_483_647


@dataclass(frozen=True)
class PestServiceAuth:
    headers: Dict[str, str]
    user_id: str


def _read_image_bytes(image_path: str) -> bytes:
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Uploaded image file not found at {image_path}.")
    return path.read_bytes()


def _normalize_pest_registration_id(value: Any) -> Optional[str]:
    """Return an ID accepted by the upstream PostgreSQL integer column."""
    if value in (None, ""):
        return None
    candidate = str(value).strip()
    if not candidate.isdigit():
        return None
    registration_id = int(candidate)
    if registration_id <= 0 or registration_id > POSTGRES_INTEGER_MAX:
        return None
    return str(registration_id)


def _get_pest_login_uid_candidates(ctx: RunContext[FarmerContext]) -> List[str]:
    user_type = ctx.deps.get_user_claim("user_type", "sub")
    if str(user_type).lower() == "guest":
        return [PEST_GUEST_USER_ID]

    candidates: List[str] = []
    # LoginCheckChatbot queries Tbl_T_FarmerRegistrations.id (PostgreSQL
    # integer). A phone number is not a valid login UID and can overflow that
    # column, so only registration/farmer identifiers are considered here.
    raw_candidates = (
        ctx.deps.farmer_id,
        ctx.deps.get_user_claim("farmer_id", "farmerid"),
        ctx.deps.unique_id,
        ctx.deps.get_user_claim("unique_id"),
    )
    for value in raw_candidates:
        registration_id = _normalize_pest_registration_id(value)
        if registration_id:
            candidates.append(registration_id)

    # Prefer the authenticated farmer registration ID. The upstream service
    # still supports its guest account for users whose JWT registration ID is
    # missing or not present in the pest-service database. Never send mobile
    # numbers: its login column is a PostgreSQL integer and phone numbers can
    # overflow it.
    candidates.append(PEST_GUEST_USER_ID)
    return list(dict.fromkeys(candidates))


def _encrypt_uid(uid: str) -> str:
    enc_key = os.getenv("PEST_ENC_KEY") or os.getenv("ENC_KEY")
    if not enc_key:
        raise RuntimeError("PEST_ENC_KEY or ENC_KEY is required for pest detection login")

    normalized_key = "".join(enc_key.strip().strip("\"'").split())
    key = base64.b64decode(normalized_key)
    if len(key) != 32:
        raise RuntimeError("PEST_ENC_KEY or ENC_KEY must decode to a 32-byte AES key")

    iv = os.urandom(12)
    encrypted = AESGCM(key).encrypt(iv, uid.encode("utf-8"), None)
    ciphertext, tag = encrypted[:-16], encrypted[-16:]
    return base64.b64encode(iv + tag + ciphertext).decode("utf-8")


def _normalize_token_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _looks_like_token(value: str) -> bool:
    stripped = value.strip()
    return (
        len(stripped) >= 20
        and not stripped.startswith(("{", "["))
        and not any(char.isspace() for char in stripped)
    )


def _extract_first_value(data: Any, *keys: str) -> Optional[str]:
    if isinstance(data, dict):
        normalized_keys = {_normalize_token_key(key): key for key in keys}
        for key, value in data.items():
            normalized_key = _normalize_token_key(key)
            if normalized_key in normalized_keys and value not in (None, ""):
                return str(value)
            if (
                normalized_key == "response"
                and isinstance(value, str)
                and _looks_like_token(value)
            ):
                return value.strip()
        for value in data.values():
            nested = _extract_first_value(value, *keys)
            if nested:
                return nested
    elif isinstance(data, list):
        for item in data:
            nested = _extract_first_value(item, *keys)
            if nested:
                return nested
    elif isinstance(data, str):
        stripped = data.strip()
        if stripped.startswith(("{", "[")):
            try:
                nested_payload = json.loads(stripped)
            except json.JSONDecodeError:
                nested_payload = None
            if nested_payload is not None:
                return _extract_first_value(nested_payload, *keys)
        if stripped.count(".") == 2:
            return stripped
    return None


def _summarize_login_payload(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {"type": type(payload).__name__}
    summary: Dict[str, Any] = {"keys": list(payload.keys())}
    for key in ("status", "success", "message", "error", "code"):
        value = payload.get(key)
        if value not in (None, ""):
            summary[key] = value
    data = payload.get("data")
    if isinstance(data, dict):
        summary["data_keys"] = list(data.keys())
    response = payload.get("response")
    if isinstance(response, dict):
        summary["response_keys"] = list(response.keys())
    elif isinstance(response, list):
        summary["response_type"] = "list"
        summary["response_len"] = len(response)
    elif isinstance(response, str):
        summary["response_type"] = "str"
        summary["response_len"] = len(response)
        if len(response) <= 80 and not _looks_like_token(response):
            summary["response"] = response
    return summary


async def _authenticate_pest_service_with_identity(
    ctx: RunContext[FarmerContext],
) -> PestServiceAuth:
    timeout = settings.pest_detection_http_timeout
    last_payload: Any = {}
    candidates = _get_pest_login_uid_candidates(ctx)
    for candidate_index, uid in enumerate(candidates, start=1):
        encrypted_uid = _encrypt_uid(uid)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    os.getenv("PEST_LOGIN_URL", DEFAULT_PEST_AUTH_URL),
                    headers={"uid": encrypted_uid},
                )
                response.raise_for_status()
                payload = response.json() if response.content else {}
        except httpx.HTTPStatusError as exc:
            # An unknown farmer registration can be rejected with either a
            # successful HTTP response containing "Invalid request" or a 4xx.
            # In both cases, continue to the configured guest candidate.
            logger.warning(
                "Pest detection login rejected uid candidate %s with status %s",
                candidate_index,
                exc.response.status_code,
            )
            last_payload = {"status_code": exc.response.status_code}
            continue
        except (ValueError, json.JSONDecodeError):
            logger.warning(
                "Pest detection login returned invalid JSON for uid candidate %s",
                candidate_index,
            )
            last_payload = {"error": "invalid_json"}
            continue

        access_token = _extract_first_value(
            payload,
            "access_token",
            "accessToken",
            "access",
            "token",
            "jwt",
            "jwt_token",
            "jwtToken",
            "bearer_token",
            "bearerToken",
            "auth_token",
            "authToken",
        )
        refresh_token = _extract_first_value(payload, "refresh_token", "refreshToken")
        if access_token:
            headers = {"Authorization": f"Bearer {access_token}"}
            if refresh_token:
                headers["X-Refresh-Token"] = refresh_token
            return PestServiceAuth(headers=headers, user_id=uid)

        last_payload = payload
        logger.warning(
            "Pest detection login did not return a token for uid candidate %s: %s",
            candidate_index,
            _summarize_login_payload(payload),
        )

    logger.error(
        "Pest detection login response missing access token: %s",
        _summarize_login_payload(last_payload),
    )
    raise RuntimeError("Pest detection login response missing access token")


async def _authenticate_pest_service(
    ctx: RunContext[FarmerContext],
) -> Dict[str, str]:
    """Authenticate for callers that only need upstream request headers."""
    auth = await _authenticate_pest_service_with_identity(ctx)
    return auth.headers


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _extract_pd_id(payload: Dict[str, Any]) -> Optional[str]:
    for key in ("pd_id", "pdId", "id"):
        value = payload.get(key)
        if value:
            return str(value)

    for nested_key in ("data", "result", "response"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            pd_id = _extract_pd_id(nested)
            if pd_id:
                return pd_id
    return None


def _extract_predictions(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("predictions", "prediction", "results", "diseases"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    data = payload.get("data")
    if isinstance(data, dict):
        nested = _extract_predictions(data)
        if nested:
            return nested

    if any(
        key in payload
        for key in ("disease_type", "disease", "disease_name", "label", "name")
    ):
        return [payload]
    return []


def _resolve_predict_pd_id(
    predict_response: Dict[str, Any],
    predictions: List[Dict[str, Any]],
) -> Optional[str]:
    pd_id = _extract_pd_id(predict_response)
    if pd_id:
        return pd_id

    for prediction in predictions:
        for key in ("pd_id", "disease_id", "id"):
            value = prediction.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return None


def _extract_advisory_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _format_advisory_for_farmer(
    advisory_response: Dict[str, Any],
    predictions: List[Dict[str, Any]],
) -> str:
    """
    Format Mahapocra crop_pd_advisory response for chat:
    Always show explicit headers (Crop / Disease-Pest), then advisory blocks.
    """
    items = _extract_advisory_items(advisory_response)
    item = items[0] if items else {}

    crop_name = (
        advisory_response.get("crop_name_mr")
        or item.get("crop_name_mr")
        or item.get("crop_name")
        or ""
    ).strip()
    disease = (item.get("disease_pest_mr") or item.get("disease_pest") or "").strip()
    preventive = (item.get("preventive_measures") or "").strip()
    curative = (item.get("curative_measures") or "").strip()

    if not disease and predictions:
        top = predictions[0]
        disease = (
            top.get("disease_type")
            or top.get("disease_name")
            or top.get("disease")
            or ""
        ).strip()

    header_lines: List[str] = []
    if crop_name:
        header_lines.append(f"**Crop name:** {crop_name}")
    if disease:
        header_lines.append(f"**Pest/Disease name:** {disease}")

    advisory_sections: List[str] = []
    if preventive:
        advisory_sections.append(
            "**Preventive measures (bachav ke upay):**\n" + preventive
        )
    if curative:
        advisory_sections.append(
            "**Curative measures (ilaaj ke upay):**\n" + curative
        )

    parts: List[str] = []
    if header_lines:
        parts.append("\n".join(header_lines))
    if advisory_sections:
        parts.append("\n\n".join(advisory_sections))

    if parts:
        return "\n\n".join(parts)

    for key in ("advisory", "advisory_text", "message", "text", "content"):
        value = advisory_response.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return json.dumps(advisory_response, ensure_ascii=False)


def _format_prediction_summary(predictions: List[Dict[str, Any]]) -> str:
    if not predictions:
        return "No disease prediction was returned by the analysis service."

    lines = []
    for index, prediction in enumerate(predictions[:3], start=1):
        name = (
            prediction.get("disease_type")
            or prediction.get("disease_name")
            or prediction.get("disease")
            or prediction.get("label")
            or prediction.get("name")
            or prediction.get("class_name")
            or "Unknown disease"
        )
        confidence = (
            prediction.get("confidence_score")
            or prediction.get("confidence")
            or prediction.get("score")
            or prediction.get("probability")
        )
        if confidence is not None:
            lines.append(f"{index}. {name} (confidence: {confidence})")
        else:
            lines.append(f"{index}. {name}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mahapocra / TIH HTTP
# ---------------------------------------------------------------------------


async def _post_multipart_predict(
    url: str,
    upload_record: Dict[str, Any],
    image_bytes: bytes,
    auth_headers: Dict[str, str],
) -> Dict[str, Any]:
    data = {
        "crop_id": upload_record["crop_id"],
        "crop_type": upload_record["crop_type"],
        "sowing_date": upload_record["sowing_date"],
    }
    files = {
        "image": (
            upload_record.get("image_filename") or f"{upload_record['upload_id']}.jpg",
            image_bytes,
            upload_record.get("content_type") or "image/jpeg",
        )
    }

    timeout = settings.pest_detection_http_timeout
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=auth_headers, data=data, files=files)
        response.raise_for_status()
        if response.content:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Pest prediction API returned a non-object response")
            return payload
        return {}


async def _post_crop_pd_advisory(
    url: str,
    pd_id: str,
    auth_headers: Dict[str, str],
) -> Dict[str, Any]:
    timeout = settings.pest_detection_http_timeout
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=auth_headers, data={"pd_id": pd_id})
        response.raise_for_status()
        if response.content:
            payload = response.json()
            if isinstance(payload, list):
                return {"data": payload}
            if not isinstance(payload, dict):
                raise ValueError("Pest advisory API returned a non-object response")
            return payload
        return {}


async def _post_store_response(
    url: str,
    upload_record: Dict[str, Any],
    image_bytes: bytes,
    predict_response: Dict[str, Any],
    pd_id: str,
    user_id: str,
    auth_headers: Dict[str, str],
) -> Dict[str, Any]:
    data = {
        "crop_id": str(upload_record["crop_id"]),
        "sowing_date": upload_record["sowing_date"],
        "is_success": "true",
        "response": json.dumps(predict_response, ensure_ascii=False),
        "user_id": user_id,
        "pd_id": pd_id,
    }
    files = {
        "image": (
            upload_record.get("image_filename") or f"{upload_record['upload_id']}.jpg",
            image_bytes,
            upload_record.get("content_type") or "image/jpeg",
        )
    }

    timeout = settings.pest_detection_http_timeout
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=auth_headers, data=data, files=files)
        response.raise_for_status()
        if response.content:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Pest store-response API returned a non-object response")
            return payload
        return {}

async def run_pest_detection_analysis(ctx: RunContext[FarmerContext], upload_id: str) -> str:
    from app.routers.upload import get_pest_upload, update_pest_upload

    upload_record = await get_pest_upload(upload_id)
    if not upload_record:
        return (
            f"No uploaded image was found for upload_id '{upload_id}'. "
            "Ask the farmer to upload the crop photo again from the app."
        )

    configured_urls = {
        name: (os.getenv(name) or "").strip() for name in REQUIRED_PEST_ENDPOINTS
    }
    missing_endpoints = [name for name, url in configured_urls.items() if not url]
    if missing_endpoints:
        logger.error("Pest detection endpoints are not configured: %s", missing_endpoints)
        return "Pest detection is temporarily unavailable. Please try again later."

    predict_url = configured_urls["PEST_DETECTION_PREDICT_URL"]
    advisory_url = configured_urls["PEST_DETECTION_ADVISORY_URL"]
    store_response_url = configured_urls["PEST_DETECTION_STORE_RESPONSE_URL"]
    image_bytes = _read_image_bytes(upload_record["image_path"])

    try:
        pest_auth = await _authenticate_pest_service_with_identity(ctx)
        auth_headers = pest_auth.headers
    except Exception:
        logger.exception("Pest detection login failed for %s", upload_id)
        return "Pest detection is temporarily unavailable. Please try again later."

    try:
        predict_response = await _post_multipart_predict(
            predict_url,
            upload_record,
            image_bytes,
            auth_headers,
        )
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Pest detection predict API failed for %s: status=%s response=%s",
            upload_id,
            exc.response.status_code,
            exc.response.text[:1000],
            exc_info=True,
        )
        return f"Pest detection prediction failed: {exc}"
    except (httpx.HTTPError, ValueError) as exc:
        logger.exception("Pest detection predict API request failed for %s", upload_id)
        return f"Pest detection prediction failed: {exc}"

    predictions = _extract_predictions(predict_response)
    predict_pd_id = _resolve_predict_pd_id(predict_response, predictions)
    if not predict_pd_id:
        logger.error(
            "Predict response missing pd_id for %s: %s", upload_id, predict_response
        )
        return (
            "The pest detection service did not return a valid prediction reference. "
            "Please try uploading the image again."
        )

    try:
        advisory_response = await _post_crop_pd_advisory(
            advisory_url,
            predict_pd_id,
            auth_headers,
        )
    except (httpx.HTTPError, ValueError) as exc:
        logger.exception("Pest detection advisory API failed for %s", upload_id)
        return (
            f"Disease prediction completed, but advisory lookup failed: {exc}\n\n"
            f"Prediction summary:\n{_format_prediction_summary(predictions)}"
        )

    farmer_message = _format_advisory_for_farmer(advisory_response, predictions)

    store_pd_id = _extract_pd_id(advisory_response) or predict_pd_id

    stored_response: Dict[str, Any] = {}
    store_error: Optional[str] = None
    try:
        stored_response = await _post_store_response(
            store_response_url,
            upload_record,
            image_bytes,
            predict_response,
            store_pd_id,
            pest_auth.user_id,
            auth_headers,
        )
    except (httpx.HTTPError, ValueError) as exc:
        logger.exception("Pest detection store-response API failed for %s", upload_id)
        # Storing is an analytics/feedback side effect. Do not turn a valid
        # prediction and advisory into a failed farmer experience.
        store_error = str(exc)

    analysis = {
        "predict_pd_id": predict_pd_id,
        "store_pd_id": store_pd_id,
        "predict_response": predict_response,
        "predictions": predictions,
        "advisory_response": advisory_response,
        "stored_response": stored_response,
        "store_error": store_error,
        "pest_user_id": pest_auth.user_id,
        "farmer_message": farmer_message,
    }
    await update_pest_upload(upload_record["upload_id"], {"analysis": analysis})

    return farmer_message


# ---------------------------------------------------------------------------
# Agent tools
# ---------------------------------------------------------------------------


async def analyze_pest_disease_image(ctx: RunContext[FarmerContext], upload_id: str) -> str:
    """
    Run pest and disease analysis for a crop image that was uploaded earlier.

    Call this immediately when the farmer asks for pest/disease analysis and the
    message contains an upload id (e.g. pest_5a466793-ca9d-4104-80a5-434344a19f7a
    or a bare UUID from POST /api/upload). Do not use search_terms or
    search_documents for photo-based pest analysis.

    Loads crop metadata and image from Redis, calls the predict API, then fetches
    advisory text for the predicted disease id (pd_id).

    Args:
        upload_id: Full upload id from the upload API (with or without pest_ prefix).

    Returns:
        Formatted text: bold crop name, bold disease/pest, then preventive and curative
        sections with Hinglish titles. Relay to the farmer with the same formatting.
    """
    upload_id = upload_id.strip().rstrip("-.,;:")
    if not upload_id:
        raise ModelRetry("upload_id is required to analyze a pest detection image.")

    try:
        return await run_pest_detection_analysis(ctx, upload_id)
    except FileNotFoundError:
        logger.exception("Uploaded pest image file missing for %s", upload_id)
        return (
            f"The uploaded image file is no longer available for upload_id '{upload_id}'. "
            "Ask the farmer to upload the photo again from the app."
        )
    except Exception as exc:
        logger.exception("Unexpected pest detection analysis failure for %s", upload_id)
        return "Pest detection is temporarily unavailable. Please try again later."
