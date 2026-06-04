# Alpha1 Evidence Runbook

Use this when alpha1 is deployed to a real staging or production domain and you need final release evidence. This runbook is the shortest acceptable path; the full gate is in `docs/ops/alpha1-release-checklist.md`.

## Inputs

You need:

- a real Relay domain, for example `https://video.customer-domain.com`,
- one existing signed customer Relay API key,
- one existing `succeeded` video id owned by that customer,
- access to the deployment host Caddyfile, normally `/etc/caddy/Caddyfile`.

The final `--base-url` must be an external HTTPS bare origin such as `https://video.customer-domain.com`. Do not use `http://`, `localhost`, `127.0.0.1`, a tunnel URL, an example domain, embedded username/password, or a URL with a path, query, or fragment as final release evidence.

Do not paste API keys into shared docs, screenshots, tickets, or chat. Prefer an environment variable or a local key file.

Before collecting final external evidence, run the local gate:

```bash
python deploy/alpha1_local_gate.py \
  --out-dir alpha1-local-gate/$(date -u +%Y%m%dT%H%M%SZ)
python deploy/alpha1_verify_evidence.py \
  --local-gate-manifest alpha1-local-gate/<timestamp>/local-gate-manifest.json
```

The local gate writes `local-gate-manifest.json` plus `alpha1-local-acceptance.json`. The manifest must show `"status": "passed"` and `"local_acceptance_acceptable": true`.
It must also include the `local_gate_manifest_verify` command label, proving the local gate manifest was re-verified by `alpha1_verify_evidence.py`.
The `python_unittest` output must include the key unit-test markers for malformed Authorization handling and delete/cancel refund idempotency. The `go_test` output must be verbose and include the matching Go runtime malformed Authorization markers.

If you need the shorter manual version, generate and verify the local acceptance JSON:

```bash
ALPHA1_LIVE_SMOKE_EVIDENCE_JSON=alpha1-local-acceptance.json \
python -m unittest tests.test_alpha1_live_smoke.Alpha1LiveSmokeTests.test_fastapi_control_plane_and_go_runtime_acceptance_path -v
python deploy/alpha1_verify_evidence.py \
  --local-only \
  --local-acceptance-json alpha1-local-acceptance.json
```

The local acceptance JSON must prove the password flow, not only the password-change endpoint call: `customer_password_reuse_rejected=true`, `cookie_password_change_rejects_cross_site_origin=true`, `cookie_password_change_revokes_other_sessions=true`, `customer_old_password_rejected=true`, `customer_new_password_login_works=true`, and `stale_session_revoked_after_password_change=true`.
It must prove the Go runtime estimate path: `runtime_estimate_uses_customer_multiplier=true`.
It must prove admin model-list semantics preserve default access versus explicit empty-list access: `admin_model_list_default_and_empty_are_distinct=true`.
It must prove the admin model picker can show native IDs and configured aliases without making aliases the default: `admin_model_options_include_native_and_alias_choices=true`.
It must prove optional model aliases are customer opt-in only: `model_aliases_are_opt_in_per_customer=true`.
It must prove admin surfaces do not expose a separate SFW/NSFW customer switch: `admin_surfaces_have_no_sfw_nsfw_customer_toggle=true`.
It must prove runtime cookie auth and CSRF safety: `runtime_cookie_estimate_accepts_session=true` and `runtime_cookie_create_rejects_cross_site_before_upstream=true`.
It must prove the Go create path delegates asset ownership checks to FastAPI before upstream submission: `runtime_prepare_helper_rejects_unowned_asset_before_upstream=true`.
It must prove a FastAPI content-preparation failure does not charge the customer: `runtime_prepare_failure_refunds_reserved_balance=true`.
It must prove an upstream task-creation error does not charge the customer and exposes only safe upstream metadata: `runtime_upstream_error_refunds_reserved_balance=true`.
It must prove a post-upstream local task-recording failure attempts upstream cancellation and refunds the customer: `runtime_local_task_write_failure_cancels_upstream_and_refunds=true`.
It must prove the Go create path rejects insufficient balance before FastAPI content preparation and upstream submission: `runtime_create_rejects_insufficient_balance_before_prepare=true`.
It must prove terminal refresh refunds hold-minus-actual exactly once: `runtime_terminal_refresh_settles_once_and_refunds_hold=true`.
It must prove settlement uses the task multiplier snapshot after the customer's current multiplier changes: `runtime_settlement_uses_task_multiplier_snapshot=true`.
It must prove failed terminal refresh refunds the full hold exactly once and returns no video URL: `runtime_failed_terminal_refresh_refunds_hold_once=true`.
It must prove the still-reachable FastAPI fallback create path refunds on upstream task-creation error: `fastapi_fallback_upstream_error_refunds_reserved_balance=true`.
It must prove the still-reachable FastAPI fallback create path cancels upstream and refunds on local task-recording failure: `fastapi_fallback_local_task_write_failure_cancels_upstream_and_refunds=true`.
It must prove admin account recovery/key control as well: `admin_reset_customer_password=true`, `admin_reset_password_revoked_customer_session=true`, `admin_rotated_customer_relay_key=true`, `old_customer_key_failed_after_admin_rotation=true`, `admin_api_key_rotation_revoked_customer_session=true`, and `admin_rotated_customer_key_works=true`.
It must prove customer account surfaces mask Relay API keys after rotation: `customer_account_surfaces_mask_relay_api_key=true`.
It must prove Relay API keys are server-generated at create/rotation time: `relay_api_keys_are_server_generated=true`.
It must prove Relay did not censor the signed-customer prompt: `prompt_text_passed_without_relay_censorship=true`.
It must prove customer-facing surfaces keep operator NSFW notes private: `customer_surfaces_keep_operator_nsfw_notes_private=true`.
It must prove cross-customer task isolation with two customers: `task_list_excludes_other_customers_tasks=true` and `video_content_rejects_other_customer_before_upstream=true`.
It must prove Go list pagination and status filtering: `task_list_status_limit_offset_works=true`.
It must prove Relay-owned `HEAD` playback metadata: `head_content_uses_relay_without_redirect=true`.
It must prove relay/runtime process logs do not expose upstream video URLs or customer/upstream keys: `process_logs_hide_upstream_urls_and_secrets=true`.
It must prove admin list/detail credential masking: `admin_user_list_masks_customer_and_upstream_keys=true` and `admin_user_detail_masks_customer_and_upstream_keys=true`.
It must also include admin audit actions for multiplier, model list, balance, status, and upstream-key changes: `admin_changed_price_multiplier`, `admin_changed_enabled_models`, `admin_changed_balance`, `admin_changed_status`, and `admin_changed_upstream_key`.

## One Command

Run this from the deployed release directory:

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

The collector writes:

- `manifest.json`
- `alpha1-local-acceptance.json`
- `alpha1-preflight-output.txt`
- `caddy-validate-output.txt`
- `alpha1-probe-output.txt`
- `alpha1-probe-evidence.json`
- `alpha1-browser-video.png`
- `alpha1-verify-output.txt`
- `alpha1-completion-output.txt`
- `alpha1-completion-summary.json`

`alpha1-browser-video.png` must be a valid PNG screenshot with dimensions at least `320x180`; empty files, non-PNG placeholders, and tiny placeholder images are not release evidence.

The collector redacts known API-key, session, upstream-key, and provider-domain patterns from command stdout before archiving. The structured probe JSON is still verified separately and must not contain upstream URLs, Relay keys, BytePlus keys, session cookies, or provider markers.

In `manifest.json`, `commands[].label` must show the release step that produced each output:

- `deploy_preflight`
- `caddy_validate`
- `external_probe`
- `evidence_verify`
- `completion_audit`

The collector copies the full local gate directory, not only `local-gate-manifest.json`, so archived `python_unittest` and `go_test` outputs remain reviewable. Final verification rejects a copied local gate manifest if its command outputs or local acceptance artifact are not archived beside that manifest.
The copied local gate manifest inside the evidence directory must still include `local_gate_manifest_verify`; final verification rejects local gate manifests that do not prove their own self-check.
The local gate `diff_check` runs `alpha1_worktree_check.py`, which checks unstaged diffs, staged diffs, and untracked text files so newly added alpha1 scripts/docs cannot bypass whitespace and final-newline checks.
When `alpha1_verify_evidence.py --manifest ...` re-checks a saved archive, evidence directory artifacts are preferred over original absolute paths so the copied directory can be reviewed independently.

## Pass Criteria

Open `manifest.json` and confirm:

```json
{
  "status": "passed",
  "release_acceptable": true,
  "browser_required": true
}
```

Then re-check the saved evidence directory:

```bash
python deploy/alpha1_verify_evidence.py \
  --manifest alpha1-evidence/<timestamp>/manifest.json
python deploy/alpha1_completion_audit.py \
  --manifest alpha1-evidence/<timestamp>/manifest.json \
  --summary-json alpha1-completion-summary.json
```

The command must print `Evidence verification passed`.
For manual explicit artifact verification, pass both `--probe-json alpha1-probe-evidence.json` and `--probe-output alpha1-probe-output.txt` so the structured probe result and the probe command output are checked together.
The completion audit must print `Alpha1 completion audit passed`, and `alpha1-completion-summary.json` must include `"status": "complete"`, `"release_complete": true`, `"failures": []`, and `checks.external_manifest.status="passed"`. When the audit compares a provided local gate manifest, the summary must also include `"local_gate_match": true`. If it says `external deployment evidence missing`, you only have local evidence and must collect final external evidence before marking alpha1 complete.
The saved `completion_audit` command in `manifest.json` must include `--manifest` pointing to that same manifest and `--summary-json` pointing to `artifacts.completion_summary`; final verification rejects mismatched completion-audit inputs.
When both `--manifest` and `--local-gate-manifest` are provided, the completion audit must record `"local_gate_match": true`. It accepts the same path or a byte-identical copied local gate manifest produced by the collector. If it reports `local gate manifest does not match external manifest artifact`, rerun evidence collection with the correct local gate output before release.

## Not Final Evidence

`--dry-run` and `--no-browser` are useful for rehearsal only. They are not final release evidence.

- `--dry-run` writes planned commands but does not prove the deployment works.
- `--no-browser` skips browser screenshot/network proof and marks `release_acceptable=false`.

Do not mark alpha1 complete with either mode.

## If It Fails

Check the output file named in the failure:

- `deploy_preflight`: fix `.env.relay`, compose, Caddy routing, SQLite mode, or deployment secrets.
- `caddy_validate`: run `caddy validate --config /etc/caddy/Caddyfile` on the deployment host and fix syntax/routing.
- `external_probe`: verify the customer key, domain, succeeded video id, and Relay-domain video proxy behavior.
- `evidence_verify`: inspect the listed missing artifact or sensitive marker leak.

Do not share artifacts publicly unless API keys, URLs, and customer data have been reviewed and redacted.

