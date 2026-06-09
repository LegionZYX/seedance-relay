import importlib
import sys
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]


def test_admin_ui_exposes_task_detail_and_request_logs():
    html = (ROOT / "static" / "admin.html").read_text(encoding="utf-8")

    assert "tab==='requestLogs'" in html
    assert "/admin/request-logs" in html
    assert "openTaskDetail(row)" in html
    assert "/admin/tasks/${row.id}" in html
    assert "admin_content_url" in html


def test_public_api_docs_describe_byteplus_compatibility_without_admin_paths():
    docs = (ROOT / "API_DOCS.md").read_text(encoding="utf-8")

    assert "BytePlus" in docs
    assert "POST /contents/generations/tasks" in docs
    assert "POST /v1/videos" in docs
    assert "reference_image" in docs
    assert "reference_video" in docs
    assert "reference_audio" in docs
    assert "return_last_frame" in docs
    assert "callback_url" in docs
    assert "extra_body" in docs
    assert "audio_url" in docs
    assert "/admin/" not in docs


def test_public_api_docs_endpoint_uses_configured_base_url(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "relay.sqlite"))
    monkeypatch.setenv("VIDEO_DIR", str(tmp_path / "videos"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("PUBLIC_DOMAIN", "seedance.ac")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://seedance.ac")
    monkeypatch.setenv("ADMIN_PASSWORD", "")

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    sys.modules.pop("relay_server", None)
    server = importlib.import_module("relay_server")
    client = TestClient(server.app)

    response = client.get("/v1/docs/api.md")

    assert response.status_code == 200
    assert "https://seedance.ac" in response.text
    assert "https://seedance3.eu" not in response.text
