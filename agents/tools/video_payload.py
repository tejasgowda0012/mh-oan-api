"""Structured video payloads for AG-UI inline playback."""

from __future__ import annotations

import re
from typing import Any, Literal, Optional
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, Field


VideoProvider = Literal["youtube", "vimeo", "direct", "unknown"]


class VideoResource(BaseModel):
    """Single video resource the frontend can render inline."""

    id: str
    title: str
    url: str
    provider: VideoProvider = "unknown"
    embed_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    description: Optional[str] = None
    source: Optional[str] = None

    def to_ag_ui_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}
_VIMEO_HOSTS = {"vimeo.com", "www.vimeo.com", "player.vimeo.com"}
_DIRECT_SUFFIXES = (".mp4", ".webm", ".m3u8", ".mov", ".ogg")


def detect_provider(url: str) -> VideoProvider:
    if not url:
        return "unknown"
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return "unknown"
    if host in _YOUTUBE_HOSTS or host.endswith(".youtube.com"):
        return "youtube"
    if host in _VIMEO_HOSTS:
        return "vimeo"
    path = urlparse(url).path.lower()
    if any(path.endswith(suffix) for suffix in _DIRECT_SUFFIXES):
        return "direct"
    return "unknown"


def youtube_video_id(url: str) -> Optional[str]:
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    host = (parsed.hostname or "").lower()
    if host in {"youtu.be", "www.youtu.be"}:
        vid = parsed.path.lstrip("/").split("/")[0]
        return vid or None
    if "youtube" in host:
        qs = parse_qs(parsed.query)
        if "v" in qs and qs["v"]:
            return qs["v"][0]
        # /embed/ID or /shorts/ID
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in {"embed", "shorts", "v"}:
            return parts[1]
    return None


def build_embed_url(url: str, provider: VideoProvider | None = None) -> Optional[str]:
    provider = provider or detect_provider(url)
    if provider == "youtube":
        vid = youtube_video_id(url)
        return f"https://www.youtube.com/embed/{vid}" if vid else None
    if provider == "vimeo":
        try:
            parts = [p for p in urlparse(url).path.split("/") if p]
            if parts:
                return f"https://player.vimeo.com/video/{parts[-1]}"
        except Exception:
            return None
        return None
    if provider == "direct":
        return url
    return None


def build_thumbnail_url(url: str, provider: VideoProvider | None = None) -> Optional[str]:
    provider = provider or detect_provider(url)
    if provider == "youtube":
        vid = youtube_video_id(url)
        return f"https://img.youtube.com/vi/{vid}/hqdefault.jpg" if vid else None
    return None


def is_http_url(value: Optional[str]) -> bool:
    if not value:
        return False
    try:
        parsed = urlparse(value.strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def humanize_video_title(title: Optional[str]) -> str:
    """Turn index slugs like Groundnut_Pest_Control into plain labels."""
    if not title:
        return "Video"
    cleaned = title.strip()
    cleaned = re.sub(r"\.(mp4|webm|mov|mkv|avi)$", "", cleaned, flags=re.I)
    # Snake/kebab slugs → words
    if "_" in cleaned or (cleaned.count("-") >= 1 and " " not in cleaned):
        cleaned = re.sub(r"[_\-]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # Title-case when it still looks like a machine slug (no lowercase prose)
    if cleaned and not re.search(r"[a-z].*[a-z]", cleaned):
        cleaned = cleaned.title()
    elif cleaned and "_" in (title or ""):
        cleaned = cleaned.title()
    return cleaned or "Video"


def video_resource_from_hit(
    *,
    doc_id: str,
    title: str,
    url: str,
    description: Optional[str] = None,
    source: Optional[str] = None,
) -> VideoResource:
    provider = detect_provider(url)
    # Never expose internal slug/doc names as "source" on the payload
    clean_source = source if is_http_url(source) else None
    return VideoResource(
        id=doc_id or url,
        title=humanize_video_title(title),
        url=url,
        provider=provider,
        embed_url=build_embed_url(url, provider),
        thumbnail_url=build_thumbnail_url(url, provider),
        description=_short_description(description),
        source=clean_source,
    )


def _short_description(text: Optional[str], max_len: int = 240) -> Optional[str]:
    if not text:
        return None
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return None
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"


def dedupe_videos(videos: list[VideoResource]) -> list[VideoResource]:
    seen: set[str] = set()
    out: list[VideoResource] = []
    for video in videos:
        key = video.url or video.id
        if key in seen:
            continue
        seen.add(key)
        out.append(video)
    return out
