import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class Alpha1CompletionAuditTests(unittest.TestCase):
    def write_good_local_acceptance(self, root: Path) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        path = root / "alpha1-local-acceptance.json"
        path.write_text(
            json.dumps(
                {
                    "status": "passed",
                    "checks": {
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
                    },
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

    def write_good_local_gate_manifest(self, root: Path) -> Path:
        local_acceptance = self.write_good_local_acceptance(root)
        commands = []
        for label in (
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
        ):
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

    def write_good_release_manifest(self, root: Path) -> Path:
        local_gate = self.write_good_local_gate_manifest(root)
        local_acceptance = root / "alpha1-local-acceptance.json"
        probe = root / "alpha1-probe-evidence.json"
        probe_output = root / "alpha1-probe-output.txt"
        screenshot = root / "alpha1-browser-video.png"
        preflight = root / "alpha1-preflight-output.txt"
        caddy = root / "caddy-validate-output.txt"
        verify = root / "alpha1-verify-output.txt"
        completion_output = root / "alpha1-completion-output.txt"
        completion_summary = root / "alpha1-completion-summary.json"
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
        probe_output.write_text("Probe passed\n", encoding="utf-8")
        screenshot.write_bytes(b"png")
        preflight.write_text(
            "[OK] caddy validate passed: /etc/caddy/Caddyfile\nPreflight passed: 0 warning(s)\n",
            encoding="utf-8",
        )
        caddy.write_text("Valid configuration\n", encoding="utf-8")
        verify.write_text("Evidence verification passed: 0 warning(s)\n", encoding="utf-8")
        manifest = root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "base_url": "https://seedance-relay.customer-domain.com",
                    "video_id": "vid",
                    "browser_required": True,
                    "release_acceptable": True,
                    "status": "passed",
                    "artifacts": {
                        "probe": str(probe),
                        "probe_output": str(probe_output),
                        "screenshot": str(screenshot),
                        "preflight": str(preflight),
                        "caddy": str(caddy),
                        "local_acceptance": str(local_acceptance),
                        "local_gate_manifest": str(local_gate),
                        "verify": str(verify),
                        "completion_output": str(completion_output),
                        "completion_summary": str(completion_summary),
                    },
                    "commands": [
                        {"label": "deploy_preflight", "command": ["preflight"], "output": str(preflight), "returncode": 0},
                        {"label": "caddy_validate", "command": ["caddy"], "output": str(caddy), "returncode": 0},
                        {"label": "external_probe", "command": ["probe"], "output": str(probe_output), "returncode": 0},
                        {"label": "evidence_verify", "command": ["verify"], "output": str(verify), "returncode": 0},
                        {"label": "completion_audit", "command": ["completion", "--manifest", str(manifest), "--summary-json", str(completion_summary)], "output": str(completion_output)},
                    ],
                }
            ),
            encoding="utf-8",
        )
        return manifest

    def test_audit_rejects_local_gate_without_external_manifest(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            local_gate = self.write_good_local_gate_manifest(root)
            summary = root / "summary.json"

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_completion_audit.py",
                    "--local-gate-manifest",
                    str(local_gate),
                    "--summary-json",
                    str(summary),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertEqual(proc.returncode, 1, proc.stdout)
            self.assertIn("external deployment evidence missing", proc.stdout)
            payload = json.loads(summary.read_text(encoding="utf-8"))
            self.assertFalse(payload["release_complete"])
            self.assertEqual(payload["checks"]["local_gate"]["status"], "passed")
            self.assertEqual(payload["checks"]["external_manifest"]["status"], "missing")

    def test_audit_accepts_verified_external_manifest(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            manifest = self.write_good_release_manifest(root)
            summary = root / "summary.json"

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_completion_audit.py",
                    "--manifest",
                    str(manifest),
                    "--summary-json",
                    str(summary),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("Alpha1 completion audit passed", proc.stdout)
            payload = json.loads(summary.read_text(encoding="utf-8"))
            self.assertTrue(payload["release_complete"])
            self.assertEqual(payload["checks"]["external_manifest"]["status"], "passed")
            self.assertNotIn("local_gate_match", payload)

    def test_audit_rejects_mismatched_local_gate_when_manifest_is_also_provided(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            manifest = self.write_good_release_manifest(root / "release")
            other_local_gate = self.write_good_local_gate_manifest(root / "other-local")
            summary = root / "summary.json"

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_completion_audit.py",
                    "--manifest",
                    str(manifest),
                    "--local-gate-manifest",
                    str(other_local_gate),
                    "--summary-json",
                    str(summary),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertEqual(proc.returncode, 1, proc.stdout)
            self.assertIn("local gate manifest does not match external manifest artifact", proc.stdout)
            payload = json.loads(summary.read_text(encoding="utf-8"))
            self.assertFalse(payload["release_complete"])
            self.assertIn("local_gate_match", payload["checks"])
            self.assertFalse(payload["local_gate_match"])
            self.assertEqual(payload["checks"]["local_gate_match"]["status"], "failed")

    def test_audit_accepts_original_local_gate_when_collector_manifest_points_to_copied_manifest(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            manifest = self.write_good_release_manifest(root / "release")
            original_local_gate = self.write_good_local_gate_manifest(root / "original-local")
            copied_local_gate = root / "release" / "alpha1-local-gate" / "local-gate-manifest.json"
            shutil.copytree(original_local_gate.parent, copied_local_gate.parent, dirs_exist_ok=True)

            manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
            manifest_payload["artifacts"]["local_gate_manifest"] = str(copied_local_gate)
            manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")
            summary = root / "summary.json"

            proc = subprocess.run(
                [
                    sys.executable,
                    "deploy/alpha1_completion_audit.py",
                    "--manifest",
                    str(manifest),
                    "--local-gate-manifest",
                    str(original_local_gate),
                    "--summary-json",
                    str(summary),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stdout)
            payload = json.loads(summary.read_text(encoding="utf-8"))
            self.assertTrue(payload["release_complete"])
            self.assertTrue(payload["local_gate_match"])
            self.assertEqual(payload["checks"]["local_gate_match"]["status"], "passed")


if __name__ == "__main__":
    unittest.main()


