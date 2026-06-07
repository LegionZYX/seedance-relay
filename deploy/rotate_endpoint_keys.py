"""Rotate customer BytePlus endpoint API keys before expiry.

This script is designed for cron:
  python deploy/rotate_endpoint_keys.py

It keeps IAM credentials server-side and only stores the generated endpoint
API key in users.byteplus_api_key. users.note stores non-secret metadata.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import relay_server  # noqa: E402


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _load_note(raw: str | None) -> dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {"legacy_note": raw}
    return parsed if isinstance(parsed, dict) else {}


def _dump_note(note: dict[str, Any]) -> str:
    return json.dumps(note, ensure_ascii=True, sort_keys=True)


def _endpoint_ids_from_note(note: dict[str, Any]) -> list[str]:
    raw_map = note.get("byteplus_endpoint_map")
    if isinstance(raw_map, dict):
        endpoint_ids = [
            str(endpoint_id or "").strip()
            for endpoint_id in raw_map.values()
            if str(endpoint_id or "").strip()
        ]
        if endpoint_ids:
            return endpoint_ids
    endpoint_id = str(note.get("byteplus_endpoint_id") or "").strip()
    return [endpoint_id] if endpoint_id else []


def _endpoint_target_for_note(note: dict[str, Any]) -> str | list[str]:
    endpoint_ids = _endpoint_ids_from_note(note)
    if len(endpoint_ids) == 1 and not isinstance(note.get("byteplus_endpoint_map"), dict):
        return endpoint_ids[0]
    return endpoint_ids


def get_endpoint_api_key(endpoint_id: str | list[str], duration_seconds: int) -> dict[str, Any]:
    """Provider adapter.

    Tests monkeypatch this function. Production uses the same signed BytePlus
    OpenAPI helper used by asset registration.
    """
    return relay_server._get_endpoint_api_key(endpoint_id, duration_seconds)


def _is_due(note: dict[str, Any], *, now: int, threshold_seconds: int) -> bool:
    if note.get("upstream_mode") != "auto_dedicated":
        return False
    if not note.get("byteplus_endpoint_key_rotation_enabled"):
        return False
    if not _endpoint_ids_from_note(note):
        return False
    expires_at = int(note.get("byteplus_endpoint_api_key_expires_at") or 0)
    return expires_at <= 0 or expires_at - now <= threshold_seconds


def rotate_due_endpoint_keys(now: int | None = None) -> dict[str, Any]:
    now = int(now or time.time())
    if not _env_bool("ENDPOINT_KEY_ROTATION_ENABLED", False):
        return {"checked": 0, "rotated": 0, "failed": 0, "dry_run": False, "data": []}

    days_before_expiry = _env_int("ENDPOINT_KEY_ROTATION_DAYS_BEFORE_EXPIRY", 5)
    duration_seconds = _env_int("ENDPOINT_KEY_DURATION_SECONDS", 2592000)
    dry_run = _env_bool("ENDPOINT_KEY_ROTATION_DRY_RUN", False)
    threshold_seconds = max(0, days_before_expiry) * 86400

    db = relay_server.get_db()
    rows = db.execute(
        "SELECT id, email, byteplus_api_key, note FROM users WHERE is_active=1"
    ).fetchall()

    result: dict[str, Any] = {"checked": 0, "rotated": 0, "failed": 0, "dry_run": dry_run, "data": []}
    for row in rows:
        note = _load_note(row["note"])
        if not _is_due(note, now=now, threshold_seconds=threshold_seconds):
            continue
        result["checked"] += 1
        user_id = row["id"]
        endpoint_target = _endpoint_target_for_note(note)
        if dry_run:
            result["data"].append({
                "user_id": user_id,
                "endpoint_id": endpoint_target if isinstance(endpoint_target, str) else "",
                "endpoint_ids": endpoint_target if isinstance(endpoint_target, list) else [],
                "status": "dry_run",
            })
            continue
        try:
            issued = get_endpoint_api_key(endpoint_target, duration_seconds)
            expires_at = int(issued["expires_at"])
            note.update({
                "byteplus_endpoint_api_key_expires_at": expires_at,
                "byteplus_endpoint_key_last_rotated_at": now,
                "byteplus_endpoint_key_next_rotate_at": max(now, expires_at - threshold_seconds),
                "byteplus_endpoint_key_rotation_error": "",
            })
            db.execute(
                "UPDATE users SET byteplus_api_key=?, note=? WHERE id=?",
                (issued["api_key"], _dump_note(note), user_id),
            )
            relay_server._audit_event(
                "system_rotated_endpoint_api_key",
                actor_user_id=None,
                actor_type="system",
                target_type="user",
                target_id=user_id,
                metadata={
                    "endpoint_id": endpoint_target if isinstance(endpoint_target, str) else "",
                    "endpoint_ids": endpoint_target if isinstance(endpoint_target, list) else [],
                    "expires_at": expires_at,
                    "secret_changed": True,
                },
            )
            result["rotated"] += 1
            result["data"].append({
                "user_id": user_id,
                "endpoint_id": endpoint_target if isinstance(endpoint_target, str) else "",
                "endpoint_ids": endpoint_target if isinstance(endpoint_target, list) else [],
                "status": "rotated",
            })
        except Exception as exc:
            message = relay_server.sanitize(str(exc))[:1000]
            note["byteplus_endpoint_key_rotation_error"] = message
            db.execute("UPDATE users SET note=? WHERE id=?", (_dump_note(note), user_id))
            relay_server._audit_event(
                "system_endpoint_api_key_rotation_failed",
                actor_user_id=None,
                actor_type="system",
                target_type="user",
                target_id=user_id,
                metadata={
                    "endpoint_id": endpoint_target if isinstance(endpoint_target, str) else "",
                    "endpoint_ids": endpoint_target if isinstance(endpoint_target, list) else [],
                    "error": message,
                },
            )
            result["failed"] += 1
            result["data"].append({
                "user_id": user_id,
                "endpoint_id": endpoint_target if isinstance(endpoint_target, str) else "",
                "endpoint_ids": endpoint_target if isinstance(endpoint_target, list) else [],
                "status": "failed",
            })

    db.close()
    return result


def main() -> int:
    result = rotate_due_endpoint_keys()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if result.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
