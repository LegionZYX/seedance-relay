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


class FakeResponse:
    status_code = 200
    text = '{"id":"upstream-task"}'

    def json(self):
        return {"id": "upstream-task"}


class FakeHttp:
    def __init__(self):
        self.posts = []
        self.fail_on_post = True

    async def post(self, url, json, headers, timeout):
        self.posts.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self.fail_on_post:
            raise AssertionError("IAM upstream mode should not use Bearer HTTP task creation")
        return FakeResponse()


class FakeTasks:
    def __init__(self):
        self.created = []
        self.retrieved = []

    def create(self, **payload):
        self.created.append(payload)
        return {"id": "iam-upstream-task"}

    def retrieve(self, task_id):
        self.retrieved.append(task_id)
        return {"id": task_id, "status": "running"}


class FakeContentGeneration:
    def __init__(self, tasks):
        self.tasks = tasks


class FakeArkClient:
    def __init__(self, tasks):
        self.content_generation = FakeContentGeneration(tasks)


class IAMUpstreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        os.environ["DB_PATH"] = str(root / "relay.sqlite")
        os.environ["VIDEO_DIR"] = str(root / "videos")
        os.environ["UPLOAD_DIR"] = str(root / "uploads")
        os.environ["PUBLIC_DOMAIN"] = "media.example.test"
        os.environ["ADMIN_PASSWORD"] = ""
        os.environ["ADMIN_KEY"] = "admin-iam"
        os.environ["UPSTREAM_API_KEY"] = ""
        os.environ["UPSTREAM_AUTH_MODE"] = "iam"
        os.environ["UPSTREAM_ENDPOINT_ID"] = "ep-dreamina-real-person"
        os.environ["BYTEPLUS_ACCESSKEY"] = "ak-test"
        os.environ["BYTEPLUS_SECRETKEY"] = "sk-test"

        if str(PROJECT_DIR) not in sys.path:
            sys.path.insert(0, str(PROJECT_DIR))
        sys.modules.pop("relay_server", None)
        self.server = importlib.import_module("relay_server")
        self.fake_http = FakeHttp()
        self.fake_tasks = FakeTasks()
        self.server.http = self.fake_http
        self.server._ark_iam_client = FakeArkClient(self.fake_tasks)

        self.api_key = "sk-iam-customer"
        db = self.server.get_db()
        db.execute(
            """INSERT INTO users
               (id, api_key, email, balance_usd, is_active, is_admin, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            ("u_iam", self.api_key, "iam@example.test", 100.0, 1, 0, int(time.time())),
        )
        db.close()
        self.client = TestClient(self.server.app)

    def tearDown(self):
        self.tmp.cleanup()
        for key in (
            "UPSTREAM_AUTH_MODE",
            "UPSTREAM_ENDPOINT_ID",
            "BYTEPLUS_ACCESSKEY",
            "BYTEPLUS_SECRETKEY",
        ):
            os.environ.pop(key, None)

    def test_iam_mode_submits_generation_with_endpoint_id(self):
        response = self.client.post(
            "/v1/videos",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": "dreamina-seedance-2-0-260128",
                "content": [{"type": "text", "text": "A calm studio portrait motion."}],
                "resolution": "480p",
                "duration": 5,
                "generate_audio": False,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.fake_http.posts, [])
        self.assertEqual(len(self.fake_tasks.created), 1)
        self.assertEqual(self.fake_tasks.created[0]["model"], "ep-dreamina-real-person")
        self.assertEqual(response.json()["model"], "dreamina-seedance-2-0-260128")

    def test_iam_mode_with_endpoint_api_key_uses_bearer_endpoint_request(self):
        self.server.UPSTREAM_ENDPOINT_API_KEY = "endpoint-task-key"
        self.fake_http.fail_on_post = False

        response = self.client.post(
            "/v1/videos",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": "dreamina-seedance-2-0-260128",
                "content": [{"type": "text", "text": "A calm studio portrait motion."}],
                "resolution": "480p",
                "duration": 5,
                "generate_audio": False,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.fake_tasks.created, [])
        self.assertEqual(len(self.fake_http.posts), 1)
        self.assertEqual(self.fake_http.posts[0]["headers"]["Authorization"], "Bearer endpoint-task-key")
        self.assertEqual(self.fake_http.posts[0]["json"]["model"], "ep-dreamina-real-person")

    def test_iam_mode_customer_endpoint_note_uses_customer_endpoint_key(self):
        self.fake_http.fail_on_post = False
        db = self.server.get_db()
        db.execute(
            "UPDATE users SET byteplus_api_key=?, note=? WHERE id=?",
            (
                "customer-endpoint-task-key",
                '{"byteplus_endpoint_id":"ep-customer-dedicated"}',
                "u_iam",
            ),
        )
        db.close()

        response = self.client.post(
            "/v1/videos",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": "dreamina-seedance-2-0-260128",
                "content": [{"type": "text", "text": "A calm studio portrait motion."}],
                "resolution": "480p",
                "duration": 5,
                "generate_audio": False,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.fake_tasks.created, [])
        self.assertEqual(len(self.fake_http.posts), 1)
        self.assertEqual(
            self.fake_http.posts[0]["headers"]["Authorization"],
            "Bearer customer-endpoint-task-key",
        )
        self.assertEqual(self.fake_http.posts[0]["json"]["model"], "ep-customer-dedicated")

    def test_iam_mode_customer_endpoint_map_routes_by_client_model(self):
        self.fake_http.fail_on_post = False
        db = self.server.get_db()
        db.execute(
            "UPDATE users SET byteplus_api_key=?, note=? WHERE id=?",
            (
                "customer-multi-endpoint-key",
                json.dumps(
                    {
                        "byteplus_endpoint_id": "ep-default-standard",
                        "byteplus_endpoint_map": {
                            "dreamina-seedance-2-0-260128": "ep-standard",
                            "seedance-1-5-pro-251215": "ep-seedance15",
                        },
                    }
                ),
                "u_iam",
            ),
        )
        db.close()

        response = self.client.post(
            "/v1/videos",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": "seedance-1-5-pro-251215",
                "content": [{"type": "text", "text": "A premium cinematic product reveal."}],
                "resolution": "480p",
                "duration": 5,
                "generate_audio": False,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.fake_tasks.created, [])
        self.assertEqual(len(self.fake_http.posts), 1)
        self.assertEqual(
            self.fake_http.posts[0]["headers"]["Authorization"],
            "Bearer customer-multi-endpoint-key",
        )
        self.assertEqual(self.fake_http.posts[0]["json"]["model"], "ep-seedance15")
        self.assertEqual(response.json()["model"], "seedance-1-5-pro-251215")

    def test_iam_mode_customer_endpoint_map_uses_per_endpoint_key_for_selected_model(self):
        self.fake_http.fail_on_post = False
        note = {
            "byteplus_endpoint_id": "ep-default-standard",
            "byteplus_endpoint_map": {
                "dreamina-seedance-2-0-260128": "ep-standard",
                "dreamina-seedance-2-0-fast-260128": "ep-fast",
            },
            "byteplus_endpoint_key_map": {
                "dreamina-seedance-2-0-260128": {
                    "endpoint_id": "ep-standard",
                    "api_key": "standard-endpoint-key",
                    "expires_at": 3333333333,
                },
                "dreamina-seedance-2-0-fast-260128": {
                    "endpoint_id": "ep-fast",
                    "api_key": "fast-endpoint-key",
                    "expires_at": 3333333333,
                },
            },
            "byteplus_endpoint_key_mode": "per_endpoint",
        }
        db = self.server.get_db()
        db.execute(
            "UPDATE users SET byteplus_api_key=?, note=? WHERE id=?",
            ("legacy-map-key", json.dumps(note), "u_iam"),
        )
        db.close()

        response = self.client.post(
            "/v1/videos",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": "dreamina-seedance-2-0-fast-260128",
                "content": [{"type": "text", "text": "A fast route request."}],
                "resolution": "480p",
                "duration": 5,
                "generate_audio": False,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.fake_tasks.created, [])
        self.assertEqual(len(self.fake_http.posts), 1)
        self.assertEqual(
            self.fake_http.posts[0]["headers"]["Authorization"],
            "Bearer fast-endpoint-key",
        )
        self.assertEqual(self.fake_http.posts[0]["json"]["model"], "ep-fast")

    def test_iam_mode_customer_endpoint_map_blocks_unmapped_model(self):
        self.fake_http.fail_on_post = False
        db = self.server.get_db()
        db.execute(
            "UPDATE users SET byteplus_api_key=?, note=? WHERE id=?",
            (
                "customer-multi-endpoint-key",
                json.dumps(
                    {
                        "byteplus_endpoint_id": "ep-default-standard",
                        "byteplus_endpoint_map": {
                            "dreamina-seedance-2-0-260128": "ep-standard",
                        },
                    }
                ),
                "u_iam",
            ),
        )
        db.close()

        response = self.client.post(
            "/v1/videos",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": "dreamina-seedance-2-0-fast-260128",
                "content": [{"type": "text", "text": "A fast route request."}],
                "resolution": "480p",
                "duration": 5,
                "generate_audio": False,
            },
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error"]["code"], "endpoint_not_configured_for_model")
        self.assertEqual(self.fake_http.posts, [])
        self.assertEqual(self.fake_tasks.created, [])

    def test_iam_mode_refresh_ignores_legacy_customer_byteplus_key(self):
        db = self.server.get_db()
        db.execute(
            "UPDATE users SET byteplus_api_key=? WHERE id=?",
            ("ark-legacy-customer-key", "u_iam"),
        )
        db.close()

        created = self.client.post(
            "/v1/videos",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": "dreamina-seedance-2-0-260128",
                "content": [{"type": "text", "text": "A calm studio portrait motion."}],
                "resolution": "480p",
                "duration": 5,
                "generate_audio": False,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)

        detail = self.client.get(
            f"/v1/videos/{created.json()['id']}",
            headers={"Authorization": f"Bearer {self.api_key}"},
        )

        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(self.fake_tasks.retrieved, ["iam-upstream-task"])
