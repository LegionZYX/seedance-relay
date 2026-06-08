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
    def __init__(self, payload=None, status_code=200, headers=None):
        self._payload = payload or {"id": "upstream-request-log"}
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeHttp:
    def __init__(self):
        self.post_response = FakeResponse()

    async def post(self, *args, **kwargs):
        return self.post_response


class RequestLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        os.environ["DB_PATH"] = str(root / "relay.sqlite")
        os.environ["VIDEO_DIR"] = str(root / "videos")
        os.environ["UPLOAD_DIR"] = str(root / "uploads")
        os.environ["PUBLIC_DOMAIN"] = "media.example.test"
        os.environ["ADMIN_KEY"] = "admin-request-logs"
        os.environ["ADMIN_PASSWORD"] = ""

        if str(PROJECT_DIR) not in sys.path:
            sys.path.insert(0, str(PROJECT_DIR))
        sys.modules.pop("relay_server", None)
        self.server = importlib.import_module("relay_server")
        self.fake_http = FakeHttp()
        self.server.http = self.fake_http
        self.client = TestClient(self.server.app)

        now = int(time.time())
        db = self.server.get_db()
        db.execute(
            """INSERT INTO users
               (id, api_key, email, balance_usd, is_active, is_admin, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            ("u_log_owner", "sk-log-owner", "logs@example.test", 10.0, 1, 0, now),
        )
        db.close()

    def tearDown(self):
        self.tmp.cleanup()

    def admin_headers(self):
        return {"X-Admin-Key": "admin-request-logs"}

    def auth_headers(self):
        return {"Authorization": "Bearer sk-log-owner"}

    def test_request_logs_table_exists_after_db_init(self):
        db = self.server.get_db()
        try:
            count = db.execute("SELECT COUNT(*) FROM request_logs").fetchone()[0]
        finally:
            db.close()

        self.assertEqual(count, 0)

    def test_admin_can_filter_request_logs(self):
        now = int(time.time())
        db = self.server.get_db()
        db.execute(
            """CREATE TABLE IF NOT EXISTS request_logs (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                task_id TEXT,
                route TEXT,
                action TEXT,
                model TEXT,
                prompt_text TEXT,
                request_payload TEXT,
                status_code INTEGER,
                error_code TEXT,
                upstream_request_id TEXT,
                ip TEXT,
                user_agent TEXT,
                created_at INTEGER NOT NULL
            )"""
        )
        db.execute(
            """INSERT INTO request_logs
               (id, user_id, task_id, route, action, model, prompt_text,
                request_payload, status_code, error_code, upstream_request_id,
                ip, user_agent, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "log_success",
                "u_log_owner",
                "vid_logged",
                "/v1/videos",
                "video_create_success",
                "dreamina-seedance-2-0-260128",
                "logged prompt",
                '{"model":"dreamina-seedance-2-0-260128"}',
                200,
                "",
                "req-success",
                "203.0.113.10",
                "pytest",
                now,
            ),
        )
        db.execute(
            """INSERT INTO request_logs
               (id, user_id, task_id, route, action, model, prompt_text,
                request_payload, status_code, error_code, upstream_request_id,
                ip, user_agent, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "log_other",
                "u_other",
                "vid_other",
                "/v1/videos",
                "video_create_failed",
                "dreamina-seedance-2-0-fast-260128",
                "other prompt",
                "{}",
                502,
                "upstream_error",
                "req-failed",
                "203.0.113.20",
                "pytest",
                now - 1,
            ),
        )
        db.close()

        response = self.client.get(
            "/admin/request-logs?user_id=u_log_owner&action=video_create_success",
            headers=self.admin_headers(),
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["limit"], 100)
        self.assertEqual(body["offset"], 0)
        row = body["data"][0]
        self.assertEqual(row["id"], "log_success")
        self.assertEqual(row["user_email"], "logs@example.test")
        self.assertEqual(row["task_id"], "vid_logged")
        self.assertEqual(row["action"], "video_create_success")
        self.assertEqual(row["prompt_text"], "logged prompt")
        self.assertEqual(row["upstream_request_id"], "req-success")

    def test_customer_cannot_read_request_logs(self):
        response = self.client.get("/admin/request-logs", headers=self.auth_headers())

        self.assertEqual(response.status_code, 403)

    def test_fastapi_create_success_writes_request_log(self):
        response = self.client.post(
            "/v1/videos",
            headers=self.auth_headers(),
            json={
                "model": "dreamina-seedance-2-0-260128",
                "content": [{"type": "text", "text": "fallback success prompt"}],
                "resolution": "480p",
                "ratio": "16:9",
                "duration": 5,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        db = self.server.get_db()
        row = db.execute(
            "SELECT * FROM request_logs WHERE user_id=? AND action=?",
            ("u_log_owner", "video_create_success"),
        ).fetchone()
        db.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["task_id"], response.json()["id"])
        self.assertEqual(row["model"], "dreamina-seedance-2-0-260128")
        self.assertEqual(row["prompt_text"], "fallback success prompt")
        self.assertEqual(row["status_code"], 200)

    def test_fastapi_create_upstream_failure_writes_request_log(self):
        self.fake_http.post_response = FakeResponse(
            status_code=400,
            headers={"x-request-id": "req-fallback-failed"},
        )

        response = self.client.post(
            "/v1/videos",
            headers=self.auth_headers(),
            json={
                "model": "dreamina-seedance-2-0-260128",
                "content": [{"type": "text", "text": "fallback failure prompt"}],
                "resolution": "480p",
                "ratio": "16:9",
                "duration": 5,
            },
        )

        self.assertEqual(response.status_code, 502, response.text)
        db = self.server.get_db()
        row = db.execute(
            "SELECT * FROM request_logs WHERE user_id=? AND action=?",
            ("u_log_owner", "video_create_failed"),
        ).fetchone()
        db.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["error_code"], "upstream_error")
        self.assertEqual(row["upstream_request_id"], "req-fallback-failed")
        self.assertEqual(row["status_code"], 502)

    def test_fastapi_create_rejected_writes_request_log(self):
        db = self.server.get_db()
        db.execute("UPDATE users SET balance_usd=0.01 WHERE id=?", ("u_log_owner",))
        db.close()

        response = self.client.post(
            "/v1/videos",
            headers=self.auth_headers(),
            json={
                "model": "dreamina-seedance-2-0-260128",
                "content": [{"type": "text", "text": "fallback rejected prompt"}],
                "resolution": "480p",
                "ratio": "16:9",
                "duration": 5,
            },
        )

        self.assertEqual(response.status_code, 402, response.text)
        db = self.server.get_db()
        row = db.execute(
            "SELECT * FROM request_logs WHERE user_id=? AND action=?",
            ("u_log_owner", "video_create_rejected"),
        ).fetchone()
        db.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["error_code"], "insufficient_balance")
        self.assertEqual(row["status_code"], 402)


if __name__ == "__main__":
    unittest.main()
