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

    def __init__(self, payload=None):
        self._payload = payload or {}
        self.headers = {}

    def json(self):
        return self._payload


class FakeStreamResponse:
    status_code = 206
    headers = {
        "content-type": "video/mp4",
        "content-length": "3",
        "content-range": "bytes 3-5/10",
        "accept-ranges": "bytes",
    }

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_bytes(self, chunk_size=65536):
        yield b"345"


class FakeHttp:
    def __init__(self):
        self.get_payload = None
        self.gets = []
        self.streams = []

    async def get(self, url, headers, timeout):
        self.gets.append({"url": url, "headers": headers, "timeout": timeout})
        return FakeResponse(self.get_payload)

    def stream(self, method, url, **kwargs):
        self.streams.append({"method": method, "url": url, **kwargs})
        return FakeStreamResponse()


class AdminTaskVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        os.environ["DB_PATH"] = str(root / "relay.sqlite")
        os.environ["VIDEO_DIR"] = str(root / "videos")
        os.environ["UPLOAD_DIR"] = str(root / "uploads")
        os.environ["PUBLIC_DOMAIN"] = "media.example.test"
        os.environ["PUBLIC_BASE_URL"] = "https://media.example.test"
        os.environ["ADMIN_KEY"] = "admin-task-visibility"
        os.environ["ADMIN_PASSWORD"] = ""

        if str(PROJECT_DIR) not in sys.path:
            sys.path.insert(0, str(PROJECT_DIR))
        sys.modules.pop("relay_server", None)
        self.server = importlib.import_module("relay_server")
        self.fake_http = FakeHttp()
        self.server.http = self.fake_http
        self.client = TestClient(self.server.app)

        self.user_id = "u_task_visibility"
        self.api_key = "sk-task-visibility"
        now = int(time.time())
        db = self.server.get_db()
        db.execute(
            """INSERT INTO users
               (id, api_key, email, balance_usd, is_active, is_admin, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (self.user_id, self.api_key, "task-owner@example.test", 50.0, 1, 0, now),
        )
        db.execute(
            """INSERT INTO tasks
               (id, user_id, upstream_task_id, upstream_model, client_model,
                resolution, duration, has_video_ref, status,
                estimated_cost_usd, held_usd, actual_cost_usd,
                upstream_actual_cost_usd, markup_pct, price_multiplier,
                completion_tokens, settled, cached_video_url, cached_video_url_until,
                prompt_text, request_payload, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "vid_admin_detail",
                self.user_id,
                "cgt-admin-detail",
                "ep-seedance-2",
                "dreamina-seedance-2-0-260128",
                "720p",
                5,
                1,
                "succeeded",
                0.12,
                0.18,
                0.13,
                0.08,
                0.3,
                1.3,
                12345,
                1,
                "https://byteplus.example.test/result.mp4",
                now + 3600,
                "A clean product reveal video",
                '{"model":"ep-seedance-2","content":[{"type":"text","text":"A clean product reveal video"}]}',
                now - 10,
                now,
            ),
        )
        db.close()

    def tearDown(self):
        self.tmp.cleanup()

    def admin_headers(self):
        return {"X-Admin-Key": "admin-task-visibility"}

    def auth_headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    def test_admin_can_read_customer_task_detail(self):
        response = self.client.get("/admin/tasks/vid_admin_detail", headers=self.admin_headers())

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["id"], "vid_admin_detail")
        self.assertEqual(body["user_id"], self.user_id)
        self.assertEqual(body["user_email"], "task-owner@example.test")
        self.assertEqual(body["upstream_task_id"], "cgt-admin-detail")
        self.assertEqual(body["upstream_model"], "ep-seedance-2")
        self.assertEqual(body["client_model"], "dreamina-seedance-2-0-260128")
        self.assertEqual(body["prompt_text"], "A clean product reveal video")
        self.assertIn('"model":"ep-seedance-2"', body["request_payload"])
        self.assertEqual(body["estimated_cost_usd"], 0.12)
        self.assertEqual(body["held_usd"], 0.18)
        self.assertEqual(body["actual_cost_usd"], 0.13)
        self.assertEqual(body["upstream_actual_cost_usd"], 0.08)
        self.assertEqual(body["completion_tokens"], 12345)
        self.assertEqual(body["admin_content_url"], "/admin/tasks/vid_admin_detail/content")

    def test_customer_cannot_read_admin_task_detail(self):
        response = self.client.get("/admin/tasks/vid_admin_detail", headers=self.auth_headers())

        self.assertEqual(response.status_code, 403)

    def test_admin_content_returns_local_video_file(self):
        video_path = Path(self.tmp.name) / "videos" / "admin-local.mp4"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"fake-mp4-bytes")
        db = self.server.get_db()
        db.execute(
            "UPDATE tasks SET local_video_path=? WHERE id=?",
            (str(video_path), "vid_admin_detail"),
        )
        db.close()

        response = self.client.get(
            "/admin/tasks/vid_admin_detail/content",
            headers=self.admin_headers(),
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, b"fake-mp4-bytes")
        self.assertEqual(response.headers["content-type"], "video/mp4")

    def test_admin_content_rejects_not_ready_task(self):
        db = self.server.get_db()
        db.execute(
            "UPDATE tasks SET status=?, settled=?, cached_video_url=NULL WHERE id=?",
            ("running", 0, "vid_admin_detail"),
        )
        db.close()

        response = self.client.get(
            "/admin/tasks/vid_admin_detail/content",
            headers=self.admin_headers(),
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["error"]["code"], "not_ready")

    def test_admin_content_reports_missing_video_url(self):
        db = self.server.get_db()
        db.execute(
            "UPDATE tasks SET status=?, cached_video_url=NULL, local_video_path=NULL WHERE id=?",
            ("succeeded", "vid_admin_detail"),
        )
        db.close()

        response = self.client.get(
            "/admin/tasks/vid_admin_detail/content",
            headers=self.admin_headers(),
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["error"]["code"], "video_unavailable")

    def test_admin_content_refreshes_expired_cached_url_before_proxying(self):
        db = self.server.get_db()
        db.execute(
            "UPDATE tasks SET cached_video_url=?, cached_video_url_until=? WHERE id=?",
            ("https://byteplus.example.test/expired.mp4", 1, "vid_admin_detail"),
        )
        db.close()
        self.fake_http.get_payload = {
            "id": "cgt-admin-detail",
            "status": "succeeded",
            "content": {"video_url": "https://byteplus.example.test/fresh-admin.mp4"},
        }

        response = self.client.get(
            "/admin/tasks/vid_admin_detail/content",
            headers={**self.admin_headers(), "Range": "bytes=3-5"},
        )

        self.assertEqual(response.status_code, 206, response.text)
        self.assertEqual(response.content, b"345")
        self.assertEqual(self.fake_http.streams[-1]["url"], "https://byteplus.example.test/fresh-admin.mp4")
        db = self.server.get_db()
        row = db.execute(
            "SELECT cached_video_url, cached_video_url_until FROM tasks WHERE id=?",
            ("vid_admin_detail",),
        ).fetchone()
        db.close()
        self.assertEqual(row["cached_video_url"], "https://byteplus.example.test/fresh-admin.mp4")
        self.assertGreater(row["cached_video_url_until"], int(time.time()))


if __name__ == "__main__":
    unittest.main()
