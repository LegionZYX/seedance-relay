import json
import subprocess
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


def png_with_dimensions(width: int = 1280, height: int = 720) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )


class Alpha1EvidenceVerifierTests(unittest.TestCase):
    def write_good_local_acceptance(self, root: Path):
        path = root / "alpha1-local-acceptance.json"
        checks = {
            "admin_created_customer": True,
            "admin_set_multiplier_and_models": True,
            "admin_model_list_default_and_empty_are_distinct": True,
            "admin_model_options_include_native_and_alias_choices": True,
            "model_aliases_are_opt_in_per_customer": True,
            "admin_surfaces_have_no_sfw_nsfw_customer_toggle": True,
            "customer_surfaces_keep_operator_nsfw_notes_private": True,
            "customer_changed_password": True,
            "customer_password_reuse_rejected": True,
            "cookie_password_change_rejects_cross_site_origin": True,
            "cookie_password_change_revokes_other_sessions": True,
            "customer_old_password_rejected": True,
            "customer_new_password_login_works": True,
            "stale_session_revoked_after_password_change": True,
            "admin_reset_customer_password": True,
            "admin_reset_password_revoked_customer_session": True,
            "customer_rotated_api_key": True,
            "customer_account_surfaces_mask_relay_api_key": True,
            "relay_api_keys_are_server_generated": True,
            "old_api_key_failed": True,
            "admin_rotated_customer_relay_key": True,
            "old_customer_key_failed_after_admin_rotation": True,
            "admin_api_key_rotation_revoked_customer_session": True,
            "admin_rotated_customer_key_works": True,
            "customer_sees_only_enabled_models": True,
            "runtime_estimate_uses_customer_multiplier": True,
            "runtime_cookie_estimate_accepts_session": True,
            "runtime_cookie_create_rejects_cross_site_before_upstream": True,
            "disabled_model_rejected_before_upstream": True,
            "enabled_native_content_created": True,
            "prompt_text_passed_without_relay_censorship": True,
            "runtime_prepare_helper_rejects_unowned_asset_before_upstream": True,
            "runtime_prepare_failure_refunds_reserved_balance": True,
            "runtime_upstream_error_refunds_reserved_balance": True,
            "runtime_local_task_write_failure_cancels_upstream_and_refunds": True,
            "runtime_create_rejects_insufficient_balance_before_prepare": True,
            "runtime_terminal_refresh_settles_once_and_refunds_hold": True,
            "runtime_settlement_uses_task_multiplier_snapshot": True,
            "runtime_failed_terminal_refresh_refunds_hold_once": True,
            "fastapi_fallback_upstream_error_refunds_reserved_balance": True,
            "fastapi_fallback_local_task_write_failure_cancels_upstream_and_refunds": True,
            "task_detail_uses_relay_video_url": True,
            "task_list_is_customer_scoped_and_relay_only": True,
            "task_list_excludes_other_customers_tasks": True,
            "task_list_status_limit_offset_works": True,
            "video_content_rejects_other_customer_before_upstream": True,
            "head_content_uses_relay_without_redirect": True,
            "range_playback_uses_relay_without_redirect": True,
            "browser_network_uses_relay_video_url": True,
            "process_logs_hide_upstream_urls_and_secrets": True,
            "no_new_local_mp4": True,
            "admin_user_list_masks_customer_and_upstream_keys": True,
            "admin_user_detail_masks_customer_and_upstream_keys": True,
            "audit_contains_expected_secret_safe_actions": True,
            "caddy_equivalent_routes_hot_paths_to_go": True,
            "control_plane_paths_remain_on_fastapi": True,
        }
        path.write_text(
            json.dumps(
                {
                    "status": "passed",
                    "checks": checks,
                    "warnings": [],
                    "runtime_paths": [
                        {"method": "GET", "path": "/v1/models"},
                        {"method": "POST", "path": "/v1/videos/estimate"},
                        {"method": "POST", "path": "/v1/videos"},
                        {"method": "GET", "path": "/v1/videos"},
                        {"method": "GET", "path": "/v1/videos?status=succeeded&limit=1&offset=1"},
                        {"method": "GET", "path": "/v1/videos/vid"},
                        {"method": "GET", "path": "/v1/videos/vid/content"},
                        {"method": "HEAD", "path": "/v1/videos/vid/content"},
                    ],
                    "relay_paths": [
                        {"method": "POST", "path": "/admin/users"},
                        {"method": "GET", "path": "/admin/model-options"},
                        {"method": "PATCH", "path": "/admin/users/u_demo"},
                        {"method": "POST", "path": "/admin/users/u_demo/api-key/rotate"},
                        {"method": "POST", "path": "/admin/users/u_demo/topup"},
                        {"method": "GET", "path": "/admin/users"},
                        {"method": "GET", "path": "/admin/users/u_demo"},
                        {"method": "POST", "path": "/auth/login"},
                        {"method": "POST", "path": "/auth/change-password"},
                        {"method": "GET", "path": "/v1/me"},
                        {"method": "POST", "path": "/v1/me/api-key/rotate"},
                        {"method": "GET", "path": "/admin/audit-events?target_id=user&limit=20"},
                    ],
                    "audit_actions": [
                        "admin_changed_enabled_models",
                        "admin_changed_price_multiplier",
                        "admin_changed_balance",
                        "admin_changed_status",
                        "admin_changed_upstream_key",
                        "admin_reset_password",
                        "admin_rotated_customer_api_key",
                        "customer_api_key_rotated",
                        "customer_password_changed",
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def write_good_artifacts(self, root: Path):
        probe = root / "alpha1-probe-evidence.json"
        probe_output = root / "alpha1-probe-output.txt"
        screenshot = root / "alpha1-browser-video.png"
        preflight = root / "alpha1-preflight-output.txt"
        caddy = root / "caddy-validate-output.txt"

        probe.write_text(
            json.dumps(
                {
                    "failures": [],
                    "warnings": [],
                    "evidence": {
                        "health": 200,
                        "me.email_present": True,
                        "models.count": 1,
                        "videos.total": 1,
                        "video_url.relay_domain": True,
                        "content.head_status": 200,
                        "content.head_location_present": False,
                        "content.range_status": 206,
                        "content.range_content_range_present": True,
                        "content.range_location_present": False,
                        "browser.expected_content_url": "https://seedance-relay.customer-domain.com/v1/videos/vid/content",
                        "browser.request_urls": ["https://seedance-relay.customer-domain.com/v1/videos/vid/content"],
                        "browser.screenshot": str(screenshot),
                        "browser.responses": [
                            {
                                "url": "https://seedance-relay.customer-domain.com/v1/videos/vid/content",
                                "status": 206,
                                "content_type": "video/mp4",
                                "content_range": "bytes 0-1/10",
                                "location_present": False,
                            }
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )
        probe_output.write_text("Probe passed: 0 warning(s)\n", encoding="utf-8")
        screenshot.write_bytes(png_with_dimensions())
        preflight.write_text(
            "[OK] caddy validate passed: /etc/caddy/Caddyfile\nPreflight passed: 0 warning(s)\n",
            encoding="utf-8",
        )
        caddy.write_text("Valid configuration\n", encoding="utf-8")
        return probe, screenshot, preflight, caddy

    def write_good_completion_summary(self, path: Path):
        path.write_text(
            json.dumps(
                {
                    "status": "complete",
                    "release_complete": True,
                    "checks": {"external_manifest": {"status": "passed"}},
                    "failures": [],
                }
            ),
            encoding="utf-8",
        )
        return path

    def write_collector_manifest(
        self,
        root: Path,
        *,
        probe: Path,
        probe_output: Path,
        screenshot: Path,
        preflight: Path,
        caddy: Path,
        local_acceptance: Path,
        local_gate_manifest: Path,
        verify: Path,
        completion_output: Path,
        completion_summary: Path,
    ) -> Path:
        manifest = root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "status": "passed",
                    "release_acceptable": True,
                    "base_url": "https://seedance-relay.customer-domain.com",
                    "video_id": "vid",
                    "browser_required": True,
                    "commands": [
                        {"label": "deploy_preflight", "command": ["python", "deploy/alpha1_preflight.py"], "output": str(preflight), "returncode": 0},
                        {"label": "caddy_validate", "command": ["caddy", "validate"], "output": str(caddy), "returncode": 0},
                        {"label": "external_probe", "command": ["python", "deploy/alpha1_probe.py"], "output": str(probe_output), "returncode": 0},
                        {"label": "evidence_verify", "command": ["python", "deploy/alpha1_verify_evidence.py"], "output": str(verify), "returncode": 0},
                        {"label": "completion_audit", "command": ["python", "deploy/alpha1_completion_audit.py", "--manifest", str(manifest), "--summary-json", str(completion_summary)], "output": str(completion_output), "returncode": 0},
                    ],
                    "artifacts": {
                        "probe": str(probe),
                        "probe_output": str(probe_output),
                        "screenshot": str(screenshot),
                        "preflight": str(preflight),
                        "caddy": str(caddy),
                        "local_acceptance": str(local_acceptance),
                        "local_gate_manifest": str(local_gate_manifest),
                        "verify": str(verify),
                        "completion_output": str(completion_output),
                        "completion_summary": str(completion_summary),
                    },
                }
            ),
            encoding="utf-8",
        )
        return manifest

    def write_good_local_gate_manifest(self, root: Path, local_acceptance: Path | None = None):
        if local_acceptance is None:
            local_acceptance = self.write_good_local_acceptance(root)
        labels = [
            "python_py_compile",
            "python_unittest",
            "go_test",
            "go_build",
            "static_inline_js_compile",
            "docker_compose_config",
            "preflight",
            "live_smoke_acceptance",
            "local_acceptance_verify",
            "local_gate_manifest_verify",
            "diff_check",
        ]
        commands = []
        for label in labels:
            output = root / f"{label}.txt"
            if label in {"local_acceptance_verify", "local_gate_manifest_verify"}:
                output.write_text("Evidence verification passed: 0 warning(s)\n", encoding="utf-8")
            elif label == "python_unittest":
                output.write_text(
                    "\n".join(
                        [
                            "test_malformed_bearer_with_cookie_does_not_bypass_csrf_origin_guard ... ok",
                            "test_malformed_authorization_does_not_fallback_to_cookie_session ... ok",
                            "test_delete_cancel_refunds_hold_only_once_at_db_boundary ... ok",
                            "OK",
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )
            elif label == "go_test":
                output.write_text(
                    "\n".join(
                        [
                            "=== RUN   TestCreateVideoRejectsMalformedBearerWithCookieViaCSRFBeforeUpstream",
                            "--- PASS: TestCreateVideoRejectsMalformedBearerWithCookieViaCSRFBeforeUpstream (0.00s)",
                            "=== RUN   TestCreateVideoRejectsMalformedAuthorizationInsteadOfFallingBackToCookie",
                            "--- PASS: TestCreateVideoRejectsMalformedAuthorizationInsteadOfFallingBackToCookie (0.00s)",
                            "PASS",
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )
            else:
                output.write_text(f"{label} passed\n", encoding="utf-8")
            commands.append(
                {
                    "label": label,
                    "command": [label],
                    "output": str(output),
                    "dry_run": False,
                    "returncode": 0,
                }
            )
        manifest = root / "local-gate-manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "status": "passed",
                    "local_acceptance_acceptable": True,
                    "artifacts": {
                        "local_acceptance": str(local_acceptance),
                        "manifest": str(manifest),
                    },
                    "commands": commands,
                }
            ),
            encoding="utf-8",
        )
        return manifest

    def run_verifier(self, probe, screenshot, preflight, caddy, local_acceptance=None, local_gate_manifest=None):
        if local_acceptance is None:
            local_acceptance = self.write_good_local_acceptance(Path(probe).parent)
        if local_gate_manifest is None:
            local_gate_manifest = self.write_good_local_gate_manifest(Path(probe).parent, local_acceptance)
        return subprocess.run(
            [
                sys.executable,
                "deploy/alpha1_verify_evidence.py",
                "--base-url",
                "https://seedance-relay.customer-domain.com",
                "--video-id",
                "vid",
                "--probe-json",
                str(probe),
                "--screenshot",
                str(screenshot),
                "--preflight-output",
                str(preflight),
                "--caddy-output",
                str(caddy),
                "--local-acceptance-json",
                str(local_acceptance),
                "--local-gate-manifest",
                str(local_gate_manifest),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def test_verifier_accepts_complete_external_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            artifacts = self.write_good_artifacts(Path(tmp))
            proc = self.run_verifier(*artifacts)

            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("Evidence verification passed", proc.stdout)
            self.assertIn("screenshot PNG dimensions verified", proc.stdout)

    def test_verifier_rejects_non_png_screenshot(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            screenshot.write_bytes(b"not a png screenshot")

            proc = self.run_verifier(probe, screenshot, preflight, caddy)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("screenshot is not a PNG file", proc.stdout)

    def test_verifier_rejects_tiny_placeholder_screenshot(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            screenshot.write_bytes(png_with_dimensions(width=1, height=1))

            proc = self.run_verifier(probe, screenshot, preflight, caddy)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("screenshot dimensions too small", proc.stdout)

    def test_verifier_rejects_probe_screenshot_mismatch(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            payload = json.loads(probe.read_text(encoding="utf-8"))
            payload["evidence"]["browser.screenshot"] = str(root / "other-browser-video.png")
            probe.write_text(json.dumps(payload), encoding="utf-8")

            proc = self.run_verifier(probe, screenshot, preflight, caddy)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("browser.screenshot does not match the verified screenshot artifact", proc.stdout)

    def test_explicit_verifier_checks_probe_command_output(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            (root / "alpha1-probe-output.txt").write_text("Probe failed\n", encoding="utf-8")

            proc = self.run_verifier(probe, screenshot, preflight, caddy)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("probe output does not show 'Probe passed'", proc.stdout)

    def test_verifier_accepts_local_acceptance_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            local_gate_manifest = self.write_good_local_gate_manifest(root)

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--base-url",
                    "https://seedance-relay.customer-domain.com",
                    "--video-id",
                    "vid",
                    "--probe-json",
                    str(probe),
                    "--screenshot",
                    str(screenshot),
                    "--preflight-output",
                    str(preflight),
                    "--caddy-output",
                    str(caddy),
                    "--local-acceptance-json",
                    str(local_acceptance),
                    "--local-gate-manifest",
                    str(local_gate_manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("local acceptance status passed", proc.stdout)
            self.assertIn("local acceptance check verified: admin_created_customer", proc.stdout)
            self.assertIn("local acceptance runtime path verified: GET /v1/models", proc.stdout)
            self.assertIn("local acceptance audit action verified: admin_changed_price_multiplier", proc.stdout)

    def test_verifier_accepts_local_only_acceptance_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            local_acceptance = self.write_good_local_acceptance(Path(tmp))

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--local-only",
                    "--local-acceptance-json",
                    str(local_acceptance),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("local acceptance status passed", proc.stdout)
            self.assertIn("Evidence verification passed", proc.stdout)

    def test_verifier_accepts_local_gate_manifest(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            manifest = self.write_good_local_gate_manifest(Path(tmp))

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--local-gate-manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("local gate status passed", proc.stdout)
            self.assertIn("local gate required command labels verified", proc.stdout)
            self.assertIn("local acceptance status passed", proc.stdout)

    def test_verifier_prefers_copied_local_gate_artifacts_over_original_absolute_paths(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            original = root / "original-local-gate"
            original.mkdir(parents=True, exist_ok=True)
            manifest = self.write_good_local_gate_manifest(original)
            copied = root / "copied-evidence" / "alpha1-local-gate"
            shutil.copytree(original, copied)

            copied_self_verify = copied / "local_gate_manifest_verify.txt"
            copied_self_verify.write_text("copied local gate output missing verifier marker\n", encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--local-gate-manifest",
                    str(copied / manifest.name),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertEqual(proc.returncode, 1, proc.stdout)
            self.assertIn(
                "local gate output local_gate_manifest_verify does not show 'Evidence verification passed'",
                proc.stdout,
            )

    def test_verifier_rejects_copied_local_gate_manifest_without_archived_outputs(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            original = root / "original-local-gate"
            original.mkdir(parents=True, exist_ok=True)
            manifest = self.write_good_local_gate_manifest(original)
            copied = root / "copied-evidence" / "alpha1-local-gate"
            copied.mkdir(parents=True, exist_ok=True)
            copied_manifest = copied / manifest.name
            copied_manifest.write_bytes(manifest.read_bytes())

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--local-gate-manifest",
                    str(copied_manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("local gate output python_unittest is not archived beside the manifest", proc.stdout)
            self.assertIn("local gate local_acceptance artifact is not archived beside the manifest", proc.stdout)

    def test_verifier_rejects_local_gate_manifest_without_self_verify_label(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            manifest = self.write_good_local_gate_manifest(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["commands"] = [
                item for item in payload["commands"] if item["label"] != "local_gate_manifest_verify"
            ]
            manifest.write_text(json.dumps(payload), encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--local-gate-manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("local gate missing command label: local_gate_manifest_verify", proc.stdout)

    def test_verifier_rejects_local_gate_manifest_without_required_unittest_markers(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            manifest = self.write_good_local_gate_manifest(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            unittest_command = next(
                item for item in payload["commands"] if item["label"] == "python_unittest"
            )
            Path(unittest_command["output"]).write_text("test_some_other_case ... ok\nOK\n", encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--local-gate-manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local gate output python_unittest missing required unittest marker: "
                "test_malformed_authorization_does_not_fallback_to_cookie_session",
                proc.stdout,
            )

    def test_verifier_rejects_local_gate_manifest_without_required_go_test_markers(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            manifest = self.write_good_local_gate_manifest(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            go_command = next(
                item for item in payload["commands"] if item["label"] == "go_test"
            )
            Path(go_command["output"]).write_text("ok  seedance-runtime/internal/httpapi\n", encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--local-gate-manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local gate output go_test missing required go test marker: "
                "TestCreateVideoRejectsMalformedAuthorizationInsteadOfFallingBackToCookie",
                proc.stdout,
            )

    def test_verifier_rejects_local_gate_manifest_with_invalid_self_verify_output(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            manifest = self.write_good_local_gate_manifest(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self_verify = next(
                item for item in payload["commands"] if item["label"] == "local_gate_manifest_verify"
            )
            Path(self_verify["output"]).write_text("local_gate_manifest_verify passed\n", encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--local-gate-manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("local gate output local_gate_manifest_verify does not show 'Evidence verification passed'", proc.stdout)

    def test_verifier_allows_pending_self_verify_output_only_for_local_gate_internal_check(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            manifest = self.write_good_local_gate_manifest(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self_verify = next(
                item for item in payload["commands"] if item["label"] == "local_gate_manifest_verify"
            )
            Path(self_verify["output"]).write_text(
                "PENDING: local gate manifest self-verification has not run yet.\n",
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--local-gate-manifest",
                    str(manifest),
                    "--allow-pending-local-gate-self-check",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("local gate output local_gate_manifest_verify pending self-check accepted", proc.stdout)

    def test_verifier_explicit_mode_with_local_gate_manifest_checks_all_artifacts(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            local_gate_manifest = self.write_good_local_gate_manifest(root)

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--base-url",
                    "https://seedance-relay.customer-domain.com",
                    "--video-id",
                    "vid",
                    "--probe-json",
                    str(probe),
                    "--screenshot",
                    str(screenshot),
                    "--preflight-output",
                    str(preflight),
                    "--caddy-output",
                    str(caddy),
                    "--local-acceptance-json",
                    str(local_acceptance),
                    "--local-gate-manifest",
                    str(local_gate_manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("probe failures empty", proc.stdout)
            self.assertIn("screenshot exists and is non-empty", proc.stdout)
            self.assertIn("local gate status passed", proc.stdout)

    def test_verifier_rejects_incomplete_local_gate_manifest(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            manifest = self.write_good_local_gate_manifest(Path(tmp))
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["status"] = "dry_run"
            payload["local_acceptance_acceptable"] = False
            payload["commands"][0]["dry_run"] = True
            payload["commands"][1]["returncode"] = 1
            payload["commands"] = [
                item for item in payload["commands"] if item["label"] != "diff_check"
            ]
            manifest.write_text(json.dumps(payload), encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--local-gate-manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("local gate status is 'dry_run'", proc.stdout)
            self.assertIn("local gate is not acceptance acceptable", proc.stdout)
            self.assertIn("manifest contains dry-run command evidence", proc.stdout)
            self.assertIn("manifest contains failed command return codes", proc.stdout)
            self.assertIn("local gate missing command label: diff_check", proc.stdout)

    def test_verifier_local_only_requires_local_acceptance_json(self):
        proc = subprocess.run(
            [
                sys.executable,
                "deploy/alpha1_verify_evidence.py",
                "--local-only",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("--local-only requires --local-acceptance-json", proc.stdout)

    def test_verifier_requires_local_acceptance_route_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["runtime_paths"] = [
                item for item in payload["runtime_paths"] if item["path"] != "/v1/videos"
            ]
            payload["relay_paths"] = [
                item for item in payload["relay_paths"] if item["path"] != "/auth/change-password"
            ]
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("local acceptance runtime_paths missing GET /v1/videos", proc.stdout)
            self.assertIn("local acceptance relay_paths missing POST /auth/change-password", proc.stdout)

    def test_verifier_requires_local_acceptance_audit_actions(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["audit_actions"] = [
                action for action in payload["audit_actions"] if action != "customer_api_key_rotated"
            ]
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("local acceptance audit_actions missing 'customer_api_key_rotated'", proc.stdout)

    def test_verifier_requires_local_acceptance_in_explicit_mode(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            probe, screenshot, preflight, caddy = self.write_good_artifacts(Path(tmp))

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--base-url",
                    "https://seedance-relay.customer-domain.com",
                    "--video-id",
                    "vid",
                    "--probe-json",
                    str(probe),
                    "--screenshot",
                    str(screenshot),
                    "--preflight-output",
                    str(preflight),
                    "--caddy-output",
                    str(caddy),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("missing --local-acceptance-json", proc.stdout)

    def test_verifier_requires_local_gate_manifest_in_explicit_mode(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--base-url",
                    "https://seedance-relay.customer-domain.com",
                    "--video-id",
                    "vid",
                    "--probe-json",
                    str(probe),
                    "--screenshot",
                    str(screenshot),
                    "--preflight-output",
                    str(preflight),
                    "--caddy-output",
                    str(caddy),
                    "--local-acceptance-json",
                    str(local_acceptance),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("missing --local-gate-manifest", proc.stdout)

    def test_verifier_requires_base_url_in_explicit_mode(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            local_gate_manifest = self.write_good_local_gate_manifest(root)

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--probe-json",
                    str(probe),
                    "--screenshot",
                    str(screenshot),
                    "--preflight-output",
                    str(preflight),
                    "--caddy-output",
                    str(caddy),
                    "--local-acceptance-json",
                    str(local_acceptance),
                    "--local-gate-manifest",
                    str(local_gate_manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("missing --base-url", proc.stdout)

    def test_verifier_requires_video_id_in_explicit_mode(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            local_gate_manifest = self.write_good_local_gate_manifest(root)

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--base-url",
                    "https://seedance-relay.customer-domain.com",
                    "--probe-json",
                    str(probe),
                    "--screenshot",
                    str(screenshot),
                    "--preflight-output",
                    str(preflight),
                    "--caddy-output",
                    str(caddy),
                    "--local-acceptance-json",
                    str(local_acceptance),
                    "--local-gate-manifest",
                    str(local_gate_manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("missing --video-id", proc.stdout)

    def test_verifier_requires_probe_models_and_videos_surfaces(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            payload = json.loads(probe.read_text(encoding="utf-8"))
            payload["evidence"]["models.count"] = 0
            payload["evidence"].pop("videos.total")
            probe.write_text(json.dumps(payload), encoding="utf-8")

            proc = self.run_verifier(probe, screenshot, preflight, caddy)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("probe evidence models.count", proc.stdout)
            self.assertIn("probe evidence videos.total", proc.stdout)

    def test_verifier_accepts_collector_manifest(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            local_gate_manifest = self.write_good_local_gate_manifest(root)
            probe_output = root / "alpha1-probe-output.txt"
            probe_output.write_text("Probe passed: 0 warning(s)\n", encoding="utf-8")
            verify = root / "alpha1-verify-output.txt"
            verify.write_text("Evidence verification passed: 0 warning(s)\n", encoding="utf-8")
            completion_output = root / "alpha1-completion-output.txt"
            completion_output.write_text("Alpha1 completion audit passed\n", encoding="utf-8")
            completion_summary = root / "alpha1-completion-summary.json"
            self.write_good_completion_summary(completion_summary)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "release_acceptable": True,
                        "base_url": "https://seedance-relay.customer-domain.com",
                        "video_id": "vid",
                        "browser_required": True,
                        "commands": [
                            {"label": "deploy_preflight", "command": ["python", "deploy/alpha1_preflight.py"], "output": str(preflight), "returncode": 0},
                            {"label": "caddy_validate", "command": ["caddy", "validate"], "output": str(caddy), "returncode": 0},
                            {"label": "external_probe", "command": ["python", "deploy/alpha1_probe.py"], "output": str(probe_output), "returncode": 0},
                            {"label": "evidence_verify", "command": ["python", "deploy/alpha1_verify_evidence.py"], "output": str(verify), "returncode": 0},
                            {"label": "completion_audit", "command": ["python", "deploy/alpha1_completion_audit.py", "--manifest", str(manifest), "--summary-json", str(completion_summary)], "output": str(completion_output), "returncode": 0},
                        ],
                        "artifacts": {
                            "probe": str(probe),
                            "probe_output": str(probe_output),
                            "screenshot": str(screenshot),
                            "preflight": str(preflight),
                            "caddy": str(caddy),
                            "local_acceptance": str(local_acceptance),
                            "local_gate_manifest": str(local_gate_manifest),
                            "verify": str(verify),
                            "completion_output": str(completion_output),
                            "completion_summary": str(completion_summary),
                        },
                    }
                ),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("manifest status passed", proc.stdout)
            self.assertIn("local acceptance status passed", proc.stdout)
            self.assertIn("local gate status passed", proc.stdout)
            self.assertIn("collector manifest required command labels verified", proc.stdout)
            self.assertIn("Evidence verification passed", proc.stdout)

            completion_summary.write_text('{"release_complete":true}\n', encoding="utf-8")
            thin_summary = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertNotEqual(thin_summary.returncode, 0, thin_summary.stdout)
            self.assertIn("completion audit summary status is not complete", thin_summary.stdout)
            self.assertIn("completion audit summary checks must be an object", thin_summary.stdout)
            self.write_good_completion_summary(completion_summary)

            with_local_gate_match = {
                "status": "complete",
                "release_complete": True,
                "checks": {
                    "external_manifest": {"status": "passed"},
                    "local_gate_match": {"status": "passed"},
                },
                "failures": [],
                "local_gate_match": False,
            }
            completion_summary.write_text(json.dumps(with_local_gate_match), encoding="utf-8")
            bad_local_gate_match = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertNotEqual(bad_local_gate_match.returncode, 0, bad_local_gate_match.stdout)
            self.assertIn("completion audit summary local_gate_match is not true", bad_local_gate_match.stdout)
            self.write_good_completion_summary(completion_summary)

            payload = json.loads(manifest.read_text(encoding="utf-8"))
            missing_summary_arg_payload = json.loads(json.dumps(payload))
            for command in missing_summary_arg_payload["commands"]:
                if command.get("label") == "completion_audit":
                    command["command"] = ["python", "deploy/alpha1_completion_audit.py"]
            manifest.write_text(json.dumps(missing_summary_arg_payload), encoding="utf-8")
            missing_summary_arg = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertNotEqual(missing_summary_arg.returncode, 0, missing_summary_arg.stdout)
            self.assertIn("completion_audit command missing --summary-json", missing_summary_arg.stdout)

            wrong_summary_payload = json.loads(json.dumps(payload))
            wrong_summary = root / "wrong-completion-summary.json"
            wrong_summary.write_text('{"release_complete":true}\n', encoding="utf-8")
            for command in wrong_summary_payload["commands"]:
                if command.get("label") == "completion_audit":
                    command["command"] = [
                        "python",
                        "deploy/alpha1_completion_audit.py",
                        "--manifest",
                        str(manifest),
                        "--summary-json",
                        str(wrong_summary),
                    ]
            manifest.write_text(json.dumps(wrong_summary_payload), encoding="utf-8")
            wrong_summary_arg = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertNotEqual(wrong_summary_arg.returncode, 0, wrong_summary_arg.stdout)
            self.assertIn(
                "completion_audit command does not reference artifacts.completion_summary",
                wrong_summary_arg.stdout,
            )

            wrong_same_name_payload = json.loads(json.dumps(payload))
            wrong_same_name = root / "wrong-dir" / completion_summary.name
            wrong_same_name.parent.mkdir()
            wrong_same_name.write_text('{"release_complete":true}\n', encoding="utf-8")
            for command in wrong_same_name_payload["commands"]:
                if command.get("label") == "completion_audit":
                    command["command"] = [
                        "python",
                        "deploy/alpha1_completion_audit.py",
                        "--manifest",
                        str(manifest),
                        "--summary-json",
                        str(wrong_same_name),
                    ]
            manifest.write_text(json.dumps(wrong_same_name_payload), encoding="utf-8")
            wrong_same_name_arg = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertNotEqual(wrong_same_name_arg.returncode, 0, wrong_same_name_arg.stdout)
            self.assertIn(
                "completion_audit command does not reference artifacts.completion_summary",
                wrong_same_name_arg.stdout,
            )

            wrong_manifest_payload = json.loads(json.dumps(payload))
            wrong_manifest = root / "wrong-manifest.json"
            wrong_manifest.write_text("{}", encoding="utf-8")
            for command in wrong_manifest_payload["commands"]:
                if command.get("label") == "completion_audit":
                    command["command"] = [
                        "python",
                        "deploy/alpha1_completion_audit.py",
                        "--manifest",
                        str(wrong_manifest),
                        "--summary-json",
                        str(completion_summary),
                    ]
            manifest.write_text(json.dumps(wrong_manifest_payload), encoding="utf-8")
            wrong_manifest_arg = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertNotEqual(wrong_manifest_arg.returncode, 0, wrong_manifest_arg.stdout)
            self.assertIn(
                "completion_audit command does not reference the current manifest",
                wrong_manifest_arg.stdout,
            )

            payload["base_url"] = "https://other-relay.customer-domain.com"
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            mismatch = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertNotEqual(mismatch.returncode, 0, mismatch.stdout)
            self.assertIn("browser.expected_content_url does not match manifest base_url", mismatch.stdout)

            payload["base_url"] = "https://seedance-relay.customer-domain.com"
            payload["video_id"] = "other-vid"
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            video_mismatch = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertNotEqual(video_mismatch.returncode, 0, video_mismatch.stdout)
            self.assertIn("browser.expected_content_url does not match release video_id", video_mismatch.stdout)

    def test_verifier_rejects_pending_completion_audit_manifest_in_final_mode(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            local_gate_manifest = self.write_good_local_gate_manifest(root)
            probe_output = root / "alpha1-probe-output.txt"
            probe_output.write_text("Probe passed: 0 warning(s)\n", encoding="utf-8")
            verify = root / "alpha1-verify-output.txt"
            verify.write_text("Evidence verification passed: 0 warning(s)\n", encoding="utf-8")
            completion_output = root / "alpha1-completion-output.txt"
            completion_summary = root / "alpha1-completion-summary.json"
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "release_acceptable": True,
                        "base_url": "https://seedance-relay.customer-domain.com",
                        "video_id": "vid",
                        "browser_required": True,
                        "commands": [
                            {"label": "deploy_preflight", "command": ["python", "deploy/alpha1_preflight.py"], "output": str(preflight), "returncode": 0},
                            {"label": "caddy_validate", "command": ["caddy", "validate"], "output": str(caddy), "returncode": 0},
                            {"label": "external_probe", "command": ["python", "deploy/alpha1_probe.py"], "output": str(probe_output), "returncode": 0},
                            {"label": "evidence_verify", "command": ["python", "deploy/alpha1_verify_evidence.py"], "output": str(verify), "returncode": 0},
                            {"label": "completion_audit", "command": ["python", "deploy/alpha1_completion_audit.py", "--manifest", str(manifest), "--summary-json", str(completion_summary)], "output": str(completion_output)},
                        ],
                        "artifacts": {
                            "probe": str(probe),
                            "probe_output": str(probe_output),
                            "screenshot": str(screenshot),
                            "preflight": str(preflight),
                            "caddy": str(caddy),
                            "local_acceptance": str(local_acceptance),
                            "local_gate_manifest": str(local_gate_manifest),
                            "verify": str(verify),
                            "completion_output": str(completion_output),
                            "completion_summary": str(completion_summary),
                        },
                    }
                ),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("manifest contains command entries without return codes", proc.stdout)
            self.assertIn("missing file:", proc.stdout)
            self.assertIn("alpha1-completion-output.txt", proc.stdout)
            self.assertIn("alpha1-completion-summary.json", proc.stdout)

    def test_verifier_rejects_collector_manifest_without_required_command_labels(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            local_gate_manifest = self.write_good_local_gate_manifest(root)
            probe_output = root / "alpha1-probe-output.txt"
            probe_output.write_text("Probe passed: 0 warning(s)\n", encoding="utf-8")
            verify = root / "alpha1-verify-output.txt"
            verify.write_text("Evidence verification passed: 0 warning(s)\n", encoding="utf-8")
            completion_output = root / "alpha1-completion-output.txt"
            completion_output.write_text("Alpha1 completion audit passed\n", encoding="utf-8")
            completion_summary = root / "alpha1-completion-summary.json"
            self.write_good_completion_summary(completion_summary)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "release_acceptable": True,
                        "base_url": "https://seedance-relay.customer-domain.com",
                        "video_id": "vid",
                        "browser_required": True,
                        "commands": [
                            {"label": "deploy_preflight", "command": ["python", "deploy/alpha1_preflight.py"], "output": str(preflight), "returncode": 0},
                            {"label": "caddy_validate", "command": ["caddy", "validate"], "output": str(caddy), "returncode": 0},
                            {"label": "external_probe", "command": ["python", "deploy/alpha1_probe.py"], "output": str(probe_output), "returncode": 0},
                            {"command": ["python", "deploy/alpha1_verify_evidence.py"], "output": str(verify), "returncode": 0},
                        ],
                        "artifacts": {
                            "probe": str(probe),
                            "probe_output": str(probe_output),
                            "screenshot": str(screenshot),
                            "preflight": str(preflight),
                            "caddy": str(caddy),
                            "local_acceptance": str(local_acceptance),
                            "local_gate_manifest": str(local_gate_manifest),
                            "verify": str(verify),
                            "completion_output": str(completion_output),
                            "completion_summary": str(completion_summary),
                        },
                    }
                ),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("collector manifest missing command label: evidence_verify", proc.stdout)

    def test_verifier_rejects_collector_manifest_without_completion_audit_label(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            local_gate_manifest = self.write_good_local_gate_manifest(root)
            probe_output = root / "alpha1-probe-output.txt"
            probe_output.write_text("Probe passed: 0 warning(s)\n", encoding="utf-8")
            verify = root / "alpha1-verify-output.txt"
            verify.write_text("Evidence verification passed: 0 warning(s)\n", encoding="utf-8")
            completion_output = root / "alpha1-completion-output.txt"
            completion_output.write_text("Alpha1 completion audit passed\n", encoding="utf-8")
            completion_summary = root / "alpha1-completion-summary.json"
            self.write_good_completion_summary(completion_summary)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "release_acceptable": True,
                        "base_url": "https://seedance-relay.customer-domain.com",
                        "video_id": "vid",
                        "browser_required": True,
                        "commands": [
                            {"label": "deploy_preflight", "command": ["python", "deploy/alpha1_preflight.py"], "output": str(preflight), "returncode": 0},
                            {"label": "caddy_validate", "command": ["caddy", "validate"], "output": str(caddy), "returncode": 0},
                            {"label": "external_probe", "command": ["python", "deploy/alpha1_probe.py"], "output": str(probe_output), "returncode": 0},
                            {"label": "evidence_verify", "command": ["python", "deploy/alpha1_verify_evidence.py"], "output": str(verify), "returncode": 0},
                        ],
                        "artifacts": {
                            "probe": str(probe),
                            "probe_output": str(probe_output),
                            "screenshot": str(screenshot),
                            "preflight": str(preflight),
                            "caddy": str(caddy),
                            "local_acceptance": str(local_acceptance),
                            "local_gate_manifest": str(local_gate_manifest),
                            "verify": str(verify),
                            "completion_output": str(completion_output),
                            "completion_summary": str(completion_summary),
                        },
                    }
                ),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("collector manifest missing command label: completion_audit", proc.stdout)

    def test_verifier_rejects_failed_completion_audit_output(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            local_gate_manifest = self.write_good_local_gate_manifest(root)
            probe_output = root / "alpha1-probe-output.txt"
            probe_output.write_text("Probe passed: 0 warning(s)\n", encoding="utf-8")
            verify = root / "alpha1-verify-output.txt"
            verify.write_text("Evidence verification passed: 0 warning(s)\n", encoding="utf-8")
            completion_output = root / "alpha1-completion-output.txt"
            completion_output.write_text("Alpha1 completion audit failed\n", encoding="utf-8")
            completion_summary = root / "alpha1-completion-summary.json"
            completion_summary.write_text('{"release_complete":false}\n', encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "release_acceptable": True,
                        "base_url": "https://seedance-relay.customer-domain.com",
                        "video_id": "vid",
                        "browser_required": True,
                        "commands": [
                            {"label": "deploy_preflight", "command": ["python", "deploy/alpha1_preflight.py"], "output": str(preflight), "returncode": 0},
                            {"label": "caddy_validate", "command": ["caddy", "validate"], "output": str(caddy), "returncode": 0},
                            {"label": "external_probe", "command": ["python", "deploy/alpha1_probe.py"], "output": str(probe_output), "returncode": 0},
                            {"label": "evidence_verify", "command": ["python", "deploy/alpha1_verify_evidence.py"], "output": str(verify), "returncode": 0},
                            {"label": "completion_audit", "command": ["python", "deploy/alpha1_completion_audit.py", "--manifest", str(manifest), "--summary-json", str(completion_summary)], "output": str(completion_output), "returncode": 0},
                        ],
                        "artifacts": {
                            "probe": str(probe),
                            "probe_output": str(probe_output),
                            "screenshot": str(screenshot),
                            "preflight": str(preflight),
                            "caddy": str(caddy),
                            "local_acceptance": str(local_acceptance),
                            "local_gate_manifest": str(local_gate_manifest),
                            "verify": str(verify),
                            "completion_output": str(completion_output),
                            "completion_summary": str(completion_summary),
                        },
                    }
                ),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("completion audit output does not show 'Alpha1 completion audit passed'", proc.stdout)

    def test_verifier_rejects_manifest_without_local_gate_manifest(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            probe_output = root / "alpha1-probe-output.txt"
            probe_output.write_text("Probe passed: 0 warning(s)\n", encoding="utf-8")
            verify = root / "alpha1-verify-output.txt"
            verify.write_text("Evidence verification passed: 0 warning(s)\n", encoding="utf-8")
            completion_output = root / "alpha1-completion-output.txt"
            completion_output.write_text("Alpha1 completion audit passed\n", encoding="utf-8")
            completion_summary = root / "alpha1-completion-summary.json"
            self.write_good_completion_summary(completion_summary)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "release_acceptable": True,
                        "base_url": "https://seedance-relay.customer-domain.com",
                        "video_id": "vid",
                        "browser_required": True,
                        "commands": [
                            {"command": ["python", "deploy/alpha1_preflight.py"], "output": str(preflight), "returncode": 0},
                            {"command": ["caddy", "validate"], "output": str(caddy), "returncode": 0},
                            {"command": ["python", "deploy/alpha1_probe.py"], "output": str(probe_output), "returncode": 0},
                            {"command": ["python", "deploy/alpha1_verify_evidence.py"], "output": str(verify), "returncode": 0},
                        ],
                        "artifacts": {
                            "probe": str(probe),
                            "probe_output": str(probe_output),
                            "screenshot": str(screenshot),
                            "preflight": str(preflight),
                            "caddy": str(caddy),
                            "local_acceptance": str(local_acceptance),
                            "verify": str(verify),
                        },
                    }
                ),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("manifest missing artifact path: local_gate_manifest", proc.stdout)

    def test_verifier_rejects_manifest_with_localhost_base_url(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)
            probe_output = root / "alpha1-probe-output.txt"
            probe_output.write_text("Probe passed: 0 warning(s)\n", encoding="utf-8")
            verify = root / "alpha1-verify-output.txt"
            verify.write_text("Evidence verification passed: 0 warning(s)\n", encoding="utf-8")
            completion_output = root / "alpha1-completion-output.txt"
            completion_output.write_text("Alpha1 completion audit passed\n", encoding="utf-8")
            completion_summary = root / "alpha1-completion-summary.json"
            self.write_good_completion_summary(completion_summary)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "release_acceptable": True,
                        "base_url": "http://localhost:8002",
                        "video_id": "vid",
                        "browser_required": True,
                        "commands": [
                            {"command": ["python", "deploy/alpha1_preflight.py"], "output": str(preflight), "returncode": 0},
                            {"command": ["caddy", "validate"], "output": str(caddy), "returncode": 0},
                            {"command": ["python", "deploy/alpha1_probe.py"], "output": str(probe_output), "returncode": 0},
                            {"command": ["python", "deploy/alpha1_verify_evidence.py"], "output": str(verify), "returncode": 0},
                        ],
                        "artifacts": {
                            "probe": str(probe),
                            "probe_output": str(probe_output),
                            "screenshot": str(screenshot),
                            "preflight": str(preflight),
                            "caddy": str(caddy),
                            "local_acceptance": str(local_acceptance),
                            "local_gate_manifest": str(local_gate_manifest),
                            "verify": str(verify),
                        },
                    }
                ),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("manifest base_url must use https", proc.stdout)

            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["base_url"] = "https://alpha1-demo.ngrok-free.app"
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            tunnel = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(tunnel.returncode, 0, tunnel.stdout)
            self.assertIn("manifest base_url must be a stable Relay domain, not a tunnel URL", tunnel.stdout)

            payload["base_url"] = "https://seedance-relay.example.com"
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            example_domain = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(example_domain.returncode, 0, example_domain.stdout)
            self.assertIn("manifest base_url must be a real Relay domain, not an example domain", example_domain.stdout)

            payload["base_url"] = "https://seedance-relay.customer-domain.com/app?debug=1"
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            path_base_url = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(path_base_url.returncode, 0, path_base_url.stdout)
            self.assertIn("manifest base_url must be a bare origin", path_base_url.stdout)

            payload["base_url"] = "https://operator:secret@seedance-relay.customer-domain.com"
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            userinfo_base_url = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(userinfo_base_url.returncode, 0, userinfo_base_url.stdout)
            self.assertIn("manifest base_url must not include username or password", userinfo_base_url.stdout)

    def test_verifier_rejects_manifest_without_probe_output(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            verify = root / "alpha1-verify-output.txt"
            verify.write_text("Evidence verification passed: 0 warning(s)\n", encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "release_acceptable": True,
                        "base_url": "https://seedance-relay.customer-domain.com",
                        "video_id": "vid",
                        "browser_required": True,
                        "commands": [
                            {"command": ["python", "deploy/alpha1_preflight.py"], "output": str(preflight), "returncode": 0},
                            {"command": ["caddy", "validate"], "output": str(caddy), "returncode": 0},
                            {"command": ["python", "deploy/alpha1_probe.py"], "output": str(root / "alpha1-probe-output.txt"), "returncode": 0},
                            {"command": ["python", "deploy/alpha1_verify_evidence.py"], "output": str(verify), "returncode": 0},
                        ],
                        "artifacts": {
                            "probe": str(probe),
                            "screenshot": str(screenshot),
                            "preflight": str(preflight),
                            "caddy": str(caddy),
                            "local_acceptance": str(local_acceptance),
                            "verify": str(verify),
                        },
                    }
                ),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("manifest missing artifact path: probe_output", proc.stdout)

    def test_verifier_rejects_manifest_with_failed_probe_output(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)
            probe_output = root / "alpha1-probe-output.txt"
            probe_output.write_text("Probe failed: 1 failure(s), 0 warning(s)\n", encoding="utf-8")
            verify = root / "alpha1-verify-output.txt"
            verify.write_text("Evidence verification passed: 0 warning(s)\n", encoding="utf-8")
            completion_output = root / "alpha1-completion-output.txt"
            completion_output.write_text("Alpha1 completion audit passed\n", encoding="utf-8")
            completion_summary = root / "alpha1-completion-summary.json"
            self.write_good_completion_summary(completion_summary)
            manifest = self.write_collector_manifest(
                root,
                probe=probe,
                probe_output=probe_output,
                screenshot=screenshot,
                preflight=preflight,
                caddy=caddy,
                local_acceptance=local_acceptance,
                local_gate_manifest=local_gate_manifest,
                verify=verify,
                completion_output=completion_output,
                completion_summary=completion_summary,
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("probe output does not show", proc.stdout)

    def test_verifier_rejects_manifest_when_artifact_is_not_command_output(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)
            probe_output = root / "alpha1-probe-output.txt"
            probe_output.write_text("Probe passed: 0 warning(s)\n", encoding="utf-8")
            other_probe_output = root / "other-probe-output.txt"
            other_probe_output.write_text("Probe passed: 0 warning(s)\n", encoding="utf-8")
            verify = root / "alpha1-verify-output.txt"
            verify.write_text("Evidence verification passed: 0 warning(s)\n", encoding="utf-8")
            completion_output = root / "alpha1-completion-output.txt"
            completion_output.write_text("Alpha1 completion audit passed\n", encoding="utf-8")
            completion_summary = root / "alpha1-completion-summary.json"
            self.write_good_completion_summary(completion_summary)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "release_acceptable": True,
                        "base_url": "https://seedance-relay.customer-domain.com",
                        "video_id": "vid",
                        "browser_required": True,
                        "commands": [
                            {"label": "deploy_preflight", "command": ["python", "deploy/alpha1_preflight.py"], "output": str(preflight), "returncode": 0},
                            {"label": "caddy_validate", "command": ["caddy", "validate"], "output": str(caddy), "returncode": 0},
                            {"label": "external_probe", "command": ["python", "deploy/alpha1_probe.py"], "output": str(other_probe_output), "returncode": 0},
                            {"label": "evidence_verify", "command": ["python", "deploy/alpha1_verify_evidence.py"], "output": str(verify), "returncode": 0},
                            {"label": "completion_audit", "command": ["python", "deploy/alpha1_completion_audit.py", "--manifest", str(manifest), "--summary-json", str(completion_summary)], "output": str(completion_output), "returncode": 0},
                        ],
                        "artifacts": {
                            "probe": str(probe),
                            "probe_output": str(probe_output),
                            "screenshot": str(screenshot),
                            "preflight": str(preflight),
                            "caddy": str(caddy),
                            "local_acceptance": str(local_acceptance),
                            "local_gate_manifest": str(local_gate_manifest),
                            "verify": str(verify),
                            "completion_output": str(completion_output),
                            "completion_summary": str(completion_summary),
                        },
                    }
                ),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("manifest artifact 'probe_output' is not recorded as a command output", proc.stdout)

    def test_verifier_rejects_completion_summary_that_is_not_release_complete(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)
            probe_output = root / "alpha1-probe-output.txt"
            probe_output.write_text("Probe passed: 0 warning(s)\n", encoding="utf-8")
            verify = root / "alpha1-verify-output.txt"
            verify.write_text("Evidence verification passed: 0 warning(s)\n", encoding="utf-8")
            completion_output = root / "alpha1-completion-output.txt"
            completion_output.write_text("Alpha1 completion audit passed\n", encoding="utf-8")
            completion_summary = root / "alpha1-completion-summary.json"
            completion_summary.write_text('{"release_complete":false}\n', encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "release_acceptable": True,
                        "base_url": "https://seedance-relay.customer-domain.com",
                        "video_id": "vid",
                        "browser_required": True,
                        "commands": [
                            {"label": "deploy_preflight", "command": ["python", "deploy/alpha1_preflight.py"], "output": str(preflight), "returncode": 0},
                            {"label": "caddy_validate", "command": ["caddy", "validate"], "output": str(caddy), "returncode": 0},
                            {"label": "external_probe", "command": ["python", "deploy/alpha1_probe.py"], "output": str(probe_output), "returncode": 0},
                            {"label": "evidence_verify", "command": ["python", "deploy/alpha1_verify_evidence.py"], "output": str(verify), "returncode": 0},
                            {"label": "completion_audit", "command": ["python", "deploy/alpha1_completion_audit.py", "--manifest", str(manifest), "--summary-json", str(completion_summary)], "output": str(completion_output), "returncode": 0},
                        ],
                        "artifacts": {
                            "probe": str(probe),
                            "probe_output": str(probe_output),
                            "screenshot": str(screenshot),
                            "preflight": str(preflight),
                            "caddy": str(caddy),
                            "local_acceptance": str(local_acceptance),
                            "local_gate_manifest": str(local_gate_manifest),
                            "verify": str(verify),
                            "completion_output": str(completion_output),
                            "completion_summary": str(completion_summary),
                        },
                    }
                ),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("completion audit summary release_complete is not true", proc.stdout)

    def test_verifier_rejects_manifest_without_verify_output(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "release_acceptable": True,
                        "base_url": "https://seedance-relay.customer-domain.com",
                        "video_id": "vid",
                        "browser_required": True,
                        "commands": [
                            {"command": ["python", "deploy/alpha1_preflight.py"], "output": str(preflight), "returncode": 0},
                            {"command": ["caddy", "validate"], "output": str(caddy), "returncode": 0},
                            {"command": ["python", "deploy/alpha1_probe.py"], "output": str(root / "alpha1-probe-output.txt"), "returncode": 0},
                            {"command": ["python", "deploy/alpha1_verify_evidence.py"], "output": str(root / "alpha1-verify-output.txt"), "returncode": 0},
                        ],
                        "artifacts": {
                            "probe": str(probe),
                            "screenshot": str(screenshot),
                            "preflight": str(preflight),
                            "caddy": str(caddy),
                            "local_acceptance": str(local_acceptance),
                        },
                    }
                ),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("manifest missing artifact path: verify", proc.stdout)

    def test_verifier_rejects_manifest_with_failed_verify_output(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)
            probe_output = root / "alpha1-probe-output.txt"
            probe_output.write_text("Probe passed: 0 warning(s)\n", encoding="utf-8")
            verify = root / "alpha1-verify-output.txt"
            verify.write_text("Evidence verification failed: 1 failure(s), 0 warning(s)\n", encoding="utf-8")
            completion_output = root / "alpha1-completion-output.txt"
            completion_output.write_text("Alpha1 completion audit passed\n", encoding="utf-8")
            completion_summary = root / "alpha1-completion-summary.json"
            self.write_good_completion_summary(completion_summary)
            manifest = self.write_collector_manifest(
                root,
                probe=probe,
                probe_output=probe_output,
                screenshot=screenshot,
                preflight=preflight,
                caddy=caddy,
                local_acceptance=local_acceptance,
                local_gate_manifest=local_gate_manifest,
                verify=verify,
                completion_output=completion_output,
                completion_summary=completion_summary,
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("evidence verifier output does not show", proc.stdout)

    def test_verifier_rejects_manifest_without_command_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "release_acceptable": True,
                        "base_url": "https://seedance-relay.customer-domain.com",
                        "video_id": "vid",
                        "browser_required": True,
                        "artifacts": {
                            "probe": str(probe),
                            "screenshot": str(screenshot),
                            "preflight": str(preflight),
                            "caddy": str(caddy),
                            "local_acceptance": str(local_acceptance),
                        },
                    }
                ),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("manifest has no commands list", proc.stdout)

    def test_verifier_rejects_manifest_without_local_acceptance(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "release_acceptable": True,
                        "base_url": "https://seedance-relay.customer-domain.com",
                        "video_id": "vid",
                        "browser_required": True,
                        "artifacts": {
                            "probe": str(probe),
                            "screenshot": str(screenshot),
                            "preflight": str(preflight),
                            "caddy": str(caddy),
                        },
                    }
                ),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("manifest missing artifact path: local_acceptance", proc.stdout)

    def test_verifier_rejects_incomplete_local_acceptance(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"]["old_api_key_failed"] = False
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--base-url",
                    "https://seedance-relay.customer-domain.com",
                    "--video-id",
                    "vid",
                    "--probe-json",
                    str(probe),
                    "--screenshot",
                    str(screenshot),
                    "--preflight-output",
                    str(preflight),
                    "--caddy-output",
                    str(caddy),
                    "--local-acceptance-json",
                    str(local_acceptance),
                    "--local-gate-manifest",
                    str(local_gate_manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("local acceptance check 'old_api_key_failed' is not true", proc.stdout)

    def test_verifier_requires_local_browser_network_acceptance(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("browser_network_uses_relay_video_url")
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--base-url",
                    "https://seedance-relay.customer-domain.com",
                    "--video-id",
                    "vid",
                    "--probe-json",
                    str(probe),
                    "--screenshot",
                    str(screenshot),
                    "--preflight-output",
                    str(preflight),
                    "--caddy-output",
                    str(caddy),
                    "--local-acceptance-json",
                    str(local_acceptance),
                    "--local-gate-manifest",
                    str(local_gate_manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'browser_network_uses_relay_video_url' is not true",
                proc.stdout,
            )

    def test_verifier_requires_process_log_secret_and_upstream_url_acceptance(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("process_logs_hide_upstream_urls_and_secrets", None)
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'process_logs_hide_upstream_urls_and_secrets' is not true",
                proc.stdout,
            )

    def test_verifier_requires_password_login_and_session_revocation_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("customer_old_password_rejected")
            payload["checks"].pop("customer_new_password_login_works")
            payload["checks"].pop("stale_session_revoked_after_password_change")
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("local acceptance check 'customer_old_password_rejected' is not true", proc.stdout)
            self.assertIn("local acceptance check 'customer_new_password_login_works' is not true", proc.stdout)
            self.assertIn("local acceptance check 'stale_session_revoked_after_password_change' is not true", proc.stdout)

    def test_verifier_requires_cookie_password_change_csrf_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("cookie_password_change_rejects_cross_site_origin", None)
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'cookie_password_change_rejects_cross_site_origin' is not true",
                proc.stdout,
            )

    def test_verifier_requires_cookie_password_change_session_revocation_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("cookie_password_change_revokes_other_sessions", None)
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'cookie_password_change_revokes_other_sessions' is not true",
                proc.stdout,
            )

    def test_verifier_requires_password_reuse_rejection_acceptance_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("customer_password_reuse_rejected", None)
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'customer_password_reuse_rejected' is not true",
                proc.stdout,
            )

    def test_verifier_requires_admin_balance_status_and_upstream_key_audit_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["audit_actions"] = [
                action
                for action in payload["audit_actions"]
                if action
                not in {
                    "admin_changed_balance",
                    "admin_changed_status",
                    "admin_changed_upstream_key",
                }
            ]
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("local acceptance audit_actions missing 'admin_changed_balance'", proc.stdout)
            self.assertIn("local acceptance audit_actions missing 'admin_changed_status'", proc.stdout)
            self.assertIn("local acceptance audit_actions missing 'admin_changed_upstream_key'", proc.stdout)

    def test_verifier_requires_admin_password_reset_and_key_rotation_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            for key in (
                "admin_reset_customer_password",
                "admin_reset_password_revoked_customer_session",
                "admin_rotated_customer_relay_key",
                "old_customer_key_failed_after_admin_rotation",
                "admin_api_key_rotation_revoked_customer_session",
                "admin_rotated_customer_key_works",
            ):
                payload["checks"].pop(key, None)
            payload["audit_actions"] = [
                action
                for action in payload["audit_actions"]
                if action
                not in {
                    "admin_reset_password",
                    "admin_rotated_customer_api_key",
                }
            ]
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("local acceptance check 'admin_reset_customer_password' is not true", proc.stdout)
            self.assertIn("local acceptance check 'admin_reset_password_revoked_customer_session' is not true", proc.stdout)
            self.assertIn("local acceptance check 'admin_rotated_customer_relay_key' is not true", proc.stdout)
            self.assertIn("local acceptance check 'old_customer_key_failed_after_admin_rotation' is not true", proc.stdout)
            self.assertIn("local acceptance check 'admin_api_key_rotation_revoked_customer_session' is not true", proc.stdout)
            self.assertIn("local acceptance check 'admin_rotated_customer_key_works' is not true", proc.stdout)
            self.assertIn("local acceptance audit_actions missing 'admin_reset_password'", proc.stdout)
            self.assertIn("local acceptance audit_actions missing 'admin_rotated_customer_api_key'", proc.stdout)

    def test_verifier_requires_no_relay_content_censorship_acceptance_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("prompt_text_passed_without_relay_censorship", None)
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'prompt_text_passed_without_relay_censorship' is not true",
                proc.stdout,
            )

    def test_verifier_requires_admin_model_list_default_and_empty_distinction_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("admin_model_list_default_and_empty_are_distinct", None)
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'admin_model_list_default_and_empty_are_distinct' is not true",
                proc.stdout,
            )

    def test_verifier_requires_model_alias_opt_in_acceptance_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("model_aliases_are_opt_in_per_customer", None)
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'model_aliases_are_opt_in_per_customer' is not true",
                proc.stdout,
            )

    def test_verifier_requires_admin_model_options_acceptance_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("admin_model_options_include_native_and_alias_choices", None)
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'admin_model_options_include_native_and_alias_choices' is not true",
                proc.stdout,
            )

    def test_verifier_requires_no_sfw_nsfw_customer_toggle_acceptance_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("admin_surfaces_have_no_sfw_nsfw_customer_toggle", None)
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'admin_surfaces_have_no_sfw_nsfw_customer_toggle' is not true",
                proc.stdout,
            )

    def test_verifier_requires_customer_account_key_masking_acceptance_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("customer_account_surfaces_mask_relay_api_key", None)
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'customer_account_surfaces_mask_relay_api_key' is not true",
                proc.stdout,
            )

    def test_verifier_requires_server_generated_relay_key_acceptance_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("relay_api_keys_are_server_generated", None)
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'relay_api_keys_are_server_generated' is not true",
                proc.stdout,
            )

    def test_verifier_requires_customer_surfaces_keep_operator_nsfw_notes_private(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("customer_surfaces_keep_operator_nsfw_notes_private", None)
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'customer_surfaces_keep_operator_nsfw_notes_private' is not true",
                proc.stdout,
            )

    def test_verifier_requires_admin_credential_masking_acceptance_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("admin_user_list_masks_customer_and_upstream_keys", None)
            payload["checks"].pop("admin_user_detail_masks_customer_and_upstream_keys", None)
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'admin_user_list_masks_customer_and_upstream_keys' is not true",
                proc.stdout,
            )
            self.assertIn(
                "local acceptance check 'admin_user_detail_masks_customer_and_upstream_keys' is not true",
                proc.stdout,
            )

    def test_verifier_requires_cross_customer_task_isolation_acceptance_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("task_list_excludes_other_customers_tasks", None)
            payload["checks"].pop("video_content_rejects_other_customer_before_upstream", None)
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'task_list_excludes_other_customers_tasks' is not true",
                proc.stdout,
            )
            self.assertIn(
                "local acceptance check 'video_content_rejects_other_customer_before_upstream' is not true",
                proc.stdout,
            )

    def test_verifier_requires_task_list_status_limit_offset_acceptance_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("task_list_status_limit_offset_works", None)
            payload["runtime_paths"] = [
                item
                for item in payload["runtime_paths"]
                if item["path"] != "/v1/videos?status=succeeded&limit=1&offset=1"
            ]
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'task_list_status_limit_offset_works' is not true",
                proc.stdout,
            )
            self.assertIn(
                "local acceptance runtime_paths missing GET ^/v1/videos\\?status=succeeded&limit=1&offset=1$",
                proc.stdout,
            )

    def test_verifier_requires_head_content_proxy_acceptance_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("head_content_uses_relay_without_redirect", None)
            payload["runtime_paths"] = [
                item
                for item in payload["runtime_paths"]
                if not (item["method"] == "HEAD" and item["path"] == "/v1/videos/vid/content")
            ]
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'head_content_uses_relay_without_redirect' is not true",
                proc.stdout,
            )
            self.assertIn(
                "local acceptance runtime_paths missing HEAD ^/v1/videos/[^/?]+/content$",
                proc.stdout,
            )

    def test_verifier_requires_runtime_estimate_acceptance_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("runtime_estimate_uses_customer_multiplier", None)
            payload["runtime_paths"] = [
                item
                for item in payload["runtime_paths"]
                if not (item["method"] == "POST" and item["path"] == "/v1/videos/estimate")
            ]
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'runtime_estimate_uses_customer_multiplier' is not true",
                proc.stdout,
            )
            self.assertIn("local acceptance runtime_paths missing POST /v1/videos/estimate", proc.stdout)

    def test_verifier_requires_runtime_cookie_session_and_csrf_acceptance_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("runtime_cookie_estimate_accepts_session", None)
            payload["checks"].pop("runtime_cookie_create_rejects_cross_site_before_upstream", None)
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'runtime_cookie_estimate_accepts_session' is not true",
                proc.stdout,
            )
            self.assertIn(
                "local acceptance check 'runtime_cookie_create_rejects_cross_site_before_upstream' is not true",
                proc.stdout,
            )

    def test_verifier_requires_runtime_prepare_helper_asset_guard_acceptance_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("runtime_prepare_helper_rejects_unowned_asset_before_upstream", None)
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'runtime_prepare_helper_rejects_unowned_asset_before_upstream' is not true",
                proc.stdout,
            )

    def test_verifier_requires_prepare_failure_refund_acceptance_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("runtime_prepare_failure_refunds_reserved_balance", None)
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'runtime_prepare_failure_refunds_reserved_balance' is not true",
                proc.stdout,
            )

    def test_verifier_requires_upstream_error_refund_acceptance_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("runtime_upstream_error_refunds_reserved_balance", None)
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'runtime_upstream_error_refunds_reserved_balance' is not true",
                proc.stdout,
            )

    def test_verifier_requires_local_task_write_failure_cancel_and_refund_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("runtime_local_task_write_failure_cancels_upstream_and_refunds", None)
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'runtime_local_task_write_failure_cancels_upstream_and_refunds' is not true",
                proc.stdout,
            )

    def test_verifier_requires_terminal_refresh_settlement_acceptance_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("runtime_terminal_refresh_settles_once_and_refunds_hold", None)
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'runtime_terminal_refresh_settles_once_and_refunds_hold' is not true",
                proc.stdout,
            )

    def test_verifier_requires_multiplier_snapshot_settlement_acceptance_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("runtime_settlement_uses_task_multiplier_snapshot", None)
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'runtime_settlement_uses_task_multiplier_snapshot' is not true",
                proc.stdout,
            )

    def test_verifier_requires_failed_terminal_refresh_refund_acceptance_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("runtime_failed_terminal_refresh_refunds_hold_once", None)
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'runtime_failed_terminal_refresh_refunds_hold_once' is not true",
                proc.stdout,
            )

    def test_verifier_requires_fastapi_fallback_upstream_error_refund_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("fastapi_fallback_upstream_error_refunds_reserved_balance", None)
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'fastapi_fallback_upstream_error_refunds_reserved_balance' is not true",
                proc.stdout,
            )

    def test_verifier_requires_fastapi_fallback_local_write_failure_cancel_and_refund_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("fastapi_fallback_local_task_write_failure_cancels_upstream_and_refunds", None)
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'fastapi_fallback_local_task_write_failure_cancels_upstream_and_refunds' is not true",
                proc.stdout,
            )

    def test_verifier_requires_insufficient_balance_before_prepare_acceptance_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            payload["checks"].pop("runtime_create_rejects_insufficient_balance_before_prepare", None)
            local_acceptance.write_text(json.dumps(payload), encoding="utf-8")
            local_gate_manifest = self.write_good_local_gate_manifest(root, local_acceptance)

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance, local_gate_manifest)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "local acceptance check 'runtime_create_rejects_insufficient_balance_before_prepare' is not true",
                proc.stdout,
            )

    def test_verifier_rejects_manifest_that_is_not_release_acceptable(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "status": "dry_run",
                        "release_acceptable": False,
                        "browser_required": False,
                        "artifacts": {
                            "probe": str(probe),
                            "screenshot": str(screenshot),
                            "preflight": str(preflight),
                            "caddy": str(caddy),
                        },
                    }
                ),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("manifest is not release acceptable", proc.stdout)
            self.assertIn("manifest status is 'dry_run'", proc.stdout)

    def test_verifier_rejects_manifest_with_failed_command_returncode(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "release_acceptable": True,
                        "base_url": "https://seedance-relay.customer-domain.com",
                        "video_id": "vid",
                        "browser_required": True,
                        "commands": [
                            {
                                "command": ["python", "deploy/alpha1_probe.py"],
                                "output": str(root / "alpha1-probe-output.txt"),
                                "returncode": 1,
                            }
                        ],
                        "artifacts": {
                            "probe": str(probe),
                            "screenshot": str(screenshot),
                            "preflight": str(preflight),
                            "caddy": str(caddy),
                            "local_acceptance": str(local_acceptance),
                        },
                    }
                ),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("manifest contains failed command return codes", proc.stdout)

    def test_verifier_rejects_manifest_with_missing_command_returncode(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "release_acceptable": True,
                        "base_url": "https://seedance-relay.customer-domain.com",
                        "video_id": "vid",
                        "browser_required": True,
                        "commands": [
                            {
                                "command": ["python", "deploy/alpha1_probe.py"],
                                "output": str(root / "alpha1-probe-output.txt"),
                            }
                        ],
                        "artifacts": {
                            "probe": str(probe),
                            "screenshot": str(screenshot),
                            "preflight": str(preflight),
                            "caddy": str(caddy),
                            "local_acceptance": str(local_acceptance),
                        },
                    }
                ),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("manifest contains command entries without return codes", proc.stdout)

    def test_verifier_rejects_manifest_with_dry_run_or_skipped_commands(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            probe, screenshot, preflight, caddy = self.write_good_artifacts(root)
            local_acceptance = self.write_good_local_acceptance(root)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "release_acceptable": True,
                        "base_url": "https://seedance-relay.customer-domain.com",
                        "video_id": "vid",
                        "browser_required": True,
                        "commands": [
                            {
                                "command": ["python", "deploy/alpha1_preflight.py"],
                                "output": str(preflight),
                                "returncode": 0,
                                "dry_run": True,
                            },
                            {
                                "command": ["python", "deploy/alpha1_verify_evidence.py"],
                                "output": str(root / "alpha1-verify-output.txt"),
                                "skipped": True,
                            },
                        ],
                        "artifacts": {
                            "probe": str(probe),
                            "screenshot": str(screenshot),
                            "preflight": str(preflight),
                            "caddy": str(caddy),
                            "local_acceptance": str(local_acceptance),
                        },
                    }
                ),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--manifest",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("manifest contains dry-run command evidence", proc.stdout)
            self.assertIn("manifest contains skipped command evidence", proc.stdout)

    def test_verifier_rejects_probe_failures_and_sensitive_markers(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            probe, screenshot, preflight, caddy = self.write_good_artifacts(Path(tmp))
            payload = json.loads(probe.read_text(encoding="utf-8"))
            payload["failures"] = ["models body leaked sensitive marker"]
            payload["evidence"]["browser.request_urls"].append("https://byteplus.example.test/private.mp4")
            payload["evidence"]["browser.responses"].append(
                {
                    "url": "https://seedance-relay.customer-domain.com/v1/videos/vid/content?key=sk-leaked-relay-key-12345",
                    "status": 200,
                    "content_type": "video/mp4",
                    "location_present": False,
                }
            )
            probe.write_text(json.dumps(payload), encoding="utf-8")
            local_acceptance = self.write_good_local_acceptance(Path(tmp))
            local_payload = json.loads(local_acceptance.read_text(encoding="utf-8"))
            local_payload["warnings"].append("debug cookie relay_session=secret-session-token")
            local_acceptance.write_text(json.dumps(local_payload), encoding="utf-8")

            proc = self.run_verifier(probe, screenshot, preflight, caddy, local_acceptance)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("probe evidence contains failures", proc.stdout)
            self.assertIn("probe evidence leaked sensitive marker", proc.stdout)
            self.assertIn("probe evidence leaked sensitive token: Relay API key", proc.stdout)
            self.assertIn("local acceptance evidence leaked sensitive token: relay_session cookie", proc.stdout)

    def test_verifier_rejects_browser_evidence_without_expected_relay_content_url(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            probe, screenshot, preflight, caddy = self.write_good_artifacts(Path(tmp))
            payload = json.loads(probe.read_text(encoding="utf-8"))
            payload["evidence"]["browser.request_urls"] = ["https://seedance-relay.customer-domain.com/favicon.ico"]
            payload["evidence"]["browser.responses"] = [
                {
                    "url": "https://seedance-relay.customer-domain.com/favicon.ico",
                    "status": 200,
                    "location_present": False,
                }
            ]
            probe.write_text(json.dumps(payload), encoding="utf-8")

            proc = self.run_verifier(probe, screenshot, preflight, caddy)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("browser request URLs do not include expected Relay content URL", proc.stdout)
            self.assertIn("browser responses do not include expected Relay content URL", proc.stdout)

    def test_verifier_rejects_browser_expected_response_error_page(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            probe, screenshot, preflight, caddy = self.write_good_artifacts(Path(tmp))
            payload = json.loads(probe.read_text(encoding="utf-8"))
            payload["evidence"]["browser.responses"][0]["status"] = 404
            payload["evidence"]["browser.responses"][0]["content_type"] = "text/html"
            payload["evidence"]["browser.responses"][0].pop("content_range")
            probe.write_text(json.dumps(payload), encoding="utf-8")

            proc = self.run_verifier(probe, screenshot, preflight, caddy)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("browser expected Relay content response has invalid status", proc.stdout)
            self.assertIn("browser expected Relay content response is not video content", proc.stdout)

    def test_verifier_rejects_browser_expected_partial_response_without_content_range(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            probe, screenshot, preflight, caddy = self.write_good_artifacts(Path(tmp))
            payload = json.loads(probe.read_text(encoding="utf-8"))
            payload["evidence"]["browser.responses"][0].pop("content_range")
            probe.write_text(json.dumps(payload), encoding="utf-8")

            proc = self.run_verifier(probe, screenshot, preflight, caddy)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("browser expected Relay 206 response is missing Content-Range", proc.stdout)

    def test_verifier_requires_explicit_range_206_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            probe, screenshot, preflight, caddy = self.write_good_artifacts(Path(tmp))
            payload = json.loads(probe.read_text(encoding="utf-8"))
            payload["evidence"]["content.range_status"] = 200
            payload["evidence"]["browser.responses"][0]["status"] = 200
            payload["evidence"]["browser.responses"][0].pop("content_range")
            probe.write_text(json.dumps(payload), encoding="utf-8")

            proc = self.run_verifier(probe, screenshot, preflight, caddy)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("probe evidence content.range_status = 200, expected 206", proc.stdout)

    def test_verifier_requires_saved_content_proxy_header_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            probe, screenshot, preflight, caddy = self.write_good_artifacts(Path(tmp))
            payload = json.loads(probe.read_text(encoding="utf-8"))
            payload["evidence"]["content.head_location_present"] = True
            payload["evidence"]["content.range_content_range_present"] = False
            payload["evidence"].pop("content.range_location_present")
            probe.write_text(json.dumps(payload), encoding="utf-8")

            proc = self.run_verifier(probe, screenshot, preflight, caddy)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(
                "probe evidence 'content.head_location_present' = True, expected False",
                proc.stdout,
            )
            self.assertIn(
                "probe evidence 'content.range_content_range_present' = False, expected True",
                proc.stdout,
            )
            self.assertIn(
                "probe evidence 'content.range_location_present' = None, expected False",
                proc.stdout,
            )

    def test_verifier_requires_browser_and_caddy_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            probe, screenshot, preflight, caddy = self.write_good_artifacts(Path(tmp))
            payload = json.loads(probe.read_text(encoding="utf-8"))
            payload["evidence"].pop("browser.request_urls")
            payload["evidence"].pop("browser.expected_content_url")
            payload["evidence"].pop("browser.screenshot")
            probe.write_text(json.dumps(payload), encoding="utf-8")
            screenshot.unlink()
            caddy.write_text("syntax error\n", encoding="utf-8")

            proc = self.run_verifier(probe, screenshot, preflight, caddy)

            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("missing browser.request_urls", proc.stdout)
            self.assertIn("missing browser.expected_content_url", proc.stdout)
            self.assertIn("missing browser.screenshot", proc.stdout)
            self.assertIn("missing screenshot", proc.stdout)
            self.assertIn("does not show a valid configuration", proc.stdout)


if __name__ == "__main__":
    unittest.main()






