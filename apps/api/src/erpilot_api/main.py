"""Erpilot API：LangGraph 会话、checkpoint 和 SSE 流式审批。

路由：
- GET  /healthz           存活探针
- POST /api/chat/stream   SSE 流式对话
- GET  /api/sessions/{id}/state 会话快照
- POST /api/chat/approve/stream 审批后续段
- POST /api/sessions/{id}/resume/stream 从中断节点继续

启动：
    uv run --package erpilot-api python -m erpilot_api
联调前端：cd apps/web && npm install && npm run dev（vite 把 /api 代理到本服务）
"""

import asyncio
import os
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_core.approval import ApprovalDecision, StreamApprovalGate
from agent_core.demo_tools import DEMO_TOOLS
from agent_core.events import LoopEnd
from agent_core.llm import DEFAULT_MODEL, LLMClient, LLMConfig
from agent_core.prices import cost_of
from agent_core.tools import Tool
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from starlette.background import BackgroundTask

from erpilot_api.auth import AuthMiddleware, register_auth
from erpilot_api.auth_store import AuthStore
from erpilot_api.checkpoint import open_checkpointer
from erpilot_api.draft_routes import ConfirmDraftRequest, register_draft_routes
from erpilot_api.events import encode_event, sse_frame
from erpilot_api.history_routes import register_history_routes
from erpilot_api.intent_routes import register_intent_routes
from erpilot_api.operation_routes import register_operation_routes
from erpilot_api.run_store import RunStore
from erpilot_api.service import ChatService, SessionError

DEFAULT_TRACE_DIR = Path("traces")

_WRITES_ENV = ("1", "true", "yes")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=32000, pattern=r"\S",
                         description="用户本轮输入，最多 32000 字符且不能全为空白")
    session_id: str | None = Field(default=None, min_length=1, max_length=128,
                                   description="缺省则新建会话")


class ApproveRequest(BaseModel):
    """审批决策回填（M4 第 2 周）：pending_id 来自 approval_pending 事件。"""

    pending_id: str = Field(min_length=1, description="待审批请求 id")
    approved: bool = Field(description="true=批准执行，false=拒绝")
    reason: str = Field(default="", max_length=2000,
                        description="拒绝原因（转述给模型与用户）")
    session_id: str | None = Field(default=None, min_length=1, max_length=128)


class ResumeRequest(ApproveRequest):
    session_id: str = Field(min_length=1, max_length=128)


def _resolve_tools(
    tools: Sequence[Tool] | None,
) -> tuple[Sequence[Tool], StreamApprovalGate | None]:
    """工具来源：显式参数 > ERPILOT_TOOLS=mcp（经 MCP 桥查真数据）> demo 假数据。

    mcp 解析在 create_app 时同步完成（此时无运行中的事件循环），失败即抛——
    显式选了真数据而库不存在，带着指引报错好过静默降级。ERPILOT_WRITES=1 时
    写工具进入工具面并挂挂起式审批门（决策经 POST /api/chat/approve 回填）。
    """
    if tools is not None:
        return tools, None
    if os.environ.get("ERPILOT_TOOLS", "").lower() == "mcp":
        from mcp_erp import build_agent_tools

        if os.environ.get("ERPILOT_WRITES", "").lower() in _WRITES_ENV:
            gate = StreamApprovalGate()
            return build_agent_tools(writes=True, approval_gate=gate), gate
        return build_agent_tools(), None
    return list(DEMO_TOOLS), None


def create_app(
    *,
    client_factory: Callable[[], LLMClient] | None = None,
    model: str = DEFAULT_MODEL,
    trace_dir: Path | None = None,
    tools: Sequence[Tool] | None = None,
    approval_gate: StreamApprovalGate | None = None,
    run_store_path: Path | None = None,
    checkpointer=None,
    mutation_reconciler=None,
    approval_ttl_seconds: int | None = None,
    clock=None,
    session_erp_tools=None,
    intent_semantic_settings=None,
    scripted_demo: bool = False,
    persistent_demo: bool = False,
    auth_enabled: bool | None = None,
    auth_store: AuthStore | None = None,
) -> FastAPI:
    """应用工厂：测试注入 mock 的 LLMClient 工厂、临时 trace 目录与工具集。"""
    if scripted_demo and client_factory is None:
        raise ValueError("scripted_demo requires an explicit offline client_factory")
    if (
        session_erp_tools is None
        and tools is None
        and os.environ.get("ERPILOT_TOOLS", "").lower() == "mcp"
    ):
        from mcp_erp.session_tools import SessionErpTools

        writes = os.environ.get("ERPILOT_WRITES", "").lower() in _WRITES_ENV
        session_erp_tools = asyncio.run(
            SessionErpTools.create(
                writes=writes, gate=approval_gate or (StreamApprovalGate() if writes else None)
            )
        )
    if session_erp_tools is not None:
        resolved_tools, env_gate = session_erp_tools.tools, session_erp_tools.gate
    else:
        resolved_tools, env_gate = _resolve_tools(tools)
    if getattr(getattr(session_erp_tools, "intent_analysis", None), "semantic", None) is not None:
        raise ValueError("semantic provider already belongs to an API host; "
                         "create a fresh provider")
    service = ChatService(
        client_factory=client_factory or (lambda: LLMClient(LLMConfig.from_env())),
        model=model,
        trace_dir=trace_dir or DEFAULT_TRACE_DIR,
        tools=resolved_tools,
        approval_gate=approval_gate or env_gate,
        checkpointer=checkpointer,
        run_store=RunStore(
            run_store_path or (trace_dir / "runs.db" if trace_dir else Path(
                os.getenv("ERPILOT_RUN_STORE_PATH", "data/runs.db")))
        ),
        mutation_reconciler=mutation_reconciler,
        approval_ttl_seconds=(
            approval_ttl_seconds
            if approval_ttl_seconds is not None
            else int(os.environ.get("ERPILOT_APPROVAL_TTL_SECONDS", "1800"))
        ),
        clock=clock,
        session_tools_factory=session_erp_tools.for_session if session_erp_tools else None,
        session_reconciler_factory=session_erp_tools.reconciler if session_erp_tools else None,
        drafts=session_erp_tools.drafts if session_erp_tools else None,
        operations=getattr(session_erp_tools, "operations", None),
        intent_store=getattr(getattr(session_erp_tools, "intent_analysis", None), "store", None),
        intent_recovery=getattr(
            getattr(session_erp_tools, "intent_analysis", None), "recovery", None),
        intent_diagnostics=getattr(
            getattr(session_erp_tools, "intent_analysis", None), "diagnostics", None,
        ),
        trace_sinks=[] if scripted_demo else None,
    )
    if intent_semantic_settings is not None:
        from mcp_erp.intent_semantic import SemanticCoordinator

        analyzer = getattr(session_erp_tools, "intent_analysis", None)
        if analyzer is None or analyzer.mode != "enforce":
            raise ValueError("semantic proposals require a session ERP provider in enforce mode")
        if model.lower() != intent_semantic_settings.rate.model.lower():
            raise ValueError("API model must match the semantic budget model")
        coordinator = SemanticCoordinator(
            analyzer, intent_semantic_settings, service._client_factory)
        analyzer.semantic = service._intent_semantic = coordinator
        # Let the candidate's own deadline produce its safe fallback before the
        # graph cancels the resolver. Other tools retain the normal deadline.
        service._loop_config = replace(service._loop_config, tool_timeouts={
            **service._loop_config.tool_timeouts,
            "resolve_order_items": max(service._loop_config.tool_timeout,
                                       intent_semantic_settings.timeout_seconds + 5),
        })

    @asynccontextmanager
    async def lifespan(app):
        try:
            if checkpointer is not None:
                yield
            else:
                async with open_checkpointer(
                    os.environ.get("ERPILOT_CHECKPOINT_DATABASE_URL", "")
                ) as saver:
                    service._checkpointer = saver
                    try:
                        yield
                    finally:
                        service._checkpointer = None
        finally:
            if service._intent_semantic is not None:
                await service._intent_semantic.aclose()
            if isinstance(service._client, LLMClient):
                await service._client.aclose()

    app = FastAPI(title="Erpilot API", version="0.1.0", lifespan=lifespan)
    app.state.service = service
    app.state.run_store = service._run_store
    enabled = (os.getenv("ERPILOT_AUTH", "0") == "1" if auth_enabled is None else auth_enabled)
    auth_store = auth_store or (AuthStore(service._run_store.path) if enabled else None)
    if auth_store is not None:
        if auth_store.path.resolve() != service._run_store.path.resolve():
            raise ValueError("auth and RunStore must share the same SQLite file")
        original_factory = service._session_tools_factory or (lambda session_id: resolved_tools)
        service._session_tools_factory = lambda sid: auth_store.session_tools(
            sid, original_factory(sid))
        app.add_middleware(AuthMiddleware, store=auth_store, service=service)
    app.state.auth_store = auth_store
    register_auth(app, auth_store)

    @app.exception_handler(SessionError)
    async def session_error(request: Request, exc: SessionError):
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": str(exc), "hint": exc.hint}},
        )

    @app.get("/api/sessions/{session_id}/state")
    async def session_state(session_id: str, request: Request):
        try:
            return await service.session_state(session_id)
        except SessionError as exc:
            # A fresh local session may not exist yet; existing/owned sessions fail closed.
            if (auth_store is None and exc.code == "session_not_found"
                    and request.headers.get("X-Erpilot-Allow-Missing") == "1"):
                return Response(status_code=204, headers={"Cache-Control": "no-store"})
            raise

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    register_draft_routes(app, session_erp_tools, service)
    register_intent_routes(app, session_erp_tools, service)
    register_operation_routes(app, service._operations)
    register_history_routes(app, service._run_store, authenticated=auth_store is not None)

    @app.get("/api/runtime")
    def runtime_info(request: Request) -> dict[str, Any]:
        return {
            **getattr(app.state, "semantic_runtime", {}),
            **({"intent_semantic_available": not service._intent_semantic.budget.exhausted}
               if service._intent_semantic is not None else {}),
            "auth_enabled": auth_store is not None,
            **({"scripted_demo": True, "demo_persistent": persistent_demo}
               if scripted_demo else {}),
            "data_source": "mcp"
            if session_erp_tools is not None
            else "custom"
            if tools is not None
            else ("mcp" if os.environ.get("ERPILOT_TOOLS", "").lower() == "mcp" else "demo"),
            "writes_enabled": any(tool.risk is not None for tool in resolved_tools) and (
                auth_store is None or request.state.identity.role == "approver"),
            "reasoning_enabled": os.environ.get("ERPILOT_THINKING", "").lower() in _WRITES_ENV,
            "drafts_enabled": session_erp_tools is not None,
            **({"intent_preview_enabled": True} if service._intent_store is not None else {}),
            **({"intent_recovery_enabled": True}
               if service._intent_store is None and service._intent_recovery is not None
               and service._intent_recovery.available else {}),
            **({"intent_semantic_revision_enabled": True}
               if intent_semantic_settings is not None else {}),
            "business_approvals_enabled": (
                service._operations is not None
                and any(tool.risk is not None for tool in resolved_tools)
            ),
        }

    @app.post("/api/chat/stream")
    async def chat_stream(req: ChatRequest, request: Request) -> EventSourceResponse:
        session_id = req.session_id or (auth_store.new_session(request.state.identity)
                                       if auth_store is not None else uuid4().hex)
        return event_response(session_id, service.stream_run(session_id, req.message))

    @app.post("/api/sessions/{session_id}/order-drafts/{draft_id}/confirm/stream")
    async def confirm_draft(session_id: str, draft_id: str, req: ConfirmDraftRequest):
        draft, release = await service.reserve_draft_submission(
            session_id, draft_id, req.expected_revision,
        )
        return event_response(
            session_id, service.stream_confirmed_draft(session_id, draft), release,
            execution_mode="confirmed_action",
        )

    @app.post("/api/chat/approve/stream")
    async def resume_approval(req: ResumeRequest) -> EventSourceResponse:
        release = await service.reserve_resume(req.session_id, req.pending_id)
        return event_response(
            req.session_id,
            service.stream_resume(
                req.session_id,
                req.pending_id,
                req.approved,
                req.reason,
                reserved=True,
            ),
            release,
        )

    @app.post("/api/sessions/{session_id}/resume/stream")
    async def continue_interrupted_session(session_id: str) -> EventSourceResponse:
        return event_response(session_id, service.stream_retry(session_id))

    @app.post("/api/sessions/{session_id}/cancel")
    async def cancel_session(session_id: str) -> dict[str, Any]:
        return await service.cancel_session(session_id)

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, after_seq: int = Query(default=0, ge=0)):
        store = service._run_store
        run = store.get_run(run_id) if store else None
        if run is None:
            raise SessionError(404, "run_not_found", "运行记录不存在")

        async def replay_and_follow():
            cursor = after_seq
            while True:
                for event in store.read_events(run_id, after_seq=cursor):
                    cursor = event["seq"]
                    frame = sse_frame(event["type"], {**event["payload"], "run_id": run_id})
                    frame["id"] = str(cursor)
                    yield frame
                current = store.get_run(run_id)
                if current is None or current["status"] in {"completed", "failed", "cancelled"}:
                    return
                await asyncio.sleep(0.25)

        return EventSourceResponse(replay_and_follow())

    def event_response(session_id, stream, release=None, *, execution_mode=None):
        heartbeat_interval = float(os.environ.get("ERPILOT_HEARTBEAT_SECONDS", "10") or 10)

        async def generate() -> AsyncIterator[dict[str, str]]:
            start = {"session_id": session_id, "model": service.model}
            if execution_mode:
                start["execution_mode"] = execution_mode
            yield sse_frame("start", start)
            t0 = time.perf_counter()
            queue: asyncio.Queue[Any] = asyncio.Queue()
            stream_end = object()

            async def produce() -> None:
                # 生产者把流推进到收口，心跳等待不取消底层迭代（设计 5.4）——
                # 反复取消异步迭代会连带取消模型请求或工具调用
                try:
                    async for event in stream:
                        await queue.put(event)
                except asyncio.CancelledError:
                    raise
                except BaseException as exc:  # noqa: BLE001 —— 异常转交消费端统一编码
                    await queue.put(exc)
                finally:
                    await queue.put(stream_end)

            producer = asyncio.create_task(produce())
            try:
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=heartbeat_interval)
                    except TimeoutError:
                        # 心跳只代表连接存活，不代表模型或工具取得进展；不落展示日志
                        yield sse_frame("heartbeat", {"at": datetime.now(UTC).isoformat()})
                        continue
                    if item is stream_end:
                        break
                    if isinstance(item, BaseException):
                        raise item
                    encoded = encode_event(item)
                    if encoded is not None:
                        yield sse_frame(*encoded)
                    if isinstance(item, LoopEnd):
                        yield sse_frame(
                            "done",
                            {
                                "steps": item.steps,
                                "completed": item.completed,
                                "usage": asdict(item.usage) if item.usage else None,
                                "cost": cost_of(service.model, item.usage),
                                "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
                                "trace": str(service.last_trace) if service.last_trace else None,
                            },
                        )
            except SessionError as exc:
                yield sse_frame("error", {"code": exc.code, "message": str(exc)})
            except Exception as exc:
                yield sse_frame("error", {"code": "execution_error", "message": str(exc)})
            finally:
                if not producer.done():
                    producer.cancel()
                await asyncio.gather(producer, return_exceptions=True)
                await stream.aclose()
                if release:
                    release()

        return EventSourceResponse(
            generate(), background=BackgroundTask(release) if release else None
        )

    @app.post("/api/chat/approve")
    async def approve(req: ApproveRequest) -> dict[str, object]:
        """审批决策回填：挂起中的 SSE 流在决策落定后继续推进。"""
        ok = service.respond_approval(
            req.pending_id, ApprovalDecision(approved=req.approved, reason=req.reason)
        )
        return {"ok": ok}  # False = 未知或已决的 pending_id

    return app


def __getattr__(name):
    # 保留 uvicorn erpilot_api.main:app；仅导入工厂时不创建默认 RunStore 或发现真实工具。
    if name == "app":
        from erpilot_api.bootstrap import default_app

        app = default_app()
        globals()[name] = app
        return app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
