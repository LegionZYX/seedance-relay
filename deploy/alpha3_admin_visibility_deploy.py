#!/usr/bin/env python3
"""Deploy alpha3 admin visibility lite to the production Docker Compose host.

The script is intentionally conservative:
- dry-run by default;
- backs up SQLite before rebuilding containers;
- verifies local container health before returning success;
- never accepts passwords as command-line arguments.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path


DEFAULT_HOST = "23.254.224.213"
DEFAULT_USER = "root"
DEFAULT_APP_DIR = "/opt/seedance-relay"
DEFAULT_BRANCH = "alpha3"
DEFAULT_DOMAIN = "https://seedance3.eu"


def quote(value: str) -> str:
    return shlex.quote(value)


def ssh_base(args: argparse.Namespace) -> list[str]:
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "PreferredAuthentications=publickey",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"ConnectTimeout={args.connect_timeout}",
    ]
    if args.key:
        cmd.extend(["-i", args.key])
    cmd.append(f"{args.user}@{args.host}")
    return cmd


def run_local(cmd: list[str], *, execute: bool) -> None:
    display = " ".join(quote(part) for part in cmd)
    print(f"+ {display}")
    if not execute:
        return
    subprocess.run(cmd, check=True)


def run_remote(args: argparse.Namespace, script: str) -> None:
    cmd = ssh_base(args) + [script]
    run_local(cmd, execute=args.execute)


def build_remote_script(args: argparse.Namespace) -> str:
    app_dir = quote(args.app_dir)
    branch = quote(args.branch)
    remote = quote(args.remote)
    backup_dir = quote(args.backup_dir)
    expected_commit = quote(args.expected_commit) if args.expected_commit else ""
    expected_check = ""
    if expected_commit:
        expected_check = f"""
actual="$(git rev-parse HEAD)"
case "$actual" in
  {expected_commit}*) ;;
  *)
  echo "ERROR: deployed commit $actual does not match expected prefix {expected_commit}" >&2
  exit 20
  ;;
esac
"""

    return f"""set -euo pipefail
cd {app_dir}
echo "== host =="
hostname
echo "== preflight =="
test -f docker-compose.relay.yml
test -f .env.relay
mkdir -p {backup_dir}
backup="{args.backup_dir.rstrip('/')}/relay.$(date -u +%Y%m%dT%H%M%SZ).sqlite"
if [ -f data/relay.sqlite ]; then
  cp data/relay.sqlite "$backup"
  chmod 600 "$backup"
  echo "backup=$backup"
else
  echo "WARN: data/relay.sqlite not found before deploy"
fi
echo "== git update =="
git fetch {remote} {branch}
git checkout {branch}
git reset --hard FETCH_HEAD
{expected_check}
echo "commit=$(git rev-parse HEAD)"
echo "== compose build/up =="
docker compose -f docker-compose.relay.yml up -d --build
echo "== compose ps =="
docker compose -f docker-compose.relay.yml ps
echo "== local health =="
curl -fsS http://127.0.0.1:8002/health >/tmp/seedance-relay-health.json
cat /tmp/seedance-relay-health.json
echo
curl -fsS http://127.0.0.1:8012/health >/tmp/seedance-runtime-health.json
cat /tmp/seedance-runtime-health.json
echo
"""


def verify_external(args: argparse.Namespace) -> None:
    commands = [
        [
            "curl",
            "-fsS",
            f"{args.domain.rstrip('/')}/health",
        ],
        [
            "curl",
            "-fsS",
            f"{args.domain.rstrip('/')}/v1/docs/api.md",
        ],
    ]
    for cmd in commands:
        run_local(cmd, execute=args.execute)
    admin_key = os.getenv("ADMIN_KEY", "").strip()
    if not admin_key:
        print("WARN: ADMIN_KEY is not set; skipping external admin smoke.")
        return
    for path in ("/admin/tasks?limit=5", "/admin/request-logs?limit=5"):
        run_local(
            [
                "curl",
                "-fsS",
                "-H",
                f"x-admin-key: {admin_key}",
                f"{args.domain.rstrip('/')}{path}",
            ],
            execute=args.execute,
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--user", default=DEFAULT_USER)
    parser.add_argument("--key", default=os.getenv("SEEDANCE_DEPLOY_KEY", ""))
    parser.add_argument("--app-dir", default=DEFAULT_APP_DIR)
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--domain", default=DEFAULT_DOMAIN)
    parser.add_argument("--backup-dir", default=f"{DEFAULT_APP_DIR}/data/backups")
    parser.add_argument("--expected-commit", default="")
    parser.add_argument("--connect-timeout", type=int, default=10)
    parser.add_argument("--execute", action="store_true", help="actually run remote deploy")
    parser.add_argument(
        "--external-only",
        action="store_true",
        help="only run external HTTP smoke checks",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if not args.execute:
        print("DRY RUN: pass --execute to run commands.")
    if not args.external_only:
        script = build_remote_script(args)
        run_remote(args, script)
    verify_external(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
