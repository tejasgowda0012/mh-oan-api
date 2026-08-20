"""
Tasks for creating conversation suggestions.
"""

import os

from langfuse import get_client, propagate_attributes

from helpers import langfuse_helper  # noqa: F401 — initialises Langfuse env vars
from helpers.langfuse_tracing import lf_set_trace_io
from helpers.utils import get_logger
from app.utils import _get_message_history, trim_history, format_message_pairs, set_cache
from agents.suggestions import suggestions_agent
from langcodes import Language
from agents.deps import FarmerContext

logger = get_logger(__name__)

SUGGESTIONS_CACHE_TTL = 60 * 30  # 30 minutes

SUGGESTIONS_TRACE_NAME = (
    os.getenv("LANGFUSE_SUGGESTIONS_TRACE_NAME")
    or os.getenv("LANGFUSE_SUGGESTIONS_TRACE_ROOT_NAME")
    or "suggestions-agent"
)
SUGGESTIONS_CHAIN_SPAN_NAME = "chain.suggestions"

_MODEL_NAME = (
    os.getenv("LLM_AGRINET_MODEL_NAME")
    or os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
    or os.getenv("LLM_MODEL_NAME")
)


async def create_suggestions(
    session_id: str, target_lang: str = "mr", user_id: str | None = None, query: str | None = None
):
    """
    Create and save suggestions for a session
    """
    logger.info(f"Getting suggestions for session {session_id}")

    try:
        raw_history = await _get_message_history(session_id)

        history = trim_history(raw_history,
                          30_000,
                          include_tool_calls=False,
                          include_system_prompts=False
                          )
        if not history:
            logger.info(f"No conversation history for session {session_id}, skipping suggestions")
            await set_cache(f"suggestions_{session_id}_{target_lang}", [], ttl=SUGGESTIONS_CACHE_TTL)
            return []

        message_pairs = "\n\n".join(format_message_pairs(history, 1))
        target_lang_name = Language.get(target_lang).display_name(target_lang)
        message = (
            f"**Conversation (use only the latest farmer question and assistant answer)**\n\n"
            f"{message_pairs}\n\n"
            f"**Important:** Ignore all earlier questions in this session. Base the suggestion only on the latest farmer question and the latest assistant answer. "
            f"Write it as a short question a farmer would ask the system, not a question the system would ask the farmer.**\n\n"
            f"**Suggest exactly 1 NEW follow-up tap-chip question in {target_lang_name} — short, casual, 4–7 words, concrete farm action. No I/you/your, no 'in your area', no vague 'safe to plant' questions. Something the farmer has NOT already asked.**"
        )

        lf_env = os.getenv("LANGFUSE_TRACING_ENVIRONMENT", "development")
        trace_tags = [
            f"env:{lf_env}",
            "task:suggestions",
        ]
        trace_metadata: dict[str, str] = {
            "target_lang": target_lang,
            "environment": lf_env,
            "task": "suggestions",
        }

        propagate_kw: dict = {
            "session_id": session_id,
            "metadata": trace_metadata,
            "tags": trace_tags,
            "trace_name": SUGGESTIONS_TRACE_NAME,
        }
        if user_id:
            propagate_kw["user_id"] = user_id

        lf_client = get_client()
        with propagate_attributes(**propagate_kw):
            with lf_client.start_as_current_observation(
                as_type="chain",
                name=SUGGESTIONS_CHAIN_SPAN_NAME,
            ):
                lf_set_trace_io(input=message if len(message) <= 2000 else f"{message[:1997]}...")
                deps = FarmerContext(query=message, lang_code=target_lang, display_lang=target_lang)
                agent_run = await suggestions_agent.run(message, deps=deps)
                suggestions = [agent_run.output] if agent_run.output else []
                lf_set_trace_io(output=suggestions)

        logger.info(f"Suggestions: {suggestions}")
        
        # Store suggestions in cache
        result = await set_cache(f"suggestions_{session_id}_{target_lang}", suggestions, ttl=SUGGESTIONS_CACHE_TTL)
        logger.info(f"Suggestions saved for session {session_id}: {result}")
        
        return suggestions
        
    except Exception as e:
        logger.error(f"Error creating suggestions: {str(e)}")
        await set_cache(f"suggestions_{session_id}_{target_lang}", [], ttl=SUGGESTIONS_CACHE_TTL)
        return []