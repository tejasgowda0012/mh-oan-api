import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents.tools.memory_tool import save_farmer_memory
from agents.tools.profile_tool import (
    remove_farmer_profile_value,
    update_farmer_profile,
)
from app.services.memory_context import preload_farmer_profile
from app.services.profile import Crop, FarmerProfile, remove_profile_value


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

    async def apply_removal(self, user_id, field, value):
        self.calls.append((user_id, field, value))
        return True


class FakeProfileLookup:
    async def get(self, user_id):
        return FarmerProfile(
            user_id=user_id,
            village="Bhadgaon",
            crops=[Crop(name="cotton")],
        )


class MemoryCreationToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_memory_uses_current_farmer_identity_with_inference(self):
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
                    True,
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

    async def test_profile_removal_uses_current_farmer_identity(self):
        store = FakeProfileStore()
        ctx = SimpleNamespace(deps=SimpleNamespace(memory_user_id="farmer-a"))

        with patch("app.services.profile.profile_store", store):
            result = await remove_farmer_profile_value(ctx, "crop", "cotton")

        self.assertEqual("Removed profile crop: cotton.", result)
        self.assertEqual([("farmer-a", "crops", "cotton")], store.calls)


class ProfilePresenceTests(unittest.TestCase):
    def test_state_only_profile_is_not_empty(self):
        self.assertFalse(FarmerProfile(user_id="farmer-a", state="Maharashtra").is_empty())

    def test_language_only_profile_is_not_empty(self):
        self.assertFalse(FarmerProfile(user_id="farmer-a", language="Marathi").is_empty())

    def test_preferred_call_time_only_profile_is_not_empty(self):
        self.assertFalse(
            FarmerProfile(user_id="farmer-a", preferred_call_time="evening").is_empty()
        )

    def test_remove_one_crop_preserves_unrelated_profile_values(self):
        profile = FarmerProfile(
            user_id="farmer-a",
            village="Bhadgaon",
            crops=[Crop(name="cotton"), Crop(name="soybean")],
        )
        updated, changed = remove_profile_value(profile, "crops", "Cotton")

        self.assertTrue(changed)
        self.assertEqual(["soybean"], [crop.name for crop in updated.crops])
        self.assertEqual("Bhadgaon", updated.village)

    def test_remove_scalar_requires_matching_old_value(self):
        profile = FarmerProfile(user_id="farmer-a", village="Bhadgaon")

        unchanged, changed = remove_profile_value(profile, "village", "Pachora")
        self.assertFalse(changed)
        self.assertEqual("Bhadgaon", unchanged.village)

        updated, changed = remove_profile_value(profile, "village", "bhadgaon")
        self.assertTrue(changed)
        self.assertIsNone(updated.village)


class ProfilePreloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_preload_contains_profile_without_episodic_memories(self):
        with patch("app.services.memory_context.profile_store", FakeProfileLookup()):
            context = await preload_farmer_profile("farmer-a")

        self.assertIn("Location: Bhadgaon", context)
        self.assertIn("Crop: cotton", context)
        self.assertNotIn("Episodic", context)
        self.assertNotIn("Memory ID", context)


class MemoryCreationPolicyTests(unittest.TestCase):
    def test_save_tool_defers_extraction_to_mem0_infer(self):
        doc = inspect.getdoc(save_farmer_memory)
        self.assertIn("skips exact duplicates automatically", doc)
        self.assertIn("no need to recall before saving", doc)
        self.assertIn("edit_farmer_memory", doc)
        self.assertIn("update_farmer_profile", doc)
        self.assertIn("Do not call it for an unchanged", inspect.getdoc(update_farmer_profile))
        self.assertIn("clearly says", inspect.getdoc(remove_farmer_profile_value))

    def test_every_live_prompt_states_memory_policy(self):
        prompts = Path("assets/prompts")
        for language in ("en", "mr", "hi", "bhb"):
            with self.subTest(language=language):
                text = (prompts / f"agrinet_system_{language}.md").read_text()
                self.assertIn("MANDATORY MEMORY CHECK", text)
                self.assertIn("Skipping the memory call is incorrect", text)
                self.assertIn('"I have a wheat farm; how can I increase yield?"', text)
                self.assertIn("save crop=wheat before answering", text)
                self.assertIn("Unstructured durable personal context", text)
                self.assertIn("before, or in the same tool-call batch as", text)
                self.assertIn("Do not save every message", text)
                self.assertIn("already contains the same value unchanged", text)
                self.assertIn("sent directly to `save_farmer_memory`", text)
                self.assertIn("skips exact duplicates automatically", text)
                self.assertIn("call `edit_farmer_memory` with the returned ID", text)
                self.assertIn("call `delete_farmer_memory` with the exact returned ID", text)
                self.assertIn("call `remove_farmer_profile_value`", text)
                self.assertIn("Never convert a question", text)
                self.assertIn("Never save OTPs", text)


if __name__ == "__main__":
    unittest.main()
