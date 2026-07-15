import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents.tools.memory_tool import save_farmer_memory
from agents.tools.profile_tool import update_farmer_profile
from app.services.profile import FarmerProfile


class FakeMemoryService:
    def __init__(self):
        self.calls = []

    async def add_fact(self, user_id, memory, *, source, infer):
        self.calls.append((user_id, memory, source, infer))
        return "Saved to farmer memory."


class FakeProfileStore:
    def __init__(self):
        self.calls = []

    async def apply_update(self, user_id, partial):
        self.calls.append((user_id, partial))
        return FarmerProfile(user_id=user_id, **partial)


class MemoryCreationToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_memory_uses_current_farmer_identity_without_inference(self):
        service = FakeMemoryService()
        ctx = SimpleNamespace(deps=SimpleNamespace(memory_user_id="farmer-a"))

        with patch("app.services.memory.memory_service", service):
            result = await save_farmer_memory(ctx, "Follow up on cotton wilt next week")

        self.assertEqual("Saved to farmer memory.", result)
        self.assertEqual(
            [
                (
                    "farmer-a",
                    "Follow up on cotton wilt next week",
                    "save_farmer_memory",
                    False,
                )
            ],
            service.calls,
        )

    async def test_profile_update_uses_current_farmer_identity_and_structured_field(self):
        store = FakeProfileStore()
        ctx = SimpleNamespace(deps=SimpleNamespace(memory_user_id="farmer-a"))

        with patch("app.services.profile.profile_store", store):
            result = await update_farmer_profile(ctx, "crop", "cotton")

        self.assertEqual("Saved profile crop: cotton.", result)
        self.assertEqual(
            [("farmer-a", {"crops": [{"name": "cotton"}]})],
            store.calls,
        )


class ProfilePresenceTests(unittest.TestCase):
    def test_state_only_profile_is_not_empty(self):
        self.assertFalse(FarmerProfile(user_id="farmer-a", state="Maharashtra").is_empty())

    def test_language_only_profile_is_not_empty(self):
        self.assertFalse(FarmerProfile(user_id="farmer-a", language="Marathi").is_empty())

    def test_preferred_call_time_only_profile_is_not_empty(self):
        self.assertFalse(
            FarmerProfile(user_id="farmer-a", preferred_call_time="evening").is_empty()
        )


class MemoryCreationPolicyTests(unittest.TestCase):
    def test_creation_tool_descriptions_are_mandatory(self):
        self.assertIn("MUST call this", inspect.getdoc(save_farmer_memory))
        self.assertIn("MUST call this", inspect.getdoc(update_farmer_profile))

    def test_every_live_prompt_requires_same_turn_creation(self):
        prompts = Path("assets/prompts")
        for language in ("en", "mr", "hi", "bhb"):
            with self.subTest(language=language):
                text = (prompts / f"agrinet_system_{language}.md").read_text()
                self.assertIn("saving that information in the same turn is mandatory", text)
                self.assertIn("MUST call `update_farmer_profile`", text)
                self.assertIn("Use `save_farmer_memory`", text)
                self.assertIn("A message containing only personal farm information", text)
                self.assertIn("Never save OTPs", text)


if __name__ == "__main__":
    unittest.main()
