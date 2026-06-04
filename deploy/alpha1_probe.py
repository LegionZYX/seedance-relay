#!/usr/bin/env python3
"""Read-only alpha1 production probe.

This probe does not create generation tasks. Use an existing customer Relay API
key, and optionally an existing succeeded video id, to collect deployment
evidence from a real domain.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, build_opener, ProxyHandler


SENSITIVE_MARKERS = (
    "byteplus",
    "bytepluses.com",
    "volces.com",
    "ark-",
)
SENSITIVE_PATTERNS = (
    (re.compile(r"\bsk[-_][A-Za-z0-9][A-Za-z0-9_-]{8,}\b"), "Relay API key"),
    (re.compile(r"\bauthorization\s*:\s*bearer\s+[A-Za-z0-9._-]{8,}", re.I), "Bearer token"),
    (re.compile(r"\brelay_session=[^;\s]+", re.I), "relay_session cookie"),
)


@dataclass
class ProbeReport:
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def ok(self, key: str, value: Any) -> None:
        self.evidence[key] = value
        print(f"[OK] {key}: {value}")

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        print(f"[WARN] {message}")

    def fail(self, message: str) -> None:
        self.failures.append(message)
        print(f"[FAIL] {message}")


def request(method: str, url: str, api_key: str | None = None, headers: dict[str, str] | None = None):
    request_headers = dict(headers or {})
    if api_key:
        request_headers["Authorization"] = f"Bearer {api_key}"
    req = Request(url, headers=request_headers, method=method)
    opener = build_opener(ProxyHandler({}))
    try:
        resp = opener.open(req, timeout=20)
    except HTTPError as exc:
        resp = exc
    body = resp.read()
    header_map = {key.lower(): value for key, value in resp.headers.items()}
    return resp.status, header_map, body


def decode_json(body: bytes) -> Any:
    return json.loads(body.decode("utf-8"))


def assert_no_sensitive(raw: bytes | str, label: str, report: ProbeReport) -> None:
    text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw
    lowered = text.lower()
    for marker in SENSITIVE_MARKERS:
        if marker in lowered:
            report.fail(f"{label} leaked sensitive marker: {marker}")
    for pattern, name in SENSITIVE_PATTERNS:
        if pattern.search(text):
            report.fail(f"{label} leaked sensitive token: {name}")


def expect_status(status: int, expected: set[int], label: str, report: ProbeReport) -> bool:
    if status not in expected:
        report.fail(f"{label} returned {status}, expected {sorted(expected)}")
        return False
    return True


def run_probe(
    base_url: str,
    api_key: str,
    video_id: str | None = None,
    browser: bool = False,
    screenshot: str | None = None,
) -> ProbeReport:
    report = ProbeReport()
    base = base_url.rstrip("/") + "/"

    status, headers, body = request("GET", urljoin(base, "health"))
    if expect_status(status, {200}, "GET /health", report):
        report.ok("health", status)
    assert_no_sensitive(body, "health body", report)

    status, headers, body = request("GET", urljoin(base, "v1/me"), api_key)
    if expect_status(status, {200}, "GET /v1/me", report):
        me = decode_json(body)
        report.ok("me.email_present", bool(me.get("email")))
        if "api_key" in me:
            report.fail("GET /v1/me returned full api_key")
        if "api_key_masked" not in me:
            report.fail("GET /v1/me missing api_key_masked")
    assert_no_sensitive(body, "me body", report)

    status, headers, body = request("GET", urljoin(base, "v1/models"), api_key)
    if expect_status(status, {200}, "GET /v1/models", report):
        models = decode_json(body).get("data", [])
        report.ok("models.count", len(models))
    assert_no_sensitive(body, "models body", report)

    status, headers, body = request("GET", urljoin(base, "v1/videos"), api_key)
    if expect_status(status, {200}, "GET /v1/videos", report):
        videos = decode_json(body)
        report.ok("videos.total", videos.get("total"))
    assert_no_sensitive(body, "videos body", report)

    if video_id:
        detail_path = f"v1/videos/{video_id}"
        content_path = f"v1/videos/{video_id}/content"
        status, headers, body = request("GET", urljoin(base, detail_path), api_key)
        if expect_status(status, {200}, f"GET /{detail_path}", report):
            detail = decode_json(body)
            video_url = str(detail.get("video_url", ""))
            if detail.get("status") == "succeeded":
                if not video_url.startswith(base_url.rstrip("/") + "/v1/videos/"):
                    report.fail("video_url is not Relay-domain content URL")
                else:
                    report.ok("video_url.relay_domain", True)
        assert_no_sensitive(body, "video detail body", report)

        status, headers, body = request("HEAD", urljoin(base, content_path), api_key)
        if expect_status(status, {200, 206}, f"HEAD /{content_path}", report):
            report.ok("content.head_status", status)
            head_location_present = "location" in headers
            report.ok("content.head_location_present", head_location_present)
            if head_location_present:
                report.fail("content HEAD returned Location redirect")
        assert_no_sensitive(str(headers), "content HEAD headers", report)

        status, headers, body = request(
            "GET",
            urljoin(base, content_path),
            api_key,
            headers={"Range": "bytes=0-1"},
        )
        if expect_status(status, {206}, f"Range GET /{content_path}", report):
            report.ok("content.range_status", status)
            range_content_range_present = "content-range" in headers
            range_location_present = "location" in headers
            report.ok("content.range_content_range_present", range_content_range_present)
            report.ok("content.range_location_present", range_location_present)
            if not range_content_range_present:
                report.fail("206 response missing Content-Range")
            if range_location_present:
                report.fail("content Range GET returned Location redirect")
        assert_no_sensitive(str(headers), "content Range headers", report)
        assert_no_sensitive(body, "content Range body", report)

        if browser:
            run_browser_probe(urljoin(base, content_path), api_key, report, screenshot)
    else:
        report.warn("no --video-id provided; skipped content/Range/browser checks")

    return report


def run_browser_probe(content_url: str, api_key: str, report: ProbeReport, screenshot: str | None = None) -> None:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        report.warn(f"Playwright unavailable; skipped browser probe: {exc}")
        return
    requests: list[str] = []
    responses: list[dict[str, Any]] = []
    report.evidence["browser.expected_content_url"] = content_url
    with sync_playwright() as playwright:
        browser = None
        try:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(extra_http_headers={"Authorization": f"Bearer {api_key}"})
            page.on("request", lambda req: requests.append(req.url))
            page.on("response", lambda resp: responses.append({"url": resp.url, "status": resp.status, "headers": resp.headers}))
            with page.expect_request(lambda req: req.url == content_url, timeout=15000):
                page.set_content(
                    f"<video src='{content_url}' controls autoplay muted playsinline></video>",
                    wait_until="domcontentloaded",
                )
            page.wait_for_timeout(1000)
            if screenshot:
                path = Path(screenshot)
                path.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(path), full_page=True)
                report.ok("browser.screenshot", str(path))
        except PlaywrightTimeoutError as exc:
            report.fail(f"browser did not request Relay content URL: {exc}")
        except PlaywrightError as exc:
            report.warn(f"Playwright browser probe skipped: {exc}")
        finally:
            if browser is not None:
                browser.close()
    if requests:
        if content_url not in requests:
            report.fail("browser requests did not include Relay content URL")
        for url in requests:
            assert_no_sensitive(url, "browser request URL", report)
        for response in responses:
            assert_no_sensitive(str(response), "browser response metadata", report)
        report.ok("browser.requests", len(requests))
        report.evidence["browser.request_urls"] = requests
        report.evidence["browser.responses"] = [
            {
                "url": response["url"],
                "status": response["status"],
                "content_type": response["headers"].get("content-type"),
                "content_range": response["headers"].get("content-range"),
                "location_present": "location" in response["headers"],
            }
            for response in responses
        ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run read-only alpha1 production probe.")
    parser.add_argument("--base-url", required=True, help="Relay base URL, for example https://video.customer-domain.com")
    parser.add_argument("--api-key", required=True, help="Existing customer Relay API key")
    parser.add_argument("--video-id", help="Existing succeeded video id for content/Range/browser checks")
    parser.add_argument("--browser", action="store_true", help="Use Playwright browser network probe when --video-id is provided")
    parser.add_argument("--screenshot", help="Optional screenshot path for the browser probe")
    parser.add_argument("--json-output", help="Optional path for evidence JSON")
    args = parser.parse_args(argv)

    if args.screenshot and not args.browser:
        parser.error("--screenshot requires --browser")
    report = run_probe(args.base_url, args.api_key, args.video_id, args.browser, args.screenshot)
    payload = {
        "failures": report.failures,
        "warnings": report.warnings,
        "evidence": report.evidence,
    }
    if args.json_output:
        with open(args.json_output, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    if report.failures:
        print(f"Probe failed: {len(report.failures)} failure(s), {len(report.warnings)} warning(s)")
        return 1
    print(f"Probe passed: {len(report.warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

