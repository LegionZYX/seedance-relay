#!/usr/bin/env python3
"""Collect alpha1 deployment evidence into one release directory."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DEFAULT_API_KEY_ENV = "ALPHA1_CUSTOMER_API_KEY"
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
SENSITIVE_REPLACEMENTS = (
    (re.compile(r"bytepluses\.com", re.I), "<provider-domain>"),
    (re.compile(r"byteplus", re.I), "<provider>"),
    (re.compile(r"volces\.com", re.I), "<provider-domain>"),
    (re.compile(r"\bark-[A-Za-z0-9._-]+"), "<redacted-upstream-key>"),
    (re.compile(r"\bsk[-_][A-Za-z0-9][A-Za-z0-9_-]{8,}\b"), "<redacted-relay-key>"),
    (re.compile(r"\bauthorization\s*:\s*bearer\s+[A-Za-z0-9._-]{8,}", re.I), "Authorization: Bearer <redacted>"),
    (re.compile(r"\brelay_session=[^;\s]+", re.I), "relay_session=<redacted>"),
)


def timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def resolve_api_key(args: argparse.Namespace) -> str:
    if args.api_key:
        return args.api_key
    if args.api_key_file:
        return Path(args.api_key_file).read_text(encoding="utf-8").strip()
    return os.getenv(args.api_key_env, "").strip()


def masked_command(command: list[str], api_key: str) -> list[str]:
    if not api_key:
        return command
    return ["<redacted-api-key>" if item == api_key else item for item in command]


def redact_text(text: str, api_key: str) -> str:
    if not api_key:
        redacted = text
    else:
        redacted = text.replace(api_key, "<redacted-api-key>")
    for pattern, replacement in SENSITIVE_REPLACEMENTS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def release_base_url_error(base_url: str) -> str | None:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        return "Final release evidence requires an https:// base URL."
    if not host:
        return "Final release evidence requires a hostname in --base-url."
    if parsed.username or parsed.password:
        return "Final release evidence requires --base-url without username or password."
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        return "Final release evidence requires a bare origin in --base-url, without path, query, or fragment."
    if host in LOCAL_HOSTS or host.endswith(".localhost"):
        return "Final release evidence requires an external domain, not localhost."
    if host in {suffix.removeprefix(".") for suffix in RESERVED_DOMAIN_SUFFIXES} or any(
        host.endswith(suffix) for suffix in RESERVED_DOMAIN_SUFFIXES
    ):
        return "Final release evidence requires a real Relay domain, not an example domain."
    if any(host.endswith(suffix) for suffix in TUNNEL_HOST_SUFFIXES):
        return "Final release evidence requires a stable Relay domain, not a tunnel URL."
    return None


def run_command(
    label: str,
    command: list[str],
    output_path: Path,
    *,
    cwd: Path,
    api_key: str,
    manifest: dict[str, Any],
    dry_run: bool,
    write_manifest_path: Path | None = None,
) -> int:
    manifest["commands"].append(
        {
            "label": label,
            "command": masked_command(command, api_key),
            "output": str(output_path),
            "dry_run": dry_run,
        }
    )
    if write_manifest_path is not None:
        write_manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if dry_run:
        output_path.write_text("DRY RUN\n" + " ".join(masked_command(command, api_key)) + "\n", encoding="utf-8")
        return 0
    proc = subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output_path.write_text(redact_text(proc.stdout, api_key), encoding="utf-8")
    manifest["commands"][-1]["returncode"] = proc.returncode
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Collect alpha1 deployment evidence.")
    parser.add_argument("--root", default=root, type=Path)
    parser.add_argument("--base-url", required=True, help="Relay base URL, for example https://video.customer-domain.com")
    parser.add_argument("--video-id", required=True, help="Existing succeeded video id for proxy playback proof")
    parser.add_argument("--api-key", help="Existing customer Relay API key; prefer --api-key-env or --api-key-file")
    parser.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV, help=f"Environment variable containing the API key, default {DEFAULT_API_KEY_ENV}")
    parser.add_argument("--api-key-file", help="File containing the customer Relay API key")
    parser.add_argument("--caddyfile", default="/etc/caddy/Caddyfile")
    parser.add_argument(
        "--local-acceptance-json",
        help="Final release required alpha1-local-acceptance.json from the local live smoke; optional only for --dry-run or --no-browser rehearsal",
    )
    parser.add_argument(
        "--local-gate-manifest",
        help="Final release required local-gate-manifest.json from alpha1_local_gate.py; optional only for --dry-run or --no-browser rehearsal",
    )
    parser.add_argument("--out-dir", type=Path, help="Evidence output directory")
    parser.add_argument("--no-browser", action="store_true", help="Skip Playwright browser probe; not acceptable for final release")
    parser.add_argument("--dry-run", action="store_true", help="Write planned commands without executing them")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    api_key = resolve_api_key(args)
    if not api_key:
        print(f"Missing API key. Set {args.api_key_env}, use --api-key-file, or pass --api-key.", file=sys.stderr)
        return 2
    base_url_error = release_base_url_error(args.base_url)
    if base_url_error and not (args.dry_run or args.no_browser):
        print(base_url_error, file=sys.stderr)
        return 2
    if not args.local_acceptance_json and not (args.dry_run or args.no_browser):
        print(
            "Missing --local-acceptance-json. Final release evidence requires "
            "alpha1-local-acceptance.json from the local live smoke.",
            file=sys.stderr,
        )
        return 2
    if not args.local_gate_manifest and not (args.dry_run or args.no_browser):
        print(
            "Missing --local-gate-manifest. Final release evidence requires "
            "local-gate-manifest.json from alpha1_local_gate.py.",
            file=sys.stderr,
        )
        return 2

    out_dir = args.out_dir or (root / "alpha1-evidence" / timestamp())
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "preflight": out_dir / "alpha1-preflight-output.txt",
        "caddy": out_dir / "caddy-validate-output.txt",
        "probe": out_dir / "alpha1-probe-evidence.json",
        "probe_output": out_dir / "alpha1-probe-output.txt",
        "screenshot": out_dir / "alpha1-browser-video.png",
        "verify": out_dir / "alpha1-verify-output.txt",
        "completion_output": out_dir / "alpha1-completion-output.txt",
        "completion_summary": out_dir / "alpha1-completion-summary.json",
        "manifest": out_dir / "manifest.json",
    }
    if args.local_acceptance_json:
        paths["local_acceptance"] = out_dir / "alpha1-local-acceptance.json"
        source = Path(args.local_acceptance_json)
        if args.dry_run:
            paths["local_acceptance"].write_text(
                f"DRY RUN: would copy {source}\n",
                encoding="utf-8",
            )
        elif not source.exists():
            print(f"Missing --local-acceptance-json file: {source}", file=sys.stderr)
            return 2
        else:
            shutil.copyfile(source, paths["local_acceptance"])
    if args.local_gate_manifest:
        source = Path(args.local_gate_manifest)
        target_dir = out_dir / "alpha1-local-gate"
        paths["local_gate_manifest"] = target_dir / source.name
        if args.dry_run:
            target_dir.mkdir(parents=True, exist_ok=True)
            paths["local_gate_manifest"].write_text(
                f"DRY RUN: would copy local gate directory {source.parent}\n",
                encoding="utf-8",
            )
        elif not source.exists():
            print(f"Missing --local-gate-manifest file: {source}", file=sys.stderr)
            return 2
        else:
            shutil.copytree(source.parent, target_dir, dirs_exist_ok=True)

    release_blockers: list[str] = []
    if args.dry_run:
        release_blockers.append("--dry-run writes planned commands but does not execute release evidence")
    if args.no_browser:
        release_blockers.append("--no-browser skips browser screenshot evidence")
    if not args.local_acceptance_json:
        release_blockers.append("--local-acceptance-json is required for final release evidence")
    if not args.local_gate_manifest:
        release_blockers.append("--local-gate-manifest is required for final release evidence")

    manifest: dict[str, Any] = {
        "created_at_utc": timestamp(),
        "base_url": args.base_url,
        "video_id": args.video_id,
        "caddyfile": str(args.caddyfile),
        "browser_required": not args.no_browser,
        "release_acceptable": not release_blockers,
        "artifacts": {key: str(value) for key, value in paths.items() if key != "manifest"},
        "commands": [],
    }
    if release_blockers:
        manifest["release_blocker"] = "; ".join(release_blockers)

    commands: list[tuple[str, list[str], Path]] = [
        (
            "deploy_preflight",
            [
                sys.executable,
                "deploy/alpha1_preflight.py",
                "--caddyfile",
                str(args.caddyfile),
            ],
            paths["preflight"],
        ),
        (
            "caddy_validate",
            [
                "caddy",
                "validate",
                "--config",
                str(args.caddyfile),
            ],
            paths["caddy"],
        ),
    ]

    probe_command = [
        sys.executable,
        "deploy/alpha1_probe.py",
        "--base-url",
        args.base_url,
        "--api-key",
        api_key,
        "--video-id",
        args.video_id,
        "--json-output",
        str(paths["probe"]),
    ]
    if not args.no_browser:
        probe_command.extend(["--browser", "--screenshot", str(paths["screenshot"])])
    commands.append(("external_probe", probe_command, paths["probe_output"]))

    if args.no_browser:
        paths["verify"].write_text(
            "SKIPPED: --no-browser was used, so browser screenshot evidence is missing.\n"
            "This evidence directory is not release acceptable.\n",
            encoding="utf-8",
        )
        manifest["commands"].append(
            {
                "command": ["deploy/alpha1_verify_evidence.py", "<skipped: no browser screenshot>"],
                "label": "evidence_verify",
                "output": str(paths["verify"]),
                "skipped": True,
                "reason": manifest["release_blocker"],
            }
        )
    else:
        commands.append(
            (
                "evidence_verify",
                [
                    sys.executable,
                    "deploy/alpha1_verify_evidence.py",
                    "--base-url",
                    args.base_url,
                    "--video-id",
                    args.video_id,
                    "--probe-json",
                    str(paths["probe"]),
                    "--probe-output",
                    str(paths["probe_output"]),
                    "--screenshot",
                    str(paths["screenshot"]),
                    "--preflight-output",
                    str(paths["preflight"]),
                    "--caddy-output",
                    str(paths["caddy"]),
                ]
                + (
                    ["--local-acceptance-json", str(paths["local_acceptance"])]
                    if "local_acceptance" in paths
                    else []
                )
                + (
                    ["--local-gate-manifest", str(paths["local_gate_manifest"])]
                    if "local_gate_manifest" in paths
                    else []
                ),
                paths["verify"],
            )
        )

    completion_command = [
        sys.executable,
        "deploy/alpha1_completion_audit.py",
        "--manifest",
        str(paths["manifest"]),
        "--summary-json",
        str(paths["completion_summary"]),
    ]
    if "local_gate_manifest" in paths:
        completion_command.extend(["--local-gate-manifest", str(paths["local_gate_manifest"])])
    if args.dry_run:
        commands.append(("completion_audit", completion_command, paths["completion_output"]))

    failed = False
    for label, command, output_path in commands:
        rc = run_command(
            label,
            command,
            output_path,
            cwd=root,
            api_key=api_key,
            manifest=manifest,
            dry_run=args.dry_run,
        )
        if rc != 0:
            failed = True
            if not args.dry_run:
                break

    manifest["status"] = "dry_run" if args.dry_run else ("failed" if failed else "passed")
    if failed:
        release_blockers.append("one or more evidence collection commands failed")
    manifest["release_acceptable"] = not release_blockers and manifest["status"] == "passed"
    if release_blockers:
        manifest["release_blocker"] = "; ".join(release_blockers)
    else:
        manifest.pop("release_blocker", None)
    paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.dry_run and not failed and not args.no_browser:
        rc = run_command(
            "completion_audit",
            completion_command,
            paths["completion_output"],
            cwd=root,
            api_key=api_key,
            manifest=manifest,
            dry_run=False,
            write_manifest_path=paths["manifest"],
        )
        if rc != 0:
            failed = True
            release_blockers.append("completion audit failed")
            manifest["status"] = "failed"
            manifest["release_acceptable"] = False
            manifest["release_blocker"] = "; ".join(release_blockers)
        paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if failed:
        print(f"Alpha1 evidence collection failed. See {out_dir}")
        return 1
    print(f"Alpha1 evidence collection {manifest['status']}. See {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

