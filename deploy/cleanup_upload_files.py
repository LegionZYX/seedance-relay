from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Optional

import relay_server


def _asset_registered(row: dict[str, Any]) -> bool:
    asset_url = str(row.get("asset_url") or "").strip()
    return asset_url.startswith("asset://")


def cleanup_registered_upload_files(
    *,
    older_than_seconds: int = 24 * 3600,
    dry_run: bool = True,
    now: Optional[int] = None,
    limit: int = 500,
) -> dict[str, Any]:
    now = int(now or time.time())
    cutoff = now - max(0, int(older_than_seconds))
    limit = max(1, int(limit))
    result: dict[str, Any] = {
        "dry_run": bool(dry_run),
        "older_than_seconds": int(older_than_seconds),
        "checked": 0,
        "eligible": 0,
        "would_delete": 0,
        "deleted": 0,
        "skipped": 0,
        "missing_local_file": 0,
        "items": [],
    }

    db = relay_server.get_db()
    try:
        rows = db.execute(
            """SELECT *
               FROM uploads
               WHERE (deleted_at IS NULL OR deleted_at=0)
                 AND (local_deleted_at IS NULL OR local_deleted_at=0)
                 AND object_key LIKE 'uploads/%'
               ORDER BY created_at ASC, id ASC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    finally:
        db.close()

    for raw_row in rows:
        row = dict(raw_row)
        result["checked"] += 1
        item = {
            "upload_id": row["id"],
            "object_key": row["object_key"],
            "asset_url": row.get("asset_url") or "",
        }
        created_at = int(row.get("created_at") or 0)
        if created_at > cutoff:
            result["skipped"] += 1
            item["reason"] = "too_new"
            result["items"].append(item)
            continue
        if not _asset_registered(row):
            result["skipped"] += 1
            item["reason"] = "not_registered_asset"
            result["items"].append(item)
            continue
        path = relay_server._local_upload_path_for_object_key(row["object_key"])
        if not path:
            result["skipped"] += 1
            item["reason"] = "invalid_object_key"
            result["items"].append(item)
            continue
        result["eligible"] += 1
        if not Path(path).exists():
            result["missing_local_file"] += 1
            item["reason"] = "missing_local_file"
            if not dry_run:
                db = relay_server.get_db()
                try:
                    db.execute(
                        "UPDATE uploads SET local_deleted_at=?, updated_at=? WHERE id=?",
                        (now, now, row["id"]),
                    )
                finally:
                    db.close()
            result["items"].append(item)
            continue
        if dry_run:
            result["would_delete"] += 1
            item["reason"] = "would_delete"
            result["items"].append(item)
            continue

        if relay_server._delete_local_upload_file(row, now=now):
            db = relay_server.get_db()
            try:
                db.execute(
                    "UPDATE uploads SET local_deleted_at=?, updated_at=? WHERE id=?",
                    (now, now, row["id"]),
                )
            finally:
                db.close()
            relay_server._audit_event(
                "system_cleaned_local_upload_file",
                actor_user_id=None,
                actor_type="system",
                target_type="upload",
                target_id=row["id"],
                metadata={
                    "object_key": row["object_key"],
                    "asset_url": row.get("asset_url") or "",
                    "older_than_seconds": int(older_than_seconds),
                },
            )
            result["deleted"] += 1
            item["reason"] = "deleted"
        else:
            result["skipped"] += 1
            item["reason"] = "delete_failed"
        result["items"].append(item)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean registered relay upload temp files")
    parser.add_argument("--older-than-seconds", type=int, default=24 * 3600)
    parser.add_argument("--older-than-hours", type=float)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true", help="Report eligible files without deleting")
    parser.add_argument("--execute", action="store_true", help="Delete eligible local files")
    args = parser.parse_args()

    older_than_seconds = args.older_than_seconds
    if args.older_than_hours is not None:
        older_than_seconds = int(args.older_than_hours * 3600)
    report = cleanup_registered_upload_files(
        older_than_seconds=older_than_seconds,
        dry_run=not args.execute,
        limit=args.limit,
    )
    print(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
