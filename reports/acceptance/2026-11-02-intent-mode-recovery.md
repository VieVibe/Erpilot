# 意图分析：回退状态、旧 checkpoint 与 PostgreSQL 验证

日期：2026-11-02。承接创建/已有草稿预览。用户要求继续收尾，随后告知本机 Docker 已可用。
核心标签人工复核包已交付，用户明确选择“尚未复核，暂保留待审核状态”；因此不改变标签状态或默认启用配置。

## 变更与范围

回退到 off/shadow 时，把未消费的新意图请求从 pending/ready 原子改为 needs_recheck，版本递增。
原始消息、已接受字段与 TTL 不变；不迁移已保存草稿、不改审批参数、不处理订单。
回到 enforce 不自动重启旧候选或确认。完整新请求另起标识，旧确认不能消费新请求。

新增只读恢复视图。关闭/影子模式允许读取停用提示，can_confirm=false，POST 确认仍拒绝。
短编号、件数、预算补充或简短确认由应用只读结束，不初始化模型，不触发写工具或审批。
没有意图表的旧数据库不新增这些表，默认 off 原路径保持；其它宿主的统一整轮诊断没有借此标为完成。

旧审批在三种模式间重建宿主后保持 pending_id、完整 arguments 和 expires_at。
批准仅执行原动作，拒绝不写入；保存来源 draft-intent-2 不因回退失去可读及提交核验能力。
图内存序列化字节测试和真实 PostgreSQL 连接重建测试分别记录，不把内存复制冒充持久化库恢复。

## 验证

| 检查 | 结果/证据 |
|---|---|
| 本地模式矩阵 | 32 passed，[JUnit](20261102-intent-mode-local-final.xml) |
| 模式/规则/预览扩大专项 | 197 passed，[JUnit](20261102-intent-mode-focused.xml) |
| 真实 PostgreSQL 模式及原恢复专项 | 56 passed，153.25 秒，[JUnit](20261102-intent-mode-postgres.xml) |
| 原 120 条工程核心集 | 120/120，原标签保持 engineering_expectations_pending_human_review，[逐例结果](20261102-intent-mode-core.json) |
| 前端协议与渲染 | 106 passed，[日志](20261102-intent-mode-web-tests.txt) |
| 手机/桌面只读恢复 | 2/2，[日志](20261102-intent-mode-ui-tests.txt)，[320px](20261102-intent-mode-recovery-320.png)、[1440px](20261102-intent-mode-recovery-1440.png) |
| 前端构建 | 通过，既有大包提示保留，[日志](20261102-intent-mode-build.txt) |
| 首轮全仓（PG 新测试加入前收集） | 1713 passed / 21 skipped / 47 deselected，405.169 秒，[JUnit](20261102-intent-mode-full.xml)；新增 PG 测试文件不在其收集范围，不作为最终冻结结果 |
| 最终全仓（真实 PostgreSQL） | **1764 passed / 0 skipped / 47 deselected**，427.05 秒，[JUnit](20261102-intent-mode-full-postgres.xml) |
| 最终源码与证据冻结 | 全仓运行前后源码指纹一致，[清单](20261102-intent-mode-manifest.json) |

PostgreSQL 专项 56 项包括新增 29 项实库模式/恢复用例，以及原 checkpoint/进程退出测试。
旧审批为三模式 × 三模式 × 批准/拒绝，共 18 项；未消费请求回退与回到 enforce 为 8 项；
新来源已保存草稿回退提交为 3 项。每个用例独立临时 schema，连接池和宿主实际关闭后重建。
原有硬退出用例使用独立子进程，未把普通异常当成进程崩溃。

环境：官方 postgres:16-alpine，实查服务器 16.15，镜像摘要
sha256:0cefaabd882e42bcb2a2f3dc56d3ce4770560ba62fd7f197f1a2e008fd5731ab。
临时容器仅映射本机回环随机端口，数据存于 tmpfs；未使用用户已有数据库、命名卷或 .env。
测试连接只通过本轮子进程环境传入。测试结束后停止并移除该专用容器，保留证据与镜像缓存。
结束时临时测试 schema 为零，已核对容器身份后停止并确认删除，没有操作用户已有容器；
见[环境及清理记录](20261102-intent-mode-postgres-environment.json)。

先前两项旧测试要求 off/shadow 的 GET 预览一律 503；本批因设计要求“回退后可见但需重新核对”，
更新为验证 GET 只读 needs_recheck、确认 POST 仍拒绝、读取前后数据库不变。
这属于明确兼容修复，未修改旧 scorer、工程标签或业务保存/审批条件。
新实测 runner 初次离线修改夹具缺库存，被旧保存约束拒绝；只补齐隔离夹具库存，未放宽原约束。

## 真实模型新批次

用户既有真实验证授权下，本轮另设创建/修改两个独立小额计划，上限各 CNY 0.05；
旧账本未重置。新创建批次首笔在约 3.1 秒遇到 APIConnectionError，没有 usage，
立即停止依赖请求，**修改批次未启动**。
本轮新增一次 SDK 生成调用尝试、费用未知，不能当成免费或成功。
[完整报告与逐请求账本](20261102-intent-completion-create-live/report.json)保留。
此前 19 次尝试中 18 次完整用量，已知 CNY 0.005192 加一笔未知；本轮后共 20 次尝试、两笔未知。
不拼接多版本成功样例，不把脚本模型当供应商效果验收。

只读基础连通检查：端点及模型清单返回 200，配置模型仍在清单；
同一 SDK 的 models.list 也成功，但这不能证明推理流式接口稳定或解释那笔未知费用。
[HTTP 检查](20261102-intent-provider-readonly-health.json)、[SDK 检查](20261102-intent-provider-sdk-health.json)
均未发生成请求、未自动重试。

## 尚未达成的发布门槛

- [人工复核包](../review/20261102-intent-core/review.md)：120 条仍待实际人工核对；原文件哈希保持。
- 创建及修改的真实模型完整质量验收、同模型冻结对照仍未通过。
- 默认启用不执行；更广自由表达、对话内目标选择、非 API 诊断与长期性能仍是后续范围。

本批代码及数据库恢复验证可单独验收，不据此声称整个优化设计或上线门槛已完成。
