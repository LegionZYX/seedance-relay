# Alpha2 客户独立 Endpoint 管理后台详细实现说明

## 1. 要解决的问题

现在 Peter 这条链路已经跑通：

```text
客户只拿 Relay 账号和 Relay API Key
 -> Relay 根据用户 ID 找到 users.note
 -> users.note.byteplus_endpoint_id 决定走哪个 BytePlus endpoint
 -> users.byteplus_api_key 保存该 endpoint 的 API key
 -> users.note.modelark_asset_group_id 决定素材注册到哪个素材组
```

最终目标不是让管理员再去 BytePlus 控制台操作，而是：

```text
管理员只登录 Seedance Relay 管理后台
 -> 在客户详情里点击开通独立资源
 -> Relay 使用平台 IAM AK/SK 调 BytePlus OpenAPI
 -> 自动创建/复用 project、endpoint、asset group、endpoint API key
 -> Relay 写入本地客户配置
 -> 客户继续只使用 Relay 账号和 Relay API Key
```

BytePlus 控制台只作为底层平台，不作为日常管理入口。

下一步要做成后台功能：

- 管理员能在客户详情里看到这个客户是不是独立 endpoint。
- 管理员能给某客户开关 endpoint key 自动轮换。
- 管理员能手动轮换该客户 endpoint key。
- 管理员能协助客户重置登录密码。
- 管理员能在本系统后台一键创建客户独立 BytePlus project、endpoint、asset group。
- 管理员能在本系统后台查看创建进度、失败原因、重试、切回共享模式。
- 客户自己看不到 BytePlus project、endpoint、asset group、endpoint key。

明确非目标：

- 不要求管理员进入 BytePlus 控制台手工创建资源。
- 不要求客户拥有 BytePlus 账号。
- 不把 IAM AK/SK 分发给客户。

## 2. 页面原型

原型文件：

```text
docs/prototypes/alpha2-customer-endpoint-admin-prototype.html
docs/prototypes/alpha2-customer-endpoint-admin-prototype.png
```

页面结构：

- 左侧：客户列表。
- 中间：当前客户详情。
- 中间上部：余额、收入、素材数、最近任务状态。
- 中间核心：BytePlus 独立 Endpoint 配置。
- 中间下部：Relay API Key 和登录密码状态。
- 右侧：快速操作，包括手动轮换 endpoint key、生成临时密码、轮换策略说明。

## 3. 数据存储设计

### 3.1 保持现有表结构

Alpha2 阶段不建议马上新增很多列，先继续使用：

```text
users.byteplus_api_key
users.note
```

其中：

- `users.byteplus_api_key`：保存该客户当前可用的 endpoint API key。
- `users.note`：保存客户独立 endpoint 的非密钥元数据。

### 3.2 客户上游模式

需要支持普通客户和重要客户之间切换。不要把客户账号永久分死，而是给每个客户一个上游模式：

```json
{
  "upstream_mode": "shared"
}
```

可选值：

| 模式 | 中文 | 说明 |
| --- | --- | --- |
| `shared` | 共享模式 | 普通客户默认模式，使用平台全局 endpoint 和平台全局素材组。 |
| `manual_dedicated` | 手动独立模式 | 兜底/迁移模式。管理员可以填入已有 endpoint id、endpoint key、asset group id。不是日常推荐路径。 |
| `auto_dedicated` | 自动独立模式 | 推荐路径。管理员点击开通后，Relay 后台通过 IAM 自动创建 project、endpoint、asset group 和 endpoint key。 |

建议默认：

- 新建普通客户：`upstream_mode=shared`。
- 测试用户：保持 `shared`。
- 长期客户/重要客户：由管理员在本系统后台升级到 `auto_dedicated`。

共享模式和独立模式可以切换，但要有明确规则：

- `shared -> auto_dedicated`：允许，系统创建独立资源。
- `shared -> manual_dedicated`：允许，但只作为已有资源迁移/紧急兜底，不作为常规路径。
- `manual_dedicated -> auto_dedicated`：允许，但如果已经有 endpoint/asset group，默认复用已有资源，不重复创建。
- `auto_dedicated -> shared`：允许，但只停止使用独立资源，不自动删除 BytePlus 资源。
- `dedicated -> shared -> dedicated`：再次开启时优先复用已有 endpoint/asset group，除非管理员明确点击“重新创建资源”。

### 3.2 users.note 的标准 JSON 结构

建议统一成下面格式：

```json
{
  "customer_slug": "peterlv",
  "upstream_mode": "auto_dedicated",
  "dedicated_endpoint_provisioning_enabled": true,
  "dedicated_endpoint_provisioning_status": "provisioned",
  "byteplus_project_name": "peterlv",
  "byteplus_endpoint_id": "ep-20260606113200-7cmmq",
  "byteplus_endpoint_status": "Running",
  "byteplus_endpoint_api_key_expires_at": 1783310874,
  "byteplus_endpoint_key_rotation_enabled": true,
  "byteplus_endpoint_key_last_rotated_at": 1780718874,
  "byteplus_endpoint_key_next_rotate_at": 1782878874,
  "byteplus_endpoint_key_rotation_error": "",
  "modelark_asset_group_id": "group-20260606113202-679wh",
  "modelark_asset_group_name": "relay-peterlv-face-assets"
}
```

注意：

- `users.note` 不保存 IAM AK/SK。
- `users.note` 不保存 endpoint API key 明文。
- endpoint API key 只存在 `users.byteplus_api_key`。
- 后台列表和详情默认都只能返回脱敏 key。

新增 provisioning 相关字段：

| 字段 | 说明 |
| --- | --- |
| `upstream_mode` | 当前客户上游模式：`shared`、`manual_dedicated`、`auto_dedicated`。 |
| `dedicated_endpoint_provisioning_enabled` | 是否允许系统为该客户创建独立资源。 |
| `dedicated_endpoint_provisioning_status` | `not_started`、`creating_project`、`creating_endpoint`、`creating_asset_group`、`generating_key`、`provisioned`、`failed`。 |
| `dedicated_endpoint_provisioning_error` | 最近一次创建失败的脱敏错误。 |
| `dedicated_endpoint_provisioned_at` | 独立资源创建完成时间。 |
| `dedicated_endpoint_last_switched_at` | 最近一次切换 shared/dedicated 的时间。 |

## 4. 后端实现

### 4.1 新增 note 解析工具

在 `relay_server.py` 里增加一组 typed helper，避免到处手写 JSON：

```python
def _user_note_dict(user: dict) -> dict:
    ...

def _merge_user_note(user_id: str, patch: dict) -> dict:
    ...

def _customer_upstream_config(user: dict) -> dict:
    ...
```

用途：

- 读取 `byteplus_endpoint_id`。
- 读取 `modelark_asset_group_id`。
- 更新 endpoint 状态、过期时间、轮换开关。
- 保留 note 里其他不相关字段。

### 4.2 新增管理员查询接口

接口：

```http
GET /admin/users/{user_id}/upstream
```

返回示例：

```json
{
  "user_id": "u_6cbe404ff2592155",
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
  "endpoint_key_last_rotated_at": 1780718874,
  "endpoint_key_next_rotate_at": 1782878874,
  "asset_group_id": "group-20260606113202-679wh",
  "asset_group_name": "relay-peterlv-face-assets",
  "warnings": []
}
```

安全要求：

- 不返回完整 endpoint API key。
- 不返回 IAM AK/SK。
- 不返回 BytePlus 原始错误体。

### 4.3 新增管理员更新接口

接口：

```http
PATCH /admin/users/{user_id}/upstream
```

请求示例：

```json
{
  "mode": "auto_dedicated",
  "provisioning_enabled": true,
  "customer_slug": "peterlv",
  "project_name": "peterlv",
  "endpoint_id": "ep-20260606113200-7cmmq",
  "endpoint_status": "Running",
  "asset_group_id": "group-20260606113202-679wh",
  "asset_group_name": "relay-peterlv-face-assets",
  "endpoint_key_rotation_enabled": true,
  "endpoint_api_key": ""
}
```

规则：

- `endpoint_api_key` 为空：保持当前 key 不变。
- `endpoint_api_key` 非空：更新 `users.byteplus_api_key`。
- `endpoint_id` 必须以 `ep-` 开头。
- `asset_group_id` 必须以 `group-` 开头。
- 更新 note 时不能覆盖未知字段。
- 写审计：`admin_changed_customer_upstream_config`。

模式切换规则：

- 切到 `shared`：
  - 任务创建不再使用 `users.note.byteplus_endpoint_id`。
  - 素材注册不再优先使用 `users.note.modelark_asset_group_id`。
  - 保留已有 endpoint/asset group 元数据，方便以后再启用。
  - 不清空 `users.byteplus_api_key`，但运行时在 `shared` 模式下不能使用它作为 endpoint key。
- 切到 `manual_dedicated`：
  - 必须提供 endpoint id。
  - 必须提供 endpoint API key，或者已有 `users.byteplus_api_key`。
  - asset group id 可选，但做人脸素材场景建议必填。
- 切到 `auto_dedicated`：
  - 如果已经有 endpoint id 和 asset group id，直接启用并设置状态为 `provisioned`。
  - 如果没有资源，进入 provisioning 流程。

### 4.4 新增开通独立资源接口

接口：

```http
POST /admin/users/{user_id}/upstream/provision
```

用途：

把一个普通客户升级成独立客户。比如某个测试客户后来变成长期客户，管理员可以在 Seedance Relay 后台点击“开通独立 Endpoint”。

这个接口必须直接调用 BytePlus OpenAPI，不要求管理员去 BytePlus 控制台操作。

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

处理流程：

1. 校验客户存在且 active。
2. 生成或校验 `customer_slug`。
3. 用 IAM 查询 BytePlus project 是否存在。
4. 如果 project 不存在，调用 IAM/Resource OpenAPI 创建 BytePlus project。
5. 用 IAM 查询 endpoint 是否存在。
6. 如果 endpoint 不存在，调用 ModelArk OpenAPI 创建 endpoint。
7. 等待 endpoint 进入 `Running` 或可用状态。
8. 用 IAM 查询 asset group 是否存在。
9. 如果 asset group 不存在，调用 ModelArk asset OpenAPI 创建 asset group。
10. 调 `GetApiKey` 生成 endpoint API key。
11. 用新的 endpoint API key 对 data plane 做一次轻量鉴权探测。
12. 更新本地 DB。
13. 写审计。

更新：

```text
users.note.upstream_mode = auto_dedicated
users.note.dedicated_endpoint_provisioning_enabled = true
users.note.dedicated_endpoint_provisioning_status = provisioned
users.note.byteplus_project_name = project_name
users.note.byteplus_endpoint_id = endpoint_id
users.note.byteplus_endpoint_status = Running
users.note.byteplus_endpoint_api_key_expires_at = expires_at
users.note.modelark_asset_group_id = asset_group_id
users.note.modelark_asset_group_name = asset_group_name
users.byteplus_api_key = endpoint_api_key
```

写审计：`admin_provisioned_customer_dedicated_endpoint`。

返回：

```json
{
  "ok": true,
  "mode": "auto_dedicated",
  "project_name": "peterlv",
  "endpoint_id": "ep-xxx",
  "endpoint_status": "Running",
  "endpoint_api_key_masked": "eyJhbGci...abcd1234",
  "endpoint_api_key_expires_at": 1783310874,
  "asset_group_id": "group-xxx",
  "asset_group_name": "relay-peterlv-face-assets"
}
```

注意：

- 不返回 endpoint API key 明文。
- `dry_run=true` 时只返回将要创建的名称和动作，不调用 BytePlus，也不写 DB。
- 如果已经创建过独立资源，再点开通时默认复用已有资源，不重复创建。
- 如果要重新创建，必须另加 `force_recreate=true`，且 UI 要二次确认。

### 4.5 Provisioning Job 设计

创建 BytePlus endpoint 可能不是瞬时完成，因此不建议把完整创建过程都阻塞在一个 HTTP 请求里。

建议增加本地 job 概念。Alpha2 可以先用 SQLite 表，后续再换队列。

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

新增接口：

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

UI 交互：

- 点击“开通独立 Endpoint”后立即创建 job。
- 页面显示进度条和当前步骤。
- 成功后自动刷新客户 upstream 配置。
- 失败后显示脱敏错误和“重试”按钮。

### 4.6 BytePlus OpenAPI 封装

新增一个内部模块，集中封装所有 BytePlus 控制面调用：

```text
byteplus_control.py
```

职责：

- 读取平台 IAM AK/SK。
- SignV4 签名。
- 创建/查询 project。
- 创建/查询 endpoint。
- 创建/查询 asset group。
- `GetApiKey`。
- data plane endpoint key 验证。
- 返回统一错误对象。

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

所有调用都必须：

- 不打印 AK/SK。
- 不打印 endpoint API key。
- 记录 BytePlus request id。
- 把错误脱敏后返回给 admin。

### 4.7 权限检查

后台需要新增 IAM 能力检测：

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

- 管理后台显示“当前 IAM 账号是否具备自动开通能力”。
- 如果权限不足，按钮禁用并提示缺少哪个能力。

这个检查可以先用 dry-run 或轻量 query 实现，不要为了检查权限创建真实资源。

### 4.8 新增手动轮换 endpoint key 接口

接口：

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

处理流程：

1. 查用户。
2. 从 `users.note.byteplus_endpoint_id` 取 endpoint id。
3. 用平台 IAM AK/SK 调 BytePlus `GetApiKey`。
4. 参数使用：

```json
{
  "DurationSeconds": 2592000,
  "ResourceType": "endpoint",
  "ResourceIds": ["ep-xxx"]
}
```

5. BytePlus 返回新 endpoint API key 和过期时间。
6. 更新：

```text
users.byteplus_api_key = new_endpoint_api_key
users.note.byteplus_endpoint_api_key_expires_at = expires_at
users.note.byteplus_endpoint_key_last_rotated_at = now
users.note.byteplus_endpoint_key_next_rotate_at = expires_at - threshold
users.note.byteplus_endpoint_key_rotation_error = ""
```

7. 写审计：

```text
admin_rotated_customer_endpoint_api_key
```

返回：

```json
{
  "ok": true,
  "endpoint_id": "ep-20260606113200-7cmmq",
  "endpoint_api_key_masked": "eyJhbGci...qw9EsC5Q",
  "expires_at": 1783310874,
  "rotated_at": 1780718874
}
```

不返回完整 key，除非以后做一个单独的 break-glass 机制。

## 5. 自动轮换实现

### 5.1 环境变量

新增：

```env
ENDPOINT_KEY_ROTATION_ENABLED=false
ENDPOINT_KEY_ROTATION_DAYS_BEFORE_EXPIRY=5
ENDPOINT_KEY_ROTATION_INTERVAL_HOURS=24
ENDPOINT_KEY_DURATION_SECONDS=2592000
ENDPOINT_KEY_ROTATION_DRY_RUN=false
```

含义：

- `ENDPOINT_KEY_ROTATION_ENABLED`：平台总开关。
- `ENDPOINT_KEY_ROTATION_DAYS_BEFORE_EXPIRY`：到期前几天开始轮换。
- `ENDPOINT_KEY_DURATION_SECONDS`：新 key 有效期，30 天就是 `2592000`。
- `ENDPOINT_KEY_ROTATION_DRY_RUN`：只打印候选，不真正更新。

### 5.2 单客户开关

每个客户自己的开关放在：

```json
{
  "byteplus_endpoint_key_rotation_enabled": true
}
```

只有同时满足：

```text
平台总开关 = true
客户开关 = true
客户有 byteplus_endpoint_id
客户 key 快过期或已过期
```

才会自动轮换。

### 5.3 轮换脚本

新增脚本：

```text
deploy/rotate_endpoint_keys.py
```

执行逻辑：

1. 读取 `.env.relay`。
2. 如果总开关关闭，退出。
3. 查所有 active users。
4. 过滤出需要轮换的客户。
5. 对每个客户调 `GetApiKey`。
6. 成功则更新 DB。
7. 失败则保留旧 key，并写：

```json
{
  "byteplus_endpoint_key_rotation_error": "sanitized error"
}
```

8. 写审计。

### 5.4 Cron

建议宿主机 cron：

```cron
17 */6 * * * cd /opt/seedance-relay && docker compose -f docker-compose.relay.yml exec -T seedance-relay python deploy/rotate_endpoint_keys.py
```

它可以 6 小时跑一次，但只有满足阈值才真正轮换。

## 6. 管理后台 UI 实现

### 6.1 用户详情增加 BytePlus 独立 Endpoint 区块

字段：

- 模式：共享模式 / 手动独立模式 / 自动独立模式。
- 开通独立 Endpoint 开关。
- Provisioning 状态。
- Customer slug。
- Project name。
- Endpoint ID。
- Endpoint status。
- Endpoint API key 脱敏值。
- Endpoint key 到期时间。
- 自动轮换开关。
- 手动轮换按钮。
- Asset group ID。
- Asset group name。
- 最近轮换状态。
- 最近错误。

按钮：

- 保存配置。
- 开通独立 Endpoint。
- 切回共享模式。
- 立即轮换 endpoint key。
- 替换 endpoint key。

### 6.2 客户升级交互

普通客户默认看到：

```text
当前模式：共享模式
使用平台默认 endpoint
使用平台默认 asset group
[开通独立 Endpoint]
```

点击“开通独立 Endpoint”后弹出确认框：

```text
为该客户创建独立 BytePlus 资源？

将创建：
- Project: relay-{customer_slug}
- Endpoint: relay-{customer_slug}-seedance2
- Asset group: relay-{customer_slug}-face-assets
- Endpoint API key: 30 天有效，支持自动轮换

不会影响客户 Relay API key 和登录密码。
```

确认后：

- 创建 provisioning job。
- UI 状态变成 `queued/running`。
- 显示当前步骤，例如“正在创建 Endpoint”。
- 后台通过 IAM 自动完成 BytePlus 资源创建。
- 成功后变成 `auto_dedicated / Running`。
- 失败则显示脱敏错误、BytePlus request id 和“重试开通”按钮。

切回共享模式时弹窗提醒：

```text
切回共享模式只会停止使用该客户独立 endpoint，不会删除 BytePlus 资源。
以后可以重新启用并复用这些资源。
```

### 6.3 右侧快速操作

新增两个操作卡片：

1. 手动轮换 endpoint key。
2. 生成客户临时密码。
3. 开通独立 Endpoint。
4. 切回共享模式。

### 6.4 平台 IAM 能力面板

在后台“平台 IAM / 素材注册配置”区域新增能力状态：

```text
IAM AK/SK：已配置
Project 创建：可用
Endpoint 创建：可用
Asset group 创建：可用
GetApiKey：可用
Data plane：ap-southeast
```

如果某项不可用：

- 独立 Endpoint 自动开通按钮禁用。
- 显示脱敏错误。
- 提示管理员检查 IAM 权限。

### 6.5 状态颜色

Endpoint key 到期状态：

- 绿色：剩余大于 7 天。
- 黄色：剩余 1 到 7 天。
- 红色：已过期或少于 24 小时。

## 7. 管理员协助客户改密码

### 7.1 当前已有能力

现在后端已经支持：

```http
PATCH /admin/users/{user_id}
```

传：

```json
{
  "new_password": "new-secure-password"
}
```

效果：

- 更新 `password_hash`。
- 更新 `password_changed_at`。
- 删除该客户现有 sessions。
- 写 `admin_reset_password` 审计。

### 7.2 建议新增更清晰的专用接口

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

响应：

```json
{
  "ok": true,
  "temporary_password": "only-shown-once",
  "password_changed_at": 1780718874,
  "sessions_revoked": true
}
```

规则：

- `generate=true` 时由服务器生成强密码。
- 临时密码只在这一次响应里显示。
- 不写入日志。
- 不写入审计 metadata。
- DB 只保存 hash。
- 旧 session 立即失效。

### 7.3 是否强制客户下次登录改密

Alpha2 可以先放到 `users.note`：

```json
{
  "must_change_password": true
}
```

登录后如果检测到这个字段：

- 允许进入账号页。
- 弹出改密码框。
- 改完后清掉 `must_change_password`。

如果要做得更正式，Alpha3 再加数据库列：

```sql
ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0;
```

## 8. 审计事件

新增或明确这些 action：

```text
admin_switched_customer_upstream_mode
admin_provisioned_customer_dedicated_endpoint
admin_customer_dedicated_endpoint_provision_failed
admin_started_customer_dedicated_endpoint_provision_job
system_created_byteplus_project
system_created_byteplus_endpoint
system_created_byteplus_asset_group
system_verified_customer_endpoint_key
admin_changed_customer_upstream_config
admin_rotated_customer_endpoint_api_key
system_rotated_endpoint_api_key
system_endpoint_api_key_rotation_failed
admin_reset_password
admin_created_customer_invoice
admin_exported_customer_invoice
admin_marked_customer_invoice_paid
```

审计 metadata 可以包含：

```json
{
  "job_id": "upj_xxx",
  "project_name": "peterlv",
  "endpoint_id": "ep-xxx",
  "asset_group_id": "group-xxx",
  "expires_at": 1783310874,
  "secret_changed": true
}
```

不能包含：

- endpoint API key 明文。
- Relay API key 明文。
- password 明文。
- IAM AK/SK。
- session token。
- 上游视频 URL。

## 9. 账单保存和导出

### 9.1 目标

后台不仅要看任务流水，还要能把某个客户某个账期的消费固定成一张账单：

- 按客户和时间范围预览账单。
- 保存账单快照。
- 导出客户版账单。
- 导出内部版账单。
- 标记账单已付款。
- 保留历史账单，不因后续任务变化而自动改金额。

核心原则：

- 账单保存后金额固定。
- 可以重新生成新账单，但旧账单不覆盖。
- 同一任务不能重复进入两张未作废账单。
- 客户版账单不显示 BytePlus 成本、毛利、endpoint id、key。
- 内部版账单可以显示 BytePlus 成本和毛利。

### 9.2 新增数据表

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

### 9.3 账单快照

`snapshot_json` 保存生成时的固定数据：

```json
{
  "customer": {
    "id": "u_xxx",
    "email": "peterlv1985@gmail.com",
    "label": "Peter"
  },
  "period": {
    "start": 1780710000,
    "end": 1783302000
  },
  "pricing": {
    "currency": "USD",
    "price_multiplier": 1.15
  },
  "totals": {
    "subtotal_usd": 12.5,
    "discount_usd": 0,
    "total_usd": 12.5,
    "upstream_cost_usd": 10.87,
    "gross_profit_usd": 1.63
  },
  "items": []
}
```

客户版导出时隐藏：

- `upstream_cost_usd`
- `gross_profit_usd`
- `price_multiplier`
- endpoint id
- BytePlus 成本字段

### 9.4 管理员 API

预览账单：

```http
GET /admin/users/{user_id}/billing/preview?period_start=...&period_end=...
```

保存账单：

```http
POST /admin/users/{user_id}/invoices
```

请求：

```json
{
  "period_start": 1780710000,
  "period_end": 1783302000,
  "discount_usd": 0,
  "note": "June usage"
}
```

规则：

- 只纳入 `settled=1` 的任务。
- 默认只纳入没有进入未作废账单的任务。
- 保存 `invoices` 和 `invoice_items`。
- 写审计：`admin_created_customer_invoice`。

查看账单：

```http
GET /admin/invoices/{invoice_id}
```

列出账单：

```http
GET /admin/invoices?user_id=u_xxx&status=issued
```

导出账单：

```http
GET /admin/invoices/{invoice_id}/export?format=csv&view=customer
GET /admin/invoices/{invoice_id}/export?format=xlsx&view=internal
GET /admin/invoices/{invoice_id}/export?format=pdf&view=customer
```

格式优先级：

- `csv`：第一版先做，最快落地。
- `xlsx`：第二步做，适合财务对账。
- `pdf`：后置，适合正式发客户。

视图：

- `customer`：客户版，不含成本/毛利/内部字段。
- `internal`：内部版，含 BytePlus 成本、毛利。

标记已付款：

```http
POST /admin/invoices/{invoice_id}/mark-paid
```

### 9.5 管理后台 UI

在客户详情里新增“账单”区块：

- 当前未出账金额。
- 已保存账单数。
- 最近账单状态。
- 选择账期。
- 预览账单。
- 保存账单。
- 导出客户版 CSV/XLSX/PDF。
- 导出内部版 CSV/XLSX。
- 标记已付款。

账单列表字段：

```text
Invoice No
Period
Status
Task count
Total USD
Created at
Paid at
Actions
```

客户版明细字段：

```text
Date
Task ID
Model
Resolution
Duration
Amount USD
```

内部版明细字段：

```text
Date
Task ID
Model
Resolution
Duration
Customer charged USD
BytePlus cost USD
Gross profit USD
```

### 9.6 导出文件命名

```text
invoice-{invoice_no}-{customer_slug}-{YYYYMMDD}-{view}.csv
invoice-{invoice_no}-{customer_slug}-{YYYYMMDD}-{view}.xlsx
invoice-{invoice_no}-{customer_slug}-{YYYYMMDD}-{view}.pdf
```

示例：

```text
invoice-INV-202606-0001-peterlv-20260630-customer.csv
invoice-INV-202606-0001-peterlv-20260630-internal.xlsx
```

### 9.7 账单验收

- Peter 可以按指定账期预览账单。
- 保存账单后金额固定。
- 同一任务不能重复进入两张未作废账单。
- 作废账单后任务可以重新进入新账单。
- 客户版导出不包含 BytePlus 成本、毛利、endpoint id、key。
- 内部版导出包含成本和毛利。
- 标记已付款后状态变成 `paid`。
- 账单创建、导出、标记付款都有审计。

## 10. 安全边界

客户可见：

- Relay API Key。
- Relay 登录密码/改密码。
- 自己的余额、任务、素材。

客户不可见：

- BytePlus project。
- BytePlus endpoint id。
- BytePlus endpoint API key。
- BytePlus asset group id。
- IAM AK/SK。
- 上游原始 URL。

管理员可见：

- endpoint id。
- asset group id。
- key 脱敏值。
- key 到期时间。
- 轮换状态。
- sanitized error。

管理员默认不可见：

- endpoint API key 完整明文。

## 11. 测试清单

后端测试：

- `GET /admin/users/{id}/upstream` 返回脱敏 key。
- `PATCH /admin/users/{id}/upstream` 能更新 endpoint id 和 asset group id。
- 空 `endpoint_api_key` 不覆盖旧 key。
- 非空 `endpoint_api_key` 更新 `users.byteplus_api_key`。
- 手动 rotate 成功后更新 key 和过期时间。
- dry-run 不更新 key。
- shared 普通客户不会使用客户 endpoint key。
- shared 客户点击开通后变成 auto_dedicated。
- auto_dedicated 客户切回 shared 后不删除原 endpoint 元数据。
- dedicated 客户再次开启时复用原 endpoint，不重复创建。
- provisioning dry-run 不调用 BytePlus，不写 DB。
- provisioning 成功后客户 generation 使用新 endpoint。
- provisioning job 能显示 queued/running/succeeded/failed。
- IAM 权限不足时，后台禁用“开通独立 Endpoint”并显示缺失能力。
- data plane 验证失败时，不把新 endpoint key 切到客户账号。
- 轮换失败保留旧 key。
- 客户 generation 使用客户 endpoint id。
- 客户上传素材使用客户 asset group id。
- 没有 endpoint metadata 的旧客户仍走原逻辑。
- 管理员重置密码后旧 session 失效。
- 临时密码只返回一次。
- 审计里没有任何 secret。

生产验收：

- Peter 页面显示 Dedicated endpoint。
- Peter 自动轮换开关可以打开。
- 手动轮换后 Peter 仍能生成视频。
- Peter 的任务表 `upstream_model` 仍是 `ep-20260606113200-7cmmq`。
- Peter 的视频代理返回 206，不跳转上游 URL。
- 管理员重置 Peter 密码后，旧密码不能登录，新密码可以登录。

账单测试：

- 账单预览只包含 `settled=1` 的任务。
- 保存账单后 `snapshot_json` 固定，不随任务后续变化自动改。
- 同一任务不能重复进入两张未作废账单。
- 客户版导出不包含 BytePlus 成本、毛利、endpoint id、key。
- 内部版导出包含 BytePlus 成本和毛利。
- 标记付款后账单状态变成 `paid`。
- Peter 可以保存一张账单并导出客户版 CSV。

## 12. 建议实施顺序

第一阶段：低风险后台化

1. typed note helpers。
2. `GET/PATCH /admin/users/{id}/upstream`。
3. 管理后台显示 shared / manual_dedicated / auto_dedicated 三种模式。
4. 支持从 dedicated 切回 shared，但保留元数据。
5. 管理员专用密码重置接口和 UI。

第二阶段：普通客户升级为独立客户

1. 封装 `byteplus_control.py`。
2. 增加 IAM capability check。
3. 增加 provisioning job 表。
4. `POST /admin/users/{id}/upstream/provision`。
5. `GET /admin/upstream/provision-jobs/{job_id}`。
6. UI 加“开通独立 Endpoint”和进度条。
7. dry-run 和失败重试。
8. 验证普通客户升级后能生成视频。

第三阶段：手动轮换

1. 封装 BytePlus `GetApiKey`。
2. `POST /admin/users/{id}/upstream/endpoint-key/rotate`。
3. UI 加“立即轮换”。
4. 验证 Peter 轮换后仍能生成。

第四阶段：自动轮换

1. `deploy/rotate_endpoint_keys.py`。
2. dry-run。
3. cron。
4. 平台总开关默认关闭。
5. Peter 单客户开关开启。
6. 生产 dry-run 通过后再开总开关。

第五阶段：账单保存和导出

1. 新增 `invoices` 和 `invoice_items` 表。
2. 增加账单 preview API。
3. 增加保存账单 API。
4. 增加客户版 CSV 导出。
5. 增加后台账单区块。
6. 增加内部版 CSV/XLSX 导出。
7. PDF 导出后置。

## 13. 我的建议

先不要一步到位做“自动创建 BytePlus project + endpoint + asset group”的完整 wizard。

Alpha2 最稳的版本是分两步做：

第一步先支持“共享模式和独立模式切换”：

- 普通客户默认共享模式。
- 管理员可以把普通客户升级为独立 endpoint。
- 管理员可以把独立客户切回共享模式。
- 切回共享模式不删除 BytePlus 资源。

第二步再支持在本系统后台自动创建 BytePlus 资源：

- 后台能创建或复用 endpoint 和 asset group。
- 后台能手动轮换 endpoint key。
- 脚本能自动轮换 endpoint key。
- 管理员能重置客户密码。

这样普通客户以后变成重要客户时，不需要重新开户，也不需要换 Relay API key，只是在后台切换模式并开通独立资源。

更明确地说，日常运营路径应该是：

```text
管理后台 -> 客户详情 -> 开通独立 Endpoint -> 系统自动调用 BytePlus IAM/OpenAPI -> 完成
```

而不是：

```text
BytePlus 控制台手工创建 -> 回到 Relay 后台复制粘贴
```
