# Alpha2 IAM Endpoint Go-Live Notes

Use this note for the alpha2 real-person video path that uses one BytePlus IAM
identity for asset registration and generation.

## Verified Path

The production path is:

```text
Relay API key
 -> Relay FastAPI service
 -> BytePlus IAM AK/SK registers ModelArk asset:// media
 -> Endpoint API key creates Dreamina Seedance generation task against Endpoint ID
 -> Relay stores local task/accounting state in SQLite
```

This avoids the previous split where an `ark-...` API key could create a task
but could not see assets created by the IAM account.

## Required BytePlus Alignment

Keep these values aligned:

```text
Region: ap-southeast-1
Project: default, or the same ProjectName used for both assets and endpoint
Endpoint model: follow Relay upstream config (`BYTEPLUS_ENDPOINT_MODEL_NAME` / `BYTEPLUS_ENDPOINT_MODEL_VERSION`; current verified example is dreamina-seedance-2-0, version 260128)
Endpoint moderation: Skip
Asset group project: same project as the endpoint
```

The client-facing model remains the native BytePlus model id, for example
`dreamina-seedance-2-0-260128` or `dreamina-seedance-2-0-fast-260128`.
In IAM upstream mode, Relay forwards the
configured Endpoint ID to BytePlus while keeping the client model id in local
task/accounting records.

## `.env.relay` Minimum

```env
UPSTREAM_AUTH_MODE=iam
UPSTREAM_ENDPOINT_ID=ep-xxxxxxxxxxxxxxxx
UPSTREAM_ENDPOINT_API_KEY=endpoint-scoped-key-from-GetApiKey
UPSTREAM_API_KEY=

BYTEPLUS_ACCESS_KEY_ID=AKxxxxxxxxxxxxxxxx
BYTEPLUS_ACCESS_KEY_SECRET=xxxxxxxxxxxxxxxx
MODELARK_ASSET_GROUP_ID=group-xxxxxxxxxxxxxxxx
MODELARK_PROJECT_NAME=default

FACE_ASSET_SELF_SERVICE=true
FACE_ASSET_ENFORCE=true
ASSET_AUTO_REGISTER_SKIP_MODERATION=true

ADMIN_KEY=replace-with-random-value
ADMIN_PASSWORD=replace-with-strong-password
RUNTIME_INTERNAL_TOKEN=replace-with-random-value
PUBLIC_DOMAIN=video.example.com
PUBLIC_BASE_URL=https://video.example.com
```

Do not paste real secrets into git, screenshots, probe output, or support
messages. `docker compose config` expands env files, so treat its output as
sensitive.

`BYTEPLUS_ACCESS_KEY_ID` / `BYTEPLUS_ACCESS_KEY_SECRET` are still required for
asset registration. Current BytePlus runtime SDKs require an API key for
`content_generation.tasks.create`, so generation should use an endpoint-scoped
key returned by IAM `GetApiKey`.

## Endpoint Key Rotation Rules

Changing a customer's endpoint API key does not affect upload asset
registration. Uploads use the server IAM AK/SK plus the customer's
`byteplus_project_name` and `modelark_asset_group_id`.

Generation requests are affected by endpoint key changes. Relay chooses the
endpoint id from `users.note.byteplus_endpoint_map` for the requested client
model, then chooses the key in this order:

1. `users.note.byteplus_endpoint_key_map` entry for the client model, upstream
   model, or endpoint id.
2. `users.byteplus_api_key`.
3. Server fallback key only when the customer has no dedicated key.

Before rotating or replacing a customer's endpoint key, verify:

- every enabled customer model has an entry in `byteplus_endpoint_map`;
- the new key is authorized for every endpoint id in that map;
- when using per-endpoint keys, `byteplus_endpoint_key_map` covers every mapped
  model or endpoint id;
- the customer's `byteplus_project_name` and `modelark_asset_group_id` remain in
  the same BytePlus project used by those endpoints.

After the key change, run one smoke generation or provider-side permission probe
for each enabled model. A key that works for one endpoint may still fail on a
different endpoint in the same customer account.

If the admin UI or upstream provision endpoint is unavailable, follow
`docs/ops/alpha2-byteplus-dedicated-endpoint-ai-runbook.md`. That runbook records
the verified fallback sequence: IAM `GetProject/CreateProject`, ModelArk
`CreateEndpoint`, `GetEndpoint`, `CreateAssetGroup` with `GroupType=AIGC`, and
`GetApiKey` with `ResourceType=endpoint`.

For a temporary IP-only deployment, set `PUBLIC_DOMAIN` to the bare IP and
`PUBLIC_BASE_URL` to `http://<ip>`. Switch `PUBLIC_BASE_URL` back to
`https://<domain>` after DNS and HTTPS are ready. The login session cookie uses
the same setting: HTTP temporary deployments omit the `Secure` flag, while
HTTPS deployments enable it.

## Local Release Gate

Run these before pushing a release commit:

```bash
python -m py_compile relay_server.py create_asset_white_label.py deploy/alpha1_preflight.py deploy/alpha1_probe.py deploy/alpha1_verify_evidence.py deploy/alpha1_collect_evidence.py deploy/alpha1_completion_audit.py tests/test_iam_upstream.py
python -m unittest discover -s tests
cd runtime-go && go test ./...
```

On the deployment host, also run:

```bash
python deploy/alpha1_preflight.py --caddyfile /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile
docker compose -f docker-compose.relay.yml up -d --build
docker compose -f docker-compose.relay.yml ps
```

After the domain is live, collect external evidence:

```bash
python deploy/alpha1_collect_evidence.py \
  --base-url https://video.example.com \
  --admin-key "$ADMIN_KEY" \
  --customer-key "$CUSTOMER_RELAY_KEY" \
  --output-dir evidence/alpha2-release
```

## Vercel Fit

Do not use Vercel as the primary alpha2 backend host for this repo without a
larger architecture change.

Reasons:

- The current deployment is a two-service Docker Compose stack: FastAPI Relay
  plus Go runtime.
- Vercel does not run Docker instances directly.
- Vercel Functions have a read-only filesystem with temporary `/tmp` storage,
  while this service uses persistent SQLite and upload/video directories under
  `/data`.
- Video proxying and long polling are better suited to a persistent service
  behind Caddy or another reverse proxy.

Vercel can still be used later for a separate static/front-end admin surface
that calls the deployed Relay API, but that is not the shortest safe path for
today's backend launch.
