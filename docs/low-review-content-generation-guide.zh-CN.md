## 13. 低审查内容生成规范

本节用于签约客户了解“低审查”内容的正确接入方式。这里的“低审查”不是无规则生成，也不是绕过平台风控；它表示 Relay 不额外增加客户侧内容开关，最终是否生成成功仍取决于已启用模型、客户账号配置、素材授权和上游返回结果。

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
