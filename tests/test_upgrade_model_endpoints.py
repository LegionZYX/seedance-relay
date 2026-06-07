import importlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


class UpgradeModelEndpointsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        os.environ["DB_PATH"] = str(root / "relay.sqlite")
        os.environ["VIDEO_DIR"] = str(root / "videos")
        os.environ["UPLOAD_DIR"] = str(root / "uploads")
        os.environ["PUBLIC_DOMAIN"] = "media.example.test"
        os.environ["ADMIN_PASSWORD"] = ""
        os.environ["UPSTREAM_API_KEY"] = "ark-test-upstream"
        os.environ["BYTEPLUS_ACCESS_KEY_ID"] = "ak-test"
        os.environ["BYTEPLUS_ACCESS_KEY_SECRET"] = "sk-test"
        os.environ["BYTEPLUS_ENDPOINT_WAIT_SECONDS"] = "1"
        os.environ["BYTEPLUS_ENDPOINT_WAIT_INTERVAL"] = "0"

        if str(PROJECT_DIR) not in sys.path:
            sys.path.insert(0, str(PROJECT_DIR))
        sys.modules.pop("relay_server", None)
        sys.modules.pop("deploy.upgrade_model_endpoints", None)
        self.server = importlib.import_module("relay_server")
        self.script = importlib.import_module("deploy.upgrade_model_endpoints")

        self.note = {
            "customer_slug": "acme",
            "byteplus_project_name": "acme-project",
            "byteplus_endpoint_map": {
                "dreamina-seedance-2-0-260128": "ep-old-standard",
            },
            "modelark_asset_group_id": "group-acme",
            "byteplus_endpoint_key_rotation_enabled": True,
            "byteplus_endpoint_key_next_rotate_at": 2222222222,
            "billing_marker": "keep-me",
        }
        db = self.server.get_db()
        db.execute(
            """INSERT INTO users
               (id, api_key, email, balance_usd, byteplus_api_key,
                is_active, is_admin, created_at, note)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                "u_acme",
                "sk-acme-relay",
                "acme@example.test",
                100.0,
                "secret-customer-endpoint-key",
                1,
                0,
                int(time.time()),
                json.dumps(self.note),
            ),
        )
        db.close()

    def tearDown(self):
        self.tmp.cleanup()

    def _user_note(self):
        db = self.server.get_db()
        row = db.execute("SELECT note, byteplus_api_key FROM users WHERE id='u_acme'").fetchone()
        db.close()
        return json.loads(row["note"]), row["byteplus_api_key"]

    def test_dry_run_reports_plan_without_upstream_call_or_db_write(self):
        def fail_call_asset_api(*_args, **_kwargs):
            raise AssertionError("dry-run must not call BytePlus")

        self.script.relay_server._call_asset_api = fail_call_asset_api

        result = self.script.upgrade_model_endpoints(
            model_id="dreamina-seedance-2-0-270101",
            from_model_id="dreamina-seedance-2-0-260128",
            dry_run=True,
        )

        note, endpoint_key = self._user_note()
        self.assertEqual(endpoint_key, "secret-customer-endpoint-key")
        self.assertEqual(note, self.note)
        self.assertEqual(result["planned"], 1)
        self.assertEqual(result["upgraded"], 0)
        self.assertEqual(result["data"][0]["status"], "would_create_endpoint")
        self.assertEqual(result["data"][0]["project_name"], "acme-project")
        rendered = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("secret-customer-endpoint-key", rendered)
        self.assertNotIn("ak-test", rendered)
        self.assertNotIn("sk-test", rendered)

    def test_real_run_creates_endpoint_and_merges_endpoint_map_only(self):
        calls = []

        def fake_call_asset_api(action, body, ak, sk):
            calls.append({"action": action, "body": body, "ak": ak, "sk": sk})
            if action == "CreateEndpoint":
                return {"Result": {"EndpointId": "ep-new-fast"}}
            if action == "GetEndpoint":
                return {"Result": {"Status": "Running"}}
            raise AssertionError(f"unexpected action: {action}")

        self.script.relay_server._call_asset_api = fake_call_asset_api

        result = self.script.upgrade_model_endpoints(
            model_id="dreamina-seedance-2-0-fast-260128",
            from_model_id="dreamina-seedance-2-0-260128",
            dry_run=False,
            wait=True,
        )

        self.assertEqual(result["upgraded"], 1)
        create_call = next(call for call in calls if call["action"] == "CreateEndpoint")
        self.assertEqual(create_call["body"]["ProjectName"], "acme-project")
        self.assertEqual(create_call["body"]["Moderation"], {"Strategy": "Skip"})
        self.assertEqual(
            create_call["body"]["ModelReference"],
            {
                "FoundationModel": {
                    "Name": "dreamina-seedance-2-0-fast",
                    "ModelVersion": "260128",
                },
                "CustomModelId": "",
            },
        )
        self.assertNotIn("GetApiKey", [call["action"] for call in calls])

        note, endpoint_key = self._user_note()
        self.assertEqual(endpoint_key, "secret-customer-endpoint-key")
        self.assertEqual(note["byteplus_endpoint_map"]["dreamina-seedance-2-0-260128"], "ep-old-standard")
        self.assertEqual(note["byteplus_endpoint_map"]["dreamina-seedance-2-0-fast-260128"], "ep-new-fast")
        self.assertEqual(note["modelark_asset_group_id"], "group-acme")
        self.assertTrue(note["byteplus_endpoint_key_rotation_enabled"])
        self.assertEqual(note["byteplus_endpoint_key_next_rotate_at"], 2222222222)
        self.assertEqual(note["billing_marker"], "keep-me")
        self.assertIn("byteplus_endpoint_map_updated_at", note)


if __name__ == "__main__":
    unittest.main()
