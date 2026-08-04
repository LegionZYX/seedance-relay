import re
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
MOJIBAKE_FRAGMENTS = ("瑙嗛", "鐢熸垚", "鐟欏棝", "閻㈢喐", "鈹€", "娑撴槒")


class StaticCustomerUiTests(unittest.TestCase):
    def test_customer_ui_title_is_readable(self):
        app_html = (PROJECT_DIR / "static/app.html").read_text(encoding="utf-8")

        self.assertIn("<title>Example Video Relay - AI 视频生成</title>", app_html)
        for fragment in MOJIBAKE_FRAGMENTS:
            self.assertNotIn(fragment, app_html)

    def test_customer_ui_does_not_expose_provider_urls_keys_or_nsfw_controls(self):
        app_html = (PROJECT_DIR / "static/app.html").read_text(encoding="utf-8")

        forbidden_fragments = [
            "bytepluses.com",
            "volces.com",
            "ark-",
            "NSFW",
            "video-pro-nsfw",
        ]
        for fragment in forbidden_fragments:
            self.assertNotIn(fragment, app_html)

    def test_customer_ui_warns_before_api_key_rotation(self):
        app_html = (PROJECT_DIR / "static/app.html").read_text(encoding="utf-8")

        self.assertIn('confirm("重新生成 API Key 后，旧 key 会立即失效。确定继续？")', app_html)
        self.assertIn("/v1/me/api-key/rotate", app_html)

    def test_customer_password_change_input_matches_api_minimum(self):
        app_html = (PROJECT_DIR / "static/app.html").read_text(encoding="utf-8")

        self.assertIn('v-model="passwordForm.new_password"', app_html)
        self.assertIn('autocomplete="new-password" minlength="10"', app_html)
        self.assertIn('placeholder="至少 10 位"', app_html)
        self.assertIn("passwordTooShort(value) { return String(value || \"\").length < 10; }", app_html)
        self.assertIn('ElMessage.warning("新密码至少 10 位")', app_html)
        self.assertIn("this.passwordTooShort(this.passwordForm.new_password)", app_html)

    def test_customer_ui_estimate_fallback_uses_price_multiplier(self):
        app_html = (PROJECT_DIR / "static/app.html").read_text(encoding="utf-8")

        self.assertIn("this.me?.price_multiplier ?? 1.0", app_html)
        self.assertNotIn("this.me?.markup_pct", app_html)

    def test_customer_ui_shows_video_retention_countdown(self):
        app_html = (PROJECT_DIR / "static/app.html").read_text(encoding="utf-8")

        self.assertIn("服务器网关：保存 2 天", app_html)
        self.assertIn("BytePlus 原始文件：官方保存 24 小时", app_html)
        self.assertIn("服务器网关下载", app_html)
        self.assertIn("BytePlus 原始下载", app_html)
        self.assertIn("contentCountdown(task)", app_html)
        self.assertIn("upstreamContentCountdown(task)", app_html)
        self.assertIn("upstream_video_url", app_html)
        self.assertIn("upstream_content_expires_at", app_html)
        self.assertIn("content_expires_at", app_html)

    def test_history_cards_open_task_detail_without_switching_to_generate_form(self):
        app_html = (PROJECT_DIR / "static/app.html").read_text(encoding="utf-8")

        self.assertIn('@click="openHistoryTask(t)"', app_html)
        self.assertIn("taskDetailDialog", app_html)
        self.assertIn("async openHistoryTask(task)", app_html)
        self.assertIn("任务详情", app_html)
        self.assertNotIn('@click="goToTask(t.id)"', app_html)
        self.assertNotIn('this.switchTab("generate");\n      try {\n        const t = await this.api(`/v1/videos/${vid}`);', app_html)

    def test_customer_ui_can_clear_history_and_explains_failures_and_countdowns(self):
        app_html = (PROJECT_DIR / "static/app.html").read_text(encoding="utf-8")

        self.assertIn('@click="clearHistory"', app_html)
        self.assertIn("async clearHistory()", app_html)
        self.assertIn('/v1/videos/history', app_html)
        self.assertIn("failureMessage(task)", app_html)
        self.assertIn("generationCountdown(task)", app_html)
        self.assertIn("contentCountdown(historyTask)", app_html)
        self.assertIn("保留进行中的任务", app_html)

    def test_customer_ui_supports_multi_reference_and_direct_asset_id(self):
        app_html = (PROJECT_DIR / "static/app.html").read_text(encoding="utf-8")

        for fragment in [
            "创作模式",
            "首帧 / 尾帧",
            "多参考素材",
            "直接粘贴 asset id",
            "asset://asset-...",
            "reference_image: 9",
            "reference_video: 3",
            "reference_audio: 3",
            "apiJsonPreview",
            "videoRequestBody()",
            "addManualAsset()",
            "normalizeAssetRef(value)",
            "asset://${raw}",
        ]:
            self.assertIn(fragment, app_html)

    def test_customer_ui_generation_form_is_model_capability_driven(self):
        app_html = (PROJECT_DIR / "static/app.html").read_text(encoding="utf-8")

        self.assertNotIn("el-radio-group v-model=\"form.resolution\"", app_html)
        for fragment in [
            "resolutionOptions",
            "supportedResolutions()",
            "supportedRatios()",
            "durationRange()",
            "normalizeModelParams()",
            "supported_resolutions",
            "supported_ratios",
            "duration_seconds",
            "480p 标清",
            "720p 高清",
            "1080p 全高清",
            "16:9 横屏",
            "9:16 竖屏",
            "1:1 方形",
        ]:
            self.assertIn(fragment, app_html)

    def test_customer_ui_creation_modes_drive_required_references_and_uploads(self):
        app_html = (PROJECT_DIR / "static/app.html").read_text(encoding="utf-8")

        for fragment in [
            'required_roles: ["first_frame"]',
            'required_roles: ["first_frame", "last_frame"]',
            'required_roles: ["reference_image"]',
            'required_roles: ["reference_video"]',
            'required_roles: ["visual_reference"]',
            "generationValidationError(requirePrompt=true)",
            "validateGenerationForm()",
            "submitValidationMessage",
            "上传首帧并加入",
            "上传尾帧并加入",
            "上传参考图并加入",
            "上传视频并加入",
            "上传音频并加入",
            "handleSlotUploadFile($event, 'first_frame')",
            "handleSlotUploadFile($event, 'reference_audio')",
            "uploadFileForRole(file, role)",
        ]:
            self.assertIn(fragment, app_html)

    def test_customer_ui_template_has_no_obvious_broken_attributes_or_tags(self):
        app_html = (PROJECT_DIR / "static/app.html").read_text(encoding="utf-8")

        for lineno, line in enumerate(app_html.splitlines(), start=1):
            self.assertEqual(
                line.count('"') % 2,
                0,
                f"line {lineno} has an unbalanced double quote: {line}",
            )
            self.assertIsNone(
                re.search(r"[^<]/(div|p|span|el-button|el-form-item|h[1-6]|section|template)>", line),
                f"line {lineno} looks like it has a broken closing tag: {line}",
            )

        for pattern in [
            r'placeholder="[^"]*$',
            r'content="[^"]*$',
            r'label="[^"]*$',
            r"<p[^>]*>[^<]*/p>",
            r"<h[1-6][^>]*>[^<]*/h[1-6]>",
        ]:
            self.assertIsNone(re.search(pattern, app_html, flags=re.MULTILINE), pattern)


if __name__ == "__main__":
    unittest.main()
