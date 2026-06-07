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


class QuotaReminderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        os.environ["DB_PATH"] = str(root / "relay.sqlite")
        os.environ["VIDEO_DIR"] = str(root / "videos")
        os.environ["UPLOAD_DIR"] = str(root / "uploads")
        os.environ["ADMIN_KEY"] = "admin-quota"
        os.environ["ADMIN_PASSWORD"] = ""
        os.environ["BYTEPLUS_PROJECT_QUOTA_WARN_AT"] = "1"
        os.environ["BYTEPLUS_ENDPOINT_QUOTA_WARN_AT"] = "2"
        os.environ["BYTEPLUS_ASSET_GROUP_QUOTA_WARN_AT"] = "1"

        if str(PROJECT_DIR) not in sys.path:
            sys.path.insert(0, str(PROJECT_DIR))
        sys.modules.pop("relay_server", None)
        self.server = importlib.import_module("relay_server")
        self.client = TestClient(self.server.app)

    def tearDown(self):
        self.tmp.cleanup()
        for key in (
            "BYTEPLUS_PROJECT_QUOTA_WARN_AT",
            "BYTEPLUS_ENDPOINT_QUOTA_WARN_AT",
            "BYTEPLUS_ASSET_GROUP_QUOTA_WARN_AT",
        ):
            os.environ.pop(key, None)

    def admin_headers(self):
        return {"X-Admin-Key": "admin-quota"}

    def test_admin_quota_reminders_count_project_endpoint_and_asset_group_usage(self):
        now = int(time.time())
        db = self.server.get_db()
        db.execute(
            """INSERT INTO users
               (id, api_key, email, balance_usd, is_active, is_admin, note, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                "u_quota_one",
                "sk-quota-one",
                "quota-one@example.test",
                10.0,
                1,
                0,
                json.dumps({
                    "byteplus_project_name": "quota-one",
                    "modelark_asset_group_id": "group-one",
                    "byteplus_endpoint_map": {
                        "model-a": "ep-a",
                        "model-b": "ep-b",
                    },
                }),
                now,
            ),
        )
        db.execute(
            """INSERT INTO users
               (id, api_key, email, balance_usd, is_active, is_admin, note, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                "u_quota_two",
                "sk-quota-two",
                "quota-two@example.test",
                10.0,
                1,
                0,
                json.dumps({
                    "byteplus_project_name": "quota-two",
                    "byteplus_endpoint_map": {"model-c": "ep-c"},
                }),
                now,
            ),
        )
        db.close()

        response = self.client.get("/admin/upstream/quota-reminders", headers=self.admin_headers())

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        by_resource = {item["resource"]: item for item in body["data"]}
        self.assertEqual(by_resource["projects"]["used"], 2)
        self.assertEqual(by_resource["projects"]["status"], "warning")
        self.assertEqual(by_resource["endpoints"]["used"], 3)
        self.assertEqual(by_resource["endpoints"]["status"], "warning")
        self.assertEqual(by_resource["asset_groups"]["used"], 1)
        self.assertEqual(by_resource["asset_groups"]["status"], "warning")
        self.assertIn("Quota Center", by_resource["projects"]["reminder"])


if __name__ == "__main__":
    unittest.main()
