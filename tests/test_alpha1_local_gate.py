import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from deploy.alpha1_local_gate import redact_sensitive_text


class Alpha1LocalGateTests(unittest.TestCase):
    def test_local_gate_redacts_sensitive_output_text(self):
        text = (
            "UPSTREAM_API_KEY=ark-real-secret\n"
            "provider URL https://bytepluses.com/path and byteplus debug\n"
            "Authorization: Bearer sk-secret-relay-key-12345\n"
            "relay_session=secret-cookie"
        )

        redacted = redact_sensitive_text(text)

        self.assertNotIn("ark-real-secret", redacted)
        self.assertNotIn("bytepluses.com", redacted.lower())
        self.assertNotIn("byteplus", redacted.lower())
        self.assertNotIn("sk-secret-relay-key-12345", redacted)
        self.assertNotIn("secret-cookie", redacted)

    def test_local_gate_dry_run_lists_required_spec_checks(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            out_dir = Path(tmp) / "local-gate"
            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_local_gate.py",
                    "--out-dir",
                    str(out_dir),
                    "--dry-run",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stdout)
            manifest = json.loads((out_dir / "local-gate-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "dry_run")
            self.assertFalse(manifest["local_acceptance_acceptable"])
            self.assertIn("--dry-run", manifest["local_blocker"])
            labels = [command["label"] for command in manifest["commands"]]
            self.assertEqual(
                labels,
                [
                    "python_py_compile",
                    "python_unittest",
                    "go_test",
                    "go_build",
                    "static_inline_js_compile",
                    "docker_compose_config",
                    "preflight",
                    "live_smoke_acceptance",
                    "local_acceptance_verify",
                    "diff_check",
                    "local_gate_manifest_verify",
                ],
            )
            serialized = json.dumps(manifest)
            self.assertIn("tests.test_alpha1_live_smoke.Alpha1LiveSmokeTests", serialized)
            self.assertIn("--local-only", serialized)
            self.assertIn("--local-gate-manifest", serialized)
            self.assertIn("alpha1-local-acceptance.json", serialized)
            self.assertIn("alpha1_worktree_check.py", serialized)
            py_compile = next(command for command in manifest["commands"] if command["label"] == "python_py_compile")
            self.assertIn("tests/test_alpha1_live_smoke.py", py_compile["command"])
            self.assertIn("tests/test_alpha1_preflight.py", py_compile["command"])
            self.assertIn("tests/test_alpha1_spec_controls.py", py_compile["command"])
            self.assertIn("tests/test_spec_scope_consistency.py", py_compile["command"])
            self.assertIn("tests/test_static_admin_ui.py", py_compile["command"])
            self.assertIn("tests/test_static_customer_ui.py", py_compile["command"])
            self.assertIn("deploy/alpha1_worktree_check.py", py_compile["command"])
            diff_check = next(command for command in manifest["commands"] if command["label"] == "diff_check")
            self.assertIn("deploy/alpha1_worktree_check.py", diff_check["command"])
            self.assertEqual(diff_check["display"], "python deploy/alpha1_worktree_check.py")
            self_verify = next(command for command in manifest["commands"] if command["label"] == "local_gate_manifest_verify")
            self.assertNotIn("--allow-pending-local-gate-self-check", self_verify["command"])
            self.assertNotIn("--allow-pending-local-gate-self-check", self_verify["display"])

    def test_worktree_check_rejects_untracked_trailing_whitespace(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True)
            (root / "clean.txt").write_text("clean\n", encoding="utf-8")
            subprocess.run(["git", "add", "clean.txt"], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True)
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            (root / "new.txt").write_text("bad trailing space \n", encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_worktree_check.py",
                    "--root",
                    str(root),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertEqual(proc.returncode, 1, proc.stdout)
            self.assertIn("trailing whitespace in untracked file", proc.stdout)

    def test_worktree_check_rejects_untracked_missing_final_newline(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True)
            (root / "new.txt").write_text("missing final newline", encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_worktree_check.py",
                    "--root",
                    str(root),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertEqual(proc.returncode, 1, proc.stdout)
            self.assertIn("missing newline at end of untracked file", proc.stdout)

    def test_worktree_check_rejects_staged_trailing_whitespace(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True)
            (root / "staged.txt").write_text("bad staged space \n", encoding="utf-8")
            subprocess.run(["git", "add", "staged.txt"], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True)

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_worktree_check.py",
                    "--root",
                    str(root),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertEqual(proc.returncode, 1, proc.stdout)
            self.assertIn("git diff --cached --check failed", proc.stdout)


if __name__ == "__main__":
    unittest.main()
