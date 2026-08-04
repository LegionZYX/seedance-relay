import importlib
import asyncio
import contextlib
import io
import json
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

    def __init__(self, payload=None, status_code=200, text=None, headers=None):
        self._payload = payload or {"id": "upstream-task"}
        self.status_code = status_code
        if text is not None:
            self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeHttp:
    def __init__(self):
        self.posts = []
        self.deletes = []
        self.gets = []
        self.streams = []
        self.get_payload = None
        self.post_response = None
        self.delete_response = None
        self.stream_response = None
        self.on_post = None

    async def post(self, url, json, headers, timeout):
        self.posts.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self.on_post is not None:
            self.on_post(url, json, headers, timeout)
        if self.post_response is not None:
            return self.post_response
        return FakeResponse()

    async def delete(self, url, headers, timeout):
        self.deletes.append({"url": url, "headers": headers, "timeout": timeout})
        if self.delete_response is not None:
            return self.delete_response
        return FakeResponse({"ok": True})

    async def get(self, url, headers, timeout):
        self.gets.append({"url": url, "headers": headers, "timeout": timeout})
        return FakeResponse(self.get_payload)

    def stream(self, method, url, **kwargs):
        self.streams.append({"method": method, "url": url, **kwargs})
        return self.stream_response or FakeStreamResponse()


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


class FakeStreamErrorResponse(FakeStreamResponse):
    status_code = 403
    headers = {
        "content-type": "text/plain",
        "location": "https://byteplus.example.test/private.mp4",
    }

    async def aiter_bytes(self, chunk_size=65536):
        yield b"forbidden https://byteplus.example.test/private.mp4"


class FakeStreamRangeUnsupportedResponse(FakeStreamResponse):
    status_code = 200
    headers = {
        "content-type": "video/mp4",
        "content-length": "10",
        "accept-ranges": "bytes",
    }

    async def aiter_bytes(self, chunk_size=65536):
        yield b"full-video"


class FakeStreamEnterErrorResponse(FakeStreamResponse):
    async def __aenter__(self):
        raise RuntimeError(
            "download failed for https://byteplus.example.test/private.mp4?token=ark-secret-key"
        )


class Alpha1SpecControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        os.environ["DB_PATH"] = str(root / "relay.sqlite")
        os.environ["VIDEO_DIR"] = str(root / "videos")
        os.environ["UPLOAD_DIR"] = str(root / "uploads")
        os.environ["PUBLIC_DOMAIN"] = "media.example.test"
        os.environ["ADMIN_PASSWORD"] = ""
        os.environ["ADMIN_KEY"] = "admin-alpha1"
        os.environ["UPSTREAM_API_KEY"] = "ark-test-upstream"
        os.environ["MARKUP_PCT"] = "0.30"
        os.environ["VIDEO_PERSIST_MODE"] = "proxy_only"
        os.environ["RUNTIME_INTERNAL_TOKEN"] = "runtime-internal-test"

        if str(PROJECT_DIR) not in sys.path:
            sys.path.insert(0, str(PROJECT_DIR))
        sys.modules.pop("relay_server", None)
        self.server = importlib.import_module("relay_server")
        self.fake_http = FakeHttp()
        self.server.http = self.fake_http
        self.client = TestClient(self.server.app)

    def tearDown(self):
        self.tmp.cleanup()

    def admin_headers(self):
        return {"X-Admin-Key": "admin-alpha1"}

    def create_user(self, email="customer@example.test", **overrides):
        body = {
            "email": email,
            "balance_usd": 100.0,
            "password": "initial-password",
        }
        body.update(overrides)
        response = self.client.post("/admin/users", headers=self.admin_headers(), json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def auth_headers(self, api_key):
        return {"Authorization": f"Bearer {api_key}"}

    def video_payload(self, model="dreamina-seedance-2-0-260128"):
        return {
            "model": model,
            "content": [{"type": "text", "text": "A signed customer prompt passes through."}],
            "resolution": "480p",
            "duration": 5,
            "generate_audio": False,
        }

    def test_sqlite_uses_wal_and_busy_timeout_for_alpha1_boundary(self):
        db = self.server.get_db()
        journal_mode = db.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = db.execute("PRAGMA busy_timeout").fetchone()[0]
        db.close()

        self.assertEqual(journal_mode.lower(), "wal")
        self.assertGreaterEqual(busy_timeout, 5000)

    def test_admin_can_set_direct_price_multiplier_on_customer(self):
        user = self.create_user("multiplier@example.test", price_multiplier=1.2)

        self.assertEqual(user["price_multiplier"], 1.2)

        estimate = self.client.post(
            "/v1/videos/estimate",
            headers=self.auth_headers(user["api_key"]),
            json=self.video_payload(),
        )

        self.assertEqual(estimate.status_code, 200, estimate.text)
        body = estimate.json()
        self.assertEqual(body["price_multiplier"], 1.2)

    def test_customer_multiplier_matrix_scales_estimate_and_hold(self):
        for multiplier in (1.0, 1.2, 1.3):
            with self.subTest(multiplier=multiplier):
                slug = str(multiplier).replace(".", "-")
                user = self.create_user(f"multiplier-{slug}@example.test", price_multiplier=multiplier)

                estimate = self.client.post(
                    "/v1/videos/estimate",
                    headers=self.auth_headers(user["api_key"]),
                    json=self.video_payload(),
                )
                self.assertEqual(estimate.status_code, 200, estimate.text)
                estimate_body = estimate.json()
                self.assertEqual(estimate_body["price_multiplier"], multiplier)
                self.assertEqual(
                    estimate_body["estimated_cost_usd"],
                    round(estimate_body["upstream_estimated_cost_usd"] * multiplier, 6),
                )
                self.assertEqual(
                    estimate_body["max_cost_usd"],
                    round(estimate_body["upstream_max_cost_usd"] * multiplier, 6),
                )

                self.fake_http.post_response = FakeResponse({"id": f"upstream-multiplier-{slug}"})
                created = self.client.post(
                    "/v1/videos",
                    headers=self.auth_headers(user["api_key"]),
                    json=self.video_payload(),
                )
                self.assertEqual(created.status_code, 200, created.text)
                create_body = created.json()
                self.assertEqual(create_body["price_multiplier"], multiplier)
                self.assertEqual(
                    create_body["estimated_cost_usd"],
                    round(create_body["upstream_estimated_cost_usd"] * multiplier, 6),
                )
                self.assertEqual(create_body["held_usd"], estimate_body["max_cost_usd"])

    def test_create_reserves_balance_before_upstream_submission(self):
        user = self.create_user(
            "reserve-before-upstream@example.test",
            balance_usd=10.0,
            price_multiplier=1.2,
        )
        observed = {}

        def capture_balance_at_upstream(_url, _json, _headers, _timeout):
            db = self.server.get_db()
            try:
                row = db.execute("SELECT balance_usd FROM users WHERE id=?", (user["id"],)).fetchone()
            finally:
                db.close()
            observed["balance"] = round(float(row["balance_usd"]), 6)

        self.fake_http.on_post = capture_balance_at_upstream

        response = self.client.post(
            "/v1/videos",
            headers=self.auth_headers(user["api_key"]),
            json=self.video_payload(),
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["held_usd"], 0.467914)
        self.assertEqual(observed["balance"], 9.532086)

    def test_price_multiplier_backfill_does_not_rewrite_new_explicit_one(self):
        user = self.create_user("explicit-one@example.test", price_multiplier=1.0)
        self.assertEqual(user["price_multiplier"], 1.0)

        for _ in range(3):
            db = self.server.get_db()
            db.close()

        detail = self.client.get(f"/admin/users/{user['id']}", headers=self.admin_headers())
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["price_multiplier"], 1.0)

    def test_settlement_uses_task_price_multiplier_snapshot_after_admin_change(self):
        user = self.create_user("settle-snapshot@example.test", price_multiplier=1.2)
        db = self.server.get_db()
        db.execute(
            """INSERT INTO tasks
               (id, user_id, upstream_task_id, upstream_model, client_model,
                resolution, duration, has_video_ref, status,
                estimated_cost_usd, held_usd, markup_pct, price_multiplier,
                settled, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "vid_settle_snapshot",
                user["id"],
                "upstream-settle-snapshot",
                "dreamina-seedance-2-0-260128",
                "dreamina-seedance-2-0-260128",
                "480p",
                5,
                0,
                "queued",
                0.425376,
                0.467914,
                0.2,
                1.2,
                0,
                1,
                1,
            ),
        )
        db.execute(
            "UPDATE users SET balance_usd=?, price_multiplier=? WHERE id=?",
            (9.532086, 1.8, user["id"]),
        )
        db.close()
        self.fake_http.get_payload = {
            "status": "succeeded",
            "model": "dreamina-seedance-2-0-260128",
            "resolution": "480p",
            "usage": {"completion_tokens": 1000},
            "content": {"video_url": "https://byteplus.example.test/private-video.mp4"},
        }

        response = self.client.get(
            "/v1/videos/vid_settle_snapshot",
            headers=self.auth_headers(user["api_key"]),
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["actual_cost_usd"], 0.0084)
        self.assertNotEqual(body["actual_cost_usd"], 0.0126)

        db = self.server.get_db()
        row = db.execute(
            "SELECT balance_usd FROM users WHERE id=?",
            (user["id"],),
        ).fetchone()
        task = db.execute(
            "SELECT actual_cost_usd, price_multiplier, settled FROM tasks WHERE id=?",
            ("vid_settle_snapshot",),
        ).fetchone()
        db.close()
        self.assertEqual(round(row["balance_usd"], 6), 9.9916)
        self.assertEqual(task["actual_cost_usd"], 0.0084)
        self.assertEqual(task["price_multiplier"], 1.2)
        self.assertEqual(task["settled"], 1)

    def test_settlement_prices_with_task_model_snapshot_not_upstream_echo_model(self):
        user = self.create_user("settle-model-snapshot@example.test", price_multiplier=1.0)
        db = self.server.get_db()
        db.execute(
            """INSERT INTO tasks
               (id, user_id, upstream_task_id, upstream_model, client_model,
                resolution, duration, has_video_ref, status,
                estimated_cost_usd, held_usd, price_multiplier,
                settled, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "vid_settle_model_snapshot",
                user["id"],
                "upstream-settle-model-snapshot",
                "dreamina-seedance-2-0-260128",
                "dreamina-seedance-2-0-260128",
                "720p",
                10,
                0,
                "queued",
                1.447446,
                1.592191,
                1.0,
                0,
                1,
                1,
            ),
        )
        db.close()
        self.fake_http.get_payload = {
            "status": "succeeded",
            "model": "seedance-1-0-pro-fast-251015",
            "resolution": "720p",
            "usage": {"completion_tokens": 206778},
            "content": {"video_url": "https://byteplus.example.test/private-video.mp4"},
        }

        response = self.client.get(
            "/v1/videos/vid_settle_model_snapshot",
            headers=self.auth_headers(user["api_key"]),
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["actual_cost_usd"], 1.447446)

        db = self.server.get_db()
        task = db.execute(
            "SELECT upstream_actual_cost_usd, actual_cost_usd, settled FROM tasks WHERE id=?",
            ("vid_settle_model_snapshot",),
        ).fetchone()
        db.close()
        self.assertEqual(task["upstream_actual_cost_usd"], 1.447446)
        self.assertEqual(task["actual_cost_usd"], 1.447446)
        self.assertEqual(task["settled"], 1)

    def test_settlement_prices_dedicated_endpoint_from_client_model_snapshot(self):
        user = self.create_user("settle-endpoint-model@example.test", price_multiplier=1.2)
        db = self.server.get_db()
        db.execute(
            """INSERT INTO tasks
               (id, user_id, upstream_task_id, upstream_model, client_model,
                resolution, duration, has_video_ref, status,
                estimated_cost_usd, held_usd, price_multiplier,
                settled, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "vid_settle_endpoint_model",
                user["id"],
                "upstream-settle-endpoint-model",
                "ep-dedicated-seedance-2",
                "dreamina-seedance-2-0-260128",
                "720p",
                5,
                0,
                "queued",
                0.0084,
                0.01,
                1.2,
                0,
                1,
                1,
            ),
        )
        db.execute(
            "UPDATE users SET balance_usd=? WHERE id=?",
            (9.99, user["id"]),
        )
        db.close()
        self.fake_http.get_payload = {
            "status": "succeeded",
            "model": "dreamina-seedance-2-0-260128",
            "resolution": "720p",
            "usage": {"completion_tokens": 1000},
            "content": {"video_url": "https://byteplus.example.test/private-video.mp4"},
        }

        response = self.client.get(
            "/v1/videos/vid_settle_endpoint_model",
            headers=self.auth_headers(user["api_key"]),
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["actual_cost_usd"], 0.0084)

        db = self.server.get_db()
        task = db.execute(
            "SELECT upstream_actual_cost_usd, actual_cost_usd, settled FROM tasks WHERE id=?",
            ("vid_settle_endpoint_model",),
        ).fetchone()
        balance = db.execute(
            "SELECT balance_usd FROM users WHERE id=?",
            (user["id"],),
        ).fetchone()["balance_usd"]
        db.close()
        self.assertEqual(task["upstream_actual_cost_usd"], 0.007)
        self.assertEqual(task["actual_cost_usd"], 0.0084)
        self.assertEqual(task["settled"], 1)
        self.assertEqual(round(balance, 6), 9.9916)

    def test_fastapi_terminal_refresh_refunds_only_once_at_db_boundary(self):
        user = self.create_user("fastapi-settle-once@example.test")
        db = self.server.get_db()
        db.execute(
            """INSERT INTO tasks
               (id, user_id, upstream_task_id, upstream_model, client_model,
                resolution, duration, has_video_ref, status,
                estimated_cost_usd, held_usd, price_multiplier,
                settled, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "vid_fastapi_settle_once",
                user["id"],
                "upstream-fastapi-settle-once",
                "dreamina-seedance-2-0-260128",
                "dreamina-seedance-2-0-260128",
                "480p",
                5,
                0,
                "queued",
                0.5,
                0.6,
                1.0,
                0,
                1,
                1,
            ),
        )
        db.execute("UPDATE users SET balance_usd=? WHERE id=?", (9.4, user["id"]))

        kwargs = {
            "task_id": "vid_fastapi_settle_once",
            "user_id": user["id"],
            "status": "succeeded",
            "actual_cost_usd": 0.1,
            "upstream_actual_cost_usd": 0.1,
            "completion_tokens": 1000,
            "cached_video_url": "https://byteplus.example.test/video.mp4",
            "cached_video_url_until": 9999999999,
            "updated_at": 2,
            "refund_usd": 0.5,
        }
        self.server._apply_terminal_task_refresh_once(db, **kwargs)
        self.server._apply_terminal_task_refresh_once(db, **kwargs)

        row = db.execute("SELECT balance_usd FROM users WHERE id=?", (user["id"],)).fetchone()
        task = db.execute(
            "SELECT settled, actual_cost_usd FROM tasks WHERE id=?",
            ("vid_fastapi_settle_once",),
        ).fetchone()
        db.close()
        self.assertEqual(round(row["balance_usd"], 6), 9.9)
        self.assertEqual(task["settled"], 1)
        self.assertEqual(task["actual_cost_usd"], 0.1)

    def test_delete_cancel_refunds_hold_only_once_at_db_boundary(self):
        user = self.create_user("delete-cancel-once@example.test")
        db = self.server.get_db()
        db.execute(
            """INSERT INTO tasks
               (id, user_id, upstream_task_id, upstream_model, client_model,
                resolution, duration, status, held_usd, settled, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "vid_delete_cancel_once",
                user["id"],
                "upstream-delete-cancel-once",
                "dreamina-seedance-2-0-260128",
                "dreamina-seedance-2-0-260128",
                "480p",
                5,
                "queued",
                0.6,
                0,
                1,
                1,
            ),
        )
        db.execute("UPDATE users SET balance_usd=? WHERE id=?", (9.4, user["id"]))

        kwargs = {
            "task_id": "vid_delete_cancel_once",
            "user_id": user["id"],
            "held_usd": 0.6,
            "updated_at": 2,
        }
        self.server._cancel_task_once(db, **kwargs)
        self.server._cancel_task_once(db, **kwargs)

        row = db.execute("SELECT balance_usd FROM users WHERE id=?", (user["id"],)).fetchone()
        task = db.execute(
            "SELECT status, settled FROM tasks WHERE id=?",
            ("vid_delete_cancel_once",),
        ).fetchone()
        db.close()
        self.assertEqual(round(row["balance_usd"], 6), 10.0)
        self.assertEqual(task["status"], "cancelled")
        self.assertEqual(task["settled"], 1)

    def test_admin_create_rejects_price_multiplier_below_one(self):
        response = self.client.post(
            "/admin/users",
            headers=self.admin_headers(),
            json={
                "email": "discount-create@example.test",
                "balance_usd": 10,
                "price_multiplier": 0.9,
            },
        )

        self.assertEqual(response.status_code, 422, response.text)

        db = self.server.get_db()
        row = db.execute(
            "SELECT id FROM users WHERE email=?",
            ("discount-create@example.test",),
        ).fetchone()
        db.close()
        self.assertIsNone(row)

    def test_admin_patch_rejects_price_multiplier_below_one(self):
        user = self.create_user("discount-patch@example.test", price_multiplier=1.2)

        response = self.client.patch(
            f"/admin/users/{user['id']}",
            headers=self.admin_headers(),
            json={"price_multiplier": 0.9},
        )

        self.assertEqual(response.status_code, 422, response.text)

        detail = self.client.get(f"/admin/users/{user['id']}", headers=self.admin_headers())
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["price_multiplier"], 1.2)

    def test_customer_model_list_filters_models_and_blocks_disabled_create(self):
        user = self.create_user(
            "models@example.test",
            enabled_models=["seedance-1-0-lite-t2v-250428"],
        )

        models = self.client.get("/v1/models", headers=self.auth_headers(user["api_key"]))
        self.assertEqual(models.status_code, 200, models.text)
        self.assertEqual([item["id"] for item in models.json()["data"]], ["seedance-1-0-lite-t2v-250428"])

        denied = self.client.post(
            "/v1/videos",
            headers=self.auth_headers(user["api_key"]),
            json=self.video_payload("dreamina-seedance-2-0-260128"),
        )
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(denied.json()["detail"]["error"]["code"], "model_not_enabled")
        self.assertEqual(self.fake_http.posts, [])

    def test_admin_can_set_empty_model_list_without_falling_back_to_default(self):
        user = self.create_user(
            "no-models@example.test",
            enabled_models=[],
        )

        self.assertFalse(user["enabled_models_default"])
        self.assertEqual(user["enabled_models"], [])

        models = self.client.get("/v1/models", headers=self.auth_headers(user["api_key"]))
        self.assertEqual(models.status_code, 200, models.text)
        self.assertEqual(models.json()["data"], [])

        denied = self.client.post(
            "/v1/videos",
            headers=self.auth_headers(user["api_key"]),
            json=self.video_payload("dreamina-seedance-2-0-260128"),
        )
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(denied.json()["detail"]["error"]["code"], "model_not_enabled")
        self.assertEqual(self.fake_http.posts, [])

    def test_admin_can_restore_default_model_list_with_null(self):
        user = self.create_user("restore-models@example.test", enabled_models=["seedance-1-0-lite-t2v-250428"])

        empty = self.client.patch(
            f"/admin/users/{user['id']}",
            headers=self.admin_headers(),
            json={"enabled_models": []},
        )
        self.assertEqual(empty.status_code, 200, empty.text)

        empty_detail = self.client.get(f"/admin/users/{user['id']}", headers=self.admin_headers())
        self.assertEqual(empty_detail.status_code, 200, empty_detail.text)
        self.assertFalse(empty_detail.json()["enabled_models_default"])
        self.assertEqual(empty_detail.json()["enabled_models"], [])

        restored = self.client.patch(
            f"/admin/users/{user['id']}",
            headers=self.admin_headers(),
            json={"enabled_models": None},
        )
        self.assertEqual(restored.status_code, 200, restored.text)

        detail = self.client.get(f"/admin/users/{user['id']}", headers=self.admin_headers())
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertTrue(detail.json()["enabled_models_default"])
        self.assertIn("dreamina-seedance-2-0-260128", detail.json()["enabled_models"])

    def test_invalid_persisted_enabled_models_do_not_fall_back_to_all_models(self):
        user = self.create_user("dirty-models@example.test", enabled_models=["seedance-1-0-lite-t2v-250428"])
        db = self.server.get_db()
        db.execute(
            "UPDATE users SET enabled_models=? WHERE id=?",
            ('["unknown-byteplus-model"]', user["id"]),
        )
        db.close()

        models = self.client.get("/v1/models", headers=self.auth_headers(user["api_key"]))
        self.assertEqual(models.status_code, 200, models.text)
        self.assertEqual(models.json()["data"], [])
        self.assertNotIn("dreamina-seedance-2-0-260128", models.text)

        denied = self.client.post(
            "/v1/videos",
            headers=self.auth_headers(user["api_key"]),
            json=self.video_payload("dreamina-seedance-2-0-260128"),
        )
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(denied.json()["detail"]["error"]["code"], "model_not_enabled")
        self.assertEqual(self.fake_http.posts, [])

    def test_malformed_persisted_enabled_models_do_not_fall_back_to_all_models(self):
        user = self.create_user("bad-model-json@example.test", enabled_models=["seedance-1-0-lite-t2v-250428"])
        db = self.server.get_db()
        db.execute(
            "UPDATE users SET enabled_models=? WHERE id=?",
            ("{bad json", user["id"]),
        )
        db.close()

        models = self.client.get("/v1/models", headers=self.auth_headers(user["api_key"]))
        self.assertEqual(models.status_code, 200, models.text)
        self.assertEqual(models.json()["data"], [])
        self.assertNotIn("dreamina-seedance-2-0-260128", models.text)

        denied = self.client.post(
            "/v1/videos",
            headers=self.auth_headers(user["api_key"]),
            json=self.video_payload("dreamina-seedance-2-0-260128"),
        )
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(denied.json()["detail"]["error"]["code"], "model_not_enabled")
        self.assertEqual(self.fake_http.posts, [])

    def test_models_return_safe_capability_fields_without_upstream_details(self):
        user = self.create_user("model-capabilities@example.test")

        response = self.client.get("/v1/models", headers=self.auth_headers(user["api_key"]))

        self.assertEqual(response.status_code, 200, response.text)
        raw = response.text
        first = response.json()["data"][0]
        self.assertEqual(first["id"], "dreamina-seedance-2-0-260128")
        self.assertIn("supported_resolutions", first)
        self.assertIn("supported_ratios", first)
        self.assertIn("duration_seconds", first)
        self.assertIn("capabilities", first)
        self.assertNotIn("upstream_model", first)
        self.assertNotIn("upstream_model_or_endpoint", first)
        self.assertNotIn("operator_notes", first)
        self.assertNotIn("byteplus", raw.lower())
        self.assertNotIn("ark-", raw.lower())

    def test_estimate_rejects_unsupported_resolution_before_pricing(self):
        user = self.create_user("invalid-resolution@example.test")
        payload = self.video_payload()
        payload["resolution"] = "8k"

        response = self.client.post(
            "/v1/videos/estimate",
            headers=self.auth_headers(user["api_key"]),
            json=payload,
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "unsupported_resolution")

    def test_create_rejects_unsupported_ratio_before_upstream(self):
        user = self.create_user("invalid-ratio@example.test")
        payload = self.video_payload()
        payload["ratio"] = "4:3"

        response = self.client.post(
            "/v1/videos",
            headers=self.auth_headers(user["api_key"]),
            json=payload,
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "unsupported_ratio")
        self.assertEqual(self.fake_http.posts, [])

    def test_create_rejects_unknown_content_block_type_before_upstream(self):
        user = self.create_user("invalid-content@example.test")
        payload = self.video_payload()
        payload["content"] = [{"type": "unsafe_unknown_block", "text": "not a native block"}]

        response = self.client.post(
            "/v1/videos",
            headers=self.auth_headers(user["api_key"]),
            json=payload,
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "invalid_content_block")
        self.assertEqual(self.fake_http.posts, [])

    def test_create_passes_prompt_text_without_relay_content_censorship(self):
        user = self.create_user("prompt-pass-through@example.test")
        payload = self.video_payload()
        prompt = "An adult-themed signed customer scene request with dramatic lighting."
        payload["content"][0]["text"] = prompt

        response = self.client.post(
            "/v1/videos",
            headers=self.auth_headers(user["api_key"]),
            json=payload,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.fake_http.posts), 1)
        upstream_payload = self.fake_http.posts[0]["json"]
        self.assertEqual(upstream_payload["content"][0]["text"], prompt)

    def test_create_upstream_error_returns_safe_request_id_without_secret_leakage(self):
        user = self.create_user("upstream-error@example.test")
        self.fake_http.post_response = FakeResponse(
            status_code=400,
            text=(
                "blocked by upstream: https://byteplus.example.test/private-video.mp4 "
                "using ark-customer-secret-key"
            ),
            headers={"x-request-id": "req_safe_123"},
        )

        response = self.client.post(
            "/v1/videos",
            headers=self.auth_headers(user["api_key"]),
            json=self.video_payload(),
        )

        self.assertEqual(response.status_code, 502, response.text)
        body = response.json()["detail"]["error"]
        self.assertEqual(body["code"], "upstream_error")
        self.assertEqual(body["request_id"], "req_safe_123")
        raw = response.text
        self.assertNotIn("byteplus.example.test", raw)
        self.assertNotIn("private-video.mp4", raw)
        self.assertNotIn("ark-customer-secret-key", raw)

        db = self.server.get_db()
        row = db.execute(
            "SELECT balance_usd FROM users WHERE id=?",
            (user["id"],),
        ).fetchone()
        db.close()
        self.assertEqual(round(float(row["balance_usd"]), 6), 100.0)

    def test_create_cancels_upstream_and_refunds_when_task_recording_fails(self):
        user = self.create_user(
            "recording-fails@example.test",
            balance_usd=10.0,
            price_multiplier=1.2,
        )
        upstream_id = "duplicate-upstream-fastapi"
        now = 1
        db = self.server.get_db()
        db.execute(
            """INSERT INTO tasks
               (id, user_id, upstream_task_id, upstream_model, client_model,
                resolution, duration, has_video_ref, status,
                estimated_cost_usd, held_usd, markup_pct, price_multiplier,
                settled, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "vid_existing_duplicate",
                user["id"],
                upstream_id,
                "dreamina-seedance-2-0-260128",
                "dreamina-seedance-2-0-260128",
                "480p",
                5,
                0,
                "queued",
                0.425376,
                0.467914,
                0.2,
                1.2,
                0,
                now,
                now,
            ),
        )
        db.close()
        self.fake_http.post_response = FakeResponse({"id": upstream_id})

        response = self.client.post(
            "/v1/videos",
            headers=self.auth_headers(user["api_key"]),
            json=self.video_payload(),
        )

        self.assertEqual(response.status_code, 500, response.text)
        self.assertEqual(
            response.json()["detail"]["error"]["code"],
            "task_recording_failed",
        )
        self.assertEqual(len(self.fake_http.deletes), 1)
        self.assertIn(
            "/contents/generations/tasks/duplicate-upstream-fastapi",
            self.fake_http.deletes[0]["url"],
        )
        db = self.server.get_db()
        row = db.execute(
            "SELECT balance_usd FROM users WHERE id=?",
            (user["id"],),
        ).fetchone()
        db.close()
        self.assertEqual(round(float(row["balance_usd"]), 6), 10.0)

    def test_estimate_rejects_unknown_content_block_type_before_pricing(self):
        user = self.create_user("invalid-estimate-content@example.test")
        payload = self.video_payload()
        payload["content"] = [{"type": "unsafe_unknown_block", "text": "not a native block"}]

        response = self.client.post(
            "/v1/videos/estimate",
            headers=self.auth_headers(user["api_key"]),
            json=payload,
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "invalid_content_block")

    def test_estimate_rejects_i2v_model_without_visual_reference_before_pricing(self):
        user = self.create_user("i2v-estimate@example.test")
        payload = self.video_payload("seedance-1-0-lite-i2v-250428")
        payload["content"] = [
            {"type": "text", "text": "Animate this without a visual reference."},
            {
                "type": "audio_url",
                "audio_url": {"url": "https://cdn.example.test/ref.mp3"},
                "role": "reference_audio",
            },
        ]

        response = self.client.post(
            "/v1/videos/estimate",
            headers=self.auth_headers(user["api_key"]),
            json=payload,
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "visual_reference_required")

    def test_create_rejects_too_many_reference_images_before_upstream(self):
        user = self.create_user("too-many-images@example.test")
        payload = self.video_payload()
        payload["content"] = [{"type": "text", "text": "Use too many references."}]
        payload["content"].extend(
            {
                "type": "image_url",
                "image_url": {"url": f"https://cdn.example.test/ref-{index}.jpg"},
                "role": "reference_image",
            }
            for index in range(10)
        )

        response = self.client.post(
            "/v1/videos",
            headers=self.auth_headers(user["api_key"]),
            json=payload,
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "too_many_reference_images")
        self.assertEqual(self.fake_http.posts, [])

    def test_create_rejects_unknown_content_role_before_upstream(self):
        user = self.create_user("invalid-role@example.test")
        payload = self.video_payload()
        payload["content"].append(
            {
                "type": "image_url",
                "image_url": {"url": "https://cdn.example.test/ref.jpg"},
                "role": "unsupported_reference_role",
            }
        )

        response = self.client.post(
            "/v1/videos",
            headers=self.auth_headers(user["api_key"]),
            json=payload,
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "invalid_content_role")
        self.assertEqual(self.fake_http.posts, [])

    def test_create_rejects_i2v_model_without_visual_reference_before_upstream(self):
        user = self.create_user("i2v-create@example.test")
        payload = self.video_payload("seedance-1-0-lite-i2v-250428")
        payload["content"] = [
            {"type": "text", "text": "Animate this without a visual reference."},
            {
                "type": "audio_url",
                "audio_url": {"url": "https://cdn.example.test/ref.mp3"},
                "role": "reference_audio",
            },
        ]

        response = self.client.post(
            "/v1/videos",
            headers=self.auth_headers(user["api_key"]),
            json=payload,
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "visual_reference_required")
        self.assertEqual(self.fake_http.posts, [])

    def test_customer_can_change_own_password(self):
        user = self.create_user("password@example.test")
        old_session = self.server.create_session(user["id"])

        changed = self.client.post(
            "/auth/change-password",
            headers=self.auth_headers(user["api_key"]),
            json={
                "current_password": "initial-password",
                "new_password": "new-secure-password",
            },
        )
        self.assertEqual(changed.status_code, 200, changed.text)

        stale_session_me = self.client.get(
            "/auth/me",
            headers={"Cookie": f"relay_session={old_session}"},
        )
        self.assertEqual(stale_session_me.status_code, 401, stale_session_me.text)

        old_login = self.client.post(
            "/auth/login",
            json={"email": "password@example.test", "password": "initial-password"},
        )
        self.assertEqual(old_login.status_code, 401, old_login.text)

        new_login = self.client.post(
            "/auth/login",
            json={"email": "password@example.test", "password": "new-secure-password"},
        )
        self.assertEqual(new_login.status_code, 200, new_login.text)

    def test_cookie_password_change_revokes_other_sessions_but_keeps_current(self):
        user = self.create_user("password-cookie-session@example.test")
        current_session = self.server.create_session(user["id"])
        other_session = self.server.create_session(user["id"])

        changed = self.client.post(
            "/auth/change-password",
            headers={
                "Cookie": f"relay_session={current_session}",
                "Origin": "https://media.example.test",
                "Host": "media.example.test",
            },
            json={
                "current_password": "initial-password",
                "new_password": "new-cookie-session-password",
            },
        )
        self.assertEqual(changed.status_code, 200, changed.text)

        current_me = self.client.get(
            "/auth/me",
            headers={"Cookie": f"relay_session={current_session}"},
        )
        self.assertEqual(current_me.status_code, 200, current_me.text)

        other_me = self.client.get(
            "/auth/me",
            headers={"Cookie": f"relay_session={other_session}"},
        )
        self.assertEqual(other_me.status_code, 401, other_me.text)

    def test_customer_password_change_rejects_same_password(self):
        user = self.create_user("same-password@example.test")

        response = self.client.post(
            "/auth/change-password",
            headers=self.auth_headers(user["api_key"]),
            json={
                "current_password": "initial-password",
                "new_password": "initial-password",
            },
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "password_reused")

        old_login = self.client.post(
            "/auth/login",
            json={"email": "same-password@example.test", "password": "initial-password"},
        )
        self.assertEqual(old_login.status_code, 200, old_login.text)

    def test_cookie_authenticated_password_change_rejects_cross_site_origin(self):
        user = self.create_user("password-csrf-block@example.test")
        token = self.server.create_session(user["id"])

        response = self.client.post(
            "/auth/change-password",
            headers={
                "Cookie": f"relay_session={token}",
                "Origin": "https://evil.example.test",
                "Host": "media.example.test",
            },
            json={
                "current_password": "initial-password",
                "new_password": "new-csrf-blocked-password",
            },
        )

        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "csrf_origin_mismatch")

        login = self.client.post(
            "/auth/login",
            json={"email": "password-csrf-block@example.test", "password": "initial-password"},
        )
        self.assertEqual(login.status_code, 200, login.text)

    def test_bearer_authenticated_password_change_ignores_origin_csrf_check(self):
        user = self.create_user("password-bearer-origin@example.test")

        response = self.client.post(
            "/auth/change-password",
            headers={
                **self.auth_headers(user["api_key"]),
                "Origin": "https://evil.example.test",
            },
            json={
                "current_password": "initial-password",
                "new_password": "new-bearer-origin-password",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)

        old_login = self.client.post(
            "/auth/login",
            json={"email": "password-bearer-origin@example.test", "password": "initial-password"},
        )
        self.assertEqual(old_login.status_code, 401, old_login.text)
        new_login = self.client.post(
            "/auth/login",
            json={"email": "password-bearer-origin@example.test", "password": "new-bearer-origin-password"},
        )
        self.assertEqual(new_login.status_code, 200, new_login.text)

    def test_repeated_failed_login_temporarily_locks_account(self):
        self.create_user("lock@example.test")

        for _ in range(5):
            response = self.client.post(
                "/auth/login",
                json={"email": "lock@example.test", "password": "wrong-password"},
            )
            self.assertEqual(response.status_code, 401, response.text)

        locked = self.client.post(
            "/auth/login",
            json={"email": "lock@example.test", "password": "initial-password"},
        )

        self.assertEqual(locked.status_code, 423, locked.text)
        self.assertEqual(locked.json()["detail"]["error"]["code"], "account_locked")
        db = self.server.get_db()
        row = db.execute(
            "SELECT failed_login_count, locked_until FROM users WHERE email=?",
            ("lock@example.test",),
        ).fetchone()
        db.close()
        self.assertGreaterEqual(row["failed_login_count"], 5)
        self.assertGreater(row["locked_until"], int(self.server.time.time()))

    def test_successful_login_resets_failed_login_counter(self):
        self.create_user("reset-login@example.test")

        for _ in range(2):
            response = self.client.post(
                "/auth/login",
                json={"email": "reset-login@example.test", "password": "wrong-password"},
            )
            self.assertEqual(response.status_code, 401, response.text)

        success = self.client.post(
            "/auth/login",
            json={"email": "reset-login@example.test", "password": "initial-password"},
        )

        self.assertEqual(success.status_code, 200, success.text)
        db = self.server.get_db()
        row = db.execute(
            "SELECT failed_login_count, locked_until FROM users WHERE email=?",
            ("reset-login@example.test",),
        ).fetchone()
        db.close()
        self.assertEqual(row["failed_login_count"], 0)
        self.assertIsNone(row["locked_until"])

    def test_customer_can_rotate_own_relay_api_key_and_old_key_stops_working(self):
        user = self.create_user("rotate@example.test")
        old_key = user["api_key"]

        rotated = self.client.post(
            "/v1/me/api-key/rotate",
            headers=self.auth_headers(old_key),
        )

        self.assertEqual(rotated.status_code, 200, rotated.text)
        body = rotated.json()
        self.assertTrue(body["api_key"].startswith("sk-"))
        self.assertNotEqual(body["api_key"], old_key)
        self.assertTrue(body["shown_once"])
        self.assertEqual(body["previous_key_status"], "disabled")

        old_me = self.client.get("/v1/me", headers=self.auth_headers(old_key))
        self.assertEqual(old_me.status_code, 401, old_me.text)

        old_models = self.client.get("/v1/models", headers=self.auth_headers(old_key))
        self.assertEqual(old_models.status_code, 401, old_models.text)

        new_me = self.client.get("/v1/me", headers=self.auth_headers(body["api_key"]))
        self.assertEqual(new_me.status_code, 200, new_me.text)
        new_me_body = new_me.json()
        self.assertNotIn("api_key", new_me_body)
        self.assertNotIn(body["api_key"], new_me.text)
        self.assertIn("api_key_masked", new_me_body)
        self.assertIn("...", new_me_body["api_key_masked"])
        self.assertEqual(new_me_body["api_key_last_rotated_at"], body["rotated_at"])

        auth_me = self.client.get("/auth/me", headers=self.auth_headers(body["api_key"]))
        self.assertEqual(auth_me.status_code, 200, auth_me.text)
        auth_me_body = auth_me.json()
        self.assertNotIn("api_key", auth_me_body)
        self.assertNotIn(body["api_key"], auth_me.text)
        self.assertEqual(auth_me_body["api_key_masked"], new_me_body["api_key_masked"])

    def test_cookie_authenticated_state_change_rejects_cross_site_origin(self):
        user = self.create_user("csrf-block@example.test")
        token = self.server.create_session(user["id"])

        response = self.client.post(
            "/v1/me/api-key/rotate",
            headers={
                "Cookie": f"relay_session={token}",
                "Origin": "https://evil.example.test",
                "Host": "media.example.test",
            },
        )

        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "csrf_origin_mismatch")

    def test_cookie_authenticated_state_change_allows_same_origin(self):
        user = self.create_user("csrf-allow@example.test")
        token = self.server.create_session(user["id"])

        response = self.client.post(
            "/v1/me/api-key/rotate",
            headers={
                "Cookie": f"relay_session={token}",
                "Origin": "https://media.example.test",
                "Host": "media.example.test",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["api_key"].startswith("sk-"))

    def test_bearer_authenticated_state_change_ignores_origin_csrf_check(self):
        user = self.create_user("csrf-bearer@example.test")

        response = self.client.post(
            "/v1/me/api-key/rotate",
            headers={
                **self.auth_headers(user["api_key"]),
                "Origin": "https://evil.example.test",
                "Host": "media.example.test",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["api_key"].startswith("sk-"))

    def test_malformed_bearer_with_cookie_does_not_bypass_csrf_origin_guard(self):
        user = self.create_user("csrf-malformed-bearer@example.test")
        token = self.server.create_session(user["id"])

        response = self.client.post(
            "/v1/me/api-key/rotate",
            headers={
                "Cookie": f"relay_session={token}",
                "Authorization": "Bearer   ",
                "Origin": "https://evil.example.test",
                "Host": "media.example.test",
            },
        )

        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "csrf_origin_mismatch")

    def test_malformed_authorization_does_not_fallback_to_cookie_session(self):
        user = self.create_user("malformed-auth-cookie@example.test")
        token = self.server.create_session(user["id"])

        response = self.client.post(
            "/v1/me/api-key/rotate",
            headers={
                "Cookie": f"relay_session={token}",
                "Authorization": "Bearer",
                "Origin": "https://media.example.test",
                "Host": "media.example.test",
            },
        )

        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "missing_auth")

    def test_models_rejects_malformed_authorization_instead_of_returning_public_list(self):
        response = self.client.get("/v1/models", headers={"Authorization": "Bearer"})

        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "missing_auth")
        self.assertNotIn("dreamina-seedance-2-0-260128", response.text)

    def test_admin_user_list_masks_customer_and_byteplus_keys(self):
        user = self.create_user(
            "masked@example.test",
            byteplus_api_key="ark-customer-secret-key",
        )

        listed = self.client.get("/admin/users", headers=self.admin_headers())
        self.assertEqual(listed.status_code, 200, listed.text)
        row = next(item for item in listed.json()["data"] if item["id"] == user["id"])

        self.assertNotEqual(row["api_key"], user["api_key"])
        self.assertTrue(row["api_key"].startswith("sk-"))
        self.assertIn("...", row["api_key"])
        self.assertNotEqual(row["byteplus_api_key"], "ark-customer-secret-key")
        self.assertIn("...", row["byteplus_api_key"])

        self.assertNotIn(user["api_key"], listed.text)
        self.assertNotIn("ark-customer-secret-key", listed.text)

        detail = self.client.get(f"/admin/users/{user['id']}", headers=self.admin_headers())
        self.assertEqual(detail.status_code, 200, detail.text)
        body = detail.json()

        self.assertNotEqual(body["api_key"], user["api_key"])
        self.assertIn("...", body["api_key"])
        self.assertNotEqual(body["byteplus_api_key"], "ark-customer-secret-key")
        self.assertIn("...", body["byteplus_api_key"])
        self.assertNotIn(user["api_key"], detail.text)
        self.assertNotIn("ark-customer-secret-key", detail.text)

    def test_proxy_only_video_persistence_mode_is_available(self):
        self.assertEqual(self.server.VIDEO_PERSIST_MODE, "proxy_only")

    def test_proxy_only_successful_refresh_does_not_create_local_mp4(self):
        user = self.create_user("no-local-video@example.test", price_multiplier=1.2)
        db = self.server.get_db()
        db.execute(
            """INSERT INTO tasks
               (id, user_id, upstream_task_id, upstream_model, client_model,
                resolution, duration, has_video_ref, status,
                estimated_cost_usd, held_usd, markup_pct, price_multiplier,
                settled, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "vid_no_local",
                user["id"],
                "upstream-no-local",
                "dreamina-seedance-2-0-260128",
                "dreamina-seedance-2-0-260128",
                "480p",
                5,
                0,
                "queued",
                0.425376,
                0.467914,
                0.2,
                1.2,
                0,
                1,
                1,
            ),
        )
        db.close()
        self.fake_http.get_payload = {
            "status": "succeeded",
            "model": "dreamina-seedance-2-0-260128",
            "resolution": "480p",
            "usage": {"completion_tokens": 1000},
            "content": {"video_url": "https://byteplus.example.test/private-video.mp4"},
        }

        response = self.client.get(
            "/v1/videos/vid_no_local",
            headers=self.auth_headers(user["api_key"]),
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "succeeded")
        self.assertEqual(list(self.server.VIDEO_DIR.glob("vid_no_local*")), [])

        db = self.server.get_db()
        row = db.execute(
            "SELECT local_video_path FROM tasks WHERE id=?",
            ("vid_no_local",),
        ).fetchone()
        db.close()
        self.assertIsNone(row["local_video_path"])

    def test_persist_video_failure_log_redacts_upstream_url_and_key(self):
        self.fake_http.stream_response = FakeStreamEnterErrorResponse()
        buffer = io.StringIO()

        with contextlib.redirect_stdout(buffer):
            asyncio.run(
                self.server._persist_video(
                    "vid_log_redaction",
                    "https://byteplus.example.test/private.mp4?token=ark-secret-key",
                )
            )

        output = buffer.getvalue()
        self.assertIn("Failed to persist video vid_log_redaction", output)
        self.assertNotIn("byteplus", output.lower())
        self.assertNotIn("private.mp4", output)
        self.assertNotIn("ark-secret-key", output)
        self.assertIn("<redacted-url>", output)

    def test_video_content_proxy_forwards_range_and_returns_partial_content(self):
        user = self.create_user("range@example.test")
        db = self.server.get_db()
        db.execute(
            """INSERT INTO tasks
               (id, user_id, upstream_task_id, upstream_model, client_model,
                resolution, duration, status, settled, cached_video_url,
                cached_video_url_until, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "vid_range",
                user["id"],
                "upstream-range",
                "dreamina-seedance-2-0-260128",
                "dreamina-seedance-2-0-260128",
                "480p",
                5,
                "succeeded",
                1,
                "https://byteplus.example.test/video.mp4",
                9999999999,
                1,
                1,
            ),
        )
        db.close()

        response = self.client.get(
            "/v1/videos/vid_range/content",
            headers={**self.auth_headers(user["api_key"]), "Range": "bytes=3-5"},
        )

        self.assertEqual(response.status_code, 206, response.text)
        self.assertEqual(response.content, b"345")
        self.assertEqual(response.headers["content-range"], "bytes 3-5/10")
        self.assertEqual(response.headers["content-length"], "3")
        self.assertNotIn("location", response.headers)
        for key, value in response.headers.items():
            self.assertNotIn("byteplus.example.test", value, f"{key} leaked upstream URL")
        self.assertEqual(self.fake_http.streams[-1]["headers"]["Range"], "bytes=3-5")

    def test_video_content_proxy_rejects_upstream_error_without_leaking_body(self):
        user = self.create_user("proxy-error@example.test")
        db = self.server.get_db()
        db.execute(
            """INSERT INTO tasks
               (id, user_id, upstream_task_id, upstream_model, client_model,
                resolution, duration, status, settled, cached_video_url,
                cached_video_url_until, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "vid_proxy_error",
                user["id"],
                "upstream-proxy-error",
                "dreamina-seedance-2-0-260128",
                "dreamina-seedance-2-0-260128",
                "480p",
                5,
                "succeeded",
                1,
                "https://byteplus.example.test/private.mp4",
                9999999999,
                1,
                1,
            ),
        )
        db.close()
        self.fake_http.stream_response = FakeStreamErrorResponse()

        response = self.client.get(
            "/v1/videos/vid_proxy_error/content",
            headers=self.auth_headers(user["api_key"]),
        )

        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "proxy_error")
        self.assertNotIn("location", response.headers)
        self.assertNotIn("byteplus", response.text.lower())
        self.assertNotIn("private.mp4", response.text)

    def test_video_content_refreshes_expired_cached_url_before_proxying(self):
        user = self.create_user("expired-content-url@example.test")
        db = self.server.get_db()
        db.execute(
            """INSERT INTO tasks
               (id, user_id, upstream_task_id, upstream_model, client_model,
                resolution, duration, status, settled, cached_video_url,
                cached_video_url_until, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "vid_expired_content_url",
                user["id"],
                "upstream-expired-content-url",
                "dreamina-seedance-2-0-260128",
                "dreamina-seedance-2-0-260128",
                "480p",
                5,
                "succeeded",
                1,
                "https://byteplus.example.test/expired.mp4",
                1,
                1,
                1,
            ),
        )
        db.close()
        self.fake_http.get_payload = {
            "id": "upstream-expired-content-url",
            "status": "succeeded",
            "content": {"video_url": "https://byteplus.example.test/fresh.mp4"},
        }

        response = self.client.get(
            "/v1/videos/vid_expired_content_url/content",
            headers={**self.auth_headers(user["api_key"]), "Range": "bytes=3-5"},
        )

        self.assertEqual(response.status_code, 206, response.text)
        self.assertEqual(response.content, b"345")
        self.assertEqual(self.fake_http.streams[-1]["url"], "https://byteplus.example.test/fresh.mp4")
        db = self.server.get_db()
        row = db.execute(
            "SELECT cached_video_url, cached_video_url_until FROM tasks WHERE id=?",
            ("vid_expired_content_url",),
        ).fetchone()
        db.close()
        self.assertEqual(row["cached_video_url"], "https://byteplus.example.test/fresh.mp4")
        self.assertGreater(row["cached_video_url_until"], int(time.time()))

    def test_signed_video_url_expiry_overrides_local_cache_guess(self):
        signed_url = (
            "https://ark-acg.example.test/video.mp4?"
            "X-Tos-Date=20260611T173023Z&X-Tos-Expires=86400&X-Tos-Signature=secret"
        )

        expires_at = self.server._signed_video_url_expires_at(signed_url)

        self.assertEqual(expires_at, 1781285423)
        self.assertEqual(self.server._cache_until_for_video_url(signed_url, 1781199023), 1781281823)
        self.assertEqual(self.server._cache_until_for_video_url(signed_url, 1781289000), 1781285423)

    def test_video_content_refreshes_signed_expired_url_even_when_cached_until_future(self):
        user = self.create_user("signed-expired-content-url@example.test")
        expired_signed_url = (
            "https://ark-acg.example.test/expired.mp4?"
            "X-Tos-Date=20200101T000000Z&X-Tos-Expires=60&X-Tos-Signature=secret"
        )
        db = self.server.get_db()
        db.execute(
            """INSERT INTO tasks
               (id, user_id, upstream_task_id, upstream_model, client_model,
                resolution, duration, status, settled, cached_video_url,
                cached_video_url_until, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "vid_signed_expired_content_url",
                user["id"],
                "upstream-signed-expired-content-url",
                "dreamina-seedance-2-0-260128",
                "dreamina-seedance-2-0-260128",
                "480p",
                5,
                "succeeded",
                1,
                expired_signed_url,
                9999999999,
                1,
                1,
            ),
        )
        db.close()
        self.fake_http.get_payload = {
            "id": "upstream-signed-expired-content-url",
            "status": "succeeded",
            "content": {"video_url": "https://byteplus.example.test/fresh.mp4"},
        }

        response = self.client.get(
            "/v1/videos/vid_signed_expired_content_url/content",
            headers={**self.auth_headers(user["api_key"]), "Range": "bytes=3-5"},
        )

        self.assertEqual(response.status_code, 206, response.text)
        self.assertEqual(self.fake_http.gets[-1]["url"].split("/")[-1], "upstream-signed-expired-content-url")
        self.assertEqual(self.fake_http.streams[-1]["url"], "https://byteplus.example.test/fresh.mp4")

    def test_succeeded_task_response_includes_content_countdown(self):
        user = self.create_user("content-countdown@example.test")
        now = int(time.time())
        db = self.server.get_db()
        db.execute(
            """INSERT INTO tasks
               (id, user_id, upstream_task_id, upstream_model, client_model,
                resolution, duration, status, settled, cached_video_url,
                cached_video_url_until, local_video_expires_at, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "vid_content_countdown",
                user["id"],
                "upstream-content-countdown",
                "dreamina-seedance-2-0-260128",
                "dreamina-seedance-2-0-260128",
                "480p",
                5,
                "succeeded",
                1,
                "https://byteplus.example.test/video.mp4",
                now + 86400,
                now + 172800,
                now,
                now,
            ),
        )
        db.close()

        response = self.client.get(
            "/v1/videos/vid_content_countdown",
            headers=self.auth_headers(user["api_key"]),
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["content_retention_seconds"], 172800)
        self.assertGreater(body["content_seconds_remaining"], 172700)
        self.assertFalse(body["content_expired"])
        self.assertEqual(body["upstream_video_url"], "https://byteplus.example.test/video.mp4")
        self.assertEqual(body["upstream_content_retention_seconds"], 86400)
        self.assertGreater(body["upstream_content_seconds_remaining"], 86300)
        self.assertFalse(body["upstream_content_expired"])

    def test_local_video_expiry_is_capped_by_current_retention_policy(self):
        now = int(time.time())
        original_mode = self.server.VIDEO_PERSIST_MODE
        try:
            self.server.VIDEO_PERSIST_MODE = "local"
            expires_at = self.server._task_content_expires_at({
                "status": "succeeded",
                "updated_at": now,
                "local_video_expires_at": now + 604800,
            })
        finally:
            self.server.VIDEO_PERSIST_MODE = original_mode

        self.assertEqual(expires_at, now + 172800)

    def test_expired_local_video_returns_clear_error_and_removes_file(self):
        user = self.create_user("expired-local-video@example.test")
        video_path = Path(self.tmp.name) / "videos" / "expired-local.mp4"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"expired-video")
        db = self.server.get_db()
        db.execute(
            """INSERT INTO tasks
               (id, user_id, upstream_task_id, upstream_model, client_model,
                resolution, duration, status, settled, cached_video_url,
                cached_video_url_until, local_video_path, local_video_expires_at,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "vid_expired_local",
                user["id"],
                "upstream-expired-local",
                "dreamina-seedance-2-0-260128",
                "dreamina-seedance-2-0-260128",
                "480p",
                5,
                "succeeded",
                1,
                "https://byteplus.example.test/video.mp4",
                int(time.time()) + 3600,
                str(video_path),
                int(time.time()) - 1,
                1,
                1,
            ),
        )
        db.close()

        response = self.client.get(
            "/v1/videos/vid_expired_local/content",
            headers=self.auth_headers(user["api_key"]),
        )

        self.assertEqual(response.status_code, 410, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "video_expired")
        self.assertFalse(video_path.exists())

    def test_video_content_proxy_rejects_range_when_upstream_returns_full_body(self):
        user = self.create_user("proxy-range-unsupported@example.test")
        db = self.server.get_db()
        db.execute(
            """INSERT INTO tasks
               (id, user_id, upstream_task_id, upstream_model, client_model,
                resolution, duration, status, settled, cached_video_url,
                cached_video_url_until, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "vid_proxy_range_unsupported",
                user["id"],
                "upstream-proxy-range-unsupported",
                "dreamina-seedance-2-0-260128",
                "dreamina-seedance-2-0-260128",
                "480p",
                5,
                "succeeded",
                1,
                "https://byteplus.example.test/private.mp4",
                9999999999,
                1,
                1,
            ),
        )
        db.close()
        self.fake_http.stream_response = FakeStreamRangeUnsupportedResponse()

        response = self.client.get(
            "/v1/videos/vid_proxy_range_unsupported/content",
            headers={**self.auth_headers(user["api_key"]), "Range": "bytes=0-1"},
        )

        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "proxy_range_unsupported")
        self.assertNotIn("content-range", response.headers)
        self.assertNotIn("byteplus", response.text.lower())

    def test_video_content_rejects_another_customers_task_before_upstream(self):
        owner = self.create_user("video-owner@example.test")
        other = self.create_user("video-other@example.test")
        db = self.server.get_db()
        db.execute(
            """INSERT INTO tasks
               (id, user_id, upstream_task_id, upstream_model, client_model,
                resolution, duration, status, settled, cached_video_url,
                cached_video_url_until, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "vid_private",
                owner["id"],
                "upstream-private",
                "dreamina-seedance-2-0-260128",
                "dreamina-seedance-2-0-260128",
                "480p",
                5,
                "succeeded",
                1,
                "https://byteplus.example.test/private.mp4",
                9999999999,
                1,
                1,
            ),
        )
        db.close()

        response = self.client.get(
            "/v1/videos/vid_private/content",
            headers=self.auth_headers(other["api_key"]),
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.fake_http.streams, [])

    def test_delete_video_upstream_failure_returns_safe_request_id_without_body_leakage(self):
        user = self.create_user("delete-upstream-failure@example.test")
        db = self.server.get_db()
        db.execute(
            """INSERT INTO tasks
               (id, user_id, upstream_task_id, upstream_model, client_model,
                resolution, duration, status, settled, cached_video_url,
                cached_video_url_until, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "vid_delete_upstream_failure",
                user["id"],
                "upstream-delete-failure",
                "dreamina-seedance-2-0-260128",
                "dreamina-seedance-2-0-260128",
                "480p",
                5,
                "succeeded",
                1,
                "https://byteplus.example.test/private.mp4",
                9999999999,
                1,
                1,
            ),
        )
        db.close()
        self.fake_http.delete_response = FakeResponse(
            status_code=403,
            text="cannot delete https://byteplus.example.test/private.mp4 using ark-customer-secret-key",
            headers={"x-tt-logid": "delete_req_safe_123"},
        )

        response = self.client.delete(
            "/v1/videos/vid_delete_upstream_failure",
            headers=self.auth_headers(user["api_key"]),
        )

        self.assertEqual(response.status_code, 409, response.text)
        body = response.json()["detail"]["error"]
        self.assertEqual(body["code"], "cannot_delete")
        self.assertEqual(body["request_id"], "delete_req_safe_123")
        self.assertNotIn("byteplus", response.text.lower())
        self.assertNotIn("private.mp4", response.text)
        self.assertNotIn("ark-customer-secret-key", response.text)

    def test_video_content_head_returns_proxy_metadata_without_body(self):
        user = self.create_user("head@example.test")
        db = self.server.get_db()
        db.execute(
            """INSERT INTO tasks
               (id, user_id, upstream_task_id, upstream_model, client_model,
                resolution, duration, status, settled, cached_video_url,
                cached_video_url_until, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "vid_head",
                user["id"],
                "upstream-head",
                "dreamina-seedance-2-0-260128",
                "dreamina-seedance-2-0-260128",
                "480p",
                5,
                "succeeded",
                1,
                "https://byteplus.example.test/video.mp4",
                9999999999,
                1,
                1,
            ),
        )
        db.close()

        response = self.client.head(
            "/v1/videos/vid_head/content",
            headers={**self.auth_headers(user["api_key"]), "Range": "bytes=3-5"},
        )

        self.assertEqual(response.status_code, 206, response.text)
        self.assertEqual(response.content, b"")
        self.assertEqual(response.headers["content-range"], "bytes 3-5/10")
        self.assertEqual(self.fake_http.streams[-1]["method"], "GET")
        self.assertEqual(self.fake_http.streams[-1]["headers"]["Range"], "bytes=3-5")

    def test_internal_runtime_prepare_video_content_materializes_real_person_assets(self):
        self.server.FACE_ASSET_SELF_SERVICE = True

        def fake_register(url, purpose, user=None):
            return {
                "asset_id": f"asset-{purpose}",
                "asset_url": f"asset://asset-{purpose}",
                "asset_status": "created",
            }

        self.server._register_upload_asset = fake_register
        user = self.create_user("runtime-materialize@example.test")

        response = self.client.post(
            "/internal/runtime/prepare-video-content",
            headers={"X-Runtime-Token": "runtime-internal-test"},
            json={
                "user_id": user["id"],
                "extra_body": {"real_person_mode": True},
                "content": [
                    {"type": "text", "text": "A signed customer prompt passes through."},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://cdn.example.test/person.jpg"},
                        "role": "reference_image",
                    },
                ],
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(
            body["content"][1],
            {
                "type": "image_url",
                "image_url": {"url": "asset://asset-image"},
                "role": "reference_image",
            },
        )
        db = self.server.get_db()
        upload = db.execute(
            "SELECT * FROM uploads WHERE user_id=? AND url=?",
            (user["id"], "https://cdn.example.test/person.jpg"),
        ).fetchone()
        db.close()
        self.assertIsNotNone(upload)
        self.assertEqual(upload["asset_url"], "asset://asset-image")
        self.assertEqual(upload["face_asset_whitelisted"], 1)

    def test_internal_runtime_prepare_refreshes_expiring_endpoint_key(self):
        user = self.create_user("runtime-refresh-key@example.test")
        now = int(time.time())
        note = {
            "upstream_mode": "auto_dedicated",
            "byteplus_endpoint_map": {
                "dreamina-seedance-2-0-260128": "ep-standard",
                "seedance-1-5-pro-251215": "ep-seedance15",
            },
            "byteplus_endpoint_key_rotation_enabled": True,
            "byteplus_endpoint_api_key_expires_at": now - 60,
        }
        db = self.server.get_db()
        db.execute(
            "UPDATE users SET byteplus_api_key=?, note=? WHERE id=?",
            ("expired-endpoint-key", json.dumps(note), user["id"]),
        )
        db.close()

        calls = []

        def fake_get_endpoint_api_key(endpoint_ids, duration_seconds):
            calls.append((endpoint_ids, duration_seconds))
            return {"api_key": "fresh-endpoint-key", "expires_at": now + 2592000}

        self.server._get_endpoint_api_key = fake_get_endpoint_api_key

        response = self.client.post(
            "/internal/runtime/prepare-video-content",
            headers={"X-Runtime-Token": "runtime-internal-test"},
            json={
                "user_id": user["id"],
                "client_model": "dreamina-seedance-2-0-260128",
                "upstream_model": "dreamina-seedance-2-0-260128",
                "content": [{"type": "text", "text": "A signed customer prompt passes through."}],
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["upstream_model"], "ep-standard")
        self.assertEqual(body["upstream_api_key"], "fresh-endpoint-key")
        self.assertEqual(set(calls[0][0]), {"ep-standard", "ep-seedance15"})
        self.assertEqual(calls[0][1], 2592000)

        db = self.server.get_db()
        row = db.execute("SELECT byteplus_api_key, note FROM users WHERE id=?", (user["id"],)).fetchone()
        db.close()
        self.assertEqual(row["byteplus_api_key"], "fresh-endpoint-key")
        stored_note = json.loads(row["note"])
        self.assertEqual(stored_note["byteplus_endpoint_key_rotation_error"], "")
        self.assertGreater(stored_note["byteplus_endpoint_key_next_rotate_at"], now)

    def test_internal_runtime_prepare_rejects_invalid_content_role(self):
        user = self.create_user("runtime-invalid-role@example.test")

        response = self.client.post(
            "/internal/runtime/prepare-video-content",
            headers={"X-Runtime-Token": "runtime-internal-test"},
            json={
                "user_id": user["id"],
                "content": [
                    {"type": "text", "text": "A signed customer prompt passes through."},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://cdn.example.test/person.jpg"},
                        "role": "unsupported_reference_role",
                    },
                ],
            },
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "invalid_content_role")

    def test_admin_password_reset_revokes_existing_customer_sessions(self):
        user = self.create_user("admin-reset-session@example.test")
        stale_session = self.server.create_session(user["id"])

        response = self.client.patch(
            f"/admin/users/{user['id']}",
            headers=self.admin_headers(),
            json={"new_password": "admin-reset-password"},
        )
        self.assertEqual(response.status_code, 200, response.text)

        stale_session_me = self.client.get(
            "/auth/me",
            headers={"Cookie": f"relay_session={stale_session}"},
        )
        self.assertEqual(stale_session_me.status_code, 401, stale_session_me.text)

    def test_admin_password_reset_rejects_short_password(self):
        user = self.create_user("admin-short-reset@example.test")

        response = self.client.patch(
            f"/admin/users/{user['id']}",
            headers=self.admin_headers(),
            json={"new_password": "short"},
        )

        self.assertEqual(response.status_code, 422, response.text)

    def test_admin_password_reset_endpoint_generates_temporary_password_and_flags_change(self):
        user = self.create_user("admin-reset-generate@example.test")
        stale_session = self.server.create_session(user["id"])

        response = self.client.post(
            f"/admin/users/{user['id']}/password/reset",
            headers=self.admin_headers(),
            json={"generate": True, "force_change_on_next_login": True},
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["shown_once"])
        self.assertGreaterEqual(len(body["temporary_password"]), 10)
        self.assertNotIn("password_hash", response.text)

        stale_session_me = self.client.get(
            "/auth/me",
            headers={"Cookie": f"relay_session={stale_session}"},
        )
        self.assertEqual(stale_session_me.status_code, 401, stale_session_me.text)

        login = self.client.post(
            "/auth/login",
            json={
                "email": "admin-reset-generate@example.test",
                "password": body["temporary_password"],
            },
        )
        self.assertEqual(login.status_code, 200, login.text)

        db = self.server.get_db()
        row = db.execute("SELECT note FROM users WHERE id=?", (user["id"],)).fetchone()
        db.close()
        note = json.loads(row["note"])
        self.assertTrue(note["must_change_password"])

    def test_admin_password_reset_endpoint_accepts_manual_password_without_echoing_it(self):
        user = self.create_user("admin-reset-manual@example.test")

        response = self.client.post(
            f"/admin/users/{user['id']}/password/reset",
            headers=self.admin_headers(),
            json={
                "generate": False,
                "new_password": "manual-reset-password",
                "force_change_on_next_login": False,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertIsNone(body.get("temporary_password"))
        self.assertNotIn("manual-reset-password", response.text)

        login = self.client.post(
            "/auth/login",
            json={
                "email": "admin-reset-manual@example.test",
                "password": "manual-reset-password",
            },
        )
        self.assertEqual(login.status_code, 200, login.text)

    def test_admin_password_reset_clears_temporary_login_lock(self):
        user = self.create_user("admin-reset-locked@example.test")
        for _ in range(5):
            failed = self.client.post(
                "/auth/login",
                json={"email": "admin-reset-locked@example.test", "password": "wrong-password"},
            )
            self.assertEqual(failed.status_code, 401, failed.text)

        locked = self.client.post(
            "/auth/login",
            json={"email": "admin-reset-locked@example.test", "password": "initial-password"},
        )
        self.assertEqual(locked.status_code, 423, locked.text)

        reset = self.client.post(
            f"/admin/users/{user['id']}/password/reset",
            headers=self.admin_headers(),
            json={
                "generate": False,
                "new_password": "manual-reset-password",
                "force_change_on_next_login": False,
            },
        )
        self.assertEqual(reset.status_code, 200, reset.text)

        login = self.client.post(
            "/auth/login",
            json={
                "email": "admin-reset-locked@example.test",
                "password": "manual-reset-password",
            },
        )
        self.assertEqual(login.status_code, 200, login.text)

        db = self.server.get_db()
        row = db.execute(
            "SELECT failed_login_count, locked_until FROM users WHERE id=?",
            (user["id"],),
        ).fetchone()
        db.close()
        self.assertEqual(row["failed_login_count"], 0)
        self.assertIsNone(row["locked_until"])

    def test_admin_api_key_rotation_revokes_existing_customer_sessions(self):
        user = self.create_user("admin-rotate-session@example.test")
        old_key = user["api_key"]
        stale_session = self.server.create_session(user["id"])

        response = self.client.post(
            f"/admin/users/{user['id']}/api-key/rotate",
            headers=self.admin_headers(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        new_key = response.json()["api_key"]
        self.assertTrue(new_key.startswith("sk-"))
        self.assertNotEqual(new_key, old_key)

        old_key_me = self.client.get("/v1/me", headers=self.auth_headers(old_key))
        self.assertEqual(old_key_me.status_code, 401, old_key_me.text)

        new_key_me = self.client.get("/v1/me", headers=self.auth_headers(new_key))
        self.assertEqual(new_key_me.status_code, 200, new_key_me.text)

        stale_session_me = self.client.get(
            "/auth/me",
            headers={"Cookie": f"relay_session={stale_session}"},
        )
        self.assertEqual(stale_session_me.status_code, 401, stale_session_me.text)

    def test_admin_disable_via_patch_revokes_sessions_and_audits_status(self):
        user = self.create_user("patch-disable-session@example.test")
        stale_session = self.server.create_session(user["id"])

        response = self.client.patch(
            f"/admin/users/{user['id']}",
            headers=self.admin_headers(),
            json={"is_active": False},
        )
        self.assertEqual(response.status_code, 200, response.text)

        db = self.server.get_db()
        stale = db.execute("SELECT token FROM sessions WHERE token=?", (stale_session,)).fetchone()
        db.close()
        self.assertIsNone(stale)

        audit = self.client.get(
            f"/admin/audit-events?target_id={user['id']}&limit=10",
            headers=self.admin_headers(),
        )
        self.assertEqual(audit.status_code, 200, audit.text)
        actions = [item["action"] for item in audit.json()["data"]]
        self.assertIn("admin_changed_status", actions)

    def test_admin_delete_revokes_sessions_and_audits_status(self):
        user = self.create_user("delete-disable-session@example.test")
        stale_session = self.server.create_session(user["id"])

        response = self.client.delete(
            f"/admin/users/{user['id']}",
            headers=self.admin_headers(),
        )
        self.assertEqual(response.status_code, 200, response.text)

        db = self.server.get_db()
        stale = db.execute("SELECT token FROM sessions WHERE token=?", (stale_session,)).fetchone()
        db.close()
        self.assertIsNone(stale)

        audit = self.client.get(
            f"/admin/audit-events?target_id={user['id']}&limit=10",
            headers=self.admin_headers(),
        )
        self.assertEqual(audit.status_code, 200, audit.text)
        actions = [item["action"] for item in audit.json()["data"]]
        self.assertIn("admin_changed_status", actions)

    def test_admin_can_list_customer_audit_events_without_secret_leakage(self):
        user = self.create_user("audit@example.test")

        updated = self.client.patch(
            f"/admin/users/{user['id']}",
            headers=self.admin_headers(),
            json={
                "price_multiplier": 1.25,
                "enabled_models": ["dreamina-seedance-2-0-260128"],
                "new_password": "updated-password",
                "byteplus_api_key": "ark-new-secret-key",
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)

        rotated = self.client.post(
            f"/admin/users/{user['id']}/api-key/rotate",
            headers=self.admin_headers(),
        )
        self.assertEqual(rotated.status_code, 200, rotated.text)

        topped = self.client.post(
            f"/admin/users/{user['id']}/topup",
            headers=self.admin_headers(),
            json={
                "amount_usd": 12.5,
                "note": (
                    "manual audit topup sk-leaked-audit-key-12345 "
                    "ark-audit-secret-key https://byteplus.example.test/private.mp4"
                ),
            },
        )
        self.assertEqual(topped.status_code, 200, topped.text)

        response = self.client.get(
            f"/admin/audit-events?target_id={user['id']}&limit=20",
            headers=self.admin_headers(),
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        actions = [item["action"] for item in body["data"]]
        for expected in (
            "admin_changed_price_multiplier",
            "admin_changed_enabled_models",
            "admin_reset_password",
            "admin_changed_upstream_key",
            "admin_rotated_customer_api_key",
            "admin_changed_balance",
        ):
            self.assertIn(expected, actions)

        raw = response.text
        self.assertNotIn("ark-new-secret-key", raw)
        self.assertNotIn("ark-audit-secret-key", raw)
        self.assertNotIn("sk-leaked-audit-key-12345", raw)
        self.assertNotIn("byteplus.example.test", raw)
        self.assertNotIn("private.mp4", raw)
        self.assertNotIn("media.example.test", raw)
        self.assertNotIn(rotated.json()["api_key"], raw)
        self.assertIn("<redacted-upstream-key>", raw)
        self.assertIn("<redacted-relay-key>", raw)
        self.assertIn("<redacted-url>", raw)
        self.assertEqual(body["limit"], 20)
        self.assertGreaterEqual(body["total"], 6)
        upstream_key_event = next(item for item in body["data"] if item["action"] == "admin_changed_upstream_key")
        self.assertEqual(upstream_key_event["metadata"]["field"], "byteplus_api_key")
        self.assertIs(upstream_key_event["metadata"]["secret_changed"], True)

    def test_audit_metadata_redacts_authorization_cookie_and_credential_fields(self):
        user = self.create_user("audit-field-redaction@example.test")
        self.server._audit_event(
            "admin_changed_balance",
            actor_user_id=None,
            actor_type="admin",
            target_type="user",
            target_id=user["id"],
            metadata={
                "authorization": "Bearer opaque-admin-token",
                "cookie": "relay_session=opaque-cookie",
                "admin_key": "plain-admin-key",
                "X-Admin-Key": "hyphen-admin-key",
                "nested": {
                    "credential": "plain-credential-value",
                    "client_secret": "plain-client-secret",
                    "client_secret_changed": True,
                    "admin-key": "nested-hyphen-admin-key",
                    "relay_key": "plain-relay-key",
                    "upstream-key": "plain-upstream-key",
                    "byteplus-key": "plain-byteplus-key",
                },
                "note": (
                    "ordinary operational note with Authorization: Bearer note-token "
                    "and relay_session=note-cookie and X-Admin-Key: note-admin-key "
                    "and ADMIN_KEY=env-admin-key"
                ),
            },
        )

        response = self.client.get(
            f"/admin/audit-events?target_id={user['id']}&limit=5",
            headers=self.admin_headers(),
        )

        self.assertEqual(response.status_code, 200, response.text)
        raw = response.text
        self.assertNotIn("opaque-admin-token", raw)
        self.assertNotIn("opaque-cookie", raw)
        self.assertNotIn("plain-credential-value", raw)
        self.assertNotIn("plain-client-secret", raw)
        self.assertNotIn("plain-admin-key", raw)
        self.assertNotIn("hyphen-admin-key", raw)
        self.assertNotIn("nested-hyphen-admin-key", raw)
        self.assertNotIn("plain-relay-key", raw)
        self.assertNotIn("plain-upstream-key", raw)
        self.assertNotIn("plain-byteplus-key", raw)
        self.assertNotIn("note-token", raw)
        self.assertNotIn("note-cookie", raw)
        self.assertNotIn("note-admin-key", raw)
        self.assertNotIn("env-admin-key", raw)
        self.assertIn("ordinary operational note", raw)
        self.assertIn("Authorization: Bearer <redacted>", raw)
        self.assertIn("relay_session=<redacted>", raw)
        self.assertIn("X-Admin-Key: <redacted>", raw)
        self.assertIn("ADMIN_KEY=<redacted>", raw)
        event = next(item for item in response.json()["data"] if item["action"] == "admin_changed_balance")
        self.assertEqual(event["metadata"]["authorization"], "<redacted>")
        self.assertEqual(event["metadata"]["cookie"], "<redacted>")
        self.assertEqual(event["metadata"]["admin_key"], "<redacted>")
        self.assertEqual(event["metadata"]["X-Admin-Key"], "<redacted>")
        self.assertEqual(event["metadata"]["nested"]["credential"], "<redacted>")
        self.assertEqual(event["metadata"]["nested"]["client_secret"], "<redacted>")
        self.assertIs(event["metadata"]["nested"]["client_secret_changed"], True)
        self.assertEqual(event["metadata"]["nested"]["admin-key"], "<redacted>")
        self.assertEqual(event["metadata"]["nested"]["relay_key"], "<redacted>")
        self.assertEqual(event["metadata"]["nested"]["upstream-key"], "<redacted>")
        self.assertEqual(event["metadata"]["nested"]["byteplus-key"], "<redacted>")

    def test_admin_patch_cannot_set_arbitrary_customer_api_key(self):
        user = self.create_user("manual-key@example.test")
        old_key = user["api_key"]

        response = self.client.patch(
            f"/admin/users/{user['id']}",
            headers=self.admin_headers(),
            json={"api_key": "sk-admin-chosen-key"},
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "api_key_rotation_required")

        old_key_still_works = self.client.get("/v1/me", headers=self.auth_headers(old_key))
        self.assertEqual(old_key_still_works.status_code, 200, old_key_still_works.text)

    def test_admin_create_cannot_set_arbitrary_customer_api_key(self):
        response = self.client.post(
            "/admin/users",
            headers=self.admin_headers(),
            json={
                "email": "manual-create-key@example.test",
                "balance_usd": 10,
                "api_key": "sk-admin-chosen-create-key",
            },
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "api_key_generation_required")

        db = self.server.get_db()
        row = db.execute(
            "SELECT id FROM users WHERE email=?",
            ("manual-create-key@example.test",),
        ).fetchone()
        db.close()
        self.assertIsNone(row)

    def test_admin_create_rejects_short_customer_password(self):
        response = self.client.post(
            "/admin/users",
            headers=self.admin_headers(),
            json={
                "email": "short-password-create@example.test",
                "balance_usd": 10,
                "password": "short",
            },
        )

        self.assertEqual(response.status_code, 422, response.text)

        db = self.server.get_db()
        row = db.execute(
            "SELECT id FROM users WHERE email=?",
            ("short-password-create@example.test",),
        ).fetchone()
        db.close()
        self.assertIsNone(row)


if __name__ == "__main__":
    unittest.main()

