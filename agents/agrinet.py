import os
from pydantic_ai import Agent, RunContext
from pydantic_ai.mcp import MCPServerHTTP
from helpers.utils import get_prompt, get_today_date_str
from agents.models import LLM_MODEL
from agents.tools import TOOLS
from pydantic_ai.settings import ModelSettings
from agents.deps import FarmerContext

_mcp_url = os.getenv('BHARAT_VISTAAR_MCP_URL', 'http://host.docker.internal:3000/sse')
_mcp_api_key = os.getenv('BHARAT_VISTAAR_MCP_API_KEY', '')

bharat_vistaar_mcp = MCPServerHTTP(
    url=_mcp_url,
    headers={'x-api-key': _mcp_api_key} if _mcp_api_key else {},
    sse_read_timeout=60 * 60 * 24,  # 24 hours — keep SSE connection alive
)

agrinet_agent = Agent(
    model=LLM_MODEL,
    name="Vistaar Agent",
    instrument=True,
    output_type=str,
    deps_type=FarmerContext,
    retries=3,
    tools=TOOLS,
    mcp_servers=[bharat_vistaar_mcp],
    end_strategy='exhaustive',
    model_settings=ModelSettings(
        max_tokens=8192,
        parallel_tool_calls=True,
        request_limit=50,
   )
)

@agrinet_agent.system_prompt(dynamic=True)
def get_agrinet_system_prompt(ctx: RunContext):
    return get_prompt('agrinet_system', context={'today_date': get_today_date_str()})