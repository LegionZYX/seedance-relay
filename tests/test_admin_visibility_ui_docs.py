from pathlib import Path


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

    assert "BytePlus 兼容说明" in docs
    assert "POST /contents/generations/tasks" in docs
    assert "POST /v1/videos" in docs
    assert "reference_image" in docs
    assert "reference_video" in docs
    assert "reference_audio" in docs
    assert "return_last_frame" in docs
    assert "callback_url" in docs
    assert "extra_body" in docs
    assert "audio_url 不能单独" in docs
    assert "/admin/" not in docs
