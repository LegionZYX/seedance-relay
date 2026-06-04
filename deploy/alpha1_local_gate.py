#!/usr/bin/env python3
"""Run the local alpha1 release gate and archive command outputs."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
import re


PY_COMPILE_TARGETS = [
    "relay_server.py",
    "create_asset_white_label.py",
    "modelark/pricing.py",
    "deploy/alpha1_preflight.py",
    "deploy/alpha1_probe.py",
    "deploy/alpha1_verify_evidence.py",
    "deploy/alpha1_collect_evidence.py",
    "deploy/alpha1_completion_audit.py",
    "deploy/alpha1_local_gate.py",
    "deploy/alpha1_worktree_check.py",
]
SENSITIVE_REPLACEMENTS = (
    (re.compile(r"bytepluses\.com", re.I), "<provider-domain>"),
    (re.compile(r"byteplus", re.I), "<provider>"),
    (re.compile(r"volces\.com", re.I), "<provider-domain>"),
    (re.compile(r"\bark-[A-Za-z0-9._-]+"), "<redacted-upstream-key>"),
    (re.compile(r"\bsk[-_][A-Za-z0-9][A-Za-z0-9_-]{8,}\b"), "<redacted-relay-key>"),
    (re.compile(r"\bauthorization\s*:\s*bearer\s+[A-Za-z0-9._-]{8,}", re.I), "Authorization: Bearer <redacted>"),
    (re.compile(r"\brelay_session=[^;\s]+", re.I), "relay_session=<redacted>"),
)


STATIC_JS_CHECK = r"""
const fs = require('fs');
const vm = require('vm');
for (const file of ['static/app.html', 'static/admin.html']) {
  const html = fs.readFileSync(file, 'utf8');
  const scripts = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)].map((m) => m[1]);
  scripts.forEach((code, index) => new vm.Script(code, { filename: `${file}#script${index + 1}` }));
  console.log(`${file}: ${scripts.length} inline scripts compiled`);
}
""".strip()


def timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def command_text(command: list[str]) -> str:
    return " ".join(command)


def redact_sensitive_text(text: str) -> str:
    for pattern, replacement in SENSITIVE_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text


def run_command(
    *,
    label: str,
    command: list[str],
    output_path: Path,
    cwd: Path,
    env: dict[str, str] | None,
    manifest: dict[str, Any],
    dry_run: bool,
    display: str | None = None,
    planned_entry: dict[str, Any] | None = None,
) -> int:
    entry: dict[str, Any]
    if planned_entry is None:
        entry = {
            "label": label,
            "command": command,
            "display": display or command_text(command),
            "output": str(output_path),
            "dry_run": dry_run,
        }
        manifest["commands"].append(entry)
    else:
        entry = planned_entry
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if dry_run:
        output_path.write_text(
            redact_sensitive_text("DRY RUN\n" + entry["display"] + "\n"),
            encoding="utf-8",
        )
        return 0

    proc = subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output_path.write_text(redact_sensitive_text(proc.stdout), encoding="utf-8")
    entry["returncode"] = proc.returncode
    return proc.returncode


def build_commands(root: Path, out_dir: Path) -> list[dict[str, Any]]:
    local_acceptance = out_dir / "alpha1-local-acceptance.json"
    python_files = PY_COMPILE_TARGETS + [
        "tests/test_alpha1_collect_evidence.py",
        "tests/test_alpha1_completion_audit.py",
        "tests/test_alpha1_evidence_verifier.py",
        "tests/test_alpha1_live_smoke.py",
        "tests/test_alpha1_local_gate.py",
        "tests/test_alpha1_preflight.py",
        "tests/test_alpha1_probe.py",
        "tests/test_alpha1_spec_controls.py",
        "tests/test_deployment_config.py",
        "tests/test_model_aliases.py",
        "tests/test_spec_scope_consistency.py",
        "tests/test_static_admin_ui.py",
        "tests/test_static_customer_ui.py",
    ]
    return [
        {
            "label": "python_py_compile",
            "command": [sys.executable, "-m", "py_compile", *python_files],
            "output": out_dir / "python-py-compile-output.txt",
        },
        {
            "label": "python_unittest",
            "command": [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
            "output": out_dir / "python-unittest-output.txt",
        },
        {
            "label": "go_test",
            "command": ["go", "test", "-v", "./..."],
            "cwd": root / "runtime-go",
            "output": out_dir / "go-test-output.txt",
        },
        {
            "label": "go_build",
            "command": ["go", "build", "./..."],
            "cwd": root / "runtime-go",
            "output": out_dir / "go-build-output.txt",
        },
        {
            "label": "static_inline_js_compile",
            "command": ["node", "-e", STATIC_JS_CHECK],
            "output": out_dir / "static-inline-js-output.txt",
        },
        {
            "label": "docker_compose_config",
            "command": ["docker", "compose", "-f", "docker-compose.relay.yml", "config"],
            "output": out_dir / "docker-compose-config-output.txt",
        },
        {
            "label": "preflight",
            "command": [sys.executable, "deploy/alpha1_preflight.py"],
            "output": out_dir / "alpha1-preflight-output.txt",
        },
        {
            "label": "live_smoke_acceptance",
            "command": [
                sys.executable,
                "-m",
                "unittest",
                "tests.test_alpha1_live_smoke.Alpha1LiveSmokeTests.test_fastapi_control_plane_and_go_runtime_acceptance_path",
                "-v",
            ],
            "output": out_dir / "alpha1-live-smoke-output.txt",
            "env": {"ALPHA1_LIVE_SMOKE_EVIDENCE_JSON": str(local_acceptance)},
        },
        {
            "label": "local_acceptance_verify",
            "command": [
                sys.executable,
                "deploy/alpha1_verify_evidence.py",
                "--local-only",
                "--local-acceptance-json",
                str(local_acceptance),
            ],
            "output": out_dir / "alpha1-local-verify-output.txt",
        },
        {
            "label": "local_gate_manifest_verify",
            "command": [
                sys.executable,
                "deploy/alpha1_verify_evidence.py",
                "--local-gate-manifest",
                str(out_dir / "local-gate-manifest.json"),
            ],
            "output": out_dir / "alpha1-local-gate-verify-output.txt",
        },
        {
            "label": "diff_check",
            "command": [sys.executable, "deploy/alpha1_worktree_check.py"],
            "display": "python deploy/alpha1_worktree_check.py",
            "output": out_dir / "git-diff-check-output.txt",
        },
    ]


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Run local alpha1 release gate.")
    parser.add_argument("--root", default=root, type=Path)
    parser.add_argument("--out-dir", type=Path, help="Local gate output directory")
    parser.add_argument("--dry-run", action="store_true", help="Write planned commands without executing them")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    out_dir = (args.out_dir or (root / "alpha1-local-gate" / timestamp())).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "created_at_utc": timestamp(),
        "status": "dry_run" if args.dry_run else "running",
        "local_acceptance_acceptable": False,
        "artifacts": {
            "local_acceptance": str(out_dir / "alpha1-local-acceptance.json"),
            "manifest": str(out_dir / "local-gate-manifest.json"),
        },
        "commands": [],
    }
    if args.dry_run:
        manifest["local_blocker"] = "--dry-run writes planned commands but does not execute local gate evidence"

    failed = False
    command_specs = build_commands(root, out_dir)
    deferred_specs = [spec for spec in command_specs if spec["label"] == "local_gate_manifest_verify"]
    immediate_specs = [spec for spec in command_specs if spec["label"] != "local_gate_manifest_verify"]
    for spec in immediate_specs:
        command_env = os.environ.copy()
        if spec.get("env"):
            command_env.update(spec["env"])
        rc = run_command(
            label=spec["label"],
            command=spec["command"],
            output_path=spec["output"],
            cwd=spec.get("cwd", root),
            env=command_env,
            manifest=manifest,
            dry_run=args.dry_run,
            display=spec.get("display"),
        )
        if rc != 0:
            failed = True

    manifest["status"] = "dry_run" if args.dry_run else ("failed" if failed else "passed")
    manifest["local_acceptance_acceptable"] = not args.dry_run and not failed
    if failed:
        manifest["local_blocker"] = "one or more local gate commands failed"
    elif not args.dry_run:
        manifest.pop("local_blocker", None)

    manifest_path = out_dir / "local-gate-manifest.json"
    deferred_entries: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for spec in deferred_specs:
        output_path = Path(spec["output"])
        entry: dict[str, Any] = {
            "label": spec["label"],
            "command": spec["command"],
            "display": spec.get("display") or command_text(spec["command"]),
            "output": str(output_path),
            "dry_run": args.dry_run,
        }
        if not args.dry_run:
            entry["returncode"] = 0
            output_path.write_text(
                "PENDING: local gate manifest self-verification has not run yet.\n",
                encoding="utf-8",
            )
        manifest["commands"].append(entry)
        deferred_entries.append((spec, entry))
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    for spec, entry in deferred_entries:
        command_env = os.environ.copy()
        bootstrap_command = [*spec["command"], "--allow-pending-local-gate-self-check"]
        bootstrap_rc = run_command(
            label=spec["label"],
            command=bootstrap_command,
            output_path=spec["output"],
            cwd=spec.get("cwd", root),
            env=command_env,
            manifest=manifest,
            dry_run=args.dry_run,
            display=f"{command_text(spec['command'])} --allow-pending-local-gate-self-check",
            planned_entry=entry,
        )
        if bootstrap_rc != 0:
            failed = True
            continue
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        strict_rc = run_command(
            label=spec["label"],
            command=spec["command"],
            output_path=spec["output"],
            cwd=spec.get("cwd", root),
            env=command_env,
            manifest=manifest,
            dry_run=args.dry_run,
            display=spec.get("display"),
            planned_entry=entry,
        )
        if strict_rc != 0:
            failed = True
    manifest["status"] = "dry_run" if args.dry_run else ("failed" if failed else "passed")
    manifest["local_acceptance_acceptable"] = not args.dry_run and not failed
    if failed:
        manifest["local_blocker"] = "one or more local gate commands failed"
    elif not args.dry_run:
        manifest.pop("local_blocker", None)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if failed:
        print(f"Alpha1 local gate failed. See {out_dir}")
        return 1
    print(f"Alpha1 local gate {manifest['status']}. See {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
