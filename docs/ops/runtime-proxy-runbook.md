# Runtime Proxy Runbook

## Scope

Generated videos should be served through Relay-owned URLs. Relay must not expose BytePlus temporary video URLs to customers.

## Current Alpha1 Behavior

- `VIDEO_PERSIST_MODE=proxy_only` is the default.
- New successful tasks should not create local MP4 files under `VIDEO_DIR`.
- Existing `local_video_path` rows remain readable for backward compatibility.
- `GET /v1/videos/{id}/content` streams bytes through Relay.
- `HEAD /v1/videos/{id}/content` returns proxy metadata without a body.
- Content endpoints should refresh an unsettled task before returning `not_ready`, then stream only through the Relay URL if the upstream task has just completed.
- Range requests are forwarded upstream and must return `206 Partial Content` with `Content-Range` for release acceptance.
- Upstream video `4xx/5xx` responses, redirects, or Range requests that fall back to `200 OK` must be converted to Relay-owned safe errors. Do not stream upstream error bodies or copy upstream `Location` headers to customers.
- Release probe evidence records `content.head_location_present=false`, `content.range_location_present=false`, and `content.range_content_range_present=true`.

## Go Runtime MVP Routing

The first Go runtime stage owns only:

- `GET /v1/models`
- `POST /v1/videos/estimate`
- `POST /v1/videos`
- `GET /v1/videos`
- `GET /v1/videos/{id}`
- `GET /v1/videos/{id}/content`
- `HEAD /v1/videos/{id}/content`

Auth compatibility:

- `GET /v1/models` may be called without auth and should return the public/default model list.
- If the customer is authenticated, Go must accept either `Authorization: Bearer <relay_api_key>` or the existing `relay_session` cookie.
- `POST /v1/videos`, `POST /v1/videos/estimate`, and video content endpoints require a valid Relay API key or `relay_session` cookie.
- If an `Authorization` header is present but malformed or invalid, Go and any reachable FastAPI fallback must fail authentication instead of falling back to a session cookie or the unauthenticated public model list.
- Cookie-authenticated `POST /v1/videos` must require same-origin `Origin` or `Referer`; Bearer API key requests are not subject to this CSRF check.
- Video content endpoints must check customer ownership before proxying.
- `POST /v1/videos` delegates content preparation to FastAPI through `/internal/runtime/prepare-video-content` using `X-Runtime-Token`.
- The internal helper performs real-person asset materialization and existing asset ownership/face-asset checks only; it must not add prompt/content censorship.

Keep these on FastAPI for now:

- uploads, auth, admin, docs, and account APIs

Required environment:

- `RUNTIME_INTERNAL_TOKEN` must be set to a random secret shared by FastAPI and Go.
- `CONTROL_PLANE_BASE_URL` must point from Go to FastAPI, for example `http://seedance-relay:8002` in Docker Compose.
- Caddy must use explicit method/path matchers for `/v1/videos`, `/v1/videos/{id}`, and `/v1/videos/{id}/content`.

## SQLite Boundary

Alpha1 may use SQLite only while deployment stays small and controlled:

- FastAPI and Go must both open SQLite with WAL mode and a busy timeout.
- Run only one Go runtime writer process against the SQLite file.
- Do not scale `seedance-runtime` horizontally while using SQLite.
- Keep task settlement ownership in one runtime path so a task is settled once.

Move to Postgres before production scale when:

- multiple Go runtime replicas are needed,
- customer traffic grows enough to cause write contention,
- admin/runtime writes conflict,
- audit-grade billing history is required.

## Future Go Runtime

The Go runtime must preserve the same behavior before it takes over the full `/v1/videos*` path.

Required checks:

- customer ownership is verified before streaming,
- create calls prepare content through the FastAPI helper before upstream submission,
- task refresh, terminal settlement, and delete/cancel refund held balance consistently,
- terminal settlement and direct delete/cancel refund only when the task row transitions from `settled=0` to `settled=1`,
- `Range` is forwarded,
- `Content-Range`, `Content-Length`, `Accept-Ranges`, and `Content-Type` are preserved,
- upstream video error bodies and redirect headers are not exposed,
- BytePlus URLs never appear in customer JSON, `Location` headers, frontend state, access logs, or browser-visible errors.
