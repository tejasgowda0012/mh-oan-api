import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agents.tools.memory_tool import delete_farmer_memory, edit_farmer_memory
from app.services.memory import MemoryService


class FakeMemoryClient:
    def __init__(self):
        self.memories = {
            "memory-a": {
                "id": "memory-a",
                "memory": "Old cotton note",
                "user_id": "farmer-a",
                "created_at": "2026-07-01T00:00:00Z",
                "updated_at": None,
                "score": 0.9,
            },
            "memory-b": {
                "id": "memory-b",
                "memory": "Another farmer's note",
                "user_id": "farmer-b",
                "created_at": "2026-07-02T00:00:00Z",
                "updated_at": None,
                "score": 0.9,
            },
        }

    def get(self, memory_id):
        memory = self.memories.get(memory_id)
        return dict(memory) if memory else None

    def get_all(self, *, filters, top_k):
        del top_k
        user_id = filters["user_id"]
        return {
            "results": [
                dict(memory)
                for memory in self.memories.values()
                if memory["user_id"] == user_id
            ]
        }

    def search(self, query, *, filters, top_k, threshold):
        del query, top_k
        return {
            "results": [
                dict(memory)
                for memory in self.memories.values()
                if memory["user_id"] == filters["user_id"]
                and memory["score"] >= threshold
            ]
        }

    def update(self, memory_id, data=None, metadata=None):
        del metadata
        self.memories[memory_id]["memory"] = data
        return {"message": "Memory updated successfully!"}

    def delete(self, memory_id):
        del self.memories[memory_id]
        return {"message": "Memory deleted successfully!"}


class MemoryServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = FakeMemoryClient()
        self.service = MemoryService()
        self.service._client = self.client

    async def test_get_all_preserves_memory_ids(self):
        memories = await self.service.get_all("farmer-a")
        self.assertEqual(["memory-a"], [memory["id"] for memory in memories])
        self.assertEqual("farmer-a", memories[0]["user_id"])

    async def test_recall_result_includes_internal_memory_id(self):
        result = await self.service.search("cotton", "farmer-a")
        self.assertIn("Memory ID: memory-a", result)
        self.assertIn("Old cotton note", result)

    async def test_owner_can_update_one_memory(self):
        result = await self.service.update_memory(
            "farmer-a", "memory-a", "Corrected cotton note"
        )
        self.assertEqual("Updated farmer memory.", result)
        self.assertEqual("Corrected cotton note", self.client.memories["memory-a"]["memory"])
        self.assertIn("memory-b", self.client.memories)

    async def test_other_farmer_cannot_update_memory(self):
        result = await self.service.update_memory(
            "farmer-a", "memory-b", "Unauthorized replacement"
        )
        self.assertEqual("Memory not found for this farmer.", result)
        self.assertEqual("Another farmer's note", self.client.memories["memory-b"]["memory"])

    async def test_owner_can_delete_one_memory(self):
        result = await self.service.delete_memory("farmer-a", "memory-a")
        self.assertEqual("Deleted farmer memory.", result)
        self.assertNotIn("memory-a", self.client.memories)
        self.assertIn("memory-b", self.client.memories)

    async def test_other_farmer_cannot_delete_memory(self):
        result = await self.service.delete_memory("farmer-a", "memory-b")
        self.assertEqual("Memory not found for this farmer.", result)
        self.assertIn("memory-b", self.client.memories)


class FakeMemoryService:
    def __init__(self):
        self.calls = []

    async def update_memory(self, user_id, memory_id, new_memory):
        self.calls.append(("update", user_id, memory_id, new_memory))
        return "Updated farmer memory."

    async def delete_memory(self, user_id, memory_id):
        self.calls.append(("delete", user_id, memory_id))
        return "Deleted farmer memory."


class MemoryToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_edit_tool_uses_current_farmer_identity(self):
        fake_service = FakeMemoryService()
        ctx = SimpleNamespace(deps=SimpleNamespace(memory_user_id="farmer-a"))
        with patch("app.services.memory.memory_service", fake_service):
            result = await edit_farmer_memory(ctx, "memory-a", "Corrected note")
        self.assertEqual("Updated farmer memory.", result)
        self.assertEqual(
            [("update", "farmer-a", "memory-a", "Corrected note")],
            fake_service.calls,
        )

    async def test_delete_tool_uses_current_farmer_identity(self):
        fake_service = FakeMemoryService()
        ctx = SimpleNamespace(deps=SimpleNamespace(memory_user_id="farmer-a"))
        with patch("app.services.memory.memory_service", fake_service):
            result = await delete_farmer_memory(ctx, "memory-a")
        self.assertEqual("Deleted farmer memory.", result)
        self.assertEqual([("delete", "farmer-a", "memory-a")], fake_service.calls)


if __name__ == "__main__":
    unittest.main()
