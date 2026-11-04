# 真实模型重连、缺陷修复与最终验收

日期：2026-11-04。用户明确允许中断后继续连接，按[本轮计划](../../docs/plans/2026-11-04-real-model-reconnect.md)实施。

**核心订单真实验收已通过：同一修复后批次 4/4 流程、34/34 阶段；55 请求全部有 usage。
语义创建最终 7/7，修改最终 6/6；正式带登录 HTTP 链路的 6 项业务检查全部通过。**
工程期望、人工标签和通用模型准确率分别看待，实验模式仍默认 off。

## 最终结果与证据

模型均为 `glm-5.3-flash`，端点主机 `flashcoding.ai`。ERP 为独立合成数据，无真实业务订单。

| 范围 | 独立完整结果 | 请求/计量与已知估价 | 证据 |
|---|---|---|---|
| 订单 v2 | **4/4 流程、34/34 阶段**；预算、拒绝重提、价格变化、多草稿选择、版本变化、恢复与重放 | 55 请求 / 55 usage，CNY **0.052260** | [原始结果](20261104-reconnect-corrected-order-v2.json)、[输入](20261104-reconnect-corrected-order-v2-inputs.jsonl)、[账本](20261104-reconnect-corrected-order-v2-requests.jsonl) |
| 语义创建最终批次 | **7/7**；5 条模型候选及 2 条无调用的前置拒绝 | 5 请求 / 5 usage，CNY **0.001935** | [完整批次](20261104-reconnect-semantic-create-final-attempt-1/report.json)、[活动汇总](20261104-reconnect-semantic-create-final-campaign.json) |
| 语义修改最终批次 | **6/6**；3 条指定目标/版本修改及 3 条无调用拒绝 | 成功子批次 3 请求 / 3 usage，CNY **0.002145**；此前一笔超时保留为未知 | [完整成功批次](20261104-reconnect-semantic-revision-final-attempt-2/report.json)、[包含超时的活动汇总](20261104-reconnect-semantic-revision-final-campaign.json) |
| 正式 API 默认上下文 | **6/6 检查**：登录、真实模型草稿、版本核对、审批、库存、重复批准与操作者审计 | 2 请求 / 2 usage，CNY **0.002148** | [真实 HTTP 结果](20261104-reconnect-live-api-network.json)、[业务事实](20261104-reconnect-live-api-network-state.json)、[账本](20261104-reconnect-live-api-network-requests.jsonl) |

订单用例与 oracle 沿用冻结 v2，没有降低提交、实际读取、字段、库存、版本或答案要求。
审计对每个阶段使用原 oracle 重新评分，并核对账本和输入哈希：
[独立复核 JSON](20261104-reconnect-final-audit.json)。通过批次不与失败批次拼接成一个成功率。

订单验收使用实际 LangGraph/MCP/ERP 和结构化上下文视图；恢复控制为同一内存 saver 上重建宿主，
不将其描述为 PostgreSQL 进程崩溃。正式 HTTP 验证另用 API 默认上下文、真实 loopback Uvicorn、
cookie/CSRF 和内存 checkpoint。PostgreSQL 硬退出恢复由独立工程测试覆盖。
RAG 不在本轮真实调用范围；HTTP 夹具配置演示政策解析器以避免额外嵌入请求，没有冒充真实 RAG 质量。

## 真实失败暴露并修复的问题

1. **当前工具结果被误标为历史快照。** 首次允许重连的批次完成 3/4 流程，执行 27/34 阶段、通过 26；
   第四条第 2 阶段，模型把 `refresh_required` 当成报价必须刷新，已确认且可提交时仍额外索取确认。
   在应用的上下文投影中区分最近用户轮之前/之后的工具观测，历史标记明确为重新只读查询，保留实际
   pending_issues 和 submission_check。修复后全量重跑 4/4，原失败仍在[首批报告](20261104-reconnect-order-v2.json)。
2. **原文存在不代表它被用户指定为备注。** 创建初批 6/7，模型把任务类型“采购清单”作为未指定备注，
   引用虽真实但字段角色不成立。增加明确备注字段及完整原文范围校验，拒绝无标记/截断备注，
   并明确缺失备注必须为 null。[原失败](20261104-reconnect-semantic-create-attempt-1/report.json)保留。
3. **动作来源对象发生错误嵌套。** 首次修订提示后，模型返回 `action.evidence` 而非直接来源对象，
   类型校验拦截；同批后续又发生超时，没有随机重抽业务失败。
   提示词明确 action 的直接结构，保留严格 schema、原文和偏移校验，最终独立创建批次 7/7。
   [该失败批次](20261104-reconnect-semantic-create-corrected-attempt-1/report.json)保留。
4. **验收客户端不能等待审批流 EOF。** 初次 HTTP 协议探针用 ASGITransport，会缓存尚在等待的 SSE。
   保留[中断记录](20261104-reconnect-live-api.json)及其两笔已计量请求，确认没有订单或扣库存；
   改为实际 loopback HTTP，在完整 pending/done 事件处关闭响应，补断连回归，再完成正式 API 验证。
   这是验收读取器问题，未改生产审批规则。

修复影响上下文投影、ERP 草稿指引、语义候选字段核验/提示版本及验收工具。订单用例、oracle、
业务事务和权限条件未改。创建协议版本为 3 / 提示 6，修改协议版本为 2 / 提示 3。

订单完整批次源码冻结后，后续只修改三份独立语义候选文件；核心图、上下文与订单事务源码保持一致。
最终语义与默认 API 验证使用新源码，差异清单和身份均在[审计 JSON](20261104-reconnect-final-audit.json)，
没有声称所有批次都是同一个全仓源码哈希。

## 重连和费用

- SDK 隐式重试为 0，验收层只对连接、超时和指定临时 HTTP 错误有限重连。
  一个模型响应完整收齐后才交给运行时，失败半流的文字与工具事件不会执行。
  第一批共 5 个失败物理请求，4 个逻辑请求重连成功；它仍因真实业务问题判失败。
- 修复后的订单批次配置最多 4 次请求、间隔 2/4/8 秒；该通过批次实际没有失败或重连。
  修改最终活动先超时一次，随后在新隔离目录完整重跑通过，旧超时和费用未知仍保留。
- 本轮新增已知估价 **CNY 0.114055 + 7 笔未知**。连同上一轮已知 CNY 0.009714 和 1 笔未知，
  合计已知 **CNY 0.123769 + 8 笔未知**；不能把未知金额写成零，也不将其冒充供应商账单总额。
- 每笔未知 CNY 0.005 仅作规划占用，合并已知为 CNY **0.163769**，在本轮 CNY 0.25 规划内。
  预留不是实际费用或供应商硬上限，原币种费率沿用代码中已确认的端点估价配置。

HTTP 异步生成器清理告警仍在部分运行中出现。没有压制告警，也没有把连接异常单一归因为供应商。
本轮业务状态核对通过；长期运行、客户端清理根因和供应商稳定性仍需持续观察。

## 工程回归与交付

| 检查 | 结果 |
|---|---|
| 全仓 Python，真实 PG17 独立实例 | **1796 passed / 0 skipped / 47 live eval deselected**；含备注作用域和完整性校验 |
| 新增 HTTP/SSE 读取器及语义专项 | **19 passed**；包含等待审批时关闭网络流，不等待 EOF |
| 中间评测/兼容性回归 | 评测包 432、MCP 与语义来源 482 通过；各自对应改动阶段，不相加为全仓数量 |
| Ruff / 原 oracle 源码差异 | 通过；用例与 oracle 没有修改 |
| 最终源码容器构建与浏览器 | Linux 镜像构建、健康检查通过；320/1440px 登录、只读角色、重建后原审批恢复 **3 passed**，无付费请求 |

全仓回归后仅补语义输出结构说明/提示版本及网络验收读取器，均有专项和实际模型结果。
JUnit、日志见 [全仓](20261104-reconnect-python.xml)、[专项](20261104-reconnect-protocol.xml)，
[结构化自审](../review/20261104-real-model-reconnect.md)。前端未改动。
[最终容器浏览器日志](20261104-reconnect-deployment.txt)独立保存；测试容器和合成数据卷在交付前清理。

在配置好现有 `.env` 后，可用新独立路径复验（真实调用可能收费）：

```sh
uv run --all-packages --frozen python -m evals.order_reconnect --budget 0.12 --output reports/acceptance/my-order-run
uv run --all-packages --frozen python scripts/semantic_reconnect.py --output reports/acceptance/my-create-run
uv run --all-packages --frozen python scripts/semantic_reconnect.py --revision --output reports/acceptance/my-revision-run
uv run --all-packages --frozen python scripts/live_api_acceptance.py --budget 0.02 --output reports/acceptance/my-api-run
```

每个路径只能使用一次。常规无密钥运行、容器账号和部署方式仍见[部署指南](../../docs/deployment.md)。
本轮源码包 `artifacts/erpilot-real-validation-20261104.zip`，带已有字幕视频的投递包
`artifacts/erpilot-delivery-real-validation-20261104.zip`，校验清单
`artifacts/erpilot-real-validation-20261104-validation.json`。不覆盖任何旧包，视频保留脚本模型口径。

成熟度自评（10 分）：**功能 9、工程 9、产品体验 9、求职说服力 9**。
默认核心闭环已有真实模型、真实 API、权限和恢复证据。语义 13 条属于工程专项，120 条人工标签仍待审，
默认保持 off；不自动宣称语义发布门槛、通用准确率或生产 SLA 已通过。
