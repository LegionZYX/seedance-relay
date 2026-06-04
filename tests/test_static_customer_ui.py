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
