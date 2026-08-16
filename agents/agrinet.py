from pydantic_ai import Agent, RunContext
from helpers.utils import get_prompt, get_today_date_str, get_crop_season
from agents.models import AGRINET_MODEL
from agents.tools import TOOLS
from pydantic_ai.settings import ModelSettings
from agents.deps import FarmerContext
from dotenv import load_dotenv
load_dotenv()

_AGRINET_MODEL_SETTINGS = ModelSettings(
    max_tokens=32768,
    parallel_tool_calls=True,
    request_limit=10,
)

agrinet_agent = Agent(
    model=AGRINET_MODEL,
    name="Vistaar Agent",
    instrument=False,
    output_type=str,
    deps_type=FarmerContext,
    retries=3,
    tools=TOOLS,
    end_strategy="exhaustive",
    model_settings=_AGRINET_MODEL_SETTINGS,
)

def build_agrinet_system_prompt(lang_code: str | None) -> str:
    """The agent's full system prompt for one language.

    Shared by the `@system_prompt` hook (used by `/chat`, which passes a
    `user_prompt`) and by `app/services/agui.py`, which must pass it as run
    `instructions` instead: the AG-UI adapter supplies the user turn inside
    `message_history` rather than as a `user_prompt`, so pydantic-ai never
    builds the new request that would trigger the hook.
    """
    prompt_name = f"agrinet_system_{lang_code or 'en'}"
    return get_prompt(prompt_name, context={
        "today_date": get_today_date_str(),
        "crop_season": get_crop_season(),
    })


@agrinet_agent.system_prompt(dynamic=True)
def get_agrinet_system_prompt(ctx: RunContext[FarmerContext]):
    return build_agrinet_system_prompt(ctx.deps.lang_code)