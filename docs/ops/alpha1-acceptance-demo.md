# Alpha1 Acceptance Demo

Use this checklist after deploying FastAPI, Go runtime, and Caddy with the same `.env.relay` values. For the bounded user/admin/architecture scope, see `docs/ops/alpha1-scope-map.md`; for the release gate, see `docs/ops/alpha1-release-checklist.md`; for the shortest evidence command path, see `docs/ops/alpha1-evidence-runbook.md`.

## Required Environment

- `VIDEO_PERSIST_MODE=proxy_only`
- `RUNTIME_INTERNAL_TOKEN` is set to a random secret.
- `CONTROL_PLANE_BASE_URL` points from Go runtime to FastAPI.
- Caddy routes the explicit customer hot paths to Go runtime.
- FastAPI keeps admin, auth, uploads, docs, and static UI.
- On the deployment host, run `caddy validate --config /etc/caddy/Caddyfile` before reload.

## Local Automated Smoke

Before the manual demo, run the deployment preflight and the live smoke:

```bash
python deploy/alpha1_local_gate.py --dry-run
python deploy/alpha1_preflight.py --skip-db
python -m unittest tests.test_alpha1_live_smoke.Alpha1LiveSmokeTests.test_fastapi_control_plane_and_go_runtime_acceptance_path -v
```

To archive the local smoke as machine-readable evidence:

```bash
ALPHA1_LIVE_SMOKE_EVIDENCE_JSON=alpha1-local-acceptance.json \
python -m unittest tests.test_alpha1_live_smoke.Alpha1LiveSmokeTests.test_fastapi_control_plane_and_go_runtime_acceptance_path -v
python deploy/alpha1_verify_evidence.py \
  --local-only \
  --local-acceptance-json alpha1-local-acceptance.json
```

For release evidence, this JSON must include `browser_network_uses_relay_video_url=true`. Install Playwright Chromium before generating it if the browser network check is unavailable.
It must also include `process_logs_hide_upstream_urls_and_secrets=true`, proving relay/runtime process logs do not expose upstream video URLs or customer/upstream keys.

On a deployment host with Caddy installed, prefer:

```bash
python deploy/alpha1_preflight.py --caddyfile /etc/caddy/Caddyfile
```

This starts FastAPI, the Go runtime, a Caddy-equivalent local proxy, and a mock upstream. If Playwright Chromium is available, the smoke also opens a real browser page with a `<video>` element and verifies browser-visible requests/responses only use the Relay video content URL, with no upstream video URL or redirect.

After deploying to a real domain, run the read-only production probe with an existing customer key. Add `--video-id` only when you already have a succeeded task to verify content proxying; the probe does not create paid generation tasks.

```bash
export ALPHA1_CUSTOMER_API_KEY=sk_existing_customer_key
python deploy/alpha1_collect_evidence.py \
  --base-url https://video.customer-domain.com \
  --video-id vid_existing_success \
  --local-acceptance-json alpha1-local-acceptance.json \
  --local-gate-manifest alpha1-local-gate/<timestamp>/local-gate-manifest.json \
  --caddyfile /etc/caddy/Caddyfile \
  --out-dir alpha1-evidence/$(date -u +%Y%m%dT%H%M%SZ)
```

If you need manual commands instead of the collector:

```bash
python deploy/alpha1_preflight.py --caddyfile /etc/caddy/Caddyfile 2>&1 | tee alpha1-preflight-output.txt
caddy validate --config /etc/caddy/Caddyfile 2>&1 | tee caddy-validate-output.txt
python deploy/alpha1_probe.py \
  --base-url https://video.customer-domain.com \
  --api-key sk_existing_customer_key \
  --video-id vid_existing_success \
  --browser \
  --screenshot alpha1-browser-video.png \
  --json-output alpha1-probe-evidence.json \
  2>&1 | tee alpha1-probe-output.txt
python deploy/alpha1_verify_evidence.py \
  --base-url https://video.customer-domain.com \
  --video-id vid_existing_success \
  --probe-json alpha1-probe-evidence.json \
  --probe-output alpha1-probe-output.txt \
  --screenshot alpha1-browser-video.png \
  --preflight-output alpha1-preflight-output.txt \
  --local-acceptance-json alpha1-local-acceptance.json \
  --local-gate-manifest alpha1-local-gate/<timestamp>/local-gate-manifest.json \
  --caddy-output caddy-validate-output.txt
```

## Demo Steps

1. Admin creates a customer.
2. Admin sets `price_multiplier` to `1.2`.
3. Admin enables only the selected model IDs for that customer. Native BytePlus API model IDs are the default; optional aliases are allowed only when explicitly configured.
4. Customer tries to reuse the current password and is rejected; then changes their own password, the old password is rejected, the new password logs in, and an old session cookie is revoked.
5. Customer rotates their own Relay API key.
6. Verify the old Relay API key fails and the new key works.
7. Customer calls `GET /v1/models` and sees only enabled models.
8. Customer submits a disabled model and receives `model_not_enabled` before any upstream call.
9. Customer submits an enabled native `content[]` request.
10. If `real_person_mode=true`, Go delegates content preparation to FastAPI before upstream submission.
11. Relay returns only Relay-domain video URLs.
12. Customer calls `GET /v1/videos` and sees only their own tasks.
13. Customer calls `GET /v1/videos/{id}` until the task succeeds.
14. Customer plays `GET /v1/videos/{id}/content`; `Range` requests return `206` with `Content-Range`.
15. Browser/network logs do not show the BytePlus temporary video URL.
16. No new MP4 appears under `VIDEO_DIR` for new successful tasks.
17. Admin resets the customer password and rotates the customer's Relay API key; stale session/key evidence is captured, including session revocation after admin key rotation.
18. Admin opens the Audit tab or calls `GET /admin/audit-events`.
19. Audit shows multiplier/model/key/password/balance/status/upstream-key changes without secret values.

## Evidence To Capture

- Admin customer detail showing multiplier and enabled model list.
- `alpha1-local-acceptance.json` showing the FastAPI control-plane and Go runtime smoke passed.
- Customer account page after password change and API key rotation.
- API response for old key failure.
- `GET /v1/models` response for the customer.
- Disabled-model rejection response.
- `GET /v1/videos` response showing only the customer's tasks and only Relay-domain `video_url` values.
- `GET /v1/videos/{id}` response with Relay-domain `video_url`.
- Browser/network screenshot showing Relay-domain video content request only.
- `alpha1-browser-video.png` saved as a valid PNG screenshot with dimensions at least `320x180`; empty files, non-PNG placeholders, and tiny placeholder images are not acceptable.
- Directory listing of `VIDEO_DIR` showing no new `{task}.mp4`.
- Audit tab or `/admin/audit-events` response showing expected actions.

## Do Not Accept

- BytePlus video URL appears in customer JSON, `Location` headers, frontend state, browser-visible errors, or logs.
- Public `POST /v1/videos` bypasses the FastAPI prepare helper when `real_person_mode=true`.
- Audit rows contain passwords, Relay API keys, BytePlus keys, session tokens, or upstream video URLs.
- Upstream failure responses expose provider URLs, video URLs, keys, tokens, or signed URLs instead of only safe metadata such as `request_id`.
- Relay/runtime process logs contain upstream video URLs, customer Relay keys, or upstream keys.
- A broad Caddy wildcard sends admin/auth/upload/docs routes to Go runtime.

