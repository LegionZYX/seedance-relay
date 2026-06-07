import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


PROJECT_DIR = Path(__file__).resolve().parents[1]


class FakeResponse:
    status_code = 200
    text = '{"id":"upstream-alias-task"}'
    headers = {}

    def json(self):
        return {"id": "upstream-alias-task"}


class FakeHttp:
    def __init__(self):
        self.posts = []

    async def post(self, url, json, headers, timeout):
        self.posts.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return FakeResponse()


class ModelAliasTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        os.environ["DB_PATH"] = str(root / "relay.sqlite")
        os.environ["VIDEO_DIR"] = str(root / "videos")
        os.environ["UPLOAD_DIR"] = str(root / "uploads")
        os.environ["PUBLIC_DOMAIN"] = "media.example.test"
        os.environ["ADMIN_PASSWORD"] = ""
        os.environ["ADMIN_KEY"] = "admin-alpha1"
        os.environ["UPSTREAM_API_KEY"] = "ark-test-upstream"
        os.environ["MARKUP_PCT"] = "0.30"
        os.environ["VIDEO_PERSIST_MODE"] = "proxy_only"
        os.environ["RUNTIME_INTERNAL_TOKEN"] = "runtime-internal-test"
        os.environ["MODEL_ID_ALIASES_JSON"] = '{"video-pro":"dreamina-seedance-2-0-260128"}'

        if str(PROJECT_DIR) not in sys.path:
            sys.path.insert(0, str(PROJECT_DIR))
        sys.modules.pop("relay_server", None)
        self.server = importlib.import_module("relay_server")
        self.fake_http = FakeHttp()
        self.server.http = self.fake_http
        self.client = TestClient(self.server.app)

    def tearDown(self):
        os.environ.pop("MODEL_ID_ALIASES_JSON", None)
        sys.modules.pop("relay_server", None)
        self.tmp.cleanup()

    def admin_headers(self):
        return {"X-Admin-Key": "admin-alpha1"}

    def auth_headers(self, api_key):
        return {"Authorization": f"Bearer {api_key}"}

    def test_admin_configured_model_alias_maps_to_native_byteplus_model(self):
        created = self.client.post(
            "/admin/users",
            headers=self.admin_headers(),
            json={
                "email": "alias@example.test",
                "password": "initial-password",
                "balance_usd": 100,
                "enabled_models": ["video-pro"],
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        user = created.json()

        models = self.client.get("/v1/models", headers=self.auth_headers(user["api_key"]))
        self.assertEqual(models.status_code, 200, models.text)
        self.assertEqual([item["id"] for item in models.json()["data"]], ["video-pro"])

        response = self.client.post(
            "/v1/videos",
            headers=self.auth_headers(user["api_key"]),
            json={
                "model": "video-pro",
                "content": [{"type": "text", "text": "Alias keeps customer-facing name short."}],
                "resolution": "480p",
                "duration": 5,
                "generate_audio": False,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["model"], "video-pro")
        self.assertEqual(self.fake_http.posts[-1]["json"]["model"], "dreamina-seedance-2-0-260128")

    def test_configured_alias_is_not_enabled_by_default(self):
        created = self.client.post(
            "/admin/users",
            headers=self.admin_headers(),
            json={
                "email": "native-default@example.test",
                "password": "initial-password",
                "balance_usd": 100,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        user = created.json()
        self.assertTrue(user["enabled_models_default"])

        models = self.client.get("/v1/models", headers=self.auth_headers(user["api_key"]))
        self.assertEqual(models.status_code, 200, models.text)
        ids = [item["id"] for item in models.json()["data"]]
        self.assertEqual(ids, self.server.DEFAULT_CUSTOMER_MODEL_IDS)
        self.assertEqual(self.server.DEFAULT_CUSTOMER_MODEL_IDS, self.server.NATIVE_MODEL_IDS)
        self.assertIn("dreamina-seedance-2-0-260128", ids)
        self.assertIn("dreamina-seedance-2-0-fast-260128", ids)
        self.assertNotIn("video-pro", ids)

        denied = self.client.post(
            "/v1/videos",
            headers=self.auth_headers(user["api_key"]),
            json={
                "model": "video-pro",
                "content": [{"type": "text", "text": "Alias must be explicitly enabled."}],
                "resolution": "480p",
                "duration": 5,
                "generate_audio": False,
            },
        )
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(denied.json()["detail"]["error"]["code"], "model_not_enabled")
        self.assertEqual(self.fake_http.posts, [])

    def test_admin_model_options_include_native_ids_and_configured_aliases(self):
        response = self.client.get("/admin/model-options", headers=self.admin_headers())

        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["data"]
        by_id = {item["id"]: item for item in items}
        self.assertIn("dreamina-seedance-2-0-260128", by_id)
        self.assertIn("dreamina-seedance-2-0-fast-260128", by_id)
        self.assertIn("video-pro", by_id)
        self.assertFalse(by_id["dreamina-seedance-2-0-260128"]["is_alias"])
        self.assertFalse(by_id["dreamina-seedance-2-0-fast-260128"]["is_alias"])
        self.assertTrue(by_id["video-pro"]["is_alias"])
        self.assertEqual(by_id["video-pro"]["alias_for"], "dreamina-seedance-2-0-260128")
        self.assertNotIn("upstream_model_or_endpoint", response.text)
        self.assertNotIn("ark-test-upstream", response.text)


if __name__ == "__main__":
    unittest.main()
