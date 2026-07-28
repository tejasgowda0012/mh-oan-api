import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from app.config import settings
from app.auth.jwt_auth import get_current_user
from app.utils import get_cache, set_cache
from helpers.utils import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/upload", tags=["upload-pest-detection-image"])

# ---------------------------------------------------------------------------
# Temp disk + Redis storage for crop image uploads
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


def get_upload_dir() -> Path:
    upload_dir = settings.base_dir / "temp" / "pest_detection"
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def _upload_cache_key(upload_id: str) -> str:
    return f"{UPLOAD_KEY_PREFIX}{upload_id}"


def _normalize_upload_id(upload_id: str) -> str:
    upload_id = upload_id.strip()
    if not upload_id.startswith(UPLOAD_ID_PREFIX):
        upload_id = f"{UPLOAD_ID_PREFIX}{upload_id}"
    return upload_id


def build_upload_image_url(base_url: str, upload_id: str) -> str:
    """Public URL to fetch a temporarily stored upload image."""
    base = base_url.rstrip("/")
    prefix = settings.api_prefix.rstrip("/")
    return f"{base}{prefix}/upload/{upload_id}/image"


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
    image_path = get_upload_dir() / filename

    async with aiofiles.open(image_path, "wb") as file_handle:
        await file_handle.write(image_bytes)

    image_url = build_upload_image_url(base_url, upload_id) if base_url else None

    record: Dict[str, Any] = {
        "upload_id": upload_id,
        "crop_id": crop_id,
        "crop_type": crop_type,
        "sowing_date": sowing_date,
        "image_path": str(image_path),
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

    Saves the image under temp/pest_detection, stores crop details and image URL in Redis,
    and returns an id (upload_id) plus the public image URL.
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
    except Exception as exc:
        logger.exception("Failed to save pest detection upload")
        return JSONResponse(
            {
                "status": "error",
                "message": f"Failed to save upload: {exc}",
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
async def get_upload_image(
    upload_id: str,
    _user_info: dict = Depends(get_current_user),
):
    """Serve a temporarily stored upload image by id."""
    record = await get_pest_upload(upload_id)
    if not record:
        raise HTTPException(status_code=404, detail="Upload not found or expired.")

    image_path = record.get("image_path")
    if not image_path:
        raise HTTPException(status_code=404, detail="Image file not available.")

    return FileResponse(
        image_path,
        media_type=record.get("content_type") or "image/jpeg",
        filename=record.get("image_filename"),
    )
