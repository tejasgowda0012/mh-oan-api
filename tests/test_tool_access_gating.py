import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from agents.tools import TOOLS, _require_farmer_identity


def _ctx(*, farmer_id=None, unique_id=None):
    return SimpleNamespace(deps=SimpleNamespace(farmer_id=farmer_id, unique_id=unique_id))


class FarmerIdentityGatingTests(unittest.TestCase):
    def test_guest_gets_no_tool(self):
        self.assertIsNone(_require_farmer_identity(_ctx(), object()))

    def test_guest_sentinel_strings_get_no_tool(self):
        self.assertIsNone(_require_farmer_identity(_ctx(farmer_id="", unique_id=""), object()))

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


if __name__ == "__main__":
    unittest.main()
