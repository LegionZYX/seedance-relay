# Security Runbook

## Scope

Security review in alpha1 means account, payment, key, call, audit, and log safety. It is not customer content censorship.

## Customer Credentials

- Customers can change their own login password with current-password verification.
- Customers can rotate their own Relay API key.
- Rotated customer keys are shown once.
- Old customer keys are disabled immediately in alpha1.
- Repeated failed password logins temporarily lock the account.
- Tune lock behavior with `LOGIN_MAX_FAILED_ATTEMPTS` and `LOGIN_LOCK_SECONDS`.
- Cookie-authenticated write requests require same-origin `Origin` or `Referer`.
- Bearer API requests and `X-Admin-Key` script requests are not subject to the cookie CSRF check.

## Admin Credentials

- Admin can reset customer passwords.
- Admin-provided customer passwords for create/reset must be at least 10 characters; omit the password on create to let Relay generate a temporary password.
- Admin can rotate customer Relay API keys.
- Admin password resets and admin customer-key rotations revoke existing customer sessions.
- Admin list/detail views should show masked Relay and BytePlus keys by default.
- BytePlus `ark-...` keys are operator/admin-only and never customer-visible.

## Customer Model Availability

For admin customer create/update payloads, `enabled_models` has three states:

- `null`: use the Relay default/public model list.
- `[]`: explicitly enable no models for this customer.
- `["dreamina-seedance-2-0-260128", "seedance-1-0-lite-t2v-250428"]`: enable only these native model IDs.

Customer-facing model IDs are native BytePlus API IDs by default. If `MODEL_ID_ALIASES_JSON` is configured, `enabled_models` may also contain those aliases, but aliases are customer-visible only when explicitly selected for that customer. Upstream URLs, endpoint/profile names, account labels, and keys are operator-only.

## Audit Events

Track at least:

- customer password changed,
- customer API key rotated,
- admin reset password,
- admin rotated customer API key,
- admin changed balance,
- admin changed price multiplier,
- admin changed enabled model list,
- admin changed customer status,
- admin changed upstream generation key.

Admin review:

- Use `GET /admin/audit-events` or the admin Audit tab to review recent account, billing, model-list, and credential actions.
- Filter by `target_id` when reviewing a single customer.
- Audit metadata may include non-secret operational fields such as amount or changed field name.
- Audit metadata must not include password values, Relay API keys, BytePlus keys, session tokens, or upstream video URLs.
- Audit metadata fields named like authorization, cookie, credential, secret, password, admin key, Relay key, upstream key, BytePlus key, API key, token, or session are redacted before storage.
- Non-secret boolean flags ending in `_changed`, such as `secret_changed=true`, may remain visible to preserve audit meaning.
- Audit metadata free-text values also scrub Bearer tokens, `X-Admin-Key: ...`, `ADMIN_KEY=...`, `relay_session=...`, Relay keys, BytePlus keys, provider URLs, and upstream video URLs.
- Relay scrubs audit metadata before writing it, but operators should still avoid putting secrets into notes.

## Log Hygiene

Never log passwords, Relay API keys, BytePlus keys, session tokens, signed video tokens, or upstream video URLs.

## Upstream Error Troubleshooting

When upstream task creation, deletion, refresh, or video proxying fails, Relay may return a safe upstream `request_id` from response headers so support can troubleshoot with the operator. Do not expose upstream error bodies if they contain provider URLs, video URLs, Relay API keys, BytePlus keys, session tokens, or signed URLs.
