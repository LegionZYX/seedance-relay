import importlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


PROJECT_DIR = Path(__file__).resolve().parents[1]


class CustomerTaskHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        os.environ["DB_PATH"] = str(root / "relay.sqlite")
        os.environ["VIDEO_DIR"] = str(root / "videos")
        os.environ["UPLOAD_DIR"] = str(root / "uploads")
        os.environ["PUBLIC_DOMAIN"] = "media.example.test"
        os.environ["ADMIN_PASSWORD"] = ""
        os.environ["ADMIN_KEY"] = "admin-history"
        os.environ["UPSTREAM_API_KEY"] = "ark-test-upstream"
        os.environ["VIDEO_PERSIST_MODE"] = "proxy_only"

        if str(PROJECT_DIR) not in sys.path:
            sys.path.insert(0, str(PROJECT_DIR))
        sys.modules.pop("relay_server", None)
        self.server = importlib.import_module("relay_server")
        self.client = TestClient(self.server.app)

    def tearDown(self):
        self.tmp.cleanup()

    def admin_headers(self):
        return {"X-Admin-Key": "admin-history"}

    def auth_headers(self, api_key):
        return {"Authorization": f"Bearer {api_key}"}

    def create_user(self):
        response = self.client.post(
            "/admin/users",
            headers=self.admin_headers(),
            json={
                "email": "history@example.test",
                "balance_usd": 100,
                "password": "initial-password",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def insert_task(self, *, user_id, task_id, status, created_at):
        db = self.server.get_db()
        db.execute(
            """INSERT INTO tasks
               (id, user_id, upstream_task_id, upstream_model, client_model,
                resolution, duration, has_video_ref, status,
                estimated_cost_usd, held_usd, price_multiplier,
                settled, prompt_text, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task_id,
                user_id,
                f"upstream-{task_id}",
                "dreamina-seedance-2-0-260128",
                "dreamina-seedance-2-0-260128",
                "480p",
                5,
                0,
                status,
                0.1,
                0.1,
                1.0,
                1 if status in {"succeeded", "failed", "cancelled", "expired"} else 0,
                f"prompt for {task_id}",
                created_at,
                created_at,
            ),
        )
        db.close()

    def test_clear_history_hides_terminal_tasks_but_keeps_running_and_db_rows(self):
        user = self.create_user()
        now = int(time.time())
        self.insert_task(user_id=user["id"], task_id="vid_done", status="succeeded", created_at=now - 30)
        self.insert_task(user_id=user["id"], task_id="vid_failed", status="failed", created_at=now - 20)
        self.insert_task(user_id=user["id"], task_id="vid_running", status="running", created_at=now - 10)

        before = self.client.get("/v1/videos", headers=self.auth_headers(user["api_key"]))
        self.assertEqual(before.status_code, 200, before.text)
        self.assertEqual({row["id"] for row in before.json()["data"]}, {"vid_done", "vid_failed", "vid_running"})

        cleared = self.client.delete("/v1/videos/history", headers=self.auth_headers(user["api_key"]))
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertEqual(cleared.json()["hidden"], 2)
        self.assertEqual(cleared.json()["kept_active"], 1)

        after = self.client.get("/v1/videos", headers=self.auth_headers(user["api_key"]))
        self.assertEqual(after.status_code, 200, after.text)
        self.assertEqual([row["id"] for row in after.json()["data"]], ["vid_running"])

        db = self.server.get_db()
        counts = db.execute(
            """SELECT
                  COUNT(*) AS total,
                  SUM(CASE WHEN customer_hidden_at IS NOT NULL THEN 1 ELSE 0 END) AS hidden
               FROM tasks WHERE user_id=?""",
            (user["id"],),
        ).fetchone()
        db.close()
        self.assertEqual(counts["total"], 3)
        self.assertEqual(counts["hidden"], 2)

