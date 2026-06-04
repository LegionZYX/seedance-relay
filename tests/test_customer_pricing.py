import importlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


PROJECT_DIR = Path(__file__).resolve().parents[1]


class FakeResponse:
    status_code = 200
    text = '{"id":"upstream-task"}'

    def json(self):
        return {"id": "upstream-task"}


class FakeHttp:
    def __init__(self):
        self.posts = []

    async def post(self, url, json, headers, timeout):
        self.posts.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return FakeResponse()


class CustomerPricingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        os.environ["DB_PATH"] = str(root / "relay.sqlite")
        os.environ["VIDEO_DIR"] = str(root / "videos")
        os.environ["UPLOAD_DIR"] = str(root / "uploads")
        os.environ["PUBLIC_DOMAIN"] = "media.example.test"
        os.environ["ADMIN_PASSWORD"] = ""
        os.environ["ADMIN_KEY"] = "admin-pricing"
        os.environ["UPSTREAM_API_KEY"] = "ark-test-upstream"
        os.environ["MARKUP_PCT"] = "0.30"

        if str(PROJECT_DIR) not in sys.path:
            sys.path.insert(0, str(PROJECT_DIR))
        sys.modules.pop("relay_server", None)
        self.server = importlib.import_module("relay_server")
        self.fake_http = FakeHttp()
        self.server.http = self.fake_http
        self.client = TestClient(self.server.app)

    def tearDown(self):
        self.tmp.cleanup()

    def admin_headers(self):
        return {"X-Admin-Key": "admin-pricing"}

    def video_payload(self):
        return {
            "model": "dreamina-seedance-2-0-260128",
            "content": [{"type": "text", "text": "A quiet street walk."}],
            "resolution": "480p",
            "duration": 5,
            "generate_audio": False,
        }

    def create_user(self, email, markup_pct=None):
        body = {
            "email": email,
            "balance_usd": 100.0,
            "password": "test-password",
        }
        if markup_pct is not None:
            body["markup_pct"] = markup_pct
        response = self.client.post("/admin/users", headers=self.admin_headers(), json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_native_content_payload_uses_global_generation_key_without_iam(self):
        user = self.create_user("native-global@example.test")

        response = self.client.post(
            "/v1/videos",
            headers={"Authorization": f"Bearer {user['api_key']}"},
            json={
                "model": "dreamina-seedance-2-0-260128",
                "content": [
                    {"type": "text", "text": "A person walks through a small street."},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://cdn.example.test/first.jpg"},
                        "role": "first_frame",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://cdn.example.test/last.jpg"},
                        "role": "last_frame",
                    },
                ],
                "ratio": "9:16",
                "duration": 5,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        upstream = self.fake_http.posts[-1]
        self.assertEqual(upstream["headers"]["Authorization"], "Bearer ark-test-upstream")
        self.assertEqual(upstream["json"]["model"], "dreamina-seedance-2-0-260128")
        self.assertEqual(upstream["json"]["ratio"], "9:16")
        self.assertEqual(
            upstream["json"]["content"],
            [
                {"type": "text", "text": "A person walks through a small street."},
                {
                    "type": "image_url",
                    "image_url": {"url": "https://cdn.example.test/first.jpg"},
                    "role": "first_frame",
                },
                {
                    "type": "image_url",
                    "image_url": {"url": "https://cdn.example.test/last.jpg"},
                    "role": "last_frame",
                },
            ],
        )

    def test_customer_upstream_key_is_separate_from_iam_asset_keys(self):
        created = self.client.post(
            "/admin/users",
            headers=self.admin_headers(),
            json={
                "email": "own-key@example.test",
                "balance_usd": 100.0,
                "password": "test-password",
                "byteplus_api_key": "ark-customer-own-key",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)

        response = self.client.post(
            "/v1/videos",
            headers={"Authorization": f"Bearer {created.json()['api_key']}"},
            json={
                "model": "dreamina-seedance-2-0-fast-260128",
                "content": [
                    {"type": "text", "text": "A product shot with reference materials."},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://cdn.example.test/ref-1.jpg"},
                        "role": "reference_image",
                    },
                    {
                        "type": "video_url",
                        "video_url": {"url": "https://cdn.example.test/ref.mp4"},
                        "role": "reference_video",
                    },
                    {
                        "type": "audio_url",
                        "audio_url": {"url": "https://cdn.example.test/ref.mp3"},
                        "role": "reference_audio",
                    },
                ],
                "duration": 5,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        upstream = self.fake_http.posts[-1]
        self.assertEqual(upstream["headers"]["Authorization"], "Bearer ark-customer-own-key")
        self.assertEqual(upstream["json"]["model"], "dreamina-seedance-2-0-fast-260128")
        self.assertEqual(
            [block["type"] for block in upstream["json"]["content"]],
            ["text", "image_url", "video_url", "audio_url"],
        )

    def test_real_person_mode_registers_reference_media_with_server_iam_only(self):
        self.server.FACE_ASSET_SELF_SERVICE = True
        calls = []

        def fake_register(url, purpose):
            calls.append((url, purpose))
            return {
                "asset_id": f"asset-{purpose}",
                "asset_url": f"asset://asset-{purpose}",
                "asset_status": "created",
            }

        self.server._register_upload_asset = fake_register
        user = self.create_user("real-person@example.test")

        response = self.client.post(
            "/v1/videos",
            headers={"Authorization": f"Bearer {user['api_key']}"},
            json={
                "model": "dreamina-seedance-2-0-260128",
                "content": [
                    {"type": "text", "text": "The portrait walks through a small street."},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://cdn.example.test/person.jpg"},
                        "role": "reference_image",
                    },
                ],
                "extra_body": {"real_person_mode": True},
                "duration": 5,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(calls, [("https://cdn.example.test/person.jpg", "image")])
        upstream = self.fake_http.posts[-1]
        self.assertEqual(
            upstream["json"]["content"][1],
            {
                "type": "image_url",
                "image_url": {"url": "asset://asset-image"},
                "role": "reference_image",
            },
        )

        db = self.server.get_db()
        upload = db.execute(
            "SELECT user_id, url, asset_url, face_asset_whitelisted FROM uploads WHERE user_id=?",
            (user["id"],),
        ).fetchone()
        db.close()
        self.assertIsNotNone(upload)
        self.assertEqual(upload["url"], "https://cdn.example.test/person.jpg")
        self.assertEqual(upload["asset_url"], "asset://asset-image")
        self.assertEqual(upload["face_asset_whitelisted"], 1)

    def test_prompt_only_payload_does_not_replace_native_content_blocks(self):
        user = self.create_user("prompt-only@example.test")

        response = self.client.post(
            "/v1/videos",
            headers={"Authorization": f"Bearer {user['api_key']}"},
            json={
                "model": "dreamina-seedance-2-0-260128",
                "prompt": "A portrait walks through a small street.",
                "duration": 5,
            },
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.fake_http.posts, [])

    def test_customer_cannot_use_another_customers_uploaded_asset_id(self):
        owner = self.create_user("asset-owner@example.test")
        caller = self.create_user("asset-caller@example.test")
        now = int(time.time())
        db = self.server.get_db()
        db.execute(
            """INSERT INTO uploads
               (id, user_id, url, object_key, content_type, size_bytes, purpose,
                original_filename, asset_id, asset_url, asset_status,
                face_asset_whitelisted, face_asset_label, face_asset_note,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "upl_owned_asset",
                owner["id"],
                "https://cdn.example.test/owned.jpg",
                "external/upl_owned_asset",
                "image/jpeg",
                0,
                "image",
                "owned.jpg",
                "asset-owned-by-other",
                "asset://asset-owned-by-other",
                "Active",
                1,
                "owner face",
                "",
                now,
                now,
            ),
        )
        db.close()

        response = self.client.post(
            "/v1/videos",
            headers={"Authorization": f"Bearer {caller['api_key']}"},
            json={
                "model": "dreamina-seedance-2-0-260128",
                "content": [
                    {"type": "text", "text": "Use the uploaded portrait."},
                    {
                        "type": "image_url",
                        "image_url": {"url": "asset://asset-owned-by-other"},
                        "role": "reference_image",
                    },
                ],
                "duration": 5,
            },
        )

        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "asset_not_owned")
        self.assertEqual(self.fake_http.posts, [])

    def test_admin_can_create_and_update_customer_markup(self):
        created = self.create_user("vip@example.test", markup_pct=0.8)
        self.assertEqual(created["markup_pct"], 0.8)

        detail = self.client.get(f"/admin/users/{created['id']}", headers=self.admin_headers())
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["markup_pct"], 0.8)

        patched = self.client.patch(
            f"/admin/users/{created['id']}",
            headers=self.admin_headers(),
            json={"markup_pct": 0.15},
        )
        self.assertEqual(patched.status_code, 200, patched.text)

        updated = self.client.get(f"/admin/users/{created['id']}", headers=self.admin_headers())
        self.assertEqual(updated.json()["markup_pct"], 0.15)

    def test_estimate_uses_customer_specific_markup(self):
        standard = self.create_user("standard@example.test")
        vip = self.create_user("vip@example.test", markup_pct=0.8)

        standard_estimate = self.client.post(
            "/v1/videos/estimate",
            headers={"Authorization": f"Bearer {standard['api_key']}"},
            json=self.video_payload(),
        )
        vip_estimate = self.client.post(
            "/v1/videos/estimate",
            headers={"Authorization": f"Bearer {vip['api_key']}"},
            json=self.video_payload(),
        )

        self.assertEqual(standard_estimate.status_code, 200, standard_estimate.text)
        self.assertEqual(vip_estimate.status_code, 200, vip_estimate.text)
        self.assertEqual(standard_estimate.json()["markup_pct"], 0.3)
        self.assertEqual(vip_estimate.json()["markup_pct"], 0.8)
        self.assertGreater(vip_estimate.json()["max_cost_usd"], standard_estimate.json()["max_cost_usd"])

    def test_video_task_snapshots_customer_markup_at_create_time(self):
        user = self.create_user("snapshot@example.test", markup_pct=0.8)

        response = self.client.post(
            "/v1/videos",
            headers={"Authorization": f"Bearer {user['api_key']}"},
            json=self.video_payload(),
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["markup_pct"], 0.8)

        db = self.server.get_db()
        row = db.execute("SELECT markup_pct, held_usd FROM tasks WHERE id=?", (response.json()["id"],)).fetchone()
        db.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["markup_pct"], 0.8)
        self.assertEqual(row["held_usd"], response.json()["held_usd"])


if __name__ == "__main__":
    unittest.main()
