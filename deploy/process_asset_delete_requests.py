from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import relay_server  # noqa: E402


def process_asset_delete_requests(
    *,
    limit: int = 100,
    statuses: tuple[str, ...] = ("queued",),
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 500))
    statuses = tuple(status.strip().lower() for status in statuses if status.strip())
    if not statuses:
        statuses = ("queued",)
    placeholders = ",".join("?" for _ in statuses)
    db = relay_server.get_db()
    try:
        rows = db.execute(
            f"""SELECT id
                FROM asset_delete_requests
                WHERE status IN ({placeholders})
                ORDER BY created_at ASC, id ASC
                LIMIT ?""",
            list(statuses) + [limit],
        ).fetchall()
    finally:
        db.close()

    report: dict[str, Any] = {
        "checked": len(rows),
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
        "data": [],
    }
    for row in rows:
        request_id = row["id"]
        try:
            result = relay_server._execute_asset_delete_request(
                request_id,
                actor_user_id=None,
                actor_type="system",
            )
            report["processed"] += 1
            if result.get("status") == "succeeded":
                report["succeeded"] += 1
            else:
                report["failed"] += 1
            report["data"].append(result)
        except Exception as exc:
            report["processed"] += 1
            report["failed"] += 1
            report["data"].append({
                "id": request_id,
                "status": "failed",
                "error": relay_server.sanitize(str(exc))[:500],
            })
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Process queued BytePlus asset delete requests")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--status",
        action="append",
        default=None,
        help="Status to process. Defaults to queued. Repeat to include multiple statuses.",
    )
    args = parser.parse_args()
    report = process_asset_delete_requests(
        limit=args.limit,
        statuses=tuple(args.status or ["queued"]),
    )
    print(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2))
    return 1 if report.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
