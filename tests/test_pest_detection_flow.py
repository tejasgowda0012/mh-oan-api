import json
import os
import sys
import tempfile
import types
import unittest
from inspect import signature
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ.setdefault("LOGFIRE_SEND_TO_LOGFIRE", "false")

from agents.deps import FarmerContext

if "agents.tools" not in sys.modules:
    tools_package = types.ModuleType("agents.tools")
    tools_package.__path__ = [str(Path(__file__).parents[1] / "agents" / "tools")]
    sys.modules["agents.tools"] = tools_package

from agents.tools import pest_detection as pest_detection_module
from agents.tools.pest_detection import PestServiceAuth, run_pest_detection_analysis

# Router imports only need the dependency callable; JWT cryptography is covered
# elsewhere and loading a deployment public key would make these unit tests
# environment-dependent.
jwt_auth_module = types.ModuleType("app.auth.jwt_auth")

async def _test_current_user():
    return {"unique_id": 45}

jwt_auth_module.get_current_user = _test_current_user
sys.modules["app.auth.jwt_auth"] = jwt_auth_module

routers_package = types.ModuleType("app.routers")
routers_package.__path__ = [str(Path(__file__).parents[1] / "app" / "routers")]
sys.modules["app.routers"] = routers_package

from app.routers import pest_detection as pest_router
from app.routers import upload as upload_router


class _ToolContext:
    def __init__(self):
        self.deps = FarmerContext(
            query="analyze pest image",
            unique_id="45",
            user_info={"unique_id": 45, "role": "public"},
        )


class UploadValidationTests(unittest.IsolatedAsyncioTestCase):
    def test_image_route_requires_the_current_user_dependency(self):
        dependency = signature(upload_router.get_upload_image).parameters[
            "_user_info"
        ].default
        self.assertIs(dependency.dependency, _test_current_user)

    async def test_validates_metadata_and_real_image_signature(self):
        class Upload:
            content_type = "image/jpeg"
            filename = "crop.jpg"

            async def read(self):
                return b"\xff\xd8\xff" + b"image-content"

        sowing_date = (date.today() - timedelta(days=7)).isoformat()
        with (
            tempfile.TemporaryDirectory() as upload_dir,
            patch.object(upload_router, "get_upload_dir", return_value=Path(upload_dir)),
            patch.object(upload_router, "set_cache", new=AsyncMock()),
        ):
            record = await upload_router.save_pest_upload(
                Upload(), "25", "cotton", sowing_date, "https://example.test"
            )

        self.assertEqual(record["crop_id"], "25")
        self.assertTrue(record["upload_id"].startswith("pest_"))
        self.assertIn("/api/upload/", record["image_url"])

    async def test_rejects_spoofed_image_and_recent_sowing_date(self):
        class Upload:
            content_type = "image/jpeg"
            filename = "not-really.jpg"

            async def read(self):
                return b"plain text"

        with self.assertRaisesRegex(ValueError, "at least 7 days"):
            await upload_router.save_pest_upload(
                Upload(), "25", "cotton", date.today().isoformat()
            )

        with self.assertRaisesRegex(ValueError, "does not match"):
            await upload_router.save_pest_upload(
                Upload(),
                "25",
                "cotton",
                (date.today() - timedelta(days=7)).isoformat(),
            )


class PestAnalysisFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_flow_persists_external_response_for_feedback(self):
        with tempfile.NamedTemporaryFile(suffix=".jpg") as image:
            image.write(b"\xff\xd8\xffimage")
            image.flush()
            upload_record = {
                "upload_id": "pest_test",
                "crop_id": "25",
                "crop_type": "cotton",
                "sowing_date": "2026-03-18",
                "image_path": image.name,
                "image_filename": "cotton.jpg",
                "content_type": "image/jpeg",
            }
            updates = AsyncMock()
            env = {
                "PEST_DETECTION_PREDICT_URL": "https://example.test/predict",
                "PEST_DETECTION_ADVISORY_URL": "https://example.test/advisory",
                "PEST_DETECTION_STORE_RESPONSE_URL": "https://example.test/store",
            }
            with (
                patch.dict(os.environ, env, clear=False),
                patch.object(upload_router, "get_pest_upload", new=AsyncMock(return_value=upload_record)),
                patch.object(upload_router, "update_pest_upload", new=updates),
                patch.object(
                    pest_detection_module,
                    "_authenticate_pest_service_with_identity",
                    new=AsyncMock(
                        return_value=PestServiceAuth(
                            headers={"Authorization": "Bearer token"}, user_id="3"
                        )
                    ),
                ),
                patch.object(
                    pest_detection_module,
                    "_post_multipart_predict",
                    new=AsyncMock(
                        return_value={
                            "data": {
                                "predictions": [
                                    {"disease_type": "Leaf spot", "disease_id": "91"}
                                ]
                            }
                        }
                    ),
                ),
                patch.object(
                    pest_detection_module,
                    "_post_crop_pd_advisory",
                    new=AsyncMock(
                        return_value={
                            "data": [{
                                "crop_name": "Cotton",
                                "disease_pest": "Leaf spot",
                                "preventive_measures": "Keep the field clean.",
                                "curative_measures": "Use the recommended treatment.",
                            }]
                        }
                    ),
                ),
                patch.object(
                    pest_detection_module,
                    "_post_store_response",
                    new=AsyncMock(return_value={"data": {"response_id": 812}}),
                ),
            ):
                message = await run_pest_detection_analysis(_ToolContext(), "pest_test")

        self.assertIn("Cotton", message)
        self.assertIn("Leaf spot", message)
        analysis = updates.await_args.args[1]["analysis"]
        self.assertEqual(analysis["predict_pd_id"], "91")
        self.assertEqual(analysis["stored_response"]["data"]["response_id"], 812)
        self.assertEqual(analysis["pest_user_id"], "3")

class PestFeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_feedback_requires_completed_upstream_store(self):
        with patch.object(
            upload_router,
            "get_pest_upload",
            new=AsyncMock(return_value={"analysis": {"stored_response": {}}}),
        ):
            with self.assertRaisesRegex(Exception, "cannot be submitted"):
                await pest_router.store_pest_detection_feedback(
                    upload_id="pest_test",
                    feedback="Liked the response",
                    user_info={"unique_id": 45},
                )

    async def test_feedback_relays_saved_external_response_id(self):
        class Response:
            content = b'{"status":"success"}'
            status_code = 200
            headers = {"content-type": "application/json"}

            def raise_for_status(self):
                return None

            def json(self):
                return {"status": "success"}

        class Client:
            posted_data = None

            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            async def post(self, url, headers, data):
                self.__class__.posted_data = data
                return Response()

        record = {
            "analysis": {"stored_response": {"data": {"response_id": 812}}}
        }
        with (
            patch.object(
                upload_router,
                "get_pest_upload",
                new=AsyncMock(return_value=record),
            ),
            patch.object(
                pest_router,
                "_authenticate_pest_service",
                new=AsyncMock(return_value={"Authorization": "Bearer token"}),
            ),
            patch.object(pest_router.httpx, "AsyncClient", Client),
        ):
            response = await pest_router.store_pest_detection_feedback(
                upload_id="pest_test",
                feedback="Liked the response",
                user_info={"unique_id": 45},
            )

        self.assertEqual(
            Client.posted_data,
            {"id": "812", "feedback": "Liked the response"},
        )
        self.assertEqual(json.loads(response.body), {"status": "success"})


if __name__ == "__main__":
    unittest.main()
