import asyncio
import unittest

from app.services.identity import phone_to_memory_user_id, resolve_memory_user_id
from app.services.profile import Crop, FarmerProfile, ProfileStore, merge_profile


class IdentityResolutionTests(unittest.TestCase):
    def test_jwt_mobile_is_highest_priority(self):
        claims = {"mobile": "9876543210", "unique_id": "2342", "sub": "session-x"}
        self.assertEqual(
            phone_to_memory_user_id("+919876543210"),
            resolve_memory_user_id("anonymous", claims),
        )

    def test_phone_normalization_variants_resolve_identically(self):
        base = resolve_memory_user_id("anonymous", {"mobile": "9876543210"})
        for variant in ["+919876543210", "919876543210", "09876543210", "+91 98765 43210"]:
            self.assertEqual(base, resolve_memory_user_id("anonymous", {"mobile": variant}))

    def test_unique_id_beats_sub_and_generic_ids(self):
        claims = {"sub": "per-session-abc", "user_id": "u-9", "unique_id": "2342"}
        self.assertEqual("2342", resolve_memory_user_id("anonymous", claims))

    def test_farmer_id_beats_generic_ids_when_no_unique_id(self):
        claims = {"user_id": "u-9", "farmer_id": "F-1", "sub": "per-session-abc"}
        self.assertEqual("F-1", resolve_memory_user_id("anonymous", claims))

    def test_sub_is_last_resort(self):
        self.assertEqual("s-1", resolve_memory_user_id("anonymous", {"sub": "s-1"}))

    def test_token_variants_without_mobile_resolve_same_farmer(self):
        """Regression: two JWT variants for one farmer must not mint two memory ids."""
        token_a = {"unique_id": "2342", "sub": "login-session-1"}
        token_b = {"unique_id": "2342", "user_id": "some-internal-id"}
        self.assertEqual(
            resolve_memory_user_id("anonymous", token_a),
            resolve_memory_user_id("anonymous", token_b),
        )

    def test_guest_without_claims_has_no_memory(self):
        self.assertIsNone(resolve_memory_user_id("anonymous", None))


class FakeProfileBackend:
    """In-memory get/upsert with a yield point between read and write."""

    def __init__(self):
        self.payload = None

    async def get(self, user_id):
        await asyncio.sleep(0.01)  # widen the read-modify-write window
        if self.payload is None:
            return None
        return FarmerProfile(**self.payload)

    async def save(self, profile):
        await asyncio.sleep(0.01)
        self.payload = profile.model_dump()


def _wired_store(backend):
    store = ProfileStore()
    store.get = backend.get
    store.save = backend.save
    return store


class ProfileStoreRaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_updates_do_not_lose_fields(self):
        """Regression: two parallel tool calls (as pydantic-ai runs them) must both persist."""
        backend = FakeProfileBackend()
        store = _wired_store(backend)
        await asyncio.gather(
            store.apply_update("user-1", {"land_area_acres": 2.0}),
            store.apply_update("user-1", {"crops": [{"name": "potato"}]}),
        )
        final = FarmerProfile(**backend.payload)
        self.assertEqual(2.0, final.land_area_acres)
        self.assertEqual(["potato"], [c.name for c in final.crops])

    async def test_concurrent_update_and_removal_serialize(self):
        backend = FakeProfileBackend()
        backend.payload = FarmerProfile(
            user_id="user-1", crops=[Crop(name="cotton"), Crop(name="wheat")]
        ).model_dump()
        store = _wired_store(backend)
        await asyncio.gather(
            store.apply_update("user-1", {"irrigation": "drip"}),
            store.apply_removal("user-1", "crops", "wheat"),
        )
        final = FarmerProfile(**backend.payload)
        self.assertEqual("drip", final.irrigation)
        self.assertEqual(["cotton"], [c.name for c in final.crops])


class CropMergeTests(unittest.TestCase):
    def test_comma_joined_crop_names_split(self):
        merged = merge_profile(
            FarmerProfile(user_id="u"),
            {"crops": [{"name": "soyabean, maize"}]},
        )
        self.assertEqual({"soyabean", "maize"}, {c.name for c in merged.crops})

    def test_split_crops_merge_case_insensitively_with_existing(self):
        merged = merge_profile(
            FarmerProfile(user_id="u", crops=[Crop(name="Soyabean")]),
            {"crops": [{"name": "soyabean, maize"}]},
        )
        self.assertEqual(2, len(merged.crops))
        # merge keeps newest-write casing for the name, matches pre-existing semantics
        self.assertEqual({"soyabean", "maize"}, {c.name for c in merged.crops})

    def test_plain_string_crop_list_splits(self):
        merged = merge_profile(FarmerProfile(user_id="u"), {"crops": ["wheat, gram"]})
        self.assertEqual({"wheat", "gram"}, {c.name for c in merged.crops})

    def test_single_crop_unaffected(self):
        merged = merge_profile(FarmerProfile(user_id="u"), {"crops": [{"name": "cotton"}]})
        self.assertEqual(["cotton"], [c.name for c in merged.crops])


if __name__ == "__main__":
    unittest.main()
