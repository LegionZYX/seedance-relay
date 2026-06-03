# Video API — Seedance 视频生成白标反代

一套**白标 SaaS 系统**：把 BytePlus Seedance 视频生成 API 包装成自己品牌的服务对外出售。
对客户隐藏底层供应商，内置用户系统、余额预扣、对账、本地视频落地、管理员 / 用户两套 Web 后台。



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
| **白标反代** | 客户调你的 `video.8864k.com` API，看不到 BytePlus / Seedance / volces.com 任何字样 |
| **模型 ID 映射** | 客户用 `video-pro / video-lite`，底层映射到 `dreamina-seedance-2-0-260128` 等真实 ID |
| **用户系统** | 邮箱 + bcrypt 密码登录，独立的客户 API key (`sk-xxxx`)，可全 Web 操作 |
| **每客户独立 BytePlus key** | 每个客户在你 BytePlus 控制台单独申请一把 key，1:1 对账，互不影响 |
| **余额预扣 + 自动结算** | 提交任务前先冻结估算上限，任务终态后按真实 token 数实扣并退差额 |
| **本地视频落地** | 任务成功后异步把视频拉到本地永久保存，绕开 BytePlus 24h 临时 URL 失效问题 |
| **错误信息脱敏** | 上游错误信息中的供应商字样被正则替换为你的品牌名 |
| **管理员后台** | 浏览器开账号、充值、对账、查任务流水 |
| **用户后台** | 浏览器生成视频、看历史、播放、查余额、查 API key |
| **30% Markup** | 默认在 BytePlus 真实价上加价 30% 收客户，可改 |

### 1.2 支持的客户层模型

| 客户层 ID | 底层 BytePlus 模型 | 用途 |
|---|---|---|
| `video-pro` | `dreamina-seedance-2-0-260128` | 高质量 (Seedance 2.0)，支持 480p / 720p / 1080p |
| `video-pro-fast` | `dreamina-seedance-2-0-fast-260128` | 2.0 快速版 |
| `video-1.5-pro` | `seedance-1-5-pro-251215` | 中端，支持 draft 草稿模式 |
| `video-1080p` | `seedance-1-0-pro-250528` | 1080p 专用 |
| `video-720p` | `seedance-1-0-pro-fast-250528` | 720p 专用，较快 |
| `video-lite` | `seedance-1-0-lite-t2v-250428` | 纯文本→视频，便宜快速 |
| `video-lite-i2v` | `seedance-1-0-lite-i2v-250428` | 图片→视频，便宜快速 |

> 客户最终能调成功的模型 = 你 BytePlus 账号已激活的模型。未激活的会返回脱敏后的 "upstream not available"。

### 1.3 计费流程

```
客户提交 ─→ relay 估算成本 ─→ 余额检查
                                ├─不够→ 402 直接拒绝（不打上游，零成本）
                                └─够 → 冻结 max_cost × (1 + markup)
                                       ↓
                              转发 BytePlus（用客户专属 key）
                                       ↓
                              客户查询状态 → relay 同步上游
                                       ↓
                              终态 succeeded → 真实 cost × (1 + markup) 实扣
                                              退差额给客户
                                              异步落地视频到本地
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
                │  video.8864k.com        │
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
                     │                  └─ videos/          (落地视频文件)
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
ADMIN_EMAIL=admin@yourbrand.com
BRAND_NAME=Your Brand
DB_PATH=./data/relay.sqlite                  # 本地开发用相对路径
VIDEO_DIR=./data/videos
UPLOAD_DIR=./data/uploads
```

### 4.3 启动

```bash
uvicorn relay_server:app --host 0.0.0.0 --port 8002 --reload
```

打开 http://localhost:8002/admin/ui/ 用 `ADMIN_EMAIL` + `ADMIN_PASSWORD` 登录。

---

## 五、部署到服务器（核心，逐步操作）

> 以全新 Ubuntu 22.04 服务器为例。若部署目标是本仓库当前的服务器 `154.92.16.74`，跳到 [5.7 复用已有服务器更新代码](#57-复用已有服务器只更新代码)。

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

在域名服务商把你的子域名（如 `video.yourbrand.com`）的 **A 记录**指到服务器 IP。
等几分钟 DNS 生效：

```bash
dig +short video.yourbrand.com    # 应该返回你的服务器 IP
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
PUBLIC_DOMAIN=video.yourbrand.com
BRAND_NAME=Your Brand Studio

# 数据库 + 视频路径（容器内路径，会挂载到宿主机 ./data）
DB_PATH=/data/relay.sqlite
VIDEO_DIR=/data/videos
UPLOAD_DIR=/data/uploads

# 上传中转站（留空时用 https://PUBLIC_DOMAIN/uploads/...）
UPLOAD_PUBLIC_BASE_URL=
UPLOAD_MAX_IMAGE_MB=10
UPLOAD_MAX_VIDEO_MB=50
UPLOAD_MAX_AUDIO_MB=15

# 可选：服务端自动把上传 URL 注册成 asset://
# 这些只放服务器环境里，用户端不需要也拿不到。
ASSET_AUTO_REGISTER_UPLOADS=false
ASSET_AUTO_REGISTER_PURPOSES=image,video,audio
ASSET_AUTO_REGISTER_WAIT_SECONDS=0
ASSET_AUTO_REGISTER_WAIT_INTERVAL=3
ASSET_AUTO_REGISTER_SKIP_MODERATION=false
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
ADMIN_EMAIL=admin@yourbrand.com
ADMIN_PASSWORD=<生成一个强密码>
ADMIN_KEY=<随机串，供 curl/脚本绕过登录用>

# Markup 比例（默认 30%）
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
sed "s/video\\.8864k\\.com/video.yourbrand.com/g" deploy/caddy_video.snippet \
  >> /etc/caddy/Caddyfile

# 重载 Caddy（首次会自动申请 Let's Encrypt 证书）
systemctl reload caddy

# 看 Caddy 日志确认证书签发
journalctl -u caddy -f
```

公网验证：

```bash
curl https://video.yourbrand.com/health
# 应返回 200 + JSON
```

浏览器打开 `https://video.yourbrand.com/admin/ui/`，用 `ADMIN_EMAIL` + `ADMIN_PASSWORD` 登录。

### 5.7 复用已有服务器（只更新代码）

如果服务器上已经跑过这套系统（如 `154.92.16.74` 的 `/opt/seedance-relay`），只想更新代码：

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
| `PUBLIC_DOMAIN` | `video.8864k.com` | 对外域名（用于生成视频 URL） |
| `BRAND_NAME` | `8864k Studio` | 品牌名（脱敏替换 / UI 标题） |
| `DB_PATH` | `/data/relay.sqlite` | SQLite 数据库路径 |
| `VIDEO_DIR` | `/data/videos` | 视频落地目录 |
| `UPLOAD_DIR` | `/data/uploads` | 上传中转站本地保存目录 |
| `UPLOAD_PUBLIC_BASE_URL` | 空 | 上传 URL 的公网 base；留空用 `https://PUBLIC_DOMAIN` |
| `UPLOAD_MAX_IMAGE_MB` | `10` | 图片上传白名单大小上限 |
| `UPLOAD_MAX_VIDEO_MB` | `50` | 视频上传白名单大小上限 |
| `UPLOAD_MAX_AUDIO_MB` | `15` | 音频上传白名单大小上限 |
| `ASSET_AUTO_REGISTER_UPLOADS` | `false` | 服务端是否自动把上传 URL 注册成 `asset://...` |
| `ASSET_AUTO_REGISTER_PURPOSES` | `image,video,audio` | 开关启用后哪些上传类型自动注册 |
| `ASSET_AUTO_REGISTER_WAIT_SECONDS` | `0` | 是否等待 asset 变 Active；0 表示只创建不等待 |
| `ASSET_AUTO_REGISTER_SKIP_MODERATION` | `false` | CreateAsset 时是否传 skip moderation |
| `BYTEPLUS_ACCESS_KEY_ID` | 空 | 服务端 asset registry AK，用户不可见 |
| `BYTEPLUS_ACCESS_KEY_SECRET` | 空 | 服务端 asset registry SK，用户不可见 |
| `MODELARK_ASSET_GROUP_ID` | 空 | 服务端 asset group，用户不可见 |
| `MODELARK_ASSET_AUTO_CREATE_GROUP` | `true` | 缺少 `MODELARK_ASSET_GROUP_ID` 时自动调用 `CreateAssetGroup` 并缓存到 SQLite |
| `MODELARK_ASSET_GROUP_NAME` | `relay-face-assets` | 自动创建素材组时使用的名称 |
| `MODELARK_ASSET_GROUP_DESCRIPTION` | `Relay self-service face asset whitelist` | 自动创建素材组描述 |
| `MODELARK_PROJECT_NAME` | 空 | 可选 Project 名；留空时不传 |
| `FACE_ASSET_ENFORCE` | `false` | 是否启用人脸/真人素材白名单闸门 |
| `FACE_ASSET_SELF_SERVICE` | `false` | 是否允许客户通过 `face_allowlist=true` 自助注册并加入白名单 |
| `FACE_ASSET_ALLOWLIST` | 空 | 逗号分隔的 `asset://...` 白名单 |
| `FACE_ASSET_ENFORCE_ROLES` | `reference_image,reference_video` | 哪些 role 必须走人脸白名单 |
| `ADMIN_EMAIL` | `admin@8864k.com` | 首次创建 admin 用的邮箱 |
| `ADMIN_PASSWORD` | 空 | 首次创建 admin 用的密码（启动时生效一次） |
| `ADMIN_KEY` | 空 | 备用 `X-Admin-Key` 头（脚本/curl 绕登录） |
| `MARKUP_PCT` | `0.3` | 全局默认加价比例（0.3 = +30%）；单个客户可在后台覆盖 |

---

## 七、API 接口总览

### 7.1 客户 API（`Bearer sk-xxxx` 鉴权）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查（无需鉴权） |
| GET | `/v1/models` | 列模型（脱敏后） |
| GET | `/v1/pricing` | 公开价格表 + 估算公式 |
| POST | `/v1/uploads` | 上传白名单媒体到中转站，返回公网 URL |
| POST | `/v1/uploads/from-url` | 登记客户已有公网素材 URL，返回可复用 content block |
| GET | `/v1/uploads` | 查看当前客户自己上传过的素材 |
| GET | `/v1/uploads/{id}` | 查看当前客户自己的单个素材 |
| POST | `/v1/videos/estimate` | **提交前估算成本**（不消耗 token） |
| POST | `/v1/videos` | 创建视频任务 |
| GET | `/v1/videos/{vid}` | 查任务（自动同步上游） |
| GET | `/v1/videos/{vid}/content` | 流式下载视频（优先本地） |
| DELETE | `/v1/videos/{vid}` | 取消（queued）/ 删除（终态） |
| GET | `/v1/videos` | 列自己的任务 |
| GET | `/v1/me` | 自己的余额 + API key |

上传中转站白名单：

| 类型 | MIME 白名单 | 默认大小上限 |
|---|---|---|
| 图片 | `image/jpeg`, `image/png`, `image/webp` | `UPLOAD_MAX_IMAGE_MB=10` |
| 视频 | `video/mp4`, `video/quicktime` | `UPLOAD_MAX_VIDEO_MB=50` |
| 音频 | `audio/mpeg`, `audio/wav`, `audio/x-wav` | `UPLOAD_MAX_AUDIO_MB=15` |

上传后会返回 `https://your-domain/uploads/YYYY/MM/DD/upl_xxx.ext`，这个 URL 是公开的，目的是让 Seedance 能直接拉取。不要把敏感文件传到中转站。

每次上传都会写入当前客户账号的素材记录。客户用自己的 API key 查询时，只会看到自己的素材：

```bash
curl https://video.yourbrand.com/v1/uploads \
  -H "Authorization: Bearer $KEY"

curl https://video.yourbrand.com/v1/uploads/upl_xxx \
  -H "Authorization: Bearer $KEY"
```

如果客户尝试访问其他账号上传的素材 ID，接口会返回 `404 upload_not_found`。

如果服务器打开了 `ASSET_AUTO_REGISTER_UPLOADS=true`，响应会额外包含 `asset_url` / `asset_status`，并且 `suggested_content_block` 会自动使用 `asset://...`。用户端仍然只调用 `/v1/uploads`，不需要任何 AK/SK/GroupId。

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
curl -X POST https://video.yourbrand.com/admin/face-assets \
  -H "X-Admin-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"asset_url":"asset://asset-xxx","asset_type":"image","label":"approved actor"}'
```

查询当前白名单：

```bash
curl https://video.yourbrand.com/admin/face-assets \
  -H "X-Admin-Key: $ADMIN_KEY"
```

注意：`ASSET_AUTO_REGISTER_UPLOADS` 只是服务端自动注册素材 URL；它不等同于真实人脸授权。真实人脸素材应先按 BytePlus 的授权/验真流程进入可用资产库，再把得到的 `asset://...` 加入这里的白名单。

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
curl -X POST https://video.yourbrand.com/v1/uploads \
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
4. 客户价格层：全局默认走 `MARKUP_PCT`；后台可以给单个客户设置 `markup_pct` 覆盖。`markup_pct=0.30` 表示 BytePlus 成本上加价 30%。任务创建时会把该客户当时的 `markup_pct` 快照写入 `tasks.markup_pct`，后续结算不受之后改价影响。

后台调价：

```bash
curl -X PATCH https://video.yourbrand.com/admin/users/u_xxx \
  -H "X-Admin-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"markup_pct":0.45}'

# 清空客户独立价格，回到全局 MARKUP_PCT
curl -X PATCH https://video.yourbrand.com/admin/users/u_xxx \
  -H "X-Admin-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"markup_pct":null}'
```

客户估价与价格表会返回自己的有效价格：

```bash
curl https://video.yourbrand.com/v1/pricing \
  -H "Authorization: Bearer $KEY"
```

响应中的 `pricing_scope` 为 `customer` 表示客户独立价格；为 `global` 表示使用全局默认。

### 客户只用命令行时怎么走

有两种方式：

1. 客户本地有文件：继续走 multipart 上传。

```bash
curl -X POST https://video.yourbrand.com/v1/uploads \
  -H "Authorization: Bearer $KEY" \
  -F "file=@portrait.jpg;type=image/jpeg"
```

2. 客户已经有公网 URL：走 URL 入库，不需要把文件再传一遍。

```bash
curl -X POST https://video.yourbrand.com/v1/uploads/from-url \
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
  "model": "video-pro",
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
curl -X POST https://video.yourbrand.com/v1/uploads \
  -H "Authorization: Bearer $KEY" \
  -F "file=@portrait.jpg;type=image/jpeg"
# 返回里的 suggested_content_block 可直接放入 /v1/videos 的 content[]

# 1) 预估
curl -X POST https://video.yourbrand.com/v1/videos/estimate \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"video-pro","resolution":"720p","duration":5,
       "content":[{"type":"text","text":"a cat walking"}]}'

# 2) 创建
curl -X POST https://video.yourbrand.com/v1/videos \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"video-pro","resolution":"720p","duration":5,
       "content":[{"type":"text","text":"a cat walking"}]}'

# 3) 查任务
curl https://video.yourbrand.com/v1/videos/vid_xxx \
  -H "Authorization: Bearer $KEY"

# 4) 下载视频
curl https://video.yourbrand.com/v1/videos/vid_xxx/content \
  -H "Authorization: Bearer $KEY" -o video.mp4
```

如果没有打开自动注册，也可以由管理员在服务器上手动把 reference video 做成 provider 可识别的白名单 asset：

```bash
export BYTEPLUS_ACCESS_KEY_ID="your-access-key-id"
export BYTEPLUS_ACCESS_KEY_SECRET="your-secret-access-key"
export MODELARK_ASSET_GROUP_ID="your-asset-group-id"

python create_asset_white_label.py create \
  --url "https://video.yourbrand.com/uploads/YYYY/MM/DD/upl_xxx.mp4" \
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
| POST | `/admin/users` | 创建用户（可指定 BytePlus key、初始余额、客户加价率） |
| GET | `/admin/users/{id}` | 用户详情（含最近任务、最近素材 + 累计统计） |
| PATCH | `/admin/users/{id}` | 改余额 / 客户加价率 / BP key / 密码 / 启停 |
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
curl -X POST https://video.yourbrand.com/admin/users \
  -H "X-Admin-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
  -d '{
    "email":"newcustomer@example.com",
    "balance_usd": 50,
    "markup_pct": 0.30,
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

打开 `https://video.yourbrand.com/admin/ui/`，登录后看到 5 个核心 Tab：

### 8.1 概览

KPI 卡：活跃用户数、总任务数、素材数、白名单素材数、已收入、BytePlus 成本、毛利、冻结中。

### 8.2 用户管理

- 点 **"+ 开新账号"**，填邮箱、初始密码（留空自动生成）、初始余额、**BytePlus API key**（你在 BytePlus 控制台为该客户单独申请的）、BytePlus 标签（内部备注）。
- 创建成功立即弹出：客户 `sk-xxxx` API key + 临时密码。把两条发给客户即可。
- 每行可点开编辑：改余额 / 一键充值 / 替换 BP key / 重置密码 / 启停 / 看该用户最近 20 个任务和最近 20 个素材。

### 8.3 素材管理

全平台上传素材账本。后台可以按客户、素材类型、是否进入人脸白名单筛选；每条记录显示上传客户、原文件名、公开 URL、asset:// 注册状态、大小和上传时间。这里是排查客户“上传了但不会用/看不到”的第一入口。

### 8.4 对账

一行一个用户，列出他名下的任务数、tokens、BytePlus 真实成本、向客户收的钱、毛利。
拿这里的数字直接对比 BytePlus 控制台按 key 的账单。

### 8.4 任务

全平台任务流水（最新 100 条），含 prompt、tokens、上游成本、向客户收费、状态。

---

## 九、用户后台使用

客户拿 admin 发的邮箱+密码登录 `https://video.yourbrand.com/`，进入 4 个 Tab：

### 9.1 新建视频

完整表单：prompt / 参考素材 / 模型 / 分辨率 / 比例 / 时长 / seed / 是否生成音频 / 是否加水印。
**右下角实时显示预估成本**（前端调用 `/v1/videos/estimate`，按当前客户自己的 `markup_pct` 返回价格；网络未返回时才用本地公式兜底）。
余额不足时提交按钮自动禁用。

提交后自动跳到任务详情，每 8 秒轮询一次，succeeded 后直接 `<video>` 标签内嵌播放 + 下载按钮。

### 9.2 我的任务

卡片列表 + 缩略图 + 状态徽章 + tokens / 价格 / prompt。

### 9.3 素材库

客户可以上传图片/视频/音频素材，列表只返回当前 API key 自己的 `uploads.user_id` 记录。素材卡片支持“用作参考”和“复制引用”；点“用作参考”后，新建视频页会自动把该素材的 `suggested_content_block` 放进生成请求。

如果服务器打开 `FACE_ASSET_SELF_SERVICE=true`，客户上传时可以勾选“上传后加入人脸白名单”，Relay 会在服务端调用素材注册并返回 `asset://...`；客户仍然不需要任何 BytePlus AK/SK/GroupId。

### 9.4 账号

显示余额、user_id、**API Key**（带复制按钮）+ curl 用法示例。
客户也可以拿 API key 直接走 SDK / curl 集成。

---

## 十、运维指令

### 10.1 看日志

```bash
ssh root@<server-ip>

# 应用日志
docker logs -f seedance-relay
docker logs --tail 200 seedance-relay

# Caddy 网关日志（HTTPS 请求 / 证书续签）
tail -f /var/log/caddy/video-8864k.log     # JSON 格式
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

# 备份本地视频
rsync -av root@<server-ip>:/opt/seedance-relay/data/videos/ ./backups/videos/
```

### 10.4 改 Markup 比例

```bash
ssh root@<server-ip>
sed -i 's/^MARKUP_PCT=.*/MARKUP_PCT=0.5/' /opt/seedance-relay/.env.relay
docker compose -f /opt/seedance-relay/docker-compose.relay.yml restart
```

### 10.5 加新模型映射

编辑 `relay_server.py` 顶部的 `MODEL_MAP`，新增一行：

```python
MODEL_MAP = {
    ...
    "video-new-id": "byteplus-real-model-id-xxx",
}
```

然后：

```bash
scp relay_server.py root@<server-ip>:/opt/seedance-relay/
ssh root@<server-ip> "cd /opt/seedance-relay && \
  docker compose -f docker-compose.relay.yml up -d --build"
```

如果新模型还要走分档定价，同时编辑 `modelark/pricing.py` 的 `PRICING` 字典。

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
| 客户提交报 402 `insufficient_balance` | 余额不够预扣 `max_cost × (1 + markup)`，admin 后台充值 |
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
