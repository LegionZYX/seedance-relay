import re
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
MOJIBAKE_FRAGMENTS = ("管理后台", "瀹㈡埛", "绠＄悊鍚庡彴", "鈹€", "涓昏壊", "娑堣垂")


class StaticAdminUiTests(unittest.TestCase):
    def test_admin_ui_preserves_empty_enabled_model_list(self):
        admin_html = (PROJECT_DIR / "static/admin.html").read_text(encoding="utf-8")

        self.assertIn("enabled_models_default", admin_html)
        self.assertIn("Use default model list", admin_html)
        self.assertIn("obj.enabled_models_default ? null : (obj.enabled_models || [])", admin_html)
        self.assertIn(
            "this.editForm.enabled_models_default ? null : (this.editForm.enabled_models || [])",
            admin_html,
        )
        self.assertNotIn("if (!obj.enabled_models?.length) obj.enabled_models = null", admin_html)
        for fragment in ["NSFW", "SFW", "nsfw", "sfw", "content_moderation"]:
            self.assertNotIn(fragment, admin_html)

    def test_admin_ui_loads_admin_model_options_for_alias_selection(self):
        admin_html = (PROJECT_DIR / "static/admin.html").read_text(encoding="utf-8")

        self.assertIn('this.api("/admin/model-options")', admin_html)
        self.assertIn("is_alias", admin_html)
        self.assertIn("alias_for", admin_html)
        self.assertNotIn('this.api("/v1/models")', admin_html)

    def test_admin_ui_uses_price_multiplier_not_markup_pct_controls(self):
        admin_html = (PROJECT_DIR / "static/admin.html").read_text(encoding="utf-8")

        self.assertIn("price_multiplier", admin_html)
        self.assertIn("消费系数 / Price Multiplier", admin_html)
        self.assertIn("<title>Example Video Relay · 管理后台</title>", admin_html)
        for fragment in [
            "newUserForm.markup_pct",
            "editForm.markup_pct",
            "effective_markup_pct",
            "客户加价率",
            "MARKUP_PCT",
        ]:
            self.assertNotIn(fragment, admin_html)
        for fragment in MOJIBAKE_FRAGMENTS[1:]:
            self.assertNotIn(fragment, admin_html)

    def test_admin_password_inputs_match_api_minimum(self):
        admin_html = (PROJECT_DIR / "static/admin.html").read_text(encoding="utf-8")

        self.assertIn('v-model="newUserForm.password"', admin_html)
        self.assertIn('placeholder="留空自动生成；手动填写至少 10 位"', admin_html)
        self.assertIn('v-model="editForm.new_password"', admin_html)
        self.assertIn('placeholder="至少 10 位；留空不改"', admin_html)
        self.assertGreaterEqual(admin_html.count('autocomplete="new-password" minlength="10"'), 2)
        self.assertIn("passwordTooShort(value) { return String(value || \"\").length < 10; }", admin_html)
        self.assertIn("this.passwordTooShort(this.newUserForm.password)", admin_html)
        self.assertIn("this.passwordTooShort(this.editForm.new_password)", admin_html)
        self.assertIn('ElMessage.warning("初始密码至少 10 位；也可以留空自动生成")', admin_html)
        self.assertIn('ElMessage.warning("新密码至少 10 位；留空则不修改")', admin_html)

    def test_admin_list_and_detail_only_show_masked_existing_keys(self):
        admin_html = (PROJECT_DIR / "static/admin.html").read_text(encoding="utf-8")

        self.assertIn("{{ row.api_key || '—' }}", admin_html)
        self.assertIn("{{ row.byteplus_api_key }}", admin_html)
        self.assertIn("masked API key: {{ editing.api_key }}", admin_html)
        self.assertIn("{{ created.api_key }}", admin_html)
        self.assertIn("<el-tag size=\"small\" type=\"info\" effect=\"plain\">masked</el-tag>", admin_html)
        self.assertNotIn("revealed[row.id+'_api']", admin_html)
        self.assertNotIn("revealed[row.id+'_bp']", admin_html)
        self.assertNotIn("copyText(row.api_key", admin_html)
        self.assertNotIn("copyText(row.byteplus_api_key", admin_html)
        self.assertNotIn("maskKey(", admin_html)
        self.assertNotIn("toggleReveal(", admin_html)

    def test_admin_ui_can_rotate_customer_api_key_once(self):
        admin_html = (PROJECT_DIR / "static/admin.html").read_text(encoding="utf-8")

        self.assertIn("rotateEditingApiKey", admin_html)
        self.assertIn("轮换 API Key", admin_html)
        self.assertIn("/api-key/rotate", admin_html)
        self.assertIn("客户 API Key 已轮换", admin_html)
        self.assertIn("{{ rotatedKey.api_key }}", admin_html)
        self.assertIn("旧 key 会立即失效，现有客户会话也会撤销", admin_html)
        self.assertIn("关闭后管理端只会显示 masked key", admin_html)
        self.assertIn("this.editing.api_key = r.api_key_masked", admin_html)
        self.assertIn("rotatedKey: null", admin_html)
        self.assertIn("rotatedKey: false", admin_html)

    def test_admin_ui_template_has_no_obvious_broken_attributes_or_tags(self):
        admin_html = (PROJECT_DIR / "static/admin.html").read_text(encoding="utf-8")

        for lineno, line in enumerate(admin_html.splitlines(), start=1):
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
            self.assertIsNone(re.search(pattern, admin_html, flags=re.MULTILINE), pattern)


if __name__ == "__main__":
    unittest.main()
