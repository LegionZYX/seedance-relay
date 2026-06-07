# Alpha3 Customer Project Endpoint Map 更新计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Alpha3 将客户上游资源模型收敛为“每个大 B 客户一个 BytePlus Project，每个开放模型一个 endpoint mapping，后台用 enabled_models 控制模型开关”。

**Architecture:** Relay 继续隐藏 BytePlus Project、endpoint、asset group 和 endpoint key。后台负责创建客户 Project、按模型创建 endpoint、写入 `users.note.byteplus_endpoint_map`，生成时按客户请求模型选择 endpoint。模型升级通过批量脚本创建新 endpoint 并 JSON merge 更新 mapping。

**Tech Stack:** FastAPI/Python, SQLite, BytePlus IAM OpenAPI, ModelArk OpenAPI, unittest, static admin UI。

---

## Branch

本计划放在 `alpha3` 分支执行。`alpha2` 保留为当前上线/修复线；`alpha3` 承载资源模型收敛、UI 文案调整和批量模型升级工具。

## Scope

Alpha3 要做：

- 每个客户默认有自己的 BytePlus Project。
- 默认模型集合使用 `DEFAULT_CUSTOMER_MODEL_IDS`，默认等于全部 `NATIVE_MODEL_IDS`。
- 后台 `enabled_models` 继续作为客户模型开关。
- Provision 按客户开放模型创建 endpoint map。
- 生成请求必须按 `byteplus_endpoint_map` 路由；已启用但未映射的模型必须拒绝。
- Endpoint 创建和素材注册继续使用 `Moderation.Strategy=Skip`。
- 新增或整理批量模型升级脚本。
- 后台 UI 从“shared/dedicated 升级”改成“客户 Project 资源 / 模型 endpoint map”。

Alpha3 不做：

- 不引入 test/prod 上游状态。
- 不给客户暴露 BytePlus 内部资源。
- 不新增 SFW/NSFW 客户开关。
- 不在 Relay 增加 prompt 内容审核层。

## Task 1: 规格收敛冻结

**Files:**
- Modify: `docs/plans/2026-06-06-alpha2-admin-final-spec.zh-CN.md`
- Reference: `docs/plans/2026-06-07-alpha2-customer-project-model-endpoint-design.zh-CN.md`

**Steps:**

1. 确认 spec 已新增“客户 Project + 全模型 Endpoint Map”章节。
2. 确认旧 shared/manual/auto dedicated 路线被标记为历史参考。
3. 确认第 14 节实施路径以 Alpha3 新路径为准。
4. 确认最终结论不再描述“普通客户升级重要客户”。

**Verify:**

```bash
Select-String -Path "docs/plans/2026-06-06-alpha2-admin-final-spec.zh-CN.md" -Pattern "本轮收敛变更|新实施路径|旧实施顺序|最终结论"
```

Expected: 能看到新章节和旧章节历史参考标记。

## Task 2: 默认模型配置

**Files:**
- Modify: `relay_server.py`
- Modify: `.env.relay.example`
- Test: `tests/test_model_aliases.py`

**Steps:**

1. 添加 `DEFAULT_CUSTOMER_MODEL_IDS` 环境变量解析。
2. 默认值设为全部 `NATIVE_MODEL_IDS`。
3. `_enabled_models_for_user()` 在 `enabled_models` 为空时返回默认模型集合。
4. `.env.relay.example` 写出全量默认模型列表。

**Verify:**

```bash
python -m unittest tests.test_model_aliases -v
```

Expected: PASS。

## Task 3: 客户 Project 与多 endpoint provision

**Files:**
- Modify: `relay_server.py`
- Test: `tests/test_upstream_admin.py`

**Steps:**

1. Project 创建继续走 IAM `GetProject/CreateProject`。
2. Provision 读取客户开放模型列表。
3. 每个模型创建一个 ModelArk endpoint。
4. Endpoint 创建请求必须带 `ProjectName`、模型对应 `ModelReference`、`Moderation.Strategy=Skip`。
5. 每个 endpoint 等待 `Running`。
6. 写入 `users.note.byteplus_endpoint_map`。
7. `GetApiKey` 使用 `ResourceType=endpoint` 和全部 endpoint id。

**Verify:**

```bash
python -m unittest tests.test_upstream_admin -v
```

Expected: PASS。

## Task 4: 生成路由保护

**Files:**
- Modify: `relay_server.py`
- Test: `tests/test_iam_upstream.py`

**Steps:**

1. 创建视频前先校验模型是否启用。
2. 若存在 `byteplus_endpoint_map`，按请求模型查 endpoint。
3. 找不到 mapping 时返回 `endpoint_not_configured_for_model`。
4. 不允许 fallback 到主 endpoint 误打模型。

**Verify:**

```bash
python -m unittest tests.test_iam_upstream -v
```

Expected: PASS。

## Task 5: 批量模型升级工具

**Files:**
- Create: `deploy/upgrade_model_endpoints.py`
- Test: new or focused unittest

**Steps:**

1. 支持 `--dry-run`。
2. 输入旧 model id、新 model id、目标客户范围。
3. 遍历 active customers。
4. 对每个客户 Project 创建新模型 endpoint。
5. JSON merge 更新 `byteplus_endpoint_map`。
6. 不覆盖 key rotation、asset group、billing、audit 等 note 字段。
7. 输出脱敏执行报告。

**Verify:**

```bash
python deploy/upgrade_model_endpoints.py --dry-run --model dreamina-seedance-2-0-270101
```

Expected: 只打印计划，不写 DB，不打印密钥。

## Task 6: Admin UI 收敛

**Files:**
- Modify: `static/admin.html`
- Test: `tests/test_static_admin_ui.py`

**Steps:**

1. 删除或弱化 shared/dedicated 升级文案。
2. 显示客户 Project 状态。
3. 显示模型 endpoint map 状态。
4. 显示模型开关和 mapping 缺失警告。
5. 保留 endpoint key 轮换、密码重置、账单入口。

**Verify:**

```bash
python -m unittest tests.test_static_admin_ui -v
```

Expected: PASS。

## Task 7: Alpha3 Focused Gate

**Commands:**

```bash
python -m py_compile relay_server.py create_asset_white_label.py deploy/rotate_endpoint_keys.py tests/test_upstream_admin.py tests/test_iam_upstream.py
python -m unittest tests.test_upstream_admin tests.test_iam_upstream tests.test_asset_group_auto tests.test_model_aliases tests.test_static_admin_ui -v
```

Expected: PASS。

## Release Notes

Alpha3 发布前必须确认：

- 新客户开通后有独立 Project。
- 默认模型全量 endpoint map 已生成。
- 上传素材进入客户自己的 AIGC AssetGroup。
- Endpoint 和素材注册请求均为 `Moderation.Strategy=Skip`。
- 客户只使用 Relay API Key，不接触 BytePlus key。
- 模型升级脚本 dry-run 可审计，真实执行不覆盖无关 note 字段。

