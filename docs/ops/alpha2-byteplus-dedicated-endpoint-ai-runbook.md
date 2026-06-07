# Alpha2 BytePlus 独立客户 Endpoint 应急创建 Runbook

更新时间：2026-06-07

用途：当管理后台 UI 或 `/admin/users/{id}/upstream/provision` 失效时，让 AI 或运维人员仍然可以按已经验证过的方式，为单个客户创建独立 BytePlus project、ModelArk endpoint、AIGC asset group 和 endpoint API key，并写回 Relay SQLite。

本手册记录的是 Peter LV 账号开通过程后修正出的真实可用流程。当前后台自动开通逻辑也应和这里保持一致。

## 1. 绝对规则

- 不要在终端、文档、截图、聊天记录里打印 `BYTEPLUS_ACCESS_KEY_SECRET`、endpoint API key 或客户 `users.byteplus_api_key` 明文。
- 每次执行前先备份 `/opt/seedance-relay/data/relay.sqlite`。
- 新客户独立模式不绑定已有客户 project。点击或执行“开通独立 endpoint”时，应为该客户 slug 新建或确保同名 project，并新增 endpoint、asset group、endpoint key。
- Project 必须通过 BytePlus IAM OpenAPI 创建或查询，不是 ModelArk/Ark 的 `CreateProject`。
- Endpoint、AssetGroup、GetApiKey 走 ModelArk OpenAPI。
- Endpoint 创建请求必须带 `Moderation: {"Strategy":"Skip"}`。
- Asset group 创建请求必须带 `GroupType: "AIGC"` 和同一个 `ProjectName`。漏掉 `AIGC` 会落到不正确的素材组类型，后续素材注册可能失败。
- 素材注册 `CreateAsset` 必须使用同一个 `ProjectName`、同一个 `GroupId`，并默认带 `Moderation: {"Strategy":"Skip"}`。
- endpoint key 不能永久不过期，当前按 `ENDPOINT_KEY_DURATION_SECONDS` 创建，默认 30 天，并依靠轮换脚本或手动轮换续期。
- Endpoint 底层 FoundationModel 默认跟 Relay 上游配置同步：读取 `BYTEPLUS_ENDPOINT_MODEL_NAME` / `BYTEPLUS_ENDPOINT_MODEL_VERSION`。不要把本手册里的示例值当作永久固定值。
- 客户可见模型权限和 BytePlus endpoint 不是同一层。`dreamina-seedance-2-0-260128` 与 `dreamina-seedance-2-0-fast-260128` 是两个客户可见模型；默认模型列表还包含 1.5、1.0 pro、1.0 fast、lite t2v/i2v。
- Alpha2 独立客户兼容一个主 `byteplus_endpoint_id`，同时支持 `byteplus_endpoint_map`。如果客户启用了 fast、1.5 或 1.0 系列，必须为对应 FoundationModel 创建独立 BytePlus endpoint，并写入 `byteplus_endpoint_map`；否则 Relay 会拒绝未映射模型，避免误打主 endpoint。

## 2. 需要准备的输入

```text
CUSTOMER_EMAIL          客户邮箱，例如 customer@example.com
CUSTOMER_SLUG           客户 slug，例如 peterlv；只用小写字母、数字、连字符
PROJECT_NAME            默认等于 CUSTOMER_SLUG
REGION                  ap-southeast-1
ENDPOINT_MODEL_NAME     默认读取 BYTEPLUS_ENDPOINT_MODEL_NAME
ENDPOINT_MODEL_VERSION  默认读取 BYTEPLUS_ENDPOINT_MODEL_VERSION
KEY_DURATION            默认 2592000 秒
```

服务器环境必须已经有：

```text
BYTEPLUS_ACCESS_KEY_ID
BYTEPLUS_ACCESS_KEY_SECRET
MODELARK_OPENAPI_HOST=ark.ap-southeast-1.byteplusapi.com
MODELARK_OPENAPI_REGION=ap-southeast-1
MODELARK_OPENAPI_VERSION=2024-01-01
BYTEPLUS_ENDPOINT_MODEL_NAME=dreamina-seedance-2-0
BYTEPLUS_ENDPOINT_MODEL_VERSION=260128
```

## 3. 优先方案：复用 Relay 已验证 helper

适用场景：管理后台打不开或按钮坏了，但服务器、Docker 容器、代码和环境变量还在。

在服务器上执行：

```bash
cd /opt/seedance-relay

export CUSTOMER_EMAIL="customer@example.com"
export CUSTOMER_SLUG="customer-slug"
export ENDPOINT_KEY_DURATION_SECONDS="${ENDPOINT_KEY_DURATION_SECONDS:-2592000}"

cp data/relay.sqlite "data/relay.sqlite.pre-manual-upstream-$(date +%Y%m%d%H%M%S).bak"

docker compose -f docker-compose.relay.yml exec -T seedance-relay python - <<'PY'
import json
import os

import relay_server as s

email = os.environ["CUSTOMER_EMAIL"].strip().lower()
slug = s._normalize_customer_slug(os.environ["CUSTOMER_SLUG"])
duration = int(os.environ.get("ENDPOINT_KEY_DURATION_SECONDS", "2592000"))

if not email:
    raise SystemExit("CUSTOMER_EMAIL is required")
if not slug:
    raise SystemExit("CUSTOMER_SLUG is required")

db = s.get_db()
try:
    row = db.execute("SELECT * FROM users WHERE lower(email)=?", (email,)).fetchone()
    if not row:
        raise SystemExit(f"user not found: {email}")
    user = dict(row)
finally:
    db.close()

req = s.ProvisionUpstreamRequest(
    customer_slug=slug,
    byteplus_project_name=slug,
    create_project=True,
    create_endpoint=True,
    create_asset_group=True,
    rotate_endpoint_key=True,
    endpoint_key_duration_seconds=duration,
    dry_run=False,
)

result = s._provision_customer_upstream_resources(user, req)
updated = s._apply_upstream_result_to_user(
    user,
    result,
    upstream_mode="auto_dedicated",
    rotation_enabled=True,
)

s._audit_event(
    "manual_ai_provisioned_customer_upstream",
    actor_user_id=None,
    actor_type="system",
    target_type="user",
    target_id=updated["id"],
    metadata={
        "customer_slug": result.get("customer_slug"),
        "project_name": result.get("byteplus_project_name"),
        "endpoint_id": result.get("byteplus_endpoint_id"),
        "asset_group_id": result.get("modelark_asset_group_id"),
        "expires_at": result.get("byteplus_endpoint_api_key_expires_at"),
        "secret_changed": bool(result.get("endpoint_api_key")),
    },
)

safe = s._user_upstream_response(updated)
print(json.dumps({
    "status": "succeeded",
    "user_id": updated["id"],
    "email": updated["email"],
    "customer_slug": safe.get("customer_slug"),
    "project_name": safe.get("byteplus_project_name"),
    "endpoint_id": safe.get("byteplus_endpoint_id"),
    "asset_group_id": safe.get("modelark_asset_group_id"),
    "endpoint_key_masked": safe.get("endpoint_api_key_masked"),
    "expires_at": safe.get("byteplus_endpoint_api_key_expires_at"),
}, ensure_ascii=False, sort_keys=True))
PY
```

成功输出应类似：

```json
{
  "asset_group_id": "group-...",
  "customer_slug": "customer-slug",
  "email": "customer@example.com",
  "endpoint_id": "ep-...",
  "endpoint_key_masked": "eyJhbG...abcde",
  "expires_at": 1783310874,
  "project_name": "customer-slug",
  "status": "succeeded",
  "user_id": "u_..."
}
```

注意：这里不会打印 endpoint key 明文。明文只会写入 `users.byteplus_api_key`。

## 4. 原始 API 顺序

如果 helper 不能用，AI 应按下面的真实 API 顺序排查或重写脚本。

### 4.1 查询或创建 Project

Project 使用 IAM 服务签名：

```text
host: iam.byteplusapi.com
service: iam
region: ap-southeast-1
version: 2018-01-01
method: POST
path: /
body: empty string
query: Action=GetProject&Version=2018-01-01&ProjectName=<PROJECT_NAME>
```

如果返回 not found，再调用：

```text
Action=CreateProject
Version=2018-01-01
ProjectName=<PROJECT_NAME>
Description=Relay customer <CUSTOMER_SLUG>
```

不要在 ModelArk host 上调 `CreateProject`。如果看到：

```text
InvalidActionOrVersion / Could not find operation CreateProject for version 2024-01-01
```

说明服务或 host 用错了，应切回 IAM OpenAPI。

### 4.2 创建 Endpoint

Endpoint 使用 ModelArk 服务签名：

```text
host: ark.ap-southeast-1.byteplusapi.com
service: ark
region: ap-southeast-1
version: 2024-01-01
Action=CreateEndpoint
```

请求体：

```json
{
  "ProjectName": "<PROJECT_NAME>",
  "Name": "relay-<CUSTOMER_SLUG>-seedance2",
  "Description": "Relay customer endpoint <CUSTOMER_SLUG>",
  "ModelReference": {
    "FoundationModel": {
      "Name": "<BYTEPLUS_ENDPOINT_MODEL_NAME>",
      "ModelVersion": "<BYTEPLUS_ENDPOINT_MODEL_VERSION>"
    },
    "CustomModelId": ""
  },
  "Moderation": {
    "Strategy": "Skip"
  },
  "Tags": [
    {"Key": "app", "Value": "seedance-relay"},
    {"Key": "customer", "Value": "<CUSTOMER_SLUG>"},
    {"Key": "createdBy", "Value": "seedance-relay"},
    {"Key": "email", "Value": "<CUSTOMER_EMAIL>"}
  ]
}
```

当前已验证示例值是：

```text
BYTEPLUS_ENDPOINT_MODEL_NAME=dreamina-seedance-2-0
BYTEPLUS_ENDPOINT_MODEL_VERSION=260128
```

如果 Relay 的上游默认模型升级，应先更新 Relay 模型配置和环境变量，再按新的环境变量创建 endpoint。不要只改 runbook。

从返回中读取：

```text
Result.EndpointId
EndpointId
Id
```

### 4.3 等待 Endpoint Running

循环调用 `GetEndpoint`：

```json
{
  "Id": "<ENDPOINT_ID>",
  "ProjectName": "<PROJECT_NAME>"
}
```

只有 `Status=Running` 后再生成 endpoint key。

### 4.4 创建 AIGC Asset Group

Action：

```text
CreateAssetGroup
```

请求体：

```json
{
  "Name": "<CUSTOMER_SLUG>-assets",
  "Description": "Relay customer asset group <CUSTOMER_SLUG>",
  "GroupType": "AIGC",
  "ProjectName": "<PROJECT_NAME>"
}
```

从返回中读取：

```text
Result.Id
Result.GroupId
Result.AssetGroupId
Id
GroupId
AssetGroupId
```

### 4.5 生成 Endpoint API Key

Action：

```text
GetApiKey
```

请求体：

```json
{
  "DurationSeconds": 2592000,
  "ResourceType": "endpoint",
  "ResourceIds": ["<ENDPOINT_ID>"]
}
```

从返回中读取：

```text
Result.ApiKey
ApiKey
api_key
```

过期时间读取：

```text
Result.ExpiresAt
Result.ExpiredTime
ExpiresAt
ExpiredTime
expires_at
```

## 5. 写回 Relay SQLite

资源创建成功后必须写回 `users` 表，否则客户生成任务不会使用新的 endpoint。

字段规则：

```text
users.byteplus_api_key = <ENDPOINT_API_KEY 明文>
users.note = JSON merge，保留原有备注字段，同时写入以下字段
```

必须写入的 note 字段：

```json
{
  "upstream_mode": "auto_dedicated",
  "customer_slug": "<CUSTOMER_SLUG>",
  "byteplus_project_name": "<PROJECT_NAME>",
  "byteplus_project_id": "<PROJECT_ID 或空字符串>",
  "byteplus_endpoint_id": "<ENDPOINT_ID>",
  "modelark_asset_group_id": "<ASSET_GROUP_ID>",
  "byteplus_endpoint_key_rotation_enabled": true,
  "byteplus_endpoint_api_key_expires_at": 1783310874,
  "byteplus_endpoint_key_last_rotated_at": 1780718874,
  "byteplus_endpoint_key_rotation_error": "",
  "byteplus_upstream_updated_at": 1780718874
}
```

如果使用第 3 节 helper，写回由 `_apply_upstream_result_to_user` 自动完成。

## 6. 生成和素材使用验证

开通后至少验证三件事：

1. 查询用户 upstream 配置，确认 endpoint/group/key 都存在且 key 只显示 masked。

```bash
curl "https://seedance3.eu/admin/users/<USER_ID>/upstream" \
  -H "X-Admin-Key: $ADMIN_KEY"
```

2. 上传素材后，`CreateAsset` 请求应使用客户自己的 `modelark_asset_group_id` 和 `byteplus_project_name`，并带：

```json
{
  "Moderation": {"Strategy": "Skip"}
}
```

3. 客户生成视频时，Relay 会把上游 model 替换为该客户的 `byteplus_endpoint_id`，并使用 `users.byteplus_api_key` 作为 endpoint API key。客户请求里仍然传 Relay 模型 ID，例如：

```text
dreamina-seedance-2-0-260128
```

fast 权限验证：

```bash
curl "https://seedance3.eu/v1/models" \
  -H "Authorization: Bearer <CUSTOMER_RELAY_API_KEY>"
```

返回的 `data[].id` 应包含：

```text
dreamina-seedance-2-0-fast-260128
```

如果不包含，说明该客户 `users.enabled_models` 是显式列表且缺少 fast 模型，需要在管理后台或 DB 中把它追加进去。

但是：在 `auto_dedicated` 模式下，追加 fast 或其他模型权限之前还要确认存在对应 endpoint，并已经写入：

```json
{
  "dreamina-seedance-2-0-260128": "ep-standard",
  "dreamina-seedance-2-0-fast-260128": "ep-fast",
  "seedance-1-5-pro-251215": "ep-seedance15"
}
```

如果没有这个映射，当前代码会返回 `endpoint_not_configured_for_model`，不会继续调用上游。

## 7. 多模型 endpoint map 补充流程

适用场景：客户已是独立 project，但需要继续开放 1.5、1.0 pro、1.0 fast 等模型。BytePlus 一个 endpoint 只绑定一个 `FoundationModel`，不能在同一个 endpoint 里直接“打开所有模型”。

执行原则：

1. 对每个需要开放的客户可见模型，先确认对应 FoundationModel 是否可创建 endpoint。
2. `CreateEndpoint` 请求继续使用同一个客户 `ProjectName`，并带 `Moderation.Strategy=Skip`。
3. 每个 endpoint 的 `ModelReference.FoundationModel.Name` / `ModelVersion` 必须与客户可见模型匹配。
4. 等所有 endpoint 都进入 `Running`。
5. 调 `GetApiKey` 时用同一个 endpoint-scoped key 覆盖全部 endpoint：`ResourceType=endpoint`，`ResourceIds=[所有 endpoint_id]`。
6. 写回 `users.byteplus_api_key` 明文和 `users.note.byteplus_endpoint_map`；不要打印 key 明文。
7. 生产验证时优先用数据库副本 + 假上游 HTTP 对象验证路由，不要为了验证路由创建真实生成任务。

Peter 当前已验证映射：

```json
{
  "dreamina-seedance-2-0-260128": "ep-20260606113200-7cmmq",
  "seedance-1-5-pro-251215": "ep-20260607190217-zrzcj",
  "seedance-1-0-pro-250528": "ep-20260607190219-vpd92",
  "seedance-1-0-pro-fast-251015": "ep-20260607190220-zjt6p"
}
```

本次 BytePlus 返回的受阻模型：

- `dreamina-seedance-2-0-fast-260128`：`ServiceNotOpen`，需要先在 BytePlus 侧开通 fast 服务权限。
- `seedance-1-0-lite-t2v-250428` / `seedance-1-0-lite-i2v-250428`：`ModelVersionStatus Retiring`，不建议作为新 dedicated endpoint 开放。

## 8. 常见错误

| 现象 | 原因 | 修复 |
|---|---|---|
| `CreateProject failed: HTTP 404 InvalidActionOrVersion` | 把 Project 创建发到了 ModelArk/Ark 服务 | 使用 IAM host `iam.byteplusapi.com`，service `iam`，version `2018-01-01` |
| 素材组创建成功但素材注册失败 | `CreateAssetGroup` 没带 `GroupType: AIGC` 或 `ProjectName` 不一致 | 重新创建 AIGC group，并写回 `modelark_asset_group_id` |
| Endpoint key 生成失败 | endpoint 未 Running 或 `GetApiKey` 请求体错误 | 先 `GetEndpoint` 等 Running，再用 `ResourceType=endpoint` + `ResourceIds=[endpoint_id]` |
| 客户上传素材看不到 `asset://...` | 未开启自动注册或 group/project 未写回用户 note | 检查 `ASSET_AUTO_REGISTER_UPLOADS`、`modelark_asset_group_id`、`byteplus_project_name` |
| 后台保存用户后对勾消失 | 旧表单覆盖了 upstream note 字段 | 当前代码已保护 upstream 字段；旧版本需要手动合并 note，不要整段覆盖 |

## 9. 回滚和重跑

- 如果只写回 DB 出错：从执行前备份恢复 SQLite，或手动把 `users.byteplus_api_key` 和 `users.note` 改回备份值。
- 如果 BytePlus 已创建 project/endpoint/group，但 DB 未写回：不要删除资源，先记录返回的 `endpoint_id` 和 `group_id`，然后只执行写回步骤。
- 如果重跑第 3 节脚本：它会确保同名 project，但会新增 endpoint 和 asset group。重跑前先查当前用户 note，避免无意创建重复资源。
- 不要把 Peter 或其他客户的 endpoint key 复制给新客户。每个独立客户必须有自己的 endpoint key。
