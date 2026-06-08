# Alpha3 Admin Visibility Deploy Runbook

Use this after `alpha3` has passed local tests and has been pushed to GitHub.

## Current Gate

Production deploy requires SSH access to `23.254.224.213`.

If SSH returns `Permission denied (publickey,password)`, register the deploy public key in the Hostwinds server panel or restore password login before running the deploy script. Do not mark the release complete while SSH is blocked.

## Deploy

From the repo root:

```powershell
$env:SEEDANCE_DEPLOY_KEY = "K:\ssh-ed25519AAAAC3NzaC1lZDI1NTE5AAAAIJJnjloWklqj7DDXOEuRIZMScetG4NFMGW49Bd3TYpjseedance-hostwinds-1210670.pem"
$commit = git rev-parse HEAD
python deploy/alpha3_admin_visibility_deploy.py --expected-commit $commit
```

The command above is a dry-run. To actually deploy:

```powershell
$commit = git rev-parse HEAD
python deploy/alpha3_admin_visibility_deploy.py --expected-commit $commit --execute
```

The script runs these production steps:

1. SSH to `root@23.254.224.213`.
2. Enter `/opt/seedance-relay`.
3. Back up `data/relay.sqlite` into `/opt/seedance-relay/data/backups`.
4. Fetch and hard reset to `origin/alpha3`.
5. Check the deployed commit matches `--expected-commit`.
6. Run `docker compose -f docker-compose.relay.yml up -d --build`.
7. Verify local container health on ports `8002` and `8012`.
8. Verify external health and docs.

Set `ADMIN_KEY` locally to include external admin smoke:

```powershell
$env:ADMIN_KEY = "<admin key>"
$commit = git rev-parse HEAD
python deploy/alpha3_admin_visibility_deploy.py --expected-commit $commit --execute
```

## Smoke Evidence

The release is not complete until these pass:

```bash
curl -fsS https://seedance3.eu/health
curl -fsS https://seedance3.eu/v1/docs/api.md | grep "BytePlus 兼容说明"
curl -fsS -H "x-admin-key: $ADMIN_KEY" "https://seedance3.eu/admin/tasks?limit=5"
curl -fsS -H "x-admin-key: $ADMIN_KEY" "https://seedance3.eu/admin/request-logs?limit=5"
```

If `/v1/docs/api.md` does not contain `BytePlus 兼容说明`, production is still serving an older build.

## Rollback

On the server:

```bash
cd /opt/seedance-relay
git reset --hard <previous-good-commit>
docker compose -f docker-compose.relay.yml up -d --build
```

SQLite backups are in:

```text
/opt/seedance-relay/data/backups
```
