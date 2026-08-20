from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator
from langcodes import Language


class FarmerContext(BaseModel):
    """Context for the farmer agent.
    
    Args:
        query (str): The user's question.
        lang_code (str): The language code of the user's question.
        session_id (str): Conversation session id (for Bharat Vistaar cross-network).
        moderation_str (Optional[str]): The moderation result of the user's question.


    Example:
        **User:** "What is the weather in Mumbai?"
        **Selected Language:** Marathi
        **Moderation Result:** "This is a valid agricultural question."
    """
    query: str = Field(description="The user's question.")
    lang_code: str = Field(description="The language code of the user's question.", default='mr')
    display_lang: str = Field(default="", description="The actual target language to display to the user.")
    session_id: str = Field(default="", description="Conversation session id.")
    moderation_str: Optional[str] = Field(default=None, description="The moderation result of the user's question.")
    farmer_id: Optional[str] = Field(default=None, description="The farmer ID of the user.")
    unique_id: Optional[str] = Field(
        default=None,
        description="Agristack registration number when farmer_id is not in the JWT.",
    )
    memory_user_id: Optional[str] = Field(
        default=None,
        description="Hashed phone id for structured profile + mem0 (None = memory tools and preload disabled).",
    )
    saved_farmer_context: Optional[str] = Field(
        default=None,
        description="Structured profile snapshot pre-loaded on the first conversation turn.",
    )
    user_info: Dict[str, Any] = Field(default_factory=dict, description="Authenticated user metadata.")
    # Populated by search_videos for AG-UI inline players (not part of the LLM prompt).
    related_videos: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Structured video resources collected during this turn for AG-UI playback.",
    )
    # Populated by search_documents for AG-UI grounding-validation cards (not part of the LLM prompt).
    related_documents: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Structured document resources retrieved this turn, for AG-UI grounding validation.",
    )
    # Filled by search_videos, read by present_video. Keyed by the short label
    # ("v1", "v2", …) shown to the model, so presenting a video is a lookup
    # rather than a second Marqo round trip.
    video_candidates: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Video payloads returned by search_videos this turn, keyed by short id.",
    )
    # Populated by present_suggestions for AG-UI follow-up chips.
    suggested_questions: List[str] = Field(
        default_factory=list,
        description="Follow-up questions the agent chose to offer this turn.",
    )

    def remember_video_candidates(self, videos: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Register search_videos results as presentable candidates.

        Returns the newly assigned {short_id: payload} mapping so the caller can
        render those ids for the model. Ids continue across repeated searches in
        one turn, and a video already registered keeps its original id.
        """
        assigned: Dict[str, Dict[str, Any]] = {}
        by_url = {
            (v.get("url") or v.get("id")): key
            for key, v in self.video_candidates.items()
        }
        for video in videos:
            url = video.get("url") or video.get("id")
            existing = by_url.get(url) if url else None
            if existing:
                assigned[existing] = self.video_candidates[existing]
                continue
            key = f"v{len(self.video_candidates) + 1}"
            self.video_candidates[key] = video
            if url:
                by_url[url] = key
            assigned[key] = video
        return assigned

    def find_video_candidate(self, video_id: str) -> Optional[Dict[str, Any]]:
        """Resolve a short id ("v1"), a raw resource id, or a URL to its payload."""
        if not video_id:
            return None
        wanted = str(video_id).strip()
        if wanted in self.video_candidates:
            return self.video_candidates[wanted]
        lowered = wanted.lower()
        for payload in self.video_candidates.values():
            if lowered in {
                str(payload.get("id", "")).lower(),
                str(payload.get("url", "")).lower(),
            }:
                return payload
        return None

    def add_suggested_questions(self, questions: List[str]) -> List[str]:
        """Append unique follow-up questions; returns the ones actually added."""
        seen = {q.strip().lower() for q in self.suggested_questions}
        added: List[str] = []
        for question in questions:
            cleaned = (question or "").strip()
            if not cleaned or cleaned.lower() in seen:
                continue
            seen.add(cleaned.lower())
            self.suggested_questions.append(cleaned)
            added.append(cleaned)
        return added

    def add_related_videos(self, videos: List[Dict[str, Any]]) -> None:
        """Append unique structured videos for AG-UI clients."""
        if not videos:
            return
        seen = {v.get("url") or v.get("id") for v in self.related_videos}
        for video in videos:
            key = video.get("url") or video.get("id")
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            self.related_videos.append(video)

    def add_related_documents(self, documents: List[Dict[str, Any]]) -> None:
        """Append structured document cards for AG-UI validation clients.

        A document already added this turn (same id/title, from an earlier
        search_documents call) gets its new chunks merged in — deduped by chunk
        id — instead of being skipped, so a second search that resurfaces the
        same document with different chunks doesn't lose those chunks.
        """
        if not documents:
            return
        by_key = {
            (d.get("id") or d.get("title")): d
            for d in self.related_documents
            if d.get("id") or d.get("title")
        }
        for document in documents:
            key = document.get("id") or document.get("title")
            existing = by_key.get(key) if key else None
            if existing is None:
                self.related_documents.append(document)
                if key:
                    by_key[key] = document
                continue
            existing_chunk_ids = {c.get("id") for c in existing.get("chunks", [])}
            for chunk in document.get("chunks", []):
                if chunk.get("id") not in existing_chunk_ids:
                    existing.setdefault("chunks", []).append(chunk)
                    existing_chunk_ids.add(chunk.get("id"))
            new_score = document.get("score")
            if new_score is not None and (existing.get("score") is None or new_score > existing["score"]):
                existing["score"] = new_score

    @field_validator("farmer_id", "unique_id", "memory_user_id", mode="before")
    @classmethod
    def _coerce_optional_identifiers(cls, value):
        if value in (None, ""):
            return None if value is None else value
        return str(value)

    def update_moderation_str(self, moderation_str: str):
        """Update the moderation result of the user's question."""
        self.moderation_str = moderation_str

    def update_farmer_id(self, farmer_id: str):
        """Update the farmer ID of the user."""
        self.farmer_id = farmer_id

    def get_farmer_id(self) -> Optional[str]:
        """Get the farmer ID of the user."""
        return self.farmer_id

    def get_user_claim(self, *names: str) -> Optional[Any]:
        """Return the first available authenticated user value matching one of the provided names."""
        for name in names:
            value = self.user_info.get(name)
            if value not in (None, ""):
                return value
        return None
        
    def get_moderation_str(self) -> Optional[str]:
        """Get the moderation result of the user's question."""
        return self.moderation_str
    
    def _language_string(self):
        """Get the language string for the agrinet agent."""
        if self.lang_code:
            return f"**Selected Language:** {Language.get(self.lang_code).display_name()}"
        else:
            return None
    
    def _query_string(self):
        """Get the query string for the agrinet agent."""
        return "**User:** " + '"' + self.query + '"'

    def _moderation_string(self):
        """Get the moderation string for the agrinet agent."""
        if self.moderation_str:
            return self.moderation_str
        else:
            return None
    
    def _saved_farmer_context_string(self):
        if not self.saved_farmer_context or not str(self.saved_farmer_context).strip():
            return None
        return (
            "**Saved farmer profile (authoritative for saved location/crops/farm setup; "
            "do not re-ask for details already in this profile):**\n"
            + self.saved_farmer_context.strip()
        )

    def get_moderation_message(self):
        """User turn for moderation only — excludes preloaded profile/mem0 PII."""
        strings = [
            self._query_string(),
            self._language_string(),
        ]
        return "\n".join([x for x in strings if x])

    def get_user_message(self):
        """Get the user message for the agrinet agent.

        Carries no login/identity lines: gated tools (Agristack, MahaDBT, POCRA DBT)
        read farmer_id/unique_id from deps and report missing login themselves.
        """
        strings = [
            self._query_string(),
            self._language_string(),
            self._moderation_string(),
            self._saved_farmer_context_string(),
        ]
        return "\n".join([x for x in strings if x])
