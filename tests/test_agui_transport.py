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


class InternalEchoFilterTests(unittest.IsolatedAsyncioTestCase):
    """Internal tool output must never surface as an answer.

    Reproduces the observed failure: after present_video/present_suggestions
    returned, the model filled its forced extra turn with
    " FileNotFound: No videos found for `how to use the app`".
    """

    @staticmethod
    async def _feed(events):
        for event in events:
            yield event

    async def _run(self, events):
        from app.services.agui import _drop_internal_echo_messages

        return [e async for e in _drop_internal_echo_messages(self._feed(events))]

    @staticmethod
    def _message(message_id: str, text: str, chunk: int = 7):
        from ag_ui.core import (
            TextMessageContentEvent,
            TextMessageEndEvent,
            TextMessageStartEvent,
        )

        events = [TextMessageStartEvent(message_id=message_id, role="assistant")]
        events += [
            TextMessageContentEvent(message_id=message_id, delta=text[i:i + chunk])
            for i in range(0, len(text), chunk)
        ]
        events.append(TextMessageEndEvent(message_id=message_id))
        return events

    @staticmethod
    def _text_of(events) -> str:
        from ag_ui.core import TextMessageContentEvent

        return "".join(e.delta for e in events if isinstance(e, TextMessageContentEvent))

    async def test_drops_the_observed_filenotfound_echo(self):
        out = await self._run(
            self._message("m2", " FileNotFound: No videos found for `how to use the app`\n\nWould you like to know more?")
        )
        self.assertEqual("", self._text_of(out))
        self.assertEqual([], out)

    async def test_keeps_a_real_answer_verbatim(self):
        answer = (
            "MahaVISTAAR is Maharashtra's AI-powered agricultural advisory platform "
            "designed to help farmers with crop management, weather and market prices.\n\n"
            "**Source: MahaVISTAAR App FAQs**"
        )
        out = await self._run(self._message("m1", answer))
        self.assertEqual(answer, self._text_of(out))

    async def test_keeps_the_farmer_facing_no_videos_sentence(self):
        # Tool string is "No videos found for `q`"; this farmer-facing line is not.
        answer = "No videos are available for this topic. Would you like written steps instead?"
        out = await self._run(self._message("m1", answer))
        self.assertEqual(answer, self._text_of(out))

    async def test_drops_raw_tool_output_echo(self):
        out = await self._run(
            self._message("m1", "> Videos for `how to use the app`\n\n**[Login with Farmer ID]**")
        )
        self.assertEqual("", self._text_of(out))

    async def test_answer_survives_when_a_junk_message_follows_it(self):
        answer = "Open the app and enter your mobile number to receive an OTP for verification."
        events = self._message("m1", answer) + self._message("m2", " FileNotFound: No videos found for `x`")
        out = await self._run(events)
        self.assertEqual(answer, self._text_of(out))

    async def test_short_message_below_sniff_window_is_classified(self):
        out = await self._run(self._message("m1", "ValueError: boom"))
        self.assertEqual("", self._text_of(out))
        out = await self._run(self._message("m1", "Spray neem oil."))
        self.assertEqual("Spray neem oil.", self._text_of(out))

    async def test_non_text_events_pass_through_untouched(self):
        from ag_ui.core import CustomEvent, RunFinishedEvent

        custom = CustomEvent(name="related_documents", value={"documents": []})
        finished = RunFinishedEvent(thread_id="t", run_id="r")
        out = await self._run([custom, *self._message("m1", "Real answer text here."), finished])
        self.assertIn(custom, out)
        self.assertIn(finished, out)


class AgUiStateHandlingTests(unittest.TestCase):
    def test_state_is_consumed_not_forwarded_to_deps(self):
        """FarmerContext is not a StateHandler dataclass; forwarding state only warns."""
        from pydantic_ai.ui.ag_ui import AGUIAdapter

        run_input = AGUIAdapter.build_run_input(json.dumps(_agui_body()).encode())
        self.assertIsInstance(run_input.state, dict)
        self.assertEqual("test-thread", run_input.state["session_id"])


if __name__ == "__main__":
    unittest.main()
