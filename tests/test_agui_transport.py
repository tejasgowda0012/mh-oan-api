"""Regression tests for the AG-UI transport (`POST /api/agui`).

The bug these guard against: the AG-UI adapter supplies the user turn inside
`message_history` rather than as a `user_prompt`, so pydantic-ai never builds
the request that triggers `@agrinet_agent.system_prompt`. The model then ran
with no persona and no tool-usage rules and answered generic questions from
memory instead of calling `search_documents` / `search_videos`.

`app/services/agui.py` therefore passes the prompt as run `instructions`. These
tests assert the model actually receives it, and that it sees the agent's tools.
"""

import json
import os
import unittest

os.environ.setdefault("ENVIRONMENT", "development")

from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import main
from agents.agrinet import agrinet_agent, build_agrinet_system_prompt
from agents.moderation import QueryModerationResult
from app.auth.jwt_auth import get_current_user


def _agui_body(query: str = "how to login the mahavistaar mobile app", lang: str = "en") -> dict:
    return {
        "threadId": "test-thread",
        "runId": "test-run",
        "state": {
            "session_id": "test-thread",
            "source_lang": lang,
            "target_lang": lang,
            "user_id": "u",
        },
        "messages": [{"id": "m1", "role": "user", "content": query}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }


class AgUiModelInputTests(unittest.TestCase):
    """Capture exactly what the model is handed on one AG-UI run."""

    def setUp(self):
        import app.services.agui as agui_mod

        self.captured: dict = {}
        self._agui_mod = agui_mod
        self._originals = {
            "_run_moderation": agui_mod._run_moderation,
            "_get_message_history": agui_mod._get_message_history,
            "update_message_history": agui_mod.update_message_history,
        }

        async def fake_moderation(**kwargs):
            return QueryModerationResult(
                category="valid_agricultural", action="Proceed with the query"
            )

        async def fake_history(_session_id):
            return []

        async def fake_update(_session_id, _messages):
            return None

        agui_mod._run_moderation = fake_moderation
        agui_mod._get_message_history = fake_history
        agui_mod.update_message_history = fake_update

        main.app.dependency_overrides[get_current_user] = lambda: {"sub": "u"}

    def tearDown(self):
        for name, original in self._originals.items():
            setattr(self._agui_mod, name, original)
        main.app.dependency_overrides.pop(get_current_user, None)

    def _run(self, body: dict) -> dict:
        captured = self.captured

        def record(messages: list[ModelMessage], info: AgentInfo):
            if "tools" not in captured:
                captured["tools"] = {t.name for t in info.function_tools}
                captured["instructions"] = next(
                    (m.instructions for m in messages if getattr(m, "instructions", None)),
                    None,
                )
                captured["system_parts"] = [
                    getattr(p, "content", "")
                    for m in messages
                    for p in m.parts
                    if getattr(p, "part_kind", "") == "system-prompt"
                ]
            return ModelResponse(parts=[TextPart("stub")])

        async def record_stream(messages, info):
            record(messages, info)
            yield "stub"

        with agrinet_agent.override(
            model=FunctionModel(record, stream_function=record_stream)
        ):
            with TestClient(main.app) as client:
                response = client.post(
                    "/api/agui", json=body, headers={"Accept": "text/event-stream"}
                )
        self.assertEqual(200, response.status_code)
        return captured

    def test_model_receives_the_full_system_prompt(self):
        captured = self._run(_agui_body())
        prompt = captured["instructions"] or "\n".join(captured["system_parts"])
        self.assertTrue(prompt, "AG-UI run reached the model with NO system prompt")
        # Not just any text — the real agent prompt, with its tool rules intact.
        self.assertIn("MahaVistaar", prompt)
        self.assertIn("search_documents", prompt)
        self.assertIn("present_video", prompt)

    def test_prompt_matches_the_language_the_client_asked_for(self):
        captured = self._run(_agui_body(query="कापसातील कीड नियंत्रण", lang="mr"))
        prompt = captured["instructions"] or "\n".join(captured["system_parts"])
        self.assertEqual(build_agrinet_system_prompt("mr"), prompt)

    def test_model_can_see_the_search_and_present_tools(self):
        captured = self._run(_agui_body())
        for tool in ("search_terms", "search_documents", "search_videos",
                     "present_video", "present_suggestions"):
            self.assertIn(tool, captured["tools"])


class AgUiStateHandlingTests(unittest.TestCase):
    def test_state_is_consumed_not_forwarded_to_deps(self):
        """FarmerContext is not a StateHandler dataclass; forwarding state only warns."""
        from pydantic_ai.ui.ag_ui import AGUIAdapter

        run_input = AGUIAdapter.build_run_input(json.dumps(_agui_body()).encode())
        self.assertIsInstance(run_input.state, dict)
        self.assertEqual("test-thread", run_input.state["session_id"])


if __name__ == "__main__":
    unittest.main()
