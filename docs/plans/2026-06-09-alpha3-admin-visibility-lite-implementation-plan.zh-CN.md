# Alpha3 Admin Visibility Lite Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让后台能查看客户生成内容、下载/预览生成结果、查询客户生成请求日志，并补齐 BytePlus 兼容文档。

**Architecture:** 保留当前 FastAPI control-plane/admin + Go runtime customer video path + SQLite 的部署形态。生产 `/v1/videos*` 由 Go runtime 处理，所以 request log 必须先覆盖 Go runtime，再补 FastAPI fallback，后台查询继续放在 FastAPI admin API。

**Tech Stack:** FastAPI, Go net/http runtime, SQLite WAL, Element Plus single-file admin UI, Python unittest/pytest, Go test.

---

## Spike Summary

当前代码已经具备一部分基础：

- `tasks.prompt_text` 和 `tasks.request_payload` 已存在，可作为后台任务详情的第一版数据来源。
- `GET /admin/tasks` 已存在，但没有 `GET /admin/tasks/{task_id}` 和 admin 内容代理。
- `static/admin.html` 已有 `tasks` tab 和 `audit` tab，适合新增详情弹窗和独立 request logs tab。
- `runtime-go` 生产承接 `/v1/videos*`，不能只在 `relay_server.py` 写 request log。
- `API_DOCS.md` 已公开主流程，但缺 BytePlus 路径映射、参考系统矩阵和参数支持状态。

本计划只做最小有效版本，不引入 Redis、Postgres、独立日志服务或客户级复杂限流。

## Development Flow

每个任务完成后先跑该任务相关测试，再进入下一项。提交节奏建议：

1. `feat: add admin task detail api`
2. `feat: record customer video request logs`
3. `feat: add admin visibility ui`
4. `docs: align public api docs with byteplus video api`

如果需要更少提交，也可以在全部验证通过后 squash 成一个版本提交。

### Task 1: Admin Task Detail API

**Files:**

- Modify: `relay_server.py`
- Test: `tests/test_admin_task_visibility.py`

**Step 1: Write failing tests**

Create tests that prove:

- Admin can read another user's task through `GET /admin/tasks/{task_id}`.
- Non-admin cannot read `GET /admin/tasks/{task_id}`.
- The response includes `user_email`, `upstream_task_id`, `client_model`, `upstream_model`, `prompt_text`, `request_payload`, cost fields, and `admin_content_url`.

Run:

```powershell
python -m pytest tests/test_admin_task_visibility.py -q
```

Expected before implementation: missing route or 404/405 failure.

**Step 2: Implement route**

Add:

```http
GET /admin/tasks/{task_id}
```

Use `SELECT t.*, u.email user_email FROM tasks t LEFT JOIN users u ON t.user_id=u.id WHERE t.id=?`.

Return 404 with `not_found` when absent. Do not expose this route in `API_DOCS.md`.

**Step 3: Verify**

Run:

```powershell
python -m pytest tests/test_admin_task_visibility.py -q
```

Expected: pass.

### Task 2: Admin Content Proxy

**Files:**

- Modify: `relay_server.py`
- Test: `tests/test_admin_task_visibility.py`

**Step 1: Write failing tests**

Extend the test file to prove:

- `GET /admin/tasks/{task_id}/content` returns local file bytes when `local_video_path` exists.
- For a non-succeeded task, it returns `409 not_ready`.
- For a succeeded task without local/cached URL, it returns `404 video_unavailable`.

Run:

```powershell
python -m pytest tests/test_admin_task_visibility.py -q
```

**Step 2: Implement route**

Add:

```http
GET /admin/tasks/{task_id}/content
```

Prefer local file when available. Otherwise proxy `cached_video_url` through Relay. Do not redirect to upstream BytePlus URL as the main behavior.

**Step 3: Verify**

Run:

```powershell
python -m pytest tests/test_admin_task_visibility.py -q
```

### Task 3: Request Logs Schema And Admin API

**Files:**

- Modify: `relay_server.py`
- Modify: `runtime-go/internal/store/store.go`
- Test: `tests/test_request_logs.py`
- Test: `runtime-go/internal/store/store_test.go` or `runtime-go/internal/httpapi/server_test.go`

**Step 1: Add failing schema/API tests**

Python tests should prove:

- `request_logs` table exists after `get_db()`.
- `GET /admin/request-logs` supports `limit`, `offset`, `user_id`, `task_id`, `action`, `model`.
- Non-admin access is rejected.

Go tests should prove:

- New runtime DB initialization creates `request_logs`.
- Store helper inserts a log row without blocking existing task creation.

Run:

```powershell
python -m pytest tests/test_request_logs.py -q
go test ./internal/store ./internal/httpapi
```

**Step 2: Implement schema**

Add `request_logs` to both schemas with indexes:

```sql
CREATE TABLE IF NOT EXISTS request_logs (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT,
    task_id             TEXT,
    route               TEXT,
    action              TEXT,
    model               TEXT,
    prompt_text         TEXT,
    request_payload     TEXT,
    status_code         INTEGER,
    error_code          TEXT,
    upstream_request_id TEXT,
    ip                  TEXT,
    user_agent          TEXT,
    created_at          INTEGER NOT NULL
);
```

Add indexes for `user_id`, `task_id`, `created_at`, `action`.

**Step 3: Implement admin API**

Add:

```http
GET /admin/request-logs?limit=100&offset=0&user_id=&task_id=&action=&model=
```

Join users for `user_email`. Cap `limit` at 200.

**Step 4: Verify**

Run the same Python and Go tests again.

### Task 4: Go Runtime Request Log Writes

**Files:**

- Modify: `runtime-go/internal/httpapi/server.go`
- Modify: `runtime-go/internal/store/store.go`
- Test: `runtime-go/internal/httpapi/server_test.go`

**Step 1: Write failing tests**

Add tests proving:

- Successful `POST /v1/videos` writes `video_create_success` with `task_id`, `user_id`, `model`, `prompt_text`, and sanitized payload.
- Upstream failure writes `video_create_failed` with `upstream_request_id` when present.
- Local validation failures such as invalid model or insufficient balance write `video_create_rejected`.

Run:

```powershell
go test ./internal/httpapi
```

**Step 2: Implement best-effort logger**

Add a store helper such as `InsertRequestLog(params RequestLogParams) error`.

In handlers, logging failure must not change the customer API response. Do not log Authorization or Cookie. Limit `request_payload` to 20000 characters.

**Step 3: Verify**

Run:

```powershell
go test ./internal/httpapi ./internal/store
```

### Task 5: FastAPI Fallback Request Log Writes

**Files:**

- Modify: `relay_server.py`
- Test: `tests/test_request_logs.py`

**Step 1: Write failing tests**

Prove FastAPI fallback `POST /v1/videos` writes the same action names:

- `video_create_success`
- `video_create_failed`
- `video_create_rejected`

**Step 2: Implement helper**

Add `_request_log(...)` in `relay_server.py`. It should be best-effort and must not raise back into customer responses.

**Step 3: Verify**

Run:

```powershell
python -m pytest tests/test_request_logs.py -q
```

### Task 6: Admin UI

**Files:**

- Modify: `static/admin.html`
- Test: `tests/test_spec_scope_consistency.py`

**Step 1: Add lightweight assertions**

Add tests that assert:

- Admin UI contains `/admin/tasks/` detail loading code.
- Admin UI contains `/admin/request-logs`.
- Public customer docs still do not contain `/admin/`.

**Step 2: Implement UI**

Changes:

- Keep `tasks` table compact.
- Add one right-side operation button: `详情`.
- Add task detail dialog with sections for basic info, prompt/payload, result/content, cost, upstream.
- Add `request logs` tab with filters and a detail drawer/dialog for payload.

**Step 3: Verify visually**

Start local server only if needed, then inspect `/admin/ui/` with an admin session or static file review. At minimum run:

```powershell
python -m pytest tests/test_spec_scope_consistency.py -q
```

### Task 7: API Docs BytePlus Alignment

**Files:**

- Modify: `API_DOCS.md`
- Test: `tests/test_spec_scope_consistency.py`

**Step 1: Add docs assertions**

Add assertions for:

- Relay-to-BytePlus path mapping.
- Async task behavior.
- Supported content types and roles.
- Reference system mutual exclusion notes.
- `return_last_frame`, `callback_url`, and `extra_body` support status.

**Step 2: Update docs**

Document:

- `POST /v1/videos` is asynchronous and returns a local Relay task id.
- BytePlus native `POST /contents/generations/tasks` maps to Relay `POST /v1/videos`.
- BytePlus Files API maps to Relay `/v1/uploads*`, but is not a 1:1 pass-through.
- Reference matrix for text, first frame, first + last frame, reference image/video/audio.
- Parameters currently supported, P1 planned, and `extra_body` escape hatch.

**Step 3: Verify**

Run:

```powershell
python -m pytest tests/test_spec_scope_consistency.py -q
```

### Task 8: Final Local Gate

**Files:**

- No direct edits.

Run:

```powershell
python -m pytest tests/test_admin_task_visibility.py tests/test_request_logs.py tests/test_spec_scope_consistency.py -q
go test ./...
```

If runtime changes touch routing or deployment assumptions, also run:

```powershell
python -m pytest tests/test_deployment_config.py -q
```

### Task 9: Commit And Deployment Gate

Before production deploy:

1. Confirm branch is `alpha3`.
2. Confirm only intended files are staged.
3. Commit with a message matching the final scope.
4. Push `origin alpha3`.
5. On production, back up SQLite using `sqlite3.Connection.backup()` or equivalent.
6. Deploy with `docker compose -f docker-compose.relay.yml up -d --build`.
7. Verify:

```bash
curl -fsS http://127.0.0.1:8002/health
curl -fsS http://127.0.0.1:8012/health
curl -fsS https://seedance3.eu/health
curl -fsS -H "x-admin-key: $ADMIN_KEY" "https://seedance3.eu/admin/tasks?limit=5"
curl -fsS -H "x-admin-key: $ADMIN_KEY" "https://seedance3.eu/admin/request-logs?limit=5"
```

Do not mark the release complete without live health and admin smoke evidence.
