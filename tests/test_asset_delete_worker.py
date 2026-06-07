import importlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


class AssetDeleteWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        os.environ["DB_PATH"] = str(root / "relay.sqlite")
        os.environ["VIDEO_DIR"] = str(root / "videos")
        os.environ["UPLOAD_DIR"] = str(root / "uploads")
        os.environ["ADMIN_PASSWORD"] = ""

        if str(PROJECT_DIR) not in sys.path:
            sys.path.insert(0, str(PROJECT_DIR))
        sys.modules.pop("relay_server", None)
        sys.modules.pop("deploy.process_asset_delete_requests", None)
        self.server = importlib.import_module("relay_server")
        self.worker = importlib.import_module("deploy.process_asset_delete_requests")

    def tearDown(self):
        self.tmp.cleanup()

    def test_worker_executes_queued_requests_and_records_audit(self):
        now = int(time.time())
        db = self.server.get_db()
        db.execute(
            """INSERT INTO users
               (id, api_key, email, balance_usd, is_active, is_admin, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            ("u_worker", "sk-worker", "worker@example.test", 10.0, 0, 0, now),
        )
        db.execute(
            """INSERT INTO uploads
               (id, user_id, url, object_key, content_type, size_bytes, purpose,
                asset_id, asset_url, asset_status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "upl_worker",
                "u_worker",
                "https://media.example.test/uploads/worker.jpg",
                "uploads/2026/06/07/upl_worker.jpg",
                "image/jpeg",
                128,
                "image",
                "asset-worker",
                "asset://asset-worker",
                "created",
                now,
                now,
            ),
        )
        db.execute(
            """INSERT INTO asset_delete_requests
               (id, user_id, upload_id, asset_id, asset_url, status,
                requested_by_user_id, execution_mode, reason, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "adr_worker",
                "u_worker",
                "upl_worker",
                "asset-worker",
                "asset://asset-worker",
                "queued",
                "u_worker",
                "auto",
                "customer_deleted_upload",
                now,
                now,
            ),
        )
        db.close()
        deleted = []

        def fake_delete(asset_id, user=None):
            deleted.append({"asset_id": asset_id, "user_id": user["id"] if user else None})
            return {"RequestId": "req-worker-delete"}

        self.server._delete_byteplus_asset = fake_delete

        report = self.worker.process_asset_delete_requests(limit=10)

        self.assertEqual(report["processed"], 1, json.dumps(report, sort_keys=True))
        self.assertEqual(report["succeeded"], 1)
        self.assertEqual(report["failed"], 0)
        self.assertEqual(deleted, [{"asset_id": "asset-worker", "user_id": "u_worker"}])
        db = self.server.get_db()
        row = db.execute(
            "SELECT status, byteplus_request_id FROM asset_delete_requests WHERE id='adr_worker'"
        ).fetchone()
        audit = db.execute(
            "SELECT action FROM audit_events WHERE target_id='adr_worker' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        db.close()
        self.assertEqual(row["status"], "succeeded")
        self.assertEqual(row["byteplus_request_id"], "req-worker-delete")
        self.assertEqual(audit["action"], "system_deleted_byteplus_asset")


if __name__ == "__main__":
    unittest.main()
