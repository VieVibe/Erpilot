# RAG 原文高亮与证据回显验收

日期：2026-10-17。范围为现有政策问答和取消资格引用的工程展示；确定性验证完成，本轮未调用真实模型或嵌入服务。工作区原有 B6 等未提交改动继续保留，本文不重判历史模型结果。

## 可见行为

- 普通 `search_policy` 从原始 JSON 改为默认展开的政策检索证据卡。卡片显示规范化查询、服务端查询时间、引用编号、完整片段原文、来源文件、政策版本、生效区间和检索相似度。
- 原文中与查询相同的中文连续片段、英文和数字用浅色高亮。中文匹配最短两个字；英文和数字有边界检查，`A100` 不标记 `A1001`，`7` 不标记 `17`。保留原文全部字符、空白、标点和 Markdown 符号，不生成或改写引文。查询最多取前 500 字符，匹配片段最长 12 字、最多 32 项；这是定位辅助，不作语义蕴含或结论准确性保证。
- 取消资格卡的可展开引用复用相同的原文、版本、分数和高亮组件，查询词使用工具实际采用的“订单取消条件”。资格和审批边界继续由原有契约控制。
- 无命中、未生效、版本冲突和检索失败明确显示证据缺口；空引用不生成编号、引文或高亮。相似度按原检索分数展示，不换算置信度或准确率；模型侧 `note` 指引不直接呈现在卡片正文。
- 历史恢复展示保存的原查询时间和引用，不重新检索、不用当前结果覆盖旧快照。缺少时间/版本的新旧不完整契约回退原始详情，不补造证据。

## 契约与验证

`search_policy` 在原 `policy_state/matches/note` 上增补 `schema_version=1`、`query` 和 `queried_at`。查询时点既用于筛选生效政策，也用于回显。`agent_core` 增加 `policy_search` 展示投影，复用引用校验，不引入 ERP 或 RAG 依赖；前端独立校验版本、查询绑定、时间、分数、生效区间和版本一致性。

| 验证面 | 证据 |
|---|---|
| MCP 查询元数据 | [test_policy_tools.py](../../packages/mcp_erp/tests/test_policy_tools.py)：规范化查询、UTC 查询时间、政策版本和生效时间；异常不泄露底层错误 |
| 展示契约 | [test_policy_search_display.py](../../packages/agent_core/tests/test_policy_search_display.py)：查询错配、非法版本/分数/时间、过期/未来引用、重叠版本、超过 k、无命中、失败和旧结果降级 |
| 真实工程链路 | [test_policy_search_api.py](../../apps/api/tests/test_policy_search_api.py)：fake embedder 和脚本模型通过真实 MCP → graph → SSE → 会话快照；命中/无命中/不可用三场景均仅检索一次、无审批事件、SQLite 全表 dump 不变；服务重建后保存同一 display |
| 语料一致性 | 同一 API 测试逐项核对浏览器 fixture 的来源、标题、原文及版本元数据与 `load_corpus()`、实际工具引用结构完全一致。截图中的分数为确定性 UI fixture，不能署为真实嵌入检索结果 |
| 前端渲染 | [policy-search-cards.test.mjs](../../apps/web/tests/policy-search-cards.test.mjs)：中文重复词、中英文/数字边界、空查询词、特殊符号和原文还原；HTML/来源按文本转义；非法/错配/过期证据不变成有效卡片 |
| 浏览器 | [policy-search.spec.ts](../../apps/web/tests/ui/policy-search.spec.ts)：320/1440px、默认可见原文和高亮、无横向溢出、历史零新请求、缺口和旧结果降级；原取消资格、工作台与 B6 浏览器回归通过 |

完整 Python 回归：**842 passed / 21 skipped / 47 deselected**，JUnit 保存于本地忽略目录 `.playwright-mcp/rag-evidence-pytest.xml`。此轮采集后补充了浏览器 fixture 的语料一致性检查，含该新增检查的 API 定向回归 **4 passed**。前端完整组件/协议回归 **84 passed**；浏览器全量 **22 passed**，fixture 元数据与语料对齐后再跑 RAG 浏览器 **6 passed**。TypeScript / Vite 构建、全仓 Ruff 与 `git diff --check` 通过。现有 bundle 体积提示仍存在，入口约 416.55 KB gzip。

本机实际使用 `.venv/Scripts/python.exe`；浏览器使用已安装的 Chromium 1243，通过 `PLAYWRIGHT_CHROMIUM_EXECUTABLE` 指定。开发阶段发现 `Intl.Segmenter` 会将“退货”拆成单字，首轮高亮检查失败；已改为不依赖运行时词典的字面匹配，最终回归如上。

## 截图与边界

- [桌面证据卡](../rag/20261017-evidence-card-1440.png)
- [320px 证据卡](../rag/20261017-evidence-card-320.png)

截图由脚本 API fixture 驱动，原文及元数据来自仓库政策语料。此次未修改政策正文、索引、检索阈值、嵌入配置、实际业务库或模型预算。新增 UI 和快照契约尚未做真实模型完整复测；原 B5 真实模型及嵌入校准报告保持其原版本含义，不与本轮离线结果合并成新成功率。
