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


class SelfServiceFaceFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        os.environ["DB_PATH"] = str(root / "relay.sqlite")
        os.environ["VIDEO_DIR"] = str(root / "videos")
        os.environ["UPLOAD_DIR"] = str(root / "uploads")
        os.environ["PUBLIC_DOMAIN"] = "relay.example.test"
        os.environ["UPSTREAM_API_KEY"] = "ark-test-upstream"
        os.environ["ADMIN_PASSWORD"] = ""
        os.environ["FACE_ASSET_ENFORCE"] = "true"
        os.environ["FACE_ASSET_SELF_SERVICE"] = "true"
        os.environ.pop("FACE_ASSET_ALLOWLIST", None)

        if str(PROJECT_DIR) not in sys.path:
            sys.path.insert(0, str(PROJECT_DIR))
        sys.modules.pop("relay_server", None)
        self.server = importlib.import_module("relay_server")
        self.fake_http = FakeHttp()
        self.server.http = self.fake_http

        def fake_register(url, purpose):
            return {
                "asset_id": "asset-self-face",
                "asset_url": "asset://asset-self-face",
                "asset_status": "created",
            }

        self.server._register_upload_asset = fake_register

        self.api_key = "sk-self-service"
        db = self.server.get_db()
        db.execute(
            """INSERT INTO users
               (id, api_key, email, balance_usd, is_active, is_admin, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            ("u_self", self.api_key, "self@example.test", 100.0, 1, 0, int(time.time())),
        )
        db.close()
        self.client = TestClient(self.server.app)

    def tearDown(self):
        self.tmp.cleanup()

    def auth_headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    def test_one_upload_returns_whitelisted_face_asset_for_generation(self):
        upload = self.client.post(
            "/v1/uploads",
            headers=self.auth_headers(),
            data={"face_allowlist": "true", "face_asset_label": "actor-a"},
            files={"file": ("face.jpg", b"\xff\xd8\xff\xe0face", "image/jpeg")},
        )
        self.assertEqual(upload.status_code, 200, upload.text)
        upload_body = upload.json()
        self.assertTrue(upload_body["face_asset_whitelisted"])
        self.assertEqual(upload_body["asset_url"], "asset://asset-self-face")
        self.assertEqual(
            upload_body["suggested_content_block"],
            {
                "type": "image_url",
                "image_url": {"url": "asset://asset-self-face"},
                "role": "reference_image",
            },
        )

        create = self.client.post(
            "/v1/videos",
            headers=self.auth_headers(),
            json={
                "model": "video-pro",
                "content": [
                    {"type": "text", "text": "Use Image 1 as the face identity reference."},
                    upload_body["suggested_content_block"],
                ],
                "resolution": "480p",
                "duration": 5,
                "generate_audio": False,
            },
        )

        self.assertEqual(create.status_code, 200, create.text)
        self.assertEqual(len(self.fake_http.posts), 1)
        sent_block = self.fake_http.posts[0]["json"]["content"][1]
        self.assertEqual(sent_block["role"], "reference_image")
        self.assertEqual(sent_block["image_url"]["url"], "asset://asset-self-face")


if __name__ == "__main__":
    unittest.main()
