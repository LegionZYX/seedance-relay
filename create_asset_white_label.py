#!/usr/bin/env python3
"""Register relay-hosted media URLs as ModelArk assets.

Typical flow:
  python create_asset_white_label.py create-group --name relay-face-assets
  python create_asset_white_label.py create --url https://example.com/ref.mp4 --asset-type Video --skip-moderation
  python create_asset_white_label.py wait --asset-id asset-xxxx

Required environment:
  BYTEPLUS_ACCESS_KEY_ID
  BYTEPLUS_ACCESS_KEY_SECRET
  MODELARK_ASSET_GROUP_ID for asset creation, unless --group-id is passed
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


SERVICE = os.getenv("MODELARK_OPENAPI_SERVICE", "ark")
REGION = os.getenv("MODELARK_OPENAPI_REGION", "ap-southeast-1")
HOST = os.getenv("MODELARK_OPENAPI_HOST", "ark.ap-southeast-1.byteplusapi.com")
VERSION = os.getenv("MODELARK_OPENAPI_VERSION", "2024-01-01")
CONTENT_TYPE = "application/json"
PATH = "/"


def env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def norm_query(params: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in sorted(params.keys()):
        value = params[key]
        if isinstance(value, list):
            values = value
        else:
            values = [value]
        for item in values:
            parts.append(
                urllib.parse.quote(str(key), safe="-_.~")
                + "="
                + urllib.parse.quote(str(item), safe="-_.~")
            )
    return "&".join(parts).replace("+", "%20")


def hmac_sha256(key: bytes, content: str) -> bytes:
    return hmac.new(key, content.encode("utf-8"), hashlib.sha256).digest()


def hash_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def request_api(action: str, body: dict[str, Any], ak: str, sk: str) -> dict[str, Any]:
    body_json = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    now = dt.datetime.utcnow()
    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    short_date = x_date[:8]
    query = {"Action": action, "Version": VERSION}
    body_hash = hash_sha256(body_json)

    signed_headers = "content-type;host;x-content-sha256;x-date"
    canonical_request = "\n".join(
        [
            "POST",
            PATH,
            norm_query(query),
            "\n".join(
                [
                    f"content-type:{CONTENT_TYPE}",
                    f"host:{HOST}",
                    f"x-content-sha256:{body_hash}",
                    f"x-date:{x_date}",
                ]
            ),
            "",
            signed_headers,
            body_hash,
        ]
    )
    credential_scope = "/".join([short_date, REGION, SERVICE, "request"])
    string_to_sign = "\n".join(
        ["HMAC-SHA256", x_date, credential_scope, hash_sha256(canonical_request)]
    )

    k_date = hmac_sha256(sk.encode("utf-8"), short_date)
    k_region = hmac_sha256(k_date, REGION)
    k_service = hmac_sha256(k_region, SERVICE)
    k_signing = hmac_sha256(k_service, "request")
    signature = hmac_sha256(k_signing, string_to_sign).hex()
    authorization = (
        f"HMAC-SHA256 Credential={ak}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    url = f"https://{HOST}{PATH}?{norm_query(query)}"
    req = urllib.request.Request(
        url,
        data=body_json.encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": CONTENT_TYPE,
            "Host": HOST,
            "X-Content-Sha256": body_hash,
            "X-Date": x_date,
            "Authorization": authorization,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"{action} failed: HTTP {exc.code}\n{text}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"{action} failed: {exc}") from exc

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{action} returned invalid JSON:\n{text}") from exc


def build_create_asset_body(
    group_id: str,
    url: str,
    asset_type: str,
    skip_moderation: bool = False,
    name: str = "",
    project_name: str = "",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "GroupId": group_id,
        "URL": url,
        "AssetType": asset_type,
    }
    if name:
        body["Name"] = name
    if project_name:
        body["ProjectName"] = project_name
    if skip_moderation:
        body["Moderation"] = {"Strategy": "Skip"}
    return body


def build_create_asset_group_body(
    name: str,
    description: str = "",
    project_name: str = "",
    group_type: str = "AIGC",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "Name": name,
        "GroupType": (group_type or "AIGC").strip() or "AIGC",
    }
    if description:
        body["Description"] = description
    if project_name:
        body["ProjectName"] = project_name
    return body


def extract_nested_value(data: dict[str, Any], *keys: str) -> str:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
        if current is None:
            return ""
    return str(current) if current is not None else ""


def extract_asset_id(data: dict[str, Any]) -> str:
    for keys in (
        ("Result", "Id"),
        ("Result", "AssetId"),
        ("Id",),
        ("AssetId",),
    ):
        value = extract_nested_value(data, *keys)
        if value:
            return value
    return ""


def extract_asset_group_id(data: dict[str, Any]) -> str:
    for keys in (
        ("Result", "Id"),
        ("Result", "GroupId"),
        ("Result", "AssetGroupId"),
        ("Id",),
        ("GroupId",),
        ("AssetGroupId",),
    ):
        value = extract_nested_value(data, *keys)
        if value:
            return value
    return ""


def get_asset(asset_id: str, ak: str, sk: str, project_name: str = "") -> dict[str, Any]:
    body: dict[str, Any] = {"Id": asset_id}
    if project_name:
        body["ProjectName"] = project_name
    return request_api("GetAsset", body, ak, sk)


def print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def cmd_create(args: argparse.Namespace) -> int:
    ak = env_required("BYTEPLUS_ACCESS_KEY_ID")
    sk = env_required("BYTEPLUS_ACCESS_KEY_SECRET")
    group_id = args.group_id or env_required("MODELARK_ASSET_GROUP_ID")
    project_name = args.project_name or os.getenv("MODELARK_PROJECT_NAME", "").strip()
    body = build_create_asset_body(
        group_id,
        args.url,
        args.asset_type,
        args.skip_moderation,
        args.name or "",
        project_name,
    )
    result = request_api("CreateAsset", body, ak, sk)
    print_json(result)
    asset_id = extract_asset_id(result)
    if not asset_id:
        print("Could not find AssetId in CreateAsset response.", file=sys.stderr)
        return 2
    print(f"asset://{asset_id}")
    return 0


def cmd_create_group(args: argparse.Namespace) -> int:
    ak = env_required("BYTEPLUS_ACCESS_KEY_ID")
    sk = env_required("BYTEPLUS_ACCESS_KEY_SECRET")
    body = build_create_asset_group_body(
        args.name,
        args.description or "",
        args.project_name or os.getenv("MODELARK_PROJECT_NAME", "").strip(),
        args.group_type or "AIGC",
    )
    result = request_api("CreateAssetGroup", body, ak, sk)
    print_json(result)
    group_id = extract_asset_group_id(result)
    if not group_id:
        print("Could not find GroupId in CreateAssetGroup response.", file=sys.stderr)
        return 2
    print(group_id)
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    ak = env_required("BYTEPLUS_ACCESS_KEY_ID")
    sk = env_required("BYTEPLUS_ACCESS_KEY_SECRET")
    print_json(get_asset(args.asset_id, ak, sk, args.project_name or os.getenv("MODELARK_PROJECT_NAME", "").strip()))
    return 0


def cmd_wait(args: argparse.Namespace) -> int:
    ak = env_required("BYTEPLUS_ACCESS_KEY_ID")
    sk = env_required("BYTEPLUS_ACCESS_KEY_SECRET")
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        result = get_asset(
            args.asset_id,
            ak,
            sk,
            args.project_name or os.getenv("MODELARK_PROJECT_NAME", "").strip(),
        )
        print_json(result)
        status = extract_nested_value(result, "Result", "Status") or extract_nested_value(result, "Status")
        if status == "Active":
            print(f"asset://{args.asset_id}")
            return 0
        if status == "Failed":
            message = extract_nested_value(result, "Result", "Error") or extract_nested_value(result, "Error")
            print(f"Asset failed: {message or 'unknown error'}", file=sys.stderr)
            return 3
        time.sleep(args.interval)
    print(f"Timed out waiting for asset {args.asset_id}", file=sys.stderr)
    return 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create and inspect ModelArk assets from public media URLs.")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create an asset from a public media URL.")
    create.add_argument("--url", required=True, help="Public HTTPS URL that ModelArk can fetch.")
    create.add_argument("--asset-type", required=True, choices=("Image", "Video", "Audio"))
    create.add_argument("--group-id", help="Override MODELARK_ASSET_GROUP_ID.")
    create.add_argument("--name", default="", help="Optional asset display name.")
    create.add_argument("--project-name", default="", help="ProjectName for the asset request.")
    create.add_argument("--skip-moderation", action="store_true")
    create.set_defaults(func=cmd_create)

    create_group = sub.add_parser("create-group", help="Create an asset group.")
    create_group.add_argument("--name", required=True, help="Asset group name.")
    create_group.add_argument("--description", default="")
    create_group.add_argument("--project-name", default="")
    create_group.add_argument("--group-type", default="AIGC", help="Asset group type. Defaults to AIGC.")
    create_group.set_defaults(func=cmd_create_group)

    get = sub.add_parser("get", help="Fetch one asset status.")
    get.add_argument("--asset-id", required=True)
    get.add_argument("--project-name", default="", help="ProjectName for the asset request.")
    get.set_defaults(func=cmd_get)

    wait = sub.add_parser("wait", help="Poll until an asset is Active.")
    wait.add_argument("--asset-id", required=True)
    wait.add_argument("--project-name", default="", help="ProjectName for the asset request.")
    wait.add_argument("--interval", type=float, default=3.0)
    wait.add_argument("--timeout", type=float, default=120.0)
    wait.set_defaults(func=cmd_wait)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
