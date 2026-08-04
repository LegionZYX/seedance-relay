import importlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


PROJECT_DIR = Path(__file__).resolve().parents[1]


class EndpointMapHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        os.environ["DB_PATH"] = str(root / "relay.sqlite")
        os.environ["VIDEO_DIR"] = str(root / "videos")
        os.environ["UPLOAD_DIR"] = str(root / "uploads")
        os.environ["ADMIN_KEY"] = "admin-health"
        os.environ["ADMIN_PASSWORD"] = ""

        if str(PROJECT_DIR) not in sys.path:
            sys.path.insert(0, str(PROJECT_DIR))
        sys.modules.pop("relay_server", None)
        self.server = importlib.import_module("relay_server")
        self.client = TestClient(self.server.app)

    def tearDown(self):
        self.tmp.cleanup()

    def admin_headers(self):
        return {"X-Admin-Key": "admin-health"}

    def test_admin_endpoint_map_health_reports_missing_extra_and_masks_key_map(self):
        models = list(self.server.DEFAULT_CUSTOMER_MODEL_IDS)[:2]
        note = {
            "upstream_mode": "auto_dedicated",
            "byteplus_project_name": "health-customer",
            "modelark_asset_group_id": "group-health",
            "byteplus_endpoint_map": {
                models[0]: "ep-standard",
                "legacy-model": "ep-legacy",
            },
            "byteplus_endpoint_key_map": {
                models[0]: {
                    "endpoint_id": "ep-standard",
                    "api_key": "very-secret-endpoint-key",
                    "expires_at": int(time.time()) + 3600,
                }
            },
            "byteplus_endpoint_key_mode": "per_endpoint",
        }
        db = self.server.get_db()
        db.execute(
            """INSERT INTO users
               (id, api_key, email, balance_usd, is_active, is_admin,
                enabled_models, byteplus_api_key, note, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                "u_health",
                "sk-health",
                "health@example.test",
                10.0,
                1,
                0,
                json.dumps(models),
                "legacy-key",
                json.dumps(note),
                int(time.time()),
            ),
        )
        db.close()

        response = self.client.get(
            "/admin/upstream/endpoint-map-health?user_id=u_health",
            headers=self.admin_headers(),
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("very-secret-endpoint-key", response.text)
        body = response.json()
        self.assertEqual(body["total"], 1)
        item = body["data"][0]
        self.assertEqual(item["user_id"], "u_health")
        self.assertEqual(item["status"], "warning")
        self.assertEqual(item["missing_models"], [models[1]])
        self.assertEqual(item["extra_models"], ["legacy-model"])
        self.assertEqual(item["mapped_models"], [models[0]])
        self.assertTrue(item["project_configured"])
        self.assertTrue(item["asset_group_configured"])
        self.assertTrue(item["endpoint_key_map_configured"])
        self.assertEqual(item["endpoint_key_mode"], "per_endpoint")
        self.assertEqual(item["endpoint_key_map"][models[0]]["api_key_masked"], "very-s...t-key")


if __name__ == "__main__":
    unittest.main()
