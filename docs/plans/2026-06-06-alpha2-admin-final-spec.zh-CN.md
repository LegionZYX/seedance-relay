# Alpha2 管理后台最终收敛 SPEC

## 0. 实施状态

更新时间：2026-06-07

已完成：

- 素材生命周期第一版：上传后可自动注册 asset、客户素材隔离、客户删除自己的素材、创建 BytePlus asset 删除请求、管理员批量/强制执行删除入口。
- 客户素材库 UI：客户可以在素材库删除自己的素材。
- 管理员协助客户重置密码：专用 `POST /admin/users/{user_id}/password/reset`，支持生成临时密码、手动重置、撤销 session、写审计。
- 账单第一版：账单预览、保存、客户 CSV 导出、内部 CSV 导出、标记 paid。
- 管理后台账单入口：客户详情里可以选账期、预览、保存、导出、标记 paid。
- endpoint key 自动轮换脚本：`deploy/rotate_endpoint_keys.py`，支持 dry-run、到期前轮换、失败保留旧 key、写审计。
- 公开配置文档：`.env.relay.example` / `README.md` / `API_DOCS.md` 已补充相关开关和客户侧接口。
- IAM capability check：`GET /admin/upstream/iam-capabilities`。
- 客户 upstream 配置读取/保存：`GET/PATCH /admin/users/{user_id}/upstream`。
- 后台 dry-run / 创建独立客户 project、endpoint、asset group 的 provision job：`POST /admin/users/{user_id}/upstream/provision` 与 `upstream_provision_jobs`。
- 单客户 endpoint API key 手动轮换：`POST /admin/users/{user_id}/upstream/endpoint-key/rotate`。
- 管理后台客户详情页 Customer Endpoint 配置区：可保存配置、dry-run、创建独立 endpoint、开启自动轮换、手动轮换 endpoint key。
- 已修复两个线上发现的问题：普通保存用户表单不再覆盖 upstream note 字段导致“自动轮换 endpoint key”对勾消失；新账号开通独立 endpoint 不再错误调用 ModelArk `CreateProject`，改为 IAM `GetProject/CreateProject`。
- 测试：新增素材删除、密码重置、账单、endpoint key 自动轮换、静态 UI 回归测试。

部分完成：

- 账单导出已完成 CSV；XLSX / PDF 后置。
- 本地上传文件自动 retention 清理后置。

未完成：

- 暂无本轮 spec 内必须先完成的阻断项。

## 0.1 本轮收敛变更：客户 Project + 全模型 Endpoint Map

本轮产品和实现收敛结论如下，优先级高于本文后续旧版 `shared/manual_dedicated/auto_dedicated` 叙事：

```text
一个客户 = 一个 BytePlus Project
一个客户 Project = 一组模型 Endpoint + 一个 AIGC AssetGroup
默认客户模型 = DEFAULT_CUSTOMER_MODEL_IDS，默认等于全部 NATIVE_MODEL_IDS
后台模型开关 = users.enabled_models
生成路由 = users.note.byteplus_endpoint_map[client_model] -> BytePlus endpoint id
```

不再把客户分成“普通共享客户 / 重要独立客户 / 测试客户”三条主路径。当前商业前提是所有客户都是大 B 客户，因此所有正式客户都应拥有自己的 BytePlus Project。`Project` 是客户级资源容器和账单/权限边界；`Endpoint` 是单个模型的实际调用入口；`AssetGroup` 是客户素材长期归属位置。

本轮新增或调整的关键规则：

- 新客户开通上游资源时，Relay 通过 IAM `GetProject/CreateProject` 确保客户 Project 存在。
- Relay 按客户当前可用模型列表创建 endpoint。若 `users.enabled_models` 为空或未设置，则使用 `DEFAULT_CUSTOMER_MODEL_IDS`；当前默认值是所有 native 模型。
- 每个模型创建一个对应 ModelArk endpoint，并写入 `users.note.byteplus_endpoint_map`。
- `users.note.byteplus_endpoint_id` 只保留为兼容主 endpoint 字段；生成路由必须优先使用 `byteplus_endpoint_map`。
- 后台仍可通过 `enabled_models` 控制客户能看到和调用哪些模型，但“打开模型”不等于“自动补齐 endpoint”。如果模型已开放但没有 endpoint mapping，Relay 必须拒绝调用，不能 fallback 到错误 endpoint。
- `GetApiKey` 使用 `ResourceType=endpoint` 和 `ResourceIds=[endpoint_id_1, endpoint_id_2, ...]`，为客户当前 endpoint 集合生成 endpoint-scoped key；如果 BytePlus 实际限制单 key 只能绑定单 endpoint，则实现要改成 per-endpoint key map。
- 不再设计 test/prod 状态开关；测试应通过客户账号状态、余额、模型开关、域名或运营流程控制，而不是引入第二套上游环境状态。

模型升级批量修改规则：

1. 新 model id 先进入 `NATIVE_MODEL_IDS` / `MODEL_REGISTRY`。
2. 如果旧 model id 需要继续兼容，使用 `MODEL_ID_ALIASES_JSON` 或保留旧 id 一段观察期。
3. 批量遍历 active customers，按客户 `byteplus_project_name` 创建新模型 endpoint。
4. 以 JSON merge 方式更新 `users.note.byteplus_endpoint_map`，不要覆盖 note 里的 asset group、key rotation、billing 等其他字段。
5. 若新模型要成为默认开放模型，更新 `DEFAULT_CUSTOMER_MODEL_IDS`。
6. 旧模型确认无调用后，再从 `enabled_models` / 默认模型列表中移除，并停用旧 endpoint。

本节作为本轮变更内容，后续实现和验收应以本节为准。

## 1. 最终目标

Alpha2 管理后台要成为日常运营入口。管理员不需要进入 BytePlus 控制台完成客户资源开通、endpoint key 轮换、客户密码支持或账单导出。

最终运营路径：

```text
Relay 管理后台
 -> 客户详情
 -> 开通/切换独立 Endpoint
 -> Relay 使用平台 IAM AK/SK 调 BytePlus OpenAPI
 -> 自动创建或复用 project、endpoint、asset group、endpoint API key
 -> 客户继续只使用 Relay 账号和 Relay API Key
```

客户侧边界：

- 客户只看到 Relay 账号、Relay API Key、余额、任务、素材和下载地址。
- 客户不看到 BytePlus project、endpoint、asset group、endpoint API key、IAM AK/SK、上游原始 URL。

视频存储边界：

- 保持现状：`VIDEO_PERSIST_MODE=proxy_only`。
- 生成成品主要保存在 BytePlus。
- Relay 只保存任务记录、成本、状态和上游临时视频 URL。
- 客户下载走 Relay 白标代理地址，不把视频大文件长期落到本服务器。

## 2. 本期范围

本期要做：

- 客户共享模式 / 独立模式切换。
- 后台一键为客户开通独立 BytePlus 资源。
- endpoint API key 手动轮换。
- endpoint API key 自动轮换开关和脚本。
- 上传素材自动注册到客户自己的 BytePlus asset group。
- 素材本地临时文件清理和删除策略。
- 管理员协助客户重置密码。
- 账单保存和导出。
- IAM 能力检测。
- 审计记录。
- 管理后台 UI 原型对应落地。

本期不做：

- 客户自己选择 BytePlus endpoint。
- 客户自己看到 BytePlus 内部资源。
- 把 endpoint API key 设置为永久不过期。
- 把生成视频长期保存到本服务器。
- 完整 PDF 账单第一版强制上线，PDF 可后置。

## 3. 客户上游模式

每个客户有一个 `upstream_mode`：

| 模式 | 用途 |
| --- | --- |
| `shared` | 普通客户、测试客户。使用平台默认 endpoint 和默认 asset group。 |
| `manual_dedicated` | 迁移/兜底模式。管理员手动录入已有 endpoint、asset group、endpoint key。 |
| `auto_dedicated` | 重要客户/长期客户。Relay 后台自动创建或复用独立 BytePlus 资源。 |

默认：

- 新客户默认 `shared`。
- 测试客户保持 `shared`。
- 长期客户由管理员升级到 `auto_dedicated`。

切换规则：

- `shared -> auto_dedicated`：允许，后台创建或复用独立资源。
- `auto_dedicated -> shared`：允许，只停止使用独立资源，不删除 BytePlus 资源。
- `shared -> manual_dedicated`：允许，但只作为迁移/应急路径。
- 再次从 `shared` 切回 dedicated：优先复用已有资源。
- 只有管理员明确选择 `force_recreate=true` 时才重新创建资源，并且 UI 必须二次确认。

## 4. 数据模型

Alpha2 先尽量保持现有 `users` 表结构，继续使用：

```text
users.byteplus_api_key
users.note
```

含义：

- `users.byteplus_api_key`：保存当前客户 endpoint API key。
- `users.note`：保存非密钥元数据。

标准 `users.note`：

```json
{
  "customer_slug": "peterlv",
  "upstream_mode": "auto_dedicated",
  "dedicated_endpoint_provisioning_enabled": true,
  "dedicated_endpoint_provisioning_status": "provisioned",
  "dedicated_endpoint_provisioning_error": "",
  "dedicated_endpoint_provisioned_at": 1780718874,
  "dedicated_endpoint_last_switched_at": 1780718874,
  "byteplus_project_name": "peterlv",
  "byteplus_endpoint_id": "ep-20260606113200-7cmmq",
  "byteplus_endpoint_status": "Running",
  "byteplus_endpoint_api_key_expires_at": 1783310874,
  "byteplus_endpoint_key_rotation_enabled": true,
  "byteplus_endpoint_key_last_rotated_at": 1780718874,
  "byteplus_endpoint_key_next_rotate_at": 1782878874,
  "byteplus_endpoint_key_rotation_error": "",
  "modelark_asset_group_id": "group-20260606113202-679wh",
  "modelark_asset_group_name": "relay-peterlv-face-assets",
  "must_change_password": false
}
```

禁止：

- 不在 `users.note` 保存 IAM AK/SK。
- 不在 `users.note` 保存 endpoint API key 明文。
- 不在审计 metadata 保存任何 key 或密码明文。

## 5. 素材生命周期

### 5.1 目标

为了降低服务器存储压力，上传素材的长期存储应尽量放在 BytePlus asset group，而不是长期放在 Relay 服务器。

目标链路：

```text
客户上传素材
 -> Relay 临时保存到 /data/uploads
 -> Relay 使用 IAM 注册到 BytePlus asset group
 -> BytePlus 返回 asset_id / asset://...
 -> Relay 保存 asset_url 到 uploads 表
 -> 生成时 content[] 直接使用 asset://...
 -> 本地上传文件可按策略清理
```

### 5.2 客户隔离

素材注册组别按客户上游模式决定：

| 客户模式 | 注册到哪里 |
| --- | --- |
| `shared` | 平台默认 `MODELARK_ASSET_GROUP_ID` |
| `auto_dedicated` | `users.note.modelark_asset_group_id` |
| `manual_dedicated` | `users.note.modelark_asset_group_id` |

代码路径已经支持优先读取：

```text
users.note.modelark_asset_group_id
```

如果客户没有自己的素材组，则回落到平台默认：

```text
MODELARK_ASSET_GROUP_ID
```

注意：

- `shared` 客户会物理上共用平台默认 asset group，但 Relay 必须继续用 `uploads.user_id` 做逻辑隔离。
- 即使两个客户都在 shared group，客户也只能在素材库看到自己的上传记录，只能引用自己记录里的 `asset://...`。
- `asset://...` 不能仅凭 BytePlus group 判断归属，必须先查 Relay `uploads` 表确认当前用户拥有该 asset。

### 5.3 上传后自动注册

建议上线配置：

```env
ASSET_AUTO_REGISTER_UPLOADS=true
ASSET_AUTO_REGISTER_PURPOSES=image,video,audio
FACE_ASSET_SELF_SERVICE=true
```

含义：

- 普通图片/视频/音频上传后都尝试注册成 BytePlus asset。
- 注册成功后素材库返回 `asset_id` 和 `asset_url`。
- `suggested_content_block` 优先使用 `asset://...`。
- 客户生成时不需要知道 BytePlus group id，也不需要 BytePlus key。

失败策略：

- 如果注册 asset 失败，但本地 URL 可用，可以保留为 `url_only` 状态。
- UI 显示“未注册到 BytePlus asset”，并允许管理员/客户重试注册。
- 对真人/人脸白名单场景，失败则不能进入白名单。

### 5.4 生成时使用 asset id

如果素材已有：

```json
{
  "asset_id": "asset-xxx",
  "asset_url": "asset://asset-xxx"
}
```

生成请求使用：

```json
{
  "type": "image_url",
  "image_url": {
    "url": "asset://asset-xxx"
  },
  "role": "first_frame"
}
```

或视频：

```json
{
  "type": "video_url",
  "video_url": {
    "url": "asset://asset-xxx"
  },
  "role": "reference_video"
}
```

Relay 行为：

- 校验 `asset://...` 是否属于当前客户，防止客户引用别人素材。
- 对人脸相关 role，校验白名单。
- 将 `asset://...` 原样放进上游 `content[]`。
- BytePlus 生成任务直接读取自己 asset group 里的素材。
- Relay 不需要再把本地上传文件传给 BytePlus。

这意味着：客户用 asset id 生成时，素材引用会直接回到 BytePlus 侧完成任务。

### 5.5 本地文件清理

本地 `/data/uploads` 只作为注册中转站，不作为长期素材库。

新增配置：

```env
UPLOAD_LOCAL_RETENTION_HOURS=24
UPLOAD_LOCAL_CLEANUP_ENABLED=true
UPLOAD_DELETE_LOCAL_AFTER_ASSET_REGISTERED=true
```

建议默认：

- asset 注册成功后，保留本地文件 24 小时。
- 超过保留期自动删除本地文件。
- DB 的 `uploads` 记录继续保留。
- 素材库继续展示 `asset://...`。

如果 `asset_url` 为空，说明素材还没有注册成功，本地文件不能自动删，除非管理员手动删除。

### 5.6 删除素材

新增客户接口：

```http
DELETE /v1/uploads/{upload_id}
```

客户删除自己的素材时，分成本地删除和 BytePlus asset 删除两层。

客户权限：

- 客户可以删除自己上传的素材。
- 客户不能删除其他客户的素材。
- 客户不能直接拿 IAM 权限调用 BytePlus。
- 客户可以请求删除自己上传并注册出来的 BytePlus asset。

删除执行模式：

```env
ASSET_DELETE_EXECUTION_MODE=admin_batch
```

可选值：

| 模式 | 含义 |
| --- | --- |
| `local_only` | 客户删除只移出 Relay 素材库并清理本地文件，不删除 BytePlus asset。 |
| `admin_batch` | 客户删除会创建 BytePlus asset 删除请求，由管理员在后台批量执行。 |
| `auto` | 客户删除会自动排队，由系统后台异步调用 BytePlus 删除该 asset。 |

建议生产默认：

```env
ASSET_DELETE_EXECUTION_MODE=admin_batch
```

原因：

- 防止客户误删仍在使用的素材。
- 管理员可以先看 pending 请求，再批量执行。
- 后续稳定后可以切到 `auto`。

客户删除默认流程：

```text
DELETE /v1/uploads/{upload_id}
 -> 校验 upload.user_id == 当前客户
 -> 标记 uploads.deleted_at
 -> 从客户素材库隐藏
 -> 删除本地 /data/uploads 文件
 -> 如果存在 asset_url，则创建 asset_delete_requests
 -> 根据 ASSET_DELETE_EXECUTION_MODE 决定是否自动执行 BytePlus 删除
```

如果素材没有 `asset_url`：

- 只删除本地文件和素材库记录。
- 不创建 BytePlus 删除请求。

如果素材有 `asset_url`：

- `local_only`：不删 BytePlus asset。
- `admin_batch`：创建 pending 删除请求。
- `auto`：创建 queued 删除请求并由后台执行。

### 5.7 BytePlus Asset 删除请求

原因：

- 旧任务可能还引用过这个 asset。
- 人脸白名单可能还引用这个 asset。
- BytePlus asset 删除是破坏性动作。

新增表：

```sql
CREATE TABLE IF NOT EXISTS asset_delete_requests (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  upload_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  asset_url TEXT NOT NULL,
  status TEXT NOT NULL,
  requested_by_user_id TEXT,
  execution_mode TEXT NOT NULL,
  reason TEXT,
  error_message TEXT,
  byteplus_request_id TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  executed_at INTEGER
);
```

幂等约束：

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_asset_delete_requests_upload_active
ON asset_delete_requests(upload_id)
WHERE status IN ('pending_admin', 'queued', 'running');
```

含义：

- 同一个 upload 同一时间只能有一个未完成删除请求。
- 客户重复点击删除时，返回已有删除请求状态，不重复创建请求。
- `failed` 请求允许管理员或客户重试，但重试必须复用或关闭旧请求，不能制造多个并发删除。

状态：

```text
pending_admin
queued
running
succeeded
failed
cancelled
blocked
```

删除前安全检查：

- asset 必须来自当前客户自己的 `uploads` 记录。
- 如果还有 running/queued 任务正在引用该 asset，标记 `blocked`。
- 如果该 asset 在当前客户的人脸白名单中，删除前先停用白名单记录或要求二次确认。
- 如果同一个 asset 被多个客户记录引用，默认拒绝客户删除 BytePlus asset，只允许管理员处理。

系统执行 BytePlus 删除：

```text
asset_delete_requests.status = queued
 -> byteplus_control.delete_asset(asset_id)
 -> 成功后 status=succeeded
 -> 失败后 status=failed，保存脱敏 error 和 request id
```

`byteplus_control.py` 增加：

```python
def delete_asset(self, asset_id: str) -> dict: ...
```

注意：

- 当前代码已经有 `CreateAsset` / `GetAsset` 封装。
- 删除动作要封装在同一个 BytePlus 控制面客户端里。
- 如果 BytePlus 对 asset 删除使用不同 action 或异步语义，适配层只改 `delete_asset`，业务层不变。

管理员批量执行接口：

```http
GET /admin/asset-delete-requests?status=pending_admin
POST /admin/asset-delete-requests/{request_id}/execute
POST /admin/asset-delete-requests/batch-execute
POST /admin/asset-delete-requests/{request_id}/cancel
```

客户查看自己的删除请求：

```http
GET /v1/uploads/delete-requests
```

返回：

```json
{
  "data": [
    {
      "id": "adr_xxx",
      "upload_id": "upl_xxx",
      "asset_url": "asset://asset-xxx",
      "status": "pending_admin",
      "created_at": 1780718874,
      "executed_at": null
    }
  ]
}
```

管理员强删除接口保留：

```http
DELETE /admin/uploads/{upload_id}?delete_byteplus_asset=true
```

强删除规则：

- 仅管理员可用。
- UI 二次确认。
- 写审计。
- 删除 BytePlus asset 失败时，Relay 本地记录不能假装完全成功。

### 5.8 素材库 UI

素材卡片需要明确显示：

- Relay upload id。
- 素材显示名。
- 文件类型。
- 状态：`url_only` / `asset_registered` / `register_failed` / `local_deleted`。
- `asset_id`。
- `asset_url`。
- 所属模式：shared group / dedicated group。
- 预览。
- 操作：
  - 用作参考。
  - 复制 asset 引用。
  - 重试注册 asset。
  - 删除本地文件。
  - 删除素材。
  - 查看删除请求状态。

客户删除规则：

- 客户只能删除自己的素材。
- 客户删除默认是“从我的素材库移除 + 删除本地文件 + 按策略请求删除 BytePlus asset”。
- 删除后该素材不再出现在客户素材库。
- 如果 BytePlus 删除请求还在 pending，UI 显示“等待平台删除”。
- 如果 BytePlus 删除成功，UI 历史记录显示“asset 已删除”。
- 已经复制出去的 `asset://...` 是否还能用，取决于 BytePlus asset 是否已经被删除。

管理员删除规则：

- 管理员可以删除任意客户素材记录。
- 管理员可以选择 `delete_byteplus_asset=true` 强删除 BytePlus asset。
- 强删除必须二次确认并写审计。

### 5.9 素材预览设计

不要为了预览图给服务器增加重处理压力。第一版使用轻量预览：

| 素材类型 | 预览方式 |
| --- | --- |
| 图片 | 直接使用 Relay 上传 URL 或本地文件 URL 显示 `<img>`；本地文件清理后如无缩略图则显示占位。 |
| 视频 | 使用 `<video preload="metadata">` 读取首帧/元数据；不主动转码生成缩略图。 |
| 音频 | 显示音频图标、文件名、大小、时长字段；可选 `<audio controls>`。 |
| 本地已清理但有 `asset://` | 显示 asset id、类型、状态，不显示真实缩略图。 |

素材卡片主标题建议：

```text
原始文件名
```

副标题：

```text
upl_xxx / asset-xxx / 图片 / 117.8 KB
```

状态标签：

```text
已注册 asset
本地已清理
普通 URL
注册失败
人脸白名单
```

是否需要专门生成缩略图：

- Alpha2 不建议默认生成视频缩略图，避免 CPU/磁盘压力。
- 图片可以自然预览。
- 后续如果需要更好的体验，可以增加 `thumbnail_url` 字段，并只对图片生成小尺寸 WebP；视频缩略图后置。

客户默认看到自己的素材；其他客户看不到。

## 6. BytePlus 控制面封装

新增内部模块：

```text
byteplus_control.py
```

职责：

- 读取平台 IAM AK/SK。
- 执行 SignV4 签名。
- 创建或查询 BytePlus project。
- 创建或查询 ModelArk endpoint。
- 等待 endpoint 进入 `Running`。
- 创建或查询 asset group。
- 调 `GetApiKey` 生成 endpoint API key。
- 用 endpoint API key 做 data plane 鉴权验证。
- 返回统一、脱敏后的错误对象。

接口草案：

```python
class BytePlusControlClient:
    def ensure_project(self, project_name: str) -> dict: ...
    def ensure_endpoint(self, project_name: str, endpoint_name: str, model: str, version: str, moderation: str) -> dict: ...
    def wait_endpoint_running(self, endpoint_id: str, timeout_seconds: int = 900) -> dict: ...
    def ensure_asset_group(self, project_name: str, group_name: str) -> dict: ...
    def get_endpoint_api_key(self, endpoint_id: str, duration_seconds: int) -> dict: ...
    def verify_endpoint_key(self, endpoint_id: str, endpoint_api_key: str) -> dict: ...
```

安全要求：

- 不打印 AK/SK。
- 不打印 endpoint API key。
- 保留 BytePlus request id 用于排障。
- 错误返回给后台前必须脱敏。
- 创建素材组时 `CreateAssetGroup` 请求必须显式带 `GroupType: "AIGC"`，并带当前客户的 `ProjectName`；否则可能落入真人素材库或 default 项目，导致后续素材注册/生成不可用。
- 注册和查询素材时，`CreateAsset` / `GetAsset` 请求也必须带同一个 `ProjectName`；`CreateAsset` 还必须带 `GroupId`、`URL`、`AssetType` 和素材 `Name`。

当前已验证的真实创建顺序：

1. 用 IAM OpenAPI `GetProject` / `CreateProject` 确保客户 project。不要在 ModelArk/Ark 服务上调用 `CreateProject`。
2. 用 ModelArk OpenAPI `CreateEndpoint` 创建客户独立 endpoint，请求体的 `ModelReference.FoundationModel` 默认跟 Relay 上游配置同步，读取 `BYTEPLUS_ENDPOINT_MODEL_NAME` / `BYTEPLUS_ENDPOINT_MODEL_VERSION`；当前已验证示例值是 `dreamina-seedance-2-0` / `260128`。请求体必须带 `Moderation.Strategy=Skip`。
3. 用 `GetEndpoint` 等 endpoint 进入 `Running`。
4. 用 ModelArk OpenAPI `CreateAssetGroup` 创建客户素材组，请求体必须带 `GroupType: "AIGC"` 和同一个 `ProjectName`。
5. 如果该客户只开放一个模型，用 `GetApiKey` 生成 endpoint-scoped API key，请求体必须是 `ResourceType: "endpoint"` 和 `ResourceIds: [endpoint_id]`。
6. 如果该客户开放多个底层 FoundationModel，必须为每个 FoundationModel 创建独立 endpoint，并用一次 `GetApiKey` 覆盖所有 endpoint：`ResourceType: "endpoint"`、`ResourceIds: [endpoint_id_1, endpoint_id_2, ...]`。
7. 写回 `users.byteplus_api_key` 和 `users.note`，其中 note 至少包含 `upstream_mode=auto_dedicated`、`customer_slug`、`byteplus_project_name`、`byteplus_endpoint_id`、`modelark_asset_group_id`、`byteplus_endpoint_api_key_expires_at`。
8. 多 endpoint 客户还必须写入 `users.note.byteplus_endpoint_map`，例如 `{"dreamina-seedance-2-0-260128":"ep-standard","seedance-1-5-pro-251215":"ep-seedance15"}`。生成时 Relay 按客户请求模型选择对应 endpoint；如果模型未映射，直接返回 `endpoint_not_configured_for_model`，不调用上游。
9. 客户可见模型权限默认同步 `NATIVE_MODEL_IDS` 全量列表，包含 `dreamina-seedance-2-0-fast-260128`、1.5、1.0 pro、1.0 fast、lite t2v/i2v。客户可见模型权限和 BytePlus endpoint 是两层配置，不能只开本地模型列表而不创建对应 endpoint。
10. 后台自动 provision job 当前默认创建主 endpoint；多模型批量 endpoint 创建可以先按应急 runbook 执行，后续再接入后台按钮。

后台失效时，AI/运维按 `docs/ops/alpha2-byteplus-dedicated-endpoint-ai-runbook.md` 执行，不要临场猜 API 字段。

## 7. 独立资源开通 Job

创建 endpoint 可能较慢，因此使用本地 job。

新增表：

```sql
CREATE TABLE IF NOT EXISTS upstream_provision_jobs (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  status TEXT NOT NULL,
  current_step TEXT,
  request_json TEXT,
  result_json TEXT,
  error_message TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  finished_at INTEGER
);
```

状态：

```text
queued
running
succeeded
failed
cancelled
```

步骤：

```text
validate_customer
ensure_project
ensure_endpoint
wait_endpoint_running
ensure_asset_group
generate_endpoint_key
verify_data_plane
persist_customer_config
```

## 8. 管理员 API

### 8.1 IAM 能力检测

```http
GET /admin/upstream/iam-capabilities
```

返回：

```json
{
  "iam_configured": true,
  "can_create_project": true,
  "can_create_endpoint": true,
  "can_create_asset_group": true,
  "can_get_api_key": true,
  "data_plane_base_url": "https://ark.ap-southeast.bytepluses.com/api/v3",
  "warnings": []
}
```

用途：

- 后台显示 IAM 账号是否具备自动开通能力。
- 权限不足时禁用“开通独立 Endpoint”按钮。

### 8.2 查看客户上游配置

```http
GET /admin/users/{user_id}/upstream
```

返回脱敏配置：

```json
{
  "user_id": "u_xxx",
  "mode": "auto_dedicated",
  "provisioning_enabled": true,
  "provisioning_status": "provisioned",
  "customer_slug": "peterlv",
  "project_name": "peterlv",
  "endpoint_id": "ep-20260606113200-7cmmq",
  "endpoint_status": "Running",
  "endpoint_api_key_masked": "eyJhbGci...qw9EsC5Q",
  "endpoint_api_key_expires_at": 1783310874,
  "endpoint_key_rotation_enabled": true,
  "asset_group_id": "group-20260606113202-679wh",
  "asset_group_name": "relay-peterlv-face-assets",
  "warnings": []
}
```

### 8.3 更新客户上游配置

```http
PATCH /admin/users/{user_id}/upstream
```

支持：

- 切换 `mode`。
- 更新 `customer_slug`。
- 更新 project / endpoint / asset group 元数据。
- 打开或关闭 endpoint key 自动轮换。
- 替换 endpoint API key。

规则：

- 空 `endpoint_api_key` 表示保持不变。
- 非空 `endpoint_api_key` 更新 `users.byteplus_api_key`。
- 切到 `shared` 后运行时必须忽略客户 endpoint 和 asset group。
- 切回 `shared` 不删除 BytePlus 资源。
- 写审计：`admin_changed_customer_upstream_config`、`admin_switched_customer_upstream_mode`。

### 8.4 开通独立资源

```http
POST /admin/users/{user_id}/upstream/provision
```

请求：

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

返回：

```json
{
  "job_id": "upj_xxx",
  "status": "queued"
}
```

`dry_run=true`：

- 返回计划创建的资源名和步骤。
- 不调用 BytePlus。
- 不写 DB。

### 8.5 查看开通 Job

```http
GET /admin/upstream/provision-jobs/{job_id}
```

返回：

```json
{
  "id": "upj_xxx",
  "user_id": "u_xxx",
  "status": "running",
  "current_step": "wait_endpoint_running",
  "progress": 55,
  "result": {
    "project_name": "peterlv",
    "endpoint_id": "ep-xxx"
  },
  "error_message": ""
}
```

### 8.6 手动轮换 endpoint API key

```http
POST /admin/users/{user_id}/upstream/endpoint-key/rotate
```

请求：

```json
{
  "duration_seconds": 2592000,
  "dry_run": false
}
```

返回：

```json
{
  "ok": true,
  "endpoint_id": "ep-xxx",
  "endpoint_api_key_masked": "eyJhbGci...abcd1234",
  "expires_at": 1783310874,
  "rotated_at": 1780718874
}
```

不返回完整 endpoint API key。

### 8.7 管理员重置客户密码

新增专用接口：

```http
POST /admin/users/{user_id}/password/reset
```

请求：

```json
{
  "generate": true,
  "new_password": "",
  "force_change_on_next_login": true
}
```

返回：

```json
{
  "ok": true,
  "temporary_password": "shown-once",
  "password_changed_at": 1780718874,
  "sessions_revoked": true
}
```

规则：

- 临时密码只显示一次。
- DB 只保存 hash。
- 删除客户现有 session。
- 可选设置 `users.note.must_change_password=true`。
- 写审计：`admin_reset_password`。

## 9. endpoint key 自动轮换

环境变量：

```env
ENDPOINT_KEY_ROTATION_ENABLED=false
ENDPOINT_KEY_ROTATION_DAYS_BEFORE_EXPIRY=5
ENDPOINT_KEY_ROTATION_INTERVAL_HOURS=24
ENDPOINT_KEY_DURATION_SECONDS=2592000
ENDPOINT_KEY_ROTATION_DRY_RUN=false
```

单客户开关：

```json
{
  "byteplus_endpoint_key_rotation_enabled": true
}
```

新增脚本：

```text
deploy/rotate_endpoint_keys.py
```

逻辑：

1. 总开关关闭则退出。
2. 查 active users。
3. 过滤 `auto_dedicated` 且客户开关开启的用户。
4. 过期或即将过期时调用 `GetApiKey`。
5. 成功更新 `users.byteplus_api_key` 和 note 里的 expiry。
6. 失败保留旧 key，写脱敏错误。
7. 写审计。

推荐 cron：

```cron
17 */6 * * * cd /opt/seedance-relay && docker compose -f docker-compose.relay.yml exec -T seedance-relay python deploy/rotate_endpoint_keys.py
```

## 10. 账单保存和导出

### 10.1 数据表

```sql
CREATE TABLE IF NOT EXISTS invoices (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  invoice_no TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL,
  period_start INTEGER NOT NULL,
  period_end INTEGER NOT NULL,
  currency TEXT NOT NULL DEFAULT 'USD',
  subtotal_usd REAL NOT NULL,
  discount_usd REAL NOT NULL DEFAULT 0,
  total_usd REAL NOT NULL,
  upstream_cost_usd REAL NOT NULL DEFAULT 0,
  gross_profit_usd REAL NOT NULL DEFAULT 0,
  task_count INTEGER NOT NULL DEFAULT 0,
  note TEXT,
  snapshot_json TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  paid_at INTEGER
);
```

```sql
CREATE TABLE IF NOT EXISTS invoice_items (
  id TEXT PRIMARY KEY,
  invoice_id TEXT NOT NULL,
  task_id TEXT,
  item_type TEXT NOT NULL,
  description TEXT NOT NULL,
  client_model TEXT,
  resolution TEXT,
  duration INTEGER,
  quantity REAL NOT NULL DEFAULT 1,
  unit_price_usd REAL NOT NULL,
  amount_usd REAL NOT NULL,
  upstream_cost_usd REAL NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);
```

账单状态：

```text
draft
issued
paid
void
```

### 10.2 API

预览账单：

```http
GET /admin/users/{user_id}/billing/preview?period_start=...&period_end=...
```

保存账单：

```http
POST /admin/users/{user_id}/invoices
```

导出账单：

```http
GET /admin/invoices/{invoice_id}/export?format=csv&view=customer
GET /admin/invoices/{invoice_id}/export?format=xlsx&view=internal
GET /admin/invoices/{invoice_id}/export?format=pdf&view=customer
```

标记付款：

```http
POST /admin/invoices/{invoice_id}/mark-paid
```

### 10.3 规则

- 只纳入 `settled=1` 的任务。
- 保存账单时生成 `snapshot_json`，后续不自动变化。
- 同一任务不能重复进入两张未作废账单。
- 客户版导出不包含 BytePlus 成本、毛利、endpoint id、key。
- 内部版导出包含 BytePlus 成本和毛利。
- 第一版先做 CSV；XLSX 第二步；PDF 后置。

文件命名：

```text
invoice-{invoice_no}-{customer_slug}-{YYYYMMDD}-{view}.csv
invoice-{invoice_no}-{customer_slug}-{YYYYMMDD}-{view}.xlsx
invoice-{invoice_no}-{customer_slug}-{YYYYMMDD}-{view}.pdf
```

## 11. 管理后台 UI

客户详情页增加：

- 当前上游模式：shared / manual_dedicated / auto_dedicated。
- 开通独立 Endpoint 按钮。
- 切回共享模式按钮。
- provisioning job 进度。
- IAM 能力检测面板。
- endpoint id、project、asset group、key 到期时间。
- endpoint key 自动轮换开关。
- 立即轮换 endpoint key。
- 素材 asset 注册状态、asset id、删除/清理操作。
- 密码重置。
- 账单预览、保存、导出、标记付款。

原型文件：

```text
docs/prototypes/alpha2-customer-endpoint-admin-prototype.html
docs/prototypes/alpha2-customer-endpoint-admin-prototype.png
```

## 12. 审计

需要记录：

```text
admin_switched_customer_upstream_mode
admin_started_customer_dedicated_endpoint_provision_job
admin_provisioned_customer_dedicated_endpoint
admin_customer_dedicated_endpoint_provision_failed
system_created_byteplus_project
system_created_byteplus_endpoint
system_created_byteplus_asset_group
system_verified_customer_endpoint_key
system_registered_customer_upload_asset
system_cleaned_local_upload_file
customer_requested_upload_delete
system_created_asset_delete_request
system_deleted_byteplus_asset
system_delete_byteplus_asset_failed
admin_deleted_customer_upload_asset
admin_changed_customer_upstream_config
admin_rotated_customer_endpoint_api_key
system_rotated_endpoint_api_key
system_endpoint_api_key_rotation_failed
admin_reset_password
admin_created_customer_invoice
admin_exported_customer_invoice
admin_marked_customer_invoice_paid
```

审计 metadata 可包含：

```json
{
  "job_id": "upj_xxx",
  "project_name": "peterlv",
  "endpoint_id": "ep-xxx",
  "asset_group_id": "group-xxx",
  "asset_url": "asset://asset-xxx",
  "expires_at": 1783310874,
  "invoice_id": "inv_xxx",
  "secret_changed": true
}
```

不能包含：

- password 明文。
- Relay API key 明文。
- BytePlus endpoint API key 明文。
- IAM AK/SK。
- session token。
- 上游视频 URL。

## 13. 验收标准

客户 endpoint：

- 普通客户默认 `shared`。
- shared 客户生成视频时不使用客户 endpoint key。
- 管理后台可把 shared 客户升级为 `auto_dedicated`。
- 开通 job 能显示 queued/running/succeeded/failed。
- IAM 权限不足时禁用开通按钮。
- 开通成功后 generation 使用客户 endpoint id。
- 客户素材注册使用客户 asset group id。
- 切回 shared 后不删除原 BytePlus 资源。
- 再次启用 dedicated 时复用原资源。

轮换：

- 手动轮换更新 `users.byteplus_api_key` 和 expiry。
- 自动轮换 dry-run 不更新 DB。
- 自动轮换失败保留旧 key。
- 轮换后 Peter 仍能生成视频。

密码：

- 管理员可生成客户临时密码。
- 临时密码只返回一次。
- 旧 session 失效。
- 新密码可登录。
- 可选下次登录强制改密。

账单：

- 可按账期预览 Peter 账单。
- 保存账单后金额固定。
- 同一任务不能重复进入两张未作废账单。
- 客户版 CSV 不包含成本、毛利、endpoint id、key。
- 内部版导出包含成本和毛利。
- 可标记账单 paid。

视频代理：

- 继续使用 `proxy_only`。
- 客户视频 URL 是 Relay URL。
- Range 下载返回 206。
- 不跳转、不暴露 BytePlus 原始 URL。

素材：

- 上传后自动注册到正确的 asset group。
- `auto_dedicated` 客户上传素材进入客户自己的 group。
- `shared` 客户上传素材进入平台默认 group。
- 素材库显示 `asset_id` / `asset_url`。
- 生成时使用 `asset://...` 并原样传给 BytePlus。
- 当前客户不能引用其他客户的 `asset://...`。
- 本地上传文件可清理，清理后仍可用 `asset://...` 生成。
- 客户删除素材时可以请求删除自己上传产生的 BytePlus asset，但由 Relay 做归属校验。
- BytePlus asset 删除默认走管理员批量执行，稳定后可切换为自动异步执行。
- 管理员强删除 BytePlus asset 需要二次确认和审计。

## 14. 新实施路径：每客户 Project + 全模型 Endpoint Map

### 阶段一：冻结新资源模型

1. 确认 `NATIVE_MODEL_IDS` 是平台当前支持的全量 native 模型列表。
2. 确认 `DEFAULT_CUSTOMER_MODEL_IDS` 默认等于全部 `NATIVE_MODEL_IDS`。
3. 确认后台 `enabled_models` 仍是客户级模型开关：空值表示默认全量，空数组表示关闭全部，显式数组表示只开放列表内模型。
4. 更新管理后台文案：客户不是 shared/dedicated 切换，而是“客户 Project / 模型 Endpoint / AssetGroup / endpoint key”状态。
5. 测试：`enabled_models=null` 返回默认全量模型；`enabled_models=[]` 阻止所有模型；显式列表只返回列表模型。

### 阶段二：开通客户 Project 与 endpoint map

1. `POST /admin/users/{id}/upstream/provision` 先通过 IAM `GetProject/CreateProject` 确保客户 Project。
2. 读取客户 `enabled_models`，若未设置则读取 `DEFAULT_CUSTOMER_MODEL_IDS`。
3. 为每个开放模型调用 ModelArk `CreateEndpoint`，请求必须带同一个 `ProjectName`、对应 `ModelReference`、`Moderation.Strategy=Skip`。
4. 等待每个 endpoint `Running`。
5. 创建或确认同一 `ProjectName` 下的 AIGC AssetGroup。
6. 调用 `GetApiKey`，请求体使用 `ResourceType=endpoint` 和 `ResourceIds=[全部 endpoint id]`。
7. 写回 `users.byteplus_api_key` 和 `users.note.byteplus_endpoint_map`。
8. 测试：provision 会创建与默认模型数量一致的 endpoint，且 `GetApiKey.ResourceIds` 覆盖这些 endpoint。

### 阶段三：生成路由与安全拒绝

1. `POST /v1/videos` 先校验客户是否启用请求模型。
2. 若客户 note 存在 `byteplus_endpoint_map`，必须按请求模型取 endpoint。
3. 如果模型已启用但没有 endpoint mapping，返回 `endpoint_not_configured_for_model`，不调用上游。
4. `byteplus_endpoint_id` 仅保留为兼容主 endpoint 字段；新客户和新开通逻辑必须写 map。
5. 测试：标准模型、fast 模型、1.5/1.0/lite 模型分别路由到对应 endpoint；未映射模型不触发上游请求。

### 阶段四：后台维护与批量模型升级

1. 新增或整理运维脚本：遍历 active customers，读取 `byteplus_project_name` 和 `byteplus_endpoint_map`。
2. 当新 model id 上线时，为每个客户 Project 创建新 endpoint。
3. JSON merge 更新 `byteplus_endpoint_map`，不要覆盖 note 中的 key rotation、asset group、billing 字段。
4. 若新模型默认开放，更新 `DEFAULT_CUSTOMER_MODEL_IDS`；若只对部分客户开放，更新对应客户 `enabled_models`。
5. 旧模型保留观察期，确认无调用后再从默认模型列表和客户 `enabled_models` 中移除。
6. 测试：批量脚本 dry-run 不写 DB；真实执行只追加或替换指定模型 mapping；旧 note 字段保持不变。

### 阶段五：UI 与验收

1. 客户详情页显示 Project、AssetGroup、endpoint map、endpoint key 过期时间、key 轮换状态。
2. 模型开关区显示每个模型是否已启用、是否已有 endpoint mapping、endpoint 是否 Running。
3. 开通按钮不再描述为“升级独立客户”，改为“开通客户 Project 资源”。
4. 验收：新客户开通后拥有自己的 Project、全量默认模型 endpoint map、AIGC AssetGroup、endpoint-scoped key；客户仍只使用 Relay API Key。

## 14.1 旧实施顺序（已被第 14 节取代，仅作历史参考）

第一阶段：模式和后台基础

1. typed note helpers。
2. `GET/PATCH /admin/users/{id}/upstream`。
3. UI 显示 shared/manual/auto dedicated。
4. 支持 dedicated 切回 shared。
5. 管理员密码重置接口和 UI。

第二阶段：后台自动开通 BytePlus 资源

1. `byteplus_control.py`。
2. IAM capability check。
3. `upstream_provision_jobs` 表。
4. `POST /admin/users/{id}/upstream/provision`。
5. `GET /admin/upstream/provision-jobs/{job_id}`。
6. UI 进度条和失败重试。
7. 用测试客户验证升级到 auto_dedicated。

第三阶段：endpoint key 轮换

1. 手动轮换 API。
2. UI 立即轮换。
3. `deploy/rotate_endpoint_keys.py`。
4. dry-run。
5. cron。
6. 生产先只给 Peter 开单客户轮换，再开全局总开关。

第四阶段：素材生命周期

1. 开启并测试 `ASSET_AUTO_REGISTER_UPLOADS=true`。
2. 素材库显示 asset id / asset url / 注册状态。
3. 生成页优先使用 `asset://...`。
4. 增加客户删除素材接口。
5. 增加 `asset_delete_requests` 删除请求表。
6. 增加管理员批量执行 BytePlus asset 删除入口。
7. 增加本地上传文件清理脚本。
8. 增加管理员强删除 BytePlus asset 入口。

第五阶段：账单

1. `invoices` / `invoice_items` 表。
2. billing preview API。
3. 保存账单 API。
4. 客户版 CSV 导出。
5. 后台账单区块。
6. 内部版 XLSX 导出。
7. PDF 导出后置。

## 15. 最终结论（本轮收敛后）

Alpha2 后台的资源开通主路径收敛为：

```text
客户管理
 -> 每个大 B 客户创建或确认一个 BytePlus Project
 -> 按 DEFAULT_CUSTOMER_MODEL_IDS / enabled_models 创建模型 endpoint map
 -> 同 Project 下创建 AIGC AssetGroup
 -> 生成覆盖 endpoint 集合的 endpoint API key
 -> 上传素材注册到客户自己的 asset group
 -> 生成时按请求模型路由到对应 endpoint
 -> endpoint key 轮换、密码支持、账单保存导出、视频白标代理继续保留
```

客户侧仍然只看到 Relay 账号、Relay API Key、模型列表、余额、任务和素材；客户看不到 BytePlus Project、endpoint、asset group、endpoint API key、IAM AK/SK 或上游原始 URL。

旧版 shared/manual/auto dedicated 叙事只保留为历史兼容背景，不再作为新客户开通设计。新实现的中心是 `byteplus_endpoint_map` 和后台 `enabled_models` 开关：模型能不能用由后台开关决定，模型请求打到哪里由 endpoint map 决定。

## 15.1 旧最终结论（已被第 15 节取代，仅作历史参考）

Alpha2 的后台要收敛成一个运营控制台：

```text
客户管理
 -> 普通客户默认共享资源
 -> 重要客户一键升级独立 BytePlus 资源
 -> endpoint key 自动轮换
 -> 上传素材注册到客户自己的 BytePlus asset group
 -> 管理员支持改密
 -> 保存和导出账单
 -> 视频仍然只做白标代理，不本地长期存储
```

这样普通客户变成长期客户时，不需要重新开户注册，也不需要更换 Relay API key，只需要管理员在后台开通独立资源。
