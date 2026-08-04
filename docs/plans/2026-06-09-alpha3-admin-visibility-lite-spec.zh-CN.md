# Alpha3 Admin Visibility Lite 版本更新 SPEC

## 1. 版本目标

本版本只做最小有效增强，不做大架构改造。

目标是让运营后台能回答三个问题：

1. 客户生成了什么内容？
2. 这个请求发生了什么、为什么失败、上游任务 id 是什么？
3. 客户提交后是否可以离开页面，回来后继续看到任务结果？

本版本不做 Redis、Postgres、独立日志服务器、异步队列、大规模限流系统。

## 2. 当前 IAM / Endpoint 能力结论

线上 `/admin/upstream/iam-capabilities` 当前返回：

```json
{
  "auth_mode": "iam",
  "iam_configured": true,
  "server_endpoint_configured": true,
  "fallback_api_key_configured": false,
  "can_create_project": true,
  "can_create_endpoint": true,
  "can_create_asset_group": true,
  "can_rotate_endpoint_key": true
}
```

解释：

- 平台 IAM AK/SK 已配置。
- 系统当前具备自动创建 Project、Endpoint、AssetGroup、Endpoint Key 的配置条件。
- 该能力接口是配置级检查，不是逐个 BytePlus action 的实时权限探测。
- 生成任务实际使用 endpoint API key，不应假设平台 IAM AK/SK 可以直接在控制台稳定查看所有客户生成任务。

当前代码生成 endpoint key 时调用：

```json
{
  "ResourceType": "endpoint",
  "ResourceIds": ["ep-xxx"]
}
```

因此可控边界是：

- IAM AK/SK 用于创建/查询项目资源、创建 endpoint、创建素材组、生成 endpoint-scoped key。
- endpoint API key 可以被限制到指定 endpoint 或 endpoint 集合。
- 后台内容可见性不能依赖 BytePlus 控制台或 IAM 账号全局查任务，必须依赖 Relay 自己记录的 task、payload、upstream_task_id 和视频链接。

## 3. 非目标

本版本不做：

- 客户级 VIP/普通等级限流。
- 复杂 QPS/RPM token bucket。
- Redis 队列。
- Postgres 迁移。
- 对象存储归档。
- 重写生成链路。
- 重做整个管理端布局。
- 让客户等待视频生成完成后才返回。

限流本版本暂不加入。后续如确实出现上游 RPM/QPS 压力，再单独设计 `Endpoint Rate Control`。

## 4. 现有行为保留

客户调用：

```http
POST /v1/videos
```

服务端继续保持：

1. 校验请求。
2. 预估费用。
3. 预扣余额。
4. 提交上游任务。
5. 本地写入 task。
6. 返回 `queued` 状态和本地 task id。

客户不需要保持请求等待视频生成完成。

客户体验要求：

- 提交后立即看到任务已创建。
- 页面可以回到任务列表。
- 用户离开后再回来，仍能看到历史任务状态。
- 任务详情页可以手动刷新或低频轮询。

## 5. 后台任务详情

新增接口：

```http
GET /admin/tasks/{task_id}
```

权限：

- 仅 admin 可访问。

返回字段：

```json
{
  "id": "vid_xxx",
  "user_id": "u_xxx",
  "user_email": "customer@example.com",
  "upstream_task_id": "cgt-xxx",
  "client_model": "dreamina-seedance-2-0-260128",
  "upstream_model": "ep-xxx",
  "resolution": "720p",
  "duration": 5,
  "status": "succeeded",
  "prompt_text": "...",
  "request_payload": "{...}",
  "estimated_cost_usd": 0.1,
  "held_usd": 0.2,
  "actual_cost_usd": 0.12,
  "upstream_actual_cost_usd": 0.08,
  "completion_tokens": 12345,
  "cached_video_url": "https://...",
  "cached_video_url_until": 1780000000,
  "local_video_path": "/data/videos/...",
  "admin_content_url": "/admin/tasks/vid_xxx/content",
  "created_at": 1780000000,
  "updated_at": 1780000000
}
```

注意：

- `request_payload` 是后台可见字段，客户 API 不返回。
- 管理端默认折叠完整 payload，避免 UI 臃肿。
- 表格只显示摘要，详情弹窗展示完整内容。

## 6. Admin 视频预览 / 下载

新增接口：

```http
GET /admin/tasks/{task_id}/content
```

权限：

- 仅 admin 可访问。

行为：

1. 如果 `local_video_path` 存在且文件存在，直接返回本地文件。
2. 否则如果 `cached_video_url` 存在，代理 upstream 视频流。
3. 如果任务还未成功，返回 `409 not_ready`。
4. 如果没有可用视频链接，返回 `404 video_unavailable`。

目的：

- 管理员不需要客户 API key。
- 管理员不依赖 BytePlus 控制台。
- 管理员能直接从 Relay 后台看客户生成结果。

## 7. 请求日志 Lite

新增表：

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

新增索引：

```sql
CREATE INDEX IF NOT EXISTS idx_request_logs_user ON request_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_request_logs_task ON request_logs(task_id);
CREATE INDEX IF NOT EXISTS idx_request_logs_created ON request_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_request_logs_action ON request_logs(action);
```

本版本只记录核心业务日志：

- `video_create_success`
- `video_create_failed`
- `video_create_rejected`
- `video_status_get`
- `video_content_get`

不记录所有 HTTP 请求，避免噪音和 SQLite 写入压力。

## 8. Admin 请求日志接口

新增接口：

```http
GET /admin/request-logs?limit=100&offset=0&user_id=&task_id=&action=&model=
```

返回：

```json
{
  "data": [
    {
      "id": "log_xxx",
      "user_id": "u_xxx",
      "user_email": "customer@example.com",
      "task_id": "vid_xxx",
      "route": "/v1/videos",
      "action": "video_create_success",
      "model": "dreamina-seedance-2-0-260128",
      "prompt_text": "...",
      "status_code": 200,
      "error_code": "",
      "upstream_request_id": "",
      "ip": "1.2.3.4",
      "user_agent": "...",
      "created_at": 1780000000
    }
  ],
  "total": 1,
  "limit": 100,
  "offset": 0
}
```

后台 UI：

- 新增独立 tab：`请求日志`。
- 表格只显示摘要。
- 点击详情展开完整 request payload。
- 不把 request log 混进任务列表，避免 UI 臃肿。

## 9. 客户端体验调整

客户应用侧保持轻量：

- 生成按钮提交 `/v1/videos`。
- 成功后立即进入任务列表或任务详情。
- 显示 `queued/running/succeeded/failed`。
- 不让用户停留在一个“长时间提交中”的阻塞状态。
- 任务详情提供刷新按钮。
- 可低频轮询，例如 5 秒一次；页面隐藏或离开时停止轮询。

这不是后端大改，只是前端交互调整。

## 10. UI 设计原则

管理端避免臃肿：

- 任务列表只展示摘要。
- 复杂字段全部放进详情弹窗。
- request payload 默认折叠。
- 下载/预览是详情页里的明确操作按钮。
- 请求日志单独 tab，不塞进任务流水。

任务详情弹窗建议分区：

1. 基本信息：客户、模型、状态、时间。
2. 内容信息：prompt、payload。
3. 结果信息：下载/预览、cached url、local path。
4. 成本信息：held、actual、upstream cost。
5. 上游信息：upstream task id、endpoint id、request id。

## 11. 验收测试

后端测试：

- admin 可以读取任意客户任务详情。
- 非 admin 不能读取 `/admin/tasks/{task_id}`。
- admin 可以通过 `/admin/tasks/{task_id}/content` 获取 succeeded 任务内容。
- 任务未成功时 admin content 返回 `409 not_ready`。
- `/v1/videos` 成功时写 `video_create_success` log。
- `/v1/videos` 上游失败时写 `video_create_failed` log。
- `/v1/videos` 余额不足、模型未启用等拒绝时写 `video_create_rejected` log。
- `/admin/request-logs` 支持按 user/task/action/model 过滤。

前端测试：

- 任务列表不明显变宽、不臃肿。
- 点击详情可以看到 prompt、payload、下载按钮。
- 请求日志 tab 可以筛选和查看详情。
- 客户生成成功后页面不长时间卡在提交状态。

部署后验证：

```bash
curl -fsS https://seedance3.eu/health
curl -fsS -H "x-admin-key: $ADMIN_KEY" https://seedance3.eu/admin/tasks?limit=5
curl -fsS -H "x-admin-key: $ADMIN_KEY" https://seedance3.eu/admin/request-logs?limit=5
```

## 12. 后续版本再考虑

如果后续出现明显并发压力，再单独做：

- endpoint/key 级 RPS/RPM token bucket；
- 客户级 submit RPM；
- queued/running active task 上限；
- Redis 或 Postgres queue；
- 结果视频对象存储归档。

这些不进入本版本。

## 13. BytePlus API 对齐补充

本版本实现前需要同步更新 `API_DOCS.md`，避免客户侧接口文档落后于 BytePlus 原生能力。

核对依据：BytePlus ModelArk 官方文档当前公开的 Video Generation API、Seedance 2.0 API Reference、Files API tutorial。结论按“必须兼容客户调用体验”处理，不要求 Relay 路径名和 BytePlus 原生路径 1:1 相同。

### 13.1 视频任务接口覆盖

BytePlus ModelArk Video Generation API 当前核心任务接口：

1. `POST /contents/generations/tasks`：创建视频生成任务。
2. `GET /contents/generations/tasks/{id}`：查询视频生成任务。
3. `GET /contents/generations/tasks`：列出视频生成任务。
4. `DELETE /contents/generations/tasks/{id}`：取消或删除视频生成任务。

Relay 当前客户侧已对应：

1. `POST /v1/videos`：创建任务。
2. `GET /v1/videos/{id}`：查询任务。
3. `GET /v1/videos`：列出客户自己的任务。
4. `DELETE /v1/videos/{id}`：取消/删除任务。
5. `GET/HEAD /v1/videos/{id}/content`：Relay 自有下载/代理能力。

结论：任务 CRUD 主体不缺。

### 13.2 需要补齐或明确支持的 BytePlus 参数

当前 `CreateVideoRequest` 已支持：

- `model`
- `content`
- `resolution`
- `ratio`
- `duration`
- `seed`
- `watermark`
- `generate_audio`
- `extra_body`

当前 `content` 已支持：

- `text`
- `image_url`
- `video_url`
- `audio_url`
- `role`

建议下一版补齐或透传以下 BytePlus 参数，但默认不改变现有行为：

| 参数 | 优先级 | 处理方式 |
| --- | --- | --- |
| `callback_url` | P1 | 加入 request schema 并透传；同时记录到 request log。 |
| `return_last_frame` | P1 | 加入 request schema、透传；查询任务时保存/返回 `last_frame_url`。 |
| `draft` | P2 | Seedance 1.5 Pro draft mode；先文档标注暂不支持或通过 `extra_body` 透传。 |
| `draft_task` content type | P2 | 扩展 `ContentBlock`，允许 `type=draft_task`；仅对支持模型开放。 |
| `camera_fixed` | P2 | 可透传，但 Seedance 2.0 当前不支持时要文档说明。 |
| `service_tier` | P2 | 可透传；Seedance 2.0 仅 online/default 时要文档说明。 |
| `execution_expires_after` | P3 | 可透传，非紧急。 |
| `ratio=adaptive` | P2 | 官方建议用于素材比例自适应场景；当前 Relay 仅列出 `16:9/9:16/1:1`，需确认上游是否允许直接透传。 |

本版本最小实现只强制做文档同步；参数透传是否实现可作为单独小任务。

额外注意：

- `return_last_frame` 是连续视频生成的关键参数，比 `draft`、`service_tier` 更贴近客户常见工作流。
- `callback_url` 对 B 端系统集成有价值，但本版本即使透传，也不依赖 webhook 完成内部状态更新，仍保留轮询链路。
- `extra_body` 已存在，可以作为短期兼容入口，但面向客户的正式参数应该逐步提升为 schema 字段，避免文档不可发现。

### 13.3 参考系统覆盖

当前 Relay 已支持：

- `content.type=text`
- `content.type=image_url`
- `content.type=video_url`
- `content.type=audio_url`
- `role=first_frame`
- `role=last_frame`
- `role=reference_image`
- `role=reference_video`
- `role=reference_audio`

这覆盖了 BytePlus Seedance 2.0 的主要参考输入形态，但还需要补齐两类规则：

1. 文档矩阵：明确 text to video、first frame、first + last frame、multimodal reference image/video/audio 各自如何传参。
2. 校验矩阵：明确互斥关系和数量限制，特别是：
   - first frame / first + last frame / multimodal reference 是三个互斥场景，不应混用。
   - `audio_url` 不能单独输入，必须至少搭配 `image_url` 或 `video_url`。
   - reference images 最多 9 个，reference videos 最多 3 个，reference audios 最多 3 个。
   - video reference 每个视频 2-15 秒，总视频时长不超过 15 秒；本版本可以先文档声明，不强制本地解析视频时长。
   - Seedance 2.0 对真人脸部直接上传有限制，Relay 的 asset 注册/白名单机制要在文档里说明为推荐路径。

### 13.4 文件接口覆盖

BytePlus Files API 核心接口：

1. Upload file。
2. Retrieve file。
3. List files。
4. Delete file。

Relay 当前不是直接暴露 BytePlus Files API，而是自有上传账本：

- `POST /v1/uploads`
- `POST /v1/uploads/from-url`
- `GET /v1/uploads`
- `GET /v1/uploads/{id}`
- `DELETE /v1/uploads/{id}`

结论：

- 客户常用上传、列表、查询、删除能力已覆盖。
- 但文档需要明确：Relay 上传接口不是 BytePlus Files API 的 1:1 映射；它会按客户归属自动注册 asset，并返回 `asset_url` / `suggested_content_block`。

### 13.5 管理端接口不需要和 BytePlus 对齐

以下是 Relay 自有运营接口，不要求 BytePlus 存在等价接口：

- `GET /admin/tasks/{task_id}`
- `GET /admin/tasks/{task_id}/content`
- `GET /admin/request-logs`
- `GET /admin/upstream/iam-capabilities`

这些接口服务于后台运营、售后取证、客户账务和内容可见性，不暴露给客户作为 BytePlus 兼容接口。

### 13.6 API 文档更新要求

更新 `API_DOCS.md` 时必须补充：

1. 客户生成任务是异步接口：提交成功后立即返回 `id`，客户后续轮询。
2. 支持的 content types 和 role 列表。
3. 图像/视频/音频参考数量限制。
4. first frame / last frame / reference image / reference video / reference audio 的互斥关系。
5. Relay 自有下载接口 `/v1/videos/{id}/content`。
6. 上传接口和 `asset://...` 使用方式。
7. 暂不支持或仅透传的 BytePlus 参数清单。
8. 与 BytePlus 原生路径的映射关系：Relay 使用 `/v1/videos*` 和 `/v1/uploads*`，不是直接暴露 `/contents/generations/tasks*` 和 `/files*`。

## 14. Spike 结果与开发约束

### 14.1 当前代码事实

本 spike 核对了当前 `alpha3` 分支的实现，得到以下事实：

- 生产路径中 `/v1/videos`, `/v1/videos/estimate`, `/v1/videos/{id}`, `/v1/videos/{id}/content` 已由 Go runtime 承接；FastAPI 保留 fallback 和 control-plane/admin 能力。
- Go runtime 通过 `CONTROL_PLANE_BASE_URL=http://seedance-relay:8002` 调用 FastAPI 的 `/internal/runtime/prepare-video-content`，用于真人素材 asset 物化等控制面能力。
- `tasks` 表已经有 `prompt_text` 和 `request_payload`，创建任务时已经保存了核心 prompt 和上游 payload。
- 管理端已有 `GET /admin/tasks`，但目前只是列表，没有单任务详情接口、admin 内容代理接口，也没有专门的 request log 查询页。
- 管理 UI 是单文件 `static/admin.html`，已有 `tasks` tab 和 `audit` tab；新增能力应该沿用现有 Element Plus 表格和弹窗风格，不重做管理端。
- `API_DOCS.md` 通过 `/v1/docs/api.md` 对外公开，客户文档不能出现 `/admin/`、`X-Admin-Key`、内部 IAM AK/SK 或 operator-only 术语。

### 14.2 中度问题与提前方案

| 问题 | 风险 | 方案 |
| --- | --- | --- |
| Go runtime 承接生产生成链路 | 只改 `relay_server.py` 会导致线上 `/v1/videos` 不写 request log | `request_logs` schema、写入 helper 和测试必须同时覆盖 Go runtime；FastAPI fallback 只作为兼容路径补齐 |
| Python 与 Go 各有 SQLite schema | 新表只加一边会导致容器启动或测试不一致 | `relay_server.py` 的 `SCHEMA/MIGRATIONS` 与 `runtime-go/internal/store/store.go` 的 schema/migrations 同步修改 |
| SQLite 写入压力 | B 端高并发下全量 HTTP access log 可能增加锁竞争 | 只记录业务事件，不做全请求日志；日志写入 best-effort，失败不影响客户生成请求；继续保持 runtime 单实例 |
| payload 过大 | 提示词和参考素材 payload 可能膨胀 SQLite | `tasks.request_payload` 保留当前 5000 字符摘要；`request_logs.request_payload` 限制上限，建议 20000 字符，并明确 `truncated=true` 元数据 |
| 敏感信息泄露 | payload/log 可能误带 API key、Authorization、Cookie | 日志只记录业务 payload，不记录请求头；如记录 user_agent/ip，禁止记录 Authorization/Cookie；后台详情 admin-only |
| UI 臃肿 | 任务列表塞太多列会难用 | 任务列表只新增“详情”操作；prompt/payload/upstream/content/cost 放进详情弹窗；request logs 单独 tab |
| BytePlus 参数兼容 | `extra_body` 可透传但客户不可发现 | P1 把 `return_last_frame`、`callback_url` 提升为 schema 字段；其他参数先文档标注或继续 `extra_body` |
| 参考系统校验 | 现在只校验类型、role、数量，没有完全校验互斥关系 | 本版本先文档补矩阵；若实现参数透传，再补 Go/Python 双侧互斥校验测试 |
| 后台内容链接 | 直接暴露上游 URL 违背白标代理策略 | `/admin/tasks/{task_id}/content` 继续走 Relay 代理/本地文件，不把 BytePlus URL 展示为主要下载入口 |

### 14.3 本版本开发流程确认

开发顺序固定为：

1. 后端 schema 与 admin API：先实现 `GET /admin/tasks/{task_id}`、`GET /admin/tasks/{task_id}/content`、`request_logs` 表和 `GET /admin/request-logs`。
2. Go runtime 日志：给生产生成链路补 `video_create_success/failed/rejected` 日志写入。
3. FastAPI fallback 日志：保持和 Go runtime 同样的 log 语义，避免回退路径行为漂移。
4. 管理 UI：在 `static/admin.html` 中给任务列表加详情弹窗，新增 request logs tab；复杂 JSON 默认折叠。
5. 客户 API 文档：更新 `API_DOCS.md` 的 BytePlus 映射、异步任务、参考系统矩阵和参数支持状态。
6. 验证：先跑针对性 Python/Go 单测，再跑文档一致性测试，最后生产部署前用 live health 和 admin smoke 验证。

### 14.4 Spike 结论

本版本可以开始开发，但必须遵守两个硬边界：

- 不引入 Redis/Postgres/独立日志服务/复杂限流；保留当前 SQLite + 单 runtime 部署形态。
- 任何影响 `/v1/videos*` 的行为都必须以 Go runtime 为主实现，FastAPI 只做 fallback/control-plane/admin 补齐。
