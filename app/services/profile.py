"""
Structured farmer profile — separate Qdrant collection (not mem0).

One point per hashed user_id; 1-d placeholder vector; payload = FarmerProfile JSON.
Aligned with voice-oan profile store; chat promotes explicit farmer-stated facts only.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_PROFILE_COLLECTION = os.getenv("QDRANT_PROFILE_COLLECTION", "vistaar_farmer_profiles")
_PROFILE_ID_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")
_PLACEHOLDER_VECTOR = [0.0]


class Crop(BaseModel):
    name: str
    variety: Optional[str] = None
    area_acres: Optional[float] = None
    sowing_date: Optional[str] = None
    season: Optional[str] = None


class OpenThread(BaseModel):
    topic: str
    advice_given: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "open"


class FarmerProfile(BaseModel):
    user_id: str
    name: Optional[str] = None
    village: Optional[str] = None
    district: Optional[str] = None
    state: Optional[str] = None
    preferred_mandi: Optional[str] = None
    crops: list[Crop] = Field(default_factory=list)
    land_area_acres: Optional[float] = None
    irrigation: Optional[str] = None
    soil_type: Optional[str] = None
    livestock: list[str] = Field(default_factory=list)
    language: Optional[str] = None
    preferred_call_time: Optional[str] = None
    schemes: list[str] = Field(default_factory=list)
    open_threads: list[OpenThread] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def is_empty(self) -> bool:
        return not any(
            [
                self.name,
                self.village,
                self.district,
                self.state,
                self.preferred_mandi,
                self.crops,
                self.land_area_acres,
                self.irrigation,
                self.soil_type,
                self.livestock,
                self.language,
                self.preferred_call_time,
                self.schemes,
                self.open_threads,
                self.notes,
            ]
        )


def render_snapshot(profile: FarmerProfile) -> Optional[str]:
    if profile.is_empty():
        return None
    lines = ["Structured farmer profile (authoritative for location, crops, farm setup):"]
    loc = ", ".join(x for x in [profile.village, profile.district, profile.state] if x)
    if profile.name:
        lines.append(f"• Name: {profile.name}")
    if loc:
        lines.append(f"• Location: {loc}")
    if profile.preferred_mandi:
        lines.append(f"• Preferred mandi: {profile.preferred_mandi}")
    for crop in profile.crops:
        parts = [crop.name]
        if crop.area_acres:
            parts.append(f"({crop.area_acres} acre)")
        lines.append(f"• Crop: {' '.join(parts)}")
    if profile.land_area_acres:
        lines.append(f"• Total land: {profile.land_area_acres} acres")
    if profile.irrigation:
        lines.append(f"• Irrigation: {profile.irrigation}")
    if profile.soil_type:
        lines.append(f"• Soil: {profile.soil_type}")
    if profile.livestock:
        lines.append(f"• Livestock: {', '.join(profile.livestock)}")
    if profile.language:
        lines.append(f"• Language: {profile.language}")
    for note in profile.notes[:3]:
        lines.append(f"• Note: {note}")
    open_threads = [t for t in profile.open_threads if t.status == "open"]
    if open_threads:
        lines.append("Open follow-ups:")
        for t in open_threads[:3]:
            lines.append(f"• {t.topic}")
    return "\n".join(lines)


_SCALAR_FIELDS = [
    "name",
    "village",
    "district",
    "state",
    "preferred_mandi",
    "land_area_acres",
    "irrigation",
    "soil_type",
    "language",
    "preferred_call_time",
]
_LIST_FIELDS = ["livestock", "schemes", "notes"]


def merge_profile(existing: FarmerProfile, partial: dict) -> FarmerProfile:
    data = existing.model_dump()
    for field in _SCALAR_FIELDS:
        val = partial.get(field)
        if val not in (None, "", []):
            data[field] = val
    for field in _LIST_FIELDS:
        incoming = partial.get(field) or []
        if isinstance(incoming, str):
            incoming = [incoming]
        seen = {str(x).strip().lower() for x in data.get(field, [])}
        for item in incoming:
            if item and str(item).strip().lower() not in seen:
                data[field].append(item)
                seen.add(str(item).strip().lower())
    incoming_crops = partial.get("crops") or []
    by_name = {c["name"].strip().lower(): c for c in data.get("crops", []) if c.get("name")}
    for raw in incoming_crops:
        crop = raw if isinstance(raw, dict) else {"name": str(raw)}
        # Models sometimes pass "soyabean, maize" as one name — split into separate crops.
        names = [n.strip() for n in str(crop.get("name") or "").split(",") if n.strip()]
        for name in names:
            keyed = name.lower()
            entry = {**crop, "name": name}
            if keyed in by_name:
                for k, v in entry.items():
                    if v not in (None, "", []):
                        by_name[keyed][k] = v
            else:
                by_name[keyed] = entry
    data["crops"] = list(by_name.values())
    incoming_threads = partial.get("open_threads") or []
    open_topics = {t["topic"].strip().lower() for t in data.get("open_threads", []) if t.get("topic")}
    for raw in incoming_threads:
        thread = raw if isinstance(raw, dict) else {"topic": str(raw)}
        topic = (thread.get("topic") or "").strip().lower()
        if topic and topic not in open_topics:
            data["open_threads"].append(OpenThread(**thread).model_dump())
            open_topics.add(topic)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    return FarmerProfile(**data)


def remove_profile_value(
    existing: FarmerProfile, field: str, value: str
) -> tuple[FarmerProfile, bool]:
    """Remove one matching value without disturbing unrelated profile data."""
    data = existing.model_dump()
    expected = str(value or "").strip().casefold()
    if not expected:
        return existing, False

    changed = False
    if field == "crops":
        before = data.get("crops", [])
        after = [
            crop
            for crop in before
            if str(crop.get("name") or "").strip().casefold() != expected
        ]
        changed = len(after) != len(before)
        data["crops"] = after
    elif field in _LIST_FIELDS:
        before = data.get(field, [])
        after = [item for item in before if str(item).strip().casefold() != expected]
        changed = len(after) != len(before)
        data[field] = after
    elif field in _SCALAR_FIELDS:
        current = data.get(field)
        current_text = str(current).strip().casefold() if current is not None else ""
        expected_matches = current_text == expected
        if field == "land_area_acres" and current is not None:
            try:
                numeric = float("".join(c for c in str(value) if c.isdigit() or c == "."))
                expected_matches = float(current) == numeric
            except (TypeError, ValueError):
                expected_matches = False
        if expected_matches:
            data[field] = None
            changed = True
    else:
        return existing, False

    if not changed:
        return existing, False
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    return FarmerProfile(**data), True


class ProfileStore:
    _INIT_RETRY_SECONDS = 60.0

    def __init__(self) -> None:
        self._client = None
        self._last_init_failure: Optional[float] = None
        # apply_update / apply_removal / apply_gap_fill are read-modify-write;
        # concurrent tool calls from one agent turn must serialize per farmer or
        # the last upsert clobbers the other's field. Values are (lock, in-flight
        # refcount); entries are evicted on release at refcount zero so the dict
        # cannot grow for the process lifetime. The refcount (not locked()/waiters)
        # is what makes eviction safe: a task that fetched the lock but has not
        # entered it yet still holds a count.
        self._update_locks: dict[str, tuple[asyncio.Lock, int]] = {}
        self._update_locks_guard = asyncio.Lock()

    async def _user_lock(self, user_id: str) -> asyncio.Lock:
        async with self._update_locks_guard:
            entry = self._update_locks.get(user_id)
            if entry is None:
                entry = (asyncio.Lock(), 0)
            self._update_locks[user_id] = (entry[0], entry[1] + 1)
            return entry[0]

    async def _release_user_lock(self, user_id: str, lock: asyncio.Lock) -> None:
        async with self._update_locks_guard:
            entry = self._update_locks.get(user_id)
            if entry is None or entry[0] is not lock:
                return
            if entry[1] <= 1:
                self._update_locks.pop(user_id, None)
            else:
                self._update_locks[user_id] = (lock, entry[1] - 1)

    def _qdrant_host_port(self) -> tuple[str, int]:
        return (
            os.getenv("QDRANT_HOST", "localhost"),
            int(os.getenv("QDRANT_PORT", "6333")),
        )

    def _get_client(self):
        if self._client is not None:
            return self._client
        if (
            self._last_init_failure is not None
            and time.monotonic() - self._last_init_failure < self._INIT_RETRY_SECONDS
        ):
            return None
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams

            host, port = self._qdrant_host_port()
            client = QdrantClient(host=host, port=port)
            existing = {c.name for c in client.get_collections().collections}
            if _PROFILE_COLLECTION not in existing:
                client.create_collection(
                    collection_name=_PROFILE_COLLECTION,
                    vectors_config=VectorParams(size=1, distance=Distance.DOT),
                )
                logger.info("ProfileStore: created collection %s", _PROFILE_COLLECTION)
            self._client = client
            self._last_init_failure = None
        except Exception:
            self._last_init_failure = time.monotonic()
            logger.warning("ProfileStore: init failed", exc_info=True)
        return self._client

    @staticmethod
    def _point_id(user_id: str) -> str:
        return str(uuid.uuid5(_PROFILE_ID_NAMESPACE, user_id))

    async def get(self, user_id: str) -> Optional[FarmerProfile]:
        if not user_id:
            return None
        client = self._get_client()
        if not client:
            return None
        try:
            records = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: client.retrieve(
                    collection_name=_PROFILE_COLLECTION,
                    ids=[self._point_id(user_id)],
                    with_payload=True,
                ),
            )
            if not records:
                return None
            return FarmerProfile(**records[0].payload)
        except Exception:
            logger.warning("profile.get failed for %s", user_id, exc_info=True)
            return None

    async def save(self, profile: FarmerProfile) -> None:
        client = self._get_client()
        if not client:
            return
        try:
            from qdrant_client.models import PointStruct

            point = PointStruct(
                id=self._point_id(profile.user_id),
                vector=_PLACEHOLDER_VECTOR,
                payload=profile.model_dump(),
            )
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: client.upsert(collection_name=_PROFILE_COLLECTION, points=[point]),
            )
        except Exception:
            logger.warning("profile.save failed for %s", profile.user_id, exc_info=True)

    async def apply_update(self, user_id: str, partial: dict) -> FarmerProfile:
        lock = await self._user_lock(user_id)
        try:
            async with lock:
                existing = await self.get(user_id) or FarmerProfile(user_id=user_id)
                merged = merge_profile(existing, partial)
                await self.save(merged)
                return merged
        finally:
            await self._release_user_lock(user_id, lock)

    async def apply_removal(self, user_id: str, field: str, value: str) -> bool:
        lock = await self._user_lock(user_id)
        try:
            async with lock:
                existing = await self.get(user_id)
                if not existing:
                    return False
                updated, changed = remove_profile_value(existing, field, value)
                if changed:
                    await self.save(updated)
                return changed
        finally:
            await self._release_user_lock(user_id, lock)

    async def apply_gap_fill(self, user_id: str, partial: dict) -> FarmerProfile:
        """Fill only scalar fields that are empty at write time (registry gap-fill).

        The emptiness check runs under the per-user lock so a concurrent
        farmer-stated update always wins over registry data.
        """
        lock = await self._user_lock(user_id)
        try:
            async with lock:
                existing = await self.get(user_id) or FarmerProfile(user_id=user_id)
                gaps = {
                    field: value
                    for field, value in partial.items()
                    if value not in (None, "", []) and not getattr(existing, field, None)
                }
                if not gaps:
                    return existing
                merged = merge_profile(existing, gaps)
                await self.save(merged)
                return merged
        finally:
            await self._release_user_lock(user_id, lock)

    async def get_snapshot(self, user_id: str) -> Optional[str]:
        profile = await self.get(user_id)
        if not profile:
            return None
        return render_snapshot(profile)

    async def delete(self, user_id: str) -> bool:
        client = self._get_client()
        if not client or not user_id:
            return False
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: client.delete(
                    collection_name=_PROFILE_COLLECTION,
                    points_selector=[self._point_id(user_id)],
                ),
            )
            return True
        except Exception:
            logger.warning("profile.delete failed for %s", user_id, exc_info=True)
            return False


profile_store = ProfileStore()
