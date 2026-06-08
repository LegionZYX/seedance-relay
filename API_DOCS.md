# Seedance3 Relay API

面向签约客户的 Seedance 视频生成 API 文档。

Base URL:

```text
https://seedance3.eu
```

## 1. 快速开始

完整流程是：提交任务、轮询状态、通过 Relay URL 播放或下载视频。

```bash
export KEY="sk_your_relay_api_key"

curl https://seedance3.eu/v1/videos \
  -X POST \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "dreamina-seedance-2-0-260128",
    "content": [
      {"type": "text", "text": "cinematic product launch video, clean studio light"}
    ],
    "resolution": "720p",
    "ratio": "16:9",
    "duration": 5,
    "generate_audio": false
  }'
```

成功响应示例：

```json
{
  "id": "vid_a3f9c1b2d8e4f7a6",
  "status": "queued",
  "model": "dreamina-seedance-2-0-260128",
  "estimated_cost_usd": 0.7623,
  "held_usd": 0.83853,
  "price_multiplier": 1.0
}
```

轮询任务：

```bash
curl https://seedance3.eu/v1/videos/vid_a3f9c1b2d8e4f7a6 \
  -H "Authorization: Bearer $KEY"
```

任务成功后，响应里的 `video_url` 只会是 Relay 域名：

```json
{
  "id": "vid_a3f9c1b2d8e4f7a6",
  "status": "succeeded",
  "video_url": "https://seedance3.eu/v1/videos/vid_a3f9c1b2d8e4f7a6/content"
}
```

播放或下载：

```bash
curl -L -o video.mp4 \
  https://seedance3.eu/v1/videos/vid_a3f9c1b2d8e4f7a6/content \
  -H "Authorization: Bearer $KEY"
```

## 2. 鉴权

除公开模型列表外，客户接口都使用 Relay API Key：

```text
Authorization: Bearer sk-your-relay-api-key
```

`GET /v1/models` 不带 Key 时返回默认公开模型列表；带 Key 时返回该客户实际可用的模型列表。

客户可以在自己的账号页面轮换 API Key。新 Key 只在创建或轮换时显示一次，之后只显示 `api_key_masked`。

## 3. 模型

默认使用字节 API 原生 `model id`。如果管理员主动配置了别名并给你的账号启用，`/v1/models` 会返回别名，你也可以直接用别名提交；否则请按原生 ID 调用。

```bash
curl https://seedance3.eu/v1/models
```

响应示例：

```json
{
  "data": [
    {
      "id": "dreamina-seedance-2-0-260128",
      "description": "High quality video generation",
      "supported_resolutions": ["480p", "720p", "1080p"],
      "supported_ratios": ["16:9", "9:16", "1:1"],
      "duration_seconds": {"min": 2, "max": 15},
      "capabilities": {
        "supports_audio": true,
        "supports_reference_image": true,
        "supports_reference_video": true,
        "supports_reference_audio": true
      }
    }
  ]
}
```

当前公开模型 ID：

| Model ID | 说明 |
|---|---|
| `dreamina-seedance-2-0-260128` | 高质量视频生成 |
| `dreamina-seedance-2-0-fast-260128` | 高质量快速生成 |
| `seedance-1-5-pro-251215` | Seedance 1.5 pro 兼容模型 |
| `seedance-1-0-pro-250528` | 1080p 视频生成 |
| `seedance-1-0-pro-fast-251015` | 720p 快速生成 |
| `seedance-1-0-lite-t2v-250428` | 轻量文生视频 |
| `seedance-1-0-lite-i2v-250428` | 轻量图生视频 |

你的实际可用模型由账号的 `enabled_models` 决定。提交未启用模型会返回 `model_not_enabled`，不会进入上游生成，也不会扣费。

## 4. 价格与余额

```bash
curl https://seedance3.eu/v1/pricing \
  -H "Authorization: Bearer $KEY"
```

响应示例：

```json
{
  "price_multiplier": 1.3,
  "pricing_scope": "customer",
  "buffer_pct": 0.1,
  "pricing": {
    "dreamina-seedance-2-0-260128": {
      "720p": {
        "tokens_per_second": 21780,
        "price_no_video_ref_usd_per_1k": 0.0091,
        "price_with_video_ref_usd_per_1k": 0.00559
      }
    }
  }
}
```

`pricing_scope=customer` 表示你的账号使用后台单独配置的 `price_multiplier`；`pricing_scope=global` 表示使用服务器全局默认价格。旧版本响应里可能带兼容字段 `markup_pct`，新接入请忽略它，只读取 `price_multiplier`。

计费规则：

```text
estimated_cost = upstream_estimate * price_multiplier
held_usd       = upstream_max_cost * price_multiplier
final_cost     = upstream_actual_cost * task_price_multiplier_snapshot
```

任务创建时会快照当时的 `price_multiplier`，之后管理员再改倍率，不影响已创建任务的最终结算。

查看账号：

```bash
curl https://seedance3.eu/v1/me \
  -H "Authorization: Bearer $KEY"
```

响应示例：

```json
{
  "email": "customer@example.com",
  "balance_usd": 100.0,
  "price_multiplier": 1.3,
  "api_key_masked": "sk-abc...xyz"
}
```

## 5. 提交前预估

```bash
curl https://seedance3.eu/v1/videos/estimate \
  -X POST \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "dreamina-seedance-2-0-260128",
    "content": [{"type": "text", "text": "short brand video"}],
    "resolution": "720p",
    "duration": 5
  }'
```

响应示例：

```json
{
  "estimated_cost_usd": 0.7623,
  "max_cost_usd": 0.83853,
  "price_multiplier": 1.0,
  "pricing_scope": "customer"
}
```

## 6. 生成视频

```text
POST /v1/videos
```

请求体字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `model` | string | 是 | `/v1/models` 返回的模型 ID |
| `content` | array | 是 | 原生内容块数组 |
| `resolution` | string | 否 | `480p`、`720p`、`1080p` |
| `ratio` | string | 否 | `16:9`、`9:16`、`1:1` 等 |
| `duration` | integer | 否 | 秒数，按模型能力限制 |
| `seed` | integer | 否 | 随机种子 |
| `generate_audio` | boolean | 否 | 是否生成音频 |
| `watermark` | boolean | 否 | 是否带水印 |

`content[]` 示例：

```json
[
  {"type": "text", "text": "clean commercial video"},
  {"type": "image_url", "image_url": {"url": "https://seedance3.eu/uploads/upl_123.jpg"}, "role": "first_frame"},
  {"type": "video_url", "video_url": {"url": "https://seedance3.eu/uploads/upl_456.mp4"}, "role": "reference_video"}
]
```

Relay 会校验结构、模型能力、素材归属和余额。Relay 不做额外的提示词内容限制；上游生成是否成功仍取决于上游模型和账号配置。

## 7. 查询任务

查询单个任务：

```bash
curl https://seedance3.eu/v1/videos/vid_a3f9c1b2d8e4f7a6 \
  -H "Authorization: Bearer $KEY"
```

列出自己的任务：

```bash
curl "https://seedance3.eu/v1/videos?limit=20&offset=0&status=succeeded" \
  -H "Authorization: Bearer $KEY"
```

客户只能看到自己的任务。任务响应不会返回上游临时视频 URL。

## 8. 视频内容代理

```text
GET  /v1/videos/{id}/content
HEAD /v1/videos/{id}/content
```

视频内容通过 Relay 代理返回，支持浏览器播放需要的 `Range`：

```bash
curl -I https://seedance3.eu/v1/videos/vid_a3f9c1b2d8e4f7a6/content \
  -H "Authorization: Bearer $KEY"

curl https://seedance3.eu/v1/videos/vid_a3f9c1b2d8e4f7a6/content \
  -H "Authorization: Bearer $KEY" \
  -H "Range: bytes=0-1048575" \
  -o part.mp4
```

正常情况下，`Range` 请求会返回 `206 Partial Content`、`Content-Range`、`Content-Length` 和视频 `Content-Type`。

## 9. 上传素材

上传本地文件：

```bash
curl https://seedance3.eu/v1/uploads \
  -X POST \
  -H "Authorization: Bearer $KEY" \
  -F "file=@reference.jpg;type=image/jpeg"
```

响应示例：

```json
{
  "id": "upl_a1b2c3d4e5f6a7b8",
  "url": "https://seedance3.eu/uploads/2026/06/02/upl_a1b2c3d4e5f6a7b8.jpg",
  "content_type": "image/jpeg",
  "size_bytes": 123456,
  "purpose": "image",
  "suggested_content_block": {
    "type": "image_url",
    "image_url": {
      "url": "https://seedance3.eu/uploads/2026/06/02/upl_a1b2c3d4e5f6a7b8.jpg"
    },
    "role": "first_frame"
  }
}
```

从 URL 导入素材：

```bash
curl https://seedance3.eu/v1/uploads/from-url \
  -X POST \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://seedance3.eu/uploads/2026/06/02/upl_a1b2c3d4e5f6a7b8.jpg", "purpose": "image"}'
```

删除自己的素材：

```bash
curl https://seedance3.eu/v1/uploads/upl_a1b2c3d4e5f6a7b8 \
  -X DELETE \
  -H "Authorization: Bearer $KEY"
```

如果素材已经注册成 `asset://...`，Relay 会按 `ASSET_DELETE_EXECUTION_MODE`
创建 BytePlus asset 删除请求。默认 `admin_batch` 模式下，素材会立即从客户素材库隐藏，
BytePlus asset 由管理员后台批量执行删除。

查看自己的删除请求：

```bash
curl https://seedance3.eu/v1/uploads/delete-requests \
  -H "Authorization: Bearer $KEY"
```

默认 `admin_batch` 模式下，管理员会在后台批量处理待删除 asset；客户不需要也不会接触管理员密钥或 BytePlus 权限。

## 10. 账号自助

修改登录密码：

```bash
curl https://seedance3.eu/auth/change-password \
  -X POST \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"current_password": "old-password", "new_password": "new-secure-password"}'
```

轮换自己的 Relay API Key：

```bash
curl https://seedance3.eu/v1/me/api-key/rotate \
  -X POST \
  -H "Authorization: Bearer $KEY"
```

响应示例：

```json
{
  "api_key": "<new_relay_key_shown_once>",
  "api_key_masked": "sk-new...shown",
  "rotated_at": 1760000000,
  "shown_once": true,
  "previous_key_status": "disabled"
}
```

旧 Key 会立即失效。

### 10.1 账单查询

客户只能查询自己的账单，响应不包含 `upstream_cost_usd` / `gross_profit_usd`。

```bash
curl https://seedance3.eu/v1/invoices?status=draft \
  -H "Authorization: Bearer $KEY"

curl https://seedance3.eu/v1/invoices/inv_xxx \
  -H "Authorization: Bearer $KEY"
```

## 11. 常见错误

错误响应格式：

```json
{
  "detail": {
    "error": {
      "code": "model_not_enabled",
      "message": "model is not enabled for this customer"
    }
  }
}
```

| HTTP | Code | 说明 |
|---:|---|---|
| 400 | `invalid_content_block` / `invalid_content_role` | `content[]` 块类型或角色不被支持 |
| 400 | `unsupported_resolution` / `unsupported_ratio` / `unsupported_duration` | 参数超出当前模型能力 |
| 401 | `missing_auth` | API Key 缺失或无效 |
| 403 | `model_not_enabled` | 模型未对该客户启用 |
| 403 | `forbidden` | 无权访问该任务或素材 |
| 402 | `insufficient_balance` | 余额不足以预扣本次任务 |
| 404 | `not_found` | 任务或素材不存在 |
| 405 | `method_not_allowed` | 请求方法不支持 |
| 409 | `not_ready` | 视频任务尚未成功，暂不能读取内容 |
| 502 | `upstream_error` | 上游任务创建失败，响应只保留安全的请求标识和摘要 |
| 502 | `proxy_error` / `proxy_range_unsupported` | 视频代理读取失败或上游不支持本次 Range 读取 |

## 12. 安全边界

- 不要把 API Key 写入前端源码、共享文档、截图或工单。
- 客户响应只返回 Relay 域名的视频 URL。
- 任务列表、任务详情、视频内容和素材列表都按客户隔离。
- 公开文档不包含上游 URL、账号标签、内部路由、策略配置、密钥或内部运维备注。
- 生成结果默认不永久保存在 Relay 服务器；视频内容通过 Relay 代理读取上游结果。

### 12.1 BytePlus 兼容说明

Relay 的客户侧视频接口对齐 BytePlus Seedance 异步生成流程，但入口保持白标和计费隔离：

| BytePlus 原生能力 | Relay 客户侧接口 | 说明 |
|---|---|---|
| `POST /contents/generations/tasks` | `POST /v1/videos` | 提交异步视频生成任务，Relay 会做客户鉴权、模型启用校验、余额预扣和素材归属校验。 |
| 查询生成任务 | `GET /v1/videos/{id}` | 返回 Relay 任务状态、成本字段和 Relay 内容地址。 |
| 拉取生成内容 | `GET /v1/videos/{id}/content` | 只暴露 Relay 代理 URL，支持浏览器播放需要的 Range 请求。 |
| Files / 素材上传 | `POST /v1/uploads`、`POST /v1/uploads/from-url` | 不是 BytePlus Files 的 1:1 透传；Relay 返回 `url`、`asset_url` 和 `suggested_content_block`，客户直接复制到 `content[]`。 |

请求体中的 `content[]` 继续使用字节内容块风格。当前建议的角色如下：

| 内容块 | 常用 role | 说明 |
|---|---|---|
| `text` | 无 | 提示词。 |
| `image_url` | `first_frame`、`last_frame`、`reference_image` | 图片首尾帧或视觉参考。真人、人脸或身体参考优先使用上传后返回的 `asset://...`。 |
| `video_url` | `reference_video` | 视频参考。可使用 Relay 上传素材 URL 或已授权的外部 URL。 |
| `audio_url` | `reference_audio` | 音频参考必须配合文本或视觉内容使用；`audio_url 不能单独`作为唯一内容块提交。 |

兼容字段说明：

- `return_last_frame`：如上游模型支持，会随请求透传；不支持的模型可能忽略或由上游拒绝。
- `callback_url`：当前以轮询为主；客户应使用 `GET /v1/videos/{id}` 查询任务状态。需要服务端回调时请先与管理员确认账号开关。
- `extra_body`：作为高级扩展字段保留，Relay 会在安全边界内转发给上游；不要在其中放 API Key、内部账号或个人敏感信息。
- `seed`、`watermark`、`generate_audio`、`ratio`、`resolution`、`duration`：按模型能力校验，超出范围会在进入上游前返回参数错误。

如果 BytePlus 新增了 Seedance 视频生成参数，客户可以先通过 `extra_body` 临时携带；稳定参数会在 Relay 版本更新后补入一等字段。

## 13. 低审查内容生成规范

本节用于签约客户了解“低审查”内容的正确接入方式。这里的“低审查”不是无规则生成，也不是绕过平台风控；它表示 Relay 不额外增加客户侧内容开关，最终是否生成成功仍取决于已启用模型、客户账号配置、素材授权和上游返回结果。

当前独立客户 Endpoint 和素材注册请求统一使用 BytePlus `Moderation.Strategy=Skip`；Relay 不额外增加内容安全审核层。

### 13.1 适用范围

- 仅限成年人参与、成年人观看或成年人业务场景。
- 只使用客户自己拥有版权、肖像授权、商用授权或明确可使用的素材。
- 真人、脸部、身体参考素材应先通过素材库上传，并按账号归属生成 `asset://...` 引用。
- 生成请求仍然使用标准接口：`POST https://seedance3.eu/v1/videos`。

### 13.2 禁止范围

- 不得提交未成年人、疑似未成年人、校园未成年语境或无法确认年龄的真人素材。
- 不得提交非自愿、偷拍、胁迫、泄露隐私、报复性传播、深度伪造冒充真实个人的内容。
- 不得提交违法交易、暴力胁迫、性剥削、人口贩卖或其他违法场景。
- 不得把他人照片、公开视频、社媒素材当作已授权素材使用。

### 13.3 推荐流程

1. 使用客户自己的 Relay API Key 上传参考素材。
2. 在素材库确认返回 `asset_url`，优先使用 `asset://...`，不要长期依赖临时公网图片 URL。
3. 用 `POST /v1/videos/estimate` 先预估成本。
4. 用 `POST /v1/videos` 创建任务。
5. 用 `GET /v1/videos/{id}` 查询状态。
6. 用 `GET /v1/videos/{id}/content` 下载或播放结果。

### 13.4 上传授权素材

```bash
export KEY="sk_your_relay_api_key"

curl https://seedance3.eu/v1/uploads \
  -X POST \
  -H "Authorization: Bearer $KEY" \
  -F "file=@authorized-adult-reference.jpg;type=image/jpeg" \
  -F "face_allowlist=true" \
  -F "face_asset_label=authorized-adult-reference"
```

上传成功后，优先复制响应里的：

```json
{
  "asset_id": "asset_xxx",
  "asset_url": "asset://asset_xxx",
  "suggested_content_block": {
    "type": "image_url",
    "image_url": {"url": "asset://asset_xxx"},
    "role": "reference_image"
  }
}
```

### 13.5 生成请求示例

```bash
curl https://seedance3.eu/v1/videos \
  -X POST \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "dreamina-seedance-2-0-260128",
    "content": [
      {
        "type": "text",
        "text": "adult-only cinematic editorial video, private studio setting, consenting adult performer, soft lighting, no minors, no public location"
      },
      {
        "type": "image_url",
        "image_url": {"url": "asset://asset_xxx"},
        "role": "reference_image"
      }
    ],
    "resolution": "720p",
    "ratio": "16:9",
    "duration": 5,
    "generate_audio": false,
    "extra_body": {
      "real_person_mode": true,
      "audience": "adult"
    }
  }'
```

### 13.6 素材与账号隔离

- 客户只能看到、删除、引用自己账号上传的素材。
- 其他客户的 `asset://...` 即使被复制进请求，也会被 Relay 拒绝。
- Relay 不向客户暴露 BytePlus project、endpoint、asset group、endpoint API key或内部平台密钥。
- 生成结果默认仍走 Relay 白标地址，例如 `https://seedance3.eu/v1/videos/{id}/content`。

### 13.7 失败处理

如果上游拒绝生成，Relay 会返回安全摘要，不会暴露上游密钥或原始内部 URL。客户可以调整素材授权、提示词、模型或联系管理员检查该账号可用模型与 endpoint 配置。
