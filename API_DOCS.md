# Example Video Relay Video API 开发文档

> AI 视频生成 API · 用文本/图像/视频/音频生成高质量视频
> Base URL: **`https://video.example.com`**
> 版本: v1

---

## 目录

- [快速开始（5 分钟跑通）](#快速开始5-分钟跑通)
- [认证](#认证)
- [模型与价格](#模型与价格)
- [上传中转站（白名单）](#上传中转站白名单)
- [生成视频（核心 API）](#生成视频核心-api)
- [查询任务状态](#查询任务状态)
- [下载视频](#下载视频)
- [列出我的任务](#列出我的任务)
- [取消 / 删除任务](#取消--删除任务)
- [账户信息与余额](#账户信息与余额)
- [提交前预估费用](#提交前预估费用可选)
- [完整请求体参考](#完整请求体参考)
- [错误码](#错误码)
- [SDK 与代码示例](#sdk-与代码示例)
- [限制与注意事项](#限制与注意事项)
- [FAQ](#faq)

---

## 快速开始（5 分钟跑通）

整个流程就 3 步：**提交 → 轮询 → 下载**。

```bash
# 你的 API key (admin 发给你的)
export KEY="sk-xxxxxxxxxxxxxxxxxxxxxxxx"

# 1) 提交任务
curl https://video.example.com/v1/videos -X POST \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "video-pro",
    "content": [{"type":"text","text":"a red lobster wearing a chef hat dances on a neon Tokyo street, cinematic"}],
    "resolution": "720p",
    "duration": 5
  }'

# 返回:
# { "id": "vid_a3f9c1b2d8e4f7a6", "status": "queued", "estimated_cost_usd": 0.91, "held_usd": 1.31 }

# 2) 轮询状态 (一般 2-10 分钟完成)
curl https://video.example.com/v1/videos/vid_a3f9c1b2d8e4f7a6 \
  -H "Authorization: Bearer $KEY"

# 返回 "status": "succeeded" 后:
# { "video_url": "https://video.example.com/v1/videos/vid_a3f9c1b2d8e4f7a6/content", ... }

# 3) 下载视频
curl -o video.mp4 https://video.example.com/v1/videos/vid_a3f9c1b2d8e4f7a6/content \
  -H "Authorization: Bearer $KEY"
```

---

## 认证

所有 `/v1/*` 端点要求 HTTP header：

```
Authorization: Bearer sk-xxxxxxxxxxxxxxxxxxxxxxxx
```

API key 由平台管理员发放（格式 `sk-` 开头）。**请妥善保管，泄露会被冒用扣费**。

如果需要在网页上让你的最终用户操作，也可以用 cookie session 登录（见 [网页登录](#网页登录可选)）。

---

## 模型与价格

我们提供 **7 个视频生成模型**，按 token 计费：

| 模型 ID | 适用场景 | 推荐场景 |
|---|---|---|
| **`video-pro`** | 最高质量 + 支持原生音频 | 商用宣传片、产品演示 |
| **`video-pro-fast`** | Pro 版本的快速档 | 同质量更快出片 |
| **`video-1.5-pro`** | 中端 + 草稿模式 | 先出草稿再确认正片，省钱 |
| **`video-1080p`** | 1080p 专用 | 高清横竖屏 |
| **`video-720p`** | 720p 快速档 | 大批量制作 |
| **`video-lite`** | 文生视频极速版 | 纯文本 → 短视频 |
| **`video-lite-i2v`** | 图生视频极速版 | 单图驱动 → 短视频 |

获取实时价格表：

```bash
curl https://video.example.com/v1/pricing

# 带客户 key 时返回该客户自己的有效价格
curl https://video.example.com/v1/pricing \
  -H "Authorization: Bearer $KEY"
```

返回每个模型 / 每个分辨率 / 是否含视频参考 的 USD 单价 + tokens-per-second 估算：

```json
{
  "markup_pct": 0.3,
  "pricing_scope": "global",
  "pricing": {
    "video-pro": {
      "480p":  { "tokens_per_second": 10128, "price_no_video_ref_usd_per_1k": 0.01092, "price_with_video_ref_usd_per_1k": 0.006708 },
      "720p":  { "tokens_per_second": 21780, "price_no_video_ref_usd_per_1k": 0.01092, "price_with_video_ref_usd_per_1k": 0.006708 },
      "1080p": { "tokens_per_second": 49005, "price_no_video_ref_usd_per_1k": 0.012012, "price_with_video_ref_usd_per_1k": 0.007332 }
    },
    "...": "..."
  },
  "buffer_pct": 0.10
}
```

`pricing_scope=customer` 表示该客户使用后台单独配置的 `markup_pct`；`pricing_scope=global` 表示使用服务器全局 `MARKUP_PCT`。

**价格规则**：

```
tokens   = tokens_per_second[resolution] × duration
cost_usd = tokens / 1000 × price_per_1k_tokens
max_cost = cost_usd × (1 + 0.10)   ← 提交时实际预扣的金额（含 10% 缓冲）
```

**含视频参考 vs 无视频参考**：当 `content[]` 数组中包含 `type=video_url` 的输入，按 `price_with_video_ref_usd_per_1k` 档计费（**便宜约 40%**）。否则按 `price_no_video_ref_usd_per_1k`。

**典型成本对照**：

| 配置 | 实际 USD |
|---|---|
| `video-pro` / 480p / 5 秒 / 文本 | ~$0.55 |
| `video-pro` / 720p / 5 秒 / 文本 | ~$1.19 |
| `video-pro` / 1080p / 5 秒 / 文本 | ~$2.62 |
| `video-pro` / 720p / 5 秒 / 含视频参考 | ~$0.73 |
| `video-pro` / 1080p / 5 秒 / 含视频参考 | ~$1.60 |

---

## 上传中转站（白名单）

```
POST /v1/uploads
```

用于把本地图片、视频或音频先上传到你的白标域名，返回 Seedance 可拉取的公网 URL。所有上传都需要 `Authorization: Bearer sk-xxxx`。

```bash
curl -X POST https://video.example.com/v1/uploads \
  -H "Authorization: Bearer $KEY" \
  -F "file=@portrait.jpg;type=image/jpeg"
```

响应：

```json
{
  "id": "upl_a1b2c3d4e5f6a7b8",
  "url": "https://video.example.com/uploads/2026/06/02/upl_a1b2c3d4e5f6a7b8.jpg",
  "object_key": "uploads/2026/06/02/upl_a1b2c3d4e5f6a7b8.jpg",
  "content_type": "image/jpeg",
  "size_bytes": 123456,
  "purpose": "image",
  "suggested_content_block": {
    "type": "image_url",
    "image_url": { "url": "https://video.example.com/uploads/2026/06/02/upl_a1b2c3d4e5f6a7b8.jpg" },
    "role": "first_frame"
  }
}
```

白名单：

| 类型 | MIME | 默认上限 |
|---|---|---|
| 图片 | `image/jpeg`, `image/png`, `image/webp` | 10 MB |
| 视频 | `video/mp4`, `video/quicktime` | 50 MB |
| 音频 | `audio/mpeg`, `audio/wav`, `audio/x-wav` | 15 MB |

返回的 `suggested_content_block` 可以直接放进 `POST /v1/videos` 的 `content[]`。

如果客户已经有可公开访问的素材 URL，不想再把文件传到 Relay，可以只登记 URL：

```bash
curl -X POST https://video.example.com/v1/uploads/from-url \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://cdn.example.com/portrait.jpg",
    "content_type": "image/jpeg",
    "original_filename": "portrait.jpg"
  }'
```

`from-url` 不会下载或探测客户 URL，只根据 `content_type` 或 URL 后缀判断素材类型，然后写入当前 API key 自己的素材账本。返回结构与 `/v1/uploads` 一致，`object_key` 会是 `external/upl_xxx`。

需要一行参数触发人脸白名单时：

```bash
curl -X POST https://video.example.com/v1/uploads/from-url \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://cdn.example.com/portrait.jpg",
    "content_type": "image/jpeg",
    "face_allowlist": true,
    "face_asset_label": "actor-a"
  }'
```

查看当前客户自己的上传素材：

```bash
curl https://video.example.com/v1/uploads \
  -H "Authorization: Bearer $KEY"

curl https://video.example.com/v1/uploads/upl_a1b2c3d4e5f6a7b8 \
  -H "Authorization: Bearer $KEY"
```

列表响应：

```json
{
  "data": [
    {
      "id": "upl_a1b2c3d4e5f6a7b8",
      "url": "https://video.example.com/uploads/2026/06/02/upl_a1b2c3d4e5f6a7b8.jpg",
      "asset_url": "asset://asset-xxxx",
      "purpose": "image",
      "original_filename": "portrait.jpg",
      "face_asset_whitelisted": true,
      "suggested_content_block": {
        "type": "image_url",
        "image_url": { "url": "asset://asset-xxxx" },
        "role": "reference_image"
      }
    }
  ],
  "limit": 50,
  "offset": 0,
  "total": 1
}
```

这两个查询接口不会接受客户传入 `user_id`；服务端只根据 `Authorization: Bearer sk-xxxx` 找当前用户，并只返回该用户自己的上传记录。访问其他客户的素材 ID 会返回 `404 upload_not_found`。

平台管理员排查素材时走后台接口：

```bash
curl "https://video.example.com/admin/uploads?user_id=u_xxx&purpose=image" \
  -H "X-Admin-Key: $ADMIN_KEY"
```

`/admin/uploads` 会返回全平台上传账本、素材归属客户、白名单状态和聚合统计；`/admin/users/{id}` 也会带最近 20 个素材。

平台 IAM / 素材注册状态可以看 `/admin/config`；它只返回是否配置，不返回 AK/SK/GroupId 明文。

如果平台管理员打开了服务端自动注册开关，响应会额外包含：

```json
{
  "asset_id": "asset-xxxx",
  "asset_url": "asset://asset-xxxx",
  "asset_status": "created"
}
```

此时 `suggested_content_block` 会自动使用 `asset://...`，用户端仍然不需要任何 AK/SK/GroupId。

### 人脸 reference 白名单

如果平台启用了人脸白名单，`role=reference_image` / `role=reference_video` 的人脸或角色参考素材必须使用服务端批准的 `asset://...`：

```json
{
  "type": "image_url",
  "image_url": { "url": "asset://asset-approved-id" },
  "role": "reference_image"
}
```

普通公网 URL 会被拒绝，未登记的 `asset://...` 也会被拒绝。白名单由管理员在服务端维护，用户不需要 AK/SK/GroupId。

平台侧最简配置：

```bash
BYTEPLUS_ACCESS_KEY_ID=your-server-ak
BYTEPLUS_ACCESS_KEY_SECRET=your-server-sk
MODELARK_ASSET_AUTO_CREATE_GROUP=true
FACE_ASSET_SELF_SERVICE=true
```

`MODELARK_ASSET_GROUP_ID` 可以留空。Relay 会在第一次素材注册时调用 Python 素材接口 `CreateAssetGroup`，把返回的 GroupId 缓存到 SQLite；后续客户上传不需要再次建组。

如果平台启用了自助人脸白名单，客户可以在上传时多传一行参数：

```bash
curl -X POST https://video.example.com/v1/uploads \
  -H "Authorization: Bearer $KEY" \
  -F "face_allowlist=true" \
  -F "face_asset_label=actor-a" \
  -F "file=@portrait.jpg;type=image/jpeg"
```

响应会包含 `asset_url` 和 `face_asset_whitelisted=true`，并且 `suggested_content_block` 会自动使用这个 `asset://...`。图片上传会返回 `role=reference_image`，可直接放进 `POST /v1/videos`。

如果没有打开自动注册，管理员也可以在服务器上手动把 reference video 注册成 provider 的 `asset://...`：

```bash
export BYTEPLUS_ACCESS_KEY_ID="your-access-key-id"
export BYTEPLUS_ACCESS_KEY_SECRET="your-secret-access-key"
export MODELARK_ASSET_GROUP_ID="your-asset-group-id"

python create_asset_white_label.py create \
  --url "https://video.example.com/uploads/2026/06/02/upl_xxx.mp4" \
  --asset-type Video \
  --skip-moderation

python create_asset_white_label.py wait --asset-id "asset-xxxx"
```

如果还没有 GroupId，可以先手动创建：

```bash
python create_asset_white_label.py create-group \
  --name "relay-face-assets" \
  --description "Relay self-service face asset whitelist"
```

---

## 生成视频（核心 API）

```
POST /v1/videos
```

### 最简请求（纯文本）

```bash
curl https://video.example.com/v1/videos -X POST \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "video-pro",
    "content": [{"type": "text", "text": "A cat playing piano in a jazz bar"}],
    "resolution": "720p",
    "duration": 5
  }'
```

真人素材一键白名单开关：

```bash
curl https://video.example.com/v1/videos -X POST \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "video-pro",
    "content": [
      {"type": "text", "text": "A portrait walks through a small street"},
      {
        "type": "image_url",
        "image_url": {"url": "https://cdn.example.com/portrait.jpg"},
        "role": "reference_image"
      }
    ],
    "ratio": "16:9",
    "duration": 5,
    "extra_body": {"real_person_mode": true}
  }'
```

`extra_body.real_person_mode=true` 是 Relay 自己识别的开关。请求主体仍然保持字节原生 `content[]`：Relay 会用服务端 IAM AK/SK 自动把图片/视频参考素材注册成 `asset://...`，写入当前客户的素材账本，再替换进上游生成请求。客户仍然只需要 `Authorization: Bearer sk-xxxx`，不需要也拿不到 AK/SK。

如果客户已经通过 `/v1/uploads` 或 `/v1/uploads/from-url` 上传并白名单过同一个 URL，Relay 会复用已有 `asset://...`，不会重复注册。客户只能使用自己账号名下的上传素材 ID；其他客户上传产生的 `asset://...` 即使被复制，也会被 Relay 拒绝。

### 响应

```json
{
  "id": "vid_a3f9c1b2d8e4f7a6",
  "model": "video-pro",
  "status": "queued",
  "estimated_cost_usd": 1.1888,
  "held_usd": 1.3077,
  "created_at": 1778415123
}
```

| 字段 | 含义 |
|---|---|
| `id` | 任务 ID（`vid_` 开头），用于查询、下载、取消 |
| `status` | 初始一律 `queued`，后续通过查询 API 看进度 |
| `estimated_cost_usd` | 预估真实成本（最终结算大概率 ≤ 这个） |
| `held_usd` | **本次从余额预扣的金额**（含 10% 缓冲）。任务完成后多扣的会退回。|
| `created_at` | Unix 时间戳（秒） |

### 完整参数

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `model` | string | 必填 | 见 [模型列表](#模型与价格) |
| `content` | object[] | 必填 | 输入素材数组，见 [content 块](#content-块结构) |
| `resolution` | `480p` / `720p` / `1080p` | `720p` | 输出视频分辨率 |
| `ratio` | string | `16:9` | 画面比例，可选 `16:9` `9:16` `1:1` `4:3` `3:4` `21:9` `adaptive` |
| `duration` | integer (秒) | `5` | 视频时长，范围 `2-15` |
| `seed` | integer | 随机 | 控制生成随机性，传同一个 seed 重现相同结果 |
| `watermark` | boolean | `false` | 是否打官方水印 |
| `generate_audio` | boolean | 自动 | 是否生成同步音频（仅 `video-pro` 系列支持） |

### content 块结构

`content[]` 数组可包含以下类型的块（按需组合）：

#### 文本块
```json
{ "type": "text", "text": "A serene mountain landscape at sunset, cinematic" }
```

#### 图像块
```json
{
  "type": "image_url",
  "image_url": { "url": "https://your-cdn.com/reference.jpg" },
  "role": "first_frame"
}
```

`role` 可选值：
- `first_frame` — 用作视频首帧
- `last_frame` — 用作视频末帧（要配合首帧使用）
- `reference_image` — 多张图作为风格/主体参考

`image_url.url` 也支持 **base64**: `data:image/png;base64,...`

#### 视频块（仅 `video-pro` / `video-pro-fast` 支持）
```json
{
  "type": "video_url",
  "video_url": { "url": "https://your-cdn.com/reference.mp4" },
  "role": "reference_video"
}
```

带视频块时，本笔任务自动按 `with_video_ref` 档计费（更便宜）。

#### 音频块（仅 `video-pro` / `video-pro-fast` 支持）
```json
{
  "type": "audio_url",
  "audio_url": { "url": "https://your-cdn.com/voice.mp3" },
  "role": "reference_audio"
}
```

### 输入组合速查

| 想要的效果 | content[] 怎么搭 |
|---|---|
| 纯文本生成视频 | `[text]` |
| 一张图驱动视频（首帧） | `[text, image(first_frame)]` |
| 首尾帧渐变 | `[text, image(first_frame), image(last_frame)]` |
| 多图参考（构图/风格） | `[text, image(reference_image), image(reference_image), ...]` |
| 视频转风格 | `[text, video(reference_video)]` |
| 唇形同步 | `[text, image, audio]` |
| 全要素（图+视频+音频） | `[text, image, video, audio]` |

---

## 查询任务状态

```
GET /v1/videos/{vid}
```

```bash
curl https://video.example.com/v1/videos/vid_a3f9c1b2d8e4f7a6 \
  -H "Authorization: Bearer $KEY"
```

```json
{
  "id": "vid_a3f9c1b2d8e4f7a6",
  "model": "video-pro",
  "status": "succeeded",
  "resolution": "720p",
  "duration": 5,
  "completion_tokens": 108900,
  "estimated_cost_usd": 1.1888,
  "actual_cost_usd": 1.1896,
  "prompt_text": "A cat playing piano in a jazz bar",
  "video_url": "https://video.example.com/v1/videos/vid_a3f9c1b2d8e4f7a6/content",
  "created_at": 1778415123,
  "updated_at": 1778415790
}
```

### 状态枚举

| status | 含义 | 终态？ |
|---|---|---|
| `queued` | 排队中（系统正在分配资源） | 否 |
| `running` | 生成中 | 否 |
| `succeeded` | 已完成，可下载 | ✅ |
| `failed` | 失败（看 `error.message`），**不计费** | ✅ |
| `cancelled` | 已取消（`queued` 阶段被 DELETE），**不计费** | ✅ |
| `expired` | 超时作废，**不计费** | ✅ |

### 推荐轮询策略

```python
import time, requests
KEY = "sk-xxx"
def wait_for(vid, max_wait=1800):
    deadline = time.time() + max_wait
    while time.time() < deadline:
        r = requests.get(f"https://video.example.com/v1/videos/{vid}",
                         headers={"Authorization": f"Bearer {KEY}"})
        info = r.json()
        if info["status"] in ("succeeded", "failed", "cancelled", "expired"):
            return info
        time.sleep(15)   # 15 秒查一次足够
    raise TimeoutError("Task didn't finish within 30 min")
```

⚠️ **不要轮询太频繁**（如每秒一次），15-30 秒一次足以。

---

## 下载视频

```
GET /v1/videos/{vid}/content
```

succeeded 状态的任务直接拉：

```bash
curl -o video.mp4 https://video.example.com/v1/videos/vid_a3f9c1b2d8e4f7a6/content \
  -H "Authorization: Bearer $KEY"
```

- 返回 `video/mp4` 二进制流
- **下载链接永不过期**（任务记录长期保留）
- 已经 succeeded 的任务，无论多久后都能下载
- 支持 HTTP Range 请求（断点续传 / 视频拖动播放）

也可以在浏览器 `<video>` 标签里直接播放：

```html
<video controls
       src="https://video.example.com/v1/videos/vid_xxx/content?token=...">
</video>
```

⚠️ 浏览器嵌入会受 Bearer auth 限制，需要服务端代理或在 query string 带 token（暂不支持，推荐用后端拉视频后转给前端）。

---

## 列出我的任务

```
GET /v1/videos?limit=20&offset=0&status=succeeded
```

```bash
curl "https://video.example.com/v1/videos?limit=10" \
  -H "Authorization: Bearer $KEY"
```

```json
{
  "data": [
    { "id": "vid_xxx", "status": "succeeded", "model": "video-pro", "actual_cost_usd": 1.19, "video_url": "...", ... },
    ...
  ],
  "total": 47,
  "limit": 10,
  "offset": 0
}
```

| Query | 默认 | 说明 |
|---|---|---|
| `limit` | 20 | 每页条数（最大 100） |
| `offset` | 0 | 分页偏移 |
| `status` | – | 按状态筛选（`queued/running/succeeded/failed/cancelled/expired`） |

---

## 取消 / 删除任务

```
DELETE /v1/videos/{vid}
```

```bash
curl -X DELETE https://video.example.com/v1/videos/vid_a3f9c1b2d8e4f7a6 \
  -H "Authorization: Bearer $KEY"
```

| 任务当前状态 | DELETE 行为 |
|---|---|
| `queued` | 取消任务，**预扣金额全额退回** |
| `running` | **不允许**取消（任务已在算力上运行） |
| `succeeded` / `failed` / `expired` | 删除你这边的记录 |
| `cancelled` | 不允许（已经取消过了） |

返回：

```json
{ "id": "vid_a3f9c1b2d8e4f7a6", "status": "deleted" }
```

---

## 账户信息与余额

```
GET /v1/me
```

```bash
curl https://video.example.com/v1/me \
  -H "Authorization: Bearer $KEY"
```

```json
{
  "id": "u_xxxxxxxx",
  "email": "you@example.com",
  "api_key": "sk-xxxxxxxxxxxxxxxxxxxxxxxx",
  "available_usd":     8.81,
  "held_usd":          1.31,
  "total_usd":        10.12,
  "pending_tasks":     1,
  "completed_tasks":  47,
  "lifetime_spent_usd": 56.34,
  "balance_usd":       8.81
}
```

| 字段 | 说明 |
|---|---|
| **`available_usd`** | 可立即使用的余额（提交新任务时判断这个值） |
| **`held_usd`** | 当前**进行中任务**的预扣总额（任务完成后会退多扣的部分） |
| **`total_usd`** | `available_usd + held_usd`（充值后剩多少） |
| `pending_tasks` | 进行中（尚未结算）任务数 |
| `completed_tasks` | 已结算任务数 |
| `lifetime_spent_usd` | 累计实际扣费总额 |
| `balance_usd` | = `available_usd`（兼容字段） |

### 提交任务前需要保证 `available_usd ≥ max_cost`

任务一旦提交：
- 立即从 `available_usd` 扣 `held_usd`
- 任务终态时：
  - **succeeded** → 按实际成本结算，多扣的退回到 `available_usd`
  - **failed / cancelled / expired** → `held_usd` 全额退回 `available_usd`

---

## 提交前预估费用（可选）

如果你想在用户点击"生成"前显示**精确的预扣金额**：

```
POST /v1/videos/estimate
```

参数跟 `POST /v1/videos` **完全一样**，但**不会真的创建任务**，不消耗任何配额。

```bash
curl https://video.example.com/v1/videos/estimate -X POST \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{
    "model": "video-pro",
    "resolution": "1080p",
    "duration": 8,
    "content": [{"type":"text","text":"a cat"}]
  }'
```

```json
{
  "model": "video-pro",
  "resolution": "1080p",
  "duration": 8,
  "has_video_ref": false,
  "estimated_tokens": 392040,
  "estimated_cost_usd": 4.7092,
  "max_cost_usd": 5.1801,
  "balance_usd": 1.4470,
  "can_afford": false,
  "shortage_usd": 3.7331
}
```

典型使用流程：

```python
est = post("/v1/videos/estimate", params)
if not est["can_afford"]:
    return show_error(f"余额不足，需要再充 ${est['shortage_usd']}")
if est["max_cost_usd"] > USER_DEFINED_LIMIT:
    require_user_confirmation(f"本笔最多 ${est['max_cost_usd']:.2f}, 确认？")
post("/v1/videos", params)  # 真提交
```

---

## 完整请求体参考

```jsonc
{
  // 必填字段
  "model": "video-pro",
  "content": [
    { "type": "text", "text": "Your prompt here" },
    { "type": "image_url", "image_url": { "url": "https://..." }, "role": "first_frame" },
    { "type": "video_url", "video_url": { "url": "https://..." }, "role": "reference_video" },
    { "type": "audio_url", "audio_url": { "url": "https://..." }, "role": "reference_audio" }
  ],

  // 可选: 视频规格
  "resolution": "720p",       // 480p | 720p | 1080p
  "ratio": "16:9",            // 16:9 | 9:16 | 1:1 | 4:3 | 3:4 | 21:9 | adaptive
  "duration": 5,              // 秒, 范围 2-15

  // 可选: 生成控制
  "seed": 42,                 // -1 或不传 = 随机
  "watermark": false,         // 加官方水印
  "generate_audio": true      // 自动生成同步音频 (仅 video-pro 系列)
}
```

### 媒体输入限制

| 输入类型 | 格式 | 单个大小 | 总请求体 |
|---|---|---|---|
| **图像** | jpeg / png / webp / bmp / tiff / gif / heic / heif | ≤ 30 MB | ≤ 64 MB |
| **视频** | mp4 / mov (H.264/H.265/AVC/HEVC) | ≤ 50 MB | ≤ 64 MB |
| **音频** | wav / mp3 | ≤ 15 MB | ≤ 64 MB |

- 图像分辨率：宽高 300-6000 px，比例 0.4-2.5
- 视频分辨率：480p / 720p / 1080p，时长 2-15 秒，FPS 24-60
- 音频时长：单段 2-15 秒，总长 ≤ 15 秒

**Base64 编码大文件不推荐**，请用公网 URL。

---

## 错误码

所有错误统一格式：

```json
{
  "detail": {
    "error": {
      "code": "error_code",
      "message": "Human-readable message"
    }
  }
}
```

| HTTP | code | 含义 / 处理 |
|---|---|---|
| **401** | `missing_auth` | 没传 `Authorization: Bearer ...` 或 key 失效 |
| **401** | `invalid_credentials` | 登录密码错误（仅网页登录） |
| **402** | `insufficient_balance` | 余额不足，response 含 `needed_usd / balance_usd / shortage_usd` |
| **400** | `invalid_model` | 模型名错，response 含 `available` 模型列表 |
| **400** | – | 参数无效（如 `duration` 超出范围） |
| **404** | `not_found` | 任务 ID 不存在 / 不属于你 |
| **409** | `cannot_delete` | 试图删除 `running` 状态的任务 |
| **409** | `not_ready` | 任务还没 succeeded，无法下载 |
| **502** | `upstream_error` | 后端服务暂时异常，请稍后重试 |
| **503** | `no_upstream_key` | 服务配置异常，联系管理员 |

### 402 余额不足示例

```json
{
  "detail": {
    "error": {
      "code": "insufficient_balance",
      "message": "This request needs $1.3081 reserved, but balance is $0.5000",
      "needed_usd": 1.3081,
      "balance_usd": 0.5000
    }
  }
}
```

客户端建议：拿到 402 后弹窗提示用户充值，附带 `needed_usd` 信息。

---

## SDK 与代码示例

### Python

```python
import time
import requests

BASE = "https://video.example.com"
KEY  = "sk-xxxxxxxxxxxxxxxxxxxxxxxx"
HEADERS = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

def generate_video(prompt, model="video-pro", resolution="720p", duration=5, **opts):
    body = {"model": model, "resolution": resolution, "duration": duration,
            "content": [{"type": "text", "text": prompt}], **opts}
    r = requests.post(f"{BASE}/v1/videos", json=body, headers=HEADERS)
    if r.status_code == 402:
        err = r.json()["detail"]["error"]
        raise RuntimeError(f"余额不足，需要 ${err['needed_usd']}")
    r.raise_for_status()
    return r.json()

def wait_for(vid, interval=15, timeout=1800):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(f"{BASE}/v1/videos/{vid}", headers=HEADERS)
        r.raise_for_status()
        info = r.json()
        if info["status"] in ("succeeded", "failed", "cancelled", "expired"):
            return info
        time.sleep(interval)
    raise TimeoutError(f"Task {vid} not finished in {timeout}s")

def download(vid, path):
    with requests.get(f"{BASE}/v1/videos/{vid}/content", headers=HEADERS, stream=True) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)

# 用法
task = generate_video("a red lobster dancing in Tokyo", resolution="720p", duration=5)
print(f"任务 {task['id']} 已提交, 预扣 ${task['held_usd']}")

info = wait_for(task["id"])
if info["status"] == "succeeded":
    download(info["id"], "output.mp4")
    print(f"成功! 实际花费 ${info['actual_cost_usd']}")
else:
    print(f"任务 {info['status']}: {info.get('error', {}).get('message', '')}")
```

### JavaScript (Node.js / fetch)

```javascript
const BASE = "https://video.example.com";
const KEY  = "sk-xxxxxxxxxxxxxxxxxxxxxxxx";

async function api(path, opts = {}) {
  const r = await fetch(`${BASE}${path}`, {
    headers: { "Authorization": `Bearer ${KEY}`, "Content-Type": "application/json" },
    ...opts,
  });
  if (!r.ok) throw Object.assign(new Error(r.statusText), { status: r.status, body: await r.json() });
  return r.json();
}

async function generateAndDownload(prompt) {
  const task = await api("/v1/videos", {
    method: "POST",
    body: JSON.stringify({
      model: "video-pro",
      content: [{ type: "text", text: prompt }],
      resolution: "720p",
      duration: 5,
    }),
  });
  console.log(`Task ${task.id} queued, held $${task.held_usd}`);

  let info;
  while (true) {
    info = await api(`/v1/videos/${task.id}`);
    if (["succeeded", "failed", "cancelled", "expired"].includes(info.status)) break;
    await new Promise(r => setTimeout(r, 15000));
  }
  if (info.status !== "succeeded") throw new Error(`Task ${info.status}`);

  const r = await fetch(`${BASE}/v1/videos/${info.id}/content`,
                       { headers: { "Authorization": `Bearer ${KEY}` } });
  const buf = await r.arrayBuffer();
  require("fs").writeFileSync("output.mp4", Buffer.from(buf));
  console.log(`Done! Actual cost $${info.actual_cost_usd}`);
}

generateAndDownload("a cat dancing in a jazz bar");
```

### Shell / curl

```bash
#!/usr/bin/env bash
set -e
KEY="sk-xxxxxxxxxxxxxxxxxxxxxxxx"
BASE="https://video.example.com"

# 1) 提交
VID=$(curl -sS $BASE/v1/videos -X POST \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"video-pro","content":[{"type":"text","text":"a panda chef cooking ramen"}],"resolution":"720p","duration":5}' \
  | jq -r .id)
echo "Task: $VID"

# 2) 轮询
while true; do
  STATUS=$(curl -sS $BASE/v1/videos/$VID -H "Authorization: Bearer $KEY" | jq -r .status)
  echo "Status: $STATUS"
  case "$STATUS" in
    succeeded) break ;;
    failed|cancelled|expired) echo "Task $STATUS, exit."; exit 1 ;;
  esac
  sleep 15
done

# 3) 下载
curl -o output.mp4 $BASE/v1/videos/$VID/content -H "Authorization: Bearer $KEY"
echo "✅ output.mp4"
```

---

## 网页登录（可选）

如果不想用 API key 而是用浏览器登录（适合最终用户操作 UI）：

```bash
# 登录, 拿到 cookie
curl -c cookie.txt https://video.example.com/auth/login -X POST \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"your-password"}'

# 之后所有请求带 cookie 即可, 不用 Bearer
curl -b cookie.txt https://video.example.com/v1/me
```

或者直接打开 https://video.example.com/ 网页登录，会跳到 `/app/`。

---

## 限制与注意事项

### 速率与配额
- **没有官方 RPM/TPM 限制**，但**单笔最大 1080p × 15s** 视频耗时较长（≤ 30 分钟）
- 单账号同时**最多 5 个并行任务**（防止系统过载）。超过会被排队
- 提交超过 3 个 1080p 任务建议错开时间

### 任务时长
| 模型 / 规格 | 一般耗时 |
|---|---|
| `video-lite` / `video-720p` / 480p / 5s | 1-2 分钟 |
| `video-pro` / 720p / 5s | 5-10 分钟 |
| `video-pro` / 1080p / 5s | 10-15 分钟 |
| `video-pro` / 1080p / 15s | 20-30 分钟 |

### 内容限制
- 不接受违反法律法规或社会道德的 prompt（暴力 / 色情 / 政治敏感等）
- 涉及真实人脸的图片输入需特别说明（避免肖像权问题）
- 提交内容你保留版权，但生成结果遵循平台 ToS

### 数据存储
- **任务元数据永久保留**（每个 `vid_xxx` 在你账号下可永久查询）
- **生成的视频文件永久保留**（不像传统 OSS 24h 过期）
- 你可以随时 DELETE 自己的任务记录

### Webhook（即将上线）
将支持在任务终态时回调你指定的 URL，避免轮询。如有需要联系管理员。

---

## FAQ

**Q: API 是同步还是异步？**
A: **异步**。`POST /v1/videos` 立即返回 `vid_xxx`，后续轮询状态。SD 2.0 系列一笔 5-30 分钟正常。

**Q: 同一个 prompt 生成的视频每次都不同吗？**
A: 默认是。如果你想**复现相同结果**，传一个固定 `seed`（如 `42`）。注意：相同 seed 也不保证 100% 一致，模型本身有微小随机性。

**Q: 怎么让生成的视频更"真实"？**
A:
- 描述具体（"a man in red jacket walking" 比 "a person" 好）
- 加入摄影术语（"cinematic", "35mm film", "shallow depth of field"）
- 用 `video-pro` 模型 + 1080p
- 提供高质量参考图 / 视频 / 音频

**Q: 余额不够会发生什么？**
A: 提交时立刻返回 `402 insufficient_balance`。**不会发起任何后端处理**，不消耗任何资源。补充余额后即可重试。

**Q: 任务失败会扣费吗？**
A: **不会**。`failed / cancelled / expired` 状态全额退回 `held_usd`。

**Q: 怎么估算我账户能跑多少视频？**
A: 调 `GET /v1/me` 看 `available_usd`，调 `GET /v1/pricing` 拿单价。或直接调 `POST /v1/videos/estimate` 看 `can_afford`。

**Q: 多个 API key 可以共享余额吗？**
A: 不可以。每个 API key 绑定唯一账号，余额独立。如需多人共享，请联系管理员。

**Q: 视频版权归谁？**
A: 生成结果归你（API 调用方）所有。但需遵循平台 ToS 中的合规要求。

**Q: 支持 webhook 通知吗？**
A: 即将上线。当前请轮询。

**Q: 国内能直接访问吗？**
A: 服务部署在新加坡，全球可访问。延迟取决于你所在地区到 Cloudflare 边缘节点的距离。

---

## 联系支持

- **充值 / 账号问题**：联系平台管理员
- **API bug / feature request**：邮箱（待补充）
- **服务状态**：`GET https://video.example.com/health` 应返回 `{"status":"ok"}`

---

**Example Video Relay Video API** · v1 · 文档更新于 2026-05-11
