# Example Video Relay API — Seedance 视频生成白标 Relay

一套**白标 SaaS 系统**：把 BytePlus Seedance 视频生成 API 包装成自己品牌的服务对外出售。
对客户隐藏底层供应商，内置用户系统、余额预扣、对账、视频代理交付、管理员 / 用户两套 Web 后台。

> 示例部署域名为 `https://video.example.com`，服务器地址请替换为 `<SERVER_IP>`，底层模型为 BytePlus Seedance 2.0。

---

## 目录

- [一、项目功能](#一项目功能)
- [二、系统架构](#二系统架构)
- [三、目录结构](#三目录结构)
- [四、本地开发](#四本地开发)
- [五、部署到服务器（核心，逐步操作）](#五部署到服务器核心逐步操作)
- [六、环境变量说明](#六环境变量说明)
- [七、API 接口总览](#七api-接口总览)
- [八、管理员后台使用](#八管理员后台使用)
- [九、用户后台使用](#九用户后台使用)
- [十、运维指令](#十运维指令)
- [十一、故障排查](#十一故障排查)

---

## 一、项目功能

### 1.1 核心能力

| 能力 | 说明 |
|---|---|
| **白标反代** | 客户调你的 `video.example.com` API，不暴露 BytePlus/volces 上游 URL、密钥、endpoint、控制台路径和存储原始地址 |
| **模型 ID 策略** | 默认使用字节 API 原生 `model id` 执行和展示；管理员可以主动配置模型别名来降低客户解释成本，不配置就保持原生 ID |
| **用户系统** | 邮箱 + bcrypt 密码登录，独立的客户 API key (`sk-xxxx`)，可全 Web 操作 |
| **每客户独立 BytePlus key** | 每个客户在你 BytePlus 控制台单独申请一把 key，1:1 对账，互不影响 |
| **余额预扣 + 自动结算** | 提交任务前先冻结估算上限，任务终态后按真实 token 数实扣并退差额 |
| **视频代理交付** | 任务成功后返回 Relay 自有视频 URL，默认不把生成视频永久保存到本地服务器 |
| **错误信息脱敏** | 上游错误信息中的供应商字样被正则替换为你的品牌名 |
| **管理员后台** | 浏览器开账号、充值、对账、查任务流水 |
| **用户后台** | 浏览器生成视频、看历史、播放、查余额、改密码、轮换自己的 Relay API Key |
| **客户消费系数** | 后台可给每个客户设置 `price_multiplier`，最低 `1.0`，如 `1.0` / `1.2` / `1.3` |

### 1.2 支持的模型

| 原生 model id | 用途 | 客户可见能力 |
|---|---|---|
| `dreamina-seedance-2-0-260128` | 高质量视频生成 | 480p / 720p / 1080p，支持文本、图片、视频、音频参考 |
| `dreamina-seedance-2-0-fast-260128` | 高质量快速版 | 480p / 720p / 1080p，支持文本、图片、视频、音频参考 |
| `seedance-1-5-pro-251215` | 中端兼容模型 | 480p / 720p / 1080p，支持文本、图片、视频、音频参考 |
| `seedance-1-0-pro-250528` | 1080p 专用路线 | 480p / 720p / 1080p，支持文本、图片、视频、音频参考 |
| `seedance-1-0-pro-fast-251015` | 720p 快速路线 | 480p / 720p / 1080p，支持文本、图片、视频、音频参考 |
| `seedance-1-0-lite-t2v-250428` | 轻量文本到视频 | 480p / 720p / 1080p，支持文本、图片、视频、音频参考 |
| `seedance-1-0-lite-i2v-250428` | 轻量图片/视频参考生成 | 必须提供 `image_url` 或 `video_url` 参考 |

> 客户最终能调用的模型由后台 `enabled_models` 控制。默认写入字节原生 `model id`；如果管理员配置 `MODEL_ID_ALIASES_JSON`，也可以给某个客户启用别名。真实上游 URL、账号、endpoint/profile、filter 和 operator notes 仍是 operator-only 配置，不写入公开文档和客户 UI。
> `enabled_models=null` 表示使用默认模型列表；`enabled_models=[]` 表示该客户暂不启用任何模型。

### 1.3 计费流程

```
客户提交 ─→ relay 估算成本 ─→ 余额检查
                                ├─不够→ 402 直接拒绝（不打上游，零成本）
                                └─够 → 冻结 max_cost × price_multiplier
                                       ↓
                              转发 BytePlus（用客户专属 key）
                                       ↓
                              客户查询状态 → relay 同步上游
                                       ↓
                              终态 succeeded → 真实 cost × price_multiplier 实扣
                                              退差额给客户
                                              通过 Relay URL 代理播放/下载
                              终态 failed/cancelled/expired → 全额退回
```

---

## 二、系统架构

```
                           Internet
                              │
                              ▼  HTTPS (443)
                ┌─────────────────────────┐
                │  Caddy (自动 TLS)        │  /etc/caddy/Caddyfile
                │  video.example.com        │
                └────────────┬────────────┘
                             │ HTTP 127.0.0.1:8002
                             ▼
              ┌──────────────────────────────┐
              │   seedance-relay (Docker)    │
              │   FastAPI + Uvicorn          │
              │                              │
              │   /auth/*  /v1/*  /admin/*   │
              │   /app/    /admin/ui/        │
              └──────┬──────────────┬────────┘
                     │              │
                     │              └─ /data 卷
                     │                  ├─ relay.sqlite     (用户/任务/会话)
                     │                  └─ videos/          (旧本地视频兼容读取；新任务默认 proxy-only)
                     │
                     ▼ HTTPS, Bearer ark-xxxx
       ┌──────────────────────────────────┐
       │   ark.ap-southeast.bytepluses.com │
       │   BytePlus ModelArk Seedance      │
       └──────────────────────────────────┘
```

**关键点**：
- 应用容器**只监听 `127.0.0.1:8002`**，Caddy 反代到公网。外网无法直连应用，强制走 HTTPS。
- 数据库 + 视频文件落在宿主机 `./data` 目录，容器销毁不丢数据。
- 每个客户**独立 BytePlus key**存在 DB 里，请求时用客户的 key 转发上游。

---

## 三、目录结构

```
sea_dance_api/
├── relay_server.py            FastAPI 主服务（鉴权 / 反代 / 结算 / 后台）
├── create_asset_white_label.py 中转站 URL → asset:// 注册脚本
├── modelark/                  计费核心包
│   ├── __init__.py
│   ├── pricing.py             25+ 模型价格表（含 Seedance 2.0 分档定价）
│   ├── estimator.py           成本预估 + 预扣对账 (Reservation)
│   └── usage.py               Token 用量提取与归一化
├── static/
│   ├── admin.html             管理员后台 SPA（Tailwind via CDN）
│   └── app.html               用户后台 SPA
├── Dockerfile.relay           应用镜像（python:3.11-slim + uvicorn）
├── docker-compose.relay.yml   容器编排
├── requirements.relay.txt     依赖：fastapi/uvicorn/httpx/bcrypt/dotenv/pydantic/python-multipart
├── deploy/
│   └── caddy_video.snippet    Caddy site block（自动 TLS）
├── .env.relay.example         环境变量模板
├── API_DOCS.md                客户 API 文档（被 /v1/docs/api.md 暴露）
└── README.md                  本文件
```

---

## 四、本地开发

### 4.1 装依赖

```bash
cd sea_dance_api
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.relay.txt
```

### 4.2 配置 `.env.relay`

```bash
cp .env.relay.example .env.relay
vi .env.relay
```

最少要填：

```bash
UPSTREAM_API_KEY=ark-xxxxxxxxxxxxxxxx        # 你 BytePlus 的全局 key (fallback)
ADMIN_PASSWORD=your_admin_password           # 首次启动会用它创建 admin 用户
ADMIN_EMAIL=admin@example.com
BRAND_NAME=Your Brand
DB_PATH=./data/relay.sqlite                  # 本地开发用相对路径
VIDEO_DIR=./data/videos
VIDEO_RETENTION_SECONDS=7200
UPLOAD_DIR=./data/uploads
```

### 4.3 启动

```bash
uvicorn relay_server:app --host 0.0.0.0 --port 8002 --reload
```

打开 http://localhost:8002/admin/ui/ 用 `ADMIN_EMAIL` + `ADMIN_PASSWORD` 登录。

---

## 五、部署到服务器（核心，逐步操作）

> 以全新 Ubuntu 22.04 服务器为例。若部署目标是你自己的已有服务器，跳到 [5.7 复用已有服务器更新代码](#57-复用已有服务器只更新代码)。

### 5.1 服务器准备

```bash
ssh root@<server-ip>

# 系统更新
apt update && apt upgrade -y

# 装 Docker + docker compose plugin
curl -fsSL https://get.docker.com | sh
apt install -y docker-compose-plugin

# 装 Caddy（自动 SSL）
apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | tee /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install -y caddy

# 开防火墙
ufw allow 22 && ufw allow 80 && ufw allow 443 && ufw --force enable
```

### 5.2 域名解析

在域名服务商把你的子域名（如 `video.example.com`）的 **A 记录**指到服务器 IP。
等几分钟 DNS 生效：

```bash
dig +short video.example.com    # 应该返回你的服务器 IP
```

### 5.3 上传代码到服务器

在**本地仓库目录**执行：

```bash
# 在服务器准备好目录
ssh root@<server-ip> "mkdir -p /opt/seedance-relay/data/videos /opt/seedance-relay/data/uploads"

# 打包上传（排除虚拟环境、本地数据、密钥）
tar -cz \
    --exclude=.venv --exclude=outputs --exclude=__pycache__ \
    --exclude=.env --exclude=.env.relay --exclude=data \
    modelark/ relay_server.py requirements.relay.txt \
    create_asset_white_label.py \
    Dockerfile.relay docker-compose.relay.yml .env.relay.example \
    static/ deploy/ API_DOCS.md \
  | ssh root@<server-ip> "tar -xz -C /opt/seedance-relay/"
```

### 5.4 服务器上配置 `.env.relay`

```bash
ssh root@<server-ip>
cd /opt/seedance-relay
cp .env.relay.example .env.relay
```

编辑 `.env.relay`，必填项：

```bash
# BytePlus 全局 fallback key（用户没填自己的 BP key 时用这把）
UPSTREAM_API_KEY=ark-xxxxxxxxxxxxxxxx
UPSTREAM_BASE_URL=https://ark.ap-southeast.bytepluses.com/api/v3

# 你的对外域名
PUBLIC_DOMAIN=video.example.com
BRAND_NAME=Your Brand Studio

# 数据库 + 视频路径（容器内路径，会挂载到宿主机 ./data）
DB_PATH=/data/relay.sqlite
VIDEO_DIR=/data/videos
VIDEO_RETENTION_SECONDS=7200
UPLOAD_DIR=/data/uploads

# 上传中转站（留空时用 https://PUBLIC_DOMAIN/uploads/...）
UPLOAD_PUBLIC_BASE_URL=
UPLOAD_MAX_IMAGE_MB=10
UPLOAD_MAX_VIDEO_MB=50
UPLOAD_MAX_AUDIO_MB=15

# 可选：服务端自动把上传 URL 注册成 asset://
# 这些只放服务器环境里，用户端不需要也拿不到。
ASSET_AUTO_REGISTER_UPLOADS=true
ASSET_AUTO_REGISTER_PURPOSES=image,video,audio
ASSET_AUTO_REGISTER_WAIT_SECONDS=0
ASSET_AUTO_REGISTER_WAIT_INTERVAL=3
ASSET_CREATE_RETRY_DELAYS=10,30
ASSET_AUTO_REGISTER_SKIP_MODERATION=true
BYTEPLUS_ACCESS_KEY_ID=
BYTEPLUS_ACCESS_KEY_SECRET=
MODELARK_ASSET_GROUP_ID=
MODELARK_ASSET_AUTO_CREATE_GROUP=true
MODELARK_ASSET_GROUP_NAME=relay-face-assets
MODELARK_ASSET_GROUP_DESCRIPTION=Relay self-service face asset whitelist
MODELARK_PROJECT_NAME=

# 人脸/真人素材白名单闸门
# 生产建议打开：reference_image/reference_video 必须使用服务端白名单 asset://
FACE_ASSET_ENFORCE=false
FACE_ASSET_SELF_SERVICE=false
FACE_ASSET_ALLOWLIST=
FACE_ASSET_ENFORCE_ROLES=reference_image,reference_video

# Admin 账号（首次启动时自动创建）
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=<生成一个强密码>
ADMIN_KEY=<随机串，供 curl/脚本绕过登录用>

# 全局默认加价兼容项；新客户建议在后台直接设置 price_multiplier
MARKUP_PCT=0.3
```

生成强密码 / ADMIN_KEY：

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
```

### 5.5 启动应用容器

```bash
cd /opt/seedance-relay
docker compose -f docker-compose.relay.yml up -d --build

# 看启动日志
docker logs -f seedance-relay
```

健康检查：

```bash
curl http://127.0.0.1:8002/health
# 期望返回: {"status":"ok","brand":"Your Brand Studio","models":[...]}
```

### 5.6 配置 Caddy 反代

```bash
# 把模板里的域名换成你的
cd /opt/seedance-relay
sed "s/video.example.com/<YOUR_DOMAIN>/g" deploy/caddy_video.snippet \
  >> /etc/caddy/Caddyfile

# 重载 Caddy（首次会自动申请 Let's Encrypt 证书）
systemctl reload caddy

# 看 Caddy 日志确认证书签发
journalctl -u caddy -f
```

公网验证：

```bash
curl https://video.example.com/health
# 应返回 200 + JSON
```

浏览器打开 `https://video.example.com/admin/ui/`，用 `ADMIN_EMAIL` + `ADMIN_PASSWORD` 登录。

### 5.7 复用已有服务器（只更新代码）

如果你的服务器上已经跑过这套系统（例如 `/opt/seedance-relay`），只想更新代码：

```bash
# 本地: 把改动的文件 scp 上去
scp relay_server.py root@<server-ip>:/opt/seedance-relay/
scp create_asset_white_label.py requirements.relay.txt root@<server-ip>:/opt/seedance-relay/
scp -r modelark/ static/ root@<server-ip>:/opt/seedance-relay/

# 服务器: 重新构建并平滑重启
ssh root@<server-ip> "cd /opt/seedance-relay && \
  docker compose -f docker-compose.relay.yml up -d --build"
```

> **注意**：改了 `static/` 必须 `--build`（HTML 被 COPY 进镜像了）。仅 `restart` 不会更新。

---

## 六、环境变量说明

| 变量 | 默认值 | 说明 |
|---|---|---|
| `UPSTREAM_API_KEY` | 空 | BytePlus 全局 fallback key |
| `UPSTREAM_BASE_URL` | `https://ark.ap-southeast.bytepluses.com/api/v3` | BytePlus API 域名 |
| `PUBLIC_DOMAIN` | `video.example.com` | 对外域名（用于生成视频 URL） |
| `BRAND_NAME` | `Example Video Relay` | 品牌名（脱敏替换 / UI 标题） |
| `DB_PATH` | `/data/relay.sqlite` | SQLite 数据库路径 |
| `VIDEO_DIR` | `/data/videos` | 视频落地目录 |
| `VIDEO_RETENTION_SECONDS` | `7200` | Relay 服务器成功视频默认保存期，7200 秒即 2 小时；BytePlus 原始生成文件官方保存 24 小时 |
| `UPLOAD_DIR` | `/data/uploads` | 上传中转站本地保存目录 |
| `UPLOAD_PUBLIC_BASE_URL` | 空 | 上传 URL 的公网 base；留空用 `https://PUBLIC_DOMAIN` |
| `UPLOAD_MAX_IMAGE_MB` | `10` | 图片上传白名单大小上限 |
| `UPLOAD_MAX_VIDEO_MB` | `50` | 视频上传白名单大小上限 |
| `UPLOAD_MAX_AUDIO_MB` | `15` | 音频上传白名单大小上限 |
| `ENDPOINT_KEY_ROTATION_ENABLED` | `false` | 是否启用客户独立 endpoint key 自动轮换脚本 |
| `ENDPOINT_KEY_ROTATION_DAYS_BEFORE_EXPIRY` | `5` | 距离过期多少天以内触发轮换 |
| `ENDPOINT_KEY_DURATION_SECONDS` | `2592000` | 新 endpoint API key 有效期秒数 |
| `ENDPOINT_KEY_ROTATION_DRY_RUN` | `false` | 只检查待轮换客户，不写 DB、不调用上游 |
| `ENDPOINT_KEY_RESOURCE_MODE` | `multi` | endpoint key 生成方式：`multi` / `per_endpoint` / `auto`；上游不支持多 endpoint ResourceIds 时用 per-endpoint key map |
| `ASSET_AUTO_REGISTER_UPLOADS` | `true` | 服务端是否自动把上传 URL 注册成 `asset://...`；默认上传到客户归属的 AIGC 素材组 |
| `ASSET_AUTO_REGISTER_PURPOSES` | `image,video,audio` | 开关启用后哪些上传类型自动注册 |
| `ASSET_AUTO_REGISTER_WAIT_SECONDS` | `0` | 是否等待 asset 变 Active；0 表示只创建不等待 |
| `ASSET_CREATE_RETRY_DELAYS` | `10,30` | CreateAsset 遇到上游临时 504/InternalServiceTimeout 时的重试等待秒数 |
| `ASSET_AUTO_REGISTER_SKIP_MODERATION` | `true` | CreateAsset 时默认传 skip moderation |
| `ASSET_DELETE_EXECUTION_MODE` | `admin_batch` | 客户删除素材后的 BytePlus asset 删除方式：`local_only` / `admin_batch` / `auto` |
| `BYTEPLUS_PROJECT_QUOTA_WARN_AT` | `0` | 后台 Quota Center reminder 的 Project 本地计数提醒阈值；0 表示关闭 |
| `BYTEPLUS_ENDPOINT_QUOTA_WARN_AT` | `0` | 后台 Quota Center reminder 的 endpoint 本地计数提醒阈值；0 表示关闭 |
| `BYTEPLUS_ASSET_GROUP_QUOTA_WARN_AT` | `0` | 后台 Quota Center reminder 的 AssetGroup 本地计数提醒阈值；0 表示关闭 |
| `BYTEPLUS_ACCESS_KEY_ID` | 空 | 服务端 asset registry AK，用户不可见 |
| `BYTEPLUS_ACCESS_KEY_SECRET` | 空 | 服务端 asset registry SK，用户不可见 |
| `MODELARK_ASSET_GROUP_ID` | 空 | 可选固定素材组；留空时优先使用客户自己的 AIGC 素材组，缺失则自动创建并写回客户配置 |
| `MODELARK_ASSET_AUTO_CREATE_GROUP` | `true` | 客户缺少素材组时自动创建 BytePlus Project / AIGC Asset Group 并写回 `users.note` |
| `MODELARK_ASSET_GROUP_NAME` | `relay-face-assets` | 自动创建素材组时使用的名称 |
| `MODELARK_ASSET_GROUP_DESCRIPTION` | `Relay self-service face asset whitelist` | 自动创建素材组描述 |
| `MODELARK_PROJECT_NAME` | 空 | 可选固定 Project 名；留空时按客户 slug 自动创建/使用客户自己的 Project |
| `FACE_ASSET_ENFORCE` | `false` | 是否启用人脸/真人素材白名单闸门 |
| `FACE_ASSET_SELF_SERVICE` | `false` | 是否允许客户通过 `face_allowlist=true` 自助注册并加入白名单 |
| `FACE_ASSET_ALLOWLIST` | 空 | 逗号分隔的 `asset://...` 白名单 |
| `FACE_ASSET_ENFORCE_ROLES` | `reference_image,reference_video` | 哪些 role 必须走人脸白名单 |
| `ADMIN_EMAIL` | `admin@example.com` | 首次创建 admin 用的邮箱 |
| `ADMIN_PASSWORD` | 空 | 首次创建 admin 用的密码（启动时生效一次） |
| `ADMIN_KEY` | 空 | 备用 `X-Admin-Key` 头（脚本/curl 绕登录） |
| `MARKUP_PCT` | `0.3` | 旧数据兼容用的全局默认加价比例；新客户优先在后台设置 `price_multiplier` |

---

## 七、API 接口总览

### 7.1 客户 API（`Bearer sk-xxxx` 鉴权）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查（无需鉴权） |
| GET | `/v1/models` | 列模型（脱敏后） |
| GET | `/v1/pricing` | 公开价格表 + 估算公式 |
| POST | `/v1/uploads` | 上传白名单媒体到中转站，默认注册到客户归属 AIGC 素材组并返回 `asset://...` |
| POST | `/v1/uploads/from-url` | 登记客户已有公网素材 URL，返回可复用 content block |
| GET | `/v1/uploads` | 查看当前客户自己上传过的素材 |
| GET | `/v1/uploads/{id}` | 查看当前客户自己的单个素材 |
| POST | `/v1/videos/estimate` | **提交前估算成本**（不消耗 token） |
| POST | `/v1/videos` | 创建视频任务 |
| GET | `/v1/videos/{vid}` | 查任务（自动同步上游） |
| GET | `/v1/videos/{vid}/content` | 通过 Relay 代理流式播放/下载视频 |
| DELETE | `/v1/videos/{vid}` | 取消（queued）/ 删除（终态） |
| GET | `/v1/videos` | 列自己的任务 |
| GET | `/v1/me` | 自己的余额 + 脱敏 API Key 元数据 |

上传中转站白名单：

| 类型 | MIME 白名单 | 默认大小上限 |
|---|---|---|
| 图片 | `image/jpeg`, `image/png`, `image/webp` | `UPLOAD_MAX_IMAGE_MB=10` |
| 视频 | `video/mp4`, `video/quicktime` | `UPLOAD_MAX_VIDEO_MB=50` |
| 音频 | `audio/mpeg`, `audio/wav`, `audio/x-wav` | `UPLOAD_MAX_AUDIO_MB=15` |

上传后会返回 `https://video.example.com/uploads/YYYY/MM/DD/upl_xxx.ext`，这个 URL 是公开的，目的是让 Seedance 能直接拉取。不要把敏感文件传到中转站。

每次上传都会写入当前客户账号的素材记录。客户用自己的 API key 查询时，只会看到自己的素材：

```bash
curl https://video.example.com/v1/uploads \
  -H "Authorization: Bearer $KEY"

curl https://video.example.com/v1/uploads/upl_xxx \
  -H "Authorization: Bearer $KEY"
```

如果客户尝试访问其他账号上传的素材 ID，接口会返回 `404 upload_not_found`。

默认 `ASSET_AUTO_REGISTER_UPLOADS=true`。上传成功后，Relay 会用服务端 IAM 把素材注册到当前客户归属的 ModelArk AIGC 素材组，响应包含 `asset_id` / `asset_url` / `asset_status`，并且 `suggested_content_block` 会自动使用 `asset://...`。用户端仍然只调用 `/v1/uploads`，不需要任何 AK/SK/GroupId。

### 人脸白名单

Seedance 2.0 对真实人脸 reference 有额外限制。生产环境建议打开：

```bash
FACE_ASSET_ENFORCE=true
FACE_ASSET_ENFORCE_ROLES=reference_image,reference_video
```

打开后，任何 `role=reference_image` 或 `role=reference_video` 的素材都必须满足：

1. URL 是 `asset://asset-id`，不能是普通公网 URL。
2. 该 `asset://asset-id` 已在服务端白名单里。

白名单有两种来源：

```bash
# 方式 1：写进服务器环境变量
FACE_ASSET_ALLOWLIST=asset://asset-xxx,asset://asset-yyy

# 方式 2：admin API 写入本地 DB
curl -X POST https://video.example.com/admin/face-assets \
  -H "X-Admin-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"asset_url":"asset://asset-xxx","asset_type":"image","label":"approved actor"}'
```

查询当前白名单：

```bash
curl https://video.example.com/admin/face-assets \
  -H "X-Admin-Key: $ADMIN_KEY"
```

注意：`ASSET_AUTO_REGISTER_UPLOADS` 只是服务端自动注册素材 URL 到客户归属 AIGC 素材组；它不是客户内容审核开关，也不等于 Relay 人脸白名单。客户仍然只使用 Relay API Key，服务端按当前 BytePlus 资产注册能力把可用素材转成 `asset://...` 并记录到账本。

如果希望客户只用你的 Relay API Key 自助完成上传和入白名单，平台侧打开：

```bash
BYTEPLUS_ACCESS_KEY_ID=your-server-ak
BYTEPLUS_ACCESS_KEY_SECRET=your-server-sk
MODELARK_ASSET_AUTO_CREATE_GROUP=true
FACE_ASSET_SELF_SERVICE=true
```

`MODELARK_ASSET_GROUP_ID` 可以留空。Relay 第一次注册素材时会用 Python 素材接口调用 `CreateAssetGroup`，把返回的 GroupId 缓存到 SQLite 的 `settings` 表，后续上传直接复用。已经有固定素材组时，也可以显式配置 `MODELARK_ASSET_GROUP_ID`，Relay 就不会自动建组。

客户只需要在上传时多传一行参数：

```bash
curl -X POST https://video.example.com/v1/uploads \
  -H "Authorization: Bearer $KEY" \
  -F "face_allowlist=true" \
  -F "face_asset_label=actor-a" \
  -F "file=@portrait.jpg;type=image/jpeg"
```

Relay 会在服务端完成：

1. 保存到中转站。
2. 调用服务端素材注册，拿到 `asset://...`。
3. 写入本地 `face_assets` 白名单。
4. 返回可直接用于 `role=reference_image` 的 `suggested_content_block`；客户照抄这个 block 放进 `/v1/videos` 即可。

客户全程不需要、也拿不到 BytePlus AK/SK/GroupId。

### Relay 运营方案：客户素材与价格隔离

推荐把中转站拆成 4 个后台层：

1. 平台密钥层：`BYTEPLUS_ACCESS_KEY_ID` / `BYTEPLUS_ACCESS_KEY_SECRET` / 素材组只保存在服务器，用来注册官方 `asset://...`。
2. 客户账户层：每个客户只拿 Relay 自己发的 `sk-xxx`，这个 key 对应 `users.id`。
3. 素材账本层：每次 `POST /v1/uploads` 都写入 `uploads.user_id`。客户查询 `GET /v1/uploads` 或 `GET /v1/uploads/{id}` 时，服务端只按当前 API key 的 `user_id` 查询；访问其他客户素材会返回 `404 upload_not_found`。
4. 客户价格层：后台可以给单个客户设置 `price_multiplier`，alpha1 最低为 `1.0`。`price_multiplier=1.2` 表示按 BytePlus 成本的 120% 向客户计费。任务创建时会把该客户当时的系数快照写入任务记录，后续结算不受之后改价影响。

后台调价：

```bash
curl -X PATCH https://video.example.com/admin/users/u_xxx \
  -H "X-Admin-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"price_multiplier":1.2}'

# 清空客户独立价格，回到全局默认
curl -X PATCH https://video.example.com/admin/users/u_xxx \
  -H "X-Admin-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"price_multiplier":null}'
```

客户估价与价格表会返回自己的有效价格：

```bash
curl https://video.example.com/v1/pricing \
  -H "Authorization: Bearer $KEY"
```

响应中的 `pricing_scope` 为 `customer` 表示客户独立价格；为 `global` 表示使用全局默认。

### 客户只用命令行时怎么走

有两种方式：

1. 客户本地有文件：继续走 multipart 上传。

```bash
curl -X POST https://video.example.com/v1/uploads \
  -H "Authorization: Bearer $KEY" \
  -F "file=@portrait.jpg;type=image/jpeg"
```

2. 客户已经有公网 URL：走 URL 入库，不需要把文件再传一遍。

```bash
curl -X POST https://video.example.com/v1/uploads/from-url \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url":"https://cdn.customer.com/portrait.jpg",
    "content_type":"image/jpeg",
    "original_filename":"portrait.jpg"
  }'
```

这两个接口都会把素材写进 `uploads.user_id`，所以客户再用 `GET /v1/uploads` 只能看到自己的素材。返回里的 `suggested_content_block` 可以直接放进 `/v1/videos` 的 `content[]`。如果开启 `FACE_ASSET_SELF_SERVICE=true`，客户只要多传 `"face_allowlist": true`，Relay 就会用服务端 IAM 自动注册 `asset://...` 并写入白名单。

### 后台 IAM 账号存在哪里

默认方案：IAM AK/SK 是平台级密钥，只存在 Relay 服务端，不存在客户表，也不发给客户。

推荐存储顺序：

1. 生产环境优先放云平台 Secret Manager / Docker secret / CI/CD secret。
2. 单机 Docker 部署可以放 `/opt/seedance-relay/.env.relay`，文件权限建议 `chmod 600`，只给部署用户可读。
3. SQLite 只缓存非敏感运行状态，例如自动创建出来的 `MODELARK_ASSET_GROUP_ID`；不保存 `BYTEPLUS_ACCESS_KEY_SECRET` 明文。

`.env.relay` 示例：

```bash
BYTEPLUS_ACCESS_KEY_ID=your-server-iam-ak
BYTEPLUS_ACCESS_KEY_SECRET=your-server-iam-sk
MODELARK_ASSET_AUTO_CREATE_GROUP=true
MODELARK_ASSET_GROUP_NAME=relay-face-assets
FACE_ASSET_SELF_SERVICE=true
```

后台 `/admin/config` 只显示“是否配置”和素材组来源，不返回 AK/SK/GroupId 明文。除非你要给不同法律主体或不同上游账单做完全隔离，否则不要给每个客户存一套 IAM AK/SK；客户只需要 Relay 发的 `sk-xxx`。

### Key 隔离兼容检查

保持原有架构不变，只加素材能力：

1. 客户认证：客户永远只用 Relay 发的 `sk-xxx` Bearer key。
2. 生成任务上游 key：可以走平台大 key `UPSTREAM_API_KEY`，也可以给单个客户在后台配置 `users.byteplus_api_key`。这两种都是 `ark-...` API key，用于 `/v1/videos` 生成任务。
3. 素材注册 IAM：`BYTEPLUS_ACCESS_KEY_ID` / `BYTEPLUS_ACCESS_KEY_SECRET` 只给 Relay 服务端使用，用于 `CreateAsset` / `CreateAssetGroup`。客户不需要、也不应该拿到 AK/SK。
4. 素材组：`MODELARK_ASSET_GROUP_ID` 可以由服务端环境变量指定，也可以让 Relay 首次注册素材时自动创建并缓存到 SQLite。

生成参数保持字节原生，不额外引入 GeekAI / Doubao 的 `prompt`、`image`、`image_tail`、`images`、`video`、`audio`、`aspect_ratio` 等字段。客户创建任务时继续使用 `content[]`。

只保留一个 Relay 自助真人素材开关：

```json
{
  "model": "dreamina-seedance-2-0-260128",
  "content": [
    { "type": "text", "text": "走在小街上" },
    {
      "type": "image_url",
      "image_url": { "url": "https://cdn.customer.com/portrait.jpg" },
      "role": "reference_image"
    }
  ],
  "extra_body": { "real_person_mode": true }
}
```

这个模式下 Relay 会用服务端 IAM 把 `content[]` 里的图片/视频参考素材注册成 `asset://...`，写入当前客户的素材账本，然后再提交生成任务。客户已经上传并白名单过同一个 URL 时会复用已有素材 ID，不重复注册。客户仍然不接触 AK/SK。

素材 ID 也做客户隔离：客户只能列出和使用自己 API key 名下的上传素材。别人上传产生的 `asset://...` 即使被复制到请求里，中转也会返回 `asset_not_owned`。

调用示例：

```bash
KEY="sk-xxxxxxxxxxxxxxxx"   # 客户从 /app/ 账号页复制

# 0) 先把本地素材上传到中转站
curl -X POST https://video.example.com/v1/uploads \
  -H "Authorization: Bearer $KEY" \
  -F "file=@portrait.jpg;type=image/jpeg"
# 返回里的 suggested_content_block 可直接放入 /v1/videos 的 content[]

# 1) 预估
curl -X POST https://video.example.com/v1/videos/estimate \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"dreamina-seedance-2-0-260128","resolution":"720p","duration":5,
       "content":[{"type":"text","text":"a cat walking"}]}'

# 2) 创建
curl -X POST https://video.example.com/v1/videos \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"dreamina-seedance-2-0-260128","resolution":"720p","duration":5,
       "content":[{"type":"text","text":"a cat walking"}]}'

# 3) 查任务
curl https://video.example.com/v1/videos/vid_xxx \
  -H "Authorization: Bearer $KEY"

# 4) 下载视频
curl https://video.example.com/v1/videos/vid_xxx/content \
  -H "Authorization: Bearer $KEY" -o video.mp4
```

如果没有打开自动注册，也可以由管理员在服务器上手动把 reference video 做成 provider 可识别的白名单 asset：

```bash
export BYTEPLUS_ACCESS_KEY_ID="your-access-key-id"
export BYTEPLUS_ACCESS_KEY_SECRET="your-secret-access-key"
export MODELARK_ASSET_GROUP_ID="your-asset-group-id"

python create_asset_white_label.py create \
  --url "https://video.example.com/uploads/YYYY/MM/DD/upl_xxx.mp4" \
  --asset-type Video \
  --skip-moderation

python create_asset_white_label.py wait --asset-id "asset-xxxx"
```

如果还没有 GroupId，可以先用同一个 Python 脚本创建素材组：

```bash
python create_asset_white_label.py create-group \
  --name "relay-face-assets" \
  --description "Relay self-service face asset whitelist"
```

脚本输出的 group id 可以写入 `MODELARK_ASSET_GROUP_ID`；Relay 自动建组开启时也会做同样的事情并缓存到 SQLite。

脚本输出的 `asset://asset-xxxx` 可以作为 `content[]` 里的 `video_url.url`，或写入你的 reference 白名单池。

### 7.2 管理员 API（`X-Admin-Key` 头 或 admin 登录态）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/config` | 查看平台 IAM / 素材注册配置状态（不返回密钥明文） |
| GET | `/admin/stats` | 全局 KPI |
| GET | `/admin/users` | 用户列表 |
| POST | `/admin/users` | 创建用户（可指定 BytePlus key、初始余额、客户消费系数） |
| GET | `/admin/users/{id}` | 用户详情（含最近任务、最近素材 + 累计统计） |
| PATCH | `/admin/users/{id}` | 改余额 / 客户消费系数 / 可用模型 / BP key / 密码 / 启停 |
| POST | `/admin/users/{id}/topup` | 充值 |
| DELETE | `/admin/users/{id}` | 禁用（软删除，保留历史） |
| GET | `/admin/reconcile` | 按用户对账（与 BytePlus 后台 1:1 比对） |
| GET | `/admin/tasks` | 全平台任务流水 |
| GET | `/admin/uploads` | 全平台素材账本（可按用户、类型、白名单状态筛选） |
| GET | `/admin/uploads/{id}` | 查看单个上传素材及归属客户 |
| GET | `/admin/face-assets` | 查看服务端人脸白名单 |
| POST | `/admin/face-assets` | 新增/更新服务端人脸白名单 asset |

curl 示例：

```bash
ADMIN_KEY="<.env.relay 里的 ADMIN_KEY>"

# 开一个新客户
curl -X POST https://video.example.com/admin/users \
  -H "X-Admin-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
  -d '{
    "email":"newcustomer@example.com",
    "balance_usd": 50,
    "price_multiplier": 1.2,
    "enabled_models": ["dreamina-seedance-2-0-260128", "seedance-1-0-lite-t2v-250428"],
    "byteplus_api_key":"ark-customer-xxxx",
    "byteplus_account_label":"客户公司名"
  }'
# 返回里有 api_key (发给客户) 和 temporary_password (登录用)
```

### 7.3 鉴权方式

| 方式 | 用途 |
|---|---|
| `Authorization: Bearer sk-xxxx` | 客户 SDK / curl |
| `relay_session` cookie | 浏览器登录后自动带 |
| `X-Admin-Key: <ADMIN_KEY>` | 运维脚本绕过登录调 admin API |

---

## 八、管理员后台使用

打开 `https://video.example.com/admin/ui/`，登录后看到 5 个核心 Tab：

### 8.1 概览

KPI 卡：活跃用户数、总任务数、素材数、白名单素材数、已收入、BytePlus 成本、毛利、冻结中。

### 8.2 用户管理

- 点 **"+ 开新账号"**，填邮箱、初始密码（留空自动生成）、初始余额、客户消费系数、可用模型、**BytePlus API key**（你在 BytePlus 控制台为该客户单独申请的）、BytePlus 标签（内部备注）。
- 创建成功立即弹出：客户 `sk-xxxx` API key + 临时密码。把两条发给客户即可。
- 每行可点开编辑：改余额 / 一键充值 / 改消费系数 / 改可用模型 / 替换 BP key / 重置密码 / 轮换 API Key / 启停 / 看该用户最近 20 个任务和最近 20 个素材。

### 8.3 素材管理

全平台上传素材账本。后台可以按客户、素材类型、是否进入人脸白名单筛选；每条记录显示上传客户、原文件名、公开 URL、asset:// 注册状态、大小和上传时间。这里是排查客户“上传了但不会用/看不到”的第一入口。

### 8.4 对账

一行一个用户，列出他名下的任务数、tokens、BytePlus 真实成本、向客户收的钱、毛利。
拿这里的数字直接对比 BytePlus 控制台按 key 的账单。

### 8.4 任务

全平台任务流水（最新 100 条），含 prompt、tokens、上游成本、向客户收费、状态。

---

## 九、用户后台使用

客户拿 admin 发的邮箱+密码登录 `https://video.example.com/`，进入 4 个 Tab：

### 9.1 新建视频

完整表单：prompt / 参考素材 / 模型 / 分辨率 / 比例 / 时长 / seed / 是否生成音频 / 是否加水印。
**右下角实时显示预估成本**（前端调用 `/v1/videos/estimate`，按当前客户自己的 `price_multiplier` 返回价格；网络未返回时才用本地公式兜底）。
余额不足时提交按钮自动禁用。

提交后自动跳到任务详情，每 8 秒轮询一次，succeeded 后直接 `<video>` 标签内嵌播放 + 下载按钮。

### 9.2 我的任务

卡片列表 + 缩略图 + 状态徽章 + tokens / 价格 / prompt。

### 9.3 素材库

客户可以上传图片/视频/音频素材，列表只返回当前 API key 自己的 `uploads.user_id` 记录。素材卡片支持“用作参考”和“复制引用”；点“用作参考”后，新建视频页会自动把该素材的 `suggested_content_block` 放进生成请求。

默认上传会注册到客户归属 AIGC 素材组并返回 `asset://...`。如果服务器打开 `FACE_ASSET_SELF_SERVICE=true`，客户上传时还可以勾选“上传后加入人脸白名单”，Relay 会额外写入本地 `face_assets` 白名单；客户仍然不需要任何 BytePlus AK/SK/GroupId。

### 9.4 账号

显示余额、user_id、脱敏后的 **API Key** 信息、改密码、轮换 API Key + curl 用法示例。
完整 API Key 只在创建账号或重新生成时显示一次；客户可以拿最新 API key 直接走 SDK / curl 集成。

---

## 十、运维指令

### 10.1 看日志

```bash
ssh root@<server-ip>

# 应用日志
docker logs -f seedance-relay
docker logs --tail 200 seedance-relay

# Caddy 网关日志（HTTPS 请求 / 证书续签）
tail -f /var/log/caddy/video-example.log     # JSON 格式
journalctl -u caddy -f
```

### 10.2 重启 / 升级

```bash
cd /opt/seedance-relay

# 改了代码（.py 或 static/*.html）→ 必须重新构建
docker compose -f docker-compose.relay.yml up -d --build

# 只是想重启容器（如改完 .env.relay）
docker compose -f docker-compose.relay.yml restart

# Caddy 改完配置
systemctl reload caddy
```

### 10.3 备份

```bash
# 备份 SQLite（一定要走 .backup，不能直接 cp，WAL 模式有未提交数据）
ssh root@<server-ip> \
  "docker exec seedance-relay python3 -c \"
import sqlite3
src = sqlite3.connect('/data/relay.sqlite')
dst = sqlite3.connect('/data/relay.backup.sqlite')
src.backup(dst); dst.close(); src.close()
print('ok')
\""
scp root@<server-ip>:/opt/seedance-relay/data/relay.backup.sqlite \
    ./backups/$(date +%Y%m%d).sqlite

# 备份客户上传素材。生成结果默认 proxy-only，不长期落本地视频文件。
rsync -av root@<server-ip>:/opt/seedance-relay/data/uploads/ ./backups/uploads/
```

### 10.4 改全局默认加价兼容项

```bash
ssh root@<server-ip>
sed -i 's/^MARKUP_PCT=.*/MARKUP_PCT=0.5/' /opt/seedance-relay/.env.relay
docker compose -f /opt/seedance-relay/docker-compose.relay.yml restart
```

单个客户的实际计费优先用后台 `price_multiplier` 调整，最低 `1.0`，例如 `1.2` 表示按上游成本的 120% 计费。

### 10.5 模型 ID 与可选别名

默认不要改字节原生 `model id`。新增模型时，先把原生 ID 加入服务端 model registry，并同步价格和能力字段：

```python
NATIVE_MODEL_IDS = [
    ...,
    "byteplus-native-model-id-xxx",
]
```

如果管理员希望客户看到更短的名字，可以在 `.env.relay` 配置别名；不配置时就按原生 ID 展示和执行：

```bash
MODEL_ID_ALIASES_JSON={"customer-short-name":"dreamina-seedance-2-0-260128"}
```

别名只改变客户请求和 `/v1/models` 里的 `id`，实际转发给字节的 `model` 仍然是原生 ID。改完后：

```bash
scp relay_server.py root@<server-ip>:/opt/seedance-relay/
ssh root@<server-ip> "cd /opt/seedance-relay && \
  docker compose -f docker-compose.relay.yml up -d --build"
```

如果新模型还要走分档定价，同时编辑价格表里的对应字典。

### 10.6 紧急重置 admin 密码

```bash
ssh root@<server-ip>
NEW="<新强密码>"

# 直接用 Python 在容器里改密码哈希
docker exec seedance-relay python3 -c "
import sqlite3, bcrypt
h = bcrypt.hashpw('$NEW'.encode(), bcrypt.gensalt(10)).decode()
c = sqlite3.connect('/data/relay.sqlite')
c.execute(\"UPDATE users SET password_hash=? WHERE is_admin=1\", (h,))
c.commit(); print('ok')
"
```

---

## 十一、故障排查

| 现象 | 检查 |
|---|---|
| `curl /health` 返回 502 | `docker ps` 看 `seedance-relay` 是否在跑；`docker logs seedance-relay` 看启动错误 |
| 域名打不开 / SSL 报错 | `dig +short <domain>` 确认 A 记录；`journalctl -u caddy -n 100` 看证书申请是否被 Let's Encrypt 限流 |
| 客户提交报 503 `no_upstream_key` | 该用户在 admin 后台没填 BytePlus key 且 `.env.relay` 里也没设 `UPSTREAM_API_KEY` |
| 客户提交报 502 `upstream_error` | 上游 BytePlus 拒绝。日志看 sanitize 前的真实错误：`docker logs seedance-relay`。常见原因：模型未在 BytePlus 控制台激活、key 已禁用、账户余额不足 |
| 客户提交报 402 `insufficient_balance` | 余额不够预扣 `max_cost × price_multiplier`，admin 后台充值 |
| 任务一直 `queued` 不动 | 客户主动 GET 一次 `/v1/videos/{vid}` 触发同步；或后台脚本批量刷新 |
| 视频文件占用磁盘大 | `du -sh /opt/seedance-relay/data/videos`；旧视频可以归档到对象存储后删除（同时清 DB 里的 `local_video_path`） |
| 改了 `static/*.html` 网页没变 | 必须 `--build` 重新构建镜像，仅 `restart` 不生效（HTML 被 COPY 进镜像了） |
| `relay.sqlite` 损坏 | 用最近一次 `.backup` 文件覆盖 `/data/relay.sqlite` 后重启容器 |

---

## 附录：技术栈

- **Python 3.11** + FastAPI + Uvicorn + httpx（异步 HTTP）+ Pydantic v2
- **SQLite (WAL mode)** — 单文件 DB，零依赖
- **bcrypt** — 密码哈希
- **Docker + Docker Compose** — 单容器部署
- **Caddy 2** — 反向代理 + 自动 Let's Encrypt
- **Tailwind CSS (via CDN)** — 前端 SPA（无 build 步骤）

依赖见 `requirements.relay.txt`，全部固定主版本号确保部署可复现。
