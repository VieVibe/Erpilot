"""HTTP expense adapter. The example backend owns documents and idempotency."""

import os

from agent_core.recovery import MutationLookup
from agent_core.tools import ApprovalPresentation, Tool
from pydantic import BaseModel, ConfigDict, Field

from erpilot_api.connectors import HttpConnector, HttpConnectorConfig
from erpilot_api.modules import BusinessModule, BusinessSessionContext


class ExpenseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: str = Field(min_length=1, max_length=200, pattern=r"\S")
    amount_cents: int = Field(strict=True, gt=0, le=10000000)
    client_token: str | None = Field(default=None, min_length=1, max_length=128)


class ExpenseQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_token: str = Field(min_length=1, max_length=128)


class ExpenseAdapter:
    def __init__(self, connector: HttpConnector, context: BusinessSessionContext):
        self.connector, self.context = connector, context

    async def request(self, method, path, **kwargs):
        return await self.connector.request(method, path, context=self.context, **kwargs)

    async def health(self):
        response = await self.request("GET", "healthz")
        response.raise_for_status()

    async def submit(self, args):
        if not args.client_token:
            raise ValueError("submission requires a host-generated client token")
        response = await self.request("POST", "expenses", json={
            **args.model_dump(),
            "session_id": self.context.session_id,
            "actor_id": self.context.user_id or "local",
            "actor_role": self.context.role,
        })
        response.raise_for_status()
        return response.json()

    async def query(self, args):
        response = await self.request("GET", "expenses", params={
            "session_id": self.context.session_id,
            "actor_id": self.context.user_id or "local",
            "client_token": args.client_token,
        })
        if response.status_code == 404:
            return {"status": "absent"}
        response.raise_for_status()
        return response.json()

    async def lookup(self, tool_name, arguments, client_token):
        if tool_name != "submit_expense":
            raise ValueError("unsupported expense operation")
        result = await self.query(ExpenseQuery(client_token=client_token))
        if result.get("status") == "absent":
            return MutationLookup("absent")
        if any(result[key] != arguments[key] for key in ("purpose", "amount_cents")):
            return MutationLookup("conflict")
        return MutationLookup("found", result)

    def tools(self, writes):
        tools = [Tool("get_expense", "按提交请求号查询报销单", ExpenseQuery, self.query)]
        if writes:
            tools.append(Tool(
                "submit_expense", "提交报销申请，金额单位为人民币分，执行前需要人工确认",
                ExpenseInput, self.submit, risk="single_confirm", retry_safe=True,
                approval_presentation=ApprovalPresentation(
                    title="提交报销申请",
                    summary="向报销系统提交申请，后续审批由该系统负责。",
                    fields=(("purpose", "用途"), ("amount_cents", "金额（分）")),
                ),
            ))
        return tools


def create_module(*, config=None, base_url=None, service_token=None,
                  writes=None, transport=None):
    if config is not None and (base_url is not None or service_token is not None):
        raise ValueError("pass either connector config or endpoint overrides, not both")
    if config is None:
        if base_url is None and service_token is None:
            config = HttpConnectorConfig.from_env(
                "ERPILOT_EXPENSE", default_url="http://127.0.0.1:8011",
            )
        else:
            config = HttpConnectorConfig(
                base_url=base_url or os.getenv(
                    "ERPILOT_EXPENSE_URL", "http://127.0.0.1:8011"
                ),
                service_token=service_token,
            )
    writes = (os.getenv("ERPILOT_WRITES", "0").lower() in {"1", "true", "yes"}
              if writes is None else writes)
    connector = HttpConnector(config, transport=transport, trust_env=True)

    def context_adapter(context):
        return ExpenseAdapter(connector, context)

    def adapter(session_id):
        return context_adapter(BusinessSessionContext(session_id))

    return BusinessModule(
        name="expense-example", title="报销接入示例",
        instructions=("帮助用户查询或提交报销申请。提交前收集用途和人民币金额，"
                      "将金额准确转换为整数分。提交成功表示 ERP 已收件，审批状态以返回值为准。"
                      "返回申请编号和请求号，供用户后续查询。"),
        tools=adapter("discovery").tools(writes),
        for_session=lambda session_id: adapter(session_id).tools(writes),
        reconciler=adapter,
        for_context=lambda context: context_adapter(context).tools(writes),
        reconciler_for_context=context_adapter,
        connector_summary=config.public_summary(),
        health_check=context_adapter(BusinessSessionContext("health-check")).health,
        lifespan=connector.lifespan,
        suggestions=("提交一笔交通费报销，用途为客户拜访，金额为 38 元",),
    )
