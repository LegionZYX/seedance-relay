import importlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


class EndpointKeyRotationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        os.environ["DB_PATH"] = str(root / "relay.sqlite")
        os.environ["VIDEO_DIR"] = str(root / "videos")
        os.environ["UPLOAD_DIR"] = str(root / "uploads")
        os.environ["ADMIN_PASSWORD"] = ""
        os.environ["ENDPOINT_KEY_ROTATION_ENABLED"] = "true"
        os.environ["ENDPOINT_KEY_ROTATION_DAYS_BEFORE_EXPIRY"] = "5"
        os.environ["ENDPOINT_KEY_DURATION_SECONDS"] = "2592000"
        os.environ["ENDPOINT_KEY_ROTATION_DRY_RUN"] = "false"

        if str(PROJECT_DIR) not in sys.path:
            sys.path.insert(0, str(PROJECT_DIR))
        sys.modules.pop("relay_server", None)
        sys.modules.pop("deploy.rotate_endpoint_keys", None)
        self.server = importlib.import_module("relay_server")
        self.rotation = importlib.import_module("deploy.rotate_endpoint_keys")

    def tearDown(self):
        self.tmp.cleanup()
        for key in (
            "ENDPOINT_KEY_ROTATION_ENABLED",
            "ENDPOINT_KEY_ROTATION_DAYS_BEFORE_EXPIRY",
            "ENDPOINT_KEY_DURATION_SECONDS",
            "ENDPOINT_KEY_ROTATION_DRY_RUN",
        ):
            os.environ.pop(key, None)

    def _insert_user(self, *, user_id="u_rotate", expires_in=3600, enabled=True):
        now = int(time.time())
        note = {
            "upstream_mode": "auto_dedicated",
            "byteplus_endpoint_id": "ep-rotate-customer",
            "byteplus_endpoint_api_key_expires_at": now + expires_in,
            "byteplus_endpoint_key_rotation_enabled": enabled,
        }
        db = self.server.get_db()
        db.execute(
            """INSERT INTO users
               (id, api_key, email, balance_usd, is_active, is_admin,
                byteplus_api_key, note, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                user_id,
                "sk-" + user_id,
                f"{user_id}@example.test",
                10.0,
                1,
                0,
                "old-endpoint-key",
                json.dumps(note),
                now,
            ),
        )
        db.close()

    def test_rotation_updates_due_customer_endpoint_key_and_note_metadata(self):
        self._insert_user()
        calls = []

        def fake_get_endpoint_api_key(endpoint_id, duration_seconds):
            calls.append((endpoint_id, duration_seconds))
            return {
                "api_key": "new-endpoint-key",
                "expires_at": int(time.time()) + 2592000,
            }

        self.rotation.get_endpoint_api_key = fake_get_endpoint_api_key

        result = self.rotation.rotate_due_endpoint_keys(now=int(time.time()))

        self.assertEqual(result["rotated"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(calls, [("ep-rotate-customer", 2592000)])
        db = self.server.get_db()
        row = db.execute("SELECT byteplus_api_key, note FROM users WHERE id='u_rotate'").fetchone()
        audit = db.execute(
            "SELECT action FROM audit_events WHERE target_id='u_rotate' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        db.close()
        self.assertEqual(row["byteplus_api_key"], "new-endpoint-key")
        note = json.loads(row["note"])
        self.assertEqual(note["byteplus_endpoint_key_rotation_error"], "")
        self.assertGreater(note["byteplus_endpoint_key_next_rotate_at"], int(time.time()))
        self.assertEqual(audit["action"], "system_rotated_endpoint_api_key")

    def test_rotation_failure_preserves_old_key_and_records_sanitized_error(self):
        self._insert_user(user_id="u_fail")

        def fake_get_endpoint_api_key(endpoint_id, duration_seconds):
            raise RuntimeError("provider failed with secret new-key-value")

        self.rotation.get_endpoint_api_key = fake_get_endpoint_api_key

        result = self.rotation.rotate_due_endpoint_keys(now=int(time.time()))

        self.assertEqual(result["rotated"], 0)
        self.assertEqual(result["failed"], 1)
        db = self.server.get_db()
        row = db.execute("SELECT byteplus_api_key, note FROM users WHERE id='u_fail'").fetchone()
        audit = db.execute(
            "SELECT action FROM audit_events WHERE target_id='u_fail' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        db.close()
        self.assertEqual(row["byteplus_api_key"], "old-endpoint-key")
        note = json.loads(row["note"])
        self.assertIn("provider failed", note["byteplus_endpoint_key_rotation_error"])
        self.assertEqual(audit["action"], "system_endpoint_api_key_rotation_failed")


if __name__ == "__main__":
    unittest.main()
