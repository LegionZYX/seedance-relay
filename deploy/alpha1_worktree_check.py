#!/usr/bin/env python3
"""Alpha1 worktree hygiene checks.

`git diff --check` does not inspect staged or untracked files by default.
Alpha1 adds release scripts, docs, and runtime files as new files, so the local
gate checks unstaged diffs, staged diffs, and untracked text files before
release evidence is accepted.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


MAX_TEXT_FILE_BYTES = 5 * 1024 * 1024


def run_git_diff_check(root: Path) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", "diff", "--check"],
        cwd=str(root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return proc.returncode, proc.stdout


def run_git_cached_diff_check(root: Path) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", "diff", "--cached", "--check"],
        cwd=str(root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return proc.returncode, proc.stdout


def untracked_files(root: Path) -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=str(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"git ls-files failed: {stderr.strip()}")
    names = [name for name in proc.stdout.decode("utf-8", errors="surrogateescape").split("\0") if name]
    return [root / name for name in names]


def is_text_payload(payload: bytes) -> bool:
    return b"\0" not in payload


def check_untracked_text_file(path: Path) -> list[str]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        return [f"{path}: could not read untracked file: {exc}"]
    if len(payload) > MAX_TEXT_FILE_BYTES or not is_text_payload(payload):
        return []
    failures: list[str] = []
    if payload and not payload.endswith((b"\n", b"\r")):
        failures.append(f"{path}: missing newline at end of untracked file")
    text = payload.decode("utf-8", errors="replace")
    for line_no, line in enumerate(text.splitlines(keepends=True), start=1):
        body = line.rstrip("\r\n")
        if body.endswith((" ", "\t")):
            failures.append(f"{path}:{line_no}: trailing whitespace in untracked file")
    return failures


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Run alpha1 worktree checks.")
    parser.add_argument("--root", default=root, type=Path)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    failures: list[str] = []
    diff_rc, diff_output = run_git_diff_check(root)
    if diff_output:
        print(diff_output, end="" if diff_output.endswith("\n") else "\n")
    if diff_rc != 0:
        failures.append("git diff --check failed")
    cached_diff_rc, cached_diff_output = run_git_cached_diff_check(root)
    if cached_diff_output:
        print(cached_diff_output, end="" if cached_diff_output.endswith("\n") else "\n")
    if cached_diff_rc != 0:
        failures.append("git diff --cached --check failed")

    try:
        files = untracked_files(root)
    except RuntimeError as exc:
        print(str(exc))
        return 1
    for path in files:
        failures.extend(check_untracked_text_file(path))

    for failure in failures:
        print(failure)
    if failures:
        print(f"Alpha1 worktree check failed: {len(failures)} issue(s)")
        return 1
    print(f"Alpha1 worktree check passed: {len(files)} untracked file(s) inspected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
