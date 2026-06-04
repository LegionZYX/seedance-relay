import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
MOJIBAKE_FRAGMENTS = (
    "瑙嗛",
    "鐢熸垚",
    "鐧芥",
    "闈㈠",
    "绛剧",
    "榛樿",
    "绠＄悊",
    "鍝嶅",
    "缁撴",
    "鏂版",
    "涓嶆",
    "瀹㈡",
    "鈥",
    "鈹",
)


class SpecScopeConsistencyTests(unittest.TestCase):
    def test_superseded_alpha1_plan_does_not_prescribe_nsfw_customer_flag(self):
        plan = (PROJECT_DIR / "docs/plans/2026-06-04-alpha1-architecture-plan.md").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("users.nsfw_enabled", plan)
        self.assertNotIn("ADD COLUMN nsfw_enabled", plan)
        self.assertNotIn("NSFW enable flag", plan)

    def test_public_readme_uses_native_model_ids_without_internal_registry_fields(self):
        readme = (PROJECT_DIR / "README.md").read_text(encoding="utf-8")

        self.assertIn("Seedance 视频生成白标 Relay", readme)
        self.assertIn("管理员可以主动配置模型别名", readme)
        self.assertIn("dreamina-seedance-2-0-260128", readme)
        self.assertIn("seedance-1-0-lite-t2v-250428", readme)
        self.assertNotIn("upstream_model_or_endpoint", readme)
        for fragment in MOJIBAKE_FRAGMENTS:
            self.assertNotIn(fragment, readme)

    def test_native_model_ids_use_current_byteplus_ids(self):
        checked_files = [
            "README.md",
            "API_DOCS.md",
            "relay_server.py",
            "runtime-go/internal/models/models.go",
            "runtime-go/internal/pricing/estimator.go",
            "static/app.html",
        ]

        for relative in checked_files:
            content = (PROJECT_DIR / relative).read_text(encoding="utf-8")
            self.assertIn("seedance-1-0-pro-fast-251015", content, relative)
            self.assertNotIn("seedance-1-0-pro-fast-250528", content, relative)

    def test_public_api_docs_keep_operator_only_nsfw_and_proxy_only_boundaries(self):
        docs = (PROJECT_DIR / "API_DOCS.md").read_text(encoding="utf-8")

        self.assertIn("面向签约客户的 Seedance 视频生成 API 文档", docs)
        for fragment in [
            "NSFW",
            "成人向模型",
            "endpoint/profile",
            "operator notes",
            '"api_key": "sk-',
            "X-Admin-Key",
            "/admin/",
            "ADMIN_KEY",
            "BYTEPLUS_ACCESS_KEY",
            "MODELARK_ASSET_GROUP_ID",
            "create_asset_white_label.py",
            "Admin Model Availability",
        ]:
            self.assertNotIn(fragment, docs)

        self.assertIn("api_key_masked", docs)
        self.assertIn("客户响应只返回 Relay 域名的视频 URL", docs)
        self.assertIn("生成结果默认不永久保存在 Relay 服务器", docs)
        for fragment in MOJIBAKE_FRAGMENTS:
            self.assertNotIn(fragment, docs)

    def test_customer_ui_keeps_operator_only_terms_out_of_guidance(self):
        app_html = (PROJECT_DIR / "static/app.html").read_text(encoding="utf-8")

        self.assertIn("客户只用 Relay API Key", app_html)
        self.assertIn("内部密钥不会出现在用户端", app_html)
        for fragment in [
            "NSFW",
            "SFW",
            "endpoint/profile",
            "operator notes",
            "X-Admin-Key",
            "ADMIN_KEY",
            "BYTEPLUS_ACCESS_KEY",
            "MODELARK_ASSET_GROUP_ID",
            "create_asset_white_label.py",
            "IAM AK/SK",
            "ark Key",
            "上游",
        ]:
            self.assertNotIn(fragment, app_html)

    def test_public_api_docs_use_price_multiplier_as_primary_pricing_field(self):
        docs = (PROJECT_DIR / "API_DOCS.md").read_text(encoding="utf-8")

        self.assertIn('"price_multiplier": 1.3', docs)
        self.assertNotIn('"markup_pct": 0.3', docs)
        self.assertIn("新接入请忽略它，只读取 `price_multiplier`", docs)

    def test_public_api_docs_explain_native_model_ids_and_optional_aliases(self):
        docs = (PROJECT_DIR / "API_DOCS.md").read_text(encoding="utf-8")

        self.assertIn("默认使用字节 API 原生 `model id`", docs)
        self.assertIn("管理员主动配置了别名", docs)
        self.assertIn("dreamina-seedance-2-0-260128", docs)
        self.assertIn("seedance-1-0-lite-t2v-250428", docs)

    def test_public_api_docs_match_current_customer_response_shapes(self):
        docs = (PROJECT_DIR / "API_DOCS.md").read_text(encoding="utf-8")

        self.assertIn("除公开模型列表外，客户接口都使用 Relay API Key", docs)
        self.assertIn(
            "`GET /v1/models` 不带 Key 时返回默认公开模型列表",
            docs,
        )
        self.assertNotIn("所有 `/v1/*` 客户接口都使用 Relay API Key", docs)
        models_curl = docs.split("curl https://video.example.com/v1/models", 1)[1].split(
            "```",
            1,
        )[0]
        self.assertNotIn("Authorization", models_curl)

        self.assertNotIn('"object": "list"', docs)
        self.assertIn('"data": [', docs)
        self.assertIn('"api_key_masked": "sk-abc...xyz"', docs)
        self.assertIn('"api_key": "<new_relay_key_shown_once>"', docs)
        self.assertIn('"api_key_masked": "sk-new...shown"', docs)
        self.assertIn('"rotated_at": 1760000000', docs)
        self.assertNotIn('"api_key": "sk-', docs)
        self.assertIn("`missing_auth`", docs)
        self.assertIn("`method_not_allowed`", docs)
        self.assertIn("`not_ready`", docs)
        self.assertIn("`proxy_error` / `proxy_range_unsupported`", docs)
        self.assertNotIn("`unauthorized`", docs)
        self.assertNotIn("`task_not_ready`", docs)
        self.assertIn('"role": "reference_video"', docs)
        self.assertNotIn('"role": "reference"', docs)

    def test_active_spec_reflects_current_execution_state_and_release_gate(self):
        spec = (PROJECT_DIR / "docs/plans/2026-06-04-alpha1-runtime-proxy-spec.md").read_text(
            encoding="utf-8"
        )

        for stale_fragment in [
            "Do not implement until",
            "cannot currently change",
            "cannot currently rotate",
            "Current successful task flow stores",
            "starts `_persist_video`",
            "Owner Decisions Before Implementation",
            "tests/test_account_security.py",
            "tests/test_customer_models.py",
            "tests/test_video_proxy_policy.py",
        ]:
            self.assertNotIn(stale_fragment, spec)

        self.assertIn("Implementation State Snapshot", spec)
        self.assertIn("Phase 7: Release Evidence", spec)
        self.assertIn("MODEL_ID_ALIASES_JSON", spec)
        self.assertIn("aliases appear only when configured and explicitly enabled for that customer", spec)
        self.assertIn("Public docs and default UI examples must use native BytePlus API model IDs", spec)
        self.assertIn("external deployment evidence is still required", spec)

    def test_active_spec_has_readable_operator_wording(self):
        spec = (PROJECT_DIR / "docs/plans/2026-06-04-alpha1-runtime-proxy-spec.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("消费系数 / Price Multiplier", spec)
        self.assertIn("管理员主动配置别名时才改变客户可见模型名称", spec)
        self.assertIn("insufficient balance must be rejected before control-plane content preparation", spec)
        self.assertIn("reserve the customer balance before control-plane content preparation and upstream submission", spec)
        self.assertIn("refund the reserved balance if preparation or upstream creation fails", spec)
        self.assertIn("attempt to cancel the upstream task", spec)
        self.assertIn("Any reachable FastAPI fallback `POST /v1/videos` path must follow the same", spec)
        for fragment in MOJIBAKE_FRAGMENTS:
            self.assertNotIn(fragment, spec)


if __name__ == "__main__":
    unittest.main()
