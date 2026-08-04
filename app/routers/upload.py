import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from app.auth.jwt_auth import get_current_user
from app.config import settings
from app.core.limiter import limiter
from app.services.pest_image_storage import PestImageStorageError, pest_image_storage
from app.utils import get_cache, set_cache
from helpers.utils import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/upload", tags=["upload-pest-detection-image"])

# ---------------------------------------------------------------------------
# MinIO object storage + Redis metadata for crop image uploads
# ---------------------------------------------------------------------------

UPLOAD_KEY_PREFIX = "pest_upload:"
UPLOAD_ID_PREFIX = "pest_"
ALLOWED_IMAGE_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024


def _validate_upload_metadata(crop_id: str, crop_type: str, sowing_date: str) -> None:
    if not crop_id.isdigit() or int(crop_id) <= 0:
        raise ValueError("crop_id must be a positive integer.")
    if not crop_type or len(crop_type) > 100:
        raise ValueError("crop_type is required and must be at most 100 characters.")
    try:
        parsed_sowing_date = date.fromisoformat(sowing_date)
    except ValueError as exc:
        raise ValueError("sowing_date must use YYYY-MM-DD format.") from exc
    if parsed_sowing_date > date.today() - timedelta(days=7):
        raise ValueError("sowing_date must be at least 7 days ago.")


def _matches_image_signature(content_type: str, content: bytes) -> bool:
    signatures = {
        "image/jpeg": content.startswith(b"\xff\xd8\xff"),
        "image/jpg": content.startswith(b"\xff\xd8\xff"),
        "image/png": content.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/webp": len(content) >= 12
        and content.startswith(b"RIFF")
        and content[8:12] == b"WEBP",
    }
    return signatures.get(content_type, False)


def generate_upload_id() -> str:
    return f"{UPLOAD_ID_PREFIX}{uuid.uuid4()}"


def _upload_cache_key(upload_id: str) -> str:
    return f"{UPLOAD_KEY_PREFIX}{upload_id}"


def _normalize_upload_id(upload_id: str) -> str:
    upload_id = upload_id.strip()
    if not upload_id.startswith(UPLOAD_ID_PREFIX):
        upload_id = f"{UPLOAD_ID_PREFIX}{upload_id}"
    return upload_id


def build_upload_image_url(base_url: str, upload_id: str) -> str:
    """Authenticated API URL to fetch a temporarily stored upload image."""
    base = base_url.rstrip("/")
    prefix = settings.api_prefix.rstrip("/")
    return f"{base}{prefix}/upload/{upload_id}/image"


async def get_pest_upload_image_bytes(record: Dict[str, Any]) -> bytes:
    """Read an upload image from MinIO, regardless of the API replica."""
    object_key = record.get("object_key")
    if not isinstance(object_key, str) or not object_key:
        raise FileNotFoundError("Pest upload object key is unavailable")

    return await asyncio.to_thread(pest_image_storage.get_image, object_key)


async def save_pest_upload(
    image: UploadFile,
    crop_id: str,
    crop_type: str,
    sowing_date: str,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    crop_id = crop_id.strip()
    crop_type = crop_type.strip()
    sowing_date = sowing_date.strip()
    _validate_upload_metadata(crop_id, crop_type, sowing_date)

    content_type = image.content_type or "application/octet-stream"
    if content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
        raise ValueError(
            "Unsupported image type. Allowed types: JPEG, PNG, WEBP."
        )

    image_bytes = await image.read()
    if not image_bytes:
        raise ValueError("Uploaded image is empty.")
    if len(image_bytes) > MAX_IMAGE_SIZE_BYTES:
        raise ValueError("Uploaded image exceeds the 10 MB size limit.")
    if not _matches_image_signature(content_type, image_bytes):
        raise ValueError("Uploaded file content does not match its image type.")

    upload_id = generate_upload_id()
    extension = ALLOWED_IMAGE_CONTENT_TYPES[content_type]
    filename = f"{upload_id}{extension}"
    object_key = f"pest-detection/{upload_id}{extension}"

    await asyncio.to_thread(
        pest_image_storage.put_image,
        object_key,
        image_bytes,
        content_type,
    )

    image_url = build_upload_image_url(base_url, upload_id) if base_url else None

    record: Dict[str, Any] = {
        "upload_id": upload_id,
        "crop_id": crop_id,
        "crop_type": crop_type,
        "sowing_date": sowing_date,
        "object_key": object_key,
        "image_url": image_url,
        "image_filename": image.filename or filename,
        "content_type": content_type,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "analysis": None,
    }

    await set_cache(
        _upload_cache_key(upload_id),
        record,
        ttl=settings.pest_upload_cache_ttl,
    )
    return record


async def get_pest_upload(upload_id: str) -> Optional[Dict[str, Any]]:
    normalized_id = _normalize_upload_id(upload_id)
    return await get_cache(_upload_cache_key(normalized_id))


async def update_pest_upload(upload_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    record = await get_pest_upload(upload_id)
    if not record:
        raise ValueError(f"No pest upload found for upload_id '{upload_id}'.")

    record.update(updates)
    await set_cache(
        _upload_cache_key(record["upload_id"]),
        record,
        ttl=settings.pest_upload_cache_ttl,
    )
    return record


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _public_base_url(request: Request) -> str:
    if settings.api_public_base_url:
        return settings.api_public_base_url.rstrip("/")
    return str(request.base_url).rstrip("/")


@router.post("/")
@limiter.limit("1000/hour")
async def upload_pest_detection_image(
    request: Request,
    image: UploadFile = File(...),
    crop_id: str = Form(...),
    crop_type: str = Form(...),
    sowing_date: str = Form(...),
    _user_info: dict = Depends(get_current_user),
):
    """
    Upload a crop image and metadata for later pest and disease analysis in chat.

    Saves the image in MinIO and stores crop details in Redis. MinIO bucket
    lifecycle retention controls how long the image object remains available.
    """
    try:
        record = await save_pest_upload(
            image=image,
            crop_id=crop_id.strip(),
            crop_type=crop_type.strip(),
            sowing_date=sowing_date.strip(),
            base_url=_public_base_url(request),
        )
    except ValueError as exc:
        return JSONResponse(
            {
                "status": "error",
                "message": str(exc),
            },
            status_code=400,
        )
    except Exception:
        logger.exception("Failed to save pest detection upload")
        return JSONResponse(
            {
                "status": "error",
                "message": "Unable to store the image right now. Please try again later.",
            },
            status_code=500,
        )

    upload_id = record["upload_id"]
    return JSONResponse(
        {
            "status": "success",
            "id": upload_id,
            "upload_id": upload_id,
            "url": record["image_url"],
            "crop_id": record["crop_id"],
            "crop_type": record["crop_type"],
            "sowing_date": record["sowing_date"],
            "message": (
                "Image uploaded successfully. Ask in chat for pest analysis using this id."
            ),
        },
        status_code=200,
    )


@router.get("/{upload_id}/image")
@limiter.limit("1000/hour")
async def get_upload_image(
    request: Request,
    upload_id: str,
    _user_info: dict = Depends(get_current_user),
):
    """Serve a temporarily stored upload image by id."""
    record = await get_pest_upload(upload_id)
    if not record:
        raise HTTPException(status_code=404, detail="Upload not found or expired.")

    try:
        image_bytes = await get_pest_upload_image_bytes(record)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Image file not available.") from exc
    except PestImageStorageError as exc:
        logger.exception("Unable to read pest upload image")
        raise HTTPException(
            status_code=502, detail="Image storage is temporarily unavailable."
        ) from exc

    return Response(
        content=image_bytes,
        media_type=record.get("content_type") or "image/jpeg",
        headers={"Cache-Control": "private, no-store"},
    )
