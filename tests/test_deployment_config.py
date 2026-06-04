import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


class DeploymentConfigTests(unittest.TestCase):
    def read(self, name):
        return (PROJECT_DIR / name).read_text(encoding="utf-8")

    def test_relay_dockerfile_installs_curl_for_healthcheck(self):
        dockerfile = self.read("Dockerfile.relay")
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn("CMD curl -fsS http://localhost:8002/health", dockerfile)
        run_lines = [line.strip() for line in dockerfile.splitlines() if line.strip().startswith("RUN ")]
        self.assertTrue(
            any("apt-get update" in line and "install" in line and "curl" in line for line in run_lines),
            "Dockerfile.relay must install curl in an actual RUN layer, not inside a comment",
        )

    def test_runtime_dockerfile_has_binary_healthcheck(self):
        dockerfile = self.read("Dockerfile.runtime-go")
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn('CMD ["/app/seedance-runtime", "healthcheck"]', dockerfile)

    def test_runtime_waits_for_relay_health_in_compose(self):
        compose = self.read("docker-compose.relay.yml")
        self.assertIn("seedance-runtime:", compose)
        self.assertIn("condition: service_healthy", compose)
        self.assertIn('CONTROL_PLANE_BASE_URL: "http://seedance-relay:8002"', compose)

    def test_env_example_contains_required_runtime_settings_and_readable_comments(self):
        env = self.read(".env.relay.example")
        for key in (
            "UPSTREAM_API_KEY=",
            "ADMIN_KEY=",
            "RUNTIME_INTERNAL_TOKEN=",
            "CONTROL_PLANE_BASE_URL=",
            "VIDEO_PERSIST_MODE=proxy_only",
            "DB_PATH=/data/relay.sqlite",
        ):
            self.assertIn(key, env)
        env.encode("ascii")

    def test_local_evidence_outputs_are_gitignored(self):
        gitignore = self.read(".gitignore")
        for pattern in (
            "alpha1-evidence/",
            "alpha1-evidence*/",
            "alpha1-local-gate/",
            "alpha1-local-gate*/",
            "alpha1-local-acceptance.json",
        ):
            self.assertIn(pattern, gitignore)

    def test_caddy_routes_hot_paths_explicitly_without_broad_video_wildcard(self):
        caddy = self.read("deploy/caddy_video.snippet")
        self.assertIn("@runtime_models path /v1/models", caddy)
        self.assertIn("@runtime_video_estimate path /v1/videos/estimate", caddy)
        self.assertIn("method GET POST", caddy)
        self.assertIn("path /v1/videos", caddy)
        self.assertIn("^/v1/videos/[^/]+$", caddy)
        self.assertIn("^/v1/videos/[^/]+/content$", caddy)
        self.assertNotIn("path /v1/videos*", caddy)
        self.assertNotIn("path_regexp runtime_all_videos", caddy)

    def test_caddy_streaming_and_header_directives_are_not_commented_out(self):
        caddy = self.read("deploy/caddy_video.snippet")
        active_lines = [
            line.strip()
            for line in caddy.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        self.assertGreaterEqual(active_lines.count("flush_interval -1"), 2)
        self.assertIn("header {", active_lines)
        self.assertFalse(
            any(
                line.strip().startswith("#") and ("flush_interval -1" in line or "header {" in line)
                for line in caddy.splitlines()
            )
        )

    def test_acceptance_demo_requires_caddy_validation_on_deploy_host(self):
        demo = self.read("docs/ops/alpha1-acceptance-demo.md")
        self.assertIn("caddy validate --config /etc/caddy/Caddyfile", demo)

    def test_acceptance_demo_mentions_automated_live_and_browser_smoke(self):
        demo = self.read("docs/ops/alpha1-acceptance-demo.md")
        self.assertIn("python deploy/alpha1_preflight.py", demo)
        self.assertIn("python deploy/alpha1_collect_evidence.py", demo)
        self.assertIn("python deploy/alpha1_probe.py", demo)
        self.assertIn("python deploy/alpha1_verify_evidence.py", demo)
        self.assertIn("does not create paid generation tasks", demo)
        self.assertIn("--screenshot alpha1-browser-video.png", demo)
        self.assertIn("--json-output alpha1-probe-evidence.json", demo)
        self.assertIn("2>&1 | tee alpha1-probe-output.txt", demo)
        self.assertIn("--probe-output alpha1-probe-output.txt", demo)
        self.assertIn("valid PNG screenshot", demo)
        self.assertIn("320x180", demo)
        self.assertIn("non-PNG placeholders", demo)
        self.assertIn("tests.test_alpha1_live_smoke.Alpha1LiveSmokeTests", demo)
        self.assertIn("ALPHA1_LIVE_SMOKE_EVIDENCE_JSON", demo)
        self.assertIn("alpha1-local-acceptance.json", demo)
        self.assertIn("browser_network_uses_relay_video_url=true", demo)
        self.assertIn("Playwright Chromium", demo)
        self.assertIn("browser-visible requests/responses", demo)
        self.assertIn("python deploy/alpha1_preflight.py --caddyfile /etc/caddy/Caddyfile 2>&1 | tee alpha1-preflight-output.txt", demo)
        self.assertIn("caddy validate --config /etc/caddy/Caddyfile 2>&1 | tee caddy-validate-output.txt", demo)

        manual_verify_start = demo.index("python deploy/alpha1_verify_evidence.py \\\n  --base-url")
        manual_verify_end = demo.index("\n```", manual_verify_start)
        manual_verify_command = demo[manual_verify_start:manual_verify_end]
        for needle in (
            "--base-url https://video.customer-domain.com",
            "--video-id vid_existing_success",
            "--local-acceptance-json alpha1-local-acceptance.json",
            "--local-gate-manifest alpha1-local-gate/<timestamp>/local-gate-manifest.json",
        ):
            self.assertIn(needle, manual_verify_command)

    def test_alpha1_preflight_script_checks_core_deploy_guards(self):
        script = self.read("deploy/alpha1_preflight.py")
        for needle in (
            "VIDEO_PERSIST_MODE must be proxy_only",
            "ADMIN_KEY and RUNTIME_INTERNAL_TOKEN must be different secrets",
            "path /v1/videos*",
            "condition: service_healthy",
            "PRAGMA busy_timeout",
            "caddy validate",
        ):
            self.assertIn(needle, script)

    def test_alpha1_probe_script_is_read_only_and_checks_customer_visible_surfaces(self):
        script = self.read("deploy/alpha1_probe.py")
        self.assertIn("This probe does not create generation tasks", script)
        for needle in (
            "GET\", urljoin(base, \"health\")",
            "GET\", urljoin(base, \"v1/me\")",
            "GET\", urljoin(base, \"v1/models\")",
            "GET\", urljoin(base, \"v1/videos\")",
            "Range",
            "Playwright",
            "--screenshot requires --browser",
            "browser.request_urls",
            "browser.responses",
            "leaked sensitive marker",
        ):
            self.assertIn(needle, script)

    def test_operator_docs_allow_only_safe_upstream_error_metadata(self):
        demo = self.read("docs/ops/alpha1-acceptance-demo.md")
        security = self.read("docs/ops/security-runbook.md")
        for text in (demo, security):
            self.assertIn("request_id", text)
            self.assertIn("upstream", text.lower())
        self.assertIn("Do not expose upstream error bodies", security)
        self.assertIn("provider URLs, video URLs, keys, tokens, or signed URLs", demo)

    def test_runtime_runbook_documents_sqlite_single_writer_boundary(self):
        runbook = self.read("docs/ops/runtime-proxy-runbook.md")
        self.assertIn("WAL mode", runbook)
        self.assertIn("busy timeout", runbook)
        self.assertIn("Run only one Go runtime writer process", runbook)
        self.assertIn("Do not scale `seedance-runtime` horizontally while using SQLite", runbook)
        self.assertIn("Move to Postgres before production scale", runbook)

    def test_runtime_runbook_documents_malformed_authorization_boundary(self):
        runbook = self.read("docs/ops/runtime-proxy-runbook.md")
        self.assertIn("malformed or invalid", runbook)
        self.assertIn("instead of falling back to a session cookie", runbook)
        self.assertIn("unauthenticated public model list", runbook)

    def test_runtime_runbook_documents_cancellation_refund_boundary(self):
        runbook = self.read("docs/ops/runtime-proxy-runbook.md")
        self.assertIn("delete/cancel refund held balance", runbook)
        self.assertIn("settled=0", runbook)
        self.assertIn("settled=1", runbook)

    def test_release_checklist_maps_spec_to_required_evidence(self):
        checklist = self.read("docs/ops/alpha1-release-checklist.md")
        for needle in (
            "docs/ops/alpha1-evidence-runbook.md",
            "Customer multiplier",
            "Customer model list",
            "No Relay content censorship",
            "No SFW/NSFW toggle",
            "BytePlus URL hidden",
            "Runtime split",
            "SQLite boundary",
            "Malformed Authorization handling",
            "Cancellation refund idempotency",
            "alpha1_preflight.py",
            "alpha1_local_gate.py",
            "alpha1_collect_evidence.py",
            "alpha1_probe.py",
            "alpha1_verify_evidence.py",
            "alpha1_completion_audit.py",
            "alpha1_worktree_check.py",
            "deploy_preflight",
            "caddy_validate",
            "external_probe",
            "evidence_verify",
            "completion_audit",
            "local_gate_manifest_verify",
            "python_unittest",
            "go_test",
            "malformed Authorization handling",
            "delete/cancel refund idempotency",
            "local_gate_match",
            '"local_gate_match": true',
            "byte-identical copied local gate manifest",
            "evidence directory artifacts are preferred over original absolute paths",
            "full local gate directory",
            "archived `python_unittest` and `go_test` outputs remain reviewable",
            "staged diffs",
            "untracked text files",
            "final-newline checks",
            "not archived beside that manifest",
            "local gate manifest does not match external manifest artifact",
            "--local-only",
            "--local-acceptance-json",
            "--local-gate-manifest",
            "--base-url https://video.customer-domain.com",
            "--video-id vid_existing_success",
            "--manifest",
            "--summary-json alpha1-completion-summary.json",
            "release_acceptable=false",
            '"release_complete": true',
            "caddy validate --config /etc/caddy/Caddyfile",
            "static/app.html",
            "static/admin.html",
            "vm.Script",
            "(cd runtime-go && go test -v ./...)",
            "(cd runtime-go && go build ./...)",
            "alpha1-probe-evidence.json",
            "alpha1-probe-output.txt",
            "alpha1-browser-video.png",
            "valid PNG screenshot",
            "320x180",
            "non-PNG placeholders",
            "tiny placeholder images",
            "alpha1-preflight-output.txt",
            "caddy-validate-output.txt",
            "alpha1-completion-output.txt",
            "manifest.json",
            "alpha1-local-acceptance.json",
            "local-gate-manifest.json",
            "browser_network_uses_relay_video_url=true",
            "runtime_paths",
            "relay_paths",
            "audit_actions",
            "Do Not Mark Complete",
        ):
            self.assertIn(needle, checklist)

        explicit_verify_start = checklist.index("python deploy/alpha1_verify_evidence.py \\\n  --base-url")
        explicit_verify_end = checklist.index("\n```\n\nRequired saved artifacts:", explicit_verify_start)
        explicit_verify_command = checklist[explicit_verify_start:explicit_verify_end]
        self.assertIn("--local-gate-manifest", explicit_verify_command)
        self.assertIn("--probe-output alpha1-probe-output.txt", explicit_verify_command)
        self.assertIn("2>&1 | tee alpha1-probe-output.txt", checklist)
        self.assertNotIn("local-gate-manifest.json` if `alpha1_local_gate.py` was used", checklist)
        self.assertIn("`local-gate-manifest.json` from the required local gate", checklist)

    def test_evidence_runbook_documents_short_release_path(self):
        runbook = self.read("docs/ops/alpha1-evidence-runbook.md")
        for needle in (
            "alpha1_collect_evidence.py",
            "alpha1_local_gate.py",
            "alpha1_completion_audit.py",
            "ALPHA1_CUSTOMER_API_KEY",
            "--base-url https://video.customer-domain.com",
            "--video-id vid_existing_success",
            "--probe-output alpha1-probe-output.txt",
            "--caddyfile /etc/caddy/Caddyfile",
            "manifest.json",
            '"status": "passed"',
            '"release_acceptable": true',
            '"browser_required": true',
            "alpha1_verify_evidence.py",
            "deploy_preflight",
            "caddy_validate",
            "external_probe",
            "evidence_verify",
            "completion_audit",
            "local_gate_manifest_verify",
            "python_unittest",
            "go_test",
            "malformed Authorization handling",
            "delete/cancel refund idempotency",
            "local_gate_match",
            "byte-identical copied local gate manifest",
            "evidence directory artifacts are preferred over original absolute paths",
            "full local gate directory",
            "archived `python_unittest` and `go_test` outputs remain reviewable",
            "not archived beside that manifest",
            "local gate manifest does not match external manifest artifact",
            "--manifest alpha1-evidence/<timestamp>/manifest.json",
            "--summary-json alpha1-completion-summary.json",
            "Alpha1 completion audit passed",
            '"status": "complete"',
            '"release_complete": true',
            '"failures": []',
            'checks.external_manifest.status="passed"',
            "pointing to that same manifest",
            "artifacts.completion_summary",
            "alpha1-completion-output.txt",
            "valid PNG screenshot",
            "320x180",
            "non-PNG placeholders",
            "tiny placeholder images",
            "external HTTPS bare origin",
            "embedded username/password",
            "path, query, or fragment",
            "redacts known API-key, session, upstream-key, and provider-domain patterns",
            "structured probe JSON is still verified separately",
            "--dry-run",
            "--no-browser",
            "Do not mark alpha1 complete",
        ):
            self.assertIn(needle, runbook)

    def test_release_evidence_entrypoints_do_not_use_reserved_example_domains(self):
        checked = {
            "deploy/alpha1_collect_evidence.py": self.read("deploy/alpha1_collect_evidence.py"),
            "deploy/alpha1_probe.py": self.read("deploy/alpha1_probe.py"),
            "deploy/caddy_video.snippet": self.read("deploy/caddy_video.snippet"),
            "docs/ops/alpha1-acceptance-demo.md": self.read("docs/ops/alpha1-acceptance-demo.md"),
            "docs/ops/alpha1-evidence-runbook.md": self.read("docs/ops/alpha1-evidence-runbook.md"),
            "docs/ops/alpha1-release-checklist.md": self.read("docs/ops/alpha1-release-checklist.md"),
        }

        for path, text in checked.items():
            self.assertNotIn("video.example.com", text, path)
            self.assertNotIn("seedance-relay.example.com", text, path)
            self.assertIn("customer-domain.com", text, path)

    def test_scope_map_keeps_user_admin_architecture_boundaries_explicit(self):
        scope = self.read("docs/ops/alpha1-scope-map.md")
        for needle in (
            "Customer Side",
            "Admin Side",
            "Runtime Architecture",
            "Improvement Space After Alpha1",
            "price_multiplier",
            "enabled_models",
            "without Relay prompt/content censorship",
            "A separate SFW/NSFW customer toggle",
            "FastAPI keeps admin, auth, upload, docs",
            "Go owns selected customer hot paths",
            "VIDEO_PERSIST_MODE=proxy_only",
            "Move from SQLite to Postgres",
        ):
            self.assertIn(needle, scope)

        demo = self.read("docs/ops/alpha1-acceptance-demo.md")
        self.assertIn("docs/ops/alpha1-scope-map.md", demo)
        self.assertIn("docs/ops/alpha1-release-checklist.md", demo)
        self.assertIn("docs/ops/alpha1-evidence-runbook.md", demo)


if __name__ == "__main__":
    unittest.main()

