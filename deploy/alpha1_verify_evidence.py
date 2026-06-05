#!/usr/bin/env python3
"""Verify saved alpha1 deployment evidence artifacts.

This verifier is intentionally offline: it reads the files captured on the
deployment host and fails if the release evidence is incomplete or leaks
provider/internal markers.
"""

from __future__ import annotations

import argparse
import struct
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SENSITIVE_MARKERS = (
    "byteplus",
    "bytepluses.com",
    "volces.com",
    "ark-",
)
SENSITIVE_PATTERNS = (
    (re.compile(r"\bsk[-_][A-Za-z0-9][A-Za-z0-9_-]{8,}\b"), "Relay API key"),
    (re.compile(r"\bauthorization\s*:\s*bearer\s+[A-Za-z0-9._-]{8,}", re.I), "Bearer token"),
    (re.compile(r"\brelay_session=[^;\s]+", re.I), "relay_session cookie"),
)
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
RESERVED_DOMAIN_SUFFIXES = (
    ".example.com",
    ".example.net",
    ".example.org",
    ".example.test",
)
TUNNEL_HOST_SUFFIXES = (
    ".ngrok-free.app",
    ".ngrok.io",
    ".trycloudflare.com",
    ".loca.lt",
    ".localtunnel.me",
    ".localhost.run",
    ".serveo.net",
)

REQUIRED_PROBE_EVIDENCE = {
    "health": 200,
    "me.email_present": True,
    "video_url.relay_domain": True,
    "content.head_location_present": False,
    "content.range_content_range_present": True,
    "content.range_location_present": False,
}

REQUIRED_LOCAL_ACCEPTANCE_CHECKS = (
    "admin_created_customer",
    "admin_set_multiplier_and_models",
    "admin_model_list_default_and_empty_are_distinct",
    "admin_model_options_include_native_and_alias_choices",
    "model_aliases_are_opt_in_per_customer",
    "admin_surfaces_have_no_sfw_nsfw_customer_toggle",
    "customer_surfaces_keep_operator_nsfw_notes_private",
    "customer_changed_password",
    "customer_password_reuse_rejected",
    "cookie_password_change_rejects_cross_site_origin",
    "cookie_password_change_revokes_other_sessions",
    "customer_old_password_rejected",
    "customer_new_password_login_works",
    "stale_session_revoked_after_password_change",
    "admin_reset_customer_password",
    "admin_reset_password_revoked_customer_session",
    "customer_rotated_api_key",
    "customer_account_surfaces_mask_relay_api_key",
    "relay_api_keys_are_server_generated",
    "old_api_key_failed",
    "admin_rotated_customer_relay_key",
    "old_customer_key_failed_after_admin_rotation",
    "admin_api_key_rotation_revoked_customer_session",
    "admin_rotated_customer_key_works",
    "customer_sees_only_enabled_models",
    "runtime_estimate_uses_customer_multiplier",
    "runtime_cookie_estimate_accepts_session",
    "runtime_cookie_create_rejects_cross_site_before_upstream",
    "disabled_model_rejected_before_upstream",
    "enabled_native_content_created",
    "prompt_text_passed_without_relay_censorship",
    "runtime_prepare_helper_rejects_unowned_asset_before_upstream",
    "runtime_prepare_failure_refunds_reserved_balance",
    "runtime_upstream_error_refunds_reserved_balance",
    "runtime_local_task_write_failure_cancels_upstream_and_refunds",
    "runtime_create_rejects_insufficient_balance_before_prepare",
    "runtime_terminal_refresh_settles_once_and_refunds_hold",
    "runtime_settlement_uses_task_multiplier_snapshot",
    "runtime_failed_terminal_refresh_refunds_hold_once",
    "fastapi_fallback_upstream_error_refunds_reserved_balance",
    "fastapi_fallback_local_task_write_failure_cancels_upstream_and_refunds",
    "task_detail_uses_relay_video_url",
    "task_list_is_customer_scoped_and_relay_only",
    "task_list_excludes_other_customers_tasks",
    "task_list_status_limit_offset_works",
    "video_content_rejects_other_customer_before_upstream",
    "head_content_uses_relay_without_redirect",
    "range_playback_uses_relay_without_redirect",
    "browser_network_uses_relay_video_url",
    "process_logs_hide_upstream_urls_and_secrets",
    "no_new_local_mp4",
    "admin_user_list_masks_customer_and_upstream_keys",
    "admin_user_detail_masks_customer_and_upstream_keys",
    "audit_contains_expected_secret_safe_actions",
    "caddy_equivalent_routes_hot_paths_to_go",
    "control_plane_paths_remain_on_fastapi",
)
REQUIRED_LOCAL_RUNTIME_PATHS = (
    ("GET", "/v1/models"),
    ("POST", "/v1/videos/estimate"),
    ("POST", "/v1/videos"),
    ("GET", "/v1/videos"),
)
REQUIRED_LOCAL_RUNTIME_PATH_PATTERNS = (
    ("GET", re.compile(r"^/v1/videos/[^/?]+$")),
    ("GET", re.compile(r"^/v1/videos\?status=succeeded&limit=1&offset=1$")),
    ("GET", re.compile(r"^/v1/videos/[^/?]+/content$")),
    ("HEAD", re.compile(r"^/v1/videos/[^/?]+/content$")),
)
REQUIRED_LOCAL_RELAY_PATHS = (
    ("POST", "/admin/users"),
    ("POST", "/auth/login"),
    ("POST", "/auth/change-password"),
    ("GET", "/v1/me"),
    ("POST", "/v1/me/api-key/rotate"),
    ("GET", "/admin/users"),
)
REQUIRED_LOCAL_RELAY_PATH_PATTERNS = (
    ("PATCH", re.compile(r"^/admin/users/[^/?]+$")),
    ("GET", re.compile(r"^/admin/users/[^/?]+$")),
    ("POST", re.compile(r"^/admin/users/[^/?]+/api-key/rotate$")),
    ("POST", re.compile(r"^/admin/users/[^/?]+/topup$")),
    ("GET", re.compile(r"^/admin/audit-events(?:\?|$)")),
)
REQUIRED_LOCAL_AUDIT_ACTIONS = (
    "admin_changed_price_multiplier",
    "admin_changed_enabled_models",
    "admin_changed_balance",
    "admin_changed_status",
    "admin_changed_upstream_key",
    "admin_reset_password",
    "admin_rotated_customer_api_key",
    "customer_password_changed",
    "customer_api_key_rotated",
)
REQUIRED_LOCAL_GATE_LABELS = (
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
)
REQUIRED_COLLECTOR_MANIFEST_LABELS = (
    "deploy_preflight",
    "caddy_validate",
    "external_probe",
    "evidence_verify",
)
REQUIRED_LOCAL_GATE_VERIFIER_OUTPUT_LABELS = (
    "local_acceptance_verify",
    "local_gate_manifest_verify",
)
REQUIRED_LOCAL_GATE_UNITTEST_MARKERS = (
    "test_malformed_bearer_with_cookie_does_not_bypass_csrf_origin_guard",
    "test_malformed_authorization_does_not_fallback_to_cookie_session",
    "test_delete_cancel_refunds_hold_only_once_at_db_boundary",
)
REQUIRED_LOCAL_GATE_GOTEST_MARKERS = (
    "TestCreateVideoRejectsMalformedBearerWithCookieViaCSRFBeforeUpstream",
    "TestCreateVideoRejectsMalformedAuthorizationInsteadOfFallingBackToCookie",
)


class EvidenceReport:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def ok(self, message: str) -> None:
        print(f"[OK] {message}")

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        print(f"[WARN] {message}")

    def fail(self, message: str) -> None:
        self.failures.append(message)
        print(f"[FAIL] {message}")


def read_text(path: Path, report: EvidenceReport) -> str:
    if not path.exists():
        report.fail(f"missing file: {path}")
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    report.ok(f"found {path}")
    return text


def assert_no_sensitive(text: str, label: str, report: EvidenceReport) -> None:
    lowered = text.lower()
    for marker in SENSITIVE_MARKERS:
        if marker in lowered:
            report.fail(f"{label} leaked sensitive marker: {marker}")
    for pattern, name in SENSITIVE_PATTERNS:
        if pattern.search(text):
            report.fail(f"{label} leaked sensitive token: {name}")


def load_probe(path: Path, report: EvidenceReport) -> dict[str, Any]:
    text = read_text(path, report)
    if not text:
        return {}
    assert_no_sensitive(text, "probe evidence", report)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        report.fail(f"probe evidence is not valid JSON: {exc}")
        return {}
    if not isinstance(payload, dict):
        report.fail("probe evidence root must be an object")
        return {}
    return payload


def verify_probe(
    path: Path,
    report: EvidenceReport,
    expected_base_url: str | None = None,
    expected_video_id: str | None = None,
    expected_screenshot: Path | None = None,
    expected_screenshot_raw: str | None = None,
) -> None:
    payload = load_probe(path, report)
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    failures = payload.get("failures")
    if failures:
        report.fail(f"probe evidence contains failures: {failures}")
    else:
        report.ok("probe failures empty")

    for key, expected in REQUIRED_PROBE_EVIDENCE.items():
        actual = evidence.get(key)
        if actual != expected:
            report.fail(f"probe evidence {key!r} = {actual!r}, expected {expected!r}")
        else:
            report.ok(f"probe evidence {key} verified")

    models_count = evidence.get("models.count")
    if type(models_count) is not int or models_count <= 0:
        report.fail(f"probe evidence models.count = {models_count!r}, expected a positive integer")
    else:
        report.ok(f"probe evidence models.count verified: {models_count}")

    videos_total = evidence.get("videos.total")
    if type(videos_total) is not int or videos_total < 0:
        report.fail(f"probe evidence videos.total = {videos_total!r}, expected a non-negative integer")
    else:
        report.ok(f"probe evidence videos.total verified: {videos_total}")

    range_status = evidence.get("content.range_status")
    if range_status != 206:
        report.fail(f"probe evidence content.range_status = {range_status!r}, expected 206")
    else:
        report.ok(f"probe evidence content.range_status verified: {range_status}")

    head_status = evidence.get("content.head_status")
    if head_status not in (200, 206):
        report.fail(f"probe evidence content.head_status = {head_status!r}, expected 200 or 206")
    else:
        report.ok(f"probe evidence content.head_status verified: {head_status}")

    request_urls = evidence.get("browser.request_urls")
    responses = evidence.get("browser.responses")
    screenshot = evidence.get("browser.screenshot")
    expected_content_url = evidence.get("browser.expected_content_url")
    if not isinstance(expected_content_url, str) or not expected_content_url:
        report.fail("probe evidence missing browser.expected_content_url")
    elif "/v1/videos/" not in expected_content_url or not expected_content_url.endswith("/content"):
        report.fail("browser.expected_content_url is not a Relay video content URL")
    else:
        report.ok("browser expected Relay content URL recorded")
        if expected_base_url:
            expected_prefix = expected_base_url.rstrip("/") + "/v1/videos/"
            if not expected_content_url.startswith(expected_prefix):
                report.fail("browser.expected_content_url does not match manifest base_url")
            else:
                report.ok("browser expected content URL matches manifest base_url")
        if expected_video_id:
            expected_suffix = f"/v1/videos/{expected_video_id}/content"
            if not expected_content_url.endswith(expected_suffix):
                report.fail("browser.expected_content_url does not match release video_id")
            else:
                report.ok("browser expected content URL matches release video_id")
    if not isinstance(request_urls, list) or not request_urls:
        report.fail("probe evidence missing browser.request_urls")
    else:
        if expected_content_url and expected_content_url not in request_urls:
            report.fail("browser request URLs do not include expected Relay content URL")
        report.ok(f"browser request count verified: {len(request_urls)}")
    if not isinstance(responses, list) or not responses:
        report.fail("probe evidence missing browser.responses")
    else:
        location_leaks = [item for item in responses if isinstance(item, dict) and item.get("location_present")]
        expected_responses = [
            item for item in responses if isinstance(item, dict) and item.get("url") == expected_content_url
        ]
        if expected_content_url and not expected_responses:
            report.fail("browser responses do not include expected Relay content URL")
        bad_expected_status = [
            item for item in expected_responses if item.get("status") not in (200, 206)
        ]
        if bad_expected_status:
            report.fail("browser expected Relay content response has invalid status")
        elif expected_responses:
            report.ok("browser expected Relay content response status verified")
        non_video_expected_responses = [
            item
            for item in expected_responses
            if "video/" not in str(item.get("content_type") or "").lower()
        ]
        if non_video_expected_responses:
            report.fail("browser expected Relay content response is not video content")
        elif expected_responses:
            report.ok("browser expected Relay content response content-type verified")
        missing_expected_range = [
            item for item in expected_responses if item.get("status") == 206 and not item.get("content_range")
        ]
        if missing_expected_range:
            report.fail("browser expected Relay 206 response is missing Content-Range")
        if location_leaks:
            report.fail("browser response metadata shows Location redirect")
        else:
            report.ok(f"browser response metadata verified: {len(responses)} response(s)")
    if not screenshot:
        report.fail("probe evidence missing browser.screenshot")
    else:
        report.ok(f"browser screenshot evidence recorded: {screenshot}")
        if expected_screenshot:
            accepted = {str(expected_screenshot), str(expected_screenshot.resolve())}
            if expected_screenshot_raw:
                accepted.add(expected_screenshot_raw)
            if str(screenshot) not in accepted:
                report.fail("browser.screenshot does not match the verified screenshot artifact")
            else:
                report.ok("browser screenshot artifact matches probe evidence")


def verify_screenshot(path: Path, report: EvidenceReport) -> None:
    if not path.exists():
        report.fail(f"missing screenshot: {path}")
        return
    raw = path.read_bytes()
    if not raw:
        report.fail(f"screenshot is empty: {path}")
        return
    report.ok("screenshot exists and is non-empty")
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        report.fail(f"screenshot is not a PNG file: {path}")
        return
    if len(raw) < 33:
        report.fail(f"screenshot PNG is too small to contain IHDR dimensions: {path}")
        return
    ihdr_length = struct.unpack(">I", raw[8:12])[0]
    ihdr_type = raw[12:16]
    if ihdr_length != 13 or ihdr_type != b"IHDR":
        report.fail(f"screenshot PNG missing valid IHDR chunk: {path}")
        return
    width, height = struct.unpack(">II", raw[16:24])
    if width < 320 or height < 180:
        report.fail(f"screenshot dimensions too small: {width}x{height}, expected at least 320x180")
        return
    report.ok(f"screenshot PNG dimensions verified: {width}x{height}")


def verify_preflight_output(path: Path, report: EvidenceReport) -> None:
    text = read_text(path, report)
    if not text:
        return
    assert_no_sensitive(text, "preflight output", report)
    if "Preflight passed" not in text:
        report.fail("preflight output does not show 'Preflight passed'")
    else:
        report.ok("preflight output passed")
    if "caddy validate passed" not in text.lower():
        report.fail("preflight output does not show Caddy validation passed")
    else:
        report.ok("preflight Caddy validation passed")


def verify_caddy_output(path: Path, report: EvidenceReport) -> None:
    text = read_text(path, report)
    if not text:
        return
    assert_no_sensitive(text, "caddy validate output", report)
    lowered = text.lower()
    accepted_markers = (
        "valid configuration",
        "caddy validate passed",
        "configuration is valid",
    )
    if not any(marker in lowered for marker in accepted_markers):
        report.fail("Caddy validate output does not show a valid configuration")
    else:
        report.ok("Caddy validate output passed")


def verify_probe_output(path: Path, report: EvidenceReport) -> None:
    text = read_text(path, report)
    if not text:
        return
    assert_no_sensitive(text, "probe output", report)
    if "Probe passed" not in text:
        report.fail("probe output does not show 'Probe passed'")
    else:
        report.ok("probe output passed")


def verify_verifier_output(path: Path, report: EvidenceReport) -> None:
    text = read_text(path, report)
    if not text:
        return
    assert_no_sensitive(text, "evidence verifier output", report)
    if "Evidence verification passed" not in text:
        report.fail("evidence verifier output does not show 'Evidence verification passed'")
    else:
        report.ok("evidence verifier output passed")


def verify_completion_output(path: Path, report: EvidenceReport) -> None:
    text = read_text(path, report)
    if not text:
        return
    assert_no_sensitive(text, "completion audit output", report)
    if "Alpha1 completion audit passed" not in text:
        report.fail("completion audit output does not show 'Alpha1 completion audit passed'")
    else:
        report.ok("completion audit output passed")


def verify_completion_summary(path: Path, report: EvidenceReport) -> None:
    text = read_text(path, report)
    if not text:
        return
    assert_no_sensitive(text, "completion audit summary", report)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        report.fail(f"completion audit summary is not valid JSON: {exc}")
        return
    if not isinstance(payload, dict):
        report.fail("completion audit summary root must be an object")
        return
    if payload.get("release_complete") is not True:
        report.fail("completion audit summary release_complete is not true")
    else:
        report.ok("completion audit summary release_complete verified")
    if payload.get("status") != "complete":
        report.fail("completion audit summary status is not complete")
    else:
        report.ok("completion audit summary status verified")
    failures = payload.get("failures")
    if failures != []:
        report.fail("completion audit summary failures must be empty")
    else:
        report.ok("completion audit summary failures verified")
    checks = payload.get("checks")
    if not isinstance(checks, dict):
        report.fail("completion audit summary checks must be an object")
        return
    external_manifest = checks.get("external_manifest")
    if not isinstance(external_manifest, dict) or external_manifest.get("status") != "passed":
        report.fail("completion audit summary external_manifest check is not passed")
    else:
        report.ok("completion audit summary external_manifest check verified")
    if "local_gate_match" in checks:
        if payload.get("local_gate_match") is not True:
            report.fail("completion audit summary local_gate_match is not true")
        else:
            report.ok("completion audit summary local_gate_match verified")


def verify_local_acceptance(path: Path, report: EvidenceReport) -> None:
    text = read_text(path, report)
    if not text:
        return
    assert_no_sensitive(text, "local acceptance evidence", report)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        report.fail(f"local acceptance evidence is not valid JSON: {exc}")
        return
    if not isinstance(payload, dict):
        report.fail("local acceptance evidence root must be an object")
        return
    if payload.get("status") != "passed":
        report.fail(f"local acceptance status is {payload.get('status')!r}, expected 'passed'")
    else:
        report.ok("local acceptance status passed")
    checks = payload.get("checks")
    if not isinstance(checks, dict):
        report.fail("local acceptance checks must be an object")
        return
    for key in REQUIRED_LOCAL_ACCEPTANCE_CHECKS:
        if checks.get(key) is not True:
            report.fail(f"local acceptance check {key!r} is not true")
        else:
            report.ok(f"local acceptance check verified: {key}")
    runtime_paths = payload.get("runtime_paths")
    relay_paths = payload.get("relay_paths")
    if not isinstance(runtime_paths, list) or not runtime_paths:
        report.fail("local acceptance missing runtime_paths")
    else:
        report.ok(f"local acceptance runtime path count verified: {len(runtime_paths)}")
        verify_local_path_evidence("runtime", runtime_paths, REQUIRED_LOCAL_RUNTIME_PATHS, REQUIRED_LOCAL_RUNTIME_PATH_PATTERNS, report)
    if not isinstance(relay_paths, list) or not relay_paths:
        report.fail("local acceptance missing relay_paths")
    else:
        report.ok(f"local acceptance relay path count verified: {len(relay_paths)}")
        verify_local_path_evidence("relay", relay_paths, REQUIRED_LOCAL_RELAY_PATHS, REQUIRED_LOCAL_RELAY_PATH_PATTERNS, report)
    audit_actions = payload.get("audit_actions")
    if not isinstance(audit_actions, list) or not audit_actions:
        report.fail("local acceptance missing audit_actions")
    else:
        action_set = {str(action) for action in audit_actions}
        for action in REQUIRED_LOCAL_AUDIT_ACTIONS:
            if action not in action_set:
                report.fail(f"local acceptance audit_actions missing {action!r}")
            else:
                report.ok(f"local acceptance audit action verified: {action}")


def normalize_local_path_entries(paths: list[Any]) -> set[tuple[str, str]]:
    entries: set[tuple[str, str]] = set()
    for item in paths:
        if not isinstance(item, dict):
            continue
        method = str(item.get("method") or "").upper()
        path = str(item.get("path") or "")
        if method and path:
            entries.add((method, path))
    return entries


def verify_local_path_evidence(
    label: str,
    paths: list[Any],
    required_exact: tuple[tuple[str, str], ...],
    required_patterns: tuple[tuple[str, re.Pattern[str]], ...],
    report: EvidenceReport,
) -> None:
    entries = normalize_local_path_entries(paths)
    for method, path in required_exact:
        if (method, path) not in entries:
            report.fail(f"local acceptance {label}_paths missing {method} {path}")
        else:
            report.ok(f"local acceptance {label} path verified: {method} {path}")
    for method, pattern in required_patterns:
        if not any(entry_method == method and pattern.search(path) for entry_method, path in entries):
            report.fail(f"local acceptance {label}_paths missing {method} {pattern.pattern}")
        else:
            report.ok(f"local acceptance {label} path pattern verified: {method} {pattern.pattern}")


def resolve_manifest_artifact(manifest_path: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        relative_candidate = manifest_path.parent / path
        if relative_candidate.exists():
            return relative_candidate
    if path.exists():
        try:
            path.resolve().relative_to(manifest_path.parent.resolve())
            return path
        except ValueError:
            pass
    nested_candidate = manifest_path.parent / path.parent.name / path.name
    if path.is_absolute() and nested_candidate.exists():
        return nested_candidate
    sibling = manifest_path.parent / path.name
    if sibling.exists():
        return sibling
    if path.exists():
        return path
    if path.is_absolute():
        return path
    return manifest_path.parent / path


def path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def load_manifest(path: Path, report: EvidenceReport) -> dict[str, Any]:
    text = read_text(path, report)
    if not text:
        return {}
    assert_no_sensitive(text, "manifest", report)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        report.fail(f"manifest is not valid JSON: {exc}")
        return {}
    if not isinstance(payload, dict):
        report.fail("manifest root must be an object")
        return {}
    return payload


def verify_manifest_commands(
    manifest: dict[str, Any],
    report: EvidenceReport,
    *,
    allow_pending_completion_audit: bool = False,
) -> None:
    commands = manifest.get("commands")
    if commands is None:
        report.fail("manifest has no commands list; cannot verify command return codes")
        return
    if not isinstance(commands, list) or not commands:
        report.fail("manifest commands must be a non-empty list")
        return
    invalid = [item for item in commands if not isinstance(item, dict)]
    if invalid:
        report.fail(f"manifest contains invalid command entries: {len(invalid)}")
        return
    dry_run = [item for item in commands if item.get("dry_run")]
    if dry_run:
        report.fail(f"manifest contains dry-run command evidence: {len(dry_run)}")
    skipped = [item for item in commands if item.get("skipped")]
    if skipped:
        report.fail(f"manifest contains skipped command evidence: {len(skipped)}")
    missing_returncode = []
    for item in commands:
        if item.get("skipped") or item.get("dry_run") or "returncode" in item:
            continue
        if allow_pending_completion_audit and item.get("label") == "completion_audit":
            continue
        missing_returncode.append(item)
    if missing_returncode:
        report.fail(f"manifest contains command entries without return codes: {len(missing_returncode)}")
    failed = []
    for item in commands:
        if item.get("skipped") or item.get("dry_run"):
            continue
        if allow_pending_completion_audit and item.get("label") == "completion_audit" and "returncode" not in item:
            continue
        if item.get("returncode") != 0:
            failed.append(item)
    if failed:
        report.fail(f"manifest contains failed command return codes: {len(failed)}")
    elif not (dry_run or skipped or missing_returncode):
        report.ok(f"manifest command return codes verified: {len(commands)} command(s)")


def verify_required_command_labels(
    commands: Any,
    required_labels: tuple[str, ...],
    *,
    label: str,
    report: EvidenceReport,
) -> None:
    if not isinstance(commands, list):
        return
    labels = {str(item.get("label")) for item in commands if isinstance(item, dict)}
    for required_label in required_labels:
        if required_label not in labels:
            report.fail(f"{label} missing command label: {required_label}")
    if all(required_label in labels for required_label in required_labels):
        report.ok(f"{label} required command labels verified")


def verify_manifest_base_url(manifest: dict[str, Any], report: EvidenceReport) -> str | None:
    base_url = manifest.get("base_url")
    if not isinstance(base_url, str) or not base_url:
        report.fail("manifest missing base_url")
        return None
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        report.fail(f"manifest base_url must use https: {base_url!r}")
        return None
    elif not host:
        report.fail(f"manifest base_url missing hostname: {base_url!r}")
        return None
    elif parsed.username or parsed.password:
        report.fail(f"manifest base_url must not include username or password: {base_url!r}")
        return None
    elif parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        report.fail(f"manifest base_url must be a bare origin without path, query, or fragment: {base_url!r}")
        return None
    elif host in LOCAL_HOSTS or host.endswith(".localhost"):
        report.fail(f"manifest base_url must be an external domain, not localhost: {base_url!r}")
        return None
    elif host in {suffix.removeprefix(".") for suffix in RESERVED_DOMAIN_SUFFIXES} or any(
        host.endswith(suffix) for suffix in RESERVED_DOMAIN_SUFFIXES
    ):
        report.fail(f"manifest base_url must be a real Relay domain, not an example domain: {base_url!r}")
        return None
    elif any(host.endswith(suffix) for suffix in TUNNEL_HOST_SUFFIXES):
        report.fail(f"manifest base_url must be a stable Relay domain, not a tunnel URL: {base_url!r}")
        return None
    else:
        report.ok(f"manifest external https base_url verified: {base_url}")
        return base_url.rstrip("/")


def verify_manifest_video_id(manifest: dict[str, Any], report: EvidenceReport) -> str | None:
    video_id = manifest.get("video_id")
    if not isinstance(video_id, str) or not video_id:
        report.fail("manifest missing video_id")
        return None
    if "/" in video_id or "?" in video_id or "#" in video_id:
        report.fail(f"manifest video_id must be a plain video id: {video_id!r}")
        return None
    report.ok(f"manifest video_id verified: {video_id}")
    return video_id


def artifact_identity(manifest_path: Path, raw_path: str) -> str:
    return str(resolve_manifest_artifact(manifest_path, raw_path).resolve())


def verify_manifest_command_outputs(
    manifest_path: Path,
    manifest: dict[str, Any],
    artifacts: dict[str, Any],
    report: EvidenceReport,
) -> None:
    commands = manifest.get("commands")
    if not isinstance(commands, list):
        return
    output_paths = {
        artifact_identity(manifest_path, str(item.get("output")))
        for item in commands
        if isinstance(item, dict) and item.get("output")
    }
    required_outputs = {
        "preflight": "preflight command output",
        "caddy": "Caddy validate command output",
        "probe_output": "probe command output",
        "verify": "evidence verifier command output",
    }
    if artifacts.get("completion_output"):
        required_outputs["completion_output"] = "completion audit command output"
    missing = [
        key
        for key in required_outputs
        if artifact_identity(manifest_path, str(artifacts[key])) not in output_paths
    ]
    for key in missing:
        report.fail(f"manifest artifact {key!r} is not recorded as a command output")
    if not missing:
        report.ok("manifest command outputs match required artifacts")


def command_references_artifact(
    manifest_path: Path,
    command: list[str],
    raw_path: str,
    resolved_path: Path,
) -> bool:
    for item in command:
        if item == raw_path or item == str(resolved_path):
            return True
        try:
            if resolve_manifest_artifact(manifest_path, item).resolve() == resolved_path.resolve():
                return True
        except OSError:
            continue
    return False


def verify_completion_audit_command_inputs(
    manifest_path: Path,
    manifest: dict[str, Any],
    artifacts: dict[str, Any],
    report: EvidenceReport,
) -> None:
    commands = manifest.get("commands")
    if not isinstance(commands, list):
        return
    completion_commands = [
        item for item in commands if isinstance(item, dict) and item.get("label") == "completion_audit"
    ]
    if not completion_commands:
        return
    raw_summary = artifacts.get("completion_summary")
    if not raw_summary:
        return
    summary_path = resolve_manifest_artifact(manifest_path, str(raw_summary))
    command = [str(item) for item in completion_commands[-1].get("command", [])]
    if "--manifest" not in command:
        report.fail("completion_audit command missing --manifest")
    elif not command_references_artifact(manifest_path, command, str(manifest_path), manifest_path):
        report.fail("completion_audit command does not reference the current manifest")
    else:
        report.ok("completion_audit command manifest artifact verified")
    if "--summary-json" not in command:
        report.fail("completion_audit command missing --summary-json")
        return
    if not command_references_artifact(manifest_path, command, str(raw_summary), summary_path):
        report.fail("completion_audit command does not reference artifacts.completion_summary")
    else:
        report.ok("completion_audit command summary artifact verified")


def verify_local_gate_manifest(
    path: Path,
    report: EvidenceReport,
    *,
    allow_pending_self_check: bool = False,
) -> None:
    manifest = load_manifest(path, report)
    if not manifest:
        return

    if manifest.get("status") != "passed":
        report.fail(f"local gate status is {manifest.get('status')!r}, expected 'passed'")
    else:
        report.ok("local gate status passed")

    if manifest.get("local_acceptance_acceptable") is not True:
        report.fail("local gate is not acceptance acceptable")
    else:
        report.ok("local gate acceptance flag verified")

    verify_manifest_commands(manifest, report)
    commands = manifest.get("commands")
    if isinstance(commands, list):
        verify_required_command_labels(
            commands,
            REQUIRED_LOCAL_GATE_LABELS,
            label="local gate",
            report=report,
        )
        for item in commands:
            if not isinstance(item, dict) or not item.get("output"):
                continue
            output_path = resolve_manifest_artifact(path, str(item["output"]))
            if not path_is_within(output_path, path.parent):
                report.fail(f"local gate output {item.get('label')} is not archived beside the manifest")
                continue
            text = read_text(output_path, report)
            if text:
                item_label = str(item.get("label") or "")
                output_label = f"local gate output {item_label}"
                assert_no_sensitive(text, output_label, report)
                if item_label in REQUIRED_LOCAL_GATE_VERIFIER_OUTPUT_LABELS:
                    if "Evidence verification passed" not in text:
                        if (
                            allow_pending_self_check
                            and item_label == "local_gate_manifest_verify"
                            and text.startswith("PENDING:")
                        ):
                            report.ok(f"{output_label} pending self-check accepted")
                        else:
                            report.fail(f"{output_label} does not show 'Evidence verification passed'")
                    else:
                        report.ok(f"{output_label} verification marker passed")
                if item_label == "python_unittest":
                    for marker in REQUIRED_LOCAL_GATE_UNITTEST_MARKERS:
                        if marker not in text:
                            report.fail(f"{output_label} missing required unittest marker: {marker}")
                    if all(marker in text for marker in REQUIRED_LOCAL_GATE_UNITTEST_MARKERS):
                        report.ok(f"{output_label} required unittest markers verified")
                if item_label == "go_test":
                    for marker in REQUIRED_LOCAL_GATE_GOTEST_MARKERS:
                        if marker not in text:
                            report.fail(f"{output_label} missing required go test marker: {marker}")
                    if all(marker in text for marker in REQUIRED_LOCAL_GATE_GOTEST_MARKERS):
                        report.ok(f"{output_label} required go test markers verified")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        report.fail("local gate artifacts must be an object")
        return
    local_acceptance = artifacts.get("local_acceptance")
    if not local_acceptance:
        report.fail("local gate missing artifact path: local_acceptance")
        return
    local_acceptance_path = resolve_manifest_artifact(path, str(local_acceptance))
    if not path_is_within(local_acceptance_path, path.parent):
        report.fail("local gate local_acceptance artifact is not archived beside the manifest")
        return
    verify_local_acceptance(local_acceptance_path, report)


def verify_manifest(
    path: Path,
    report: EvidenceReport,
    *,
    allow_pending_completion_audit: bool = False,
) -> None:
    manifest = load_manifest(path, report)
    if not manifest:
        return

    if manifest.get("release_acceptable") is not True:
        report.fail("manifest is not release acceptable")
    else:
        report.ok("manifest release_acceptable verified")

    if manifest.get("browser_required") is not True:
        report.fail("manifest does not require browser evidence")
    else:
        report.ok("manifest browser evidence required")

    if manifest.get("status") != "passed":
        report.fail(f"manifest status is {manifest.get('status')!r}, expected 'passed'")
    else:
        report.ok("manifest status passed")

    manifest_base_url = verify_manifest_base_url(manifest, report)
    manifest_video_id = verify_manifest_video_id(manifest, report)
    verify_manifest_commands(
        manifest,
        report,
        allow_pending_completion_audit=allow_pending_completion_audit,
    )
    verify_required_command_labels(
        manifest.get("commands"),
        REQUIRED_COLLECTOR_MANIFEST_LABELS,
        label="collector manifest",
        report=report,
    )

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        report.fail("manifest artifacts must be an object")
        return

    required = {
        "probe": "probe JSON",
        "probe_output": "probe command output",
        "screenshot": "browser screenshot",
        "preflight": "preflight output",
        "caddy": "caddy validate output",
        "local_acceptance": "local acceptance smoke JSON",
        "local_gate_manifest": "local gate manifest",
        "verify": "evidence verifier output",
    }
    if not allow_pending_completion_audit:
        required.update(
            {
                "completion_output": "completion audit output",
                "completion_summary": "completion audit summary JSON",
            }
        )
    missing = [key for key in required if not artifacts.get(key)]
    for key in missing:
        report.fail(f"manifest missing artifact path: {key} ({required[key]})")
    if missing:
        return

    verify_required_command_labels(
        manifest.get("commands"),
        ("completion_audit",),
        label="collector manifest",
        report=report,
    )

    verify_manifest_command_outputs(path, manifest, artifacts, report)
    verify_completion_audit_command_inputs(path, manifest, artifacts, report)
    screenshot_artifact = resolve_manifest_artifact(path, str(artifacts["screenshot"]))
    verify_probe(
        resolve_manifest_artifact(path, str(artifacts["probe"])),
        report,
        manifest_base_url,
        manifest_video_id,
        expected_screenshot=screenshot_artifact,
        expected_screenshot_raw=str(artifacts["screenshot"]),
    )
    verify_probe_output(resolve_manifest_artifact(path, str(artifacts["probe_output"])), report)
    verify_screenshot(screenshot_artifact, report)
    verify_preflight_output(resolve_manifest_artifact(path, str(artifacts["preflight"])), report)
    verify_caddy_output(resolve_manifest_artifact(path, str(artifacts["caddy"])), report)
    verify_local_acceptance(resolve_manifest_artifact(path, str(artifacts["local_acceptance"])), report)
    verify_local_gate_manifest(resolve_manifest_artifact(path, str(artifacts["local_gate_manifest"])), report)
    verify_verifier_output(resolve_manifest_artifact(path, str(artifacts["verify"])), report)
    if artifacts.get("completion_output"):
        completion_output_path = resolve_manifest_artifact(path, str(artifacts["completion_output"]))
        if allow_pending_completion_audit and not completion_output_path.exists():
            report.ok("completion audit output pending self-check accepted")
        else:
            verify_completion_output(completion_output_path, report)
    elif allow_pending_completion_audit:
        report.ok("completion audit output pending self-check accepted")
    if artifacts.get("completion_summary"):
        completion_summary_path = resolve_manifest_artifact(path, str(artifacts["completion_summary"]))
        if allow_pending_completion_audit and not completion_summary_path.exists():
            report.ok("completion audit summary pending self-check accepted")
        else:
            verify_completion_summary(completion_summary_path, report)
    elif allow_pending_completion_audit:
        report.ok("completion audit summary pending self-check accepted")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify saved alpha1 deployment evidence.")
    parser.add_argument("--manifest", help="Path to manifest.json from alpha1_collect_evidence.py")
    parser.add_argument("--local-gate-manifest", help="Path to local-gate-manifest.json from alpha1_local_gate.py")
    parser.add_argument("--base-url", help="External HTTPS Relay base URL for explicit artifact verification")
    parser.add_argument("--video-id", help="Existing succeeded video id used for explicit artifact verification")
    parser.add_argument("--probe-json", help="Path to alpha1-probe-evidence.json")
    parser.add_argument(
        "--probe-output",
        help="Captured stdout from alpha1_probe.py; defaults to alpha1-probe-output.txt beside --probe-json",
    )
    parser.add_argument("--screenshot", help="Path to alpha1-browser-video.png")
    parser.add_argument("--preflight-output", help="Captured output from alpha1_preflight.py --caddyfile")
    parser.add_argument("--caddy-output", help="Captured output from caddy validate")
    parser.add_argument("--local-acceptance-json", help="alpha1-local-acceptance.json from the local live smoke")
    parser.add_argument("--local-only", action="store_true", help="Verify only alpha1-local-acceptance.json")
    parser.add_argument(
        "--allow-pending-local-gate-self-check",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    report = EvidenceReport()
    explicit_artifact_values = {
        "--base-url": args.base_url,
        "--video-id": args.video_id,
        "--probe-json": args.probe_json,
        "--screenshot": args.screenshot,
        "--preflight-output": args.preflight_output,
        "--caddy-output": args.caddy_output,
        "--local-acceptance-json": args.local_acceptance_json,
    }
    explicit_required_values = {
        **explicit_artifact_values,
        "--local-gate-manifest": args.local_gate_manifest,
    }
    has_explicit_artifacts = any(explicit_artifact_values.values())
    if args.local_only:
        if args.manifest:
            parser.error("--local-only cannot be combined with --manifest")
        if args.local_gate_manifest:
            parser.error("--local-only cannot be combined with --local-gate-manifest")
        if not args.local_acceptance_json:
            parser.error("--local-only requires --local-acceptance-json")
        verify_local_acceptance(Path(args.local_acceptance_json), report)
    elif args.local_gate_manifest and not has_explicit_artifacts:
        if args.manifest:
            parser.error("--local-gate-manifest cannot be combined with --manifest")
        verify_local_gate_manifest(
            Path(args.local_gate_manifest),
            report,
            allow_pending_self_check=args.allow_pending_local_gate_self_check,
        )
    elif args.manifest:
        if args.local_gate_manifest:
            parser.error("--manifest reads local_gate_manifest from artifacts; do not also pass --local-gate-manifest")
        verify_manifest(Path(args.manifest), report)
    else:
        missing = [
            name
            for name, value in explicit_required_values.items()
            if not value
        ]
        if missing:
            parser.error("--manifest or all explicit artifact arguments are required; missing " + ", ".join(missing))
        explicit_base_url = verify_manifest_base_url({"base_url": args.base_url}, report)
        explicit_video_id = verify_manifest_video_id({"video_id": args.video_id}, report)
        probe_json_path = Path(args.probe_json)
        probe_output_path = Path(args.probe_output) if args.probe_output else probe_json_path.with_name("alpha1-probe-output.txt")
        verify_probe(
            probe_json_path,
            report,
            explicit_base_url,
            explicit_video_id,
            expected_screenshot=Path(args.screenshot),
            expected_screenshot_raw=args.screenshot,
        )
        verify_probe_output(probe_output_path, report)
        verify_screenshot(Path(args.screenshot), report)
        verify_preflight_output(Path(args.preflight_output), report)
        verify_caddy_output(Path(args.caddy_output), report)
        verify_local_acceptance(Path(args.local_acceptance_json), report)
        verify_local_gate_manifest(
            Path(args.local_gate_manifest),
            report,
            allow_pending_self_check=args.allow_pending_local_gate_self_check,
        )

    if report.failures:
        print(f"Evidence verification failed: {len(report.failures)} failure(s), {len(report.warnings)} warning(s)")
        return 1
    print(f"Evidence verification passed: {len(report.warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
