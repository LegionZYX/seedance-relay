# Alpha2 Customer Endpoint Admin Spec

## Context

Alpha2 now supports the operating model where a customer can have an isolated
BytePlus project, endpoint, endpoint API key, and ModelArk asset group while the
customer only sees Relay credentials.

The production Peter test proved the path:

```text
Relay customer key
 -> Relay account
 -> users.note.byteplus_endpoint_id
 -> users.byteplus_api_key as endpoint API key
 -> BytePlus ap-southeast data plane
 -> users.note.modelark_asset_group_id for asset registration
```

The remaining gap is productizing this into the admin surface so an operator can
create, inspect, rotate, and troubleshoot a customer's isolated upstream setup
without editing JSON notes or SSH files by hand.

## Goals

- Add a controlled switch for endpoint API key auto-rotation.
- Add admin UI and admin API support for per-customer BytePlus project,
  endpoint, endpoint key, endpoint key expiry, and asset group metadata.
- Let admin assist customers with password reset in a clean support workflow.
- Keep BytePlus keys, IAM AK/SK, upstream URLs, and endpoint internals
  operator-only and never visible to customers.
- Keep the existing customer Relay API behavior stable.

## Non-Goals

- A public customer UI for choosing BytePlus endpoint, project, moderation mode,
  asset group, or safety/filter switches.
- Permanent endpoint API keys. BytePlus `GetApiKey` keys expire; Relay should
  rotate them.
- Storing one IAM AK/SK pair per customer.
- Exposing BytePlus control-plane errors or raw upstream responses to customers.

## Current Implementation Status

Already present:

- `users.byteplus_api_key` stores the customer-specific upstream generation key.
- `users.note` can store structured JSON such as:

```json
{
  "customer_slug": "peterlv",
  "byteplus_project_name": "peterlv",
  "byteplus_endpoint_id": "ep-20260606113200-7cmmq",
  "byteplus_endpoint_status": "Running",
  "byteplus_endpoint_api_key_expires_at": "1783310874",
  "modelark_asset_group_id": "group-20260606113202-679wh",
  "modelark_asset_group_name": "relay-peterlv-face-assets"
}
```

- Generation uses `byteplus_endpoint_id` from `users.note` as the upstream
  model/endpoint and uses `users.byteplus_api_key` as the endpoint API key.
- Asset registration uses `modelark_asset_group_id` from `users.note` before
  falling back to the platform global asset group.
- Admin API can reset a customer password with `PATCH /admin/users/{id}` and
  `new_password`.
- Admin password reset deletes existing customer sessions.
- Admin UI masks BytePlus key values by default.

Missing:

- No first-class admin form for customer endpoint metadata.
- No auto-rotation job for endpoint API keys.
- No admin button to rotate only a customer's endpoint API key.
- No explicit status badge for endpoint key expiry.
- No admin password reset workflow that generates and shows a temporary
  password once.
- No audit actions dedicated to endpoint setup/rotation.

## Data Model

For alpha2, keep the database schema stable and continue using `users.note` for
operator metadata. Add typed helpers around it instead of asking operators to
edit JSON manually.

Canonical `users.note` keys:

| Key | Type | Meaning |
| --- | --- | --- |
| `upstream_mode` | string | `shared`, `manual_dedicated`, or `auto_dedicated`. |
| `dedicated_endpoint_provisioning_enabled` | bool | Whether Relay may automatically create dedicated BytePlus resources for this customer. |
| `dedicated_endpoint_provisioning_status` | string | `not_started`, `creating_project`, `creating_endpoint`, `creating_asset_group`, `generating_key`, `provisioned`, or `failed`. |
| `dedicated_endpoint_provisioning_error` | string | Sanitized latest provisioning error. |
| `dedicated_endpoint_provisioned_at` | int | Successful provisioning timestamp. |
| `dedicated_endpoint_last_switched_at` | int | Latest switch between shared and dedicated modes. |
| `customer_slug` | string | Stable customer slug, for example `peterlv`. |
| `byteplus_project_name` | string | BytePlus project name for this customer. |
| `byteplus_endpoint_id` | string | Dedicated ModelArk endpoint id. |
| `byteplus_endpoint_status` | string | Last known endpoint status, for example `Running`. |
| `byteplus_endpoint_api_key_expires_at` | string/int | Unix expiry for `users.byteplus_api_key` when it is an endpoint key. |
| `byteplus_endpoint_key_rotation_enabled` | bool | Per-customer auto-rotation switch. |
| `byteplus_endpoint_key_last_rotated_at` | int | Last successful rotation Unix timestamp. |
| `byteplus_endpoint_key_next_rotate_at` | int | Planned next rotation timestamp. |
| `byteplus_endpoint_key_rotation_error` | string | Sanitized last rotation error, if any. |
| `modelark_asset_group_id` | string | Dedicated asset group id. |
| `modelark_asset_group_name` | string | Dedicated asset group display name. |

Do not store raw IAM AK/SK in `users.note`.

Customer mode rules:

- `shared`: normal/test customer mode. Runtime uses the platform global endpoint
  and global asset group.
- `manual_dedicated`: admin manually enters endpoint and asset group metadata.
- `auto_dedicated`: admin enables provisioning; Relay creates or reuses project,
  endpoint, asset group, and endpoint API key.
- Switching back to `shared` stops using dedicated resources but does not delete
  BytePlus resources.
- Switching to dedicated again reuses existing endpoint and asset group unless
  admin explicitly chooses recreate.

## Config Switches

Add platform-level environment switches:

```env
ENDPOINT_KEY_ROTATION_ENABLED=false
ENDPOINT_KEY_ROTATION_DAYS_BEFORE_EXPIRY=5
ENDPOINT_KEY_ROTATION_INTERVAL_HOURS=24
ENDPOINT_KEY_DURATION_SECONDS=2592000
ENDPOINT_KEY_ROTATION_DRY_RUN=false
```

Semantics:

- Global switch off means no automatic endpoint key rotation runs.
- Per-customer `byteplus_endpoint_key_rotation_enabled=false` opts that customer
  out even when the global switch is on.
- Missing per-customer switch defaults to false for existing accounts, and true
  for new accounts created with "dedicated endpoint" enabled.
- Dry-run computes candidates and writes audit/log status but does not call
  `GetApiKey` or update `users.byteplus_api_key`.

## Admin API

### Get Customer Upstream Config

```http
GET /admin/users/{user_id}/upstream
```

Response must be secret-safe:

```json
{
  "user_id": "u_xxx",
  "mode": "auto_dedicated",
  "provisioning_enabled": true,
  "provisioning_status": "provisioned",
  "project_name": "peterlv",
  "endpoint_id": "ep-20260606113200-7cmmq",
  "endpoint_status": "Running",
  "endpoint_api_key_masked": "eyJhbGci...w9EsC5Q",
  "endpoint_api_key_expires_at": 1783310874,
  "endpoint_key_rotation_enabled": true,
  "endpoint_key_next_rotate_at": 1782878874,
  "asset_group_id": "group-20260606113202-679wh",
  "asset_group_name": "relay-peterlv-face-assets",
  "warnings": []
}
```

Never return full BytePlus keys.

### Update Customer Upstream Config

```http
PATCH /admin/users/{user_id}/upstream
```

Allowed fields:

```json
{
  "mode": "auto_dedicated",
  "provisioning_enabled": true,
  "customer_slug": "peterlv",
  "project_name": "peterlv",
  "endpoint_id": "ep-xxx",
  "endpoint_status": "Running",
  "asset_group_id": "group-xxx",
  "asset_group_name": "relay-peterlv-face-assets",
  "endpoint_key_rotation_enabled": true,
  "endpoint_api_key": "eyJhbGci..."
}
```

Rules:

- Empty `endpoint_api_key` means keep current key.
- Non-empty `endpoint_api_key` updates `users.byteplus_api_key`.
- Validate endpoint id starts with `ep-`.
- Validate asset group id starts with `group-`.
- Preserve unrelated keys in `users.note`.
- Audit as `admin_changed_customer_upstream_config`.

Mode switch rules:

- Switching to `shared` preserves existing endpoint and asset group metadata but
  runtime must ignore them.
- Switching to `manual_dedicated` requires endpoint id and endpoint key, either
  newly supplied or already stored.
- Switching to `auto_dedicated` reuses existing resources when present; otherwise
  it starts provisioning.

### Provision Customer Dedicated Resources

```http
POST /admin/users/{user_id}/upstream/provision
```

Use this when a normal/test customer becomes important and should receive
isolated BytePlus resources.

Request:

```json
{
  "mode": "auto_dedicated",
  "customer_slug": "peterlv",
  "project_name": "peterlv",
  "endpoint_name": "relay-peterlv-seedance2",
  "asset_group_name": "relay-peterlv-face-assets",
  "endpoint_key_rotation_enabled": true,
  "dry_run": false
}
```

Flow:

1. Validate active customer and slug.
2. Create or reuse BytePlus project.
3. Create or reuse endpoint.
4. Create or reuse asset group.
5. Generate endpoint API key with `GetApiKey`.
6. Store the endpoint key in `users.byteplus_api_key`.
7. Store project, endpoint, asset group, expiry, mode, and provisioning status in
   `users.note`.
8. Audit as `admin_provisioned_customer_dedicated_endpoint`.

`dry_run=true` returns planned names and actions without calling BytePlus or
writing DB state.

### Rotate Customer Endpoint API Key

```http
POST /admin/users/{user_id}/upstream/endpoint-key/rotate
```

Request:

```json
{
  "duration_seconds": 2592000,
  "dry_run": false
}
```

Response:

```json
{
  "ok": true,
  "endpoint_id": "ep-20260606113200-7cmmq",
  "endpoint_api_key_masked": "eyJhbGci...w9EsC5Q",
  "expires_at": 1783310874,
  "rotated_at": 1780718874,
  "shown_once": false
}
```

The full endpoint API key is never shown in the browser by default. If an
operator explicitly asks to reveal it for break-glass support, require a
separate confirmation and never persist it to audit metadata.

### Password Reset Support

Keep the existing backend behavior but add a clearer endpoint:

```http
POST /admin/users/{user_id}/password/reset
```

Request options:

```json
{
  "new_password": "optional-admin-chosen-password",
  "generate": true,
  "force_change_on_next_login": true
}
```

Response:

```json
{
  "ok": true,
  "temporary_password": "shown-once-if-generated",
  "password_changed_at": 1780718874,
  "sessions_revoked": true
}
```

Rules:

- Minimum password length remains 10 characters.
- If `generate=true`, Relay generates a strong temporary password and returns it
  once.
- Existing sessions are revoked.
- Audit as `admin_reset_password`.
- Do not store plaintext password in DB, logs, or audit metadata.
- Optional `force_change_on_next_login` requires adding a future
  `users.must_change_password` column or a `users.note.must_change_password`
  flag. For alpha2, prefer a `users.note` flag to avoid migration risk.

## Admin UI

In the user detail drawer/modal, add two operational sections.

### BytePlus Dedicated Endpoint

Fields:

- Mode: `Global endpoint` / `Dedicated customer endpoint`.
- Customer slug.
- Project name.
- Endpoint ID.
- Endpoint status badge.
- Endpoint API key masked field with "replace" action.
- Endpoint API key expiry badge:
  - green: more than 7 days left,
  - amber: 1 to 7 days left,
  - red: expired or less than 24 hours.
- Auto-rotation toggle.
- Rotate endpoint key now button.
- Asset group ID.
- Asset group name.
- Last rotation status and sanitized error.

UX:

- Show internal warning text only to admin.
- Do not show endpoint details in customer UI.
- Empty API key field means "leave unchanged".
- After manual rotate, show only masked key and expiry.

### Support Password Reset

Fields/actions:

- Generate temporary password.
- Or type a new password.
- Reset password button.
- Copy generated password once after success.
- Checkbox: revoke active sessions, checked and locked.
- Optional checkbox: require password change on next login.

The customer-facing account page remains self-service for changing their own
password with current password.

## Rotation Worker

Add an operator script first, then optionally wire it into cron:

```text
deploy/rotate_endpoint_keys.py
```

Inputs:

- Reads `.env.relay` for IAM AK/SK and rotation switches.
- Reads `users.note` for endpoint id, expiry, and per-customer toggle.

Algorithm:

1. Exit immediately if `ENDPOINT_KEY_ROTATION_ENABLED=false`.
2. Find active users with:
   - `note.byteplus_endpoint_id`,
   - `note.byteplus_endpoint_key_rotation_enabled=true`,
   - expiry missing, expired, or within `ENDPOINT_KEY_ROTATION_DAYS_BEFORE_EXPIRY`.
3. For each candidate call BytePlus `GetApiKey` with:
   - `ResourceType=endpoint`,
   - `ResourceIds=[endpoint_id]`,
   - `DurationSeconds=ENDPOINT_KEY_DURATION_SECONDS`.
4. Update `users.byteplus_api_key`.
5. Update note expiry, last rotated, next rotate, and clear last error.
6. Audit `system_rotated_endpoint_api_key`.
7. On failure, keep the old key, store sanitized error in note, and audit
   `system_endpoint_api_key_rotation_failed`.

Cron recommendation:

```cron
17 */6 * * * cd /opt/seedance-relay && docker compose -f docker-compose.relay.yml exec -T seedance-relay python deploy/rotate_endpoint_keys.py
```

Run every 6 hours, but only rotate when the threshold is reached.

## Security Rules

- Customer sees only Relay API key and Relay password controls.
- Admin sees masked BytePlus key by default.
- Full endpoint API key is not returned by list/detail endpoints.
- Audit metadata records `secret_changed=true`, endpoint id, expiry, and status,
  but not key material.
- Rotation logs must sanitize provider URLs, request headers, keys, and tokens.
- If rotation fails, generation continues with the old key until it expires.
- If endpoint key is expired and rotation fails, customer generation returns a
  safe `upstream_auth_failed` or `upstream_error` with request id, not raw
  provider body.

## Acceptance Tests

- Admin can view Peter's dedicated endpoint config with masked key and expiry.
- Admin can toggle endpoint key auto-rotation on/off for Peter.
- Manual rotate updates `users.byteplus_api_key`, expiry metadata, and audit.
- Manual rotate does not expose full key in normal API response.
- Dry-run rotate does not update key but reports candidate and intended action.
- Expired endpoint key is selected by the rotation worker.
- Customer generation after rotate still uses `users.note.byteplus_endpoint_id`.
- Customer asset upload after rotate still uses `users.note.modelark_asset_group_id`.
- Existing global endpoint customers are unaffected.
- Legacy API-key mode customers without endpoint metadata still use their
  customer upstream key.
- Admin generated password is shown once, stored hashed only, and old sessions
  are revoked.
- Customer can log in with the new temporary password and then change it.
- Audit events include password reset, endpoint config change, and endpoint key
  rotation without secret values.

## Implementation Order

1. Add typed note helpers for customer upstream metadata.
2. Add admin upstream config API.
3. Add manual endpoint key rotate API.
4. Add rotation script and dry-run mode.
5. Add admin UI endpoint section.
6. Add admin password reset endpoint and UI flow.
7. Add tests for route compatibility, key masking, rotation, and password reset.
8. Deploy with `ENDPOINT_KEY_ROTATION_ENABLED=false`.
9. Run dry-run rotation on production.
10. Enable per-customer toggle for Peter, then enable global rotation.

## Open Questions

- Should new dedicated endpoint customers default rotation on immediately, or
  should admin explicitly enable it after a first successful generation?
- Should "force change on next login" be alpha2 or alpha3?
- Should the admin UI include a "create BytePlus project/endpoint/asset group"
  wizard, or should alpha2 only manage metadata for resources created by an
  operator script?
