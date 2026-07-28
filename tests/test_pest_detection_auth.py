import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("LOGFIRE_SEND_TO_LOGFIRE", "false")

from agents.deps import FarmerContext

# Import the module under test without executing agents.tools.__init__, whose
# eager tool registration is unrelated to these unit tests.
tools_package = types.ModuleType("agents.tools")
tools_package.__path__ = [str(Path(__file__).parents[1] / "agents" / "tools")]
sys.modules["agents.tools"] = tools_package

from agents.tools import pest_detection as pest_detection_module
from agents.tools.pest_detection import (
    PEST_GUEST_USER_ID,
    _authenticate_pest_service_with_identity,
    _get_pest_login_uid_candidates,
    _normalize_pest_registration_id,
)


class _ToolContext:
    def __init__(self, deps: FarmerContext):
        self.deps = deps


def _context(**user_info):
    return _ToolContext(
        FarmerContext(
            query="test",
            farmer_id=user_info.get("farmer_id"),
            unique_id=user_info.get("unique_id"),
            user_info=user_info,
        )
    )


class PestLoginCandidateTests(unittest.TestCase):
    def test_current_public_user_metadata_never_uses_mobile_as_login_uid(self):
        ctx = _context(
            mobile="7676884202",
            name="Shashank",
            role="public",
            farmer_id=None,
            unique_id=45,
        )

        self.assertEqual(_get_pest_login_uid_candidates(ctx), ["45"])

    def test_guest_only_uses_service_guest_id(self):
        ctx = _context(user_type="guest", mobile="7676884202", unique_id=45)

        self.assertEqual(_get_pest_login_uid_candidates(ctx), [PEST_GUEST_USER_ID])

    def test_farmer_id_precedes_unique_id_and_candidates_are_deduplicated(self):
        ctx = _context(farmer_id="12", unique_id="12", mobile="7676884202")

        self.assertEqual(_get_pest_login_uid_candidates(ctx), ["12"])

    def test_authenticated_user_without_registration_id_does_not_become_guest(self):
        ctx = _context(mobile="7676884202", role="public")

        self.assertEqual(_get_pest_login_uid_candidates(ctx), [])

    def test_registration_id_must_fit_postgres_integer(self):
        self.assertEqual(_normalize_pest_registration_id("00045"), "45")
        self.assertIsNone(_normalize_pest_registration_id("7676884202"))
        self.assertIsNone(_normalize_pest_registration_id("farmer-45"))


class _Response:
    def __init__(self, payload):
        self._payload = payload
        self.content = b"{}"

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _AsyncClient:
    calls = []
    responses = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, url, headers):
        self.__class__.calls.append(headers["uid"])
        return _Response(self.__class__.responses.pop(0))


class PestAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_the_authenticated_registration_id(self):
        _AsyncClient.calls = []
        _AsyncClient.responses = [{"access_token": "farmer-access-token"}]
        ctx = _context(unique_id=45, mobile="7676884202", role="public")

        with (
            patch.object(pest_detection_module, "_encrypt_uid", side_effect=lambda uid: uid),
            patch.object(pest_detection_module.httpx, "AsyncClient", _AsyncClient),
        ):
            auth = await _authenticate_pest_service_with_identity(ctx)

        self.assertEqual(_AsyncClient.calls, ["45"])
        self.assertEqual(auth.user_id, "45")
        self.assertEqual(auth.headers, {"Authorization": "Bearer farmer-access-token"})

    async def test_rejected_authenticated_id_does_not_retry_as_guest(self):
        _AsyncClient.calls = []
        _AsyncClient.responses = [
            {"status": 201, "response": "Invalid request"},
        ]
        ctx = _context(unique_id=45, mobile="7676884202", role="public")

        with (
            patch.object(pest_detection_module, "_encrypt_uid", side_effect=lambda uid: uid),
            patch.object(pest_detection_module.httpx, "AsyncClient", _AsyncClient),
        ):
            with self.assertRaisesRegex(RuntimeError, "missing access token"):
                await _authenticate_pest_service_with_identity(ctx)

        self.assertEqual(_AsyncClient.calls, ["45"])


if __name__ == "__main__":
    unittest.main()
