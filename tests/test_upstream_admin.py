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

    def test_admin_manual_endpoint_key_rotation_uses_endpoint_map_when_present(self):
        db = self.server.get_db()
        note = json.loads(db.execute("SELECT note FROM users WHERE id=?", (self.user_id,)).fetchone()["note"])
        note["byteplus_endpoint_map"] = {
            "dreamina-seedance-2-0-260128": "ep-standard",
            "dreamina-seedance-2-0-fast-260128": "ep-fast",
        }
        db.execute("UPDATE users SET note=? WHERE id=?", (json.dumps(note), self.user_id))
        db.close()

        def fake_get_endpoint_api_key(endpoint_ids, duration_seconds):
            self.assertEqual(set(endpoint_ids), {"ep-standard", "ep-fast"})
            self.assertEqual(duration_seconds, 60)
            return {"api_key": "rotated-map-key", "expires_at": 1234567890}

        self.server._get_endpoint_api_key = fake_get_endpoint_api_key

        rotated = self.client.post(
            f"/admin/users/{self.user_id}/upstream/endpoint-key/rotate",
            headers=self.admin_headers(),
            json={"duration_seconds": 60},
        )

        self.assertEqual(rotated.status_code, 200, rotated.text)
        self.assertNotIn("rotated-map-key", rotated.text)
        db = self.server.get_db()
        row = db.execute("SELECT byteplus_api_key FROM users WHERE id=?", (self.user_id,)).fetchone()
        db.close()
        self.assertEqual(row["byteplus_api_key"], "rotated-map-key")

    def test_endpoint_key_request_uses_resource_ids_contract(self):
        calls = []

        def fake_call_asset_api(action, body, ak, sk):
            calls.append({"action": action, "body": body, "ak": ak, "sk": sk})
            return {"ApiKey": "endpoint-key", "ExpiredTime": 1234567890}

        self.server._call_asset_api = fake_call_asset_api

        issued = self.server._get_endpoint_api_key("ep-peter", 3600)

        self.assertEqual(issued, {"api_key": "endpoint-key", "expires_at": 1234567890})
        self.assertEqual(calls, [{
            "action": "GetApiKey",
            "body": {
                "DurationSeconds": 3600,
                "ResourceType": "endpoint",
                "ResourceIds": ["ep-peter"],
            },
            "ak": "ak-test",
            "sk": "sk-test",
        }])

    def test_admin_rotate_endpoint_key_can_store_per_endpoint_key_map_without_leaking(self):
        models = list(self.server.DEFAULT_CUSTOMER_MODEL_IDS)[:2]
        note = {
            "upstream_mode": "auto_dedicated",
            "customer_slug": "upstream",
            "byteplus_project_name": "upstream",
            "byteplus_endpoint_id": "ep-standard",
            "byteplus_endpoint_map": {
                models[0]: "ep-standard",
                models[1]: "ep-fast",
            },
            "byteplus_endpoint_key_rotation_enabled": True,
        }
        db = self.server.get_db()
        db.execute(
            "UPDATE users SET note=?, byteplus_api_key=? WHERE id=?",
            (json.dumps(note), "old-key", self.user_id),
        )
        db.close()
        self.server.ENDPOINT_KEY_RESOURCE_MODE = "per_endpoint"
        calls = []

        def fake_call_asset_api(action, body, ak, sk):
            calls.append(body)
            resource_ids = body["ResourceIds"]
            self.assertEqual(len(resource_ids), 1)
            endpoint_id = resource_ids[0]
            return {"ApiKey": f"secret-key-for-{endpoint_id}", "ExpiresAt": 3333333333}

        self.server._call_asset_api = fake_call_asset_api

        rotated = self.client.post(
            f"/admin/users/{self.user_id}/upstream/endpoint-key/rotate",
            headers=self.admin_headers(),
            json={"duration_seconds": 3600},
        )

        self.assertEqual(rotated.status_code, 200, rotated.text)
        self.assertNotIn("secret-key-for-ep-standard", rotated.text)
        self.assertNotIn("secret-key-for-ep-fast", rotated.text)
        body = rotated.json()
        self.assertEqual(body["endpoint_key_mode"], "per_endpoint")
        self.assertTrue(body["endpoint_key_map_configured"])
        self.assertEqual(set(body["endpoint_key_map"]), set(models))
        self.assertEqual(
            body["endpoint_key_map"][models[0]]["api_key_masked"],
            "secret...ndard",
        )
        self.assertEqual(
            [call["ResourceIds"] for call in calls],
            [["ep-standard"], ["ep-fast"]],
        )

        db = self.server.get_db()
        row = db.execute("SELECT byteplus_api_key, note FROM users WHERE id=?", (self.user_id,)).fetchone()
        db.close()
        self.assertEqual(row["byteplus_api_key"], "secret-key-for-ep-standard")
        stored_note = json.loads(row["note"])
        self.assertEqual(stored_note["byteplus_endpoint_key_mode"], "per_endpoint")
        self.assertEqual(
            stored_note["byteplus_endpoint_key_map"][models[1]]["api_key"],
            "secret-key-for-ep-fast",
        )

        user_detail = self.client.get(f"/admin/users/{self.user_id}", headers=self.admin_headers())
        self.assertEqual(user_detail.status_code, 200, user_detail.text)
        self.assertNotIn("secret-key-for-ep-standard", user_detail.text)
        self.assertNotIn("secret-key-for-ep-fast", user_detail.text)

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
        self.assertEqual(dry["current_step"], "dry_run")
        self.assertEqual(dry["progress"], 100)
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
        self.assertEqual(body["current_step"], "persist_customer_config")
        self.assertEqual(body["progress"], 100)
        self.assertEqual(body["upstream"]["byteplus_endpoint_id"], "ep-peter")
        self.assertEqual(body["upstream"]["endpoint_api_key_masked"], "peter-...t-key")
        self.assertNotIn("peter-endpoint-key", created.text)

        db = self.server.get_db()
        row = db.execute("SELECT byteplus_api_key, note FROM users WHERE id=?", (self.user_id,)).fetchone()
        job = db.execute(
            "SELECT status, current_step, progress FROM upstream_provision_jobs WHERE user_id=? AND status='succeeded' LIMIT 1",
            (self.user_id,),
        ).fetchone()
        db.close()
        self.assertEqual(row["byteplus_api_key"], "peter-endpoint-key")
        self.assertEqual(json.loads(row["note"])["modelark_asset_group_id"], "group-peter")
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["current_step"], "persist_customer_config")
        self.assertEqual(job["progress"], 100)

    def test_provision_creates_aigc_asset_group_inside_customer_project(self):
        calls = []
        ensured_projects = []

        def fake_call_asset_api(action, body, ak, sk):
            calls.append({"action": action, "body": body, "ak": ak, "sk": sk})
            if action == "CreateEndpoint":
                return {"Result": {"EndpointId": f"ep-peter-{len([c for c in calls if c['action'] == 'CreateEndpoint'])}"}}
            if action == "GetEndpoint":
                return {"Result": {"Status": "Running"}}
            if action == "CreateAssetGroup":
                return {"Result": {"Id": "group-peter"}}
            if action == "GetApiKey":
                return {"Result": {"ApiKey": "peter-endpoint-key", "ExpiresAt": 2222222222}}
            raise AssertionError(f"unexpected action: {action}")

        def fake_ensure_project(project_name, display_name, description):
            ensured_projects.append(
                {
                    "project_name": project_name,
                    "display_name": display_name,
                    "description": description,
                }
            )
            return {"ProjectName": project_name, "Status": "Created"}

        self.server._call_asset_api = fake_call_asset_api
        self.server._ensure_byteplus_project = fake_ensure_project
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
        self.assertEqual(set(result["byteplus_endpoint_map"]), set(self.server.DEFAULT_CUSTOMER_MODEL_IDS))
        self.assertEqual(ensured_projects[0]["project_name"], "peterlv")
        self.assertNotIn("CreateProject", [call["action"] for call in calls])
        create_endpoint_calls = [call for call in calls if call["action"] == "CreateEndpoint"]
        self.assertEqual(len(create_endpoint_calls), len(self.server.DEFAULT_CUSTOMER_MODEL_IDS))
        create_endpoint = create_endpoint_calls[0]
        self.assertEqual(create_endpoint["body"]["ProjectName"], "peterlv")
        self.assertEqual(create_endpoint["body"]["Name"], "relay-peterlv-sd-2-0-260128")
        self.assertEqual(
            create_endpoint["body"]["ModelReference"],
            {
                "FoundationModel": {
                    "Name": "dreamina-seedance-2-0",
                    "ModelVersion": "260128",
                },
                "CustomModelId": "",
            },
        )
        self.assertEqual(create_endpoint["body"]["Moderation"], {"Strategy": "Skip"})
        create_group = next(call for call in calls if call["action"] == "CreateAssetGroup")
        self.assertEqual(create_group["body"]["GroupType"], "AIGC")
        self.assertEqual(create_group["body"]["ProjectName"], "peterlv")
        get_key = next(call for call in calls if call["action"] == "GetApiKey")
        self.assertEqual(set(get_key["body"]["ResourceIds"]), set(result["byteplus_endpoint_map"].values()))

    def test_provision_skips_existing_endpoint_map_entries(self):
        models = list(self.server.DEFAULT_CUSTOMER_MODEL_IDS)
        existing_model = models[0]
        missing_model_count = len(models) - 1
        db = self.server.get_db()
        row = db.execute("SELECT note FROM users WHERE id=?", (self.user_id,)).fetchone()
        note = json.loads(row["note"])
        note["byteplus_endpoint_map"] = {existing_model: "ep-existing-mapped"}
        db.execute("UPDATE users SET note=? WHERE id=?", (json.dumps(note), self.user_id))
        user = dict(db.execute("SELECT * FROM users WHERE id=?", (self.user_id,)).fetchone())
        db.close()

        calls = []

        def fake_call_asset_api(action, body, ak, sk):
            calls.append({"action": action, "body": body})
            if action == "CreateEndpoint":
                model = next(
                    tag["Value"]
                    for tag in body["Tags"]
                    if tag["Key"] == "clientModel"
                )
                self.assertNotEqual(model, existing_model)
                return {"Result": {"EndpointId": f"ep-created-{model}"}}
            if action == "GetEndpoint":
                return {"Result": {"Status": "Running"}}
            if action == "GetApiKey":
                return {"Result": {"ApiKey": "merged-map-key", "ExpiresAt": 2222222222}}
            raise AssertionError(f"unexpected action: {action}")

        self.server._call_asset_api = fake_call_asset_api
        self.server._ensure_byteplus_project = lambda project_name, display_name, description: {
            "ProjectName": project_name,
        }
        req = self.server.ProvisionUpstreamRequest(
            customer_slug="upstream",
            create_project=True,
            create_endpoint=True,
            create_asset_group=False,
            rotate_endpoint_key=True,
            endpoint_key_duration_seconds=3600,
        )

        result = self.server._provision_customer_upstream_resources(user, req)

        create_endpoint_calls = [call for call in calls if call["action"] == "CreateEndpoint"]
        self.assertEqual(len(create_endpoint_calls), missing_model_count)
        self.assertEqual(result["byteplus_endpoint_map"][existing_model], "ep-existing-mapped")
        self.assertEqual(set(result["byteplus_endpoint_map"]), set(models))
        get_key = next(call for call in calls if call["action"] == "GetApiKey")
        self.assertIn("ep-existing-mapped", get_key["body"]["ResourceIds"])

    def test_main_user_save_preserves_endpoint_note_fields_from_stale_form(self):
        patched = self.client.patch(
            f"/admin/users/{self.user_id}/upstream",
            headers=self.admin_headers(),
            json={
                "upstream_mode": "auto_dedicated",
                "customer_slug": "peterlv",
                "byteplus_project_name": "peterlv",
                "byteplus_endpoint_id": "ep-peter",
                "modelark_asset_group_id": "group-peter",
                "endpoint_key_rotation_enabled": True,
            },
        )
        self.assertEqual(patched.status_code, 200, patched.text)

        stale_note_from_open_form = json.dumps({"legacy_note": "opened before endpoint save"})
        saved = self.client.patch(
            f"/admin/users/{self.user_id}",
            headers=self.admin_headers(),
            json={
                "balance_usd": 51.0,
                "note": stale_note_from_open_form,
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)

        current = self.client.get(
            f"/admin/users/{self.user_id}/upstream",
            headers=self.admin_headers(),
        )
        self.assertEqual(current.status_code, 200, current.text)
        body = current.json()
        self.assertEqual(body["byteplus_endpoint_id"], "ep-peter")
        self.assertEqual(body["modelark_asset_group_id"], "group-peter")
        self.assertTrue(body["endpoint_key_rotation_enabled"])

    def test_ensure_byteplus_project_uses_iam_project_openapi(self):
        calls = []

        def fake_call_iam_api(action, params, ak, sk):
            calls.append({"action": action, "params": params, "ak": ak, "sk": sk})
            if action == "GetProject":
                raise self.server.HTTPException(502, {"error": {
                    "code": "iam_project_error",
                    "message": "EntityNotFound: project is not found",
                }})
            if action == "CreateProject":
                return {"Result": {"Project": {"ProjectName": params["ProjectName"], "Id": 41626686}}}
            raise AssertionError(f"unexpected action: {action}")

        self.server._call_iam_api = fake_call_iam_api

        result = self.server._ensure_byteplus_project(
            "peterlv",
            display_name="peterlv",
            description="Relay customer peterlv",
        )

        self.assertEqual(result["ProjectName"], "peterlv")
        self.assertEqual([call["action"] for call in calls], ["GetProject", "CreateProject"])
        self.assertEqual(calls[1]["params"], {
            "ProjectName": "peterlv",
            "Description": "Relay customer peterlv",
        })
        self.assertEqual(calls[1]["ak"], "ak-test")
        self.assertEqual(calls[1]["sk"], "sk-test")

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
