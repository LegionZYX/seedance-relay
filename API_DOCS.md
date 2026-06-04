# Example Video Relay API

面向签约客户的 Seedance 视频生成 API 文档。

Base URL:

```text
https://video.example.com
```

## 1. 快速开始

完整流程是：提交任务、轮询状态、通过 Relay URL 播放或下载视频。

```bash
export KEY="sk_your_relay_api_key"

curl https://video.example.com/v1/videos \
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
  "estimated_cost_usd": 1.188,
  "held_usd": 1.3068,
  "price_multiplier": 1.0
}
```

轮询任务：

```bash
curl https://video.example.com/v1/videos/vid_a3f9c1b2d8e4f7a6 \
  -H "Authorization: Bearer $KEY"
```

任务成功后，响应里的 `video_url` 只会是 Relay 域名：

```json
{
  "id": "vid_a3f9c1b2d8e4f7a6",
  "status": "succeeded",
  "video_url": "https://video.example.com/v1/videos/vid_a3f9c1b2d8e4f7a6/content"
}
```

播放或下载：

```bash
curl -L -o video.mp4 \
  https://video.example.com/v1/videos/vid_a3f9c1b2d8e4f7a6/content \
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
curl https://video.example.com/v1/models
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
curl https://video.example.com/v1/pricing \
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
        "price_no_video_ref_usd_per_1k": 0.01092,
        "price_with_video_ref_usd_per_1k": 0.006708
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
curl https://video.example.com/v1/me \
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
curl https://video.example.com/v1/videos/estimate \
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
  "estimated_cost_usd": 1.188,
  "max_cost_usd": 1.3068,
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
  {"type": "image_url", "image_url": {"url": "https://video.example.com/uploads/upl_123.jpg"}, "role": "first_frame"},
  {"type": "video_url", "video_url": {"url": "https://video.example.com/uploads/upl_456.mp4"}, "role": "reference_video"}
]
```

Relay 会校验结构、模型能力、素材归属和余额。Relay 不做额外的提示词内容限制；上游生成是否成功仍取决于上游模型和账号配置。

## 7. 查询任务

查询单个任务：

```bash
curl https://video.example.com/v1/videos/vid_a3f9c1b2d8e4f7a6 \
  -H "Authorization: Bearer $KEY"
```

列出自己的任务：

```bash
curl "https://video.example.com/v1/videos?limit=20&offset=0&status=succeeded" \
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
curl -I https://video.example.com/v1/videos/vid_a3f9c1b2d8e4f7a6/content \
  -H "Authorization: Bearer $KEY"

curl https://video.example.com/v1/videos/vid_a3f9c1b2d8e4f7a6/content \
  -H "Authorization: Bearer $KEY" \
  -H "Range: bytes=0-1048575" \
  -o part.mp4
```

正常情况下，`Range` 请求会返回 `206 Partial Content`、`Content-Range`、`Content-Length` 和视频 `Content-Type`。

## 9. 上传素材

上传本地文件：

```bash
curl https://video.example.com/v1/uploads \
  -X POST \
  -H "Authorization: Bearer $KEY" \
  -F "file=@reference.jpg;type=image/jpeg"
```

响应示例：

```json
{
  "id": "upl_a1b2c3d4e5f6a7b8",
  "url": "https://video.example.com/uploads/2026/06/02/upl_a1b2c3d4e5f6a7b8.jpg",
  "content_type": "image/jpeg",
  "size_bytes": 123456,
  "purpose": "image",
  "suggested_content_block": {
    "type": "image_url",
    "image_url": {
      "url": "https://video.example.com/uploads/2026/06/02/upl_a1b2c3d4e5f6a7b8.jpg"
    },
    "role": "first_frame"
  }
}
```

从 URL 导入素材：

```bash
curl https://video.example.com/v1/uploads/from-url \
  -X POST \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/reference.jpg", "purpose": "image"}'
```

## 10. 账号自助

修改登录密码：

```bash
curl https://video.example.com/auth/change-password \
  -X POST \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"current_password": "old-password", "new_password": "new-secure-password"}'
```

轮换自己的 Relay API Key：

```bash
curl https://video.example.com/v1/me/api-key/rotate \
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
