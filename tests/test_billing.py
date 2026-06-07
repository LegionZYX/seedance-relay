import importlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


PROJECT_DIR = Path(__file__).resolve().parents[1]


class BillingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        os.environ["DB_PATH"] = str(root / "relay.sqlite")
        os.environ["VIDEO_DIR"] = str(root / "videos")
        os.environ["UPLOAD_DIR"] = str(root / "uploads")
        os.environ["PUBLIC_DOMAIN"] = "media.example.test"
        os.environ["ADMIN_KEY"] = "admin-billing"
        os.environ["ADMIN_PASSWORD"] = ""

        if str(PROJECT_DIR) not in sys.path:
            sys.path.insert(0, str(PROJECT_DIR))
        sys.modules.pop("relay_server", None)
        self.server = importlib.import_module("relay_server")

        self.client = TestClient(self.server.app)
        self.user_id = "u_billing"
        now = int(time.time())
        db = self.server.get_db()
        db.execute(
            """INSERT INTO users
               (id, api_key, email, balance_usd, is_active, is_admin, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (self.user_id, "sk-billing", "billing@example.test", 100.0, 1, 0, now),
        )
        self._insert_task(
            db,
            task_id="vid_bill_1",
            created_at=now - 200,
            actual=12.5,
            upstream=7.25,
        )
        self._insert_task(
            db,
            task_id="vid_bill_2",
            created_at=now - 100,
            actual=3.0,
            upstream=1.0,
        )
        self._insert_task(
            db,
            task_id="vid_unsettled",
            created_at=now - 50,
            actual=9.0,
            upstream=4.0,
            settled=0,
        )
        db.close()
        self.period_start = now - 1000
        self.period_end = now + 10

    def tearDown(self):
        self.tmp.cleanup()

    def admin_headers(self):
        return {"X-Admin-Key": "admin-billing"}

    def _insert_task(self, db, *, task_id, created_at, actual, upstream, settled=1):
        db.execute(
            """INSERT INTO tasks
               (id, user_id, upstream_task_id, upstream_model, client_model,
                resolution, duration, has_video_ref, status,
                estimated_cost_usd, held_usd, actual_cost_usd,
                upstream_actual_cost_usd, markup_pct, price_multiplier,
                completion_tokens, settled, prompt_text, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task_id,
                self.user_id,
                "upstream-" + task_id,
                "dreamina-seedance-2-0-260128",
                "dreamina-seedance-2-0-260128",
                "720p",
                5,
                0,
                "succeeded",
                actual,
                actual,
                actual,
                upstream,
                0.3,
                1.3,
                1000,
                settled,
                "invoice prompt",
                created_at,
                created_at,
            ),
        )

    def test_admin_can_preview_save_export_and_mark_paid_invoice(self):
        preview = self.client.get(
            f"/admin/users/{self.user_id}/billing/preview"
            f"?period_start={self.period_start}&period_end={self.period_end}",
            headers=self.admin_headers(),
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        body = preview.json()
        self.assertEqual(body["task_count"], 2)
        self.assertEqual(body["total_usd"], 15.5)
        self.assertEqual(body["upstream_cost_usd"], 8.25)
        self.assertEqual(body["gross_profit_usd"], 7.25)

        saved = self.client.post(
            f"/admin/users/{self.user_id}/invoices",
            headers=self.admin_headers(),
            json={"period_start": self.period_start, "period_end": self.period_end},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        invoice = saved.json()
        self.assertEqual(invoice["status"], "draft")
        self.assertEqual(invoice["task_count"], 2)

        second_preview = self.client.get(
            f"/admin/users/{self.user_id}/billing/preview"
            f"?period_start={self.period_start}&period_end={self.period_end}",
            headers=self.admin_headers(),
        )
        self.assertEqual(second_preview.status_code, 200, second_preview.text)
        self.assertEqual(second_preview.json()["task_count"], 0)

        customer_csv = self.client.get(
            f"/admin/invoices/{invoice['id']}/export?format=csv&view=customer",
            headers=self.admin_headers(),
        )
        self.assertEqual(customer_csv.status_code, 200, customer_csv.text)
        self.assertIn("task_id,description,model,resolution,duration,amount_usd", customer_csv.text)
        self.assertIn("vid_bill_1", customer_csv.text)
        self.assertNotIn("upstream_cost_usd", customer_csv.text)
        self.assertNotIn("gross_profit", customer_csv.text)

        internal_csv = self.client.get(
            f"/admin/invoices/{invoice['id']}/export?format=csv&view=internal",
            headers=self.admin_headers(),
        )
        self.assertEqual(internal_csv.status_code, 200, internal_csv.text)
        self.assertIn("upstream_cost_usd", internal_csv.text)
        self.assertIn("gross_profit_usd", internal_csv.text)

        paid = self.client.post(
            f"/admin/invoices/{invoice['id']}/mark-paid",
            headers=self.admin_headers(),
        )
        self.assertEqual(paid.status_code, 200, paid.text)
        self.assertEqual(paid.json()["status"], "paid")


if __name__ == "__main__":
    unittest.main()
