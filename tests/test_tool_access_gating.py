import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from agents.tools import TOOLS, _require_farmer_identity
from agents.tools.agristack import AgristackResponse, _extract_registry_fields
from app.services.profile import FarmerProfile, profile_store


def _ctx(*, farmer_id=None, unique_id=None):
    return SimpleNamespace(deps=SimpleNamespace(farmer_id=farmer_id, unique_id=unique_id))


class FarmerIdentityGatingTests(unittest.TestCase):
    def test_guest_gets_no_tool(self):
        self.assertIsNone(_require_farmer_identity(_ctx(), object()))

    def test_guest_sentinel_strings_get_no_tool(self):
        self.assertIsNone(_require_farmer_identity(_ctx(farmer_id="", unique_id=""), object()))

    def test_guest_sentinel_values_get_no_tool(self):
        for sentinel in ("guest", "anonymous", "unknown", "unauthenticated", "guest_user"):
            with self.subTest(sentinel=sentinel):
                self.assertIsNone(
                    _require_farmer_identity(_ctx(unique_id=sentinel), object())
                )

    def test_unique_id_offers_tool(self):
        tool_def = object()
        self.assertIs(tool_def, _require_farmer_identity(_ctx(unique_id="2342"), tool_def))

    def test_farmer_id_offers_tool(self):
        tool_def = object()
        self.assertIs(tool_def, _require_farmer_identity(_ctx(farmer_id="F-1"), tool_def))


class ToolRegistrationGatingTests(unittest.TestCase):
    def test_login_gated_tools_have_prepare(self):
        gated = {"fetch_agristack_data", "get_scheme_status", "get_pocra_dbt_status"}
        found = {t.function.__name__: t for t in TOOLS if t.function.__name__ in gated}
        self.assertEqual(gated, set(found), "gated tools missing from TOOLS")
        for name, tool in found.items():
            self.assertIs(tool.prepare, _require_farmer_identity, name)

    def test_guest_usable_tools_stay_ungated(self):
        # PM-KISAN / SMAM / search take manual identifiers or need no identity —
        # they must remain available to guests.
        open_tools = {
            "pmkisan_installment_init",
            "pmkisan_installment_status",
            "smam_application_status",
            "get_scheme_info",
            "search_documents",
        }
        found = {t.function.__name__: t for t in TOOLS if t.function.__name__ in open_tools}
        self.assertEqual(open_tools, set(found), "expected tools missing from TOOLS")
        for name, tool in found.items():
            self.assertIsNone(tool.prepare, name)


class AgristackIdentityFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_unique_id_farmer_reaches_network_call(self):
        """unique_id-only token: resolver supplies farmer_id, tool proceeds to fetch."""
        from agents.tools import agristack as agristack_module

        ctx = SimpleNamespace(deps=SimpleNamespace(farmer_id=None, unique_id="2342"))
        with patch(
            "agents.tools.cross_network._resolve_farmer_id_from_context",
            new=AsyncMock(return_value="F-123"),
        ) as resolver, patch("httpx.AsyncClient", side_effect=httpx.RequestError("boom")):
            result = await agristack_module.fetch_agristack_data(ctx)

        resolver.assert_awaited_once_with(ctx)
        self.assertIn("request failed", result)

    async def test_guest_gets_not_available(self):
        from agents.tools import agristack as agristack_module

        ctx = SimpleNamespace(deps=SimpleNamespace(farmer_id=None, unique_id=None))
        with patch(
            "agents.tools.cross_network._resolve_farmer_id_from_context",
            new=AsyncMock(return_value=None),
        ):
            result = await agristack_module.fetch_agristack_data(ctx)

        self.assertIn("not available", result)


def _agristack_payload(tags):
    ctx = {
        "action": "search",
        "timestamp": "2026-07-21T09:00:00Z",
        "message_id": "m-1",
        "transaction_id": "t-1",
        "domain": "advisory:mh-vistaar",
        "version": "1.1.0",
    }
    return {
        "context": ctx,
        "responses": [
            {
                "context": ctx,
                "message": {
                    "catalog": {
                        "providers": [
                            {
                                "id": "p1",
                                "descriptor": {"name": "Agristack"},
                                "items": [
                                    {
                                        "id": "i1",
                                        "descriptor": {"name": "Farmer"},
                                        "tags": tags,
                                    }
                                ],
                                "locations": [{"gps": "13.34,77.10"}],
                            }
                        ]
                    }
                },
            }
        ],
    }


_LOCATION_TAGS = [
    {"code": "village_name", "value": "Huliyar"},
    {"code": "district_name", "value": "Tumkur"},
    {"code": "taluka_name", "value": "Chiknayakanhalli"},
    {"code": "mobile", "value": "9999999969"},
    {"code": "total_plot_area", "value": "0.81"},
]


class AgristackRegistryFieldTests(unittest.TestCase):
    def test_extracts_village_and_district(self):
        response = AgristackResponse.model_validate(_agristack_payload(_LOCATION_TAGS))
        self.assertEqual(
            {"village": "Huliyar", "district": "Tumkur"},
            _extract_registry_fields(response),
        )

    def test_pii_plot_area_and_taluka_never_extracted(self):
        response = AgristackResponse.model_validate(_agristack_payload(_LOCATION_TAGS))
        fields = _extract_registry_fields(response)
        self.assertNotIn("mobile", fields)
        self.assertNotIn("land_area_acres", fields)
        self.assertNotIn("taluka", fields)

    def test_empty_response_yields_nothing(self):
        response = AgristackResponse.model_validate(_agristack_payload([]))
        self.assertEqual({}, _extract_registry_fields(response))


class _FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, timeout=None):
        return _FakeResponse(self._payload)


class _FakeProfileBackend:
    """In-memory get/save wired onto the real profile_store."""

    def __init__(self):
        self.payload = None

    async def get(self, user_id):
        if self.payload is None:
            return None
        return FarmerProfile(**self.payload)

    async def save(self, profile):
        self.payload = profile.model_dump()


class AgristackToolHarvestWiringTests(unittest.IsolatedAsyncioTestCase):
    """Tool → resolver → http → harvest → real store (fake backend)."""

    async def _run_tool(self, backend):
        from agents.tools import agristack as agristack_module

        ctx = SimpleNamespace(
            deps=SimpleNamespace(farmer_id="F-1", unique_id=None, memory_user_id="u-1")
        )
        payload = _agristack_payload(_LOCATION_TAGS)
        with patch.object(profile_store, "get", backend.get), patch.object(
            profile_store, "save", backend.save
        ), patch("httpx.AsyncClient", lambda *a, **k: _FakeAsyncClient(payload)):
            return await agristack_module.fetch_agristack_data(ctx)

    async def test_fetch_harvests_location_into_empty_profile(self):
        backend = _FakeProfileBackend()
        result = await self._run_tool(backend)
        self.assertIn("Farmer Information", result)
        saved = FarmerProfile(**backend.payload)
        self.assertEqual("Huliyar", saved.village)
        self.assertEqual("Tumkur", saved.district)

    async def test_fetch_preserves_farmer_stated_location(self):
        backend = _FakeProfileBackend()
        backend.payload = FarmerProfile(
            user_id="u-1", village="Bhadgaon", district="Jalgaon"
        ).model_dump()
        result = await self._run_tool(backend)
        self.assertIn("Farmer Information", result)
        saved = FarmerProfile(**backend.payload)
        self.assertEqual("Bhadgaon", saved.village)
        self.assertEqual("Jalgaon", saved.district)


class AgristackPrecedencePromptTests(unittest.TestCase):
    def test_prompts_state_profile_first_and_auto_harvest(self):
        from pathlib import Path

        expected = {
            "en": ("Use the saved farmer profile first", "stored in the farmer's profile automatically"),
            "hi": ("पहले सहेजी गई किसान प्रोफ़ाइल उपयोग करें", "अपने आप सहेजे जाते हैं"),
            "mr": ("आधी सेव्ह केलेली शेतकरी प्रोफाइल वापरा", "प्रोफाइलमध्ये आपोआप सेव्ह होतात"),
            "bhb": ("पेला सेव्ह रेहेली शेतकरी प्रोफाइल वापरो", "प्रोफाइल म आपोआप सेव्ह रेहे"),
        }
        for lang, (profile_first, auto_store) in expected.items():
            with self.subTest(language=lang):
                text = Path(f"assets/prompts/agrinet_system_{lang}.md").read_text()
                self.assertIn(profile_first, text)
                self.assertIn(auto_store, text)


class PresentToolTests(unittest.IsolatedAsyncioTestCase):
    """present_video / present_suggestions are the agent's explicit UI decisions."""

    def setUp(self):
        from agents.deps import FarmerContext

        self.deps = FarmerContext(query="cotton pests", lang_code="en")
        self.deps.remember_video_candidates(
            [
                {"id": "doc-1", "url": "https://youtu.be/aaa", "title": "Cotton Pest Control"},
                {"id": "doc-2", "url": "https://youtu.be/bbb", "title": "MahaVISTAAR AI App"},
            ]
        )
        self.ctx = SimpleNamespace(deps=self.deps)

    async def test_unknown_id_retries_and_attaches_nothing(self):
        from pydantic_ai import ModelRetry
        from agents.tools.present import present_video

        with self.assertRaises(ModelRetry):
            await present_video(self.ctx, "v99")
        self.assertEqual([], self.deps.related_videos)

    async def test_present_video_attaches_only_the_chosen_candidate(self):
        from agents.tools.present import present_video

        result = await present_video(self.ctx, "v1")
        self.assertIn("Cotton Pest Control", result)
        self.assertEqual(
            ["Cotton Pest Control"], [v["title"] for v in self.deps.related_videos]
        )

    async def test_search_videos_alone_attaches_nothing(self):
        # remember_video_candidates ran in setUp — candidates exist, but until the
        # model calls present_video the reply carries no video.
        self.assertEqual(2, len(self.deps.video_candidates))
        self.assertEqual([], self.deps.related_videos)

    async def test_present_suggestions_trims_and_dedupes(self):
        from agents.tools.present import MAX_SUGGESTIONS, present_suggestions

        await present_suggestions(
            self.ctx,
            [
                " Best sowing time for cotton ",
                "",
                "x" * 500,
                "Best sowing time for cotton",
                "Fertilizer dose next week",
                "Irrigation schedule for June",
                "One question too many",
            ],
        )
        self.assertLessEqual(len(self.deps.suggested_questions), MAX_SUGGESTIONS)
        self.assertEqual(
            len(set(self.deps.suggested_questions)), len(self.deps.suggested_questions)
        )
        self.assertNotIn("", self.deps.suggested_questions)


class PresentToolPromptTests(unittest.TestCase):
    def test_all_languages_describe_both_present_tools(self):
        from pathlib import Path

        for lang in ("en", "hi", "mr", "bhb"):
            with self.subTest(language=lang):
                text = Path(f"assets/prompts/agrinet_system_{lang}.md").read_text()
                self.assertIn("present_video", text)
                self.assertIn("present_suggestions", text)


if __name__ == "__main__":
    unittest.main()
