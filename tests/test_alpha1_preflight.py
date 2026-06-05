import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_DIR / "deploy" / "alpha1_preflight.py"


class Alpha1PreflightTests(unittest.TestCase):
    def make_env(self, root: Path, **overrides):
        values = {
            "UPSTREAM_API_KEY": "ark-live-test-key",
            "ADMIN_KEY": "admin-secret-token-url-safe-123456",
            "RUNTIME_INTERNAL_TOKEN": "runtime-secret-token-url-safe-123456",
            "UPSTREAM_BASE_URL": "https://ark.ap-southeast.bytepluses.com/api/v3",
            "PUBLIC_DOMAIN": "video.customer-domain.com",
            "DB_PATH": str(root / "relay.sqlite"),
            "CONTROL_PLANE_BASE_URL": "http://seedance-relay:8002",
            "VIDEO_PERSIST_MODE": "proxy_only",
        }
        values.update(overrides)
        env_path = root / ".env.relay"
        env_path.write_text(
            "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
            encoding="utf-8",
        )
        return env_path

    def copy_deploy_files(self, root: Path):
        shutil.copy(PROJECT_DIR / "docker-compose.relay.yml", root / "docker-compose.relay.yml")
        deploy_dir = root / "deploy"
        deploy_dir.mkdir()
        shutil.copy(PROJECT_DIR / "deploy" / "caddy_video.snippet", deploy_dir / "caddy_video.snippet")

    def run_preflight(self, root: Path):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(root), "--skip-db"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def test_preflight_passes_with_required_alpha1_files(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            self.make_env(root)
            self.copy_deploy_files(root)

            proc = self.run_preflight(root)

            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("Preflight passed", proc.stdout)

    def test_preflight_passes_with_iam_upstream_without_api_key(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            self.make_env(
                root,
                UPSTREAM_AUTH_MODE="iam",
                UPSTREAM_API_KEY="",
                UPSTREAM_ENDPOINT_ID="ep-live-endpoint",
                BYTEPLUS_ACCESS_KEY_ID="AKliveendpointkey",
                BYTEPLUS_ACCESS_KEY_SECRET="live-endpoint-secret",
                MODELARK_ASSET_GROUP_ID="group-live-assets",
            )
            self.copy_deploy_files(root)

            proc = self.run_preflight(root)

            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("Preflight passed", proc.stdout)

    def test_preflight_fails_when_iam_endpoint_is_missing(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            self.make_env(
                root,
                UPSTREAM_AUTH_MODE="iam",
                UPSTREAM_API_KEY="",
                UPSTREAM_ENDPOINT_ID="",
                BYTEPLUS_ACCESS_KEY_ID="AKliveendpointkey",
                BYTEPLUS_ACCESS_KEY_SECRET="live-endpoint-secret",
                MODELARK_ASSET_GROUP_ID="group-live-assets",
            )
            self.copy_deploy_files(root)

            proc = self.run_preflight(root)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("UPSTREAM_ENDPOINT_ID is required when UPSTREAM_AUTH_MODE=iam", proc.stdout)

    def test_preflight_fails_on_placeholder_secret_and_wrong_video_mode(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            self.make_env(
                root,
                ADMIN_KEY="replace-this-with-a-random-string",
                VIDEO_PERSIST_MODE="local",
            )
            self.copy_deploy_files(root)

            proc = self.run_preflight(root)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("ADMIN_KEY still looks like a placeholder", proc.stdout)
            self.assertIn("VIDEO_PERSIST_MODE must be proxy_only", proc.stdout)

    def test_preflight_rejects_public_domain_url_localhost_and_tunnel(self):
        cases = [
            ("https://video.customer-domain.com", "PUBLIC_DOMAIN must be a bare hostname"),
            ("operator:secret@video.customer-domain.com", "PUBLIC_DOMAIN must not include username or password"),
            ("localhost", "PUBLIC_DOMAIN must be an external Relay hostname"),
            ("relay.ngrok-free.app", "PUBLIC_DOMAIN must be a stable Relay hostname"),
            ("video.example.com", "PUBLIC_DOMAIN must be a real Relay hostname"),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                    root = Path(tmp)
                    self.make_env(root, PUBLIC_DOMAIN=value)
                    self.copy_deploy_files(root)

                    proc = self.run_preflight(root)

                    self.assertNotEqual(proc.returncode, 0, proc.stdout)
                    self.assertIn(expected, proc.stdout)


if __name__ == "__main__":
    unittest.main()
