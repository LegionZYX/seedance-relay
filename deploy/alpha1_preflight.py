#!/usr/bin/env python3
"""Alpha1 deployment preflight checks.

This script is intentionally stdlib-only so it can run on a fresh deployment
host before Docker/Caddy reloads. It checks local files and, when available,
the live SQLite database and Caddy binary.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


REQUIRED_ENV = (
    "ADMIN_KEY",
    "RUNTIME_INTERNAL_TOKEN",
    "UPSTREAM_BASE_URL",
    "PUBLIC_DOMAIN",
    "DB_PATH",
    "CONTROL_PLANE_BASE_URL",
    "VIDEO_PERSIST_MODE",
)
IAM_REQUIRED_ENV = (
    "UPSTREAM_ENDPOINT_ID",
    "UPSTREAM_ENDPOINT_API_KEY",
    "BYTEPLUS_ACCESS_KEY_ID",
    "BYTEPLUS_ACCESS_KEY_SECRET",
)

PLACEHOLDER_FRAGMENTS = (
    "replace-this",
    "xxxxxxxx",
    "your_",
    "<your",
    "example.invalid",
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


class Report:
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


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def looks_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(fragment in lowered for fragment in PLACEHOLDER_FRAGMENTS)


def public_domain_error(value: str) -> str | None:
    if not value:
        return None
    parsed = urlparse(value if "://" in value else f"//{value}")
    host = (parsed.hostname or "").lower()
    if parsed.scheme:
        return "PUBLIC_DOMAIN must be a bare hostname, not a URL with scheme"
    if not host:
        return "PUBLIC_DOMAIN must contain a hostname"
    if parsed.username or parsed.password:
        return "PUBLIC_DOMAIN must not include username or password"
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        return "PUBLIC_DOMAIN must not include a path, query, or fragment"
    if host in LOCAL_HOSTS or host.endswith(".localhost"):
        return "PUBLIC_DOMAIN must be an external Relay hostname, not localhost"
    if host in {suffix.removeprefix(".") for suffix in RESERVED_DOMAIN_SUFFIXES} or any(
        host.endswith(suffix) for suffix in RESERVED_DOMAIN_SUFFIXES
    ):
        return "PUBLIC_DOMAIN must be a real Relay hostname, not an example domain"
    if any(host.endswith(suffix) for suffix in TUNNEL_HOST_SUFFIXES):
        return "PUBLIC_DOMAIN must be a stable Relay hostname, not a tunnel URL"
    return None


def check_env(path: Path, report: Report) -> dict[str, str]:
    if not path.exists():
        report.fail(f"env file missing: {path}")
        return {}
    values = parse_env(path)
    for key in REQUIRED_ENV:
        value = values.get(key, "")
        if not value:
            report.fail(f"{key} is missing or empty")
        elif looks_placeholder(value):
            report.fail(f"{key} still looks like a placeholder")
    if values.get("VIDEO_PERSIST_MODE") != "proxy_only":
        report.fail("VIDEO_PERSIST_MODE must be proxy_only for alpha1")
    upstream_auth_mode = (values.get("UPSTREAM_AUTH_MODE") or "api_key").strip().lower()
    if upstream_auth_mode in {"iam", "aksk", "access_key"}:
        for key in IAM_REQUIRED_ENV:
            value = values.get(key, "")
            if not value:
                report.fail(f"{key} is required when UPSTREAM_AUTH_MODE=iam")
            elif looks_placeholder(value):
                report.fail(f"{key} still looks like a placeholder")
        if not values.get("MODELARK_ASSET_GROUP_ID") and values.get("MODELARK_ASSET_AUTO_CREATE_GROUP", "true").lower() not in {"1", "true", "yes", "on"}:
            report.fail("MODELARK_ASSET_GROUP_ID is required when automatic asset group creation is disabled")
        if not values.get("MODELARK_ASSET_GROUP_ID"):
            report.warn("MODELARK_ASSET_GROUP_ID is empty; Relay will auto-create/cache a group on first asset registration")
    elif upstream_auth_mode in {"api_key", "bearer", ""}:
        value = values.get("UPSTREAM_API_KEY", "")
        if not value:
            report.fail("UPSTREAM_API_KEY is missing or empty")
        elif looks_placeholder(value):
            report.fail("UPSTREAM_API_KEY still looks like a placeholder")
    else:
        report.fail("UPSTREAM_AUTH_MODE must be api_key or iam")
    public_domain_problem = public_domain_error(values.get("PUBLIC_DOMAIN", ""))
    if public_domain_problem:
        report.fail(public_domain_problem)
    if values.get("ADMIN_KEY") and values.get("ADMIN_KEY") == values.get("RUNTIME_INTERNAL_TOKEN"):
        report.fail("ADMIN_KEY and RUNTIME_INTERNAL_TOKEN must be different secrets")
    for key in ("ADMIN_KEY", "RUNTIME_INTERNAL_TOKEN"):
        value = values.get(key, "")
        if value and not looks_placeholder(value) and len(value) < 24:
            report.warn(f"{key} is shorter than 24 characters")
    report.ok(f"checked env file: {path}")
    return values


def active_lines(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def check_caddy_snippet(path: Path, report: Report) -> None:
    if not path.exists():
        report.fail(f"Caddy snippet missing: {path}")
        return
    text = path.read_text(encoding="utf-8")
    lines = active_lines(path)
    required = (
        "@runtime_models path /v1/models",
        "@runtime_video_estimate path /v1/videos/estimate",
        "method POST",
        "path /v1/videos",
        "header {",
    )
    for needle in required:
        if needle not in text:
            report.fail(f"Caddy snippet missing: {needle}")
    if lines.count("flush_interval -1") < 1:
        report.fail("Caddy snippet must keep flush_interval -1 active for streaming proxies")
    if "method GET POST" in text:
        report.fail("Caddy snippet must route only POST /v1/videos to the Go runtime")
    if "runtime_video_detail" in text or "runtime_video_content" in text:
        report.fail("Caddy snippet must route task reads and content through the Relay service")
    if "path /v1/videos*" in text or "path_regexp runtime_all_videos" in text:
        report.fail("Caddy snippet must not route /v1/videos* with a broad wildcard")
    report.ok(f"checked Caddy snippet: {path}")


def check_compose(path: Path, report: Report) -> None:
    if not path.exists():
        report.fail(f"compose file missing: {path}")
        return
    text = path.read_text(encoding="utf-8")
    required = (
        "seedance-relay:",
        "seedance-runtime:",
        'CONTROL_PLANE_BASE_URL: "http://seedance-relay:8002"',
        "condition: service_healthy",
        '"127.0.0.1:8002:8002"',
        '"127.0.0.1:8012:8012"',
    )
    for needle in required:
        if needle not in text:
            report.fail(f"compose file missing: {needle}")
    report.ok(f"checked compose file: {path}")


def check_sqlite(path: Path, report: Report) -> None:
    if not path.exists():
        report.warn(f"SQLite DB does not exist yet, skipped pragma check: {path}")
        return
    conn = sqlite3.connect(path)
    try:
        journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        busy_timeout = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
    finally:
        conn.close()
    if journal_mode != "wal":
        report.fail(f"SQLite journal_mode is {journal_mode!r}, expected 'wal'")
    if busy_timeout < 5000:
        report.fail(f"SQLite busy_timeout is {busy_timeout}, expected >= 5000")
    report.ok(f"checked SQLite pragmas: {path}")


def validate_caddyfile(path: Path | None, report: Report) -> None:
    if path is None:
        report.warn("no --caddyfile provided; run caddy validate on the deployment host")
        return
    if not path.exists():
        report.fail(f"Caddyfile missing: {path}")
        return
    caddy = shutil.which("caddy")
    if not caddy:
        report.warn("caddy binary not found; cannot validate Caddyfile locally")
        return
    proc = subprocess.run(
        [caddy, "validate", "--config", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if proc.returncode != 0:
        report.fail(f"caddy validate failed:\n{proc.stdout.strip()}")
    else:
        report.ok(f"caddy validate passed: {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run alpha1 deployment preflight checks.")
    parser.add_argument("--root", default=Path(__file__).resolve().parents[1], type=Path)
    parser.add_argument("--env", default=None, type=Path)
    parser.add_argument("--compose", default=None, type=Path)
    parser.add_argument("--caddy-snippet", default=None, type=Path)
    parser.add_argument("--caddyfile", default=None, type=Path)
    parser.add_argument("--skip-db", action="store_true")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    env_path = args.env or (root / ".env.relay")
    compose_path = args.compose or (root / "docker-compose.relay.yml")
    caddy_snippet_path = args.caddy_snippet or (root / "deploy" / "caddy_video.snippet")

    report = Report()
    env_values = check_env(env_path, report)
    check_compose(compose_path, report)
    check_caddy_snippet(caddy_snippet_path, report)
    validate_caddyfile(args.caddyfile, report)
    if not args.skip_db and env_values.get("DB_PATH"):
        check_sqlite(Path(env_values["DB_PATH"]), report)

    if report.failures:
        print(f"Preflight failed: {len(report.failures)} failure(s), {len(report.warnings)} warning(s)")
        return 1
    print(f"Preflight passed: {len(report.warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
