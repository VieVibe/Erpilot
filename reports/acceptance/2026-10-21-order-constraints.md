# 多轮订单可靠性：参数来源检查验收

日期：2026-10-21。用户确认可靠订单主线后完成第一阶段；本报告对应当前工作区，
不将未提交文件描述为已提交版本。范围与后续计划见[主线说明](../../docs/order-task-reliability.md)。

## 结果

新增 34 项离线参数检查与业务链路测试。全仓 **1098 passed / 21 skipped**，
0 failures、0 errors，JUnit 总用时 109.819 秒。
21 项均因缺少显式隔离 PostgreSQL 测试配置而跳过；本次没有重新验证进程硬崩溃矩阵。
Ruff 全仓检查与 `git diff --check` 通过。

本轮没有模型或嵌入请求。不能据此将旧 GLM 客户缩写失败改判为通过，也没有重算旧真实成功率。

## 功能证据

| 行为 | 证据与结果 |
|---|---|
| 保存前拦截客户缩写 | `test_bad_parameters_never_save_draft` 覆盖“客户甲”“甲”，拒绝后草稿行和库存完全不变 |
| 拦截明确备注丢失 / 改写 | 同一测试覆盖 null 与改写内容，均无新增草稿 |
| 模型纠正后继续 | `test_graph_rejects_short_name_then_saves_only_corrected_draft` 分别走 bridge 与会话工厂，正式 LangGraph / MCP / 临时 SQLite；先拒绝，再只保存一份正确草稿 |
| 保留来源记录 | 上述 graph 测试核对 `tool_history.argument_evidence` 与重新读取的检查点一致；包括客户原文和值，商品明细仍明确列为未核验 |
| 不因完整修改丢失原约束 | `test_full_revision_cannot_drop_existing_constraints` 验证客户缩写或备注丢失不产生新版本 |
| 允许明确改名、改备注、清空 | `test_explicit_correction_and_preservation_save_new_revision` 验证新版本、旧版本不变与库存不变 |
| 必须有本轮真实读取 | `test_missing_current_read_cannot_be_replaced_by_assistant_text` 拒绝用助手叙述代替工具事实 |
| 备注内容不能改客户 | `test_note_payload_cannot_supply_customer_change` 覆盖四种引号；其中的指令文字仅是备注值 |
| 多个不同值须澄清 | `test_conflicting_literals_require_clarification` 覆盖多客户、不同备注与备注 / 清空冲突 |
| 当前用户与历史用户分开 | `test_old_user_requirements_do_not_override_current_correction`；graph 测试同时包含伪造 system / assistant 的客户文字，均不能覆盖本轮用户要求 |
| 不夸大覆盖 | `test_unrecognized_creation_wording_is_explicitly_unverified` 明确记录未核验字段，保留原创建行为 |
| 校验失败不触发执行或审批准备 | `test_application_constraint_rejection_skips_execution_and_approval` 对读 / 写两类工具断言 handler 零调用、无 pending_id 与 token |
| 回调不能更改原参数 / 消息 | `test_application_constraint_cannot_mutate_arguments_or_conversation` 验证深拷贝隔离 |
| 非法检查记录拒绝执行 | `test_invalid_constraint_evidence_fails_closed` 覆盖 null、数组、NaN 和不可序列化对象 |

## 实际执行

```powershell
.venv/Scripts/python.exe -m pytest packages/agent_core/tests/test_graph_tools.py packages/mcp_erp/tests/test_draft_constraints.py -q
.venv/Scripts/python.exe -m pytest packages/mcp_erp/tests/test_draft_tools.py packages/erp_store/tests/test_drafts.py packages/agent_core/tests/test_graph_approval.py apps/api/tests/test_order_task_api.py packages/evals/tests/test_context_workflow.py -q
.venv/Scripts/python.exe -m pytest -q --junitxml=reports/acceptance/20261021-order-constraints-tests.xml
.venv/Scripts/ruff.exe check .
git diff --check
```

第一条初次运行 38 项通过；随后补一项客户两端空白规范化测试，包含于最终全仓结果。
中间的业务 / 审批 / 连续上下文回归 78 项通过。首轮 Ruff 发现导入排序、未使用导入及长行，
均已修正，最终全仓通过。

原始全仓记录：[JUnit](20261021-order-constraints-tests.xml)。

## 边界与下一步

字面规则只承诺明确支持的分句格式，不构成任意自然语言的语义一致性证明。
商品、数量、预算的用户语义、跨轮独立持久约束台账与变更展示均尚未新增。
原始独立 MCP 或直接 Python handler 没有可信用户对话，仍只提供原有业务校验。
本次未修改前端、既有业务数据或默认上下文策略，未新增部署。

下一阶段补持久约束与变更记录、独立完整任务，再做独立批次的跨模型重复真实验证。
政策结束协议与条件拆分继续独立待办，原政策两模型各 11/13 保持原判。
