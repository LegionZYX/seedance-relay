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


class UpstreamAdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        os.environ["DB_PATH"] = str(root / "relay.sqlite")
        os.environ["VIDEO_DIR"] = str(root / "videos")
        os.environ["UPLOAD_DIR"] = str(root / "uploads")
        os.environ["ADMIN_KEY"] = "admin-upstream"
        os.environ["ADMIN_PASSWORD"] = ""
        os.environ["BYTEPLUS_ACCESS_KEY_ID"] = "ak-test"
        os.environ["BYTEPLUS_SECRET_ACCESS_KEY"] = "sk-test"

        if str(PROJECT_DIR) not in sys.path:
            sys.path.insert(0, str(PROJECT_DIR))
        sys.modules.pop("relay_server", None)
        self.server = importlib.import_module("relay_server")
        self.client = TestClient(self.server.app)
        self.user_id = "u_upstream"
        now = int(time.time())
        db = self.server.get_db()
        db.execute(
            """INSERT INTO users
               (id, api_key, email, balance_usd, is_active, is_admin,
                byteplus_api_key, note, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                self.user_id,
                "sk-upstream",
                "upstream@example.test",
                50.0,
                1,
                0,
                "existing-endpoint-key",
                json.dumps(
                    {
                        "upstream_mode": "auto_dedicated",
                        "customer_slug": "upstream",
                        "byteplus_project_name": "upstream",
                        "byteplus_endpoint_id": "ep-existing",
                        "modelark_asset_group_id": "group-existing",
                        "byteplus_endpoint_key_rotation_enabled": True,
                        "byteplus_endpoint_api_key_expires_at": now + 86400,
                    }
                ),
                now,
            ),
        )
        db.close()

    def tearDown(self):
        self.tmp.cleanup()
        for key in (
            "BYTEPLUS_ACCESS_KEY_ID",
            "BYTEPLUS_SECRET_ACCESS_KEY",
        ):
            os.environ.pop(key, None)

    def admin_headers(self):
        return {"X-Admin-Key": "admin-upstream"}

    def test_admin_can_read_and_patch_customer_upstream_config_without_key_leak(self):
        current = self.client.get(
            f"/admin/users/{self.user_id}/upstream",
            headers=self.admin_headers(),
        )
        self.assertEqual(current.status_code, 200, current.text)
        body = current.json()
        self.assertEqual(body["upstream_mode"], "auto_dedicated")
        self.assertEqual(body["byteplus_endpoint_id"], "ep-existing")
        self.assertEqual(body["endpoint_api_key_masked"], "existi...t-key")
        self.assertNotIn("existing-endpoint-key", current.text)

        patched = self.client.patch(
            f"/admin/users/{self.user_id}/upstream",
            headers=self.admin_headers(),
            json={
                "upstream_mode": "shared",
                "customer_slug": "upstream-renamed",
                "byteplus_project_name": "project-renamed",
                "byteplus_endpoint_id": "ep-renamed",
                "modelark_asset_group_id": "group-renamed",
                "endpoint_key_rotation_enabled": False,
                "endpoint_api_key": "",
            },
        )
        self.assertEqual(patched.status_code, 200, patched.text)
        updated = patched.json()
        self.assertEqual(updated["upstream_mode"], "shared")
        self.assertEqual(updated["customer_slug"], "upstream-renamed")
        self.assertEqual(updated["endpoint_api_key_masked"], "existi...t-key")
        self.assertNotIn("existing-endpoint-key", patched.text)

        db = self.server.get_db()
        row = db.execute("SELECT byteplus_api_key, note FROM users WHERE id=?", (self.user_id,)).fetchone()
        db.close()
        self.assertEqual(row["byteplus_api_key"], "existing-endpoint-key")
        note = json.loads(row["note"])
        self.assertEqual(note["byteplus_endpoint_id"], "ep-renamed")
        self.assertFalse(note["byteplus_endpoint_key_rotation_enabled"])

    def test_admin_manual_endpoint_key_rotation_updates_key_and_masks_response(self):
        def fake_get_endpoint_api_key(endpoint_id, duration_seconds):
            self.assertEqual(endpoint_id, "ep-existing")
            self.assertEqual(duration_seconds, 60)
            return {"api_key": "rotated-endpoint-key", "expires_at": 1234567890}

        self.server._get_endpoint_api_key = fake_get_endpoint_api_key

        rotated = self.client.post(
            f"/admin/users/{self.user_id}/upstream/endpoint-key/rotate",
            headers=self.admin_headers(),
            json={"duration_seconds": 60},
        )
        self.assertEqual(rotated.status_code, 200, rotated.text)
        body = rotated.json()
        self.assertEqual(body["endpoint_api_key_masked"], "rotate...t-key")
        self.assertEqual(body["byteplus_endpoint_api_key_expires_at"], 1234567890)
        self.assertNotIn("rotated-endpoint-key", rotated.text)

        db = self.server.get_db()
        row = db.execute("SELECT byteplus_api_key, note FROM users WHERE id=?", (self.user_id,)).fetchone()
        db.close()
        self.assertEqual(row["byteplus_api_key"], "rotated-endpoint-key")
        self.assertEqual(json.loads(row["note"])["byteplus_endpoint_api_key_expires_at"], 1234567890)

    def test_admin_can_create_dry_run_and_mocked_provision_job(self):
        dry_run = self.client.post(
            f"/admin/users/{self.user_id}/upstream/provision",
            headers=self.admin_headers(),
            json={
                "customer_slug": "peterlv",
                "dry_run": True,
                "create_project": True,
                "create_endpoint": True,
                "create_asset_group": True,
                "rotate_endpoint_key": True,
            },
        )
        self.assertEqual(dry_run.status_code, 200, dry_run.text)
        dry = dry_run.json()
        self.assertEqual(dry["status"], "dry_run")
        self.assertEqual(dry["planned"]["customer_slug"], "peterlv")

        def fake_provision(user, req):
            self.assertEqual(user["id"], self.user_id)
            self.assertEqual(req.customer_slug, "peterlv")
            return {
                "customer_slug": "peterlv",
                "byteplus_project_name": "peterlv",
                "byteplus_endpoint_id": "ep-peter",
                "modelark_asset_group_id": "group-peter",
                "endpoint_api_key": "peter-endpoint-key",
                "byteplus_endpoint_api_key_expires_at": 2222222222,
            }

        self.server._provision_customer_upstream_resources = fake_provision
        created = self.client.post(
            f"/admin/users/{self.user_id}/upstream/provision",
            headers=self.admin_headers(),
            json={
                "customer_slug": "peterlv",
                "dry_run": False,
                "create_project": True,
                "create_endpoint": True,
                "create_asset_group": True,
                "rotate_endpoint_key": True,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()
        self.assertEqual(body["status"], "succeeded")
        self.assertEqual(body["upstream"]["byteplus_endpoint_id"], "ep-peter")
        self.assertEqual(body["upstream"]["endpoint_api_key_masked"], "peter-...t-key")
        self.assertNotIn("peter-endpoint-key", created.text)

        db = self.server.get_db()
        row = db.execute("SELECT byteplus_api_key, note FROM users WHERE id=?", (self.user_id,)).fetchone()
        job = db.execute(
            "SELECT status FROM upstream_provision_jobs WHERE user_id=? AND status='succeeded' LIMIT 1",
            (self.user_id,),
        ).fetchone()
        db.close()
        self.assertEqual(row["byteplus_api_key"], "peter-endpoint-key")
        self.assertEqual(json.loads(row["note"])["modelark_asset_group_id"], "group-peter")
        self.assertEqual(job["status"], "succeeded")

    def test_provision_creates_aigc_asset_group_inside_customer_project(self):
        calls = []

        def fake_call_asset_api(action, body, ak, sk):
            calls.append({"action": action, "body": body, "ak": ak, "sk": sk})
            if action == "CreateProject":
                return {"Result": {"ProjectId": "project-peter"}}
            if action == "CreateEndpoint":
                return {"Result": {"EndpointId": "ep-peter"}}
            if action == "CreateAssetGroup":
                return {"Result": {"Id": "group-peter"}}
            if action == "GetApiKey":
                return {"Result": {"ApiKey": "peter-endpoint-key", "ExpiresAt": 2222222222}}
            raise AssertionError(f"unexpected action: {action}")

        self.server._call_asset_api = fake_call_asset_api
        db = self.server.get_db()
        user = dict(db.execute("SELECT * FROM users WHERE id=?", (self.user_id,)).fetchone())
        db.close()
        req = self.server.ProvisionUpstreamRequest(
            customer_slug="peterlv",
            create_project=True,
            create_endpoint=True,
            create_asset_group=True,
            rotate_endpoint_key=True,
            endpoint_key_duration_seconds=3600,
        )

        result = self.server._provision_customer_upstream_resources(user, req)

        self.assertEqual(result["byteplus_project_name"], "peterlv")
        self.assertEqual(result["modelark_asset_group_id"], "group-peter")
        create_group = next(call for call in calls if call["action"] == "CreateAssetGroup")
        self.assertEqual(create_group["body"]["GroupType"], "AIGC")
        self.assertEqual(create_group["body"]["ProjectName"], "peterlv")

    def test_iam_capabilities_reports_configured_flags_without_secrets(self):
        resp = self.client.get("/admin/upstream/iam-capabilities", headers=self.admin_headers())
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["iam_configured"])
        self.assertTrue(body["can_rotate_endpoint_key"])
        self.assertNotIn("ak-test", resp.text)
        self.assertNotIn("sk-test", resp.text)


if __name__ == "__main__":
    unittest.main()
