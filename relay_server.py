"""
Example Video Relay API — BytePlus Seedance 视频生成反代（含管理员/用户后台）
=====================================================================
• 反代 BytePlus 视频 API，对客户隐藏底层供应商
• **每个用户独立 BytePlus key**：方便 1:1 跟 BytePlus 后台对账
• Admin Web UI: /admin/ui/   (开账号/充值/对账)
• User Web UI:  /app/        (生成视频/查历史/看视频)

启动:
    uvicorn relay_server:app --host 0.0.0.0 --port 8002

环境变量 (.env.relay):
    UPSTREAM_BASE_URL   default: https://ark.ap-southeast.bytepluses.com/api/v3
    UPSTREAM_API_KEY    fallback BytePlus key (用户没填自己的就用这个)
    PUBLIC_DOMAIN       default: video.example.com
    DB_PATH             default: /data/relay.sqlite
    VIDEO_DIR           default: /data/videos    (落地视频文件)
    UPLOAD_DIR          default: /data/uploads   (上传中转站)
    ASSET_AUTO_REGISTER_UPLOADS  default: false  (服务端自动注册 asset://)
    FACE_ASSET_ENFORCE default: false  (reference 人脸素材必须走白名单 asset://)
    FACE_ASSET_SELF_SERVICE default: false  (客户上传时自助注册并加入人脸白名单)
    BRAND_NAME          default: Example Video Relay
    MARKUP_PCT          default: 0.3
    ADMIN_EMAIL         default: admin@example.com
    ADMIN_PASSWORD      启动时若 admin 用户不存在则用此密码创建
    ADMIN_KEY           备用 X-Admin-Key (脚本/curl 用)
"""

from __future__ import annotations

import os
import re
import sys
import time
import sqlite3
import secrets
import asyncio
import json
import bcrypt
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional, AsyncIterator, List, Tuple
from urllib.parse import urlparse, unquote, quote

import httpx
from fastapi import (
    FastAPI, HTTPException, Header, Depends, Request, Response, Cookie,
    File, Form, UploadFile, status,
)
from fastapi.responses import (
    StreamingResponse, JSONResponse, FileResponse, RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, EmailStr
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from modelark import estimate_video_cost, actual_video_cost
from create_asset_white_label import (
    build_create_asset_body,
    build_create_asset_group_body,
    extract_asset_group_id,
    extract_asset_id,
    extract_nested_value,
    request_api as request_asset_api,
)

# ─── Config ──────────────────────────────────────────────────────
load_dotenv(Path(__file__).parent / ".env.relay")
load_dotenv(Path(__file__).parent / ".env")

UPSTREAM_API_KEY  = os.getenv("UPSTREAM_API_KEY", os.getenv("ARK_API_KEY", "")).strip()
UPSTREAM_BASE_URL = os.getenv("UPSTREAM_BASE_URL",
                              "https://ark.ap-southeast.bytepluses.com/api/v3").rstrip("/")
UPSTREAM_AUTH_MODE = os.getenv("UPSTREAM_AUTH_MODE", "api_key").strip().lower() or "api_key"
UPSTREAM_ENDPOINT_ID = os.getenv("UPSTREAM_ENDPOINT_ID", "").strip()
BYTEPLUS_ACCESSKEY = os.getenv(
    "BYTEPLUS_ACCESSKEY",
    os.getenv("BYTEPLUS_ACCESS_KEY", os.getenv("BYTEPLUS_ACCESS_KEY_ID", "")),
).strip()
BYTEPLUS_SECRETKEY = os.getenv(
    "BYTEPLUS_SECRETKEY",
    os.getenv("BYTEPLUS_SECRET_KEY", os.getenv("BYTEPLUS_ACCESS_KEY_SECRET", "")),
).strip()
PUBLIC_DOMAIN  = os.getenv("PUBLIC_DOMAIN", "video.example.com")
DB_PATH        = os.getenv("DB_PATH", "/data/relay.sqlite")
VIDEO_DIR      = Path(os.getenv("VIDEO_DIR", "/data/videos"))
UPLOAD_DIR     = Path(os.getenv("UPLOAD_DIR", "/data/uploads"))
VIDEO_PERSIST_MODE = os.getenv("VIDEO_PERSIST_MODE", "proxy_only").strip().lower() or "proxy_only"
UPLOAD_PUBLIC_BASE_URL = os.getenv("UPLOAD_PUBLIC_BASE_URL", "").strip().rstrip("/")
PRICE_MULTIPLIER_BACKFILL_SETTING = "migration.price_multiplier_backfill.v1"
ADMIN_KEY      = os.getenv("ADMIN_KEY", "").strip()
RUNTIME_INTERNAL_TOKEN = os.getenv("RUNTIME_INTERNAL_TOKEN", "").strip()
ADMIN_EMAIL    = os.getenv("ADMIN_EMAIL", "admin@example.com").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()
BRAND_NAME     = os.getenv("BRAND_NAME", "Example Video Relay")
MARKUP_PCT     = float(os.getenv("MARKUP_PCT", "0.3"))
UPLOAD_MAX_IMAGE_MB = float(os.getenv("UPLOAD_MAX_IMAGE_MB", os.getenv("WEB_MAX_IMAGE_MB", "10")))
UPLOAD_MAX_VIDEO_MB = float(os.getenv("UPLOAD_MAX_VIDEO_MB", "50"))
UPLOAD_MAX_AUDIO_MB = float(os.getenv("UPLOAD_MAX_AUDIO_MB", "15"))
ASSET_AUTO_REGISTER_UPLOADS = os.getenv("ASSET_AUTO_REGISTER_UPLOADS", "false").strip().lower() in (
    "1", "true", "yes", "on"
)
ASSET_AUTO_REGISTER_PURPOSES = {
    item.strip().lower()
    for item in os.getenv("ASSET_AUTO_REGISTER_PURPOSES", "image,video,audio").split(",")
    if item.strip()
}
ASSET_AUTO_REGISTER_WAIT_SECONDS = float(os.getenv("ASSET_AUTO_REGISTER_WAIT_SECONDS", "0"))
ASSET_AUTO_REGISTER_WAIT_INTERVAL = float(os.getenv("ASSET_AUTO_REGISTER_WAIT_INTERVAL", "3"))
ASSET_AUTO_REGISTER_SKIP_MODERATION = os.getenv(
    "ASSET_AUTO_REGISTER_SKIP_MODERATION", "false"
).strip().lower() in ("1", "true", "yes", "on")
MODELARK_ASSET_AUTO_CREATE_GROUP = os.getenv(
    "MODELARK_ASSET_AUTO_CREATE_GROUP", "true"
).strip().lower() in ("1", "true", "yes", "on")
MODELARK_ASSET_GROUP_NAME = os.getenv(
    "MODELARK_ASSET_GROUP_NAME", "relay-face-assets"
).strip() or "relay-face-assets"
MODELARK_ASSET_GROUP_DESCRIPTION = os.getenv(
    "MODELARK_ASSET_GROUP_DESCRIPTION", "Relay self-service face asset whitelist"
).strip()
MODELARK_PROJECT_NAME = os.getenv("MODELARK_PROJECT_NAME", "").strip()
FACE_ASSET_ENFORCE = os.getenv("FACE_ASSET_ENFORCE", "false").strip().lower() in (
    "1", "true", "yes", "on"
)
FACE_ASSET_ALLOWLIST = {
    item.strip()
    for item in os.getenv("FACE_ASSET_ALLOWLIST", "").split(",")
    if item.strip()
}
FACE_ASSET_ENFORCE_ROLES = {
    item.strip().lower()
    for item in os.getenv("FACE_ASSET_ENFORCE_ROLES", "reference_image,reference_video").split(",")
    if item.strip()
}
FACE_ASSET_SELF_SERVICE = os.getenv("FACE_ASSET_SELF_SERVICE", "false").strip().lower() in (
    "1", "true", "yes", "on"
)

SESSION_TTL_SECS = 7 * 86400        # 登录后 7 天有效
LOGIN_MAX_FAILED_ATTEMPTS = int(os.getenv("LOGIN_MAX_FAILED_ATTEMPTS", "5"))
LOGIN_LOCK_SECONDS = int(os.getenv("LOGIN_LOCK_SECONDS", "900"))

VIDEO_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_UPLOAD_TYPES = {
    "image/jpeg": {"purpose": "image", "ext": ".jpg", "max_mb": UPLOAD_MAX_IMAGE_MB},
    "image/png": {"purpose": "image", "ext": ".png", "max_mb": UPLOAD_MAX_IMAGE_MB},
    "image/webp": {"purpose": "image", "ext": ".webp", "max_mb": UPLOAD_MAX_IMAGE_MB},
    "video/mp4": {"purpose": "video", "ext": ".mp4", "max_mb": UPLOAD_MAX_VIDEO_MB},
    "video/quicktime": {"purpose": "video", "ext": ".mov", "max_mb": UPLOAD_MAX_VIDEO_MB},
    "audio/mpeg": {"purpose": "audio", "ext": ".mp3", "max_mb": UPLOAD_MAX_AUDIO_MB},
    "audio/wav": {"purpose": "audio", "ext": ".wav", "max_mb": UPLOAD_MAX_AUDIO_MB},
    "audio/x-wav": {"purpose": "audio", "ext": ".wav", "max_mb": UPLOAD_MAX_AUDIO_MB},
}

# ─── 模型映射 ────────────────────────────────────────────────────
NATIVE_MODEL_IDS = [
    "dreamina-seedance-2-0-260128",
    "dreamina-seedance-2-0-fast-260128",
    "seedance-1-5-pro-251215",
    "seedance-1-0-pro-250528",
    "seedance-1-0-pro-fast-251015",
    "seedance-1-0-lite-t2v-250428",
    "seedance-1-0-lite-i2v-250428",
]


def _load_model_aliases() -> dict[str, str]:
    raw = os.getenv("MODEL_ID_ALIASES_JSON", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        print(f"Invalid MODEL_ID_ALIASES_JSON: {exc}")
        return {}
    if not isinstance(parsed, dict):
        print("Invalid MODEL_ID_ALIASES_JSON: expected object mapping alias to native model id")
        return {}
    native = set(NATIVE_MODEL_IDS)
    aliases: dict[str, str] = {}
    for alias, upstream_id in parsed.items():
        alias = str(alias).strip()
        upstream_id = str(upstream_id).strip()
        if not alias or not upstream_id:
            continue
        if alias in native:
            print(f"Ignored MODEL_ID_ALIASES_JSON alias {alias!r}: alias conflicts with native model id")
            continue
        if upstream_id not in native:
            print(f"Ignored MODEL_ID_ALIASES_JSON alias {alias!r}: unknown native model id {upstream_id!r}")
            continue
        aliases[alias] = upstream_id
    return aliases


MODEL_ALIASES = _load_model_aliases()
MODEL_MAP = {model_id: model_id for model_id in NATIVE_MODEL_IDS}
MODEL_MAP.update(MODEL_ALIASES)
DEFAULT_SUPPORTED_RESOLUTIONS = ["480p", "720p", "1080p"]
DEFAULT_SUPPORTED_RATIOS = ["16:9", "9:16", "1:1"]
DEFAULT_DURATION_SECONDS = {"min": 2, "max": 15}
MODEL_REGISTRY = {
    "dreamina-seedance-2-0-260128": {
        "description": "High quality video generation",
        "upstream_model_or_endpoint": "dreamina-seedance-2-0-260128",
        "profile": "standard",
        "enabled_by_default": True,
    },
    "dreamina-seedance-2-0-fast-260128": {
        "description": "Fast high quality video generation",
        "upstream_model_or_endpoint": "dreamina-seedance-2-0-fast-260128",
        "profile": "standard",
        "enabled_by_default": True,
    },
    "seedance-1-5-pro-251215": {
        "description": "Seedance 1.5 pro compatible model",
        "upstream_model_or_endpoint": "seedance-1-5-pro-251215",
        "profile": "standard",
        "enabled_by_default": True,
    },
    "seedance-1-0-pro-250528": {
        "description": "1080p video generation",
        "upstream_model_or_endpoint": "seedance-1-0-pro-250528",
        "profile": "standard",
        "enabled_by_default": True,
    },
    "seedance-1-0-pro-fast-251015": {
        "description": "720p video generation",
        "upstream_model_or_endpoint": "seedance-1-0-pro-fast-251015",
        "profile": "standard",
        "enabled_by_default": True,
    },
    "seedance-1-0-lite-t2v-250428": {
        "description": "Lite text-to-video generation",
        "upstream_model_or_endpoint": "seedance-1-0-lite-t2v-250428",
        "profile": "standard",
        "enabled_by_default": True,
    },
    "seedance-1-0-lite-i2v-250428": {
        "description": "Lite image-to-video generation",
        "upstream_model_or_endpoint": "seedance-1-0-lite-i2v-250428",
        "profile": "standard",
        "enabled_by_default": True,
        "requires_visual_reference": True,
    },
}
for _model_id, _model_spec in MODEL_REGISTRY.items():
    _model_spec.setdefault("supported_resolutions", list(DEFAULT_SUPPORTED_RESOLUTIONS))
    _model_spec.setdefault("supported_ratios", list(DEFAULT_SUPPORTED_RATIOS))
    _model_spec.setdefault("duration_seconds", dict(DEFAULT_DURATION_SECONDS))
    _model_spec.setdefault("supports_audio", True)
    _model_spec.setdefault("supports_reference_image", True)
    _model_spec.setdefault("supports_reference_video", True)
    _model_spec.setdefault("supports_reference_audio", True)
    _model_spec.setdefault("max_reference_images", 9)
    _model_spec.setdefault("max_reference_videos", 3)
    _model_spec.setdefault("max_reference_audios", 3)
for _alias_id, _upstream_id in MODEL_ALIASES.items():
    _base_spec = MODEL_REGISTRY[_upstream_id]
    _alias_spec = dict(_base_spec)
    _alias_spec["upstream_model_or_endpoint"] = _upstream_id
    _alias_spec["alias_for"] = _upstream_id
    MODEL_REGISTRY[_alias_id] = _alias_spec
ALLOWED_CONTENT_BLOCK_TYPES = {"text", "image_url", "video_url", "audio_url"}
CONTENT_BLOCK_LIMITS = {
    "image_url": ("too_many_reference_images", 9),
    "video_url": ("too_many_reference_videos", 3),
    "audio_url": ("too_many_reference_audios", 3),
}
ALLOWED_CONTENT_ROLES = {
    "first_frame",
    "last_frame",
    "reference_image",
    "reference_video",
    "reference_audio",
}
VISUAL_REFERENCE_REQUIRED_MODELS = {
    model_id
    for model_id, model_spec in MODEL_REGISTRY.items()
    if model_spec.get("requires_visual_reference")
}

# ─── 错误信息脱敏 ────────────────────────────────────────────────
SENSITIVE_PATTERNS = [
    (re.compile(r"https?://[^\s\"']*(?:byteplus|bytepluses|volces)[^\s\"']*", re.I), PUBLIC_DOMAIN),
    (re.compile(r"\bByte[\s\-]?Plus\b", re.I), BRAND_NAME),
    (re.compile(r"\bModelArk\b", re.I),         BRAND_NAME),
    (re.compile(r"\bdreamina[\w\-]*", re.I),    "video-pro"),
    (re.compile(r"\bseedance[\w\-]*", re.I),    "video-model"),
    (re.compile(r"bytepluses\.com",   re.I),    PUBLIC_DOMAIN),
    (re.compile(r"volces\.com",       re.I),    PUBLIC_DOMAIN),
    (re.compile(r"\bark[\.\-][\w\.\-]+", re.I), PUBLIC_DOMAIN),
]


def sanitize(text: Optional[str]) -> str:
    if not text:
        return text or ""
    for pat, repl in SENSITIVE_PATTERNS:
        text = pat.sub(repl, text)
    return text


def _sanitize_log_text(text: Optional[str]) -> str:
    if not text:
        return text or ""
    text = re.sub(r"https?://[^\s\"']+", "<redacted-url>", text)
    text = re.sub(r"\bx-admin-key\s*:\s*[A-Za-z0-9._~+/=-]+", "X-Admin-Key: <redacted>", text, flags=re.I)
    text = re.sub(r"\badmin[_-]?key\s*=\s*[A-Za-z0-9._~+/=-]+", "ADMIN_KEY=<redacted>", text, flags=re.I)
    text = re.sub(r"\bauthorization\s*:\s*bearer\s+[A-Za-z0-9._~+/=-]+", "Authorization: Bearer <redacted>", text, flags=re.I)
    text = re.sub(r"\bbearer\s+[A-Za-z0-9._~+/=-]+", "Bearer <redacted>", text, flags=re.I)
    text = re.sub(r"\brelay_session=[^;\s]+", "relay_session=<redacted>", text, flags=re.I)
    text = re.sub(r"\bsk[-_][A-Za-z0-9][A-Za-z0-9_-]{8,}\b", "<redacted-relay-key>", text)
    text = re.sub(r"\bark[\.\-][\w\.\-]+", "<redacted-upstream-key>", text, flags=re.I)
    return sanitize(text)


AUDIT_SECRET_KEYWORDS = (
    "password",
    "api_key",
    "admin_key",
    "x_admin_key",
    "relay_key",
    "upstream_key",
    "byteplus_key",
    "token",
    "session",
    "authorization",
    "cookie",
    "credential",
    "secret",
)


def _audit_key_has_secret_name(key: str, value=None) -> bool:
    lowered_key = key.lower()
    normalized_key = re.sub(r"[^a-z0-9]+", "_", lowered_key).strip("_")
    if isinstance(value, bool) and normalized_key.endswith("_changed"):
        return False
    return any(
        word in lowered_key or word in normalized_key
        for word in AUDIT_SECRET_KEYWORDS
    )


def _sanitize_audit_metadata(value, key: str = ""):
    if _audit_key_has_secret_name(key, value):
        return "<redacted>"
    if isinstance(value, dict):
        return {
            str(item_key): _sanitize_audit_metadata(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_audit_metadata(item) for item in value]
    if isinstance(value, str):
        return _sanitize_log_text(value)
    return value


def _upstream_request_id(headers) -> Optional[str]:
    for key in ("x-request-id", "x-tt-logid", "x-tt-trace-id", "request-id"):
        try:
            value = headers.get(key)
        except Exception:
            value = None
        if value:
            return sanitize(str(value))[:128]
    return None


def _record_get(record, key: str, default=None):
    if record is None:
        return default
    if isinstance(record, sqlite3.Row):
        return record[key] if key in record.keys() else default
    if isinstance(record, dict):
        return record.get(key, default)
    return getattr(record, key, default)


def _effective_markup_pct(user_or_task=None) -> float:
    value = _record_get(user_or_task, "markup_pct")
    if value is None or value == "":
        return MARKUP_PCT
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return MARKUP_PCT


def _pricing_scope(user_or_task=None) -> str:
    return "customer" if (
        _record_get(user_or_task, "price_multiplier") is not None
        or _record_get(user_or_task, "markup_pct") is not None
    ) else "global"


def _effective_price_multiplier(user_or_task=None) -> float:
    value = _record_get(user_or_task, "price_multiplier")
    if value not in (None, ""):
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            pass
    return round(1 + _effective_markup_pct(user_or_task), 6)


def _with_markup(amount: float, markup_pct: float) -> float:
    return round(float(amount or 0) * (1 + markup_pct), 6)


def _with_multiplier(amount: float, price_multiplier: float) -> float:
    return round(float(amount or 0) * price_multiplier, 6)


def _masked_secret(value: Optional[str], *, prefix: int = 8, suffix: int = 4) -> Optional[str]:
    if value is None:
        return None
    value = str(value)
    if not value:
        return value
    if len(value) <= prefix + suffix:
        return "***"
    return f"{value[:prefix]}...{value[-suffix:]}"


def _generate_api_key() -> str:
    return "sk-" + secrets.token_urlsafe(32)


def _customer_key_metadata(user: dict) -> dict:
    return {
        "api_key_masked": _masked_secret(_record_get(user, "api_key"), prefix=6, suffix=6),
        "api_key_last_rotated_at": _record_get(user, "api_key_last_rotated_at"),
    }


def _enabled_models_for_user(user: Optional[dict]) -> list[str]:
    raw = _record_get(user, "enabled_models")
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return list(NATIVE_MODEL_IDS)
    if isinstance(raw, list):
        items = raw
    else:
        try:
            items = json.loads(raw)
        except (TypeError, ValueError):
            return []
    if not isinstance(items, list):
        return []
    allowed = []
    for item in items:
        model = str(item).strip()
        if model in MODEL_MAP and model not in allowed:
            allowed.append(model)
    return allowed


def _serialize_enabled_models(models: Optional[list[str]]) -> Optional[str]:
    if models is None:
        return None
    allowed = []
    for item in models:
        model = str(item).strip()
        if model not in MODEL_MAP:
            raise HTTPException(400, {"error": {
                "code": "invalid_model",
                "message": f"Unknown model '{model}'",
            }})
        if model not in allowed:
            allowed.append(model)
    return json.dumps(allowed)


def _enabled_models_uses_default(user: Optional[dict]) -> bool:
    raw = _record_get(user, "enabled_models")
    return raw is None or (isinstance(raw, str) and raw.strip() == "")


def _ensure_model_enabled(client_model: str, user: dict) -> None:
    if client_model not in _enabled_models_for_user(user):
        raise HTTPException(403, {"error": {
            "code": "model_not_enabled",
            "message": "This model is not enabled for this customer",
            "model": client_model,
        }})


def _model_public_info(client_model: str) -> dict:
    spec = MODEL_REGISTRY[client_model]
    return {
        "id": client_model,
        "description": spec.get("description", ""),
        "supported_resolutions": list(spec["supported_resolutions"]),
        "supported_ratios": list(spec["supported_ratios"]),
        "duration_seconds": dict(spec["duration_seconds"]),
        "capabilities": {
            "supports_audio": bool(spec["supports_audio"]),
            "supports_reference_image": bool(spec["supports_reference_image"]),
            "supports_reference_video": bool(spec["supports_reference_video"]),
            "supports_reference_audio": bool(spec["supports_reference_audio"]),
        },
    }


def _admin_model_option_info(client_model: str) -> dict:
    item = _model_public_info(client_model)
    alias_for = MODEL_REGISTRY[client_model].get("alias_for")
    item["is_alias"] = bool(alias_for)
    item["alias_for"] = alias_for
    return item


def _validate_model_parameters(client_model: str, req: CreateVideoRequest | EstimateRequest) -> None:
    spec = MODEL_REGISTRY[client_model]
    resolution = req.resolution or "720p"
    if resolution not in spec["supported_resolutions"]:
        raise HTTPException(400, {"error": {
            "code": "unsupported_resolution",
            "message": f"Resolution '{resolution}' is not supported by this model",
            "model": client_model,
            "supported_resolutions": spec["supported_resolutions"],
        }})

    ratio = _request_ratio(req)
    if ratio not in spec["supported_ratios"]:
        raise HTTPException(400, {"error": {
            "code": "unsupported_ratio",
            "message": f"Ratio '{ratio}' is not supported by this model",
            "model": client_model,
            "supported_ratios": spec["supported_ratios"],
        }})

    duration = req.duration if req.duration is not None else 5
    duration_range = spec["duration_seconds"]
    if duration < duration_range["min"] or duration > duration_range["max"]:
        raise HTTPException(400, {"error": {
            "code": "unsupported_duration",
            "message": f"Duration '{duration}' is not supported by this model",
            "model": client_model,
            "duration_seconds": duration_range,
        }})

    if _request_generate_audio(req) and not spec["supports_audio"]:
        raise HTTPException(400, {"error": {
            "code": "unsupported_audio",
            "message": "This model does not support generate_audio",
            "model": client_model,
        }})


def _validate_model_content_requirements(client_model: str, content: list[ContentBlock]) -> None:
    if client_model not in VISUAL_REFERENCE_REQUIRED_MODELS:
        return
    if any(block.type in {"image_url", "video_url"} for block in content):
        return
    raise HTTPException(400, {"error": {
        "code": "visual_reference_required",
        "message": "This model requires at least one image_url or video_url content block",
        "model": client_model,
    }})


def _audit_event(action: str, *, actor_user_id: Optional[str], actor_type: str,
                 target_type: Optional[str] = None, target_id: Optional[str] = None,
                 metadata: Optional[dict] = None) -> None:
    db = get_db()
    db.execute(
        """INSERT INTO audit_events
           (id, actor_user_id, actor_type, action, target_type, target_id,
            metadata_json, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            "aud_" + secrets.token_hex(12),
            actor_user_id,
            actor_type,
            action,
            target_type,
            target_id,
            json.dumps(_sanitize_audit_metadata(metadata or {}), ensure_ascii=True),
            int(time.time()),
        ),
    )


def _reserve_balance_if_available(user_id: str, amount: float) -> bool:
    db = get_db()
    try:
        cur = db.execute(
            "UPDATE users SET balance_usd = balance_usd - ? "
            "WHERE id=? AND balance_usd >= ?",
            (amount, user_id, amount),
        )
        return cur.rowcount > 0
    finally:
        db.close()


def _refund_reserved_balance(user_id: str, amount: float) -> None:
    db = get_db()
    try:
        db.execute(
            "UPDATE users SET balance_usd = balance_usd + ? WHERE id=?",
            (amount, user_id),
        )
    finally:
        db.close()


async def _cancel_upstream_task(upstream_task_id: Optional[str], bp_key: Optional[str]) -> None:
    if not upstream_task_id:
        return
    try:
        await _delete_upstream_task(upstream_task_id, bp_key)
    except Exception:
        pass


def _validate_content_blocks(blocks: list[ContentBlock], *, required: bool = False) -> list[ContentBlock]:
    if required and not blocks:
        raise HTTPException(400, {"error": {
            "code": "missing_content",
            "message": "Provide BytePlus-native content[] blocks",
        }})
    for block in blocks:
        if block.type not in ALLOWED_CONTENT_BLOCK_TYPES:
            raise HTTPException(400, {"error": {
                "code": "invalid_content_block",
                "message": f"Unsupported content block type '{block.type}'",
            }})
        role = (block.role or "").strip()
        if role and role not in ALLOWED_CONTENT_ROLES:
            raise HTTPException(400, {"error": {
                "code": "invalid_content_role",
                "message": f"Unsupported content role '{role}'",
            }})
    for block_type, (code, limit) in CONTENT_BLOCK_LIMITS.items():
        count = sum(1 for block in blocks if block.type == block_type)
        if count > limit:
            raise HTTPException(400, {"error": {
                "code": code,
                "message": f"Too many {block_type} content blocks; maximum is {limit}",
            }})
    return blocks


def _request_content_blocks(req: Any, *, required: bool = False) -> list[ContentBlock]:
    return _validate_content_blocks(list(req.content or []), required=required)


def _request_ratio(req: CreateVideoRequest | EstimateRequest) -> str:
    return req.ratio or "16:9"


def _request_generate_audio(req: CreateVideoRequest | EstimateRequest) -> bool:
    if req.generate_audio is not None:
        return bool(req.generate_audio)
    return False


def _real_person_mode(req: CreateVideoRequest | EstimateRequest) -> bool:
    return bool((req.extra_body or {}).get("real_person_mode"))


# ─── DB ──────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                       TEXT PRIMARY KEY,
    api_key                  TEXT UNIQUE NOT NULL,
    email                    TEXT UNIQUE,
    balance_usd              REAL NOT NULL DEFAULT 0,
    markup_pct               REAL,
    price_multiplier         REAL NOT NULL DEFAULT 1.0,
    enabled_models           TEXT,
    is_active                INTEGER NOT NULL DEFAULT 1,
    is_admin                 INTEGER NOT NULL DEFAULT 0,
    password_hash            TEXT,
    byteplus_api_key         TEXT,
    byteplus_account_label   TEXT,
    note                     TEXT,
    api_key_last_rotated_at  INTEGER,
    password_changed_at      INTEGER,
    failed_login_count       INTEGER NOT NULL DEFAULT 0,
    locked_until             INTEGER,
    created_at               INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id                       TEXT PRIMARY KEY,
    user_id                  TEXT NOT NULL,
    upstream_task_id         TEXT UNIQUE NOT NULL,
    upstream_model           TEXT NOT NULL,
    client_model             TEXT NOT NULL,
    resolution               TEXT,
    duration                 INTEGER,
    has_video_ref            INTEGER DEFAULT 0,
    status                   TEXT NOT NULL DEFAULT 'queued',
    estimated_cost_usd       REAL,
    held_usd                 REAL,
    actual_cost_usd          REAL,
    upstream_actual_cost_usd REAL,
    markup_pct               REAL,
    price_multiplier         REAL,
    completion_tokens        INTEGER,
    settled                  INTEGER NOT NULL DEFAULT 0,
    cached_video_url         TEXT,
    cached_video_url_until   INTEGER,
    local_video_path         TEXT,
    prompt_text              TEXT,
    request_payload          TEXT,
    created_at               INTEGER NOT NULL,
    updated_at               INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token         TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    expires_at    INTEGER NOT NULL,
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS face_assets (
    asset_url      TEXT PRIMARY KEY,
    asset_id       TEXT NOT NULL,
    asset_type     TEXT NOT NULL,
    label          TEXT,
    note           TEXT,
    is_active      INTEGER NOT NULL DEFAULT 1,
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS uploads (
    id                       TEXT PRIMARY KEY,
    user_id                  TEXT NOT NULL,
    url                      TEXT NOT NULL,
    object_key               TEXT NOT NULL,
    content_type             TEXT NOT NULL,
    size_bytes               INTEGER NOT NULL,
    purpose                  TEXT NOT NULL,
    original_filename        TEXT,
    asset_id                 TEXT,
    asset_url                TEXT,
    asset_status             TEXT,
    face_asset_whitelisted   INTEGER NOT NULL DEFAULT 0,
    face_asset_label         TEXT,
    face_asset_note          TEXT,
    created_at               INTEGER NOT NULL,
    updated_at               INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key            TEXT PRIMARY KEY,
    value          TEXT NOT NULL,
    updated_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id             TEXT PRIMARY KEY,
    actor_user_id  TEXT,
    actor_type     TEXT NOT NULL,
    action         TEXT NOT NULL,
    target_type    TEXT,
    target_id      TEXT,
    metadata_json  TEXT,
    created_at     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_user      ON tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_upstream  ON tasks(upstream_task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status    ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_face_assets_active ON face_assets(is_active);
CREATE INDEX IF NOT EXISTS idx_uploads_user_created ON uploads(user_id, created_at DESC);
"""

# 现有 DB 升级到新 schema (添加新字段, 已存在则跳过)
MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN password_hash TEXT",
    "ALTER TABLE users ADD COLUMN byteplus_api_key TEXT",
    "ALTER TABLE users ADD COLUMN byteplus_account_label TEXT",
    "ALTER TABLE users ADD COLUMN note TEXT",
    "ALTER TABLE users ADD COLUMN markup_pct REAL",
    "ALTER TABLE users ADD COLUMN price_multiplier REAL NOT NULL DEFAULT 1.0",
    "ALTER TABLE users ADD COLUMN enabled_models TEXT",
    "ALTER TABLE users ADD COLUMN api_key_last_rotated_at INTEGER",
    "ALTER TABLE users ADD COLUMN password_changed_at INTEGER",
    "ALTER TABLE users ADD COLUMN failed_login_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN locked_until INTEGER",
    "ALTER TABLE tasks ADD COLUMN upstream_actual_cost_usd REAL",
    "ALTER TABLE tasks ADD COLUMN markup_pct REAL",
    "ALTER TABLE tasks ADD COLUMN price_multiplier REAL",
    "ALTER TABLE tasks ADD COLUMN completion_tokens INTEGER",
    "ALTER TABLE tasks ADD COLUMN local_video_path TEXT",
    "ALTER TABLE tasks ADD COLUMN prompt_text TEXT",
    "ALTER TABLE tasks ADD COLUMN request_payload TEXT",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)",
]


def get_db() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    for m in MIGRATIONS:
        try:
            conn.execute(m)
        except sqlite3.OperationalError:
            pass   # 已存在
    backfilled = conn.execute(
        "SELECT value FROM settings WHERE key=?",
        (PRICE_MULTIPLIER_BACKFILL_SETTING,),
    ).fetchone()
    if not backfilled:
        conn.execute(
            "UPDATE users SET price_multiplier = 1 + markup_pct "
            "WHERE markup_pct IS NOT NULL AND (price_multiplier IS NULL OR price_multiplier = 1.0)"
        )
        conn.execute(
            "UPDATE users SET price_multiplier = ? "
            "WHERE markup_pct IS NULL AND (price_multiplier IS NULL OR price_multiplier = 1.0)",
            (1 + MARKUP_PCT,),
        )
        conn.execute(
            "UPDATE tasks SET price_multiplier = 1 + markup_pct "
            "WHERE markup_pct IS NOT NULL AND price_multiplier IS NULL"
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?,?,?)",
            (PRICE_MULTIPLIER_BACKFILL_SETTING, "done", int(time.time())),
        )
    return conn


# ─── Password / Session ──────────────────────────────────────────
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=10)).decode()


def verify_password(plain: str, hashed: Optional[str]) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    db = get_db()
    db.execute(
        "INSERT INTO sessions (token, user_id, expires_at, created_at) VALUES (?,?,?,?)",
        (token, user_id, now + SESSION_TTL_SECS, now),
    )
    return token


def revoke_session(token: str):
    if not token:
        return
    db = get_db()
    db.execute("DELETE FROM sessions WHERE token=?", (token,))


def lookup_session(token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    db = get_db()
    row = db.execute(
        "SELECT s.*, u.id u_id, u.email, u.balance_usd, u.is_active, u.is_admin, "
        "       u.byteplus_api_key, u.byteplus_account_label, u.api_key "
        "FROM sessions s JOIN users u ON s.user_id=u.id WHERE s.token=?",
        (token,),
    ).fetchone()
    if not row:
        return None
    if row["expires_at"] < int(time.time()):
        db.execute("DELETE FROM sessions WHERE token=?", (token,))
        return None
    if not row["is_active"]:
        return None
    return dict(row)


def cleanup_expired_sessions():
    db = get_db()
    db.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))


# ─── 启动: 确保 admin 用户存在 ───────────────────────────────────
def ensure_admin_user():
    if not ADMIN_PASSWORD:
        print("ADMIN_PASSWORD not set; skipping admin user creation")
        return
    db = get_db()
    row = db.execute("SELECT id FROM users WHERE email=?", (ADMIN_EMAIL,)).fetchone()
    if row:
        return
    user_id = "u_" + secrets.token_hex(8)
    api_key = "sk-" + secrets.token_urlsafe(32)
    db.execute(
        """INSERT INTO users (id, api_key, email, balance_usd, is_active, is_admin,
                              password_hash, created_at)
           VALUES (?,?,?,?,1,1,?,?)""",
        (user_id, api_key, ADMIN_EMAIL, 0.0,
         hash_password(ADMIN_PASSWORD), int(time.time())),
    )
    print(f"Created admin user: {ADMIN_EMAIL} id={user_id}")


# ─── Auth dependencies ──────────────────────────────────────────
def _bearer_token_from_authorization(authorization: Optional[str]) -> Optional[str]:
    raw = (authorization or "").strip()
    if not raw or not raw.lower().startswith("bearer "):
        return None
    token = raw[len("Bearer "):].strip()
    return token or None


def auth_user(authorization: Optional[str] = Header(None),
              relay_session: Optional[str] = Cookie(None)) -> dict:
    """普通用户鉴权: Bearer token (API) 或 session cookie (Web)。"""
    # 1) Bearer
    if authorization:
        api_key = _bearer_token_from_authorization(authorization)
        if not api_key:
            raise HTTPException(401, {"error": {"code": "missing_auth",
                                                "message": "Authentication required"}})
        db = get_db()
        u = db.execute(
            "SELECT * FROM users WHERE api_key=? AND is_active=1", (api_key,)
        ).fetchone()
        if u:
            return dict(u)
        raise HTTPException(401, {"error": {"code": "missing_auth",
                                            "message": "Authentication required"}})
    # 2) Session cookie
    sess = lookup_session(relay_session)
    if sess:
        db = get_db()
        u = db.execute("SELECT * FROM users WHERE id=? AND is_active=1",
                       (sess["user_id"],)).fetchone()
        if u:
            return dict(u)
    raise HTTPException(401, {"error": {"code": "missing_auth",
                                        "message": "Authentication required"}})


def optional_auth_user(authorization: Optional[str] = Header(None),
                       relay_session: Optional[str] = Cookie(None)) -> Optional[dict]:
    try:
        return auth_user(authorization, relay_session)
    except HTTPException as exc:
        if authorization or relay_session:
            raise exc
        return None


def auth_admin(x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
               authorization: Optional[str] = Header(None),
               relay_session: Optional[str] = Cookie(None)):
    """admin 鉴权: X-Admin-Key (脚本) 或 admin user 的 Bearer/session"""
    if x_admin_key and ADMIN_KEY and x_admin_key == ADMIN_KEY:
        return {"id": "admin_via_env_key"}
    try:
        u = auth_user(authorization, relay_session)
    except HTTPException:
        raise HTTPException(403, {"error": {"code": "forbidden",
                                            "message": "Admin access required"}})
    if not u.get("is_admin"):
        raise HTTPException(403, {"error": {"code": "forbidden",
                                            "message": "Admin access required"}})
    return u


# ─── Upstream HTTP helpers ───────────────────────────────────────
def upstream_headers(api_key: Optional[str] = None) -> dict:
    """没传 api_key 就回退到全局 (主要给 admin / system 调用)。"""
    return {"Content-Type": "application/json",
            "Authorization": f"Bearer {api_key or UPSTREAM_API_KEY}"}


def _upstream_iam_enabled(bp_key: Optional[str] = None) -> bool:
    return UPSTREAM_AUTH_MODE in {"iam", "aksk", "access_key"} and not bp_key


def _upstream_iam_ready() -> bool:
    return bool(BYTEPLUS_ACCESSKEY and BYTEPLUS_SECRETKEY and UPSTREAM_ENDPOINT_ID)


def _upstream_model_for_request(real_model: str, bp_key: Optional[str] = None) -> str:
    if _upstream_iam_enabled(bp_key):
        if not _upstream_iam_ready():
            raise HTTPException(503, {"error": {
                "code": "iam_upstream_not_configured",
                "message": "IAM upstream mode requires BYTEPLUS_ACCESSKEY, BYTEPLUS_SECRETKEY, and UPSTREAM_ENDPOINT_ID",
            }})
        return UPSTREAM_ENDPOINT_ID
    return real_model


def _sdk_payload_to_dict(payload: Any) -> dict:
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return payload
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_none=True)
    if hasattr(payload, "dict"):
        return payload.dict()
    if hasattr(payload, "to_dict"):
        return payload.to_dict()
    data = {}
    for key in ("id", "status", "content", "usage", "error"):
        if hasattr(payload, key):
            data[key] = getattr(payload, key)
    return data


class _UpstreamSDKResponse:
    def __init__(self, payload: Any, status_code: int = 200):
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self.text = json.dumps(_sdk_payload_to_dict(payload), ensure_ascii=False)
        self._payload = payload

    def json(self) -> dict:
        return _sdk_payload_to_dict(self._payload)


_ark_iam_client: Any = None


def _get_ark_iam_client() -> Any:
    global _ark_iam_client
    if _ark_iam_client is None:
        try:
            from byteplussdkarkruntime import Ark
        except Exception as exc:
            raise RuntimeError(
                "byteplus-python-sdk-v2 is required for UPSTREAM_AUTH_MODE=iam"
            ) from exc
        _ark_iam_client = Ark(
            ak=BYTEPLUS_ACCESSKEY,
            sk=BYTEPLUS_SECRETKEY,
            base_url=UPSTREAM_BASE_URL,
        )
    return _ark_iam_client


def _content_generation_tasks(client: Any) -> Any:
    return client.content_generation.tasks


async def _create_upstream_task(payload: dict, bp_key: Optional[str]) -> _UpstreamSDKResponse | httpx.Response:
    if not _upstream_iam_enabled(bp_key):
        return await http.post(
            f"{UPSTREAM_BASE_URL}/contents/generations/tasks",
            json=payload,
            headers=upstream_headers(bp_key),
            timeout=60,
        )

    def call_sdk() -> Any:
        tasks = _content_generation_tasks(_get_ark_iam_client())
        if hasattr(tasks, "create"):
            return tasks.create(**payload)
        if hasattr(tasks, "create_task"):
            return tasks.create_task(**payload)
        raise RuntimeError("BytePlus Ark SDK does not expose content_generation.tasks.create")

    return _UpstreamSDKResponse(await asyncio.to_thread(call_sdk))


async def _get_upstream_task(upstream_task_id: str, bp_key: Optional[str]) -> _UpstreamSDKResponse | httpx.Response:
    if not _upstream_iam_enabled(bp_key):
        return await http.get(
            f"{UPSTREAM_BASE_URL}/contents/generations/tasks/{upstream_task_id}",
            headers=upstream_headers(bp_key),
            timeout=30,
        )

    def call_sdk() -> Any:
        tasks = _content_generation_tasks(_get_ark_iam_client())
        for method in ("retrieve", "get", "get_task"):
            if hasattr(tasks, method):
                return getattr(tasks, method)(upstream_task_id)
        raise RuntimeError("BytePlus Ark SDK does not expose a task retrieval method")

    return _UpstreamSDKResponse(await asyncio.to_thread(call_sdk))


async def _delete_upstream_task(upstream_task_id: str, bp_key: Optional[str]) -> _UpstreamSDKResponse | httpx.Response:
    if not _upstream_iam_enabled(bp_key):
        return await http.delete(
            f"{UPSTREAM_BASE_URL}/contents/generations/tasks/{quote(upstream_task_id, safe='')}",
            headers=upstream_headers(bp_key),
            timeout=30,
        )

    def call_sdk() -> Any:
        tasks = _content_generation_tasks(_get_ark_iam_client())
        for method in ("delete", "cancel", "cancel_task"):
            if hasattr(tasks, method):
                return getattr(tasks, method)(upstream_task_id)
        raise RuntimeError("BytePlus Ark SDK does not expose a task delete/cancel method")

    return _UpstreamSDKResponse(await asyncio.to_thread(call_sdk), status_code=200)


# ─── App ─────────────────────────────────────────────────────────
http: Optional[httpx.AsyncClient] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http
    http = httpx.AsyncClient(timeout=httpx.Timeout(120.0))
    if not UPSTREAM_API_KEY:
        print("UPSTREAM_API_KEY not set; users must provide byteplus_api_key")
    ensure_admin_user()
    cleanup_expired_sessions()
    yield
    await http.aclose()


app = FastAPI(
    title=f"{BRAND_NAME} Video API",
    description="Video generation API",
    version="1.0.0",
    lifespan=lifespan,
)

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


def _host_without_port(value: str) -> str:
    value = (value or "").strip().lower()
    if not value:
        return ""
    if "://" in value:
        value = urlparse(value).netloc
    if value.startswith("["):
        return value.split("]", 1)[0].lstrip("[")
    return value.split(":", 1)[0]


def _public_domain_host() -> str:
    value = PUBLIC_DOMAIN.strip()
    if value.startswith(("http://", "https://")):
        return _host_without_port(value)
    return _host_without_port(value)


def _same_origin_write_allowed(request: Request) -> bool:
    source = request.headers.get("origin") or request.headers.get("referer") or ""
    if not source:
        return False
    source_host = _host_without_port(source)
    allowed = {
        _host_without_port(request.headers.get("host", "")),
        _public_domain_host(),
    }
    allowed.discard("")
    return source_host in allowed


@app.middleware("http")
async def csrf_origin_guard(request: Request, call_next):
    if request.method.upper() in {"POST", "PATCH", "DELETE"}:
        has_cookie_session = bool(request.cookies.get("relay_session"))
        auth = request.headers.get("authorization", "")
        has_bearer = _bearer_token_from_authorization(auth) is not None
        has_admin_key = bool(request.headers.get("x-admin-key"))
        if has_cookie_session and not has_bearer and not has_admin_key:
            if not _same_origin_write_allowed(request):
                return JSONResponse(
                    status_code=403,
                    content={"detail": {"error": {
                        "code": "csrf_origin_mismatch",
                        "message": "Cookie-authenticated write requests require same-origin Origin or Referer",
                    }}},
                )
    return await call_next(request)


@app.get("/health")
def health():
    return {"status": "ok", "brand": BRAND_NAME, "models": list(MODEL_MAP.keys())}


# ─── Pydantic 客户端契约 ─────────────────────────────────────────
class ContentBlock(BaseModel):
    type: str
    text: Optional[str] = None
    image_url: Optional[dict] = None
    video_url: Optional[dict] = None
    audio_url: Optional[dict] = None
    role: Optional[str] = None


class CreateVideoRequest(BaseModel):
    model: str
    content: List[ContentBlock]
    resolution: Optional[str] = "720p"
    ratio: Optional[str] = "16:9"
    duration: Optional[int] = 5
    seed: Optional[int] = None
    watermark: Optional[bool] = False
    generate_audio: Optional[bool] = None
    extra_body: Optional[dict[str, Any]] = None


class EstimateRequest(BaseModel):
    """跟 CreateVideoRequest 一样, 但 content 可选（用户没想好内容也能估）。"""
    model: str
    content: Optional[List[ContentBlock]] = None
    resolution: Optional[str] = "720p"
    ratio: Optional[str] = "16:9"
    duration: Optional[int] = 5
    frames: Optional[int] = None
    generate_audio: Optional[bool] = None
    extra_body: Optional[dict[str, Any]] = None


class RuntimePrepareVideoContentRequest(BaseModel):
    user_id: str
    content: List[ContentBlock]
    extra_body: Optional[dict[str, Any]] = None


class LoginRequest(BaseModel):
    email: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=10)


class CreateUserRequest(BaseModel):
    email: str
    password: Optional[str] = Field(None, min_length=10)  # 不传就生成临时密码并返回
    balance_usd: float = 0.0
    markup_pct: Optional[float] = Field(None, ge=0, le=10)
    price_multiplier: Optional[float] = Field(None, ge=1, le=10)
    enabled_models: Optional[List[str]] = None
    api_key: Optional[str] = None       # legacy: reject manual values; server generates sk-...
    byteplus_api_key: Optional[str] = None
    byteplus_account_label: Optional[str] = None
    note: Optional[str] = None
    is_admin: bool = False


class TopupReq(BaseModel):
    amount_usd: float
    note: Optional[str] = None


class UpdateUserReq(BaseModel):
    balance_usd: Optional[float] = None
    markup_pct: Optional[float] = Field(None, ge=0, le=10)
    price_multiplier: Optional[float] = Field(None, ge=1, le=10)
    enabled_models: Optional[List[str]] = None
    api_key: Optional[str] = None       # legacy: reject manual edits; use rotate endpoint
    byteplus_api_key: Optional[str] = None
    byteplus_account_label: Optional[str] = None
    is_active: Optional[bool] = None
    note: Optional[str] = None
    new_password: Optional[str] = Field(None, min_length=10)


class FaceAssetReq(BaseModel):
    asset_url: str
    asset_type: str = Field("image", pattern="^(image|video)$")
    label: Optional[str] = None
    note: Optional[str] = None
    is_active: bool = True


class UploadFromUrlRequest(BaseModel):
    url: str
    content_type: Optional[str] = None
    purpose: Optional[str] = None
    size_bytes: Optional[int] = Field(None, ge=0)
    original_filename: Optional[str] = None
    face_allowlist: bool = False
    face_asset_label: Optional[str] = None
    face_asset_note: Optional[str] = None


# ─── 内部工具 ────────────────────────────────────────────────────
def _normalize_asset_url(asset_url: str) -> str:
    value = (asset_url or "").strip()
    if not value.startswith("asset://"):
        value = "asset://" + value.removeprefix("asset:")
    return value


def _asset_id_from_url(asset_url: str) -> str:
    return _normalize_asset_url(asset_url).replace("asset://", "", 1)


def _active_face_asset_allowlist() -> set[str]:
    allowed = {_normalize_asset_url(item) for item in FACE_ASSET_ALLOWLIST if item}
    try:
        db = get_db()
        rows = db.execute(
            "SELECT asset_url FROM face_assets WHERE is_active=1"
        ).fetchall()
        allowed.update(_normalize_asset_url(row["asset_url"]) for row in rows)
        db.close()
    except Exception:
        pass
    return allowed


def _upsert_face_asset_record(
    asset_url: str,
    asset_type: str,
    label: Optional[str] = None,
    note: Optional[str] = None,
    is_active: bool = True,
) -> dict:
    normalized = _normalize_asset_url(asset_url)
    if not normalized.startswith("asset://") or normalized == "asset://":
        raise HTTPException(400, {"error": {
            "code": "invalid_asset_url",
            "message": "asset_url must be an asset:// URI",
        }})
    now = int(time.time())
    db = get_db()
    db.execute(
        """INSERT INTO face_assets
           (asset_url, asset_id, asset_type, label, note, is_active, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(asset_url) DO UPDATE SET
             asset_type=excluded.asset_type,
             label=excluded.label,
             note=excluded.note,
             is_active=excluded.is_active,
             updated_at=excluded.updated_at""",
        (
            normalized,
            _asset_id_from_url(normalized),
            asset_type,
            label,
            note,
            int(is_active),
            now,
            now,
        ),
    )
    row = db.execute("SELECT * FROM face_assets WHERE asset_url=?", (normalized,)).fetchone()
    db.close()
    return dict(row)


def _content_url_for_block(block: ContentBlock) -> str:
    if block.type == "image_url" and isinstance(block.image_url, dict):
        return str(block.image_url.get("url") or "")
    if block.type == "video_url" and isinstance(block.video_url, dict):
        return str(block.video_url.get("url") or "")
    return ""


def _validate_face_asset_allowlist(content: list[ContentBlock]) -> None:
    if not FACE_ASSET_ENFORCE:
        return
    allowed = _active_face_asset_allowlist()
    if not allowed:
        raise HTTPException(503, {"error": {
            "code": "face_asset_allowlist_empty",
            "message": "Face asset whitelist is enabled but empty",
        }})

    for block in content:
        if block.type not in ("image_url", "video_url"):
            continue
        role = (block.role or "").strip().lower()
        if role not in FACE_ASSET_ENFORCE_ROLES:
            continue
        url = _content_url_for_block(block).strip()
        if not url.startswith("asset://"):
            raise HTTPException(400, {"error": {
                "code": "face_asset_requires_asset_uri",
                "message": f"{role} inputs must use a server-approved asset:// URI",
            }})
        normalized = _normalize_asset_url(url)
        if normalized not in allowed:
            raise HTTPException(403, {"error": {
                "code": "face_asset_not_whitelisted",
                "message": "This face asset is not enabled on the server whitelist",
                "asset_url": normalized,
            }})


def _asset_urls_from_content(content: list[ContentBlock]) -> list[str]:
    asset_urls: list[str] = []
    for block in content:
        if block.type not in ("image_url", "video_url"):
            continue
        url = _content_url_for_block(block).strip()
        if url.startswith("asset://"):
            asset_urls.append(_normalize_asset_url(url))
    return sorted(set(asset_urls))


def _validate_customer_asset_access(content: list[ContentBlock], user_id: str) -> None:
    asset_urls = _asset_urls_from_content(content)
    if not asset_urls:
        return

    global_allowlist = _active_face_asset_allowlist()
    db = get_db()
    try:
        for asset_url in asset_urls:
            rows = db.execute(
                "SELECT DISTINCT user_id FROM uploads WHERE asset_url=?",
                (asset_url,),
            ).fetchall()
            if not rows:
                continue
            owners = {row["user_id"] for row in rows}
            if user_id not in owners and asset_url not in global_allowlist:
                raise HTTPException(403, {"error": {
                    "code": "asset_not_owned",
                    "message": "This asset:// material belongs to another account",
                    "asset_url": asset_url,
                }})
    finally:
        db.close()


def _format_task(t: dict, error: Optional[str] = None) -> dict:
    out = {
        "id": t["id"],
        "model": t["client_model"],
        "status": t["status"],
        "resolution": t.get("resolution"),
        "duration": t.get("duration"),
        "completion_tokens": t.get("completion_tokens"),
        "estimated_cost_usd": t.get("estimated_cost_usd"),
        "actual_cost_usd": t.get("actual_cost_usd"),
        "prompt_text": t.get("prompt_text"),
        "created_at": t["created_at"],
        "updated_at": t["updated_at"],
    }
    if t.get("status") == "succeeded":
        out["video_url"] = f"https://{PUBLIC_DOMAIN}/v1/videos/{t['id']}/content"
    if error:
        out["error"] = {"message": sanitize(error)}
    return out


def _apply_terminal_task_refresh_once(
    db: sqlite3.Connection,
    *,
    task_id: str,
    user_id: str,
    status: str,
    actual_cost_usd: float,
    upstream_actual_cost_usd: float,
    completion_tokens: Optional[int],
    cached_video_url: Optional[str],
    cached_video_url_until: int,
    updated_at: int,
    refund_usd: float,
) -> dict:
    try:
        db.execute("BEGIN IMMEDIATE")
        cursor = db.execute(
            """UPDATE tasks
              SET status=?, actual_cost_usd=?,
                  upstream_actual_cost_usd=?, completion_tokens=?,
                  settled=1,
                  cached_video_url=?, cached_video_url_until=?, updated_at=?
              WHERE id=? AND user_id=? AND settled=0""",
            (
                status,
                actual_cost_usd,
                upstream_actual_cost_usd,
                completion_tokens,
                cached_video_url,
                cached_video_url_until,
                updated_at,
                task_id,
                user_id,
            ),
        )
        if cursor.rowcount > 0 and refund_usd > 0:
            db.execute(
                "UPDATE users SET balance_usd = balance_usd + ? WHERE id = ?",
                (refund_usd, user_id),
            )
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise

    row = db.execute("SELECT * FROM tasks WHERE id=? AND user_id=?", (task_id, user_id)).fetchone()
    return dict(row) if row else {}


def _cancel_task_once(
    db: sqlite3.Connection,
    *,
    task_id: str,
    user_id: str,
    held_usd: float,
    updated_at: int,
) -> None:
    try:
        db.execute("BEGIN IMMEDIATE")
        cursor = db.execute(
            "UPDATE tasks SET status='cancelled', settled=1, updated_at=? WHERE id=? AND user_id=? AND settled=0",
            (updated_at, task_id, user_id),
        )
        if cursor.rowcount > 0 and held_usd > 0:
            db.execute(
                "UPDATE users SET balance_usd = balance_usd + ? WHERE id=?",
                (held_usd, user_id),
            )
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise


async def _refresh_task(task_id: str, user_id: str) -> dict:
    db = get_db()
    t = db.execute("SELECT * FROM tasks WHERE id=? AND user_id=?",
                   (task_id, user_id)).fetchone()
    if not t:
        raise HTTPException(404, {"error": {"code": "not_found",
                                            "message": "video not found"}})
    t = dict(t)
    if t["settled"]:
        return t

    # 用提交时使用的 BytePlus key 来查询(因为 task 是用那把 key 创建的)
    user_row = db.execute("SELECT byteplus_api_key, markup_pct, price_multiplier FROM users WHERE id=?",
                          (user_id,)).fetchone()
    bp_key = user_row["byteplus_api_key"] if user_row else None
    if _upstream_iam_enabled():
        bp_key = None

    r = await _get_upstream_task(t["upstream_task_id"], bp_key)
    if r.status_code != 200:
        return t
    info = r.json()
    new_status = info.get("status", t["status"])
    now = int(time.time())

    cached_url = t.get("cached_video_url")
    cached_until = t.get("cached_video_url_until") or 0
    if new_status == "succeeded":
        url = (info.get("content") or {}).get("video_url")
        if url:
            cached_url = url
            cached_until = now + 23 * 3600

    completion_tokens = (info.get("usage") or {}).get("completion_tokens")
    actual_cost_usd = t.get("actual_cost_usd")
    upstream_cost = t.get("upstream_actual_cost_usd")
    if new_status in ("succeeded", "failed", "cancelled", "expired") \
            and not t["settled"]:
        if new_status == "succeeded":
            upstream_cost = actual_video_cost(
                info, has_video_ref=bool(t["has_video_ref"]))
            price_multiplier = _effective_price_multiplier(
                t if t.get("price_multiplier") is not None else user_row
            )
            actual_to_user = upstream_cost * price_multiplier
        else:
            upstream_cost = 0.0
            actual_to_user = 0.0
        actual_cost_usd = round(actual_to_user, 6)
        upstream_cost = round(upstream_cost or 0.0, 6)
        refund = max(0.0, (t["held_usd"] or 0) - actual_to_user)
        t = _apply_terminal_task_refresh_once(
            db,
            task_id=task_id,
            user_id=user_id,
            status=new_status,
            actual_cost_usd=actual_cost_usd,
            upstream_actual_cost_usd=upstream_cost,
            completion_tokens=completion_tokens,
            cached_video_url=cached_url,
            cached_video_url_until=cached_until,
            updated_at=now,
            refund_usd=refund,
        )
        # 任务成功后异步把视频拉到本地
        if new_status == "succeeded" and cached_url and VIDEO_PERSIST_MODE != "proxy_only":
            asyncio.create_task(_persist_video(task_id, cached_url))
    else:
        db.execute("""UPDATE tasks SET status=?, cached_video_url=?,
                      cached_video_url_until=?, updated_at=? WHERE id=?""",
                   (new_status, cached_url, cached_until, now, task_id))

    t["status"] = new_status
    t["cached_video_url"] = cached_url
    t["cached_video_url_until"] = cached_until
    t["actual_cost_usd"] = actual_cost_usd
    t["upstream_actual_cost_usd"] = upstream_cost
    t["completion_tokens"] = completion_tokens
    return t


async def _persist_video(task_id: str, url: str) -> None:
    """succeeded 后台落地视频到本地。已存在则跳过。"""
    out = VIDEO_DIR / f"{task_id}.mp4"
    if out.exists():
        return
    try:
        tmp = out.with_suffix(".mp4.partial")
        async with http.stream("GET", url, timeout=300) as r:
            with open(tmp, "wb") as f:
                async for chunk in r.aiter_bytes(64 * 1024):
                    f.write(chunk)
        tmp.rename(out)
        db = get_db()
        db.execute("UPDATE tasks SET local_video_path=? WHERE id=?",
                   (str(out), task_id))
        print(f"Persisted video {task_id} -> {out} ({out.stat().st_size} bytes)")
    except Exception as e:
        print(f"Failed to persist video {task_id}: {_sanitize_log_text(str(e))}")


def _public_url_for_object_key(object_key: str) -> str:
    if UPLOAD_PUBLIC_BASE_URL:
        return f"{UPLOAD_PUBLIC_BASE_URL}/{object_key}"
    base = PUBLIC_DOMAIN if PUBLIC_DOMAIN.startswith(("http://", "https://")) else f"https://{PUBLIC_DOMAIN}"
    return f"{base.rstrip('/')}/{object_key}"


EXTENSION_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
}


def _filename_from_url(url: str) -> str:
    path = unquote(urlparse(url).path or "")
    name = Path(path).name
    return name[:255]


def _external_upload_spec(url: str, content_type: Optional[str], purpose: Optional[str],
                          size_bytes: Optional[int]) -> tuple[str, dict, str]:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(400, {"error": {
            "code": "invalid_upload_url",
            "message": "url must be a public http(s) URL",
        }})

    normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if not normalized_content_type:
        ext = Path(parsed.path.lower()).suffix
        normalized_content_type = EXTENSION_CONTENT_TYPES.get(ext, "")
    spec = ALLOWED_UPLOAD_TYPES.get(normalized_content_type)
    if not spec:
        raise HTTPException(400, {"error": {
            "code": "invalid_upload_type",
            "message": "content_type or URL extension must be a whitelisted image, video, or audio type",
            "allowed_content_types": sorted(ALLOWED_UPLOAD_TYPES.keys()),
        }})

    inferred_purpose = spec["purpose"]
    if purpose and purpose.strip().lower() != inferred_purpose:
        raise HTTPException(400, {"error": {
            "code": "invalid_upload_purpose",
            "message": f"{normalized_content_type} uploads must use purpose={inferred_purpose}",
        }})
    if size_bytes is not None:
        max_bytes = int(float(spec["max_mb"]) * 1024 * 1024)
        if size_bytes > max_bytes:
            raise HTTPException(413, {"error": {
                "code": "upload_too_large",
                "message": f"Remote file exceeds {spec['max_mb']} MB limit",
                "max_bytes": max_bytes,
            }})
    return normalized_content_type, spec, inferred_purpose


def _suggested_content_block(purpose: str, url: str, role: Optional[str] = None) -> dict:
    if purpose == "image":
        return {"type": "image_url", "image_url": {"url": url}, "role": role or "first_frame"}
    if purpose == "video":
        return {"type": "video_url", "video_url": {"url": url}, "role": role or "reference_video"}
    if purpose == "audio":
        return {"type": "audio_url", "audio_url": {"url": url}, "role": role or "reference_audio"}
    return {}


_ASSET_GROUP_SETTING_KEY = "modelark_asset_group_id"


def _get_setting(key: str) -> str:
    db = get_db()
    try:
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else ""
    finally:
        db.close()


def _set_setting(key: str, value: str) -> None:
    now = int(time.time())
    db = get_db()
    try:
        db.execute(
            """INSERT INTO settings (key, value, updated_at) VALUES (?,?,?)
               ON CONFLICT(key) DO UPDATE SET
                 value=excluded.value,
                 updated_at=excluded.updated_at""",
            (key, value, now),
        )
    finally:
        db.close()


def _call_asset_api(action: str, body: dict, ak: str, sk: str) -> dict:
    try:
        return request_asset_api(action, body, ak, sk)
    except SystemExit as exc:
        raise HTTPException(502, {"error": {
            "code": "asset_registry_error",
            "message": str(exc),
        }}) from exc


def _create_and_cache_asset_group(ak: str, sk: str) -> str:
    body = build_create_asset_group_body(
        name=MODELARK_ASSET_GROUP_NAME,
        description=MODELARK_ASSET_GROUP_DESCRIPTION,
        project_name=MODELARK_PROJECT_NAME,
    )
    result = _call_asset_api("CreateAssetGroup", body, ak, sk)
    group_id = extract_asset_group_id(result)
    if not group_id:
        raise HTTPException(502, {"error": {
            "code": "asset_group_registry_error",
            "message": "Asset group registry did not return a group id",
        }})
    _set_setting(_ASSET_GROUP_SETTING_KEY, group_id)
    return group_id


def _server_asset_config() -> tuple[str, str, str]:
    ak = os.getenv("BYTEPLUS_ACCESS_KEY_ID", "").strip()
    sk = os.getenv("BYTEPLUS_ACCESS_KEY_SECRET", "").strip()
    group_id = os.getenv("MODELARK_ASSET_GROUP_ID", "").strip() or _get_setting(_ASSET_GROUP_SETTING_KEY)
    if ak and sk and not group_id and MODELARK_ASSET_AUTO_CREATE_GROUP:
        group_id = _create_and_cache_asset_group(ak, sk)
    missing = [
        name for name, value in (
            ("BYTEPLUS_ACCESS_KEY_ID", ak),
            ("BYTEPLUS_ACCESS_KEY_SECRET", sk),
            ("MODELARK_ASSET_GROUP_ID", group_id),
        )
        if not value
    ]
    if missing:
        raise HTTPException(503, {"error": {
            "code": "asset_registry_not_configured",
            "message": "Server asset registration is not configured",
            "missing": missing,
        }})
    return ak, sk, group_id


def _asset_type_for_purpose(purpose: str) -> str:
    return {"image": "Image", "video": "Video", "audio": "Audio"}[purpose]


def _register_upload_asset(url: str, purpose: str) -> dict:
    ak, sk, group_id = _server_asset_config()
    body = build_create_asset_body(
        group_id=group_id,
        url=url,
        asset_type=_asset_type_for_purpose(purpose),
        skip_moderation=ASSET_AUTO_REGISTER_SKIP_MODERATION,
    )
    result = _call_asset_api("CreateAsset", body, ak, sk)
    asset_id = extract_asset_id(result)
    if not asset_id:
        raise HTTPException(502, {"error": {
            "code": "asset_registry_error",
            "message": "Asset registry did not return an asset id",
        }})

    status = extract_nested_value(result, "Result", "Status") or extract_nested_value(result, "Status")
    if ASSET_AUTO_REGISTER_WAIT_SECONDS > 0:
        deadline = time.time() + ASSET_AUTO_REGISTER_WAIT_SECONDS
        while time.time() < deadline:
            checked = _call_asset_api("GetAsset", {"Id": asset_id}, ak, sk)
            status = (
                extract_nested_value(checked, "Result", "Status")
                or extract_nested_value(checked, "Status")
                or status
            )
            if status == "Active":
                break
            if status == "Failed":
                message = extract_nested_value(checked, "Result", "Error") or extract_nested_value(checked, "Error")
                raise HTTPException(502, {"error": {
                    "code": "asset_registry_failed",
                    "message": message or "Asset registration failed",
                }})
            time.sleep(ASSET_AUTO_REGISTER_WAIT_INTERVAL)

    return {
        "asset_id": asset_id,
        "asset_url": f"asset://{asset_id}",
        "asset_status": status or "created",
    }


async def _save_upload(file: UploadFile, spec: dict) -> tuple[str, int]:
    upload_id = "upl_" + secrets.token_hex(8)
    day = time.strftime("%Y/%m/%d", time.gmtime())
    filename = f"{upload_id}{spec['ext']}"
    object_key = f"uploads/{day}/{filename}"
    target = UPLOAD_DIR / day / filename
    target.parent.mkdir(parents=True, exist_ok=True)

    max_bytes = int(float(spec["max_mb"]) * 1024 * 1024)
    size = 0
    try:
        with open(target, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(413, {"error": {
                        "code": "upload_too_large",
                        "message": f"Upload exceeds {spec['max_mb']} MB limit",
                        "max_bytes": max_bytes,
                    }})
                out.write(chunk)
        if size <= 0:
            raise HTTPException(400, {"error": {
                "code": "empty_upload",
                "message": "Uploaded file is empty",
            }})
    except Exception:
        try:
            target.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    finally:
        await file.close()

    return object_key, size


def _upload_response_from_row(row: sqlite3.Row | dict) -> dict:
    purpose = row["purpose"]
    content_url = row["asset_url"] or row["url"]
    face_whitelisted = bool(row["face_asset_whitelisted"])
    suggested_role = "reference_image" if face_whitelisted and purpose == "image" else None
    data = {
        "id": row["id"],
        "url": row["url"],
        "object_key": row["object_key"],
        "content_type": row["content_type"],
        "size_bytes": row["size_bytes"],
        "purpose": purpose,
        "original_filename": row["original_filename"] or "",
        "face_asset_whitelisted": face_whitelisted,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "suggested_content_block": _suggested_content_block(
            purpose, content_url, role=suggested_role
        ),
    }
    if row["asset_id"]:
        data["asset_id"] = row["asset_id"]
    if row["asset_url"]:
        data["asset_url"] = row["asset_url"]
    if row["asset_status"]:
        data["asset_status"] = row["asset_status"]
    if face_whitelisted:
        data["face_asset"] = {
            "asset_url": row["asset_url"],
            "asset_type": purpose,
            "label": row["face_asset_label"],
            "is_active": True,
        }
    return data


def _admin_upload_response_from_row(row: sqlite3.Row | dict) -> dict:
    data = _upload_response_from_row(row)
    data.update({
        "user_id": row["user_id"],
        "user_email": row["user_email"] if "user_email" in row.keys() else None,
        "byteplus_account_label": (
            row["byteplus_account_label"] if "byteplus_account_label" in row.keys() else None
        ),
        "face_asset_label": row["face_asset_label"],
        "face_asset_note": row["face_asset_note"],
    })
    return data


def _record_upload(
    *,
    user_id: str,
    upload_id: str,
    url: str,
    object_key: str,
    content_type: str,
    size_bytes: int,
    purpose: str,
    original_filename: str,
    asset_info: dict,
    face_asset_whitelisted: bool,
    face_asset_label: Optional[str],
    face_asset_note: Optional[str],
) -> dict:
    now = int(time.time())
    db = get_db()
    try:
        db.execute(
            """INSERT INTO uploads
               (id, user_id, url, object_key, content_type, size_bytes, purpose,
                original_filename, asset_id, asset_url, asset_status,
                face_asset_whitelisted, face_asset_label, face_asset_note,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                upload_id,
                user_id,
                url,
                object_key,
                content_type,
                size_bytes,
                purpose,
                original_filename,
                asset_info.get("asset_id"),
                asset_info.get("asset_url"),
                asset_info.get("asset_status"),
                1 if face_asset_whitelisted else 0,
                face_asset_label,
                face_asset_note,
                now,
                now,
            ),
        )
        row = db.execute(
            "SELECT * FROM uploads WHERE id=? AND user_id=?",
            (upload_id, user_id),
        ).fetchone()
        return _upload_response_from_row(row)
    finally:
        db.close()


def _find_user_whitelisted_upload_asset(user_id: str, url: str, purpose: str) -> Optional[dict]:
    db = get_db()
    try:
        row = db.execute(
            """SELECT * FROM uploads
               WHERE user_id=? AND url=? AND purpose=?
                 AND face_asset_whitelisted=1
                 AND asset_url IS NOT NULL
               ORDER BY updated_at DESC
               LIMIT 1""",
            (user_id, url, purpose),
        ).fetchone()
        if not row:
            return None
        return {
            "asset_id": row["asset_id"],
            "asset_url": row["asset_url"],
            "asset_status": row["asset_status"] or "cached",
        }
    finally:
        db.close()


def _materialize_real_person_assets(content: list[ContentBlock], user: dict) -> list[ContentBlock]:
    if not FACE_ASSET_SELF_SERVICE:
        raise HTTPException(403, {"error": {
            "code": "face_asset_self_service_disabled",
            "message": "real_person_mode requires server-side face asset self service to be enabled",
        }})

    materialized: list[ContentBlock] = []
    for block in content:
        if block.type not in ("image_url", "video_url"):
            materialized.append(block)
            continue

        url = _content_url_for_block(block).strip()
        if not url or url.startswith("asset://"):
            materialized.append(block)
            continue

        purpose = "image" if block.type == "image_url" else "video"
        asset_info = _find_user_whitelisted_upload_asset(user["id"], url, purpose)
        if asset_info is None:
            asset_info = _register_upload_asset(url, purpose)
            row = _upsert_face_asset_record(
                asset_info["asset_url"],
                purpose,
                label=f"real_person_mode:{user['id']}",
                note=f"auto registered by real_person_mode for {user['id']}",
                is_active=True,
            )
            asset_info["asset_url"] = row["asset_url"]
            upload_id = "upl_" + secrets.token_hex(8)
            _record_upload(
                user_id=user["id"],
                upload_id=upload_id,
                url=url,
                object_key=f"external/{upload_id}",
                content_type=EXTENSION_CONTENT_TYPES.get(Path(urlparse(url).path.lower()).suffix, ""),
                size_bytes=0,
                purpose=purpose,
                original_filename=_filename_from_url(url),
                asset_info=asset_info,
                face_asset_whitelisted=True,
                face_asset_label=f"real_person_mode:{user['id']}",
                face_asset_note=f"auto registered by real_person_mode for {user['id']}",
            )

        if block.type == "image_url":
            materialized.append(ContentBlock(
                type="image_url",
                image_url={"url": asset_info["asset_url"]},
                role=block.role or "reference_image",
            ))
        else:
            materialized.append(ContentBlock(
                type="video_url",
                video_url={"url": asset_info["asset_url"]},
                role=block.role or "reference_video",
            ))
    return materialized


@app.post("/internal/runtime/prepare-video-content")
async def internal_prepare_video_content(
    req: RuntimePrepareVideoContentRequest,
    x_runtime_token: Optional[str] = Header(None, alias="X-Runtime-Token"),
):
    if not RUNTIME_INTERNAL_TOKEN or x_runtime_token != RUNTIME_INTERNAL_TOKEN:
        raise HTTPException(403, {"error": {
            "code": "forbidden",
            "message": "Runtime internal token is invalid",
        }})

    db = get_db()
    try:
        user = db.execute(
            "SELECT * FROM users WHERE id=? AND is_active=1",
            (req.user_id,),
        ).fetchone()
    finally:
        db.close()
    if not user:
        raise HTTPException(404, {"error": {
            "code": "user_not_found",
            "message": "User was not found",
        }})

    content = _request_content_blocks(req, required=True)
    if bool((req.extra_body or {}).get("real_person_mode")):
        content = _materialize_real_person_assets(content, dict(user))
    content = _validate_content_blocks(content, required=True)
    _validate_customer_asset_access(content, req.user_id)
    _validate_face_asset_allowlist(content)
    return {"content": [block.model_dump(exclude_none=True) for block in content]}


# ─── /auth/* (Web 登录) ──────────────────────────────────────────
@app.post("/auth/login")
async def auth_login(req: LoginRequest, response: Response):
    db = get_db()
    now = int(time.time())
    u = db.execute("SELECT * FROM users WHERE email=? AND is_active=1",
                   (req.email.strip().lower(),)).fetchone()
    if u and u["locked_until"] and int(u["locked_until"]) > now:
        raise HTTPException(423, {"error": {
            "code": "account_locked",
            "message": "Account is temporarily locked after repeated failed login attempts",
            "locked_until": int(u["locked_until"]),
        }})
    if not u or not verify_password(req.password, u["password_hash"]):
        if u:
            failed_count = int(u["failed_login_count"] or 0) + 1
            locked_until = None
            if failed_count >= max(1, LOGIN_MAX_FAILED_ATTEMPTS):
                locked_until = now + max(60, LOGIN_LOCK_SECONDS)
            db.execute(
                "UPDATE users SET failed_login_count=?, locked_until=? WHERE id=?",
                (failed_count, locked_until, u["id"]),
            )
        raise HTTPException(401, {"error": {"code": "invalid_credentials",
                                            "message": "邮箱或密码错误"}})
    if (u["failed_login_count"] or 0) or u["locked_until"]:
        db.execute("UPDATE users SET failed_login_count=0, locked_until=NULL WHERE id=?", (u["id"],))
    token = create_session(u["id"])
    response.set_cookie(
        "relay_session", token,
        max_age=SESSION_TTL_SECS, httponly=True,
        secure=True, samesite="lax", path="/",
    )
    return {"id": u["id"], "email": u["email"], "is_admin": bool(u["is_admin"]),
            "balance_usd": u["balance_usd"],
            "markup_pct": _effective_markup_pct(u),
            "pricing_scope": _pricing_scope(u)}


@app.post("/auth/logout")
async def auth_logout(response: Response,
                      relay_session: Optional[str] = Cookie(None)):
    revoke_session(relay_session or "")
    response.delete_cookie("relay_session", path="/")
    return {"ok": True}


@app.get("/auth/me")
async def auth_me(user=Depends(auth_user)):
    return {
        "id": user["id"],
        "email": user.get("email"),
        "is_admin": bool(user.get("is_admin")),
        "balance_usd": user["balance_usd"],
        "markup_pct": _effective_markup_pct(user),
        "price_multiplier": _effective_price_multiplier(user),
        "pricing_scope": _pricing_scope(user),
        **_customer_key_metadata(user),
    }


@app.post("/auth/change-password")
async def change_password(req: ChangePasswordRequest,
                          user=Depends(auth_user),
                          relay_session: Optional[str] = Cookie(None)):
    db = get_db()
    row = db.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
    if not row or not verify_password(req.current_password, row["password_hash"]):
        raise HTTPException(400, {"error": {
            "code": "invalid_current_password",
            "message": "Current password is incorrect",
        }})
    if verify_password(req.new_password, row["password_hash"]):
        raise HTTPException(400, {"error": {
            "code": "password_reused",
            "message": "New password must be different from the current password",
        }})
    now = int(time.time())
    db.execute(
        "UPDATE users SET password_hash=?, password_changed_at=? WHERE id=?",
        (hash_password(req.new_password), now, user["id"]),
    )
    if relay_session:
        db.execute("DELETE FROM sessions WHERE user_id=? AND token<>?", (user["id"], relay_session))
    else:
        db.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
    _audit_event(
        "customer_password_changed",
        actor_user_id=user["id"],
        actor_type="customer",
        target_type="user",
        target_id=user["id"],
    )
    return {"ok": True}


# ─── /v1/* 客户 API ──────────────────────────────────────────────
@app.post("/v1/videos/estimate")
async def estimate_video_endpoint(req: EstimateRequest, user=Depends(auth_user)):
    """提交前预估单次任务的成本与是否能承担。
    不创建任务，不调用上游 BytePlus，不消耗 token。
    """
    client_model = req.model
    if client_model not in MODEL_MAP:
        raise HTTPException(400, {"error": {
            "code": "invalid_model",
            "message": f"Unknown model '{req.model}'. Available: {list(MODEL_MAP.keys())}"
    }})
    _ensure_model_enabled(client_model, user)
    real_model = MODEL_MAP[client_model]
    content = _request_content_blocks(req)
    _validate_model_parameters(client_model, req)
    _validate_model_content_requirements(client_model, content)
    _validate_customer_asset_access(content, user["id"])
    _validate_face_asset_allowlist(content)
    has_vref = any(b.type == "video_url" for b in content)

    est = estimate_video_cost(
        real_model,
        resolution=req.resolution or "720p",
        ratio=_request_ratio(req),
        duration=req.duration,
        frames=req.frames,
        has_video_ref=has_vref,
        generate_audio=_request_generate_audio(req),
    )
    markup_pct = _effective_markup_pct(user)
    price_multiplier = _effective_price_multiplier(user)
    # 注意: 客户看到的价格已经包含 markup
    est_cost = _with_multiplier(est.estimated_cost_usd, price_multiplier)
    max_cost = _with_multiplier(est.max_cost_usd, price_multiplier)
    shortage = max(0.0, max_cost - user["balance_usd"])
    return {
        "model":             client_model,
        "resolution":        req.resolution,
        "ratio":             _request_ratio(req),
        "duration":          req.duration,
        "frames":            req.frames,
        "has_video_ref":     has_vref,
        "generate_audio":    _request_generate_audio(req),
        "estimated_tokens":  est.estimated_tokens,
        "estimated_cost_usd": est_cost,
        "max_cost_usd":      max_cost,    # 这是提交时会预扣的金额
        "upstream_estimated_cost_usd": round(est.estimated_cost_usd, 6),
        "upstream_max_cost_usd": round(est.max_cost_usd, 6),
        "markup_pct":        markup_pct,
        "price_multiplier":  price_multiplier,
        "pricing_scope":     _pricing_scope(user),
        "balance_usd":       user["balance_usd"],
        "can_afford":        shortage == 0,
        "shortage_usd":      round(shortage, 6),
    }


@app.get("/v1/pricing")
async def get_pricing(user=Depends(optional_auth_user)):
    """价格表 + 估算公式；带客户鉴权时返回该客户自己的价格。"""
    from modelark.estimator import TOKENS_PER_SEC_OVERRIDE, _strip_date
    from modelark import get_output_rate
    markup_pct = _effective_markup_pct(user)
    price_multiplier = _effective_price_multiplier(user)
    pricing = {}
    for client_model, real_model in MODEL_MAP.items():
        base = _strip_date(real_model)
        tps = TOKENS_PER_SEC_OVERRIDE.get(base, {})
        rates_by_res = {}
        for res in ("480p", "720p", "1080p"):
            no_ref_rate   = get_output_rate(real_model, res, has_video_ref=False)
            with_ref_rate = get_output_rate(real_model, res, has_video_ref=True)
            entry = {
                "tokens_per_second":        tps.get(res),
                "price_no_video_ref_usd_per_1k":   _with_multiplier(no_ref_rate, price_multiplier),
                "price_with_video_ref_usd_per_1k": _with_multiplier(with_ref_rate, price_multiplier),
                "upstream_price_no_video_ref_usd_per_1k": round(no_ref_rate, 6),
                "upstream_price_with_video_ref_usd_per_1k": round(with_ref_rate, 6),
            }
            rates_by_res[res] = entry
        pricing[client_model] = rates_by_res
    return {
        "pricing": pricing,
        "markup_pct": markup_pct,
        "price_multiplier": price_multiplier,
        "pricing_scope": _pricing_scope(user),
        "buffer_pct": 0.10,
        "formula": (
            "tokens = tokens_per_second[resolution] * duration\n"
            "cost   = tokens / 1000 * price_per_1k\n"
            "max_cost = cost * (1 + buffer_pct)    # 提交时实际预扣\n"
            "选 'no_video_ref' 或 'with_video_ref' 取决于 content[] 是否包含 type=video_url"
        ),
        "notes": [
            "价格已经包含平台运营费用，所见即所收。",
            "实际生成成功后会按真实 token 数结算，把 max_cost 中多扣的退回。",
            "失败/取消/超时任务不计费，全额退回 max_cost。",
            "1080p 的每秒 token 数为像素比例外推估算；480p/720p 来自实测。",
        ],
    }


@app.post("/v1/uploads")
async def upload_media(
    file: UploadFile = File(...),
    purpose: Optional[str] = Form(None),
    face_allowlist: bool = Form(False),
    face_asset_label: Optional[str] = Form(None),
    face_asset_note: Optional[str] = Form(None),
    user=Depends(auth_user),
):
    """Upload a whitelisted media file and return a public relay URL.

    Returned URLs are intentionally public so the upstream video provider can
    fetch them as image_url/video_url/audio_url inputs.
    """
    content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    spec = ALLOWED_UPLOAD_TYPES.get(content_type)
    if not spec:
        raise HTTPException(400, {"error": {
            "code": "invalid_upload_type",
            "message": "Only whitelisted image, video, and audio MIME types are supported",
            "allowed_content_types": sorted(ALLOWED_UPLOAD_TYPES.keys()),
        }})

    inferred_purpose = spec["purpose"]
    if purpose and purpose.strip().lower() != inferred_purpose:
        raise HTTPException(400, {"error": {
            "code": "invalid_upload_purpose",
            "message": f"{content_type} uploads must use purpose={inferred_purpose}",
        }})
    if face_allowlist and not FACE_ASSET_SELF_SERVICE:
        raise HTTPException(403, {"error": {
            "code": "face_asset_self_service_disabled",
            "message": "Self-service face asset whitelisting is disabled",
        }})
    if face_allowlist and inferred_purpose not in ("image", "video"):
        raise HTTPException(400, {"error": {
            "code": "invalid_face_asset_type",
            "message": "Only image and video uploads can be added to the face asset whitelist",
        }})

    object_key, size = await _save_upload(file, spec)
    url = _public_url_for_object_key(object_key)
    asset_info: dict = {}
    should_register_asset = (
        face_allowlist
        or (ASSET_AUTO_REGISTER_UPLOADS and inferred_purpose in ASSET_AUTO_REGISTER_PURPOSES)
    )
    if should_register_asset:
        asset_info = _register_upload_asset(url, inferred_purpose)
    face_asset_note_to_store: Optional[str] = None
    if face_allowlist:
        face_asset_note_to_store = face_asset_note or f"self-service upload by {user['id']}"
        row = _upsert_face_asset_record(
            asset_info["asset_url"],
            inferred_purpose,
            label=face_asset_label,
            note=face_asset_note_to_store,
            is_active=True,
        )
        asset_info["asset_url"] = row["asset_url"]

    return _record_upload(
        user_id=user["id"],
        upload_id=Path(object_key).stem,
        url=url,
        object_key=object_key,
        content_type=content_type,
        size_bytes=size,
        purpose=inferred_purpose,
        original_filename=file.filename or "",
        asset_info=asset_info,
        face_asset_whitelisted=face_allowlist,
        face_asset_label=face_asset_label,
        face_asset_note=face_asset_note_to_store,
    )


@app.get("/v1/uploads")
async def list_uploads(
    user=Depends(auth_user),
    limit: int = 50,
    offset: int = 0,
):
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    db = get_db()
    try:
        rows = db.execute(
            """SELECT * FROM uploads
               WHERE user_id=?
               ORDER BY created_at DESC, id DESC
               LIMIT ? OFFSET ?""",
            (user["id"], limit, offset),
        ).fetchall()
        total = db.execute(
            "SELECT COUNT(*) AS total FROM uploads WHERE user_id=?",
            (user["id"],),
        ).fetchone()["total"]
        return {
            "data": [_upload_response_from_row(row) for row in rows],
            "limit": limit,
            "offset": offset,
            "total": total,
        }
    finally:
        db.close()


@app.post("/v1/uploads/from-url")
async def upload_media_from_url(req: UploadFromUrlRequest, user=Depends(auth_user)):
    """Record an already-public media URL in the relay upload ledger.

    The relay deliberately does not download or probe the remote URL. This keeps
    the server from becoming an SSRF scanner while still giving CLI/API-only
    customers a one-call way to register reusable materials.
    """
    content_type, spec, inferred_purpose = _external_upload_spec(
        req.url,
        req.content_type,
        req.purpose,
        req.size_bytes,
    )
    if req.face_allowlist and not FACE_ASSET_SELF_SERVICE:
        raise HTTPException(403, {"error": {
            "code": "face_asset_self_service_disabled",
            "message": "Self-service face asset whitelisting is disabled",
        }})
    if req.face_allowlist and inferred_purpose not in ("image", "video"):
        raise HTTPException(400, {"error": {
            "code": "invalid_face_asset_type",
            "message": "Only image and video uploads can be added to the face asset whitelist",
        }})

    upload_id = "upl_" + secrets.token_hex(8)
    asset_info: dict = {}
    should_register_asset = (
        req.face_allowlist
        or (ASSET_AUTO_REGISTER_UPLOADS and inferred_purpose in ASSET_AUTO_REGISTER_PURPOSES)
    )
    if should_register_asset:
        asset_info = _register_upload_asset(req.url, inferred_purpose)

    face_asset_note_to_store: Optional[str] = None
    if req.face_allowlist:
        face_asset_note_to_store = req.face_asset_note or f"self-service URL upload by {user['id']}"
        row = _upsert_face_asset_record(
            asset_info["asset_url"],
            inferred_purpose,
            label=req.face_asset_label,
            note=face_asset_note_to_store,
            is_active=True,
        )
        asset_info["asset_url"] = row["asset_url"]

    return _record_upload(
        user_id=user["id"],
        upload_id=upload_id,
        url=req.url,
        object_key=f"external/{upload_id}",
        content_type=content_type,
        size_bytes=int(req.size_bytes or 0),
        purpose=inferred_purpose,
        original_filename=req.original_filename or _filename_from_url(req.url),
        asset_info=asset_info,
        face_asset_whitelisted=req.face_allowlist,
        face_asset_label=req.face_asset_label,
        face_asset_note=face_asset_note_to_store,
    )


@app.get("/v1/uploads/{upload_id}")
async def get_upload(upload_id: str, user=Depends(auth_user)):
    db = get_db()
    try:
        row = db.execute(
            "SELECT * FROM uploads WHERE id=? AND user_id=?",
            (upload_id, user["id"]),
        ).fetchone()
    finally:
        db.close()
    if not row:
        raise HTTPException(404, {"error": {
            "code": "upload_not_found",
            "message": "Upload was not found for this account",
        }})
    return _upload_response_from_row(row)


@app.post("/v1/videos")
async def create_video(req: CreateVideoRequest, user=Depends(auth_user)):
    client_model = req.model
    if client_model not in MODEL_MAP:
        raise HTTPException(400, {"error": {
            "code": "invalid_model",
            "message": f"Unknown model '{req.model}'. Available: {list(MODEL_MAP.keys())}"
    }})
    _ensure_model_enabled(client_model, user)
    real_model = MODEL_MAP[client_model]
    content = _request_content_blocks(req, required=True)
    _validate_model_parameters(client_model, req)
    if _real_person_mode(req):
        content = _materialize_real_person_assets(content, user)
    _validate_model_content_requirements(client_model, content)
    _validate_customer_asset_access(content, user["id"])
    _validate_face_asset_allowlist(content)

    has_vref = any(b.type == "video_url" for b in content)
    est = estimate_video_cost(
        real_model,
        resolution=req.resolution or "720p",
        ratio=_request_ratio(req),
        duration=req.duration,
        has_video_ref=has_vref,
        generate_audio=_request_generate_audio(req),
    )
    markup_pct = _effective_markup_pct(user)
    price_multiplier = _effective_price_multiplier(user)
    estimated_to_user = _with_multiplier(est.estimated_cost_usd, price_multiplier)
    your_max_cost = _with_multiplier(est.max_cost_usd, price_multiplier)

    if user["balance_usd"] < your_max_cost:
        raise HTTPException(402, {"error": {
            "code": "insufficient_balance",
            "message": f"This request needs ${your_max_cost:.4f} reserved, "
                       f"but balance is ${user['balance_usd']:.4f}",
            "needed_usd": your_max_cost, "balance_usd": user["balance_usd"],
        }})

    bp_key = (user.get("byteplus_api_key") or "").strip() or UPSTREAM_API_KEY
    if _upstream_iam_enabled():
        bp_key = None
    if not bp_key:
        if _upstream_iam_enabled():
            if not _upstream_iam_ready():
                raise HTTPException(503, {"error": {
                    "code": "iam_upstream_not_configured",
                    "message": "IAM upstream mode requires BYTEPLUS_ACCESSKEY, BYTEPLUS_SECRETKEY, and UPSTREAM_ENDPOINT_ID",
                }})
        else:
            raise HTTPException(503, {"error": {
                "code": "no_upstream_key",
                "message": "Service not configured: contact administrator"}})

    upstream_model = _upstream_model_for_request(real_model, bp_key)

    if not bp_key and not _upstream_iam_enabled():
        raise HTTPException(503, {"error": {
            "code": "no_upstream_key",
            "message": "Service not configured: contact administrator"}})

    payload: dict = {
        "model": upstream_model,
        "content": [b.model_dump(exclude_none=True) for b in content],
        "resolution": req.resolution,
        "ratio": _request_ratio(req),
    }
    if req.duration is not None:    payload["duration"] = req.duration
    if req.seed is not None:        payload["seed"] = req.seed
    if req.watermark is not None:   payload["watermark"] = req.watermark
    if req.generate_audio is not None:
        payload["generate_audio"] = _request_generate_audio(req)

    if not _reserve_balance_if_available(user["id"], your_max_cost):
        raise HTTPException(402, {"error": {
            "code": "insufficient_balance",
            "message": f"This request needs ${your_max_cost:.4f} reserved, "
                       "but the available balance changed before submission",
            "needed_usd": your_max_cost, "balance_usd": user["balance_usd"],
        }})

    try:
        r = await _create_upstream_task(payload, bp_key)
    except Exception:
        _refund_reserved_balance(user["id"], your_max_cost)
        raise HTTPException(502, {"error": {
            "code": "upstream_error",
            "message": "upstream request failed",
        }})
    if r.status_code != 200:
        _refund_reserved_balance(user["id"], your_max_cost)
        error = {
            "code": "upstream_error",
            "message": "upstream returned an error",
        }
        request_id = _upstream_request_id(getattr(r, "headers", {}))
        if request_id:
            error["request_id"] = request_id
        raise HTTPException(502, {"error": error})
    upstream_id = (r.json() or {}).get("id")
    if not upstream_id:
        _refund_reserved_balance(user["id"], your_max_cost)
        raise HTTPException(502, {"error": {"code": "upstream_error",
                                            "message": "no task id returned"}})

    our_id = "vid_" + secrets.token_hex(8)
    now = int(time.time())
    prompt_text = next(
        (b.text for b in content if b.type == "text" and b.text), "")[:500]
    db = get_db()
    try:
        db.execute("""INSERT INTO tasks
            (id, user_id, upstream_task_id, upstream_model, client_model,
             resolution, duration, has_video_ref, status,
             estimated_cost_usd, held_usd, markup_pct, price_multiplier, prompt_text, request_payload,
             created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (our_id, user["id"], upstream_id, real_model, client_model,
             req.resolution, req.duration, int(has_vref), "queued",
             estimated_to_user, your_max_cost, markup_pct, price_multiplier,
             prompt_text, json.dumps(payload, ensure_ascii=False)[:5000],
             now, now))
    except Exception:
        await _cancel_upstream_task(upstream_id, bp_key)
        _refund_reserved_balance(user["id"], your_max_cost)
        raise HTTPException(500, {"error": {
            "code": "task_recording_failed",
            "message": "upstream task was created but could not be recorded locally",
        }})
    finally:
        db.close()

    return {
        "id": our_id, "model": client_model, "status": "queued",
        "estimated_cost_usd": estimated_to_user,
        "upstream_estimated_cost_usd": round(est.estimated_cost_usd, 6),
        "markup_pct": markup_pct,
        "price_multiplier": price_multiplier,
        "pricing_scope": _pricing_scope(user),
        "held_usd": your_max_cost,
        "created_at": now,
    }


@app.get("/v1/videos/{vid}")
async def get_video(vid: str, user=Depends(auth_user)):
    t = await _refresh_task(vid, user["id"])
    return _format_task(t)


@app.get("/v1/videos/{vid}/content")
async def stream_video(request: Request, vid: str, user=Depends(auth_user)):
    db = get_db()
    t = db.execute("SELECT * FROM tasks WHERE id=? AND user_id=?",
                   (vid, user["id"])).fetchone()
    if not t:
        raise HTTPException(404, {"error": {"code": "not_found",
                                            "message": "video not found"}})
    t = dict(t)

    # 优先本地文件
    local = t.get("local_video_path")
    if local and Path(local).exists():
        return FileResponse(
            local, media_type="video/mp4",
            headers={"Content-Disposition": f'inline; filename="{vid}.mp4"',
                     "Cache-Control": "private, max-age=86400"})

    # 没本地: 走 BytePlus 流
    t = await _refresh_task(vid, user["id"])
    if t["status"] != "succeeded":
        raise HTTPException(409, {"error": {"code": "not_ready",
                                            "message": f"video status: {t['status']}"}})
    cached_url = t.get("cached_video_url")
    if not cached_url:
        raise HTTPException(404, {"error": {"code": "video_unavailable",
                                            "message": "video URL no longer available"}})

    range_header = request.headers.get("range")
    upstream_headers_for_content = {"Range": range_header} if range_header else None
    upstream_cm = http.stream(
        "GET",
        cached_url,
        headers=upstream_headers_for_content,
        timeout=300,
    )
    upstream = await upstream_cm.__aenter__()
    if upstream.status_code < 200 or upstream.status_code >= 300:
        await upstream_cm.__aexit__(None, None, None)
        raise HTTPException(502, {"error": {"code": "proxy_error",
                                            "message": "upstream video unavailable"}})
    if range_header and upstream.status_code != 206:
        await upstream_cm.__aexit__(None, None, None)
        raise HTTPException(502, {"error": {"code": "proxy_range_unsupported",
                                            "message": "upstream video did not return partial content"}})
    status_code = 206 if upstream.status_code == 206 else 200
    response_headers = {
        "Content-Disposition": f'inline; filename="{vid}.mp4"',
        "Cache-Control": "private, max-age=3600",
        "Accept-Ranges": upstream.headers.get("accept-ranges", "bytes"),
    }
    for header in ("content-length", "content-range"):
        if upstream.headers.get(header):
            response_headers[header.title()] = upstream.headers[header]
    media_type = upstream.headers.get("content-type", "video/mp4").split(";", 1)[0]

    async def gen() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_bytes(chunk_size=64 * 1024):
                yield chunk
        finally:
            await upstream_cm.__aexit__(None, None, None)

    return StreamingResponse(
        gen(), media_type=media_type, status_code=status_code, headers=response_headers)


@app.head("/v1/videos/{vid}/content")
async def head_video_content(request: Request, vid: str, user=Depends(auth_user)):
    db = get_db()
    t = db.execute("SELECT * FROM tasks WHERE id=? AND user_id=?",
                   (vid, user["id"])).fetchone()
    if not t:
        raise HTTPException(404, {"error": {"code": "not_found",
                                            "message": "video not found"}})
    t = dict(t)
    t = await _refresh_task(vid, user["id"])
    if t["status"] != "succeeded":
        raise HTTPException(409, {"error": {"code": "not_ready",
                                            "message": f"video status: {t['status']}"}})
    cached_url = t.get("cached_video_url")
    if not cached_url:
        raise HTTPException(404, {"error": {"code": "video_unavailable",
                                            "message": "video URL no longer available"}})

    range_header = request.headers.get("range")
    upstream_headers_for_content = {"Range": range_header} if range_header else None
    async with http.stream(
        "HEAD",
        cached_url,
        headers=upstream_headers_for_content,
        timeout=300,
    ) as upstream:
        if upstream.status_code < 200 or upstream.status_code >= 300:
            raise HTTPException(502, {"error": {"code": "proxy_error",
                                                "message": "upstream video unavailable"}})
        if range_header and upstream.status_code != 206:
            raise HTTPException(502, {"error": {"code": "proxy_range_unsupported",
                                                "message": "upstream video did not return partial content"}})
        status_code = 206 if upstream.status_code == 206 else 200
        response_headers = {
            "Content-Disposition": f'inline; filename="{vid}.mp4"',
            "Cache-Control": "private, max-age=3600",
            "Accept-Ranges": upstream.headers.get("accept-ranges", "bytes"),
        }
        for header in ("content-length", "content-range"):
            if upstream.headers.get(header):
                response_headers[header.title()] = upstream.headers[header]
        media_type = upstream.headers.get("content-type", "video/mp4").split(";", 1)[0]
        return Response(status_code=status_code, headers=response_headers, media_type=media_type)


@app.delete("/v1/videos/{vid}")
async def delete_video(vid: str, user=Depends(auth_user)):
    db = get_db()
    t = db.execute("SELECT * FROM tasks WHERE id=? AND user_id=?",
                   (vid, user["id"])).fetchone()
    if not t:
        raise HTTPException(404, {"error": {"code": "not_found",
                                            "message": "video not found"}})
    t = dict(t)
    bp_key = db.execute("SELECT byteplus_api_key FROM users WHERE id=?",
                        (user["id"],)).fetchone()
    bp_key = (bp_key["byteplus_api_key"] if bp_key else None) or UPSTREAM_API_KEY
    if _upstream_iam_enabled():
        bp_key = None

    r = await _delete_upstream_task(t["upstream_task_id"], bp_key)
    if r.status_code not in (200, 204):
        error = {
            "code": "cannot_delete",
            "message": "upstream could not delete the task",
        }
        request_id = _upstream_request_id(getattr(r, "headers", {}))
        if request_id:
            error["request_id"] = request_id
        raise HTTPException(409, {"error": error})

    now = int(time.time())
    if not t["settled"]:
        _cancel_task_once(
            db,
            task_id=vid,
            user_id=user["id"],
            held_usd=t["held_usd"] or 0,
            updated_at=now,
        )
    return {"id": vid, "status": "deleted"}


@app.get("/v1/videos")
async def list_videos(user=Depends(auth_user),
                      limit: int = 20, offset: int = 0,
                      status: Optional[str] = None):
    db = get_db()
    sql = "SELECT * FROM tasks WHERE user_id=?"
    args: list = [user["id"]]
    if status:
        sql += " AND status=?"; args.append(status)
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    args.extend([limit, offset])
    rows = db.execute(sql, args).fetchall()
    total = db.execute("SELECT COUNT(*) c FROM tasks WHERE user_id=?",
                       (user["id"],)).fetchone()["c"]
    return {"data": [_format_task(dict(r)) for r in rows],
            "total": total, "limit": limit, "offset": offset}


@app.get("/v1/me")
async def me(user=Depends(auth_user)):
    """返回完整的余额拆分: 可用 / 冻结中 / 总额 + 累计花费 + 进行中任务数。"""
    db = get_db()
    agg = db.execute(
        """SELECT
              COALESCE(SUM(CASE WHEN settled=0 THEN held_usd END), 0) AS held_usd,
              COALESCE(SUM(CASE WHEN settled=0 THEN 1 ELSE 0 END), 0) AS pending_tasks,
              COALESCE(SUM(CASE WHEN settled=1 THEN actual_cost_usd END), 0) AS lifetime_spent_usd,
              COALESCE(SUM(CASE WHEN settled=1 THEN 1 ELSE 0 END), 0) AS completed_tasks
           FROM tasks WHERE user_id=?""",
        (user["id"],),
    ).fetchone()
    available = float(user["balance_usd"])
    held = float(agg["held_usd"])
    return {
        "id":                  user["id"],
        "email":               user.get("email"),
        **_customer_key_metadata(user),
        "markup_pct":          _effective_markup_pct(user),
        "pricing_scope":       _pricing_scope(user),
        "available_usd":       round(available, 6),
        "held_usd":            round(held, 6),
        "total_usd":           round(available + held, 6),
        "balance_usd":         round(available, 6),   # 兼容旧字段名
        "pending_tasks":       agg["pending_tasks"],
        "completed_tasks":     agg["completed_tasks"],
        "lifetime_spent_usd":  round(float(agg["lifetime_spent_usd"]), 6),
    }


@app.post("/v1/me/api-key/rotate")
async def rotate_my_api_key(user=Depends(auth_user)):
    new_key = _generate_api_key()
    now = int(time.time())
    db = get_db()
    db.execute(
        "UPDATE users SET api_key=?, api_key_last_rotated_at=? WHERE id=?",
        (new_key, now, user["id"]),
    )
    _audit_event(
        "customer_api_key_rotated",
        actor_user_id=user["id"],
        actor_type="customer",
        target_type="user",
        target_id=user["id"],
    )
    return {
        "api_key": new_key,
        "api_key_masked": _masked_secret(new_key, prefix=6, suffix=6),
        "rotated_at": now,
        "shown_once": True,
        "previous_key_status": "disabled",
    }


@app.get("/v1/models")
async def list_models(user=Depends(optional_auth_user)):
    """列出所有可用模型 (脱敏后的客户名 + 提示)。"""
    descs = {
        "video-pro":      "高质量, 480p/720p/1080p, 含音频, 慢 (5-30 分钟)",
        "video-pro-fast": "Pro 快速版, 同质量更快",
        "video-1.5-pro":  "中端, 支持草稿模式",
        "video-1080p":    "1080p 专用",
        "video-720p":     "720p 专用, 较快",
        "video-lite":     "纯文本→视频, 便宜快速",
        "video-lite-i2v": "图片→视频, 便宜快速",
    }
    return {
        "data": [_model_public_info(k) for k in _enabled_models_for_user(user)],
    }


# ─── /admin/* API ────────────────────────────────────────────────
@app.get("/admin/model-options", dependencies=[Depends(auth_admin)])
async def admin_model_options():
    return {
        "data": [_admin_model_option_info(model_id) for model_id in MODEL_REGISTRY.keys()],
    }


@app.get("/admin/config", dependencies=[Depends(auth_admin)])
async def admin_config():
    env_group_id = os.getenv("MODELARK_ASSET_GROUP_ID", "").strip()
    cached_group_id = _get_setting(_ASSET_GROUP_SETTING_KEY)
    group_source = "env" if env_group_id else ("cache" if cached_group_id else "missing")
    return {
        "iam": {
            "storage": "server_env_or_secret_manager",
            "byteplus_access_key_id_configured": bool(os.getenv("BYTEPLUS_ACCESS_KEY_ID", "").strip()),
            "byteplus_access_key_secret_configured": bool(os.getenv("BYTEPLUS_ACCESS_KEY_SECRET", "").strip()),
            "note": "IAM AK/SK must stay on the relay server. They are not stored per customer and are never returned by this API.",
        },
        "asset_registry": {
            "modelark_asset_group_id_configured": bool(env_group_id or cached_group_id),
            "modelark_asset_group_id_source": group_source,
            "modelark_asset_auto_create_group": MODELARK_ASSET_AUTO_CREATE_GROUP,
            "modelark_asset_group_name": MODELARK_ASSET_GROUP_NAME,
            "asset_auto_register_uploads": ASSET_AUTO_REGISTER_UPLOADS,
            "asset_auto_register_purposes": sorted(ASSET_AUTO_REGISTER_PURPOSES),
            "face_asset_self_service": FACE_ASSET_SELF_SERVICE,
            "face_asset_enforce": FACE_ASSET_ENFORCE,
        },
    }


@app.get("/admin/face-assets", dependencies=[Depends(auth_admin)])
async def admin_list_face_assets():
    db = get_db()
    rows = db.execute(
        """SELECT asset_url, asset_id, asset_type, label, note, is_active,
                  created_at, updated_at
           FROM face_assets ORDER BY updated_at DESC"""
    ).fetchall()
    db.close()
    env_rows = [
        {
            "asset_url": _normalize_asset_url(asset_url),
            "asset_id": _asset_id_from_url(asset_url),
            "asset_type": "env",
            "label": "env allowlist",
            "note": "FACE_ASSET_ALLOWLIST",
            "is_active": 1,
            "source": "env",
        }
        for asset_url in sorted(FACE_ASSET_ALLOWLIST)
    ]
    db_rows = [dict(row) | {"source": "db"} for row in rows]
    return {
        "enforce": FACE_ASSET_ENFORCE,
        "enforce_roles": sorted(FACE_ASSET_ENFORCE_ROLES),
        "data": env_rows + db_rows,
    }


@app.post("/admin/face-assets", dependencies=[Depends(auth_admin)])
async def admin_upsert_face_asset(req: FaceAssetReq):
    return _upsert_face_asset_record(
        req.asset_url,
        req.asset_type,
        label=req.label,
        note=req.note,
        is_active=req.is_active,
    )


@app.get("/admin/uploads", dependencies=[Depends(auth_admin)])
async def admin_list_uploads(
    limit: int = 50,
    offset: int = 0,
    user_id: Optional[str] = None,
    purpose: Optional[str] = None,
    face_asset_whitelisted: Optional[bool] = None,
):
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    sql = """FROM uploads up
             LEFT JOIN users u ON up.user_id=u.id
             WHERE 1=1"""
    args: list = []
    if user_id:
        sql += " AND up.user_id=?"
        args.append(user_id)
    if purpose:
        sql += " AND up.purpose=?"
        args.append(purpose.strip().lower())
    if face_asset_whitelisted is not None:
        sql += " AND up.face_asset_whitelisted=?"
        args.append(1 if face_asset_whitelisted else 0)

    db = get_db()
    try:
        rows = db.execute(
            f"""SELECT up.*, u.email AS user_email, u.byteplus_account_label
                {sql}
                ORDER BY up.created_at DESC, up.id DESC
                LIMIT ? OFFSET ?""",
            args + [limit, offset],
        ).fetchall()
        total = db.execute(f"SELECT COUNT(*) AS total {sql}", args).fetchone()["total"]
        stats = db.execute(
            f"""SELECT COUNT(*) AS total_uploads,
                       COALESCE(SUM(up.size_bytes), 0) AS total_size_bytes,
                       COALESCE(SUM(CASE WHEN up.face_asset_whitelisted=1 THEN 1 ELSE 0 END), 0)
                           AS face_whitelisted_uploads
                {sql}""",
            args,
        ).fetchone()
        return {
            "data": [_admin_upload_response_from_row(row) for row in rows],
            "limit": limit,
            "offset": offset,
            "total": total,
            "stats": dict(stats),
        }
    finally:
        db.close()


@app.get("/admin/uploads/{upload_id}", dependencies=[Depends(auth_admin)])
async def admin_get_upload(upload_id: str):
    db = get_db()
    try:
        row = db.execute(
            """SELECT up.*, u.email AS user_email, u.byteplus_account_label
               FROM uploads up LEFT JOIN users u ON up.user_id=u.id
               WHERE up.id=?""",
            (upload_id,),
        ).fetchone()
    finally:
        db.close()
    if not row:
        raise HTTPException(404, {"error": {
            "code": "upload_not_found",
            "message": "Upload was not found",
        }})
    return _admin_upload_response_from_row(row)


@app.get("/admin/users", dependencies=[Depends(auth_admin)])
async def admin_list_users():
    db = get_db()
    rows = db.execute(
        "SELECT id, email, balance_usd, is_active, is_admin, "
        "       api_key, byteplus_api_key, byteplus_account_label, "
        "       markup_pct, price_multiplier, enabled_models, note, created_at "
        "FROM users ORDER BY created_at DESC"
    ).fetchall()
    data = []
    for row in rows:
        item = dict(row)
        item["effective_markup_pct"] = _effective_markup_pct(item)
        item["price_multiplier"] = _effective_price_multiplier(item)
        item["pricing_scope"] = _pricing_scope(item)
        item["enabled_models_default"] = _enabled_models_uses_default(item)
        item["enabled_models"] = _enabled_models_for_user(item)
        item["api_key"] = _masked_secret(item.get("api_key"), prefix=6, suffix=6)
        item["byteplus_api_key"] = _masked_secret(item.get("byteplus_api_key"))
        data.append(item)
    return {"data": data}


@app.get("/admin/users/{user_id}", dependencies=[Depends(auth_admin)])
async def admin_get_user(user_id: str):
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not u:
        raise HTTPException(404, "user not found")
    u = dict(u)
    u["effective_markup_pct"] = _effective_markup_pct(u)
    u["price_multiplier"] = _effective_price_multiplier(u)
    u["pricing_scope"] = _pricing_scope(u)
    u["enabled_models_default"] = _enabled_models_uses_default(u)
    u["enabled_models"] = _enabled_models_for_user(u)
    if u.get("api_key"):
        u["api_key"] = _masked_secret(u["api_key"], prefix=6, suffix=6)
    # 脱敏 BytePlus key, 只显示前 8 后 4
    if u.get("byteplus_api_key"):
        u["byteplus_api_key"] = _masked_secret(u["byteplus_api_key"])
    u.pop("password_hash", None)

    # 该用户最近 20 个任务
    tasks = db.execute(
        """SELECT id, client_model, resolution, duration, status,
                  estimated_cost_usd, actual_cost_usd, upstream_actual_cost_usd,
                  completion_tokens, prompt_text, created_at
           FROM tasks WHERE user_id=? ORDER BY created_at DESC LIMIT 20""",
        (user_id,),
    ).fetchall()
    u["recent_tasks"] = [dict(t) for t in tasks]
    uploads = db.execute(
        """SELECT *
           FROM uploads
           WHERE user_id=?
           ORDER BY created_at DESC, id DESC
           LIMIT 20""",
        (user_id,),
    ).fetchall()
    u["recent_uploads"] = [_upload_response_from_row(row) for row in uploads]
    # 累计统计
    stats = db.execute(
        """SELECT COUNT(*) total_tasks,
                  COALESCE(SUM(actual_cost_usd),0) total_charged_usd,
                  COALESCE(SUM(upstream_actual_cost_usd),0) total_byteplus_usd,
                  COALESCE(SUM(completion_tokens),0) total_tokens
           FROM tasks WHERE user_id=? AND settled=1""", (user_id,)
    ).fetchone()
    u["lifetime_stats"] = dict(stats)
    return u


@app.post("/admin/users", dependencies=[Depends(auth_admin)])
async def admin_create_user(req: CreateUserRequest):
    db = get_db()
    email = req.email.strip().lower()
    if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        raise HTTPException(409, {"error": {"code": "email_exists",
                                            "message": "email 已存在"}})
    if req.api_key:
        raise HTTPException(400, {"error": {
            "code": "api_key_generation_required",
            "message": "Relay API keys are generated by the server at account creation or rotation",
        }})
    api_key = _generate_api_key()
    user_id = "u_" + secrets.token_hex(8)
    pw = req.password or secrets.token_urlsafe(12)
    price_multiplier = req.price_multiplier
    if price_multiplier is None:
        price_multiplier = 1 + (req.markup_pct if req.markup_pct is not None else MARKUP_PCT)
    enabled_models = _serialize_enabled_models(req.enabled_models)
    db.execute(
        """INSERT INTO users (id, api_key, email, balance_usd, is_active,
                              is_admin, password_hash,
                              byteplus_api_key, byteplus_account_label, markup_pct,
                              price_multiplier, enabled_models, note,
                              api_key_last_rotated_at, password_changed_at, created_at)
           VALUES (?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?)""",
        (user_id, api_key, email, req.balance_usd, int(req.is_admin),
         hash_password(pw), req.byteplus_api_key,
         req.byteplus_account_label, req.markup_pct, price_multiplier,
         enabled_models, req.note, int(time.time()), int(time.time()), int(time.time())),
    )
    return {"id": user_id, "api_key": api_key, "email": email,
            "balance_usd": req.balance_usd,
            "markup_pct": _effective_markup_pct({"markup_pct": req.markup_pct}),
            "price_multiplier": price_multiplier,
            "enabled_models_default": enabled_models is None,
            "enabled_models": _enabled_models_for_user({"enabled_models": enabled_models}),
            "pricing_scope": "customer" if req.price_multiplier is not None or req.markup_pct is not None else "global",
            "temporary_password": pw if not req.password else None,
            "is_admin": req.is_admin}


@app.patch("/admin/users/{user_id}", dependencies=[Depends(auth_admin)])
async def admin_update_user(user_id: str, req: UpdateUserReq):
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not u:
        raise HTTPException(404, "user not found")
    fields, args = [], []
    if req.balance_usd is not None:
        fields.append("balance_usd=?"); args.append(req.balance_usd)
    if "markup_pct" in req.model_fields_set:
        fields.append("markup_pct=?"); args.append(req.markup_pct)
        if "price_multiplier" not in req.model_fields_set and req.markup_pct is not None:
            fields.append("price_multiplier=?"); args.append(1 + req.markup_pct)
    if "price_multiplier" in req.model_fields_set:
        fields.append("price_multiplier=?"); args.append(req.price_multiplier)
    if "enabled_models" in req.model_fields_set:
        fields.append("enabled_models=?"); args.append(_serialize_enabled_models(req.enabled_models))
    if req.api_key is not None:
        raise HTTPException(400, {"error": {
            "code": "api_key_rotation_required",
            "message": "Use POST /admin/users/{user_id}/api-key/rotate to generate a new Relay API key",
        }})
    if req.byteplus_api_key is not None:
        fields.append("byteplus_api_key=?"); args.append(req.byteplus_api_key)
    if req.byteplus_account_label is not None:
        fields.append("byteplus_account_label=?"); args.append(req.byteplus_account_label)
    if req.is_active is not None:
        fields.append("is_active=?"); args.append(int(req.is_active))
        if not req.is_active:
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    if req.note is not None:
        fields.append("note=?"); args.append(req.note)
    if req.new_password:
        fields.append("password_hash=?"); args.append(hash_password(req.new_password))
        fields.append("password_changed_at=?"); args.append(int(time.time()))
        db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    if not fields:
        raise HTTPException(400, "nothing to update")
    args.append(user_id)
    db.execute(f"UPDATE users SET {','.join(fields)} WHERE id=?", args)
    if "price_multiplier" in req.model_fields_set or "markup_pct" in req.model_fields_set:
        _audit_event(
            "admin_changed_price_multiplier",
            actor_user_id=None,
            actor_type="admin",
            target_type="user",
            target_id=user_id,
        )
    if "enabled_models" in req.model_fields_set:
        _audit_event(
            "admin_changed_enabled_models",
            actor_user_id=None,
            actor_type="admin",
            target_type="user",
            target_id=user_id,
        )
    if "byteplus_api_key" in req.model_fields_set:
        _audit_event(
            "admin_changed_upstream_key",
            actor_user_id=None,
            actor_type="admin",
            target_type="user",
            target_id=user_id,
            metadata={"field": "byteplus_api_key", "secret_changed": True},
        )
    if req.new_password:
        _audit_event(
            "admin_reset_password",
            actor_user_id=None,
            actor_type="admin",
            target_type="user",
            target_id=user_id,
        )
    if "is_active" in req.model_fields_set:
        _audit_event(
            "admin_changed_status",
            actor_user_id=None,
            actor_type="admin",
            target_type="user",
            target_id=user_id,
            metadata={"is_active": bool(req.is_active)},
        )
    return {"ok": True}


@app.post("/admin/users/{user_id}/api-key/rotate", dependencies=[Depends(auth_admin)])
async def admin_rotate_user_api_key(user_id: str):
    db = get_db()
    u = db.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
    if not u:
        raise HTTPException(404, "user not found")
    new_key = _generate_api_key()
    now = int(time.time())
    db.execute(
        "UPDATE users SET api_key=?, api_key_last_rotated_at=? WHERE id=?",
        (new_key, now, user_id),
    )
    db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    _audit_event(
        "admin_rotated_customer_api_key",
        actor_user_id=None,
        actor_type="admin",
        target_type="user",
        target_id=user_id,
    )
    return {
        "api_key": new_key,
        "api_key_masked": _masked_secret(new_key, prefix=6, suffix=6),
        "rotated_at": now,
        "shown_once": True,
        "previous_key_status": "disabled",
    }


@app.post("/admin/users/{user_id}/topup", dependencies=[Depends(auth_admin)])
async def admin_topup(user_id: str, req: TopupReq):
    db = get_db()
    db.execute("UPDATE users SET balance_usd = balance_usd + ? WHERE id=?",
               (req.amount_usd, user_id))
    user = db.execute("SELECT id, email, balance_usd FROM users WHERE id=?",
                      (user_id,)).fetchone()
    if not user:
        raise HTTPException(404, "user not found")
    _audit_event(
        "admin_changed_balance",
        actor_user_id=None,
        actor_type="admin",
        target_type="user",
        target_id=user_id,
        metadata={"amount_usd": req.amount_usd, "note": req.note or ""},
    )
    return dict(user)


@app.delete("/admin/users/{user_id}", dependencies=[Depends(auth_admin)])
async def admin_disable_user(user_id: str):
    """禁用而非删除 (保留任务历史)。"""
    db = get_db()
    db.execute("UPDATE users SET is_active=0 WHERE id=?", (user_id,))
    db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    _audit_event(
        "admin_changed_status",
        actor_user_id=None,
        actor_type="admin",
        target_type="user",
        target_id=user_id,
        metadata={"is_active": False},
    )
    return {"ok": True, "id": user_id, "is_active": False}


def _format_audit_event(row: sqlite3.Row | dict) -> dict:
    metadata = {}
    raw = row["metadata_json"] if row["metadata_json"] else ""
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                metadata = parsed
        except (TypeError, ValueError):
            metadata = {}
    return {
        "id": row["id"],
        "actor_user_id": row["actor_user_id"],
        "actor_type": row["actor_type"],
        "action": row["action"],
        "target_type": row["target_type"],
        "target_id": row["target_id"],
        "metadata": metadata,
        "created_at": row["created_at"],
    }


@app.get("/admin/audit-events", dependencies=[Depends(auth_admin)])
async def admin_audit_events(limit: int = 50, offset: int = 0,
                             action: Optional[str] = None,
                             target_id: Optional[str] = None):
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    where = []
    args: list[Any] = []
    if action:
        where.append("action=?"); args.append(action)
    if target_id:
        where.append("target_id=?"); args.append(target_id)
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    db = get_db()
    rows = db.execute(
        f"""SELECT * FROM audit_events
            {where_sql}
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?""",
        [*args, limit, offset],
    ).fetchall()
    total = db.execute(
        f"SELECT COUNT(*) c FROM audit_events{where_sql}",
        args,
    ).fetchone()["c"]
    return {
        "data": [_format_audit_event(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.get("/admin/stats", dependencies=[Depends(auth_admin)])
async def admin_stats():
    db = get_db()
    n_users = db.execute("SELECT COUNT(*) c FROM users WHERE is_active=1").fetchone()["c"]
    n_tasks = db.execute("SELECT COUNT(*) c FROM tasks").fetchone()["c"]
    revenue = db.execute(
        "SELECT COALESCE(SUM(actual_cost_usd),0) r FROM tasks WHERE settled=1"
    ).fetchone()["r"]
    upstream_cost = db.execute(
        "SELECT COALESCE(SUM(upstream_actual_cost_usd),0) r FROM tasks WHERE settled=1"
    ).fetchone()["r"]
    held = db.execute(
        "SELECT COALESCE(SUM(held_usd),0) r FROM tasks WHERE settled=0"
    ).fetchone()["r"]
    upload_stats = db.execute(
        """SELECT COUNT(*) AS total_uploads,
                  COALESCE(SUM(CASE WHEN face_asset_whitelisted=1 THEN 1 ELSE 0 END), 0)
                      AS face_whitelisted_uploads
           FROM uploads"""
    ).fetchone()
    return {
        "active_users": n_users,
        "total_tasks": n_tasks,
        "total_uploads": upload_stats["total_uploads"],
        "face_whitelisted_uploads": upload_stats["face_whitelisted_uploads"],
        "revenue_settled_usd": round(revenue, 4),
        "upstream_cost_usd": round(upstream_cost, 4),
        "gross_profit_usd": round(revenue - upstream_cost, 4),
        "held_usd": round(held, 4),
    }


@app.get("/admin/reconcile", dependencies=[Depends(auth_admin)])
async def admin_reconcile():
    """对账: 按用户聚合, 列出每个 BytePlus key 在我们这边的累计用量与花费。"""
    db = get_db()
    rows = db.execute(
        """SELECT u.id, u.email, u.byteplus_account_label,
                  (CASE WHEN u.byteplus_api_key IS NULL OR u.byteplus_api_key=''
                        THEN 0 ELSE 1 END) has_key,
                  COUNT(t.id) tasks,
                  COALESCE(SUM(t.completion_tokens), 0) tokens,
                  COALESCE(SUM(t.upstream_actual_cost_usd), 0) byteplus_usd,
                  COALESCE(SUM(t.actual_cost_usd), 0) charged_usd
           FROM users u LEFT JOIN tasks t
             ON u.id=t.user_id AND t.settled=1
           WHERE u.is_active=1
           GROUP BY u.id
           ORDER BY tokens DESC"""
    ).fetchall()
    return {"data": [dict(r) for r in rows]}


@app.get("/admin/tasks", dependencies=[Depends(auth_admin)])
async def admin_list_tasks(limit: int = 50, offset: int = 0,
                           user_id: Optional[str] = None,
                           status: Optional[str] = None):
    db = get_db()
    sql = """SELECT t.*, u.email user_email
             FROM tasks t LEFT JOIN users u ON t.user_id=u.id
             WHERE 1=1"""
    args = []
    if user_id: sql += " AND t.user_id=?"; args.append(user_id)
    if status:  sql += " AND t.status=?"; args.append(status)
    sql += " ORDER BY t.created_at DESC LIMIT ? OFFSET ?"
    args.extend([limit, offset])
    rows = db.execute(sql, args).fetchall()
    return {"data": [dict(r) for r in rows]}


# ─── 静态 HTML 页面 ──────────────────────────────────────────────
@app.get("/")
async def root_redirect():
    return RedirectResponse(url="/app/", status_code=302)


@app.get("/app/")
async def serve_user_app():
    f = STATIC_DIR / "app.html"
    if not f.exists():
        return JSONResponse({"error": "user UI not deployed"}, status_code=503)
    return FileResponse(f)


@app.get("/admin/ui/")
async def serve_admin_app():
    f = STATIC_DIR / "admin.html"
    if not f.exists():
        return JSONResponse({"error": "admin UI not deployed"}, status_code=503)
    return FileResponse(f)


@app.get("/v1/docs/api.md", response_class=None)
async def serve_api_docs():
    """对外公开的 API 文档（Markdown 源文件）。供 admin/user UI 渲染。"""
    docs = Path(__file__).parent / "API_DOCS.md"
    if not docs.exists():
        return JSONResponse({"error": "docs not deployed"}, status_code=404)
    return FileResponse(docs, media_type="text/markdown; charset=utf-8")
