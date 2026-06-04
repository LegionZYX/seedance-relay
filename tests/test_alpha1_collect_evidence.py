import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from deploy.alpha1_collect_evidence import redact_text


class Alpha1CollectEvidenceTests(unittest.TestCase):
    def write_local_gate_manifest(self, root: Path):
        gate_dir = root / "local-gate"
        gate_dir.mkdir()
        local_acceptance = gate_dir / "alpha1-local-acceptance.json"
        local_acceptance.write_text('{"status":"passed","checks":{}}', encoding="utf-8")
        python_output = gate_dir / "python-unittest-output.txt"
        python_output.write_text(
            "test_malformed_authorization_does_not_fallback_to_cookie_session ... ok\n",
            encoding="utf-8",
        )
        go_output = gate_dir / "go-test-output.txt"
        go_output.write_text(
            "=== RUN   TestCreateVideoRejectsMalformedAuthorizationInsteadOfFallingBackToCookie\n",
            encoding="utf-8",
        )
        manifest = gate_dir / "local-gate-manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "status": "passed",
                    "local_acceptance_acceptable": True,
                    "artifacts": {"local_acceptance": str(local_acceptance), "manifest": str(manifest)},
                    "commands": [
                        {
                            "label": "python_unittest",
                            "command": ["python", "-m", "unittest", "discover"],
                            "output": str(python_output),
                            "dry_run": False,
                            "returncode": 0,
                        },
                        {
                            "label": "go_test",
                            "command": ["go", "test", "-v", "./..."],
                            "output": str(go_output),
                            "dry_run": False,
                            "returncode": 0,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        return manifest

    def test_collect_evidence_help_marks_local_gate_inputs_as_final_release_required(self):
        proc = subprocess.run(
            [
                sys.executable,
                "deploy/alpha1_collect_evidence.py",
                "--help",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("Final release required alpha1-local-acceptance.json", proc.stdout)
        self.assertIn("Final release required local-gate-manifest.json", proc.stdout)

    def test_collect_evidence_redacts_generic_sensitive_command_output(self):
        text = (
            "current sk-current-release-key-12345\n"
            "other sk-other-relay-key-12345\n"
            "upstream ark-live-secret\n"
            "provider https://bytepluses.com/private and byteplus debug\n"
            "Authorization: Bearer sk-bearer-secret-12345\n"
            "relay_session=session-secret"
        )

        redacted = redact_text(text, "sk-current-release-key-12345")

        for secret in (
            "sk-current-release-key-12345",
            "sk-other-relay-key-12345",
            "ark-live-secret",
            "bytepluses.com",
            "byteplus",
            "sk-bearer-secret-12345",
            "session-secret",
        ):
            self.assertNotIn(secret, redacted)
        self.assertIn("<redacted-api-key>", redacted)
        self.assertIn("<redacted-relay-key>", redacted)
        self.assertIn("<redacted-upstream-key>", redacted)
        self.assertIn("relay_session=<redacted>", redacted)

    def run_collect(self, root: Path, *, env=None, extra_args=None):
        args = [
            sys.executable,
            "deploy/alpha1_collect_evidence.py",
            "--base-url",
            "https://seedance-relay.customer-domain.com",
            "--video-id",
            "vid_existing_success",
            "--out-dir",
            str(root / "evidence"),
            "--dry-run",
        ]
        if extra_args:
            args.extend(extra_args)
        run_env = os.environ.copy()
        if env:
            run_env.update(env)
        return subprocess.run(
            args,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            env=run_env,
        )

    def test_collect_evidence_dry_run_writes_manifest_without_api_key(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            local_acceptance = root / "alpha1-local-acceptance.json"
            local_acceptance.write_text('{"status":"passed","checks":{}}', encoding="utf-8")
            proc = self.run_collect(
                root,
                env={"ALPHA1_CUSTOMER_API_KEY": "sk-secret-customer-key"},
                extra_args=["--local-acceptance-json", str(local_acceptance)],
            )

            self.assertEqual(proc.returncode, 0, proc.stdout)
            manifest = json.loads((root / "evidence" / "manifest.json").read_text(encoding="utf-8"))
            raw_manifest = json.dumps(manifest)
            self.assertEqual(manifest["status"], "dry_run")
            self.assertNotIn("sk-secret-customer-key", raw_manifest)
            self.assertIn("<redacted-api-key>", raw_manifest)
            self.assertTrue(manifest["browser_required"])
            self.assertFalse(manifest["release_acceptable"])
            self.assertIn("--dry-run", manifest["release_blocker"])
            artifacts = manifest["artifacts"]
            self.assertIn("alpha1-probe-evidence.json", artifacts["probe"])
            self.assertIn("alpha1-probe-output.txt", artifacts["probe_output"])
            self.assertIn("alpha1-browser-video.png", artifacts["screenshot"])
            self.assertIn("alpha1-preflight-output.txt", artifacts["preflight"])
            self.assertIn("caddy-validate-output.txt", artifacts["caddy"])
            self.assertIn("alpha1-local-acceptance.json", artifacts["local_acceptance"])
            labels = {command.get("label") for command in manifest["commands"]}
            self.assertIn("deploy_preflight", labels)
            self.assertIn("caddy_validate", labels)
            self.assertIn("external_probe", labels)
            self.assertIn("evidence_verify", labels)
            verify_command = next(command for command in manifest["commands"] if command.get("label") == "evidence_verify")
            self.assertIn("--probe-output", verify_command["command"])
            self.assertIn("alpha1-probe-output.txt", " ".join(verify_command["command"]))
            self.assertTrue((root / "evidence" / "alpha1-local-acceptance.json").exists())
            for output_path in artifacts.values():
                path = Path(output_path)
                if path.exists() and path.is_file():
                    self.assertNotIn("sk-secret-customer-key", path.read_text(encoding="utf-8"))

    def test_collect_evidence_dry_run_records_completion_audit_artifacts(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            local_acceptance = root / "alpha1-local-acceptance.json"
            local_acceptance.write_text('{"status":"passed","checks":{}}', encoding="utf-8")
            local_gate_manifest = self.write_local_gate_manifest(root)

            proc = self.run_collect(
                root,
                env={"ALPHA1_CUSTOMER_API_KEY": "sk-secret-customer-key"},
                extra_args=[
                    "--local-acceptance-json",
                    str(local_acceptance),
                    "--local-gate-manifest",
                    str(local_gate_manifest),
                ],
            )

            self.assertEqual(proc.returncode, 0, proc.stdout)
            manifest = json.loads((root / "evidence" / "manifest.json").read_text(encoding="utf-8"))
            artifacts = manifest["artifacts"]
            self.assertIn("alpha1-completion-output.txt", artifacts["completion_output"])
            self.assertIn("alpha1-completion-summary.json", artifacts["completion_summary"])
            labels = {command.get("label") for command in manifest["commands"]}
            self.assertIn("completion_audit", labels)
            completion_command = next(command for command in manifest["commands"] if command.get("label") == "completion_audit")
            self.assertIn("--manifest", completion_command["command"])
            self.assertIn("--local-gate-manifest", completion_command["command"])
            self.assertIn("alpha1-local-gate", " ".join(completion_command["command"]))
            self.assertTrue((root / "evidence" / "alpha1-completion-output.txt").exists())

    def test_collect_evidence_writes_completion_audit_label_before_running_completion_audit(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            from deploy import alpha1_collect_evidence as collect

            root = Path(tmp)
            local_acceptance = root / "alpha1-local-acceptance.json"
            local_acceptance.write_text('{"status":"passed","checks":{}}', encoding="utf-8")
            local_gate_manifest = self.write_local_gate_manifest(root)

            original_run_command = collect.run_command

            def fake_run_command(
                label,
                command,
                output_path,
                *,
                cwd,
                api_key,
                manifest,
                dry_run,
                write_manifest_path=None,
            ):
                manifest["commands"].append(
                    {
                        "label": label,
                        "command": command,
                        "output": str(output_path),
                        "dry_run": dry_run,
                        "returncode": 0,
                    }
                )
                output_path.parent.mkdir(parents=True, exist_ok=True)
                if write_manifest_path is not None:
                    write_manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
                if label == "completion_audit":
                    manifest_path = output_path.parent / "manifest.json"
                    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                    labels = {item.get("label") for item in payload.get("commands", []) if isinstance(item, dict)}
                    if "completion_audit" not in labels:
                        output_path.write_text("missing completion_audit label before completion audit\n", encoding="utf-8")
                        return 9
                output_path.write_text(f"{label} passed\n", encoding="utf-8")
                return 0

            env = os.environ.copy()
            env["ALPHA1_CUSTOMER_API_KEY"] = "sk-secret-customer-key"
            old_env = os.environ.copy()
            old_argv = sys.argv
            try:
                os.environ.clear()
                os.environ.update(env)
                sys.argv = [
                    "alpha1_collect_evidence.py",
                    "--root",
                    str(root),
                    "--base-url",
                    "https://seedance-relay.customer-domain.com",
                    "--video-id",
                    "vid_existing_success",
                    "--local-acceptance-json",
                    str(local_acceptance),
                    "--local-gate-manifest",
                    str(local_gate_manifest),
                    "--caddyfile",
                    str(root / "Caddyfile"),
                    "--out-dir",
                    str(root / "evidence"),
                ]
                collect.run_command = fake_run_command
                rc = collect.main()
            finally:
                collect.run_command = original_run_command
                sys.argv = old_argv
                os.environ.clear()
                os.environ.update(old_env)

            self.assertEqual(rc, 0)
            manifest = json.loads((root / "evidence" / "manifest.json").read_text(encoding="utf-8"))
            labels = {command.get("label") for command in manifest["commands"]}
            self.assertIn("completion_audit", labels)
            completion_command = next(command for command in manifest["commands"] if command["label"] == "completion_audit")
            self.assertEqual(completion_command["returncode"], 0)
            self.assertIn("--manifest", completion_command["command"])
            self.assertIn("--summary-json", completion_command["command"])

    def test_collect_evidence_copies_local_gate_command_outputs_for_archive_review(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            from deploy import alpha1_collect_evidence as collect

            root = Path(tmp)
            local_acceptance = root / "alpha1-local-acceptance.json"
            local_acceptance.write_text('{"status":"passed","checks":{}}', encoding="utf-8")
            local_gate_manifest = self.write_local_gate_manifest(root)

            original_run_command = collect.run_command

            def fake_run_command(
                label,
                command,
                output_path,
                *,
                cwd,
                api_key,
                manifest,
                dry_run,
                write_manifest_path=None,
            ):
                manifest["commands"].append(
                    {
                        "label": label,
                        "command": command,
                        "output": str(output_path),
                        "dry_run": dry_run,
                        "returncode": 0,
                    }
                )
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(f"{label} passed\n", encoding="utf-8")
                if write_manifest_path is not None:
                    write_manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
                return 0

            old_env = os.environ.copy()
            old_argv = sys.argv
            try:
                os.environ["ALPHA1_CUSTOMER_API_KEY"] = "sk-secret-customer-key"
                sys.argv = [
                    "alpha1_collect_evidence.py",
                    "--root",
                    str(root),
                    "--base-url",
                    "https://seedance-relay.customer-domain.com",
                    "--video-id",
                    "vid_existing_success",
                    "--local-acceptance-json",
                    str(local_acceptance),
                    "--local-gate-manifest",
                    str(local_gate_manifest),
                    "--caddyfile",
                    str(root / "Caddyfile"),
                    "--out-dir",
                    str(root / "evidence"),
                ]
                collect.run_command = fake_run_command
                rc = collect.main()
            finally:
                collect.run_command = original_run_command
                sys.argv = old_argv
                os.environ.clear()
                os.environ.update(old_env)

            self.assertEqual(rc, 0)
            copied_gate = root / "evidence" / "alpha1-local-gate"
            self.assertTrue((copied_gate / "local-gate-manifest.json").exists())
            self.assertTrue((copied_gate / "python-unittest-output.txt").exists())
            self.assertTrue((copied_gate / "go-test-output.txt").exists())
            manifest = json.loads((root / "evidence" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(
                Path(manifest["artifacts"]["local_gate_manifest"]),
                copied_gate / "local-gate-manifest.json",
            )

    def test_collect_evidence_requires_api_key(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            env = os.environ.copy()
            env.pop("ALPHA1_CUSTOMER_API_KEY", None)
            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_collect_evidence.py",
                    "--base-url",
                    "https://seedance-relay.customer-domain.com",
                    "--video-id",
                    "vid_existing_success",
                    "--out-dir",
                    str(Path(tmp) / "evidence"),
                    "--dry-run",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                env=env,
            )

            self.assertEqual(proc.returncode, 2, proc.stdout)
            self.assertIn("Missing API key", proc.stdout)

    def test_collect_evidence_requires_local_acceptance_for_final_release(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            env = os.environ.copy()
            env["ALPHA1_CUSTOMER_API_KEY"] = "sk-secret-customer-key"
            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_collect_evidence.py",
                    "--base-url",
                    "https://seedance-relay.customer-domain.com",
                    "--video-id",
                    "vid_existing_success",
                    "--out-dir",
                    str(Path(tmp) / "evidence"),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                env=env,
            )

            self.assertEqual(proc.returncode, 2, proc.stdout)
            self.assertIn("Missing --local-acceptance-json", proc.stdout)

    def test_collect_evidence_requires_local_gate_manifest_for_final_release(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            local_acceptance = root / "alpha1-local-acceptance.json"
            local_acceptance.write_text('{"status":"passed","checks":{}}', encoding="utf-8")
            env = os.environ.copy()
            env["ALPHA1_CUSTOMER_API_KEY"] = "sk-secret-customer-key"
            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_collect_evidence.py",
                    "--base-url",
                    "https://seedance-relay.customer-domain.com",
                    "--video-id",
                    "vid_existing_success",
                    "--local-acceptance-json",
                    str(local_acceptance),
                    "--out-dir",
                    str(root / "evidence"),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                env=env,
            )

            self.assertEqual(proc.returncode, 2, proc.stdout)
            self.assertIn("Missing --local-gate-manifest", proc.stdout)

    def test_collect_evidence_requires_external_https_base_url_for_final_release(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            local_acceptance = root / "alpha1-local-acceptance.json"
            local_acceptance.write_text('{"status":"passed","checks":{}}', encoding="utf-8")
            local_gate_manifest = self.write_local_gate_manifest(root)
            env = os.environ.copy()
            env["ALPHA1_CUSTOMER_API_KEY"] = "sk-secret-customer-key"

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_collect_evidence.py",
                    "--base-url",
                    "http://localhost:8002",
                    "--video-id",
                    "vid_existing_success",
                    "--local-acceptance-json",
                    str(local_acceptance),
                    "--local-gate-manifest",
                    str(local_gate_manifest),
                    "--out-dir",
                    str(root / "evidence"),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                env=env,
            )

            self.assertEqual(proc.returncode, 2, proc.stdout)
            self.assertIn("https:// base URL", proc.stdout)

    def test_collect_evidence_rejects_tunnel_base_url_for_final_release(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            local_acceptance = root / "alpha1-local-acceptance.json"
            local_acceptance.write_text('{"status":"passed","checks":{}}', encoding="utf-8")
            local_gate_manifest = self.write_local_gate_manifest(root)
            env = os.environ.copy()
            env["ALPHA1_CUSTOMER_API_KEY"] = "sk-secret-customer-key"

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_collect_evidence.py",
                    "--base-url",
                    "https://alpha1-demo.ngrok-free.app",
                    "--video-id",
                    "vid_existing_success",
                    "--local-acceptance-json",
                    str(local_acceptance),
                    "--local-gate-manifest",
                    str(local_gate_manifest),
                    "--out-dir",
                    str(root / "evidence"),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                env=env,
            )

            self.assertEqual(proc.returncode, 2, proc.stdout)
            self.assertIn("not a tunnel URL", proc.stdout)

    def test_collect_evidence_rejects_example_base_url_for_final_release(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            local_acceptance = root / "alpha1-local-acceptance.json"
            local_acceptance.write_text('{"status":"passed","checks":{}}', encoding="utf-8")
            local_gate_manifest = self.write_local_gate_manifest(root)
            env = os.environ.copy()
            env["ALPHA1_CUSTOMER_API_KEY"] = "sk-secret-customer-key"

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_collect_evidence.py",
                    "--base-url",
                    "https://seedance-relay.example.com",
                    "--video-id",
                    "vid_existing_success",
                    "--local-acceptance-json",
                    str(local_acceptance),
                    "--local-gate-manifest",
                    str(local_gate_manifest),
                    "--out-dir",
                    str(root / "evidence"),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                env=env,
            )

            self.assertEqual(proc.returncode, 2, proc.stdout)
            self.assertIn("not an example domain", proc.stdout)

    def test_collect_evidence_rejects_base_url_with_path_for_final_release(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            local_acceptance = root / "alpha1-local-acceptance.json"
            local_acceptance.write_text('{"status":"passed","checks":{}}', encoding="utf-8")
            local_gate_manifest = self.write_local_gate_manifest(root)
            env = os.environ.copy()
            env["ALPHA1_CUSTOMER_API_KEY"] = "sk-secret-customer-key"

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_collect_evidence.py",
                    "--base-url",
                    "https://seedance-relay.customer-domain.com/app?debug=1",
                    "--video-id",
                    "vid_existing_success",
                    "--local-acceptance-json",
                    str(local_acceptance),
                    "--local-gate-manifest",
                    str(local_gate_manifest),
                    "--out-dir",
                    str(root / "evidence"),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                env=env,
            )

            self.assertEqual(proc.returncode, 2, proc.stdout)
            self.assertIn("bare origin", proc.stdout)

    def test_collect_evidence_rejects_base_url_with_userinfo_for_final_release(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            local_acceptance = root / "alpha1-local-acceptance.json"
            local_acceptance.write_text('{"status":"passed","checks":{}}', encoding="utf-8")
            local_gate_manifest = self.write_local_gate_manifest(root)
            env = os.environ.copy()
            env["ALPHA1_CUSTOMER_API_KEY"] = "sk-secret-customer-key"

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_collect_evidence.py",
                    "--base-url",
                    "https://operator:secret@seedance-relay.customer-domain.com",
                    "--video-id",
                    "vid_existing_success",
                    "--local-acceptance-json",
                    str(local_acceptance),
                    "--local-gate-manifest",
                    str(local_gate_manifest),
                    "--out-dir",
                    str(root / "evidence"),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                env=env,
            )

            self.assertEqual(proc.returncode, 2, proc.stdout)
            self.assertIn("without username or password", proc.stdout)

    def test_collect_evidence_failed_commands_mark_manifest_not_release_acceptable(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            local_acceptance = root / "alpha1-local-acceptance.json"
            local_acceptance.write_text('{"status":"passed","checks":{}}', encoding="utf-8")
            local_gate_manifest = self.write_local_gate_manifest(root)
            env = os.environ.copy()
            env["ALPHA1_CUSTOMER_API_KEY"] = "sk-secret-customer-key"

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_collect_evidence.py",
                    "--base-url",
                    "https://seedance-relay.customer-domain.com",
                    "--video-id",
                    "vid_existing_success",
                    "--local-acceptance-json",
                    str(local_acceptance),
                    "--local-gate-manifest",
                    str(local_gate_manifest),
                    "--caddyfile",
                    str(root / "missing-Caddyfile"),
                    "--out-dir",
                    str(root / "evidence"),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                env=env,
            )

            self.assertEqual(proc.returncode, 1, proc.stdout)
            manifest = json.loads((root / "evidence" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "failed")
            self.assertFalse(manifest["release_acceptable"])
            self.assertIn("commands failed", manifest["release_blocker"])
            self.assertTrue(any(command.get("returncode") for command in manifest["commands"]))

    def test_collect_evidence_no_browser_is_marked_not_release_acceptable(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            proc = self.run_collect(
                root,
                env={"ALPHA1_CUSTOMER_API_KEY": "sk-secret-customer-key"},
                extra_args=["--no-browser"],
            )

            self.assertEqual(proc.returncode, 0, proc.stdout)
            manifest = json.loads((root / "evidence" / "manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["browser_required"])
            self.assertFalse(manifest["release_acceptable"])
            self.assertIn("--no-browser", manifest["release_blocker"])
            self.assertIn("--local-acceptance-json", manifest["release_blocker"])
            verify_output = (root / "evidence" / "alpha1-verify-output.txt").read_text(encoding="utf-8")
            self.assertIn("not release acceptable", verify_output)
            self.assertTrue(any(command.get("skipped") for command in manifest["commands"]))


if __name__ == "__main__":
    unittest.main()

