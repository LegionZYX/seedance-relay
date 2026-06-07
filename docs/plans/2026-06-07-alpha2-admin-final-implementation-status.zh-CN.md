# Alpha2 管理后台最终功能实施状态

更新时间：2026-06-07

## 已完成

- 素材上传后注册为 BytePlus asset，并在素材库返回 `asset_id`、`asset_url`、`suggested_content_block`。
- 客户素材隔离：生成时校验 `asset://...` 必须属于当前客户，防止引用其他客户素材。
- 客户可删除自己上传的素材；删除 BytePlus asset 通过 `asset_delete_requests` 支持 `local_only`、`admin_batch`、`auto` 模式。
- 管理员可批量执行或取消 BytePlus asset 删除请求。
- 管理员可协助客户重置密码：`POST /admin/users/{user_id}/password/reset`。
- 账单第一版：预览、保存、客户 CSV 导出、内部 CSV 导出、标记 paid。
- endpoint key 自动轮换脚本：`deploy/rotate_endpoint_keys.py`。
- IAM 能力检查：`GET /admin/upstream/iam-capabilities`。
- 客户 upstream 配置读取/保存：`GET/PATCH /admin/users/{user_id}/upstream`。
- 后台 dry-run / 创建独立客户 project、endpoint、asset group 的 provision job：`POST /admin/users/{user_id}/upstream/provision` 与 `upstream_provision_jobs`。
- provision job 查询：`GET /admin/upstream/provision-jobs/{job_id}`。
- 单客户 endpoint API key 手动轮换：`POST /admin/users/{user_id}/upstream/endpoint-key/rotate`。
- 管理后台客户详情页已增加 Customer Endpoint 配置区，可保存配置、dry-run、创建独立 endpoint、开启自动轮换、手动轮换 endpoint key。
- `.env.relay.example`、`README.md`、`API_DOCS.md` 已补充相关开关与使用说明。
- 自动化测试已覆盖素材删除、密码重置、账单、endpoint key 自动轮换、upstream 管理接口、静态 UI 回归。

## 部分完成 / 后置

- BytePlus 控制面 adapter 已集中到 `_provision_customer_upstream_resources`；如果 BytePlus OpenAPI 的真实字段名与当前 adapter 不一致，只需要调整该 adapter。
- 账单导出当前完成 CSV；XLSX / PDF 后置。
- 本地上传文件自动 retention 清理后置；当前已支持删除与本地软删除字段。

## 未完成

- 暂无本轮 spec 内必须先完成的阻断项。

## 当前测试口径

- `tests/test_upstream_admin.py` 覆盖 IAM capability、客户 upstream 配置、dry-run/真实 provision job、endpoint key 手动轮换与密钥脱敏。
- `tests/test_endpoint_key_rotation.py` 覆盖 endpoint key 到期自动轮换、失败保留旧 key、失败错误脱敏。
- `tests/test_billing.py` 覆盖账单预览、保存、客户/内部 CSV 导出、标记 paid。
- `tests/test_uploads.py` 覆盖素材注册、客户隔离、客户删除与 BytePlus asset 删除请求。
- `tests/test_static_admin_ui.py` 覆盖管理后台关键入口与模板基础完整性。
