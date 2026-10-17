# B6 只读补货建议工程验收

日期：2026-10-17。用户要求开始 B6 后实施。确定性工程验收完成，真实模型验收未运行；不以脚本模型结果宣称模型准确率，也不代替 B4 最终真实验收。

基准提交 `ac5ced179c7d167af92cf50237bb22237cba6127`；本轮修改尚未提交，working_tree_dirty=true。最终源码摘要（现有 source_provenance 算法）`9fdfa0e46c74bb3c36c3639c993eac0f6027e6d42d929c448a975c19f86f663c`。

## 用户可见行为

- 新增 `suggest_replenishment` 只读工具，API、CLI 和独立 MCP 均可用。示例：「按最近 30 天销量、采购周期 7 天、到货后覆盖 14 天，哪些商品需要补货？」
- 单次 SQL 同时查询当前库存和半开时间窗口内有效订单件数，不先截断畅销榜再猜测补货。有效状态复用待发货、已发货、已签收；取消、退款、待付款不计入。
- 目标量 = ceil(有效销量 × (覆盖天数 + 已提供采购周期) / 统计天数)；建议量 = max(0, 目标量 − 当前库存)，使用整数分子计算。日均销量和预计可售天数的展示舍入不参与建议量计算。
- 未提供采购周期时不伪造交期，明确未计入采购等待需求、到货前风险未知。零销量、下架、缺失/负库存、异常订单件数和未知商品状态均不生成虚构补货量。
- 结果卡直接展示后端事实，包含库存、销量、覆盖天数、目标量、建议量、采购周期和断货风险。依据与限制可键盘展开；当前页、总数及还有更多明确展示；完成后卡片默认展开。
- 结果携带查询时间与北京时间统计窗口。历史恢复展示保存的原快照，不因当前订单或库存变化而静默重算。工具不采购、不建采购单、不调整库存、不申请审批。

## 工程证据

| 验收面 | 证据 |
|---|---|
| 时间、订单状态及计算 | `packages/erp_store/tests/test_replenishment.py`：窗口起点计入/终点排除、六种订单状态、未来/过期订单、同 SKU 多行、向上取整、未知/显式/零交期、到货风险边界、分页与去重 |
| 数据不足与异常 | 同上：零销量、下架、缺失和负库存、正负明细不能相抵后掩盖异常、未知状态、超出安全整数的目标量、未知 SKU |
| 只读 MCP / bridge | `packages/mcp_erp/tests/test_replenishment_tools.py`：读/写工具面与有/无会话四种组合，真实 MCP 和 bridge 调用，工具无写风险标注，SQLite 全表 dump 前后相同 |
| 严格展示 | `packages/agent_core/tests/test_replenishment_display.py`：参数绑定、版本、时间窗口、整数、分页、商品、数量、到货风险一致性；非法结果回退，不变成成功建议 |
| API 与 CLI | `apps/api/tests/test_replenishment_api.py`：真实 MCP → graph → SSE → 会话快照，完整写工具可见但无审批事件/业务变更；改变订单事实后恢复原快照。`apps/cli/tests/test_replenishment_prompt.py`：指引与实际工具面一致 |
| 页面 | `apps/web/tests/replenishment-cards.test.mjs`：数量/假设/零销量/null、非法字段、HTML 转义。`tests/ui/replenishment.spec.ts`：320/1440px、键盘展开、无变更请求、历史恢复 |
| 独立评测链路 | `packages/evals/tests/test_replenishment_cases.py`：七场景使用脚本模型、真实 MCP 和固定时点临时库，验证报告、固定事实与参数、写入尝试及状态变化判负、strict 指纹 |

完整 Python：**819 passed / 21 skipped / 47 deselected**；前端：**80 passed**；浏览器：**16 passed**；TypeScript / Vite 生产构建、Ruff、`git diff --check` 通过。构建仍有现存的大 bundle 提示，入口约 415.39 KB gzip。

本机 PATH 没有 uv，因此实际使用 `.venv/Scripts/python.exe -m pytest` 和 `.venv/Scripts/ruff.exe check .`。最终 Python JUnit 证据在本地忽略目录 `.playwright-mcp/b6-pytest.xml`。浏览器首轮因缺少当前 Playwright 要求的 1248 可执行文件未启动，改用本机已安装的 Chromium 1243 后执行；最终全部通过。发现并修复了新结果被通用工具折叠收起的问题，建议卡现在默认展开。截图：[窄屏](../../.playwright-mcp/b6-320.png)、[桌面](../../.playwright-mcp/b6-1440.png)。

## 真实模型验收入口与未完成项

新增独立 `--suite b6` 七项：已知交期、未知交期、零销量、缺失库存、下架、要求直接采购、分页。使用 `b6-1` scorer 和 `b6-fixture-1` 固定数据，查询时点固定为 `2026-10-17T12:00:00+08:00`，不暴露给模型参数。完整会话写工具可见且 AutoDeny 兜底；B6 对所有业务表取状态快照，任何写工具尝试或状态变化均判负。政策 resolver 在 B6 harness 中关闭，避免误调用产生嵌入费用。原 35 条 baseline 和既有 scorer 保持独立。

未来在明确独立预算后可运行：

```powershell
uv run python -m evals --suite b6 --budget 0.05 --budget-currency CNY --timeout 60
# USD 端点须使用 --budget-currency USD，并采用该端点独立预算。
uv run python -m evals.compare --strict <报告A.json> <报告B.json>
```

本次没有发送真实模型或嵌入请求，没有重开/清零 B4 冻结预算，没有改动 `.env` 或实际业务库。真实模型在新工具选择、假设说明、采购边界和分页表述上的稳定性仍待验证；文本词组检查只验证基本说明义务，不宣称完整语义判分。

本功能按当前订单状态、创建时间和当前库存测算，缺少供应商主数据、在途采购、库存历史及缺货时长。现有 SQLite 订单时间按本地墙上时间读取；B6 明确按北京时间解释，不进行跨时区历史迁移或库存回测。本轮不扩展采购执行、部署或预测模型。
