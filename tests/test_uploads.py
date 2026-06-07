import importlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


PROJECT_DIR = Path(__file__).resolve().parents[1]


class UploadEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        os.environ["DB_PATH"] = str(root / "relay.sqlite")
        os.environ["VIDEO_DIR"] = str(root / "videos")
        os.environ["UPLOAD_DIR"] = str(root / "uploads")
        os.environ["PUBLIC_DOMAIN"] = "media.example.test"
        os.environ["ADMIN_KEY"] = "admin-upload-test"
        os.environ["ADMIN_PASSWORD"] = ""
        os.environ.pop("ASSET_AUTO_REGISTER_UPLOADS", None)
        os.environ.pop("ASSET_AUTO_REGISTER_PURPOSES", None)
        os.environ.pop("BYTEPLUS_ACCESS_KEY_ID", None)
        os.environ.pop("BYTEPLUS_ACCESS_KEY_SECRET", None)
        os.environ.pop("MODELARK_ASSET_GROUP_ID", None)
        os.environ.pop("FACE_ASSET_SELF_SERVICE", None)
        os.environ.pop("FACE_ASSET_ENFORCE", None)
        os.environ.pop("FACE_ASSET_ALLOWLIST", None)
        os.environ.pop("ASSET_DELETE_EXECUTION_MODE", None)

        if str(PROJECT_DIR) not in sys.path:
            sys.path.insert(0, str(PROJECT_DIR))
        sys.modules.pop("relay_server", None)
        self.server = importlib.import_module("relay_server")

        self.api_key = "sk-upload-test"
        db = self.server.get_db()
        db.execute(
            """INSERT INTO users
               (id, api_key, email, balance_usd, is_active, is_admin, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            ("u_upload", self.api_key, "upload@example.test", 5.0, 1, 0, int(time.time())),
        )
        db.close()
        self.client = TestClient(self.server.app)

    def tearDown(self):
        self.tmp.cleanup()

    def auth_headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    def admin_headers(self):
        return {"X-Admin-Key": "admin-upload-test"}

    def test_upload_image_returns_public_url_and_saved_file(self):
        response = self.client.post(
            "/v1/uploads",
            headers=self.auth_headers(),
            files={"file": ("portrait.jpg", b"\xff\xd8\xff\xe0seedance", "image/jpeg")},
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["content_type"], "image/jpeg")
        self.assertEqual(body["purpose"], "image")
        self.assertTrue(body["url"].startswith("https://media.example.test/uploads/"))
        self.assertEqual(
            body["suggested_content_block"],
            {
                "type": "image_url",
                "image_url": {"url": body["url"]},
                "role": "first_frame",
            },
        )
        saved = Path(os.environ["UPLOAD_DIR"]) / body["object_key"].replace("uploads/", "")
        self.assertTrue(saved.exists())
        self.assertEqual(saved.read_bytes(), b"\xff\xd8\xff\xe0seedance")

        public_response = self.client.get("/" + body["object_key"])
        self.assertEqual(public_response.status_code, 200, public_response.text)
        self.assertEqual(public_response.content, b"\xff\xd8\xff\xe0seedance")

    def test_upload_rejects_non_whitelisted_mime(self):
        response = self.client.post(
            "/v1/uploads",
            headers=self.auth_headers(),
            files={"file": ("notes.txt", b"plain text", "text/plain")},
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "invalid_upload_type")

    def test_upload_rejects_video_format_not_supported_by_seedance(self):
        response = self.client.post(
            "/v1/uploads",
            headers=self.auth_headers(),
            files={"file": ("clip.webm", b"webm", "video/webm")},
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "invalid_upload_type")

    def test_upload_can_auto_register_asset_with_server_side_switch(self):
        self.server.ASSET_AUTO_REGISTER_UPLOADS = True
        self.server.ASSET_AUTO_REGISTER_PURPOSES = {"image", "video", "audio"}
        calls = []

        def fake_register(url, purpose, user=None):
            calls.append((url, purpose))
            return {
                "asset_id": "asset-server-side",
                "asset_url": "asset://asset-server-side",
                "asset_status": "created",
            }

        self.server._register_upload_asset = fake_register

        response = self.client.post(
            "/v1/uploads",
            headers=self.auth_headers(),
            files={"file": ("portrait.jpg", b"\xff\xd8\xff\xe0seedance", "image/jpeg")},
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["asset_url"], "asset://asset-server-side")
        self.assertEqual(body["asset_status"], "created")
        self.assertEqual(calls, [(body["url"], "image")])
        self.assertEqual(
            body["suggested_content_block"],
            {
                "type": "image_url",
                "image_url": {"url": "asset://asset-server-side"},
                "role": "first_frame",
            },
        )

    def test_upload_can_self_service_register_and_whitelist_face_asset(self):
        self.server.FACE_ASSET_SELF_SERVICE = True
        calls = []

        def fake_register(url, purpose, user=None):
            calls.append((url, purpose))
            return {
                "asset_id": "asset-face-self-service",
                "asset_url": "asset://asset-face-self-service",
                "asset_status": "created",
            }

        self.server._register_upload_asset = fake_register

        response = self.client.post(
            "/v1/uploads",
            headers=self.auth_headers(),
            data={
                "face_allowlist": "true",
                "face_asset_label": "customer approved face",
            },
            files={"file": ("portrait.jpg", b"\xff\xd8\xff\xe0seedance", "image/jpeg")},
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["asset_url"], "asset://asset-face-self-service")
        self.assertTrue(body["face_asset_whitelisted"])
        self.assertEqual(calls, [(body["url"], "image")])

        db = self.server.get_db()
        row = db.execute(
            "SELECT asset_url, asset_type, label, is_active FROM face_assets WHERE asset_url=?",
            ("asset://asset-face-self-service",),
        ).fetchone()
        db.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["asset_type"], "image")
        self.assertEqual(row["label"], "customer approved face")
        self.assertEqual(row["is_active"], 1)

    def test_upload_from_url_records_customer_owned_material(self):
        response = self.client.post(
            "/v1/uploads/from-url",
            headers=self.auth_headers(),
            json={
                "url": "https://cdn.example.test/assets/portrait.jpg",
                "content_type": "image/jpeg",
                "original_filename": "remote-portrait.jpg",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["url"], "https://cdn.example.test/assets/portrait.jpg")
        self.assertEqual(body["object_key"], f"external/{body['id']}")
        self.assertEqual(body["purpose"], "image")
        self.assertEqual(body["size_bytes"], 0)
        self.assertEqual(body["original_filename"], "remote-portrait.jpg")
        self.assertEqual(
            body["suggested_content_block"],
            {
                "type": "image_url",
                "image_url": {"url": "https://cdn.example.test/assets/portrait.jpg"},
                "role": "first_frame",
            },
        )

        list_response = self.client.get("/v1/uploads", headers=self.auth_headers())
        self.assertEqual(list_response.status_code, 200, list_response.text)
        self.assertEqual([row["id"] for row in list_response.json()["data"]], [body["id"]])

    def test_upload_from_url_can_self_service_register_and_whitelist_face_asset(self):
        self.server.FACE_ASSET_SELF_SERVICE = True
        calls = []

        def fake_register(url, purpose, user=None):
            calls.append((url, purpose))
            return {
                "asset_id": "asset-url-face",
                "asset_url": "asset://asset-url-face",
                "asset_status": "created",
            }

        self.server._register_upload_asset = fake_register

        response = self.client.post(
            "/v1/uploads/from-url",
            headers=self.auth_headers(),
            json={
                "url": "https://cdn.example.test/assets/portrait.jpg",
                "content_type": "image/jpeg",
                "face_allowlist": True,
                "face_asset_label": "remote face",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["asset_url"], "asset://asset-url-face")
        self.assertTrue(body["face_asset_whitelisted"])
        self.assertEqual(calls, [("https://cdn.example.test/assets/portrait.jpg", "image")])
        self.assertEqual(
            body["suggested_content_block"],
            {
                "type": "image_url",
                "image_url": {"url": "asset://asset-url-face"},
                "role": "reference_image",
            },
        )

    def test_users_can_only_list_and_fetch_their_own_uploads(self):
        other_api_key = "sk-upload-other"
        db = self.server.get_db()
        db.execute(
            """INSERT INTO users
               (id, api_key, email, balance_usd, is_active, is_admin, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            ("u_other", other_api_key, "other-upload@example.test", 5.0, 1, 0, int(time.time())),
        )
        db.close()

        mine = self.client.post(
            "/v1/uploads",
            headers=self.auth_headers(),
            files={"file": ("mine.jpg", b"\xff\xd8\xff\xe0mine", "image/jpeg")},
        )
        other = self.client.post(
            "/v1/uploads",
            headers={"Authorization": f"Bearer {other_api_key}"},
            files={"file": ("other.jpg", b"\xff\xd8\xff\xe0other", "image/jpeg")},
        )
        self.assertEqual(mine.status_code, 200, mine.text)
        self.assertEqual(other.status_code, 200, other.text)

        list_response = self.client.get("/v1/uploads", headers=self.auth_headers())

        self.assertEqual(list_response.status_code, 200, list_response.text)
        rows = list_response.json()["data"]
        self.assertEqual([row["id"] for row in rows], [mine.json()["id"]])
        self.assertEqual(rows[0]["original_filename"], "mine.jpg")

        own_detail = self.client.get(
            f"/v1/uploads/{mine.json()['id']}",
            headers=self.auth_headers(),
        )
        other_detail = self.client.get(
            f"/v1/uploads/{other.json()['id']}",
            headers=self.auth_headers(),
        )

        self.assertEqual(own_detail.status_code, 200, own_detail.text)
        self.assertEqual(own_detail.json()["id"], mine.json()["id"])
        self.assertEqual(other_detail.status_code, 404, other_detail.text)
        self.assertEqual(other_detail.json()["detail"]["error"]["code"], "upload_not_found")

    def test_customer_can_delete_own_local_upload_and_it_is_hidden(self):
        upload = self.client.post(
            "/v1/uploads",
            headers=self.auth_headers(),
            files={"file": ("mine.jpg", b"\xff\xd8\xff\xe0mine", "image/jpeg")},
        )
        self.assertEqual(upload.status_code, 200, upload.text)
        body = upload.json()
        saved = Path(os.environ["UPLOAD_DIR"]) / body["object_key"].replace("uploads/", "")
        self.assertTrue(saved.exists())

        deleted = self.client.delete(
            f"/v1/uploads/{body['id']}",
            headers=self.auth_headers(),
        )

        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertTrue(deleted.json()["ok"])
        self.assertIsNone(deleted.json()["asset_delete_request"])
        self.assertFalse(saved.exists())

        listed = self.client.get("/v1/uploads", headers=self.auth_headers())
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["data"], [])

        fetched = self.client.get(
            f"/v1/uploads/{body['id']}",
            headers=self.auth_headers(),
        )
        self.assertEqual(fetched.status_code, 404, fetched.text)

    def test_customer_delete_asset_upload_creates_idempotent_admin_request(self):
        self.server.ASSET_AUTO_REGISTER_UPLOADS = True
        self.server.ASSET_AUTO_REGISTER_PURPOSES = {"image"}
        self.server.ASSET_DELETE_EXECUTION_MODE = "admin_batch"

        def fake_register(url, purpose, user=None):
            return {
                "asset_id": "asset-delete-me",
                "asset_url": "asset://asset-delete-me",
                "asset_status": "created",
            }

        self.server._register_upload_asset = fake_register
        upload = self.client.post(
            "/v1/uploads",
            headers=self.auth_headers(),
            files={"file": ("mine.jpg", b"\xff\xd8\xff\xe0mine", "image/jpeg")},
        )
        self.assertEqual(upload.status_code, 200, upload.text)
        upload_id = upload.json()["id"]

        first = self.client.delete(f"/v1/uploads/{upload_id}", headers=self.auth_headers())
        second = self.client.delete(f"/v1/uploads/{upload_id}", headers=self.auth_headers())

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        first_req = first.json()["asset_delete_request"]
        second_req = second.json()["asset_delete_request"]
        self.assertEqual(first_req["id"], second_req["id"])
        self.assertEqual(first_req["status"], "pending_admin")
        self.assertEqual(first_req["asset_url"], "asset://asset-delete-me")

        requests = self.client.get(
            "/v1/uploads/delete-requests",
            headers=self.auth_headers(),
        )
        self.assertEqual(requests.status_code, 200, requests.text)
        self.assertEqual(requests.json()["total"], 1)

    def test_auto_asset_delete_executes_byteplus_delete_adapter(self):
        self.server.ASSET_AUTO_REGISTER_UPLOADS = True
        self.server.ASSET_AUTO_REGISTER_PURPOSES = {"image"}
        self.server.ASSET_DELETE_EXECUTION_MODE = "auto"
        deleted_assets = []

        def fake_register(url, purpose, user=None):
            return {
                "asset_id": "asset-auto-delete",
                "asset_url": "asset://asset-auto-delete",
                "asset_status": "created",
            }

        def fake_delete(asset_id, user=None):
            deleted_assets.append((asset_id, user["id"] if user else None))
            return {"RequestId": "req-delete-1"}

        self.server._register_upload_asset = fake_register
        self.server._delete_byteplus_asset = fake_delete
        upload = self.client.post(
            "/v1/uploads",
            headers=self.auth_headers(),
            files={"file": ("mine.jpg", b"\xff\xd8\xff\xe0mine", "image/jpeg")},
        )
        self.assertEqual(upload.status_code, 200, upload.text)

        deleted = self.client.delete(
            f"/v1/uploads/{upload.json()['id']}",
            headers=self.auth_headers(),
        )

        self.assertEqual(deleted.status_code, 200, deleted.text)
        request = deleted.json()["asset_delete_request"]
        self.assertEqual(request["status"], "succeeded")
        self.assertEqual(request["byteplus_request_id"], "req-delete-1")
        self.assertEqual(deleted_assets, [("asset-auto-delete", "u_upload")])

    def test_admin_can_list_all_uploads_and_filter_by_user(self):
        other_api_key = "sk-upload-other"
        db = self.server.get_db()
        db.execute(
            """INSERT INTO users
               (id, api_key, email, balance_usd, is_active, is_admin, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            ("u_other", other_api_key, "other-upload@example.test", 5.0, 1, 0, int(time.time())),
        )
        db.close()

        mine = self.client.post(
            "/v1/uploads",
            headers=self.auth_headers(),
            files={"file": ("mine.jpg", b"\xff\xd8\xff\xe0mine", "image/jpeg")},
        )
        other = self.client.post(
            "/v1/uploads",
            headers={"Authorization": f"Bearer {other_api_key}"},
            files={"file": ("other.mp4", b"\x00\x00\x00\x18ftypmp42", "video/mp4")},
        )
        self.assertEqual(mine.status_code, 200, mine.text)
        self.assertEqual(other.status_code, 200, other.text)

        all_response = self.client.get("/admin/uploads", headers=self.admin_headers())

        self.assertEqual(all_response.status_code, 200, all_response.text)
        all_body = all_response.json()
        self.assertEqual(all_body["total"], 2)
        self.assertEqual(all_body["stats"]["total_uploads"], 2)
        self.assertEqual(all_body["stats"]["total_size_bytes"], mine.json()["size_bytes"] + other.json()["size_bytes"])
        self.assertEqual(
            {row["user_email"] for row in all_body["data"]},
            {"upload@example.test", "other-upload@example.test"},
        )

        filtered = self.client.get(
            "/admin/uploads?user_id=u_upload",
            headers=self.admin_headers(),
        )

        self.assertEqual(filtered.status_code, 200, filtered.text)
        rows = filtered.json()["data"]
        self.assertEqual([row["id"] for row in rows], [mine.json()["id"]])
        self.assertEqual(rows[0]["user_id"], "u_upload")
        self.assertEqual(rows[0]["original_filename"], "mine.jpg")

    def test_admin_user_detail_includes_recent_uploads(self):
        upload = self.client.post(
            "/v1/uploads",
            headers=self.auth_headers(),
            files={"file": ("portrait.jpg", b"\xff\xd8\xff\xe0seedance", "image/jpeg")},
        )
        self.assertEqual(upload.status_code, 200, upload.text)

        detail = self.client.get("/admin/users/u_upload", headers=self.admin_headers())

        self.assertEqual(detail.status_code, 200, detail.text)
        recent_uploads = detail.json()["recent_uploads"]
        self.assertEqual(len(recent_uploads), 1)
        self.assertEqual(recent_uploads[0]["id"], upload.json()["id"])
        self.assertEqual(recent_uploads[0]["original_filename"], "portrait.jpg")

    def test_admin_config_reports_iam_status_without_secret_values(self):
        os.environ["BYTEPLUS_ACCESS_KEY_ID"] = "ak-visible-test"
        os.environ["BYTEPLUS_ACCESS_KEY_SECRET"] = "sk-secret-test"
        os.environ["MODELARK_ASSET_GROUP_ID"] = "group-visible-test"

        response = self.client.get("/admin/config", headers=self.admin_headers())

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["iam"]["storage"], "server_env_or_secret_manager")
        self.assertTrue(body["iam"]["byteplus_access_key_id_configured"])
        self.assertTrue(body["iam"]["byteplus_access_key_secret_configured"])
        self.assertTrue(body["asset_registry"]["modelark_asset_group_id_configured"])
        serialized = response.text
        self.assertNotIn("ak-visible-test", serialized)
        self.assertNotIn("sk-secret-test", serialized)
        self.assertNotIn("group-visible-test", serialized)


if __name__ == "__main__":
    unittest.main()
