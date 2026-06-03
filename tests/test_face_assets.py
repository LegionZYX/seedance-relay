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


class FaceAssetWhitelistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        os.environ["DB_PATH"] = str(root / "relay.sqlite")
        os.environ["VIDEO_DIR"] = str(root / "videos")
        os.environ["UPLOAD_DIR"] = str(root / "uploads")
        os.environ["PUBLIC_DOMAIN"] = "media.example.test"
        os.environ["ADMIN_PASSWORD"] = ""
        os.environ["ADMIN_KEY"] = "admin-test"
        os.environ["UPSTREAM_API_KEY"] = "ark-test-upstream"
        os.environ["FACE_ASSET_ENFORCE"] = "true"
        os.environ["FACE_ASSET_ALLOWLIST"] = "asset://asset-approved"

        if str(PROJECT_DIR) not in sys.path:
            sys.path.insert(0, str(PROJECT_DIR))
        sys.modules.pop("relay_server", None)
        self.server = importlib.import_module("relay_server")
        self.fake_http = FakeHttp()
        self.server.http = self.fake_http

        self.api_key = "sk-face-test"
        db = self.server.get_db()
        db.execute(
            """INSERT INTO users
               (id, api_key, email, balance_usd, is_active, is_admin, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            ("u_face", self.api_key, "face@example.test", 100.0, 1, 0, int(time.time())),
        )
        db.close()
        self.client = TestClient(self.server.app)

    def tearDown(self):
        self.tmp.cleanup()

    def auth_headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    def video_payload(self, image_url):
        return {
            "model": "video-pro",
            "content": [
                {"type": "text", "text": "Use Image 1 as a character reference."},
                {
                    "type": "image_url",
                    "image_url": {"url": image_url},
                    "role": "reference_image",
                },
            ],
            "resolution": "480p",
            "duration": 5,
            "generate_audio": False,
        }

    def test_rejects_reference_face_url_that_is_not_asset_uri(self):
        response = self.client.post(
            "/v1/videos",
            headers=self.auth_headers(),
            json=self.video_payload("https://media.example.test/uploads/face.jpg"),
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "face_asset_requires_asset_uri")
        self.assertEqual(self.fake_http.posts, [])

    def test_rejects_reference_face_asset_not_in_allowlist(self):
        response = self.client.post(
            "/v1/videos",
            headers=self.auth_headers(),
            json=self.video_payload("asset://asset-unapproved"),
        )

        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "face_asset_not_whitelisted")
        self.assertEqual(self.fake_http.posts, [])

    def test_allows_whitelisted_reference_face_asset(self):
        response = self.client.post(
            "/v1/videos",
            headers=self.auth_headers(),
            json=self.video_payload("asset://asset-approved"),
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["id"], "vid_" + response.json()["id"][4:])
        self.assertEqual(len(self.fake_http.posts), 1)
        sent = self.fake_http.posts[0]["json"]["content"][1]
        self.assertEqual(sent["image_url"]["url"], "asset://asset-approved")

    def test_admin_can_add_face_asset_to_server_whitelist(self):
        admin_response = self.client.post(
            "/admin/face-assets",
            headers={"X-Admin-Key": "admin-test"},
            json={
                "asset_url": "asset://asset-db-approved",
                "asset_type": "image",
                "label": "approved actor",
            },
        )
        self.assertEqual(admin_response.status_code, 200, admin_response.text)

        response = self.client.post(
            "/v1/videos",
            headers=self.auth_headers(),
            json=self.video_payload("asset://asset-db-approved"),
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.fake_http.posts), 1)


if __name__ == "__main__":
    unittest.main()
