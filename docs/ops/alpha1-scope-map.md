# Alpha1 User/Admin/Architecture Scope Map

This scope map is the operator handoff for the alpha1 SPEC. It keeps the current release bounded while recording the next architecture improvements.

## Customer Side

Included now:

- Login with email/password session.
- Change own password with current-password verification.
- Rotate own server-generated Relay API key.
- View masked Relay API key metadata, balance, multiplier, and enabled model IDs.
- Call only the model IDs enabled for that customer. Native BytePlus API model IDs are the default; configured aliases are allowed only when explicitly selected for that customer.
- Submit native `content[]` video requests without Relay prompt/content censorship.
- Receive Relay-domain `video_url` values and play video through `/v1/videos/{id}/content`.

Not included now:

- Customer-managed upstream BytePlus endpoint/profile selection.
- Customer-visible upstream BytePlus storage URLs.
- Public NSFW prompt recipes or endpoint/profile notes.

## Admin Side

Included now:

- Create/update customers.
- Set per-customer `price_multiplier`.
- Set per-customer `enabled_models`, including default list, selected list, or explicit empty list.
- Reset customer password.
- Rotate customer Relay API key.
- Configure customer upstream generation key when needed.
- Recharge, adjust status, and review audit events.
- View masked credentials only; full Relay keys are shown once at create/rotation.

Not included now:

- A separate SFW/NSFW customer toggle.
- Manual admin-selected Relay API key values.
- Customer content approval workflow.

## Runtime Architecture

Alpha1 split:

- FastAPI keeps admin, auth, upload, docs, account UI, static UI, and internal content preparation.
- Go owns selected customer hot paths: models, estimate, create, list/detail video, and video content proxy.
- Caddy routes only explicit customer hot paths to Go.
- `VIDEO_PERSIST_MODE=proxy_only` keeps new generated videos out of local MP4 storage.
- SQLite is allowed only for a small alpha1 deployment with one Go runtime writer.

## Improvement Space After Alpha1

Do later, after alpha1 evidence passes:

- Move from SQLite to Postgres before horizontal Go runtime scaling or audit-grade billing scale.
- Add queue/worker ownership for task refresh and settlement if traffic grows.
- Add dashboard-level observability for upstream failures, request IDs, latency, and customer balance changes.
- Add deploy evidence retention so `alpha1-probe-evidence.json`, browser screenshot, Caddy validation output, and preflight output are archived per release.
- Add stricter operational alerting around secret rotation, upstream error rate, failed login bursts, and proxy content failures.
