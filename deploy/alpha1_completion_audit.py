#!/usr/bin/env python3
"""Final alpha1 completion audit.

This is the last human-safe gate: local evidence can pass while the release is
still incomplete. A complete alpha1 release requires a verified external
collector manifest from a stable HTTPS Relay domain.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path
from typing import Any, Callable

from alpha1_verify_evidence import (
    EvidenceReport,
    resolve_manifest_artifact,
    verify_local_gate_manifest,
    verify_manifest,
)


def run_verifier_check(
    label: str,
    path: Path | None,
    verifier: Callable[[Path, EvidenceReport], None],
) -> dict[str, Any]:
    if path is None:
        return {
            "status": "missing",
            "path": None,
            "failures": [f"{label} path is missing"],
            "warnings": [],
            "output": "",
        }
    if not path.exists():
        return {
            "status": "missing",
            "path": str(path),
            "failures": [f"{label} file is missing: {path}"],
            "warnings": [],
            "output": "",
        }

    report = EvidenceReport()
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        verifier(path, report)
    return {
        "status": "failed" if report.failures else "passed",
        "path": str(path),
        "failures": report.failures,
        "warnings": report.warnings,
        "output": buffer.getvalue(),
    }


def write_summary(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def compare_manifest_local_gate(manifest_path: Path, local_gate_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "status": "failed",
            "path": str(local_gate_path),
            "manifest_path": str(manifest_path),
            "failures": [f"could not read external manifest for local gate comparison: {exc}"],
            "warnings": [],
            "output": "",
        }
    artifacts = payload.get("artifacts") if isinstance(payload, dict) else None
    declared = artifacts.get("local_gate_manifest") if isinstance(artifacts, dict) else None
    if not declared:
        return {
            "status": "failed",
            "path": str(local_gate_path),
            "manifest_path": str(manifest_path),
            "failures": ["external manifest missing artifacts.local_gate_manifest"],
            "warnings": [],
            "output": "",
        }
    declared_path = resolve_manifest_artifact(manifest_path, str(declared)).resolve()
    provided_path = local_gate_path.resolve()
    if declared_path != provided_path:
        try:
            declared_bytes = declared_path.read_bytes()
            provided_bytes = provided_path.read_bytes()
        except Exception as exc:
            return {
                "status": "failed",
                "path": str(provided_path),
                "manifest_path": str(manifest_path),
                "declared_path": str(declared_path),
                "failures": [f"could not compare local gate manifests: {exc}"],
                "warnings": [],
                "output": "",
            }
        if declared_bytes == provided_bytes:
            return {
                "status": "passed",
                "path": str(provided_path),
                "manifest_path": str(manifest_path),
                "declared_path": str(declared_path),
                "content_match": True,
                "failures": [],
                "warnings": [],
                "output": "",
            }
        return {
            "status": "failed",
            "path": str(provided_path),
            "manifest_path": str(manifest_path),
            "declared_path": str(declared_path),
            "failures": [
                "local gate manifest does not match external manifest artifact: "
                f"{provided_path} != {declared_path}"
            ],
            "warnings": [],
            "output": "",
        }
    return {
        "status": "passed",
        "path": str(provided_path),
        "manifest_path": str(manifest_path),
        "declared_path": str(declared_path),
        "failures": [],
        "warnings": [],
        "output": "",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit whether alpha1 is complete.")
    parser.add_argument("--manifest", help="Final external manifest.json from alpha1_collect_evidence.py")
    parser.add_argument("--local-gate-manifest", help="Local local-gate-manifest.json from alpha1_local_gate.py")
    parser.add_argument("--summary-json", type=Path, help="Write machine-readable audit summary JSON")
    args = parser.parse_args(argv)

    checks: dict[str, Any] = {}
    if args.local_gate_manifest:
        checks["local_gate"] = run_verifier_check(
            "local gate manifest",
            Path(args.local_gate_manifest),
            verify_local_gate_manifest,
        )
    elif not args.manifest:
        checks["local_gate"] = {
            "status": "missing",
            "path": None,
            "failures": ["local gate evidence missing"],
            "warnings": [],
            "output": "",
        }

    if args.manifest:
        checks["external_manifest"] = run_verifier_check(
            "external release manifest",
            Path(args.manifest),
            lambda path, report: verify_manifest(
                path,
                report,
                allow_pending_completion_audit=True,
            ),
        )
        if args.local_gate_manifest:
            checks["local_gate_match"] = compare_manifest_local_gate(
                Path(args.manifest),
                Path(args.local_gate_manifest),
            )
    else:
        checks["external_manifest"] = {
            "status": "missing",
            "path": None,
            "failures": ["external deployment evidence missing"],
            "warnings": [],
            "output": "",
        }

    failures: list[str] = []
    for check_name, check in checks.items():
        for failure in check.get("failures", []):
            failures.append(f"{check_name}: {failure}")

    release_complete = checks["external_manifest"]["status"] == "passed" and not failures
    local_gate_match = (
        checks.get("local_gate_match", {}).get("status") == "passed"
        if "local_gate_match" in checks
        else None
    )
    summary = {
        "status": "complete" if release_complete else "incomplete",
        "release_complete": release_complete,
        "checks": checks,
        "failures": failures,
    }
    if local_gate_match is not None:
        summary["local_gate_match"] = local_gate_match
    write_summary(args.summary_json, summary)

    if release_complete:
        print("Alpha1 completion audit passed")
        print(f"Verified external manifest: {checks['external_manifest']['path']}")
        return 0

    if checks["external_manifest"]["status"] == "missing":
        print("Alpha1 completion audit failed: external deployment evidence missing")
    else:
        print("Alpha1 completion audit failed")
    for failure in failures:
        print(f"- {failure}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
