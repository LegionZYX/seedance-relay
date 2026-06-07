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


class UploadRetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        os.environ["DB_PATH"] = str(root / "relay.sqlite")
        os.environ["VIDEO_DIR"] = str(root / "videos")
        os.environ["UPLOAD_DIR"] = str(root / "uploads")
        os.environ["PUBLIC_DOMAIN"] = "media.example.test"
        os.environ["ADMIN_PASSWORD"] = ""

        if str(PROJECT_DIR) not in sys.path:
            sys.path.insert(0, str(PROJECT_DIR))
        sys.modules.pop("relay_server", None)
        sys.modules.pop("deploy.cleanup_upload_files", None)
        self.server = importlib.import_module("relay_server")
        self.cleanup = importlib.import_module("deploy.cleanup_upload_files")
        self.client = TestClient(self.server.app)
        self.api_key = "sk-retention"
        db = self.server.get_db()
        db.execute(
            """INSERT INTO users
               (id, api_key, email, balance_usd, is_active, is_admin, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            ("u_retention", self.api_key, "retention@example.test", 10.0, 1, 0, int(time.time())),
        )
        db.close()

    def tearDown(self):
        self.tmp.cleanup()

    def auth_headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    def test_cleanup_deletes_only_registered_old_local_files_and_keeps_asset_record(self):
        old_upload = self.client.post(
            "/v1/uploads",
            headers=self.auth_headers(),
            files={"file": ("old.jpg", b"\xff\xd8\xff\xe0old", "image/jpeg")},
        )
        fresh_upload = self.client.post(
            "/v1/uploads",
            headers=self.auth_headers(),
            files={"file": ("fresh.jpg", b"\xff\xd8\xff\xe0fresh", "image/jpeg")},
        )
        unregistered_upload = self.client.post(
            "/v1/uploads",
            headers=self.auth_headers(),
            files={"file": ("local.jpg", b"\xff\xd8\xff\xe0local", "image/jpeg")},
        )
        self.assertEqual(old_upload.status_code, 200, old_upload.text)
        self.assertEqual(fresh_upload.status_code, 200, fresh_upload.text)
        self.assertEqual(unregistered_upload.status_code, 200, unregistered_upload.text)

        now = int(time.time())
        db = self.server.get_db()
        db.execute(
            """UPDATE uploads
               SET asset_id=?, asset_url=?, asset_status='created', created_at=?, updated_at=?
               WHERE id=?""",
            ("asset-old", "asset://asset-old", now - 3 * 86400, now - 3 * 86400, old_upload.json()["id"]),
        )
        db.execute(
            """UPDATE uploads
               SET asset_id=?, asset_url=?, asset_status='created', created_at=?, updated_at=?
               WHERE id=?""",
            ("asset-fresh", "asset://asset-fresh", now - 3600, now - 3600, fresh_upload.json()["id"]),
        )
        db.execute(
            "UPDATE uploads SET created_at=?, updated_at=? WHERE id=?",
            (now - 3 * 86400, now - 3 * 86400, unregistered_upload.json()["id"]),
        )
        db.close()

        old_path = Path(os.environ["UPLOAD_DIR"]) / old_upload.json()["object_key"].replace("uploads/", "")
        fresh_path = Path(os.environ["UPLOAD_DIR"]) / fresh_upload.json()["object_key"].replace("uploads/", "")
        unregistered_path = Path(os.environ["UPLOAD_DIR"]) / unregistered_upload.json()["object_key"].replace("uploads/", "")
        self.assertTrue(old_path.exists())
        self.assertTrue(fresh_path.exists())
        self.assertTrue(unregistered_path.exists())

        result = self.cleanup.cleanup_registered_upload_files(
            older_than_seconds=86400,
            dry_run=False,
            now=now,
        )

        self.assertEqual(result["deleted"], 1, json.dumps(result, sort_keys=True))
        self.assertEqual(result["skipped"], 2)
        self.assertFalse(old_path.exists())
        self.assertTrue(fresh_path.exists())
        self.assertTrue(unregistered_path.exists())

        db = self.server.get_db()
        old_row = db.execute("SELECT asset_url, local_deleted_at, deleted_at FROM uploads WHERE id=?", (old_upload.json()["id"],)).fetchone()
        audit = db.execute(
            "SELECT action FROM audit_events WHERE target_id=? ORDER BY created_at DESC LIMIT 1",
            (old_upload.json()["id"],),
        ).fetchone()
        db.close()
        self.assertEqual(old_row["asset_url"], "asset://asset-old")
        self.assertIsNotNone(old_row["local_deleted_at"])
        self.assertIsNone(old_row["deleted_at"])
        self.assertEqual(audit["action"], "system_cleaned_local_upload_file")

    def test_cleanup_dry_run_reports_without_deleting_or_writing_db(self):
        uploaded = self.client.post(
            "/v1/uploads",
            headers=self.auth_headers(),
            files={"file": ("old.jpg", b"\xff\xd8\xff\xe0old", "image/jpeg")},
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        now = int(time.time())
        db = self.server.get_db()
        db.execute(
            """UPDATE uploads
               SET asset_id=?, asset_url=?, asset_status='created', created_at=?, updated_at=?
               WHERE id=?""",
            ("asset-dry-run", "asset://asset-dry-run", now - 3 * 86400, now - 3 * 86400, uploaded.json()["id"]),
        )
        db.close()
        path = Path(os.environ["UPLOAD_DIR"]) / uploaded.json()["object_key"].replace("uploads/", "")

        result = self.cleanup.cleanup_registered_upload_files(
            older_than_seconds=86400,
            dry_run=True,
            now=now,
        )

        self.assertEqual(result["would_delete"], 1)
        self.assertTrue(path.exists())
        db = self.server.get_db()
        row = db.execute("SELECT local_deleted_at FROM uploads WHERE id=?", (uploaded.json()["id"],)).fetchone()
        db.close()
        self.assertIsNone(row["local_deleted_at"])


if __name__ == "__main__":
    unittest.main()
