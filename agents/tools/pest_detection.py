import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import load_dotenv
from pydantic_ai import RunContext, UnexpectedModelBehavior

from agents.deps import FarmerContext
from helpers.utils import get_logger

load_dotenv()

logger = get_logger(__name__)

DEFAULT_AUTH_API_URL = "https://stage-farmers-app-api.mahapocra.gov.in"
DEFAULT_FARMERS_API_URL = "https://farmers-app-api.mahapocra.gov.in"
DEFAULT_PEST_PREDICT_URL = "https://ndksp-tih.mahapocra.gov.in/api/v1/predict"
DEFAULT_PEST_ADVISORY_URL = "https://stage-farmers-app-api.mahapocra.gov.in/pestdetectionServices/crop_pd_advisory"
DEFAULT_PEST_CROPS_URL = f"{DEFAULT_FARMERS_API_URL}/pestdetectionServices/get-crops-for-pest-detection"
DEFAULT_PEST_STORE_RESPONSE_URL = f"{DEFAULT_FARMERS_API_URL}/pestdetectionServices/store-response-against-crop-image"


def _env_url(name: str, default_path: str = "", default_base_url: str = DEFAULT_FARMERS_API_URL) -> str:
    base_url = os.getenv("PEST_FARMERS_API_URL", default_base_url).rstrip("/")
    return os.getenv(name, f"{base_url}{default_path}")


def _extract_first_value(data: Dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    nested = data.get("data")
    if isinstance(nested, dict):
        return _extract_first_value(nested, *keys)
    return None


def _get_pest_user_id(ctx: RunContext[FarmerContext]) -> str:
    user_type = ctx.deps.get_user_claim("user_type", "sub")
    if str(user_type).lower() == "guest":
        return "3"

    mobile = ctx.deps.get_user_claim("mobile")
    return str(mobile) if mobile not in (None, "") else "3"


def _encrypt_uid(uid: str) -> str:
    enc_key = os.getenv("PEST_ENC_KEY") or os.getenv("ENC_KEY")
    if not enc_key:
        raise RuntimeError("PEST_ENC_KEY or ENC_KEY is required for pest detection login")

    key = base64.b64decode(enc_key)
    iv = os.urandom(12)
    encrypted = AESGCM(key).encrypt(iv, uid.encode("utf-8"), None)
    ciphertext, tag = encrypted[:-16], encrypted[-16:]
    return base64.b64encode(iv + tag + ciphertext).decode("utf-8")


def _authenticate_pest_service(ctx: RunContext[FarmerContext]) -> Dict[str, str]:
    uid = _get_pest_user_id(ctx)

    login_url = _env_url("PEST_LOGIN_URL", "/jwtServices/LoginCheckChatbot", DEFAULT_AUTH_API_URL)
    encrypted_uid = _encrypt_uid(uid)
    response = requests.post(login_url, headers={"uid": encrypted_uid}, timeout=(10, 20))

    if response.status_code != 200:
        logger.error("Pest login returned status code %s: %s", response.status_code, response.text[:500])
        raise RuntimeError("Pest detection login failed")

    payload = response.json()
    access_token = _extract_first_value(payload, "access_token", "accessToken", "token", "jwt", "jwt_token")
    refresh_token = _extract_first_value(payload, "refresh_token", "refreshToken")

    if not access_token:
        logger.error("Pest login response did not include an access token: %s", payload)
        raise RuntimeError("Pest detection login response missing access token")

    headers = {"Authorization": f"Bearer {access_token}"}
    if refresh_token:
        headers["X-Refresh-Token"] = refresh_token
    return headers


def _image_file_args(image_path: Optional[str], image_base64: Optional[str], image_filename: Optional[str]) -> tuple[str, bytes, str]:
    if image_path:
        path = Path(image_path).expanduser()
        if not path.exists() or not path.is_file():
            raise RuntimeError(f"Image file not found: {image_path}")
        content = path.read_bytes()
        filename = path.name
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return filename, content, content_type

    if image_base64:
        if "," in image_base64 and image_base64.split(",", 1)[0].startswith("data:"):
            image_base64 = image_base64.split(",", 1)[1]
        content = base64.b64decode(image_base64)
        filename = image_filename or "pest-image.jpg"
        content_type = mimetypes.guess_type(filename)[0] or "image/jpeg"
        return filename, content, content_type

    raise RuntimeError("Either image_path or image_base64 is required for pest detection")


def _find_crop_id(crops_payload: Any, crop_type: str) -> Optional[str]:
    crop_name = crop_type.strip().lower()
    candidates = crops_payload
    if isinstance(crops_payload, dict):
        candidates = crops_payload.get("data") or crops_payload.get("crops") or crops_payload.get("result") or crops_payload
    if isinstance(candidates, dict):
        candidates = candidates.get("data") or candidates.get("crops") or candidates.get("list") or []
    if not isinstance(candidates, list):
        return None

    for crop in candidates:
        if not isinstance(crop, dict):
            continue
        name = str(crop.get("crop_type") or crop.get("crop_name") or crop.get("name") or "").strip().lower()
        if name == crop_name:
            crop_id = crop.get("crop_id") or crop.get("id")
            return str(crop_id) if crop_id not in (None, "") else None
    for crop in candidates:
        if not isinstance(crop, dict):
            continue
        name = str(crop.get("crop_type") or crop.get("crop_name") or crop.get("name") or "").strip().lower()
        if crop_name in name or name in crop_name:
            crop_id = crop.get("crop_id") or crop.get("id")
            return str(crop_id) if crop_id not in (None, "") else None
    return None


def _resolve_crop_id(auth_headers: Dict[str, str], crop_type: str, crop_id: Optional[str]) -> str:
    if crop_id:
        return crop_id

    crops_response = requests.get(
        os.getenv("PEST_CROPS_URL", DEFAULT_PEST_CROPS_URL),
        headers=auth_headers,
        timeout=(10, 20),
    )
    if crops_response.status_code != 200:
        logger.error("Pest crop list returned status code %s: %s", crops_response.status_code, crops_response.text[:500])
        raise RuntimeError("Could not fetch pest detection crop list")

    resolved_crop_id = _find_crop_id(crops_response.json(), crop_type)
    if not resolved_crop_id:
        raise RuntimeError(f"Could not find crop_id for crop type: {crop_type}")
    return resolved_crop_id


def _format_detection_result(prediction: Dict[str, Any], advisory: Optional[Dict[str, Any]], stored_response: Optional[Dict[str, Any]]) -> str:
    lines = ["> Pest Detection Result"]

    prediction_data = prediction.get("data") if isinstance(prediction.get("data"), dict) else prediction
    predictions = prediction_data.get("predictions") if isinstance(prediction_data, dict) else None

    if predictions:
        lines.append("Predictions:")
        for item in predictions:
            disease_type = item.get("disease_type") or item.get("name") or item.get("disease_name") or "Unknown"
            disease_id = item.get("disease_id") or item.get("pd_id")
            confidence = item.get("confidence_score") or item.get("confidence")
            detail = f"- {disease_type}"
            if disease_id:
                detail += f" (ID: {disease_id})"
            if confidence is not None:
                detail += f", confidence: {confidence}"
            lines.append(detail)
    else:
        lines.append(json.dumps(prediction, ensure_ascii=False))

    if advisory:
        lines.append("\nAdvisory:")
        advisory_data = advisory.get("data", advisory)
        if isinstance(advisory_data, (dict, list)):
            lines.append(json.dumps(advisory_data, ensure_ascii=False, indent=2))
        else:
            lines.append(str(advisory_data))

    if stored_response:
        stored_id = _extract_first_value(stored_response, "id", "response_id", "feedback_id")
        if stored_id:
            lines.append(f"\nStored response ID: {stored_id}")

    return "\n".join(lines)


async def detect_crop_pest(
    ctx: RunContext[FarmerContext],
    crop_type: str,
    sowing_date: str,
    crop_id: Optional[str] = None,
    image_path: Optional[str] = None,
    image_base64: Optional[str] = None,
    image_filename: Optional[str] = None,
) -> str:
    """Detect pest or disease from a crop image and fetch advisory details for the top detected pest when available.

    Args:
        crop_type: Crop name in English, for example Cotton or Groundnut.
        sowing_date: Crop sowing date in YYYY-MM-DD format.
        crop_id: Crop ID from the pest detection crop list. Leave empty if unknown; the tool will look it up.
        image_path: Local path to the crop image to analyze.
        image_base64: Base64 encoded crop image if a local path is not available.
        image_filename: Filename to use when image_base64 is provided.
    """
    try:
        auth_headers = _authenticate_pest_service(ctx)
        crop_id = _resolve_crop_id(auth_headers, crop_type, crop_id)
        filename, image_content, content_type = _image_file_args(image_path, image_base64, image_filename)
        files = {"image": (filename, image_content, content_type)}
        data = {
            "crop_type": crop_type,
            "sowing_date": sowing_date,
            "crop_id": crop_id,
        }

        predict_url = os.getenv("PEST_PREDICT_URL", DEFAULT_PEST_PREDICT_URL)
        prediction_response = requests.post(
            predict_url,
            headers=auth_headers,
            data=data,
            files=files,
            timeout=(15, 60),
        )

        if prediction_response.status_code != 200:
            logger.error("Pest prediction returned status code %s: %s", prediction_response.status_code, prediction_response.text[:500])
            return "Pest detection service is currently unavailable. Please try again later."

        prediction = prediction_response.json()
        prediction_data = prediction.get("data") if isinstance(prediction.get("data"), dict) else prediction
        top_prediction = None
        if isinstance(prediction_data, dict) and prediction_data.get("predictions"):
            top_prediction = prediction_data["predictions"][0]

        advisory = None
        pd_id = None
        if isinstance(top_prediction, dict):
            pd_id = top_prediction.get("disease_id") or top_prediction.get("pd_id")
        if pd_id:
            advisory_response = requests.post(
                os.getenv("PEST_ADVISORY_URL", DEFAULT_PEST_ADVISORY_URL),
                headers=auth_headers,
                data={"pd_id": str(pd_id)},
                timeout=(10, 20),
            )
            if advisory_response.status_code == 200:
                advisory = advisory_response.json()
            else:
                logger.warning("Pest advisory returned status code %s", advisory_response.status_code)

        stored_response = None
        user_id = _get_pest_user_id(ctx)
        store_files = {"image": (filename, image_content, content_type)}
        store_data = {
            "crop_id": crop_id,
            "sowing_date": sowing_date,
            "is_success": str(bool(prediction.get("success", True))).lower(),
            "response": json.dumps(prediction, ensure_ascii=False),
            "user_id": user_id,
        }
        if pd_id:
            store_data["pd_id"] = str(pd_id)
        store_response = requests.post(
            os.getenv("PEST_STORE_RESPONSE_URL", DEFAULT_PEST_STORE_RESPONSE_URL),
            headers=auth_headers,
            data=store_data,
            files=store_files,
            timeout=(10, 30),
        )
        if store_response.status_code == 200:
            stored_response = store_response.json()
        else:
            logger.warning("Pest store response returned status code %s", store_response.status_code)

        return _format_detection_result(prediction, advisory, stored_response)

    except requests.Timeout as e:
        logger.error("Pest detection request timed out: %s", str(e))
        return "Pest detection request timed out. Please try again later."
    except requests.RequestException as e:
        logger.error("Pest detection request failed: %s", str(e))
        return "Pest detection request failed. Please try again later."
    except UnexpectedModelBehavior:
        logger.warning("Pest detection request exceeded retry limit")
        return "Pest detection is temporarily unavailable. Please try again later."
    except Exception as e:
        logger.error("Error in pest detection tool: %s", str(e))
        return "Pest detection is temporarily unavailable. Please try again later."
