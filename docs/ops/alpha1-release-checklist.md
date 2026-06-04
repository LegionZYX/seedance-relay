# Alpha1 Release Evidence Checklist

Use this file as the final release gate after implementing the alpha1 SPEC. It maps the user-facing, admin-facing, and architecture requirements to concrete evidence. Do not mark complete until the local automated evidence and the external deployment evidence are both captured. For the shortest deployment-host command path, use `docs/ops/alpha1-evidence-runbook.md`.

## Scope Boundary

Included in alpha1:

- Customer account controls: password change, Relay API key rotation, customer-visible model list, and Relay-domain video playback.
- Admin controls: per-customer `price_multiplier`, per-customer `enabled_models`, balance and status controls, password reset, Relay API key rotation, upstream key update, and audit review.
- Runtime architecture: FastAPI remains the control plane; Go owns the selected customer hot paths; Caddy routes only explicit hot paths to Go.
- Video delivery: BytePlus URL hidden from customer JSON, browser-visible responses, redirects, frontend state, and logs.
- NSFW operation: access is represented by enabled model IDs and operator-only upstream profile notes. Model IDs default to native BytePlus API IDs; optional admin aliases are visible only when configured and explicitly selected for that customer. Relay does not add prompt/content censorship and does not add an SFW/NSFW customer toggle.

Out of scope for alpha1:

- A public NSFW customer guide.
- A real-person authorization workflow beyond existing asset ownership and face-asset safety checks.
- Horizontal Go runtime scaling while SQLite is the database.
- Permanent local storage of new generated MP4 files.

## Local Automated Evidence

Prefer the local gate before any deployment claim:

```bash
python deploy/alpha1_local_gate.py \
  --out-dir alpha1-local-gate/$(date -u +%Y%m%dT%H%M%SZ)
python deploy/alpha1_verify_evidence.py \
  --local-gate-manifest alpha1-local-gate/<timestamp>/local-gate-manifest.json
```

This writes `local-gate-manifest.json`, command outputs, and `alpha1-local-acceptance.json`. The manifest must show `"status": "passed"` and `"local_acceptance_acceptable": true`.
It must include the `local_gate_manifest_verify` command label, proving the local gate manifest was re-verified by `alpha1_verify_evidence.py`.
It must include `python_unittest` output markers for malformed Authorization handling and delete/cancel refund idempotency, plus `go_test` output markers for the matching Go runtime malformed Authorization cases.

Run these before any deployment claim:

```bash
python -m py_compile relay_server.py create_asset_white_label.py deploy/alpha1_preflight.py deploy/alpha1_probe.py deploy/alpha1_verify_evidence.py deploy/alpha1_collect_evidence.py deploy/alpha1_completion_audit.py deploy/alpha1_worktree_check.py
python -m unittest discover -s tests -p "test_*.py" -v
(cd runtime-go && go test -v ./...)
(cd runtime-go && go build ./...)
docker compose -f docker-compose.relay.yml config
python deploy/alpha1_preflight.py --skip-db
```

Archive the local end-to-end acceptance smoke as structured JSON:

```bash
ALPHA1_LIVE_SMOKE_EVIDENCE_JSON=alpha1-local-acceptance.json \
python -m unittest tests.test_alpha1_live_smoke.Alpha1LiveSmokeTests.test_fastapi_control_plane_and_go_runtime_acceptance_path -v
python deploy/alpha1_verify_evidence.py \
  --local-only \
  --local-acceptance-json alpha1-local-acceptance.json
```

This release JSON must include `browser_network_uses_relay_video_url=true`; install Playwright Chromium before generating it if the browser check is not available.
It must include `process_logs_hide_upstream_urls_and_secrets=true`, proving relay/runtime process logs do not expose upstream video URLs or customer/upstream keys.
It must also include `runtime_paths` proving the customer hot paths reached Go and `relay_paths` proving admin/auth/account/audit paths stayed on FastAPI.
It must include `runtime_estimate_uses_customer_multiplier=true`, proving `POST /v1/videos/estimate` reached Go, applied the customer multiplier, and did not submit upstream work.
It must include `admin_model_list_default_and_empty_are_distinct=true`, proving admin can distinguish default model access from an explicit empty model list for a customer.
It must include `admin_model_options_include_native_and_alias_choices=true`, proving the admin model picker can show native BytePlus model IDs and configured aliases while aliases remain opt-in.
It must include runtime cookie/CSRF evidence fields: `runtime_cookie_estimate_accepts_session=true` and `runtime_cookie_create_rejects_cross_site_before_upstream=true`.
It must include `runtime_prepare_helper_rejects_unowned_asset_before_upstream=true`, proving Go create delegates asset ownership checks to FastAPI before upstream submission.
It must include `runtime_prepare_failure_refunds_reserved_balance=true`, proving a FastAPI content-preparation failure does not charge the customer.
It must include `runtime_upstream_error_refunds_reserved_balance=true`, proving an upstream task-creation error does not charge the customer and returns only safe upstream metadata.
It must include `runtime_local_task_write_failure_cancels_upstream_and_refunds=true`, proving a post-upstream local task-recording failure attempts upstream cancellation and refunds the customer.
It must include `runtime_create_rejects_insufficient_balance_before_prepare=true`, proving Go create rejects insufficient balance before FastAPI content preparation and upstream submission.
It must include `runtime_terminal_refresh_settles_once_and_refunds_hold=true`, proving terminal refresh refunds hold-minus-actual once and does not double-refund.
It must include `runtime_settlement_uses_task_multiplier_snapshot=true`, proving an existing task settles with its task multiplier snapshot after the customer's current multiplier changes.
It must include `runtime_failed_terminal_refresh_refunds_hold_once=true`, proving a failed terminal refresh refunds the full hold once and does not expose a video URL.
It must include `fastapi_fallback_upstream_error_refunds_reserved_balance=true`, proving the still-reachable FastAPI fallback create path refunds on upstream task-creation error.
It must include `fastapi_fallback_local_task_write_failure_cancels_upstream_and_refunds=true`, proving the still-reachable FastAPI fallback create path cancels upstream and refunds on local task-recording failure.
It must include password-change evidence fields: `customer_password_reuse_rejected=true`, `cookie_password_change_rejects_cross_site_origin=true`, `cookie_password_change_revokes_other_sessions=true`, `customer_old_password_rejected=true`, `customer_new_password_login_works=true`, and `stale_session_revoked_after_password_change=true`.
It must include admin reset/rotation evidence fields: `admin_reset_customer_password=true`, `admin_reset_password_revoked_customer_session=true`, `admin_rotated_customer_relay_key=true`, `old_customer_key_failed_after_admin_rotation=true`, `admin_api_key_rotation_revoked_customer_session=true`, and `admin_rotated_customer_key_works=true`.
It must include `customer_account_surfaces_mask_relay_api_key=true`, proving `/v1/me` returns only `api_key_masked` after customer key rotation and never re-exposes the full Relay API key.
It must include `relay_api_keys_are_server_generated=true`, proving customer creation, customer self-rotation, and admin rotation all use server-generated Relay API keys. Unit tests also prove manual customer key assignment is rejected.
It must include `prompt_text_passed_without_relay_censorship=true`, proving the signed-customer prompt text reached the upstream generation payload unchanged.
It must include `model_aliases_are_opt_in_per_customer=true`, proving customers see native BytePlus model IDs by default, aliases stay hidden unless explicitly enabled for that customer, and alias calls forward the native model ID upstream.
It must include `admin_surfaces_have_no_sfw_nsfw_customer_toggle=true`, proving admin API/UI surfaces do not expose a separate SFW/NSFW customer switch.
It must include `customer_surfaces_keep_operator_nsfw_notes_private=true`, proving customer docs/UI/model responses do not expose NSFW endpoint/profile notes, operator prompt recipes, admin keys, or internal asset/moderation settings.
It must include cross-customer isolation evidence fields: `task_list_excludes_other_customers_tasks=true` and `video_content_rejects_other_customer_before_upstream=true`.
It must include `task_list_status_limit_offset_works=true`, proving Go list supports `status`, `limit`, and `offset` without leaking upstream URLs.
It must include `head_content_uses_relay_without_redirect=true`, proving the `HEAD /v1/videos/{id}/content` playback metadata path stays on Relay and does not redirect or leak BytePlus URLs.
External probe JSON must include `content.head_location_present=false`, `content.range_location_present=false`, and `content.range_content_range_present=true`; release verification rejects these fields when missing or contradictory.
It must include admin credential masking evidence fields: `admin_user_list_masks_customer_and_upstream_keys=true` and `admin_user_detail_masks_customer_and_upstream_keys=true`.
It must include `audit_actions` with the expected multiplier, model-list, balance, status, upstream-key, admin password reset, admin customer-key rotation, customer password-change, and customer Relay-key-rotation audit events: `admin_changed_price_multiplier`, `admin_changed_enabled_models`, `admin_changed_balance`, `admin_changed_status`, `admin_changed_upstream_key`, `admin_reset_password`, `admin_rotated_customer_api_key`, `customer_password_changed`, and `customer_api_key_rotated`.

Compile the inline static scripts in `static/app.html` and `static/admin.html` before release:

```powershell
@'
const fs = require('fs');
const vm = require('vm');
for (const file of ['static/app.html', 'static/admin.html']) {
  const html = fs.readFileSync(file, 'utf8');
  const scripts = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)].map((m) => m[1]);
  scripts.forEach((code, index) => new vm.Script(code, { filename: `${file}#script${index + 1}` }));
  console.log(`${file}: ${scripts.length} inline scripts compiled`);
}
'@ | node
```

## External Deployment Evidence

Prefer the collector on the deployment host or against the real production/staging domain:

Final external evidence must use an HTTPS Relay bare origin, such as `https://video.customer-domain.com`. `http://`, `localhost`, `127.0.0.1`, tunnel URLs, example domains, embedded username/password, and URLs with a path, query, or fragment are rehearsal evidence only, not release evidence.

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

The collector writes `manifest.json` and runs preflight, Caddy validation, read-only probe, browser screenshot capture, evidence verification, and completion audit. If you need to run each command manually:
The saved `alpha1-browser-video.png` must be a valid PNG screenshot with dimensions at least `320x180`; the verifier rejects empty files, non-PNG placeholders, and tiny placeholder images.

The collector also records stable `commands[].label` values in `manifest.json`: `deploy_preflight`, `caddy_validate`, `external_probe`, `evidence_verify`, and `completion_audit`. Use these labels to identify which release step failed before inspecting the corresponding output file.
The collector must copy the full local gate directory, not only `local-gate-manifest.json`, so archived `python_unittest` and `go_test` outputs remain reviewable. Final verification rejects a copied local gate manifest if its command outputs or local acceptance artifact are not archived beside that manifest.
The copied local gate manifest must still include `local_gate_manifest_verify`; otherwise the final verifier rejects the external evidence directory.
The local gate `diff_check` runs `alpha1_worktree_check.py`, which checks unstaged diffs, staged diffs, and untracked text files so newly added alpha1 scripts/docs cannot bypass whitespace and final-newline checks.
When `alpha1_verify_evidence.py --manifest ...` re-checks a saved archive, evidence directory artifacts are preferred over original absolute paths so the copied directory can be reviewed independently.

Do not use `--no-browser` for final release evidence. That mode is only for temporary server-side checks; the manifest is marked `release_acceptable=false` because browser screenshot/network evidence is missing.

After the collector finishes, you can re-check the saved evidence directory with:

```bash
python deploy/alpha1_verify_evidence.py \
  --manifest alpha1-evidence/<timestamp>/manifest.json
python deploy/alpha1_completion_audit.py \
  --manifest alpha1-evidence/<timestamp>/manifest.json \
  --summary-json alpha1-completion-summary.json
```

The completion audit must print `Alpha1 completion audit passed`, and `alpha1-completion-summary.json` must include `"status": "complete"`, `"release_complete": true`, `"failures": []`, and `checks.external_manifest.status="passed"`. When the audit compares a provided local gate manifest, the summary must also include `"local_gate_match": true`. If only the local gate has passed, this audit fails with `external deployment evidence missing`; that is expected and means alpha1 is locally verified but not externally complete.
The saved `completion_audit` command in `manifest.json` must include `--manifest` pointing to that same manifest and `--summary-json` pointing to `artifacts.completion_summary`; final verification rejects mismatched completion-audit inputs.
When both `--manifest` and `--local-gate-manifest` are provided, the audit must record `"local_gate_match": true`. It accepts the same path or a byte-identical copied local gate manifest produced by the collector. If it reports `local gate manifest does not match external manifest artifact`, treat the external evidence set as invalid and rerun collection with the correct local gate output.

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

Required saved artifacts:

- `alpha1-local-acceptance.json` from the local FastAPI + Go + Caddy-equivalent smoke.
- `local-gate-manifest.json` from the required local gate.
- `alpha1-probe-evidence.json`
- `alpha1-probe-output.txt`
- `alpha1-browser-video.png`
- `alpha1-preflight-output.txt`
- `caddy-validate-output.txt`
- `alpha1-completion-output.txt`
- `manifest.json` if `alpha1_collect_evidence.py` was used
- `alpha1-completion-summary.json` from `alpha1_completion_audit.py`
- Deployment terminal output showing successful Caddy validation.
- Deployment terminal output showing `alpha1_preflight.py` passed with the real Caddyfile.

## Evidence Matrix

| Area | Requirement | Evidence |
|---|---|---|
| Customer | Customer password change | Unit tests plus customer UI path; same-as-current password is rejected, old password is rejected, new password login works, and stale sessions are revoked. |
| Customer | Customer Relay API key rotation | Unit tests plus account UI; new key shown once, old key fails, account surfaces return only the masked key afterward, and Relay keys are server-generated. |
| Customer | Runtime cookie auth and CSRF | Go accepts `relay_session` for estimate and rejects cross-site cookie create before upstream submission. |
| Customer | Customer model list | `/v1/models` returns only enabled model IDs for the authenticated customer. |
| Customer | Default vs empty model list | Local smoke proves `enabled_models=NULL` uses default access and `enabled_models=[]` blocks all models before upstream. |
| Customer | Optional model aliases | Local smoke proves admin model options show native IDs and configured aliases; customer defaults still use native IDs, aliases are visible only when enabled for that customer, and alias calls forward native BytePlus model IDs upstream. |
| Customer | Disabled model rejection | Disabled model returns `model_not_enabled` before upstream submission. |
| Customer | Asset ownership guard | Local smoke proves Go create delegates to FastAPI helper and rejects another customer's `asset://` material before upstream submission. |
| Customer | Prepare failure refund | Local smoke proves content-preparation failure refunds the reserved balance and does not submit upstream work. |
| Customer | Upstream failure refund | Local smoke proves upstream task-creation failure refunds the reserved balance and exposes only a safe request id. |
| Customer | Local task write failure safety | Local smoke proves post-upstream local write failure attempts upstream cancellation and refunds the reserved balance. |
| Customer | Insufficient balance ordering | Local smoke proves Go create rejects insufficient balance before content preparation and upstream submission. |
| Customer | Relay-domain video playback | `alpha1_probe.py` and browser screenshot show customer-visible playback through Relay URLs. |
| Customer | Terminal settlement | Local smoke proves terminal refresh refunds hold-minus-actual once and returns only a Relay-domain video URL. |
| Customer | Failed terminal settlement | Local smoke proves failed terminal refresh refunds the full hold once and returns no video URL. |
| Customer | Cancellation refund idempotency | Unit tests prove direct delete/cancel refunds the reserved balance only when the task transitions from `settled=0` to `settled=1`. |
| Customer | Content refresh before playback | Unit tests prove `GET` and `HEAD` content playback refresh an unsettled task before returning `not_ready`, then proxy only Relay-owned video responses. |
| Customer | Malformed Authorization handling | Unit tests prove malformed `Authorization` cannot bypass cookie CSRF checks or fall back to public `/v1/models` responses. |
| Customer | FastAPI fallback upstream failure | Local smoke proves direct FastAPI fallback create refunds on upstream task-creation failure and exposes only a safe request id. |
| Customer | FastAPI fallback local write failure | Local smoke proves direct FastAPI fallback create cancels upstream and refunds on local task-recording failure. |
| Customer | Task list pagination | Local smoke proves `GET /v1/videos?status=succeeded&limit=1&offset=1` returns a paged, customer-scoped, Relay-safe result. |
| Admin | Customer multiplier | Unit tests cover estimate/create/settle with `price_multiplier` values such as `1.0`, `1.2`, and `1.3`; local smoke proves Go estimate applies the selected multiplier. |
| Admin | Historical price snapshot | Unit tests and local smoke prove changing multiplier after creation does not alter settlement for an existing task. |
| Admin | Enabled model management | Admin UI/API can set default list, selected list, and explicit empty list. |
| Admin | Credential masking | Admin list/detail never return full Relay API keys, BytePlus keys, passwords, session tokens, or upstream URLs. |
| Admin | Customer key rotation UI | Admin customer detail can rotate a customer's Relay API key, shows the new key once, then returns to masked key display. |
| Admin | Audit trail | Audit events cover multiplier, model list, password, Relay key, balance, status, and upstream key changes. |
| Policy | No Relay content censorship | Tests prove prompt text is not blocked by Relay; Relay validates only structure, permissions, model capability, and asset ownership. |
| Policy | No SFW/NSFW toggle | Local smoke and docs tests prove admin UI/API/docs do not contain a separate customer SFW/NSFW enable flag. |
| Policy | NSFW operating support | Local smoke proves customer docs/UI/model responses keep upstream/profile notes private; customer access is controlled only through enabled models. |
| Video | No local video storage | New successful tasks in `proxy_only` mode do not create `VIDEO_DIR/{task}.mp4`. |
| Video | BytePlus URL hidden | API JSON, `Location` headers, relay/runtime process logs, and browser-visible network records contain only Relay-owned URLs and no customer/upstream keys. |
| Video | HEAD and Range playback | Video content endpoint supports `HEAD` and `Range`; `HEAD` returns relay-owned metadata without redirect and `Range` returns `206` with `Content-Range`. |
| Customer isolation | Task ownership | Two-customer local evidence proves task lists exclude other customers' tasks and content access is rejected before upstream fetch. |
| Architecture | Runtime split | Go serves selected customer hot paths, including estimate; FastAPI keeps admin/auth/uploads/docs/static UI and the runtime content-preparation helper. |
| Architecture | Internal prepare helper | Go create path delegates material preparation to FastAPI and preserves existing real-person/material checks without adding censorship. |
| Architecture | Caddy route safety | Caddy uses explicit route matchers; no broad `/v1/videos*` wildcard sends admin/auth/upload/docs traffic to Go. |
| Architecture | SQLite boundary | FastAPI and Go use WAL plus busy timeout; only one Go writer is allowed while SQLite remains in use. |
| Architecture | Scale path | Move to Postgres before horizontal Go runtime scaling or audit-grade billing scale. |

## Final Manual Walkthrough

1. Admin creates a test customer.
2. Admin sets `price_multiplier=1.2`.
3. Admin enables only selected models.
4. Customer tries to reuse the current password and is rejected; then changes password, old password is rejected, new password logs in, and an old session cookie no longer works.
5. Customer rotates Relay API key.
6. Old Relay API key fails.
7. Customer sees only enabled models.
8. Disabled model fails before upstream.
9. Customer submits an enabled native `content[]` generation.
10. Result JSON returns only Relay-domain `video_url`.
11. Video plays through `/v1/videos/{id}/content`.
12. Browser network panel and relay/runtime process logs never show the BytePlus video URL or customer/upstream keys.
13. No new local MP4 appears under `VIDEO_DIR`.
14. Admin resets customer password and rotates the customer's Relay API key; stale customer session/key evidence is captured, including session revocation after admin key rotation.
15. Admin audit shows multiplier/model/key/password/balance/status/upstream-key changes without secrets.

## Do Not Mark Complete

Do not mark complete if any of these are true:

- `alpha1-probe-evidence.json` is missing, failed, or shows a BytePlus URL leak.
- `alpha1-browser-video.png` is missing for the browser playback proof.
- `caddy validate --config /etc/caddy/Caddyfile` has not passed on the deployment host.
- `alpha1_preflight.py --caddyfile /etc/caddy/Caddyfile` has not passed on the deployment host.
- `alpha1_verify_evidence.py` has not passed against the saved external evidence files.
- `alpha1_completion_audit.py --manifest ...` has not printed `Alpha1 completion audit passed`.
- Customer UI or admin UI exposes upstream URLs, full keys, endpoint/profile names, filter settings, operator notes, or an SFW/NSFW customer toggle.
- The Go runtime is horizontally scaled while the deployment is still on SQLite.

