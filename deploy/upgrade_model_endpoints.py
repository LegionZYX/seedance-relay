"""Create per-customer endpoints for a newly introduced model id.

Typical dry-run:
  python deploy/upgrade_model_endpoints.py --dry-run --model dreamina-seedance-2-0-270101

The script only appends/updates users.note.byteplus_endpoint_map. It does not
rotate endpoint keys and does not overwrite asset group, billing, or key
rotation metadata in users.note.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import relay_server  # noqa: E402


def _load_note(raw: str | None) -> dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {"legacy_note": raw}
    return parsed if isinstance(parsed, dict) else {"legacy_note": raw}


def _dump_note(note: dict[str, Any]) -> str:
    return json.dumps(note, ensure_ascii=True, sort_keys=True)


def _slug_from_row(row: Any, note: dict[str, Any]) -> str:
    raw = str(note.get("customer_slug") or row["email"] or row["id"] or "customer")
    raw = raw.split("@", 1)[0].strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    return slug or str(row["id"])


def _endpoint_map_from_note(note: dict[str, Any]) -> dict[str, str]:
    raw = note.get("byteplus_endpoint_map")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for model_id, endpoint_id in raw.items():
        model = str(model_id or "").strip()
        endpoint = str(endpoint_id or "").strip()
        if model and endpoint:
            out[model] = endpoint
    return out


def _provider_keys() -> tuple[str, str]:
    ak = os.getenv("BYTEPLUS_ACCESS_KEY_ID", os.getenv("BYTEPLUS_ACCESSKEY", relay_server.BYTEPLUS_ACCESSKEY)).strip()
    sk = os.getenv(
        "BYTEPLUS_ACCESS_KEY_SECRET",
        os.getenv("BYTEPLUS_SECRETKEY", relay_server.BYTEPLUS_SECRETKEY),
    ).strip()
    return ak, sk


def _selected_customers(user_id: str = "", email: str = "") -> list[Any]:
    clauses = ["is_active=1"]
    args: list[str] = []
    if user_id:
        clauses.append("id=?")
        args.append(user_id)
    if email:
        clauses.append("email=?")
        args.append(email)
    db = relay_server.get_db()
    try:
        return db.execute(
            f"SELECT id, email, enabled_models, byteplus_api_key, note FROM users WHERE {' AND '.join(clauses)}",
            args,
        ).fetchall()
    finally:
        db.close()


def _create_endpoint_for_model(
    *,
    slug: str,
    email: str,
    project_name: str,
    model_id: str,
    wait: bool,
) -> str:
    ak, sk = _provider_keys()
    if not ak or not sk:
        raise RuntimeError("BYTEPLUS_ACCESS_KEY_ID/BYTEPLUS_ACCESS_KEY_SECRET are required")
    body = relay_server._endpoint_create_body(slug, project_name, email, model_id)
    result = relay_server._call_asset_api("CreateEndpoint", body, ak, sk)
    endpoint_id = str(
        relay_server.extract_nested_value(result, "Result", "EndpointId")
        or relay_server.extract_nested_value(result, "EndpointId")
        or relay_server.extract_nested_value(result, "Id")
        or ""
    ).strip()
    if not endpoint_id:
        raise RuntimeError("CreateEndpoint did not return EndpointId")
    if wait:
        relay_server._wait_endpoint_ready(endpoint_id, project_name)
    return endpoint_id


def _write_endpoint_mapping(user_id: str, note: dict[str, Any], model_id: str, endpoint_id: str, now: int) -> None:
    endpoint_map = _endpoint_map_from_note(note)
    endpoint_map[model_id] = endpoint_id
    note["byteplus_endpoint_map"] = endpoint_map
    note["byteplus_endpoint_map_updated_at"] = now
    db = relay_server.get_db()
    try:
        db.execute("UPDATE users SET note=? WHERE id=?", (_dump_note(note), user_id))
    finally:
        db.close()


def upgrade_model_endpoints(
    *,
    model_id: str,
    from_model_id: str = "",
    user_id: str = "",
    email: str = "",
    dry_run: bool = True,
    wait: bool = True,
) -> dict[str, Any]:
    model_id = str(model_id or "").strip()
    from_model_id = str(from_model_id or "").strip()
    if not model_id:
        raise ValueError("model_id is required")

    now = int(time.time())
    rows = _selected_customers(user_id=user_id, email=email)
    result: dict[str, Any] = {
        "dry_run": bool(dry_run),
        "model_id": model_id,
        "from_model_id": from_model_id,
        "checked": 0,
        "planned": 0,
        "upgraded": 0,
        "skipped": 0,
        "failed": 0,
        "data": [],
    }

    for row in rows:
        result["checked"] += 1
        note = _load_note(row["note"])
        endpoint_map = _endpoint_map_from_note(note)
        slug = _slug_from_row(row, note)
        project_name = str(note.get("byteplus_project_name") or "").strip()
        item = {
            "user_id": row["id"],
            "email": row["email"],
            "project_name": project_name,
            "model_id": model_id,
            "from_model_id": from_model_id,
        }
        if not project_name:
            result["skipped"] += 1
            result["data"].append({**item, "status": "missing_project"})
            continue
        if endpoint_map.get(model_id):
            result["skipped"] += 1
            result["data"].append({**item, "endpoint_id": endpoint_map[model_id], "status": "already_mapped"})
            continue

        endpoint_body = relay_server._endpoint_create_body(slug, project_name, str(row["email"] or ""), model_id)
        item["endpoint_name"] = endpoint_body["Name"]
        if dry_run:
            result["planned"] += 1
            result["data"].append({**item, "status": "would_create_endpoint"})
            continue

        try:
            endpoint_id = _create_endpoint_for_model(
                slug=slug,
                email=str(row["email"] or ""),
                project_name=project_name,
                model_id=model_id,
                wait=wait,
            )
            _write_endpoint_mapping(row["id"], note, model_id, endpoint_id, now)
            relay_server._audit_event(
                "system_upgraded_model_endpoint",
                actor_user_id=None,
                actor_type="system",
                target_type="user",
                target_id=row["id"],
                metadata={
                    "model_id": model_id,
                    "from_model_id": from_model_id,
                    "project_name": project_name,
                    "endpoint_id": endpoint_id,
                },
            )
            result["upgraded"] += 1
            result["data"].append({**item, "endpoint_id": endpoint_id, "status": "created"})
        except Exception as exc:
            result["failed"] += 1
            result["data"].append({**item, "status": "failed", "error": relay_server.sanitize(str(exc))[:500]})

    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create model endpoints and merge users.note.byteplus_endpoint_map")
    parser.add_argument("--model", dest="model_id", required=True, help="New or target model id to map")
    parser.add_argument("--from-model", dest="from_model_id", default="", help="Old model id, for audit/report context")
    parser.add_argument("--user-id", default="", help="Limit to one user id")
    parser.add_argument("--email", default="", help="Limit to one customer email")
    parser.add_argument("--dry-run", action="store_true", help="Plan only; do not call BytePlus or write DB")
    parser.add_argument("--execute", action="store_true", help="Actually create endpoints and update DB")
    parser.add_argument("--no-wait", action="store_true", help="Do not wait for endpoint Running")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    dry_run = True
    if args.execute:
        dry_run = False
    if args.dry_run:
        dry_run = True
    result = upgrade_model_endpoints(
        model_id=args.model_id,
        from_model_id=args.from_model_id,
        user_id=args.user_id,
        email=args.email,
        dry_run=dry_run,
        wait=not args.no_wait,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if result.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
