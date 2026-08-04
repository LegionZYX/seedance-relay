"""Recompute settled video task costs from task model snapshots.

Default mode is read-only. Use --apply only after backing up relay.sqlite.
The script adjusts users.balance_usd by the task charge delta so already
settled tasks remain consistent with the corrected actual_cost_usd.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modelark.estimator import actual_video_cost  # noqa: E402
from modelark.pricing import get_price  # noqa: E402


def round6(value: float) -> float:
    return round(float(value or 0), 6)


def price_multiplier(row: sqlite3.Row) -> float:
    candidates = (
        row["task_price_multiplier"],
        1 + row["task_markup_pct"] if row["task_markup_pct"] is not None else None,
        row["user_price_multiplier"],
        1 + row["user_markup_pct"] if row["user_markup_pct"] is not None else None,
    )
    for value in candidates:
        if value is not None and float(value) > 0:
            return float(value)
    return 1.0


def pricing_model(row: sqlite3.Row) -> str:
    for candidate in (row["upstream_model"], row["client_model"]):
        candidate = (candidate or "").strip()
        if candidate and get_price(candidate):
            return candidate
    return (row["upstream_model"] or row["client_model"] or "").strip()


def has_audit_events(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit_events'"
    ).fetchone()
    return row is not None


def select_rows(conn: sqlite3.Connection, args: argparse.Namespace) -> list[sqlite3.Row]:
    clauses = [
        "t.status='succeeded'",
        "t.settled=1",
        "t.completion_tokens IS NOT NULL",
        "t.completion_tokens>0",
    ]
    params: list[Any] = []
    if args.task_id:
        placeholders = ",".join("?" for _ in args.task_id)
        clauses.append(f"t.id IN ({placeholders})")
        params.extend(args.task_id)
    if args.model:
        clauses.append("(t.client_model=? OR t.upstream_model=?)")
        params.extend([args.model, args.model])
    if args.since:
        clauses.append("t.created_at>=?")
        params.append(args.since)
    if not args.include_invoiced:
        clauses.append(
            """NOT EXISTS (
               SELECT 1 FROM invoice_items ii
               JOIN invoices inv ON inv.id=ii.invoice_id
               WHERE ii.task_id=t.id AND inv.status<>'void'
            )"""
        )

    sql = f"""
        SELECT
          t.id, t.user_id, t.client_model, t.upstream_model, t.resolution,
          t.has_video_ref, t.completion_tokens,
          t.actual_cost_usd AS old_actual_cost_usd,
          t.upstream_actual_cost_usd AS old_upstream_cost_usd,
          t.price_multiplier AS task_price_multiplier,
          t.markup_pct AS task_markup_pct,
          t.created_at,
          u.price_multiplier AS user_price_multiplier,
          u.markup_pct AS user_markup_pct
        FROM tasks t
        LEFT JOIN users u ON u.id=t.user_id
        WHERE {" AND ".join(clauses)}
        ORDER BY t.created_at DESC, t.id DESC
    """
    if args.limit:
        sql += " LIMIT ?"
        params.append(args.limit)
    return conn.execute(sql, params).fetchall()


def compute_change(row: sqlite3.Row, min_abs_delta: float) -> dict[str, Any] | None:
    model = pricing_model(row)
    if not model or not get_price(model):
        return {
            "task_id": row["id"],
            "user_id": row["user_id"],
            "client_model": row["client_model"],
            "upstream_model": row["upstream_model"],
            "skipped": "no_pricing_for_model",
        }
    upstream = actual_video_cost(
        {
            "status": "succeeded",
            "usage": {"completion_tokens": int(row["completion_tokens"])},
        },
        has_video_ref=bool(row["has_video_ref"]),
        model=model,
        resolution=row["resolution"] or "720p",
    )
    multiplier = price_multiplier(row)
    actual = round6(upstream * multiplier)
    old_actual = round6(row["old_actual_cost_usd"])
    old_upstream = round6(row["old_upstream_cost_usd"])
    delta_actual = round6(actual - old_actual)
    delta_upstream = round6(upstream - old_upstream)
    if abs(delta_actual) < min_abs_delta and abs(delta_upstream) < min_abs_delta:
        return None
    return {
        "task_id": row["id"],
        "user_id": row["user_id"],
        "client_model": row["client_model"],
        "upstream_model": row["upstream_model"],
        "pricing_model": model,
        "resolution": row["resolution"],
        "completion_tokens": int(row["completion_tokens"]),
        "price_multiplier": multiplier,
        "old_upstream_cost_usd": old_upstream,
        "new_upstream_cost_usd": upstream,
        "delta_upstream_cost_usd": delta_upstream,
        "old_actual_cost_usd": old_actual,
        "new_actual_cost_usd": actual,
        "delta_actual_cost_usd": delta_actual,
    }


def apply_changes(conn: sqlite3.Connection, changes: list[dict[str, Any]]) -> None:
    now = int(time.time())
    audit = has_audit_events(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        for change in changes:
            task_cursor = conn.execute(
                """UPDATE tasks
                   SET upstream_actual_cost_usd=?,
                       actual_cost_usd=?,
                       updated_at=?
                   WHERE id=? AND settled=1 AND status='succeeded'""",
                (
                    change["new_upstream_cost_usd"],
                    change["new_actual_cost_usd"],
                    now,
                    change["task_id"],
                ),
            )
            if task_cursor.rowcount != 1:
                raise RuntimeError(
                    f"task update matched {task_cursor.rowcount} rows for {change['task_id']}"
                )
            user_cursor = conn.execute(
                "UPDATE users SET balance_usd=ROUND(balance_usd - ?, 6) WHERE id=?",
                (change["delta_actual_cost_usd"], change["user_id"]),
            )
            if user_cursor.rowcount != 1:
                raise RuntimeError(
                    f"user balance update matched {user_cursor.rowcount} rows for {change['user_id']}"
                )
            if audit:
                conn.execute(
                    """INSERT INTO audit_events
                       (id, actor_user_id, actor_type, action, target_type,
                        target_id, metadata_json, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        "audit_" + secrets.token_hex(12),
                        None,
                        "system",
                        "system_repriced_settled_task",
                        "task",
                        change["task_id"],
                        json.dumps(change, ensure_ascii=False, sort_keys=True),
                        now,
                    ),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.getenv("DB_PATH", "/data/relay.sqlite"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--include-invoiced", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--task-id", action="append")
    parser.add_argument("--since", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--min-abs-delta", type=float, default=0.000001)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        rows = select_rows(conn, args)
        changes = []
        skipped = []
        for row in rows:
            change = compute_change(row, args.min_abs_delta)
            if not change:
                continue
            if change.get("skipped"):
                skipped.append(change)
            else:
                changes.append(change)

        print(json.dumps(
            {
                "db": args.db,
                "apply": args.apply,
                "selected": len(rows),
                "changes": len(changes),
                "skipped": skipped,
                "total_delta_upstream_cost_usd": round6(
                    sum(c["delta_upstream_cost_usd"] for c in changes)
                ),
                "total_delta_actual_cost_usd": round6(
                    sum(c["delta_actual_cost_usd"] for c in changes)
                ),
                "items": changes,
            },
            ensure_ascii=False,
            indent=2,
        ))
        if args.apply and changes:
            apply_changes(conn, changes)
            print(json.dumps({"applied": len(changes)}, ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
