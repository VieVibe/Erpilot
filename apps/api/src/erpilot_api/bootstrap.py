"""One real-model startup policy shared by local CLI and authenticated deployment."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from urllib.parse import urlsplit

from agent_core.approval import StreamApprovalGate
from agent_core.billing import EndpointRate, endpoint_rate
from agent_core.llm import LLMClient, LLMConfig
from erp_store.db import DEFAULT_DB
from mcp_erp.intent_semantic import SemanticSettings
from mcp_erp.session_tools import SessionErpTools

from erpilot_api.main import create_app


def semantic_settings(config, mode):
    enabled = os.getenv("ERPILOT_SEMANTIC", "1").lower()
    if enabled not in {"1", "true", "yes", "0", "false", "no"}:
        raise ValueError("ERPILOT_SEMANTIC must be 1/0, true/false or yes/no")
    if mode != "enforce" or enabled in {"0", "false", "no"}:
        return None, "已显式关闭智能解析"
    try:
        rate = endpoint_rate(config.model, config.base_url)
    except ValueError:
        incoming = os.getenv("ERPILOT_SEMANTIC_INPUT_PER_M")
        outgoing = os.getenv("ERPILOT_SEMANTIC_OUTPUT_PER_M")
        if incoming is None or outgoing is None:
            return None, "请配置模型价目后启用智能解析；仍可使用原对话和草稿表单"
        rate = EndpointRate(
            config.model,
            urlsplit(config.base_url).hostname or "",
            os.getenv("ERPILOT_SEMANTIC_CURRENCY", "CNY"),
            Decimal(incoming),
            Decimal(outgoing),
            price_source="operator-configuration",
        )
    settings = SemanticSettings(
        rate=rate,
        budget_id=os.getenv("ERPILOT_SEMANTIC_BUDGET_ID", "default-semantic-v1"),
        limit=Decimal(os.getenv("ERPILOT_SEMANTIC_BUDGET", "1.00")),
        max_requests=int(os.getenv("ERPILOT_SEMANTIC_MAX_REQUESTS", "200")),
        timeout_seconds=float(os.getenv("ERPILOT_SEMANTIC_TIMEOUT", "60")),
        include_business=False,
    )
    return settings, None


async def build_live_app(
    *,
    config=None,
    database=None,
    run_store_path=None,
    trace_dir=None,
    auth_enabled=None,
    client_factory=None,
    checkpointer=None,
    policy_resolver=None,
):
    config = config or LLMConfig.from_env()
    mode = os.getenv("ERPILOT_INTENT_MODE", "enforce")
    settings, reason = semantic_settings(config, mode)
    provider = await SessionErpTools.create(
        database or DEFAULT_DB,
        writes=os.getenv("ERPILOT_WRITES", "0").lower() in {"1", "true", "yes"},
        gate=StreamApprovalGate(),
        intent_mode=mode,
        policy_resolver=policy_resolver,
    )
    app = create_app(
        client_factory=client_factory or (lambda: LLMClient(config)),
        model=config.model,
        session_erp_tools=provider,
        intent_semantic_settings=settings,
        run_store_path=run_store_path,
        trace_dir=trace_dir,
        auth_enabled=auth_enabled,
        checkpointer=checkpointer,
    )
    app.state.semantic_runtime = {
        "intent_mode": mode,
        "intent_semantic_enabled": settings is not None,
        "intent_semantic_disabled_reason": reason,
    }
    return app


def default_app():
    # Uvicorn can load main:app from inside its running event loop. Discovery
    # creates no long-lived async resources; the saver opens on the serving loop.
    def build():
        return asyncio.run(build_live_app())

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return build()
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="erpilot-startup") as executor:
        return executor.submit(build).result()
