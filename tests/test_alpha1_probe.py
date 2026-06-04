import json
import subprocess
import sys
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread


class ProbeHandler(BaseHTTPRequestHandler):
    leak_models = False
    range_returns_200 = False

    def log_message(self, *_):
        return

    def do_GET(self):
        if self.path == "/health":
            self.json({"ok": True})
            return
        if self.headers.get("Authorization") != "Bearer sk-probe-test":
            self.json({"error": "missing auth"}, 401)
            return
        if self.path == "/v1/me":
            self.json({"email": "probe@example.test", "api_key_masked": "sk-pro...e-test"})
            return
        if self.path == "/v1/models":
            if type(self).leak_models:
                self.json(
                    {
                        "data": [
                            {
                                "id": "dreamina-seedance-2-0-260128",
                                "upstream": "https://byteplus.example.test/private",
                                "debug_key": "sk-leaked-relay-key-12345",
                            }
                        ]
                    }
                )
            else:
                self.json({"data": [{"id": "dreamina-seedance-2-0-260128"}]})
            return
        if self.path == "/v1/videos":
            self.json({"data": [{"id": "vid_probe", "video_url": self.relay_url("/v1/videos/vid_probe/content")}], "total": 1})
            return
        if self.path == "/v1/videos/vid_probe":
            self.json({"id": "vid_probe", "status": "succeeded", "video_url": self.relay_url("/v1/videos/vid_probe/content")})
            return
        if self.path == "/v1/videos/vid_probe/content":
            self.video()
            return
        self.json({"error": "not found"}, 404)

    def do_HEAD(self):
        if self.path == "/v1/videos/vid_probe/content":
            self.video(head=True)
            return
        self.send_error(404)

    def relay_url(self, path):
        return f"http://127.0.0.1:{self.server.server_port}{path}"

    def json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def video(self, head=False):
        body = b"video"
        range_header = self.headers.get("Range")
        if range_header == "bytes=0-1":
            body = body[:2]
            if type(self).range_returns_200:
                self.send_response(200)
            else:
                self.send_response(206)
                self.send_header("Content-Range", "bytes 0-1/5")
        else:
            self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        if not head:
            self.wfile.write(body)


class Alpha1ProbeTests(unittest.TestCase):
    def run_server_probe(self, *, leak_models=False, range_returns_200=False, video_id="vid_probe", json_output=None):
        ProbeHandler.leak_models = leak_models
        ProbeHandler.range_returns_200 = range_returns_200
        server = ThreadingHTTPServer(("127.0.0.1", 0), ProbeHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            args = [
                sys.executable,
                "deploy/alpha1_probe.py",
                "--base-url",
                f"http://127.0.0.1:{server.server_port}",
                "--api-key",
                "sk-probe-test",
            ]
            if video_id:
                args.extend(["--video-id", video_id])
            if json_output:
                args.extend(["--json-output", str(json_output)])
            return subprocess.run(
                args,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
        finally:
            server.shutdown()
            server.server_close()

    def test_probe_passes_read_only_acceptance_checks(self):
        proc = self.run_server_probe()

        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("Probe passed", proc.stdout)
        self.assertIn("video_url.relay_domain", proc.stdout)
        self.assertIn("content.range_status", proc.stdout)

    def test_probe_writes_json_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            evidence_path = Path(tmp) / "probe-evidence.json"
            proc = self.run_server_probe(json_output=evidence_path)

            self.assertEqual(proc.returncode, 0, proc.stdout)
            payload = json.loads(evidence_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["failures"], [])
            self.assertEqual(payload["evidence"]["content.range_status"], 206)
            self.assertFalse(payload["evidence"]["content.head_location_present"])
            self.assertTrue(payload["evidence"]["content.range_content_range_present"])
            self.assertFalse(payload["evidence"]["content.range_location_present"])
            self.assertTrue(payload["evidence"]["video_url.relay_domain"])
            self.assertNotIn("browser.expected_content_url", payload["evidence"])

    def test_probe_fails_when_customer_visible_models_leak_upstream_id(self):
        proc = self.run_server_probe(leak_models=True, video_id=None)

        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("models body leaked sensitive marker", proc.stdout)
        self.assertIn("models body leaked sensitive token: Relay API key", proc.stdout)

    def test_probe_requires_range_206_response(self):
        proc = self.run_server_probe(range_returns_200=True)

        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("Range GET /v1/videos/vid_probe/content returned 200, expected [206]", proc.stdout)


if __name__ == "__main__":
    unittest.main()
