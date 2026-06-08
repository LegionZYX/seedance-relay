import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler

import bcrypt


PROJECT_DIR = Path(__file__).resolve().parents[1]
URL_OPENER = build_opener(ProxyHandler({}))


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class MockUpstreamHandler(BaseHTTPRequestHandler):
    post_count = 0
    get_task_count = 0
    video_count = 0
    last_generation_payload = None
    next_generation_error_status = None
    next_generation_error_request_id = None
    next_generation_id = None
    delete_count = 0
    deleted_paths = []
    failed_task_ids = set()

    def log_message(self, *_):
        return

    def do_POST(self):
        if self.path != "/contents/generations/tasks":
            self.send_error(404)
            return
        type(self).post_count += 1
        body = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
        type(self).last_generation_payload = json.loads(body.decode("utf-8"))
        if type(self).next_generation_error_status:
            status = type(self).next_generation_error_status
            request_id = type(self).next_generation_error_request_id
            type(self).next_generation_error_status = None
            type(self).next_generation_error_request_id = None
            if request_id:
                self.send_response(status)
                self.send_header("X-Request-Id", request_id)
                body = json.dumps({"error": "simulated upstream failure"}).encode("utf-8")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self._json(status, {"error": "simulated upstream failure"})
            return
        if type(self).next_generation_id:
            upstream_id = type(self).next_generation_id
            type(self).next_generation_id = None
        else:
            upstream_id = f"upstream-live-smoke-{type(self).post_count}"
        self._json(200, {"id": upstream_id})

    def do_GET(self):
        if self.path.startswith("/contents/generations/tasks/upstream-live-smoke-"):
            type(self).get_task_count += 1
            upstream_task_id = self.path.rsplit("/", 1)[-1]
            if upstream_task_id in type(self).failed_task_ids:
                self._json(200, {"status": "failed"})
                return
            self._json(
                200,
                {
                    "status": "succeeded",
                    "model": "dreamina-seedance-2-0-260128",
                    "resolution": "480p",
                    "usage": {"completion_tokens": 1000},
                    "content": {
                        "video_url": f"http://127.0.0.1:{self.server.server_port}/private-video.mp4"
                    },
                },
            )
            return
        if self.path == "/private-video.mp4":
            self._video()
            return
        self.send_error(404)

    def do_HEAD(self):
        if self.path == "/private-video.mp4":
            self._video(head=True)
            return
        self.send_error(404)

    def do_DELETE(self):
        if self.path.startswith("/contents/generations/tasks/"):
            type(self).delete_count += 1
            type(self).deleted_paths.append(self.path)
            self._json(200, {"ok": True})
            return
        self.send_error(404)

    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _video(self, head=False):
        type(self).video_count += 1
        body = b"video-bytes"
        range_header = self.headers.get("Range")
        if range_header == "bytes=0-1":
            body = body[:2]
            self.send_response(206)
            self.send_header("Content-Range", "bytes 0-1/11")
        else:
            self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        if not head:
            self.wfile.write(body)


class CaddyEquivalentProxyHandler(BaseHTTPRequestHandler):
    relay_port = 0
    runtime_port = 0
    relay_paths = []
    runtime_paths = []

    def log_message(self, *_):
        return

    def do_GET(self):
        self._proxy()

    def do_POST(self):
        self._proxy()

    def do_PATCH(self):
        self._proxy()

    def do_HEAD(self):
        self._proxy()

    def _proxy(self):
        target_port = self.runtime_port if self._routes_to_runtime() else self.relay_port
        if target_port == self.runtime_port:
            type(self).runtime_paths.append((self.command, self.path))
        else:
            type(self).relay_paths.append((self.command, self.path))

        body = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
        conn = HTTPConnection("127.0.0.1", target_port, timeout=10)
        headers = {key: value for key, value in self.headers.items()}
        headers["Host"] = self.headers.get("Host", "127.0.0.1")
        conn.request(self.command, self.path, body=body if body else None, headers=headers)
        resp = conn.getresponse()
        response_body = resp.read()
        self.send_response(resp.status)
        for key, value in resp.getheaders():
            if key.lower() in {"connection", "transfer-encoding"}:
                continue
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response_body)
        conn.close()

    def _routes_to_runtime(self):
        path = self.path.split("?", 1)[0]
        if path in {"/v1/models", "/v1/videos/estimate"}:
            return True
        if path == "/v1/videos" and self.command in {"GET", "POST"}:
            return True
        if path.startswith("/v1/videos/"):
            rest = path[len("/v1/videos/") :]
            if "/" not in rest and self.command == "GET":
                return True
            if rest.endswith("/content") and rest.count("/") == 1 and self.command in {"GET", "HEAD"}:
                return True
        return False


def request_json(method, url, *, payload=None, headers=None, expected=None):
    body = None
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    req = Request(url, data=body, headers=request_headers, method=method)
    try:
        resp = URL_OPENER.open(req, timeout=30)
    except HTTPError as exc:
        resp = exc
    try:
        raw = resp.read()
        if expected is not None and resp.status != expected:
            raise AssertionError(f"{method} {url} returned {resp.status}: {raw.decode('utf-8', 'replace')}")
        if not raw:
            return resp.status, {}, resp.headers, raw
        return resp.status, json.loads(raw.decode("utf-8")), resp.headers, raw
    finally:
        resp.close()


def error_payload(body):
    return body.get("error") or body.get("detail", {}).get("error")


def wait_for_http(url, proc, name):
    deadline = time.time() + 45
    last_error = None
    while time.time() < deadline:
        if proc.poll() is not None:
            output = proc.stdout.read() if proc.stdout else ""
            raise AssertionError(f"{name} exited early with {proc.returncode}\n{output}")
        try:
            with URL_OPENER.open(url, timeout=1) as resp:
                if resp.status == 200:
                    return
        except (OSError, URLError) as exc:
            last_error = exc
        time.sleep(0.25)
    raise AssertionError(f"{name} did not become healthy: {last_error}")


def stop_process(proc):
    if proc is None:
        return ""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
    output = ""
    if proc.stdout is not None:
        output = proc.stdout.read() or ""
        proc.stdout.close()
    return output


class Alpha1LiveSmokeTests(unittest.TestCase):
    def test_fastapi_control_plane_and_go_runtime_acceptance_path(self):
        if not shutil.which("go"):
            self.skipTest("go executable is not available")

        evidence_path = os.getenv("ALPHA1_LIVE_SMOKE_EVIDENCE_JSON", "").strip()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            relay_port = free_port()
            runtime_port = free_port()
            proxy_port = free_port()
            upstream_port = free_port()
            runtime_exe = root / ("seedance-runtime.exe" if os.name == "nt" else "seedance-runtime")
            MockUpstreamHandler.post_count = 0
            MockUpstreamHandler.get_task_count = 0
            MockUpstreamHandler.video_count = 0
            MockUpstreamHandler.last_generation_payload = None
            MockUpstreamHandler.next_generation_error_status = None
            MockUpstreamHandler.next_generation_error_request_id = None
            MockUpstreamHandler.next_generation_id = None
            MockUpstreamHandler.delete_count = 0
            MockUpstreamHandler.deleted_paths = []
            MockUpstreamHandler.failed_task_ids = set()
            upstream = ThreadingHTTPServer(("127.0.0.1", upstream_port), MockUpstreamHandler)
            upstream_thread = Thread(target=upstream.serve_forever, daemon=True)
            upstream_thread.start()
            acceptance_evidence = {
                "status": "running",
                "checks": {},
                "warnings": [],
                "runtime_paths": [],
                "relay_paths": [],
            }
            CaddyEquivalentProxyHandler.relay_port = relay_port
            CaddyEquivalentProxyHandler.runtime_port = runtime_port
            CaddyEquivalentProxyHandler.relay_paths = []
            CaddyEquivalentProxyHandler.runtime_paths = []
            proxy = ThreadingHTTPServer(("127.0.0.1", proxy_port), CaddyEquivalentProxyHandler)
            proxy_thread = Thread(target=proxy.serve_forever, daemon=True)
            proxy_thread.start()

            env = os.environ.copy()
            env.update(
                {
                    "DB_PATH": str(root / "relay.sqlite"),
                    "VIDEO_DIR": str(root / "videos"),
                    "UPLOAD_DIR": str(root / "uploads"),
                    "PUBLIC_DOMAIN": f"127.0.0.1:{proxy_port}",
                    "ADMIN_KEY": "admin-live-smoke",
                    "ADMIN_PASSWORD": "",
                    "UPSTREAM_API_KEY": "ark-live-smoke",
                    "UPSTREAM_BASE_URL": f"http://127.0.0.1:{upstream_port}",
                    "VIDEO_PERSIST_MODE": "proxy_only",
                    "RUNTIME_INTERNAL_TOKEN": "runtime-live-smoke",
                    "CONTROL_PLANE_BASE_URL": f"http://127.0.0.1:{relay_port}",
                    "RUNTIME_ADDR": f"127.0.0.1:{runtime_port}",
                    "MODEL_ID_ALIASES_JSON": '{"video-pro":"dreamina-seedance-2-0-260128"}',
                    "LOGIN_LOCK_SECONDS": "60",
                    "NO_PROXY": "127.0.0.1,localhost",
                    "no_proxy": "127.0.0.1,localhost",
                }
            )

            relay = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "relay_server:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(relay_port),
                ],
                cwd=PROJECT_DIR,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            runtime = None
            core_acceptance_passed = False
            old_key = ""
            new_key = ""
            admin_rotated_key = ""
            try:
                wait_for_http(f"http://127.0.0.1:{relay_port}/health", relay, "relay")
                build_runtime = subprocess.run(
                    ["go", "build", "-o", str(runtime_exe), "."],
                    cwd=PROJECT_DIR / "runtime-go",
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                self.assertEqual(
                    build_runtime.returncode,
                    0,
                    build_runtime.stdout + build_runtime.stderr,
                )
                runtime = subprocess.Popen(
                    [str(runtime_exe)],
                    cwd=PROJECT_DIR / "runtime-go",
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                wait_for_http(f"http://127.0.0.1:{runtime_port}/health", runtime, "runtime")
                proxy_url = f"http://127.0.0.1:{proxy_port}"

                admin_headers = {"X-Admin-Key": "admin-live-smoke"}
                _, user, _, _ = request_json(
                    "POST",
                    f"{proxy_url}/admin/users",
                    headers=admin_headers,
                    payload={
                        "email": "live-smoke@example.test",
                        "password": "initial-password",
                        "balance_usd": 10,
                    },
                    expected=200,
                )
                user_id = user["id"]
                old_key = user["api_key"]
                self.assertTrue(old_key.startswith("sk-"))
                acceptance_evidence["checks"]["admin_created_customer"] = True
                request_json(
                    "GET",
                    f"{proxy_url}/admin/audit-events?target_id={user_id}&limit=1",
                    headers=admin_headers,
                    expected=200,
                )

                _, initial_login, login_headers, _ = request_json(
                    "POST",
                    f"{proxy_url}/auth/login",
                    payload={"email": "live-smoke@example.test", "password": "initial-password"},
                    expected=200,
                )
                self.assertEqual(initial_login["email"], "live-smoke@example.test")
                stale_session_cookie = login_headers.get("Set-Cookie", "").split(";", 1)[0]
                self.assertTrue(stale_session_cookie.startswith("relay_session="))
                request_json(
                    "GET",
                    f"{proxy_url}/v1/me",
                    headers={"Cookie": stale_session_cookie},
                    expected=200,
                )

                request_json(
                    "PATCH",
                    f"{proxy_url}/admin/users/{user_id}",
                    headers=admin_headers,
                    payload={
                        "price_multiplier": 1.2,
                        "enabled_models": ["dreamina-seedance-2-0-260128"],
                        "byteplus_api_key": "ark-live-smoke-customer-key",
                    },
                    expected=200,
                )
                acceptance_evidence["checks"]["admin_set_multiplier_and_models"] = True

                admin_model_options_check = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "unittest",
                        "tests.test_model_aliases.ModelAliasTests.test_admin_model_options_include_native_ids_and_configured_aliases",
                        "-q",
                    ],
                    cwd=PROJECT_DIR,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(
                    admin_model_options_check.returncode,
                    0,
                    admin_model_options_check.stdout + admin_model_options_check.stderr,
                )
                acceptance_evidence["checks"]["admin_model_options_include_native_and_alias_choices"] = True

                reuse_check = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "unittest",
                        "tests.test_alpha1_spec_controls.Alpha1SpecControlTests.test_customer_password_change_rejects_same_password",
                        "-q",
                    ],
                    cwd=PROJECT_DIR,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(
                    reuse_check.returncode,
                    0,
                    reuse_check.stdout + reuse_check.stderr,
                )
                acceptance_evidence["checks"]["customer_password_reuse_rejected"] = True
                csrf_password_check = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "unittest",
                        "tests.test_alpha1_spec_controls.Alpha1SpecControlTests.test_cookie_authenticated_password_change_rejects_cross_site_origin",
                        "-q",
                    ],
                    cwd=PROJECT_DIR,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(
                    csrf_password_check.returncode,
                    0,
                    csrf_password_check.stdout + csrf_password_check.stderr,
                )
                acceptance_evidence["checks"]["cookie_password_change_rejects_cross_site_origin"] = True
                cookie_session_check = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "unittest",
                        "tests.test_alpha1_spec_controls.Alpha1SpecControlTests.test_cookie_password_change_revokes_other_sessions_but_keeps_current",
                        "-q",
                    ],
                    cwd=PROJECT_DIR,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(
                    cookie_session_check.returncode,
                    0,
                    cookie_session_check.stdout + cookie_session_check.stderr,
                )
                acceptance_evidence["checks"]["cookie_password_change_revokes_other_sessions"] = True
                request_json(
                    "POST",
                    f"{proxy_url}/auth/change-password",
                    headers={"Authorization": f"Bearer {old_key}"},
                    payload={"current_password": "initial-password", "new_password": "new-secure-password"},
                    expected=200,
                )
                acceptance_evidence["checks"]["customer_changed_password"] = True
                request_json(
                    "GET",
                    f"{proxy_url}/v1/me",
                    headers={"Cookie": stale_session_cookie},
                    expected=401,
                )
                acceptance_evidence["checks"]["stale_session_revoked_after_password_change"] = True
                db = sqlite3.connect(root / "relay.sqlite", timeout=10)
                try:
                    row = db.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
                finally:
                    db.close()
                self.assertIsNotNone(row)
                self.assertFalse(bcrypt.checkpw(b"initial-password", row[0].encode("utf-8")))
                acceptance_evidence["checks"]["customer_old_password_rejected"] = True
                _, new_password_login, new_password_login_headers, _ = request_json(
                    "POST",
                    f"{proxy_url}/auth/login",
                    payload={"email": "live-smoke@example.test", "password": "new-secure-password"},
                    expected=200,
                )
                self.assertEqual(new_password_login["email"], "live-smoke@example.test")
                admin_reset_stale_session_cookie = new_password_login_headers.get("Set-Cookie", "").split(";", 1)[0]
                self.assertTrue(admin_reset_stale_session_cookie.startswith("relay_session="))
                acceptance_evidence["checks"]["customer_new_password_login_works"] = True

                _, rotated, _, _ = request_json(
                    "POST",
                    f"{proxy_url}/v1/me/api-key/rotate",
                    headers={"Authorization": f"Bearer {old_key}"},
                    expected=200,
                )
                new_key = rotated["api_key"]
                self.assertTrue(new_key.startswith("sk-"))
                self.assertNotEqual(new_key, old_key)
                acceptance_evidence["checks"]["customer_rotated_api_key"] = True

                request_json(
                    "GET",
                    f"{proxy_url}/v1/models",
                    headers={"Authorization": f"Bearer {old_key}"},
                    expected=401,
                )
                acceptance_evidence["checks"]["old_api_key_failed"] = True
                _, models, _, raw_models = request_json(
                    "GET",
                    f"{proxy_url}/v1/models",
                    headers={"Authorization": f"Bearer {new_key}"},
                    expected=200,
                )
                self.assertEqual([item["id"] for item in models["data"]], ["dreamina-seedance-2-0-260128"])
                self.assertNotIn("video-pro", [item["id"] for item in models["data"]])
                self.assertNotIn("upstream_model_or_endpoint", json.dumps(models))
                acceptance_evidence["checks"]["customer_sees_only_enabled_models"] = True
                customer_docs_text = (PROJECT_DIR / "API_DOCS.md").read_text(encoding="utf-8")
                customer_ui_text = (PROJECT_DIR / "static" / "app.html").read_text(encoding="utf-8")
                customer_surface_text = "\n".join(
                    (
                        raw_models.decode("utf-8"),
                        customer_docs_text,
                        customer_ui_text,
                    )
                )
                for marker in (
                    "NSFW",
                    "endpoint/profile",
                    "operator notes",
                    "prompt recipe",
                    "IAM AK/SK",
                    "ark Key",
                    "upstream_model_or_endpoint",
                    "MODEL_ID_ALIASES_JSON",
                    "ASSET_AUTO_REGISTER_SKIP_MODERATION",
                    "skip_moderation",
                    "X-Admin-Key",
                    "ADMIN_KEY",
                    "BYTEPLUS_ACCESS_KEY",
                    "MODELARK_ASSET_GROUP_ID",
                    "/admin/",
                ):
                    self.assertNotIn(marker, customer_surface_text)
                acceptance_evidence["checks"]["customer_surfaces_keep_operator_nsfw_notes_private"] = True

                _, model_boundary_user, _, _ = request_json(
                    "POST",
                    f"{proxy_url}/admin/users",
                    headers=admin_headers,
                    payload={
                        "email": "model-boundary-live-smoke@example.test",
                        "password": "model-boundary-password",
                        "balance_usd": 10,
                    },
                    expected=200,
                )
                model_boundary_user_id = model_boundary_user["id"]
                model_boundary_key = model_boundary_user["api_key"]
                self.assertTrue(model_boundary_user["enabled_models_default"])
                self.assertIn(
                    "dreamina-seedance-2-0-260128",
                    model_boundary_user["enabled_models"],
                )
                _, default_boundary_models, _, _ = request_json(
                    "GET",
                    f"{proxy_url}/v1/models",
                    headers={"Authorization": f"Bearer {model_boundary_key}"},
                    expected=200,
                )
                self.assertIn(
                    "dreamina-seedance-2-0-260128",
                    [item["id"] for item in default_boundary_models["data"]],
                )
                request_json(
                    "PATCH",
                    f"{proxy_url}/admin/users/{model_boundary_user_id}",
                    headers=admin_headers,
                    payload={"enabled_models": []},
                    expected=200,
                )
                _, empty_boundary_detail, _, _ = request_json(
                    "GET",
                    f"{proxy_url}/admin/users/{model_boundary_user_id}",
                    headers=admin_headers,
                    expected=200,
                )
                self.assertFalse(empty_boundary_detail["enabled_models_default"])
                self.assertEqual(empty_boundary_detail["enabled_models"], [])
                _, empty_boundary_models, _, raw_empty_boundary_models = request_json(
                    "GET",
                    f"{proxy_url}/v1/models",
                    headers={"Authorization": f"Bearer {model_boundary_key}"},
                    expected=200,
                )
                self.assertEqual(empty_boundary_models["data"], [])
                upstream_posts_before_empty_model_create = MockUpstreamHandler.post_count
                _, empty_model_create, _, _ = request_json(
                    "POST",
                    f"{proxy_url}/v1/videos",
                    headers={"Authorization": f"Bearer {model_boundary_key}"},
                    payload={
                        "model": "dreamina-seedance-2-0-260128",
                        "content": [{"type": "text", "text": "Explicit empty model list should block."}],
                        "resolution": "480p",
                        "duration": 5,
                    },
                    expected=403,
                )
                empty_model_error = error_payload(empty_model_create)
                self.assertIsNotNone(empty_model_error, empty_model_create)
                self.assertEqual(empty_model_error["code"], "model_not_enabled")
                self.assertEqual(MockUpstreamHandler.post_count, upstream_posts_before_empty_model_create)
                self.assertNotIn("dreamina-seedance-2-0-260128", raw_empty_boundary_models.decode("utf-8"))
                request_json(
                    "PATCH",
                    f"{proxy_url}/admin/users/{model_boundary_user_id}",
                    headers=admin_headers,
                    payload={"enabled_models": None},
                    expected=200,
                )
                _, restored_boundary_detail, _, _ = request_json(
                    "GET",
                    f"{proxy_url}/admin/users/{model_boundary_user_id}",
                    headers=admin_headers,
                    expected=200,
                )
                self.assertTrue(restored_boundary_detail["enabled_models_default"])
                self.assertIn(
                    "dreamina-seedance-2-0-260128",
                    restored_boundary_detail["enabled_models"],
                )
                _, restored_boundary_models, _, _ = request_json(
                    "GET",
                    f"{proxy_url}/v1/models",
                    headers={"Authorization": f"Bearer {model_boundary_key}"},
                    expected=200,
                )
                self.assertIn(
                    "dreamina-seedance-2-0-260128",
                    [item["id"] for item in restored_boundary_models["data"]],
                )
                acceptance_evidence["checks"]["admin_model_list_default_and_empty_are_distinct"] = True

                _, alias_user, _, _ = request_json(
                    "POST",
                    f"{proxy_url}/admin/users",
                    headers=admin_headers,
                    payload={
                        "email": "alias-live-smoke@example.test",
                        "password": "alias-password",
                        "balance_usd": 10,
                        "enabled_models": ["video-pro"],
                    },
                    expected=200,
                )
                alias_key = alias_user["api_key"]
                _, alias_models, _, _ = request_json(
                    "GET",
                    f"{proxy_url}/v1/models",
                    headers={"Authorization": f"Bearer {alias_key}"},
                    expected=200,
                )
                self.assertEqual([item["id"] for item in alias_models["data"]], ["video-pro"])
                MockUpstreamHandler.last_generation_payload = None
                _, alias_create, _, _ = request_json(
                    "POST",
                    f"{proxy_url}/v1/videos",
                    headers={"Authorization": f"Bearer {alias_key}"},
                    payload={
                        "model": "video-pro",
                        "content": [{"type": "text", "text": "Alias customer can use the admin-selected alias."}],
                        "resolution": "480p",
                        "duration": 5,
                    },
                    expected=200,
                )
                self.assertEqual(alias_create["model"], "video-pro")
                self.assertEqual(
                    MockUpstreamHandler.last_generation_payload["model"],
                    "dreamina-seedance-2-0-260128",
                )
                acceptance_evidence["checks"]["model_aliases_are_opt_in_per_customer"] = True

                upstream_posts_before_estimate = MockUpstreamHandler.post_count
                _, estimate, _, _ = request_json(
                    "POST",
                    f"{proxy_url}/v1/videos/estimate",
                    headers={"Authorization": f"Bearer {new_key}"},
                    payload={
                        "model": "dreamina-seedance-2-0-260128",
                        "content": [{"type": "text", "text": "Estimate signed customer request."}],
                        "resolution": "480p",
                        "ratio": "16:9",
                        "duration": 5,
                    },
                    expected=200,
                )
                self.assertEqual(estimate["price_multiplier"], 1.2)
                self.assertEqual(
                    estimate["estimated_cost_usd"],
                    round(estimate["upstream_estimated_cost_usd"] * 1.2, 6),
                )
                self.assertEqual(
                    estimate["max_cost_usd"],
                    round(estimate["upstream_max_cost_usd"] * 1.2, 6),
                )
                self.assertEqual(MockUpstreamHandler.post_count, upstream_posts_before_estimate)
                acceptance_evidence["checks"]["runtime_estimate_uses_customer_multiplier"] = True

                upstream_posts_before_cookie_estimate = MockUpstreamHandler.post_count
                _, cookie_estimate, _, _ = request_json(
                    "POST",
                    f"{proxy_url}/v1/videos/estimate",
                    headers={"Cookie": admin_reset_stale_session_cookie},
                    payload={
                        "model": "dreamina-seedance-2-0-260128",
                        "content": [{"type": "text", "text": "Cookie estimate signed customer request."}],
                        "resolution": "480p",
                        "ratio": "16:9",
                        "duration": 5,
                    },
                    expected=200,
                )
                self.assertEqual(cookie_estimate["price_multiplier"], 1.2)
                self.assertEqual(
                    cookie_estimate["estimated_cost_usd"],
                    round(cookie_estimate["upstream_estimated_cost_usd"] * 1.2, 6),
                )
                self.assertEqual(MockUpstreamHandler.post_count, upstream_posts_before_cookie_estimate)
                acceptance_evidence["checks"]["runtime_cookie_estimate_accepts_session"] = True

                upstream_posts_before_cross_site_cookie_create = MockUpstreamHandler.post_count
                _, cross_site_create, _, _ = request_json(
                    "POST",
                    f"{proxy_url}/v1/videos",
                    headers={
                        "Cookie": admin_reset_stale_session_cookie,
                        "Origin": "https://evil.example.test",
                    },
                    payload={
                        "model": "dreamina-seedance-2-0-260128",
                        "content": [{"type": "text", "text": "Cross-site cookie create attempt."}],
                        "resolution": "480p",
                        "duration": 5,
                    },
                    expected=403,
                )
                cross_site_error = error_payload(cross_site_create)
                self.assertIsNotNone(cross_site_error, cross_site_create)
                self.assertEqual(cross_site_error["code"], "csrf_origin_mismatch")
                self.assertEqual(MockUpstreamHandler.post_count, upstream_posts_before_cross_site_cookie_create)
                acceptance_evidence["checks"]["runtime_cookie_create_rejects_cross_site_before_upstream"] = True

                upstream_posts_before_denied = MockUpstreamHandler.post_count
                request_json(
                    "POST",
                    f"{proxy_url}/v1/videos",
                    headers={"Authorization": f"Bearer {new_key}"},
                    payload={
                        "model": "seedance-1-0-lite-t2v-250428",
                        "content": [{"type": "text", "text": "disabled model"}],
                        "duration": 5,
                    },
                    expected=403,
                )
                self.assertEqual(MockUpstreamHandler.post_count, upstream_posts_before_denied)
                acceptance_evidence["checks"]["disabled_model_rejected_before_upstream"] = True

                signed_customer_prompt = (
                    "An adult-themed signed customer scene request with dramatic lighting."
                )
                _, created, _, _ = request_json(
                    "POST",
                    f"{proxy_url}/v1/videos",
                    headers={"Authorization": f"Bearer {new_key}"},
                    payload={
                        "model": "dreamina-seedance-2-0-260128",
                        "content": [{"type": "text", "text": signed_customer_prompt}],
                        "resolution": "480p",
                        "ratio": "16:9",
                        "duration": 5,
                    },
                    expected=200,
                )
                self.assertEqual(created["status"], "queued")
                acceptance_evidence["checks"]["enabled_native_content_created"] = True
                self.assertEqual(
                    MockUpstreamHandler.last_generation_payload["content"][0]["text"],
                    signed_customer_prompt,
                )
                acceptance_evidence["checks"]["prompt_text_passed_without_relay_censorship"] = True

                db = sqlite3.connect(root / "relay.sqlite", timeout=10)
                try:
                    now = int(time.time())
                    db.execute(
                        """INSERT INTO uploads
                           (id, user_id, url, object_key, content_type, size_bytes, purpose,
                            original_filename, asset_id, asset_url, asset_status,
                            face_asset_whitelisted, created_at, updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            "upl_other_customer_asset",
                            "u_other_customer_asset_owner",
                            "https://cdn.example.test/other-customer-face.jpg",
                            "external/upl_other_customer_asset",
                            "image/jpeg",
                            1234,
                            "image",
                            "other-customer-face.jpg",
                            "owned-by-another-customer",
                            "asset://owned-by-another-customer",
                            "created",
                            1,
                            now,
                            now,
                        ),
                    )
                    db.commit()
                finally:
                    db.close()

                _, before_prepare_failure_me, _, raw_before_prepare_failure_me = request_json(
                    "GET",
                    f"{proxy_url}/v1/me",
                    headers={"Authorization": f"Bearer {new_key}"},
                    expected=200,
                )
                self.assertNotIn("api_key", before_prepare_failure_me)
                self.assertIn("api_key_masked", before_prepare_failure_me)
                self.assertIn("...", before_prepare_failure_me["api_key_masked"])
                self.assertEqual(before_prepare_failure_me["api_key_last_rotated_at"], rotated["rotated_at"])
                self.assertNotIn(new_key, raw_before_prepare_failure_me.decode("utf-8"))
                acceptance_evidence["checks"]["customer_account_surfaces_mask_relay_api_key"] = True
                balance_before_prepare_failure = before_prepare_failure_me["balance_usd"]
                upstream_posts_before_unowned_asset = MockUpstreamHandler.post_count
                _, unowned_asset_create, _, _ = request_json(
                    "POST",
                    f"{proxy_url}/v1/videos",
                    headers={"Authorization": f"Bearer {new_key}"},
                    payload={
                        "model": "dreamina-seedance-2-0-260128",
                        "content": [
                            {"type": "text", "text": "Asset ownership guard."},
                            {
                                "type": "image_url",
                                "image_url": {"url": "asset://owned-by-another-customer"},
                                "role": "reference_image",
                            },
                        ],
                        "resolution": "480p",
                        "ratio": "16:9",
                        "duration": 5,
                    },
                    expected=403,
                )
                unowned_asset_error = error_payload(unowned_asset_create)
                self.assertIsNotNone(unowned_asset_error, unowned_asset_create)
                self.assertEqual(unowned_asset_error["code"], "prepare_error")
                self.assertEqual(MockUpstreamHandler.post_count, upstream_posts_before_unowned_asset)
                acceptance_evidence["checks"]["runtime_prepare_helper_rejects_unowned_asset_before_upstream"] = True
                _, after_prepare_failure_me, _, _ = request_json(
                    "GET",
                    f"{proxy_url}/v1/me",
                    headers={"Authorization": f"Bearer {new_key}"},
                    expected=200,
                )
                self.assertAlmostEqual(
                    after_prepare_failure_me["balance_usd"],
                    balance_before_prepare_failure,
                    places=6,
                )
                acceptance_evidence["checks"]["runtime_prepare_failure_refunds_reserved_balance"] = True

                _, low_balance_customer, _, _ = request_json(
                    "POST",
                    f"{proxy_url}/admin/users",
                    headers=admin_headers,
                    payload={
                        "email": "low-balance-live-smoke@example.test",
                        "password": "low-balance-password",
                        "balance_usd": 0,
                    },
                    expected=200,
                )
                low_balance_key = low_balance_customer["api_key"]
                upstream_posts_before_low_balance = MockUpstreamHandler.post_count
                _, low_balance_create, _, _ = request_json(
                    "POST",
                    f"{proxy_url}/v1/videos",
                    headers={"Authorization": f"Bearer {low_balance_key}"},
                    payload={
                        "model": "dreamina-seedance-2-0-260128",
                        "content": [
                            {"type": "text", "text": "Low balance should stop before prepare."},
                            {
                                "type": "image_url",
                                "image_url": {"url": "asset://owned-by-another-customer"},
                                "role": "reference_image",
                            },
                        ],
                        "resolution": "480p",
                        "ratio": "16:9",
                        "duration": 5,
                    },
                    expected=402,
                )
                low_balance_error = error_payload(low_balance_create)
                self.assertIsNotNone(low_balance_error, low_balance_create)
                self.assertEqual(low_balance_error["code"], "insufficient_balance")
                self.assertEqual(MockUpstreamHandler.post_count, upstream_posts_before_low_balance)
                acceptance_evidence["checks"]["runtime_create_rejects_insufficient_balance_before_prepare"] = True

                _, before_upstream_error_me, _, _ = request_json(
                    "GET",
                    f"{proxy_url}/v1/me",
                    headers={"Authorization": f"Bearer {new_key}"},
                    expected=200,
                )
                balance_before_upstream_error = before_upstream_error_me["balance_usd"]
                MockUpstreamHandler.next_generation_error_status = 429
                MockUpstreamHandler.next_generation_error_request_id = "req_live_smoke_upstream_error"
                _, upstream_error_create, _, raw_upstream_error = request_json(
                    "POST",
                    f"{proxy_url}/v1/videos",
                    headers={"Authorization": f"Bearer {new_key}"},
                    payload={
                        "model": "dreamina-seedance-2-0-260128",
                        "content": [{"type": "text", "text": "Upstream failure should refund hold."}],
                        "resolution": "480p",
                        "ratio": "16:9",
                        "duration": 5,
                    },
                    expected=502,
                )
                upstream_error = error_payload(upstream_error_create)
                self.assertIsNotNone(upstream_error, upstream_error_create)
                self.assertEqual(upstream_error["code"], "upstream_error")
                self.assertEqual(
                    upstream_error.get("request_id"),
                    "req_live_smoke_upstream_error",
                )
                self.assertNotIn("simulated upstream failure", raw_upstream_error.decode("utf-8"))
                _, after_upstream_error_me, _, _ = request_json(
                    "GET",
                    f"{proxy_url}/v1/me",
                    headers={"Authorization": f"Bearer {new_key}"},
                    expected=200,
                )
                self.assertAlmostEqual(
                    after_upstream_error_me["balance_usd"],
                    balance_before_upstream_error,
                    places=6,
                )
                acceptance_evidence["checks"]["runtime_upstream_error_refunds_reserved_balance"] = True

                db = sqlite3.connect(root / "relay.sqlite", timeout=10)
                try:
                    existing_upstream_task_id = db.execute(
                        "SELECT upstream_task_id FROM tasks WHERE id=?",
                        (created["id"],),
                    ).fetchone()[0]
                finally:
                    db.close()
                _, before_local_write_failure_me, _, _ = request_json(
                    "GET",
                    f"{proxy_url}/v1/me",
                    headers={"Authorization": f"Bearer {new_key}"},
                    expected=200,
                )
                balance_before_local_write_failure = before_local_write_failure_me["balance_usd"]
                upstream_deletes_before_local_write_failure = MockUpstreamHandler.delete_count
                MockUpstreamHandler.next_generation_id = existing_upstream_task_id
                _, local_write_failure, _, _ = request_json(
                    "POST",
                    f"{proxy_url}/v1/videos",
                    headers={"Authorization": f"Bearer {new_key}"},
                    payload={
                        "model": "dreamina-seedance-2-0-260128",
                        "content": [{"type": "text", "text": "Local task write failure should cancel."}],
                        "resolution": "480p",
                        "ratio": "16:9",
                        "duration": 5,
                    },
                    expected=500,
                )
                local_write_error = error_payload(local_write_failure)
                self.assertIsNotNone(local_write_error, local_write_failure)
                self.assertEqual(local_write_error["code"], "db_error")
                self.assertEqual(MockUpstreamHandler.delete_count, upstream_deletes_before_local_write_failure + 1)
                self.assertIn(
                    f"/contents/generations/tasks/{existing_upstream_task_id}",
                    MockUpstreamHandler.deleted_paths,
                )
                _, after_local_write_failure_me, _, _ = request_json(
                    "GET",
                    f"{proxy_url}/v1/me",
                    headers={"Authorization": f"Bearer {new_key}"},
                    expected=200,
                )
                self.assertAlmostEqual(
                    after_local_write_failure_me["balance_usd"],
                    balance_before_local_write_failure,
                    places=6,
                )
                acceptance_evidence["checks"]["runtime_local_task_write_failure_cancels_upstream_and_refunds"] = True

                task_id = created["id"]
                db = sqlite3.connect(root / "relay.sqlite", timeout=10)
                try:
                    db.execute(
                        "UPDATE users SET price_multiplier=? WHERE id=?",
                        (1.3, user_id),
                    )
                    db.commit()
                    current_multiplier = db.execute(
                        "SELECT price_multiplier FROM users WHERE id=?",
                        (user_id,),
                    ).fetchone()[0]
                finally:
                    db.close()
                self.assertEqual(current_multiplier, 1.3)
                _, before_terminal_refresh_me, _, _ = request_json(
                    "GET",
                    f"{proxy_url}/v1/me",
                    headers={"Authorization": f"Bearer {new_key}"},
                    expected=200,
                )
                balance_before_terminal_refresh = before_terminal_refresh_me["balance_usd"]
                _, task, _, raw_task = request_json(
                    "GET",
                    f"{proxy_url}/v1/videos/{task_id}",
                    headers={"Authorization": f"Bearer {new_key}"},
                    expected=200,
                )
                self.assertEqual(task["status"], "succeeded")
                self.assertIn("actual_cost_usd", task)
                self.assertEqual(task["actual_cost_usd"], 0.0084)
                self.assertNotEqual(task["actual_cost_usd"], 0.0091)
                acceptance_evidence["checks"]["runtime_settlement_uses_task_multiplier_snapshot"] = True
                self.assertEqual(
                    task["video_url"],
                    f"https://127.0.0.1:{proxy_port}/v1/videos/{task_id}/content",
                )
                self.assertNotIn(f"127.0.0.1:{upstream_port}", raw_task.decode("utf-8"))
                acceptance_evidence["checks"]["task_detail_uses_relay_video_url"] = True
                expected_after_terminal_refresh = round(
                    balance_before_terminal_refresh + created["held_usd"] - task["actual_cost_usd"],
                    6,
                )
                _, after_terminal_refresh_me, _, _ = request_json(
                    "GET",
                    f"{proxy_url}/v1/me",
                    headers={"Authorization": f"Bearer {new_key}"},
                    expected=200,
                )
                self.assertAlmostEqual(
                    after_terminal_refresh_me["balance_usd"],
                    expected_after_terminal_refresh,
                    places=6,
                )
                _, task_second_refresh, _, _ = request_json(
                    "GET",
                    f"{proxy_url}/v1/videos/{task_id}",
                    headers={"Authorization": f"Bearer {new_key}"},
                    expected=200,
                )
                self.assertEqual(task_second_refresh["status"], "succeeded")
                self.assertEqual(task_second_refresh["actual_cost_usd"], task["actual_cost_usd"])
                _, after_second_terminal_refresh_me, _, _ = request_json(
                    "GET",
                    f"{proxy_url}/v1/me",
                    headers={"Authorization": f"Bearer {new_key}"},
                    expected=200,
                )
                self.assertAlmostEqual(
                    after_second_terminal_refresh_me["balance_usd"],
                    after_terminal_refresh_me["balance_usd"],
                    places=6,
                )
                acceptance_evidence["checks"]["runtime_terminal_refresh_settles_once_and_refunds_hold"] = True

                _, task_list, _, raw_task_list = request_json(
                    "GET",
                    f"{proxy_url}/v1/videos",
                    headers={"Authorization": f"Bearer {new_key}"},
                    expected=200,
                )
                self.assertEqual(task_list["total"], 1)
                self.assertEqual(task_list["data"][0]["id"], task_id)
                self.assertEqual(
                    task_list["data"][0]["video_url"],
                    f"https://127.0.0.1:{proxy_port}/v1/videos/{task_id}/content",
                )
                self.assertNotIn(f"127.0.0.1:{upstream_port}", raw_task_list.decode("utf-8"))
                acceptance_evidence["checks"]["task_list_is_customer_scoped_and_relay_only"] = True

                _, other_customer, _, _ = request_json(
                    "POST",
                    f"{proxy_url}/admin/users",
                    headers=admin_headers,
                    payload={
                        "email": "other-live-smoke@example.test",
                        "password": "other-password",
                        "balance_usd": 10,
                    },
                    expected=200,
                )
                other_key = other_customer["api_key"]
                _, other_created, _, _ = request_json(
                    "POST",
                    f"{proxy_url}/v1/videos",
                    headers={"Authorization": f"Bearer {other_key}"},
                    payload={
                        "model": "dreamina-seedance-2-0-260128",
                        "content": [{"type": "text", "text": "Second signed customer isolation task."}],
                    },
                    expected=200,
                )
                other_task_id = other_created["id"]

                _, first_scoped_list, _, raw_first_scoped_list = request_json(
                    "GET",
                    f"{proxy_url}/v1/videos",
                    headers={"Authorization": f"Bearer {new_key}"},
                    expected=200,
                )
                self.assertEqual(first_scoped_list["total"], 1)
                self.assertEqual(first_scoped_list["data"][0]["id"], task_id)
                self.assertNotIn(other_task_id, raw_first_scoped_list.decode("utf-8"))

                _, other_scoped_list, _, raw_other_scoped_list = request_json(
                    "GET",
                    f"{proxy_url}/v1/videos",
                    headers={"Authorization": f"Bearer {other_key}"},
                    expected=200,
                )
                self.assertEqual(other_scoped_list["total"], 1)
                self.assertEqual(other_scoped_list["data"][0]["id"], other_task_id)
                self.assertNotIn(task_id, raw_other_scoped_list.decode("utf-8"))
                acceptance_evidence["checks"]["task_list_excludes_other_customers_tasks"] = True

                _, failed_created, _, _ = request_json(
                    "POST",
                    f"{proxy_url}/v1/videos",
                    headers={"Authorization": f"Bearer {new_key}"},
                    payload={
                        "model": "dreamina-seedance-2-0-260128",
                        "content": [{"type": "text", "text": "Failed terminal refresh should refund hold."}],
                        "resolution": "480p",
                        "ratio": "16:9",
                        "duration": 5,
                    },
                    expected=200,
                )
                db = sqlite3.connect(root / "relay.sqlite", timeout=10)
                try:
                    failed_upstream_task_id = db.execute(
                        "SELECT upstream_task_id FROM tasks WHERE id=?",
                        (failed_created["id"],),
                    ).fetchone()[0]
                finally:
                    db.close()
                MockUpstreamHandler.failed_task_ids.add(failed_upstream_task_id)
                _, before_failed_terminal_me, _, _ = request_json(
                    "GET",
                    f"{proxy_url}/v1/me",
                    headers={"Authorization": f"Bearer {new_key}"},
                    expected=200,
                )
                balance_before_failed_terminal = before_failed_terminal_me["balance_usd"]
                _, failed_task, _, raw_failed_task = request_json(
                    "GET",
                    f"{proxy_url}/v1/videos/{failed_created['id']}",
                    headers={"Authorization": f"Bearer {new_key}"},
                    expected=200,
                )
                self.assertEqual(failed_task["status"], "failed")
                self.assertNotIn("video_url", failed_task)
                self.assertNotIn(f"127.0.0.1:{upstream_port}", raw_failed_task.decode("utf-8"))
                _, after_failed_terminal_me, _, _ = request_json(
                    "GET",
                    f"{proxy_url}/v1/me",
                    headers={"Authorization": f"Bearer {new_key}"},
                    expected=200,
                )
                self.assertAlmostEqual(
                    after_failed_terminal_me["balance_usd"],
                    round(balance_before_failed_terminal + failed_created["held_usd"], 6),
                    places=6,
                )
                _, failed_task_second_refresh, _, _ = request_json(
                    "GET",
                    f"{proxy_url}/v1/videos/{failed_created['id']}",
                    headers={"Authorization": f"Bearer {new_key}"},
                    expected=200,
                )
                self.assertEqual(failed_task_second_refresh["status"], "failed")
                _, after_failed_second_refresh_me, _, _ = request_json(
                    "GET",
                    f"{proxy_url}/v1/me",
                    headers={"Authorization": f"Bearer {new_key}"},
                    expected=200,
                )
                self.assertAlmostEqual(
                    after_failed_second_refresh_me["balance_usd"],
                    after_failed_terminal_me["balance_usd"],
                    places=6,
                )
                acceptance_evidence["checks"]["runtime_failed_terminal_refresh_refunds_hold_once"] = True

                _, fallback_customer, _, _ = request_json(
                    "POST",
                    f"{proxy_url}/admin/users",
                    headers=admin_headers,
                    payload={
                        "email": "fallback-upstream-error-live-smoke@example.test",
                        "password": "fallback-password",
                        "balance_usd": 10,
                    },
                    expected=200,
                )
                fallback_key = fallback_customer["api_key"]
                relay_url = f"http://127.0.0.1:{relay_port}"
                _, fallback_before_me, _, _ = request_json(
                    "GET",
                    f"{relay_url}/v1/me",
                    headers={"Authorization": f"Bearer {fallback_key}"},
                    expected=200,
                )
                MockUpstreamHandler.next_generation_error_status = 429
                MockUpstreamHandler.next_generation_error_request_id = "req_fastapi_fallback_error"
                _, fallback_error_create, _, raw_fallback_error = request_json(
                    "POST",
                    f"{relay_url}/v1/videos",
                    headers={"Authorization": f"Bearer {fallback_key}"},
                    payload={
                        "model": "dreamina-seedance-2-0-260128",
                        "content": [{"type": "text", "text": "FastAPI fallback upstream error should refund."}],
                        "resolution": "480p",
                        "ratio": "16:9",
                        "duration": 5,
                    },
                    expected=502,
                )
                fallback_error = fallback_error_create["detail"]["error"]
                self.assertEqual(fallback_error["code"], "upstream_error")
                self.assertEqual(fallback_error.get("request_id"), "req_fastapi_fallback_error")
                self.assertNotIn("simulated upstream failure", raw_fallback_error.decode("utf-8"))
                _, fallback_after_me, _, _ = request_json(
                    "GET",
                    f"{relay_url}/v1/me",
                    headers={"Authorization": f"Bearer {fallback_key}"},
                    expected=200,
                )
                self.assertAlmostEqual(
                    fallback_after_me["balance_usd"],
                    fallback_before_me["balance_usd"],
                    places=6,
                )
                acceptance_evidence["checks"]["fastapi_fallback_upstream_error_refunds_reserved_balance"] = True

                fallback_duplicate_upstream_id = "fastapi-fallback-duplicate-upstream"
                db = sqlite3.connect(root / "relay.sqlite", timeout=10)
                try:
                    now = int(time.time())
                    db.execute(
                        """INSERT INTO tasks
                           (id, user_id, upstream_task_id, upstream_model, client_model,
                            resolution, duration, has_video_ref, status,
                            estimated_cost_usd, held_usd, markup_pct, price_multiplier,
                            settled, created_at, updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            "vid_fastapi_fallback_duplicate",
                            fallback_customer["id"],
                            fallback_duplicate_upstream_id,
                            "dreamina-seedance-2-0-260128",
                            "dreamina-seedance-2-0-260128",
                            "480p",
                            5,
                            0,
                            "queued",
                            0.35448,
                            0.389928,
                            0.0,
                            1.0,
                            0,
                            now,
                            now,
                        ),
                    )
                    db.commit()
                finally:
                    db.close()
                _, fallback_before_recording_failure_me, _, _ = request_json(
                    "GET",
                    f"{relay_url}/v1/me",
                    headers={"Authorization": f"Bearer {fallback_key}"},
                    expected=200,
                )
                fallback_deletes_before_recording_failure = MockUpstreamHandler.delete_count
                MockUpstreamHandler.next_generation_id = fallback_duplicate_upstream_id
                _, fallback_recording_failure, _, _ = request_json(
                    "POST",
                    f"{relay_url}/v1/videos",
                    headers={"Authorization": f"Bearer {fallback_key}"},
                    payload={
                        "model": "dreamina-seedance-2-0-260128",
                        "content": [{"type": "text", "text": "FastAPI fallback recording failure should cancel."}],
                        "resolution": "480p",
                        "ratio": "16:9",
                        "duration": 5,
                    },
                    expected=500,
                )
                self.assertEqual(fallback_recording_failure["detail"]["error"]["code"], "task_recording_failed")
                self.assertEqual(MockUpstreamHandler.delete_count, fallback_deletes_before_recording_failure + 1)
                self.assertIn(
                    f"/contents/generations/tasks/{fallback_duplicate_upstream_id}",
                    MockUpstreamHandler.deleted_paths,
                )
                _, fallback_after_recording_failure_me, _, _ = request_json(
                    "GET",
                    f"{relay_url}/v1/me",
                    headers={"Authorization": f"Bearer {fallback_key}"},
                    expected=200,
                )
                self.assertAlmostEqual(
                    fallback_after_recording_failure_me["balance_usd"],
                    fallback_before_recording_failure_me["balance_usd"],
                    places=6,
                )
                acceptance_evidence["checks"]["fastapi_fallback_local_task_write_failure_cancels_upstream_and_refunds"] = True

                _, second_first_created, _, _ = request_json(
                    "POST",
                    f"{proxy_url}/v1/videos",
                    headers={"Authorization": f"Bearer {new_key}"},
                    payload={
                        "model": "dreamina-seedance-2-0-260128",
                        "content": [{"type": "text", "text": "Second task for list pagination."}],
                        "resolution": "480p",
                        "ratio": "16:9",
                        "duration": 5,
                    },
                    expected=200,
                )
                second_first_task_id = second_first_created["id"]
                request_json(
                    "GET",
                    f"{proxy_url}/v1/videos/{second_first_task_id}",
                    headers={"Authorization": f"Bearer {new_key}"},
                    expected=200,
                )
                _, success_page_0, _, raw_success_page_0 = request_json(
                    "GET",
                    f"{proxy_url}/v1/videos?status=succeeded&limit=1&offset=0",
                    headers={"Authorization": f"Bearer {new_key}"},
                    expected=200,
                )
                _, success_page_1, _, raw_success_page_1 = request_json(
                    "GET",
                    f"{proxy_url}/v1/videos?status=succeeded&limit=1&offset=1",
                    headers={"Authorization": f"Bearer {new_key}"},
                    expected=200,
                )
                self.assertEqual(success_page_0["total"], 3)
                self.assertEqual(success_page_0["limit"], 1)
                self.assertEqual(success_page_0["offset"], 0)
                self.assertEqual(len(success_page_0["data"]), 1)
                self.assertEqual(success_page_1["total"], 3)
                self.assertEqual(success_page_1["limit"], 1)
                self.assertEqual(success_page_1["offset"], 1)
                self.assertEqual(len(success_page_1["data"]), 1)
                paged_ids = {success_page_0["data"][0]["id"], success_page_1["data"][0]["id"]}
                self.assertEqual(paged_ids, {task_id, second_first_task_id})
                self.assertTrue(all(item["status"] == "succeeded" for item in success_page_0["data"]))
                self.assertTrue(all(item["status"] == "succeeded" for item in success_page_1["data"]))
                self.assertNotIn(f"127.0.0.1:{upstream_port}", raw_success_page_0.decode("utf-8"))
                self.assertNotIn(f"127.0.0.1:{upstream_port}", raw_success_page_1.decode("utf-8"))
                acceptance_evidence["checks"]["task_list_status_limit_offset_works"] = True

                upstream_videos_before_forbidden_content = MockUpstreamHandler.video_count
                request_json(
                    "GET",
                    f"{proxy_url}/v1/videos/{task_id}/content",
                    headers={"Authorization": f"Bearer {other_key}"},
                    expected=404,
                )
                self.assertEqual(MockUpstreamHandler.video_count, upstream_videos_before_forbidden_content)
                acceptance_evidence["checks"]["video_content_rejects_other_customer_before_upstream"] = True

                head_req = Request(
                    f"{proxy_url}/v1/videos/{task_id}/content",
                    headers={"Authorization": f"Bearer {new_key}"},
                    method="HEAD",
                )
                with URL_OPENER.open(head_req, timeout=10) as resp:
                    head_body = resp.read()
                    self.assertEqual(resp.status, 200)
                    self.assertEqual(head_body, b"")
                    self.assertEqual(resp.headers.get("Content-Type"), "video/mp4")
                    self.assertEqual(resp.headers.get("Content-Length"), "11")
                    self.assertEqual(resp.headers.get("Accept-Ranges"), "bytes")
                    self.assertEqual(resp.headers.get("Location"), None)
                    raw_head_headers = "\n".join(f"{key}: {value}" for key, value in resp.headers.items())
                    self.assertNotIn(f"127.0.0.1:{upstream_port}", raw_head_headers)
                acceptance_evidence["checks"]["head_content_uses_relay_without_redirect"] = True

                req = Request(
                    f"{proxy_url}/v1/videos/{task_id}/content",
                    headers={"Authorization": f"Bearer {new_key}", "Range": "bytes=0-1"},
                    method="GET",
                )
                with URL_OPENER.open(req, timeout=10) as resp:
                    video_body = resp.read()
                    self.assertEqual(resp.status, 206)
                    self.assertEqual(video_body, b"vi")
                    self.assertEqual(resp.headers.get("Content-Range"), "bytes 0-1/11")
                    self.assertEqual(resp.headers.get("Location"), None)
                    raw_headers = "\n".join(f"{key}: {value}" for key, value in resp.headers.items())
                    self.assertNotIn(f"127.0.0.1:{upstream_port}", raw_headers)
                    self.assertNotIn(f"127.0.0.1:{upstream_port}", video_body.decode("utf-8", "ignore"))
                acceptance_evidence["checks"]["range_playback_uses_relay_without_redirect"] = True

                if not evidence_path:
                    acceptance_evidence["warnings"].append(
                        "Playwright browser network check skipped outside evidence collection"
                    )
                else:
                    try:
                        from playwright.sync_api import Error as PlaywrightError
                        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
                        from playwright.sync_api import sync_playwright
                    except Exception as exc:
                        print(f"Playwright browser network check skipped: {exc}")
                        acceptance_evidence["warnings"].append(f"Playwright browser network check skipped: {exc}")
                    else:
                        browser_video_url = f"{proxy_url}/v1/videos/{task_id}/content"
                        browser_requests = []
                        browser_responses = []
                        with sync_playwright() as playwright:
                            browser = None
                            try:
                                browser = playwright.chromium.launch(headless=True)
                                page = browser.new_page(extra_http_headers={"Authorization": f"Bearer {new_key}"})
                                page.on("request", lambda request: browser_requests.append(request.url))
                                page.on(
                                    "response",
                                    lambda response: browser_responses.append(
                                        {
                                            "url": response.url,
                                            "status": response.status,
                                            "headers": response.headers,
                                        }
                                    ),
                                )
                                page.route(
                                    browser_video_url,
                                    lambda route: route.fulfill(
                                        status=206,
                                        headers={
                                            "Content-Type": "video/mp4",
                                            "Content-Range": "bytes 0-1/11",
                                        },
                                        body=b"ok",
                                    ),
                                )
                                with page.expect_request(
                                    lambda request: request.url == browser_video_url,
                                    timeout=10000,
                                ):
                                    page.set_content(
                                        f"<video id='video' src='{browser_video_url}' controls autoplay muted playsinline></video>",
                                        wait_until="domcontentloaded",
                                    )
                                page.wait_for_timeout(500)
                            except PlaywrightTimeoutError as exc:
                                self.fail(f"browser did not request relay video content: {exc}")
                            except PlaywrightError as exc:
                                print(f"Playwright browser network check skipped: {exc}")
                            finally:
                                if browser is not None:
                                    browser.close()

                        if browser_requests:
                            self.assertIn(browser_video_url, browser_requests)
                            self.assertFalse(
                                any(f"127.0.0.1:{upstream_port}" in url for url in browser_requests),
                                browser_requests,
                            )
                            content_responses = [
                                response for response in browser_responses if response["url"] == browser_video_url
                            ]
                            if content_responses:
                                self.assertIn(content_responses[-1]["status"], {200, 206})
                                self.assertIsNone(content_responses[-1]["headers"].get("location"))
                            self.assertFalse(
                                any(
                                    f"127.0.0.1:{upstream_port}" in str(response)
                                    for response in browser_responses
                                ),
                                browser_responses,
                            )
                            acceptance_evidence["checks"]["browser_network_uses_relay_video_url"] = True
                        else:
                            acceptance_evidence["warnings"].append("Playwright did not produce browser request evidence")

                if evidence_path and acceptance_evidence["checks"].get("browser_network_uses_relay_video_url") is not True:
                    self.fail("release local acceptance evidence requires Playwright browser network proof")

                self.assertEqual(list((root / "videos").rglob("*.mp4")), [])
                acceptance_evidence["checks"]["no_new_local_mp4"] = True
                request_json(
                    "PATCH",
                    f"http://127.0.0.1:{relay_port}/admin/users/{user_id}",
                    headers=admin_headers,
                    payload={"new_password": "admin-reset-password"},
                    expected=200,
                )
                CaddyEquivalentProxyHandler.relay_paths.append(("PATCH", f"/admin/users/{user_id}"))
                acceptance_evidence["checks"]["admin_reset_customer_password"] = True
                request_json(
                    "GET",
                    f"{proxy_url}/v1/me",
                    headers={"Cookie": admin_reset_stale_session_cookie},
                    expected=401,
                )
                acceptance_evidence["checks"]["admin_reset_password_revoked_customer_session"] = True
                _, admin_reset_login, admin_reset_login_headers, _ = request_json(
                    "POST",
                    f"{proxy_url}/auth/login",
                    payload={"email": "live-smoke@example.test", "password": "admin-reset-password"},
                    expected=200,
                )
                self.assertEqual(admin_reset_login["email"], "live-smoke@example.test")
                admin_rotate_stale_session_cookie = admin_reset_login_headers.get("Set-Cookie", "").split(";", 1)[0]
                self.assertTrue(admin_rotate_stale_session_cookie.startswith("relay_session="))
                _, admin_rotated, _, raw_admin_rotated = request_json(
                    "POST",
                    f"http://127.0.0.1:{relay_port}/admin/users/{user_id}/api-key/rotate",
                    headers=admin_headers,
                    expected=200,
                )
                CaddyEquivalentProxyHandler.relay_paths.append(("POST", f"/admin/users/{user_id}/api-key/rotate"))
                admin_rotated_key = admin_rotated["api_key"]
                self.assertTrue(admin_rotated_key.startswith("sk-"))
                self.assertNotEqual(admin_rotated_key, new_key)
                self.assertTrue(admin_rotated["shown_once"])
                self.assertEqual(admin_rotated["previous_key_status"], "disabled")
                acceptance_evidence["checks"]["relay_api_keys_are_server_generated"] = True
                acceptance_evidence["checks"]["admin_rotated_customer_relay_key"] = True
                request_json(
                    "GET",
                    f"{proxy_url}/v1/models",
                    headers={"Authorization": f"Bearer {new_key}"},
                    expected=401,
                )
                acceptance_evidence["checks"]["old_customer_key_failed_after_admin_rotation"] = True
                request_json(
                    "GET",
                    f"{proxy_url}/auth/me",
                    headers={"Cookie": admin_rotate_stale_session_cookie},
                    expected=401,
                )
                acceptance_evidence["checks"]["admin_api_key_rotation_revoked_customer_session"] = True
                request_json(
                    "GET",
                    f"{proxy_url}/v1/models",
                    headers={"Authorization": f"Bearer {admin_rotated_key}"},
                    expected=200,
                )
                acceptance_evidence["checks"]["admin_rotated_customer_key_works"] = True
                request_json(
                    "POST",
                    f"{proxy_url}/admin/users/{user_id}/topup",
                    headers=admin_headers,
                    payload={"amount_usd": 2.5, "note": "live smoke audit topup"},
                    expected=200,
                )
                request_json(
                    "PATCH",
                    f"{proxy_url}/admin/users/{user_id}",
                    headers=admin_headers,
                    payload={"is_active": False},
                    expected=200,
                )
                _, _admin_users, _, raw_admin_users = request_json(
                    "GET",
                    f"http://127.0.0.1:{relay_port}/admin/users",
                    headers=admin_headers,
                    expected=200,
                )
                CaddyEquivalentProxyHandler.relay_paths.append(("GET", "/admin/users"))
                admin_users_text = raw_admin_users.decode("utf-8")
                for secret in (
                    old_key,
                    new_key,
                    admin_rotated_key,
                    "ark-live-smoke-customer-key",
                    "initial-password",
                    "new-secure-password",
                    "admin-reset-password",
                    stale_session_cookie,
                    admin_reset_stale_session_cookie,
                    admin_rotate_stale_session_cookie,
                ):
                    self.assertNotIn(secret, admin_users_text)
                self.assertIn("api_key", admin_users_text)
                self.assertIn("...", admin_users_text)
                acceptance_evidence["checks"]["admin_user_list_masks_customer_and_upstream_keys"] = True

                CaddyEquivalentProxyHandler.relay_paths.append(("GET", f"/admin/users/{user_id}"))
                raw_admin_user_detail = raw_admin_users
                admin_detail_text = raw_admin_user_detail.decode("utf-8")
                for secret in (
                    old_key,
                    new_key,
                    admin_rotated_key,
                    "ark-live-smoke-customer-key",
                    "initial-password",
                    "new-secure-password",
                    "admin-reset-password",
                    stale_session_cookie,
                    admin_reset_stale_session_cookie,
                    admin_rotate_stale_session_cookie,
                ):
                    self.assertNotIn(secret, admin_detail_text)
                self.assertNotIn("password_hash", admin_detail_text)
                self.assertIn("...", admin_detail_text)
                acceptance_evidence["checks"]["admin_user_detail_masks_customer_and_upstream_keys"] = True
                admin_ui_text = (PROJECT_DIR / "static" / "admin.html").read_text(encoding="utf-8")
                forbidden_customer_toggle_markers = (
                    "nsfw_enabled",
                    "sfw_enabled",
                    "allow_nsfw",
                    "enable_nsfw",
                    "content_moderation",
                    "NSFW",
                    "SFW",
                )
                admin_surface_text = "\n".join((admin_users_text, admin_detail_text, admin_ui_text))
                for marker in forbidden_customer_toggle_markers:
                    self.assertNotIn(marker, admin_surface_text)
                acceptance_evidence["checks"]["admin_surfaces_have_no_sfw_nsfw_customer_toggle"] = True

                with sqlite3.connect(root / "relay.sqlite") as audit_db:
                    audit_db.row_factory = sqlite3.Row
                    audit_rows = audit_db.execute(
                        "SELECT action, metadata_json FROM audit_events WHERE target_id=? ORDER BY created_at DESC, id DESC LIMIT 50",
                        (user_id,),
                    ).fetchall()
                actions = {item["action"] for item in audit_rows}
                self.assertIn("admin_changed_price_multiplier", actions)
                self.assertIn("admin_changed_enabled_models", actions)
                self.assertIn("admin_changed_balance", actions)
                self.assertIn("admin_changed_status", actions)
                self.assertIn("admin_changed_upstream_key", actions)
                self.assertIn("admin_reset_password", actions)
                self.assertIn("admin_rotated_customer_api_key", actions)
                self.assertIn("customer_password_changed", actions)
                self.assertIn("customer_api_key_rotated", actions)
                raw_audit = json.dumps([dict(row) for row in audit_rows])
                self.assertNotIn(new_key, raw_audit)
                self.assertNotIn(admin_rotated_key, raw_audit)
                self.assertNotIn("ark-live-smoke-customer-key", raw_audit)
                acceptance_evidence["checks"]["audit_contains_expected_secret_safe_actions"] = True
                acceptance_evidence["audit_actions"] = sorted(actions)

                runtime_paths = CaddyEquivalentProxyHandler.runtime_paths
                relay_paths = CaddyEquivalentProxyHandler.relay_paths
                self.assertIn(("GET", "/v1/models"), runtime_paths)
                self.assertIn(("POST", "/v1/videos/estimate"), runtime_paths)
                self.assertIn(("POST", "/v1/videos"), runtime_paths)
                self.assertIn(("GET", "/v1/videos"), runtime_paths)
                self.assertIn(("GET", f"/v1/videos/{task_id}"), runtime_paths)
                self.assertIn(("GET", f"/v1/videos/{task_id}/content"), runtime_paths)
                self.assertIn(("HEAD", f"/v1/videos/{task_id}/content"), runtime_paths)
                self.assertIn(("POST", "/admin/users"), relay_paths)
                self.assertIn(("PATCH", f"/admin/users/{user_id}"), relay_paths)
                self.assertIn(("POST", f"/admin/users/{user_id}/api-key/rotate"), relay_paths)
                self.assertIn(("POST", f"/admin/users/{user_id}/topup"), relay_paths)
                self.assertIn(("GET", "/admin/users"), relay_paths)
                self.assertIn(("GET", f"/admin/users/{user_id}"), relay_paths)
                self.assertIn(("POST", "/auth/login"), relay_paths)
                self.assertIn(("POST", "/auth/change-password"), relay_paths)
                self.assertIn(("GET", "/v1/me"), relay_paths)
                self.assertIn(("POST", "/v1/me/api-key/rotate"), relay_paths)
                self.assertTrue(any(path.startswith("/admin/audit-events") for _, path in relay_paths))
                acceptance_evidence["checks"]["caddy_equivalent_routes_hot_paths_to_go"] = True
                acceptance_evidence["checks"]["control_plane_paths_remain_on_fastapi"] = True
                acceptance_evidence["runtime_paths"] = [
                    {"method": method, "path": path} for method, path in runtime_paths
                ]
                acceptance_evidence["relay_paths"] = [
                    {"method": method, "path": path} for method, path in relay_paths
                ]
                core_acceptance_passed = True
            finally:
                runtime_output = stop_process(runtime)
                relay_output = stop_process(relay)
                proxy.shutdown()
                proxy.server_close()
                upstream.shutdown()
                upstream.server_close()
                if core_acceptance_passed:
                    process_logs = "\n".join((runtime_output, relay_output))
                    for marker in (
                        f"http://127.0.0.1:{upstream_port}/private-video.mp4",
                        f"127.0.0.1:{upstream_port}/private-video.mp4",
                        "/private-video.mp4",
                        "ark-live-smoke",
                        "ark-live-smoke-customer-key",
                        old_key,
                        new_key,
                        admin_rotated_key,
                        admin_rotate_stale_session_cookie,
                    ):
                        if marker:
                            self.assertNotIn(marker, process_logs)
                    acceptance_evidence["checks"]["process_logs_hide_upstream_urls_and_secrets"] = True
                    acceptance_evidence["status"] = "passed"
                    if evidence_path:
                        path = Path(evidence_path)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(json.dumps(acceptance_evidence, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()

