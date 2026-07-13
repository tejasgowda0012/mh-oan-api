import asyncio
import unittest

from app.services.ag_ui import format_sse_event, stream_ag_ui_events


class AGUIAdapterTests(unittest.TestCase):
    def test_format_sse_event_uses_event_and_data(self) -> None:
        event = format_sse_event("message_delta", {"delta": "hello"})

        self.assertIn("event: message_delta", event)
        self.assertIn('"delta": "hello"', event)

    def test_stream_ag_ui_events_emits_session_and_completion_events(self) -> None:
        async def fake_chunks():
            yield "Hello"
            yield " world"

        async def run_stream() -> list[str]:
            events: list[str] = []
            async for event in stream_ag_ui_events(
                chunk_source=fake_chunks(),
                session_id="session-123",
                user_id="user-123",
                query="How are you?",
            ):
                events.append(event)
            return events

        events = asyncio.run(run_stream())

        self.assertGreaterEqual(len(events), 3)
        self.assertTrue(any("session_started" in event for event in events))
        self.assertTrue(any("message_delta" in event for event in events))
        self.assertTrue(any("message_completed" in event for event in events))
        self.assertTrue(any("Hello world" in event for event in events))


if __name__ == "__main__":
    unittest.main()
