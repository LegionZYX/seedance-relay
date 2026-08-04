# Alpha2 管理后台最终功能实施状态

更新时间：2026-06-07

## 已完成

- 素材上传后注册为 BytePlus asset，并在素材库返回 `asset_id`、`asset_url`、`suggested_content_block`。
- 客户素材隔离：生成时校验 `asset://...` 必须属于当前客户，防止引用其他客户素材。
- 客户可删除自己上传的素材；删除 BytePlus asset 通过 `asset_delete_requests` 支持 `local_only`、`admin_batch`、`auto` 模式。
- 管理员可批量执行或取消 BytePlus asset 删除请求。
- 管理员可协助客户重置密码：`POST /admin/users/{user_id}/password/reset`。
- 账单第一版：预览、保存、客户/内部 CSV 导出、XLSX 导出、客户 PDF 导出、账单筛选、客户账单视图、标记 paid。
- endpoint key 自动轮换脚本：`deploy/rotate_endpoint_keys.py`。
- IAM 能力检查：`GET /admin/upstream/iam-capabilities`。
- 客户 upstream 配置读取/保存：`GET/PATCH /admin/users/{user_id}/upstream`。
- 后台 dry-run / 创建独立客户 project、endpoint、asset group 的 provision job：`POST /admin/users/{user_id}/upstream/provision` 与 `upstream_provision_jobs`。
- provision job 查询：`GET /admin/upstream/provision-jobs/{job_id}`。
- 单客户 endpoint API key 手动轮换：`POST /admin/users/{user_id}/upstream/endpoint-key/rotate`。
- 管理后台客户详情页已增加 Customer Endpoint 配置区，可保存配置、dry-run、创建独立 endpoint、开启自动轮换、手动轮换 endpoint key。
- 独立 endpoint 创建已按真实流程修正：Project 走 IAM `GetProject/CreateProject`，Endpoint/AssetGroup/GetApiKey 走 ModelArk；`CreateAssetGroup` 固定带 `GroupType=AIGC`，Endpoint 和素材注册固定 `Moderation.Strategy=Skip`。
- 客户模型权限默认同步 `NATIVE_MODEL_IDS`，包含 `dreamina-seedance-2-0-fast-260128`、1.5、1.0 pro、1.0 fast、lite t2v/i2v。
- dedicated 客户已支持 per-model endpoint mapping：`users.note.byteplus_endpoint_map` 可把客户可见模型映射到不同 BytePlus endpoint；生成时按请求模型选择 endpoint，未配置映射的模型返回 `endpoint_not_configured_for_model`，不会误打默认 endpoint。
- 后台自动开通 job 已按客户可用模型创建 per-model endpoints，并写入 `users.note.byteplus_endpoint_map`；`users.note.byteplus_endpoint_id` 仅作为兼容主 endpoint 字段保留。
- 管理后台客户详情已从旧的 Customer Endpoint 升级叙事收敛为 Customer Project Resources / Model Endpoint Map，能展示 mapping 状态和缺失告警。
- 批量模型升级脚本已新增：`deploy/upgrade_model_endpoints.py`，支持 dry-run、指定客户、创建新模型 endpoint、JSON merge 更新 `byteplus_endpoint_map`，且不覆盖 asset group、key rotation、billing 等 note 字段。
- endpoint key 手动轮换和自动轮换已支持 endpoint map：存在 `byteplus_endpoint_map` 时，默认按全部 endpoint ids 请求 endpoint-scoped key；若切换 `ENDPOINT_KEY_RESOURCE_MODE=per_endpoint` 或 auto fallback，则写入 `users.note.byteplus_endpoint_key_map`，生成时按请求模型选择对应 key，API/UI 只返回脱敏状态。
- `deploy/cleanup_upload_files.py` 已支持上传 retention 清理：只清理已注册为 `asset://...` 且超过保留期的本地临时文件，保留 DB 素材记录并写入审计。
- `deploy/process_asset_delete_requests.py` 已支持异步处理 `queued` BytePlus asset 删除请求。
- `GET /admin/upstream/endpoint-map-health` 与后台 Endpoint Map Health 面板已支持 mapping 缺失、额外 mapping、key map 状态检查。
- `GET /admin/upstream/quota-reminders` 与后台 Quota Center Reminders 已支持 Project / endpoint / AssetGroup 本地计数提醒。
- Peter 已按多 endpoint 模式开通并验证 4 个 Running endpoint：`dreamina-seedance-2-0-260128`、`seedance-1-5-pro-251215`、`seedance-1-0-pro-250528`、`seedance-1-0-pro-fast-251015`；BytePlus endpoint 均为 `Moderation.Strategy=Skip`。
- 已修复两个线上发现的问题：普通保存用户表单不再覆盖 upstream note 字段导致自动轮换对勾消失；新账号开通独立 endpoint 不再错误调用 ModelArk `CreateProject`。
- `.env.relay.example`、`README.md`、`API_DOCS.md` 已补充相关开关与使用说明。
- 已新增后台失效应急手册：`docs/ops/alpha2-byteplus-dedicated-endpoint-ai-runbook.md`，记录真实 IAM Project + ModelArk Endpoint + AIGC AssetGroup + GetApiKey 执行顺序。
- 自动化测试已覆盖素材删除、密码重置、账单、endpoint key 自动轮换、upstream 管理接口、静态 UI 回归。

## 部分完成 / 后置

- BytePlus 控制面 adapter 已集中到 `_provision_customer_upstream_resources`；当前已按真实 IAM Project 创建方式修正。如果 BytePlus OpenAPI 后续字段变化，只需要调整该 adapter 和应急 runbook。
- BytePlus Quota Center 仍为本地计数提醒，尚未接入 BytePlus 实时配额 API。

## 未完成

- 暂无本轮 spec 内必须先完成的阻断项。

## 当前测试口径

- `tests/test_upstream_admin.py` 覆盖 IAM capability、客户 upstream 配置、dry-run/真实 provision job、endpoint key 手动轮换与密钥脱敏。
- `tests/test_iam_upstream.py` 覆盖 dedicated 客户按 `byteplus_endpoint_map` 路由到对应 endpoint，以及未映射模型不调用上游。
- `tests/test_endpoint_key_rotation.py` 覆盖 endpoint key 到期自动轮换、失败保留旧 key、失败错误脱敏。
- `tests/test_billing.py` 覆盖账单预览、保存、客户/内部 CSV 导出、标记 paid。
- `tests/test_uploads.py` 覆盖素材注册、客户隔离、客户删除与 BytePlus asset 删除请求。
- `tests/test_static_admin_ui.py` 覆盖管理后台关键入口与模板基础完整性。
