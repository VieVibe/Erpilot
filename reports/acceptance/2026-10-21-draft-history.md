# 跨轮约束与草稿修改记录验收

日期：2026-10-21。承接保存前参数检查，完成业务版本内的来源持久化、实际变更记录、
只读查询及网页查看入口。对应当前工作区，不将未提交源码描述为已有提交。
范围、查询契约和限制见[功能说明](../../docs/draft-history.md)。

## 验证结果

| 验证 | 结果与证据 |
|---|---|
| Python 全仓 | **1125 passed / 21 skipped**，0 failure / error；总用时 114.949 秒，[JUnit](20261021-draft-history-tests.xml) |
| 跳过范围 | 21 项均要求显式隔离 PostgreSQL 配置；本轮不宣称重新跑过硬崩溃矩阵 |
| 前端协议 / 组件 | **89 passed**，含新增 5 项历史协议测试，[原始日志](20261021-draft-history-web-tests.log) |
| 前端构建 | TypeScript / Vite 通过，原有大 bundle 提示保留，[最终构建](20261021-draft-history-web-build.log) |
| 浏览器回归 | **26 passed**，含新增 4 项历史页面场景及最终布局检查，[最终日志](20261021-draft-history-browser-final.log) |
| 正式离线 API 演示 | **3 passed**，覆盖真实 API / MCP / 临时 SQLite；新增修改记录分页读取和刷新恢复断言，[最终日志](20261021-draft-history-demo-final.log) |
| 静态检查 | Ruff 全仓与 `git diff --check` 通过 |

相对上一阶段新增 27 项 Python 测试。普通 pytest 不运行付费 eval，浏览器和 API 演示均使用
脚本化模型 / 临时业务库。本轮没有真实模型或嵌入调用，旧模型成功率与失败保持原判。

## 关键证据

| 问题 | 验证 |
|---|---|
| 聊天丢失 / 重新创建实例 | `test_origins_survive_new_engine_lost_chat_and_quantity_edits` 使用新数据库引擎；`test_quantity_turn_persists_request_and_original_origins_after_new_factory` 使用新会话工具工厂，无旧聊天，修改仍保留原来源 |
| 字段来源与版本一起保存 | `task_constraints`、后端差异与业务报价在同一个不可变 snapshot 行中；`test_revision_and_origins_roll_back_together_after_insert_failure` 在真实 INSERT 后注入故障，回滚后只有原版本，无孤立来源记录 |
| 修改不能擅自丢掉原要求 | `test_saved_verified_requirements_cannot_be_dropped_without_new_evidence` 拦截客户缩写和丢失备注；`test_explicit_new_requirement_replaces_only_changed_field_origin` 允许明确新要求且只更新对应来源 |
| 手工表单明确修改 | `test_manual_form_has_explicit_source_and_can_change_or_clear_fields`；HTTP 测试确认表单修改与清空、旧版本来源以及新宿主读取 |
| 模型伪造来源 | 原始 MCP schema 不暴露 evidence，`test_raw_mcp_cannot_replace_saved_origin_or_supply_its_own_evidence` 验证普通或伪造参数调用均不能改掉已有核验要求 |
| 错误 / 不匹配材料 | `test_invalid_evidence_cannot_commit_revision` 覆盖错误请求哈希、未出现的原话、字段不符、未知字段及错误前序版本 |
| 不绕过审批 | `test_execution_evidence_handler_cannot_bypass_approval_wrapper` 禁止审批风险工具使用独立 evidence handler；原建单审批与恢复回归继续通过 |
| 并发与事实差异 | `test_concurrent_revision_has_one_matching_audit_and_keeps_origin` 验证两个旧版本编辑只成功一个；`test_quote_refresh_and_reorder_are_computed_from_actual_snapshots` 核对现价变化、顺序变化且不伪造数量变化 |
| 旧草稿不造依据 | `test_legacy_history_is_unknown_and_quantity_edit_does_not_invent_user_source` 去掉历史来源后只读查询不回写，旧记录来源保持未知 |
| 分页和会话边界 | 领域及 HTTP 验证倒序独占游标、无重复 / 漏项、非法参数；跨会话与不存在均为 not_found |
| 页面只读与异常处理 | 320 / 1440px 验证前后差异、来源标签、纯文本原话、分页及零写请求；失败重试、损坏记录拒绝、切换会话后的迟到结果隔离 |
| 实际完整链路 | 正式离线 API 演示创建两版草稿，浏览器检查第二行 20 → 50，刷新后记录仍存在；随后拒绝、重提并批准同一确定版本，原流程通过 |

## 实现取舍与修正

没有新增表、列或执行实际业务库迁移。新的来源和差异随新草稿版本保存；旧快照按需返回
未知来源。普通草稿响应只含当前约束与 `last_change`，完整记录按需分页读取，避免大量明细重复回显。

在接入业务层来源复核时，曾发现屏蔽备注后产生的尾部空白影响客户原话片段匹配，导致明确改名误拒绝；
已修正片段边界并通过原更名测试。截图复核还发现长记录会带动整个工作台滚动，已将滚动限制在
业务面板内；最终桌面浏览器检查顶部仍在视野中、工作台外层 scrollTop 为 0，26 项回归通过。
最终 320 / 1440px 截图已人工查看，脚本文字只作为转义文本显示。

本机 Playwright 使用已安装 Chromium 1243 的明确路径。Windows 下首次测试服务结束等待较长，
在测试断言完成后核对并停止本轮自建服务；最终 UI 回归复用本轮显式启动的独立服务，以避免同样的退出等待。
未停止其他项目服务。

## 执行入口

```powershell
.venv/Scripts/python.exe -m pytest -q --junitxml=reports/acceptance/20261021-draft-history-tests.xml
.venv/Scripts/ruff.exe check .
git diff --check
npm --prefix apps/web test
npm --prefix apps/web run build
```

本机浏览器测试在 `apps/web` 中设置 `PLAYWRIGHT_CHROMIUM_EXECUTABLE` 后运行：

```powershell
npm run test:ui -- --workers=2
npm run test:demo
```

演示服务只读写隔离临时数据库，不代表真实模型表现；正式服务器开启写能力仍需原有配置与审批。

## 剩余边界

字面规则仍是有限格式；不是任意自然语言的语义检查。`verified` 只涉及客户 / 备注的来源，
不代表商品、数量、预算或整个订单任务都已验证。查询 / 澄清但没有保存草稿的要求、未决问题和
其他业务约束还没有独立台账。没有登录、租户或多 worker 能力扩展。

本轮完成的是工程可靠性与离线链路验证。真实模型的连续任务改进需要独立样本、独立批次和
跨模型重复验收，不从本轮测试推算生产稳定率；原政策两模型各 11/13 保持原判。
