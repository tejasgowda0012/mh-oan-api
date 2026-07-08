"""
Farmer long-term memory via mem0 + Qdrant for chat.

Chat uses in-conversation tools to search and save memories (no post-session job).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)


def _inference_openai_base_url() -> Optional[str]:
    url = os.getenv("INFERENCE_ENDPOINT_URL") or os.getenv("OPENAI_BASE_URL")
    if not url:
        return None
    return url.rstrip("/")


def _build_mem0_config() -> dict:
    from app.config import settings

    embed_model = os.getenv("MEMORY_EMBED_MODEL", "text-embedding-3-small")
    embed_dims = int(os.getenv("MEMORY_EMBED_DIMS", "1536"))

    openai_api_key = settings.openai_api_key or os.getenv("OPENAI_API_KEY")
    azure_api_key = os.getenv("AZURE_OPENAI_API_KEY")
    embed_api_key = openai_api_key or azure_api_key

    embed_base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE")
    if (
        not embed_base_url
        and not openai_api_key
        and azure_api_key
        and os.getenv("AZURE_OPENAI_ENDPOINT")
    ):
        deployment = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", embed_model)
        embed_base_url = (
            f"{os.getenv('AZURE_OPENAI_ENDPOINT').rstrip('/')}/openai/deployments/{deployment}"
        )

    embedder_config: dict = {
        "model": embed_model,
        "embedding_dims": embed_dims,
        "api_key": embed_api_key,
    }
    if embed_base_url:
        embedder_config["openai_base_url"] = embed_base_url

    config: dict = {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": os.getenv(
                    "QDRANT_COLLECTION", "vistaar_chat_farmer_memories"
                ),
                "host": os.getenv("QDRANT_HOST", "localhost"),
                "port": int(os.getenv("QDRANT_PORT", "6333")),
                "embedding_model_dims": embed_dims,
            },
        },
        "embedder": {
            "provider": "openai",
            "config": embedder_config,
        },
    }

    inference_url = _inference_openai_base_url()
    extraction_model = os.getenv("LLM_MODEL_NAME") or "gpt-4.1"
    if inference_url:
        config["llm"] = {
            "provider": "openai",
            "config": {
                "model": extraction_model,
                "openai_base_url": inference_url,
                "api_key": os.getenv("INFERENCE_API_KEY") or os.getenv("OPENAI_API_KEY") or "not-required",
            },
        }

    return config


def _patch_mem0_disable_thinking() -> None:
    try:
        from mem0.llms.openai import OpenAILLM  # type: ignore
    except Exception:
        return

    if getattr(OpenAILLM, "_thinking_patched", False):
        return

    _orig_generate = OpenAILLM.generate_response

    def _generate_no_think(self, messages, *args, **kwargs):
        extra_body = dict(kwargs.pop("extra_body", {}) or {})
        extra_body.setdefault("chat_template_kwargs", {"enable_thinking": False})
        kwargs["extra_body"] = extra_body
        return _orig_generate(self, messages, *args, **kwargs)

    OpenAILLM.generate_response = _generate_no_think
    OpenAILLM._thinking_patched = True


class MemoryService:
    _INIT_RETRY_SECONDS = 60.0

    def __init__(self) -> None:
        self._client = None
        self._last_init_failure: Optional[float] = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        if (
            self._last_init_failure is not None
            and time.monotonic() - self._last_init_failure < self._INIT_RETRY_SECONDS
        ):
            return None
        try:
            from mem0 import Memory  # type: ignore

            _patch_mem0_disable_thinking()
            self._client = Memory.from_config(_build_mem0_config())
            self._last_init_failure = None
            logger.info("MemoryService: mem0 client initialized")
        except Exception:
            self._last_init_failure = time.monotonic()
            logger.warning(
                "MemoryService: initialization failed — memory disabled, retrying in %ss",
                self._INIT_RETRY_SECONDS,
                exc_info=True,
            )
        return self._client

    async def search(
        self,
        query: str,
        user_id: str,
        top_k: int = 5,
        threshold: float = 0.3,
    ) -> str:
        client = self._get_client()
        if not client or not user_id:
            return ""
        try:
            results = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: client.search(query, filters={"user_id": user_id}, limit=top_k),
            )
            memories = results if isinstance(results, list) else results.get("results", [])
            if not memories:
                return "No relevant past memories found."
            filtered = [m for m in memories if m.get("score", 1.0) >= threshold]
            if not filtered:
                return "No relevant past memories found."
            return "\n".join(f"- {m['memory']}" for m in filtered)
        except Exception:
            logger.warning("memory.search failed for user %s", user_id, exc_info=True)
            return ""

    async def get_all(self, user_id: str) -> list[dict]:
        client = self._get_client()
        if not client or not user_id:
            return []
        try:
            results = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: client.get_all(filters={"user_id": user_id}, limit=200),
            )
            memories = results if isinstance(results, list) else results.get("results", [])
            return [
                {
                    "memory": m.get("memory"),
                    "created_at": m.get("created_at"),
                    "updated_at": m.get("updated_at"),
                }
                for m in memories
            ]
        except Exception:
            logger.warning("get_all failed for user %s", user_id, exc_info=True)
            return []

    async def add_fact(
        self,
        user_id: str,
        fact: str,
        *,
        source: str = "chat_tool",
        infer: bool = True,
    ) -> str:
        """Store a farmer memory. With infer=True, mem0 extracts atomic facts from the text."""
        client = self._get_client()
        if not client or not user_id:
            return "Memory storage is not available for this session."
        text = (fact or "").strip()
        if not text:
            return "Nothing to save — memory text was empty."

        def _add():
            try:
                return client.add(
                    [{"role": "user", "content": text}],
                    user_id=user_id,
                    metadata={"source": source, "channel": "chat"},
                    infer=infer,
                )
            except TypeError:
                return client.add(
                    [{"role": "user", "content": text}],
                    user_id=user_id,
                    metadata={"source": source, "channel": "chat"},
                )

        try:
            await asyncio.get_event_loop().run_in_executor(None, _add)
            logger.info("add_fact user=%s len=%d infer=%s", user_id, len(text), infer)
            return "Saved to farmer memory."
        except Exception:
            logger.error("add_fact failed for user %s", user_id, exc_info=True)
            return "Could not save memory right now. Please try again later."

    async def delete_all(self, user_id: str) -> int:
        client = self._get_client()
        if not client or not user_id:
            return 0
        try:
            before = await self.get_all(user_id)
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: client.delete_all(user_id=user_id),
            )
            return len(before)
        except Exception:
            logger.warning("delete_all failed for user %s", user_id, exc_info=True)
            return 0


memory_service = MemoryService()