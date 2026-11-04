# ADR-0001：主技术栈选型

- 状态：已接受
- 日期：2026-09-29

## 实施核对（2026-11-04）

下文保留最初选型，当前实际实现以[架构说明](../architecture.md)为准：

- 业务和 RunStore 使用 SQLite；PostgreSQL 只用于 LangGraph checkpoint。
- RAG 使用可核对的 JSON 向量索引及真实嵌入接口，没有引入 pgvector。
- 模型通过 OpenAI 兼容 SDK 和供应商配置切换，没有引入 LiteLLM 服务。
- trace 默认本地 JSONL，Langfuse 可选；没有完整 OpenTelemetry 管道。
- 评测为 pytest / 自建 runner、确定性状态断言和质量门槛，没有引入 DeepEval。
- 工作台使用 React / TypeScript / Ant Design X。

这些取舍用于控制小型作品的复杂度；不把原规划中列出的组件写成已交付技术。

## 背景

本项目是转岗 agent 应用开发的求职作品集。选型目标不是个人最快上手，而是对齐 2026 年生产环境真实在用的 agent 技术栈（国内 JD 基本盘：Python + FastAPI + LangChain/LangGraph + RAG + MCP），多出的学习成本计入 M1–M2 并用 AI 编程工具对冲。

## 决策

- 主栈 Python 3.12+，承载全部 agent 核心逻辑；TypeScript 仅用于前端
- 后端 FastAPI + sse-starlette（SSE）+ Pydantic v2；工程链 uv workspace + ruff + pytest
- M1–2 手写 agent loop；M6 起引入 LangGraph（Postgres checkpointer + interrupt 实现 HITL）
- 工具层 FastMCP；数据库 PostgreSQL + pgvector；模型路由 LiteLLM
- 模型：GLM-5.3 Flash（智谱，OpenAI 兼容端点）起步；评测阶段横向对比 DeepSeek / Qwen
- 可观测 Langfuse 自托管 + OpenTelemetry GenAI 语义约定
- 评测 pytest 自建 runner + DeepEval（LLM-as-judge）
- 前端 React + TS（Vite）；UI 组件库 M6 在 assistant-ui / CopilotKit 中二选一

## 理由

- 目标岗位 JD 的基本盘在 Python 侧；agent 编排、评测、可观测的生态重心也在 Python
- LangGraph 的 checkpoint / interrupt 是 HITL 的工业级实现；先手写 loop 保证面试能答原理
- agent 前端生态（generative UI、AG-UI 协议）集中在 React 侧（CopilotKit / assistant-ui）

## 备选与放弃原因

- TypeScript 全栈（Vercel AI SDK / Mastra）：个人更熟，但与目标岗位主流栈错位
- Vue：同上，agent UI 生态明显弱于 React
- SQLite 起步：省事，但 LangGraph checkpointer 的生产路径是 Postgres，不值得二次迁移

## 后果

- M1 第一周需补 asyncio、类型标注、uv 工具链（已列入启动计划风险表）
- 评测对比阶段若其他模型表现更优，只需改 LiteLLM 配置与 env，不触碰业务代码
- CI 回归评测需在 GitHub Secrets 配置 API key，并设预算熔断
