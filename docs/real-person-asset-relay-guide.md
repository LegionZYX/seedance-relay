# Seedance 真人素材中转与白名单方案

## 目标

客户只拿 Relay API Key 调用你的中转站，不接触 BytePlus IAM AK/SK。生成任务保持字节原生 `content[]` 请求结构，只增加一个 Relay 自识别开关：

```json
{
  "extra_body": { "real_person_mode": true }
}
```

当客户打开这个开关时，Relay 自动把 `content[]` 中的图片或视频参考素材注册到 ModelArk 素材组，拿到 `asset://...`，写入当前客户素材账本，再转发给上游 Seedance 生成接口。

## Key 隔离设计

| Key | 谁持有 | 用途 | 客户可见 |
|---|---|---|---|
| Relay API Key `sk-...` | 客户 | 调用你的 `/v1/*` API | 是 |
| Ark API Key `ark-...` | 平台或按客户后台配置 | 创建 Seedance 生成任务 | 否 |
| IAM `BYTEPLUS_ACCESS_KEY_ID` / `BYTEPLUS_ACCESS_KEY_SECRET` | Relay 服务器 | 创建素材组、注册素材、拿 `asset://...` | 否 |
| `MODELARK_ASSET_GROUP_ID` | Relay 服务器 | 指定素材注册到哪个组 | 否 |

原则：

1. 客户生成任务不需要 AK/SK。
2. 客户上传素材不需要 AK/SK。
3. AK/SK 只放服务器环境变量或密钥管理系统，不进数据库客户表，不返回给前端。
4. 客户自己的 `ark-...` 生成 Key 和平台 IAM AK/SK 是两套完全不同的凭证。

## 数据隔离设计

每次上传或 from-url 登记都会写入 `uploads.user_id`。客户查询：

```http
GET /v1/uploads
GET /v1/uploads/{id}
```

服务端只根据 `Authorization: Bearer sk-...` 识别当前客户，然后按当前 `user_id` 查询。客户不能传 `user_id` 查看别人素材。

生成时如果请求里包含 `asset://...`，Relay 会检查这个 asset 是否来自当前客户的 `uploads` 记录。若该 asset 属于其他客户，返回：

```json
{
  "error": {
    "code": "asset_not_owned",
    "message": "This asset:// material belongs to another account"
  }
}
```

管理员后台仍可通过 `/admin/uploads` 查看全平台素材账本，用于排查和运营。

## 客户上传素材

### 本地文件上传

```bash
KEY="sk_xxxxxxxxxxxxxxxx"

curl -X POST https://video.example.com/v1/uploads \
  -H "Authorization: Bearer $KEY" \
  -F "file=@portrait.jpg;type=image/jpeg" \
  -F "face_allowlist=true"
```

返回：

```json
{
  "id": "upl_xxx",
  "url": "https://video.example.com/uploads/2026/06/03/upl_xxx.jpg",
  "asset_id": "asset_xxx",
  "asset_url": "asset://asset_xxx",
  "face_asset_whitelisted": true,
  "suggested_content_block": {
    "type": "image_url",
    "image_url": { "url": "asset://asset_xxx" },
    "role": "reference_image"
  }
}
```

### 公网 URL 登记

```bash
curl -X POST https://video.example.com/v1/uploads/from-url \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://cdn.customer.com/portrait.jpg",
    "content_type": "image/jpeg",
    "face_allowlist": true
  }'
```

这个接口不会下载文件，只登记 URL 并按需注册白名单素材。

## 生成任务

### 字节原生 content[] 生成

```bash
curl -X POST https://video.example.com/v1/videos \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "dreamina-seedance-2-0-260128",
    "content": [
      { "type": "text", "text": "这个人走在小街上，电影感，自然光" },
      {
        "type": "image_url",
        "image_url": { "url": "asset://asset_xxx" },
        "role": "reference_image"
      }
    ],
    "resolution": "720p",
    "ratio": "16:9",
    "duration": 5
  }'
```

### 一行开关自动注册真人素材

如果客户还没有先上传素材，也可以在生成时直接传公网图片 URL，并加一行：

```json
"extra_body": { "real_person_mode": true }
```

完整示例：

```bash
curl -X POST https://video.example.com/v1/videos \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "dreamina-seedance-2-0-260128",
    "content": [
      { "type": "text", "text": "这个人走在小街上，电影感，自然光" },
      {
        "type": "image_url",
        "image_url": { "url": "https://cdn.customer.com/portrait.jpg" },
        "role": "reference_image"
      }
    ],
    "resolution": "720p",
    "ratio": "16:9",
    "duration": 5,
    "extra_body": { "real_person_mode": true }
  }'
```

Relay 处理顺序：

1. 读取当前客户身份。
2. 扫描 `content[]` 里的 `image_url` / `video_url`。
3. 如果已经有当前客户同 URL 的白名单素材，复用已有 `asset://...`。
4. 如果没有，使用服务器 IAM AK/SK 调用素材注册接口。
5. 写入 `uploads` 与 `face_assets`。
6. 把请求里的公网 URL 替换成 `asset://...`。
7. 使用平台或客户绑定的 Ark API Key 创建 Seedance 任务。

## 服务端环境变量

```bash
UPSTREAM_API_KEY=ark_xxx
BYTEPLUS_ACCESS_KEY_ID=AK_xxx
BYTEPLUS_ACCESS_KEY_SECRET=SK_xxx
MODELARK_ASSET_GROUP_ID=group_xxx
FACE_ASSET_SELF_SERVICE=true
FACE_ASSET_ENFORCE=true
```

可选：

```bash
MODELARK_ASSET_AUTO_CREATE_GROUP=true
MODELARK_ASSET_GROUP_NAME=relay-face-assets
ASSET_AUTO_REGISTER_WAIT_SECONDS=0
ASSET_AUTO_REGISTER_SKIP_MODERATION=true
```

## 后台管理

管理员需要能看到：

1. IAM 是否配置，不展示明文。
2. 素材组来源：环境变量或自动创建缓存。
3. 全平台素材账本：客户、文件名、URL、asset ID、白名单状态。
4. 客户详情里的最近素材。
5. 客户价格：单客户 `price_multiplier`；旧数据可由全局 `MARKUP_PCT` 兼容回填。
6. 任务价格快照，防止后续改价影响历史任务。

## 常见错误

| 错误码 | 场景 | 处理方式 |
|---|---|---|
| `asset_registry_not_configured` | 服务器没有配置 IAM AK/SK 或素材组 | 在服务器配置环境变量 |
| `face_asset_self_service_disabled` | 客户用了 `real_person_mode` 但后台未开启 | 设置 `FACE_ASSET_SELF_SERVICE=true` |
| `face_asset_requires_asset_uri` | 开启强制白名单后仍传普通 URL | 先上传过白名单，或打开 `real_person_mode` |
| `face_asset_not_whitelisted` | `asset://...` 未在白名单 | 重新上传并注册，或管理员添加 |
| `asset_not_owned` | 客户使用别人素材 ID | 让客户使用自己账号上传的素材 |
| `no_upstream_key` | 没有平台或客户 Ark API Key | 配置 `UPSTREAM_API_KEY` 或客户 `byteplus_api_key` |

## 生产建议

1. 生产环境必须 HTTPS，否则浏览器登录 cookie 的 `secure=true` 不会在本地 HTTP 保存。
2. `.env.relay` 不进入 GitHub，使用服务器环境变量或密钥管理。
3. 客户上传素材是公开 URL 给模型拉取，不要上传敏感私密文件。
4. 如果要强制真人素材只走白名单，开启 `FACE_ASSET_ENFORCE=true`。
5. 如果客户希望“一次上传直接过白名单”，开启 `FACE_ASSET_SELF_SERVICE=true` 并配置 IAM。
