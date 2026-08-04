import sqlite3
import tempfile
import unittest

from deploy import reprice_settled_tasks


class RepriceSettledTasksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = f"{self.tmp.name}/relay.sqlite"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                balance_usd REAL,
                price_multiplier REAL,
                markup_pct REAL
            );
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                client_model TEXT,
                upstream_model TEXT,
                resolution TEXT,
                duration INTEGER,
                has_video_ref INTEGER,
                status TEXT,
                settled INTEGER,
                completion_tokens INTEGER,
                actual_cost_usd REAL,
                upstream_actual_cost_usd REAL,
                price_multiplier REAL,
                markup_pct REAL,
                created_at INTEGER,
                updated_at INTEGER
            );
            CREATE TABLE invoices (
                id TEXT PRIMARY KEY,
                status TEXT
            );
            CREATE TABLE invoice_items (
                id TEXT PRIMARY KEY,
                invoice_id TEXT,
                task_id TEXT
            );
            CREATE TABLE audit_events (
                id TEXT PRIMARY KEY,
                actor_user_id TEXT,
                actor_type TEXT NOT NULL,
                action TEXT NOT NULL,
                target_type TEXT,
                target_id TEXT,
                metadata_json TEXT,
                created_at INTEGER NOT NULL
            );
            """
        )
        self.conn.execute(
            "INSERT INTO users (id, balance_usd, price_multiplier) VALUES (?,?,?)",
            ("u_reprice", 98.0, 1.0),
        )
        self.conn.execute(
            """INSERT INTO tasks
               (id, user_id, client_model, upstream_model, resolution, duration,
                has_video_ref, status, settled, completion_tokens,
                actual_cost_usd, upstream_actual_cost_usd, price_multiplier,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "vid_wrong_price",
                "u_reprice",
                "dreamina-seedance-2-0-260128",
                "dreamina-seedance-2-0-260128",
                "720p",
                10,
                0,
                "succeeded",
                1,
                206778,
                0.1861,
                0.1861,
                1.0,
                1,
                1,
            ),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_reprice_computes_and_applies_seedance2_task_delta(self):
        row = reprice_settled_tasks.select_rows(
            self.conn,
            _Args(db=self.db_path, include_invoiced=False),
        )[0]
        change = reprice_settled_tasks.compute_change(row, 0.000001)

        self.assertEqual(change["new_upstream_cost_usd"], 1.447446)
        self.assertEqual(change["new_actual_cost_usd"], 1.447446)
        self.assertEqual(change["delta_actual_cost_usd"], 1.261346)

        reprice_settled_tasks.apply_changes(self.conn, [change])

        task = self.conn.execute(
            "SELECT upstream_actual_cost_usd, actual_cost_usd FROM tasks WHERE id=?",
            ("vid_wrong_price",),
        ).fetchone()
        user = self.conn.execute(
            "SELECT balance_usd FROM users WHERE id=?",
            ("u_reprice",),
        ).fetchone()
        audit = self.conn.execute(
            "SELECT action, target_id FROM audit_events WHERE target_id=?",
            ("vid_wrong_price",),
        ).fetchone()

        self.assertEqual(task["upstream_actual_cost_usd"], 1.447446)
        self.assertEqual(task["actual_cost_usd"], 1.447446)
        self.assertEqual(user["balance_usd"], 96.738654)
        self.assertEqual(audit["action"], "system_repriced_settled_task")

    def test_apply_changes_rolls_back_when_task_update_matches_no_rows(self):
        row = reprice_settled_tasks.select_rows(
            self.conn,
            _Args(db=self.db_path, include_invoiced=False),
        )[0]
        change = dict(reprice_settled_tasks.compute_change(row, 0.000001))
        change["task_id"] = "missing_task"

        with self.assertRaisesRegex(RuntimeError, "task update matched 0 rows"):
            reprice_settled_tasks.apply_changes(self.conn, [change])

        task = self.conn.execute(
            "SELECT upstream_actual_cost_usd, actual_cost_usd FROM tasks WHERE id=?",
            ("vid_wrong_price",),
        ).fetchone()
        user = self.conn.execute(
            "SELECT balance_usd FROM users WHERE id=?",
            ("u_reprice",),
        ).fetchone()
        audit_count = self.conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]

        self.assertEqual(task["upstream_actual_cost_usd"], 0.1861)
        self.assertEqual(task["actual_cost_usd"], 0.1861)
        self.assertEqual(user["balance_usd"], 98.0)
        self.assertEqual(audit_count, 0)

    def test_apply_changes_rolls_back_when_user_balance_update_matches_no_rows(self):
        row = reprice_settled_tasks.select_rows(
            self.conn,
            _Args(db=self.db_path, include_invoiced=False),
        )[0]
        change = dict(reprice_settled_tasks.compute_change(row, 0.000001))
        change["user_id"] = "missing_user"

        with self.assertRaisesRegex(RuntimeError, "user balance update matched 0 rows"):
            reprice_settled_tasks.apply_changes(self.conn, [change])

        task = self.conn.execute(
            "SELECT upstream_actual_cost_usd, actual_cost_usd FROM tasks WHERE id=?",
            ("vid_wrong_price",),
        ).fetchone()
        user = self.conn.execute(
            "SELECT balance_usd FROM users WHERE id=?",
            ("u_reprice",),
        ).fetchone()
        audit_count = self.conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]

        self.assertEqual(task["upstream_actual_cost_usd"], 0.1861)
        self.assertEqual(task["actual_cost_usd"], 0.1861)
        self.assertEqual(user["balance_usd"], 98.0)
        self.assertEqual(audit_count, 0)


class _Args:
    task_id = None
    model = None
    since = None
    limit = None

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


if __name__ == "__main__":
    unittest.main()
