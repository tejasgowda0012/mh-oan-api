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
    
    def _logged_in_identity_string(self) -> Optional[str]:
        """Tell the agent which farmer identity is already available from the login token."""
        if self.farmer_id:
            return f"**Logged-in farmer ID (from token):** {self.farmer_id}"
        if self.unique_id:
            return f"**Logged-in registration number (from token):** {self.unique_id}"
        return None

    def _agristack_availability_string(self):
        """Whether the farmer is logged in with Agristack-linked identity."""
        if self.farmer_id or self.unique_id:
            return "**Logged-in farmer (Agristack-linked):** ✅"
        else:
            return "**Logged-in farmer (Agristack-linked):** ❌"

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
            self._agristack_availability_string(),
        ]
        return "\n".join([x for x in strings if x])

    def get_user_message(self):
        """Get the user message for the agrinet agent."""
        strings = [
            self._query_string(),
            self._language_string(),
            self._moderation_string(),
            self._logged_in_identity_string(),
            self._saved_farmer_context_string(),
            self._agristack_availability_string(),
        ]
        return "\n".join([x for x in strings if x])
