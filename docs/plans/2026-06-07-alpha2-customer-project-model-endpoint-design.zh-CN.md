# Alpha2 客户 Project 与模型 Endpoint 收敛设计

更新时间：2026-06-07

## 结论

Alpha2 不再把客户分成 shared、test、dedicated 等多条主路径。当前商业前提是所有客户都是大 B 客户，因此默认采用：

```text
一个客户 = 一个 BytePlus Project
一个客户 Project = 一组模型 Endpoint + 一个 AIGC AssetGroup
客户可用模型 = Relay 后台 enabled_models 开关控制
客户生成路由 = users.note.byteplus_endpoint_map 按模型选择 endpoint
```

`Project` 是客户级资源容器和账单/权限边界；`Endpoint` 是某个模型的实际调用入口；`AssetGroup` 和 `Endpoint` 必须使用同一个 `ProjectName`。

## 默认开户规则

新客户开通上游资源时：

1. Relay 用 IAM `GetProject/CreateProject` 确保客户 Project 存在，默认 `ProjectName = customer_slug`。
2. Relay 读取该客户当前 `enabled_models`。
3. 如果 `enabled_models` 为空或未设置，则使用 `DEFAULT_CUSTOMER_MODEL_IDS`。
4. 默认 `DEFAULT_CUSTOMER_MODEL_IDS` 等于所有 `NATIVE_MODEL_IDS`，即所有模型默认开放。
5. Relay 为每个开放模型创建一个 ModelArk Endpoint，并写入：

```json
{
  "byteplus_project_name": "peterlv",
  "byteplus_endpoint_id": "ep-main",
  "byteplus_endpoint_map": {
    "dreamina-seedance-2-0-260128": "ep-standard",
    "dreamina-seedance-2-0-fast-260128": "ep-fast",
    "seedance-1-5-pro-251215": "ep-seedance15"
  },
  "modelark_asset_group_id": "group-peterlv"
}
```

`byteplus_endpoint_id` 只保留为兼容主 endpoint 字段；新路由必须优先使用 `byteplus_endpoint_map`。

## 后台模型开关

后台的 `enabled_models` 是客户可见和可调用模型开关：

- `enabled_models = null` 或空字符串：使用 `DEFAULT_CUSTOMER_MODEL_IDS`。
- `enabled_models = []`：客户不允许调用任何模型。
- `enabled_models = ["model-a", "model-b"]`：客户只能看到并调用这些模型。

如果管理员给客户新增模型，必须同时确认该客户 Project 下已经存在对应 endpoint，并把模型写入 `byteplus_endpoint_map`。如果模型已开放但没有 endpoint mapping，Relay 应拒绝调用，避免把请求误打到错误 endpoint。

## 模型升级与批量修改

模型升级时不要直接在业务代码里到处替换旧 model id。推荐流程：

1. 先把新模型加入 `NATIVE_MODEL_IDS` / `MODEL_REGISTRY`。
2. 如果希望客户请求旧 ID 也能继续工作，配置 `MODEL_ID_ALIASES_JSON`，例如：

```json
{
  "dreamina-seedance-2-0-260128": "dreamina-seedance-2-0-270101"
}
```

3. 为每个客户 Project 创建新模型 endpoint。
4. 批量更新每个客户 `users.note.byteplus_endpoint_map`：

```json
{
  "dreamina-seedance-2-0-270101": "ep-new-standard"
}
```

5. 如果旧模型要下线，再批量从 `enabled_models` 中移除旧 ID，或把默认 `DEFAULT_CUSTOMER_MODEL_IDS` 改成新 ID 列表。
6. 保留旧 endpoint 一段观察期；确认无调用后再停用或删除。

批量迁移脚本应该只做三件事：

- 遍历 active customers。
- 按客户 `byteplus_project_name` 创建或确认新 endpoint。
- 以 JSON merge 方式更新 `byteplus_endpoint_map`，不要覆盖 note 里的账单、key 轮换、asset group 等其他字段。

## 运营边界

- 不再设计 test/prod 状态开关。
- 不再设计普通客户共享 Project 的主路径。
- 不把客户请求模型 ID 直接等同于 BytePlus endpoint ID。
- 不允许未映射模型 fallback 到主 endpoint。
- 不把 endpoint API key、IAM AK/SK、BytePlus endpoint id 暴露给客户。

