"""Installable adapter for Roundup's issue REST API."""

import json
import os
from hashlib import sha256
from urllib.parse import urlsplit

from erpilot_api.plugin import (
    ApprovalPresentation,
    BusinessModule,
    BusinessSessionContext,
    HttpConnector,
    HttpConnectorConfig,
    MutationLookup,
    ResultPresentation,
    Tool,
)
from pydantic import BaseModel, ConfigDict, Field


class IssueQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue_id: str = Field(pattern=r"^[1-9][0-9]{0,11}$")


class IssueCreation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    client_token: str | None = Field(default=None, min_length=1, max_length=128)


def issue_summary(issue_id, title):
    return {"issue_id": IssueQuery(issue_id=issue_id).issue_id,
            "title": IssueCreation(title=title).title}


class Roundup:
    def __init__(self, connector, accounts, context):
        self.connector, self.accounts, self.context = connector, accounts, context

    async def request(self, method, path, **kwargs):
        auth = self.accounts.get(self.context.user_id)
        if auth is None:
            raise ValueError("当前 Erpilot 账号未绑定 Roundup 操作者")
        url = urlsplit(self.connector.config.base_url)
        response = await self.connector.request(
            method, path, auth=tuple(auth),
            headers={"Origin": f"{url.scheme}://{url.netloc}", "X-Requested-With": "Erpilot"},
            **kwargs,
        )
        if response.status_code >= 300:
            # Upstream errors can contain request paths, credentials or private business data.
            raise ValueError(f"Roundup 请求失败（HTTP {response.status_code}）")
        return response.json()["data"]

    def request_key(self, token):
        material = json.dumps([self.context.user_id, self.context.session_id, token])
        return sha256(material.encode()).hexdigest()

    async def read(self, args):
        issue = await self.request(
            "GET", f"data/issue/{args.issue_id}", params={"@fields": "title"},
        )
        return issue_summary(issue["id"], issue["attributes"]["title"])

    async def create(self, args):
        if self.context.role != "approver" or not args.client_token:
            raise ValueError("创建工单需要已绑定的审批员和宿主请求号")
        existing = await self.lookup("create_issue", {"title": args.title}, args.client_token)
        if existing.status == "found":
            return existing.result
        if existing.status == "conflict":
            raise ValueError("工单请求号已绑定其他内容")
        issue = await self.request("POST", "data/issue", json={
            "title": args.title,
            "erpilot_request": self.request_key(args.client_token),
            "erpilot_title": args.title,
        })
        return issue_summary(issue["id"], args.title)

    async def lookup(self, tool_name, arguments, client_token):
        if tool_name != "create_issue" or self.context.role != "approver":
            raise ValueError("无法核对当前工单请求")
        key = self.request_key(client_token)
        data = await self.request("GET", "data/issue", params={
            "erpilot_request": key, "@fields": "erpilot_request,erpilot_title", "@page_size": "2",
        })
        issues = data["collection"]
        if data["@total_size"] != len(issues) or any(
            issue.get("erpilot_request") != key for issue in issues
        ):
            raise ValueError("Roundup 未返回完整的请求号核对结果")
        if not issues:
            return MutationLookup("absent")
        if len(issues) != 1:
            raise ValueError("Roundup 中存在重复请求记录")
        issue = issues[0]
        if issue["erpilot_title"] != arguments["title"]:
            return MutationLookup("conflict")
        return MutationLookup("found", issue_summary(issue["id"], issue["erpilot_title"]))

    def tools(self, writes):
        fields = (("issue_id", "工单编号"), ("title", "标题"))
        tools = [Tool("get_issue", "按编号查询 Roundup 工单", IssueQuery, self.read,
                      result_presentation=ResultPresentation("Roundup 工单", fields))]
        if writes:
            tools.append(Tool(
                "create_issue", "在 Roundup 创建工单，须等待人工批准",
                IssueCreation, self.create, risk="single_confirm",
                approval_presentation=ApprovalPresentation(
                    "创建 Roundup 工单", (("title", "标题"),), "批准后将写入工单系统。",
                ),
                result_presentation=ResultPresentation("已创建工单", fields),
            ))
        return tools


def create_module():
    config = HttpConnectorConfig.from_env("ERPILOT_ROUNDUP")
    url = urlsplit(config.base_url)
    if config.service_token is not None:
        raise ValueError("Roundup 使用各操作者的凭据，请删除服务令牌配置")
    if url.scheme != "https" and url.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("远端 Roundup 连接需要 HTTPS")
    accounts = json.loads(os.environ["ERPILOT_ROUNDUP_ACCOUNTS"])
    if not isinstance(accounts, dict) or not accounts or any(
        not isinstance(pair, list) or len(pair) != 2 or not all(
            isinstance(value, str) and value for value in pair
        ) for pair in accounts.values()
    ):
        raise ValueError("Roundup 账号映射需要按用户 ID 提供用户名和密码")
    connector = HttpConnector(config)
    writes = os.getenv("ERPILOT_WRITES", "0").lower() in {"1", "true", "yes"}

    def adapter(context):
        return Roundup(connector, accounts, context)

    return BusinessModule(
        name="roundup", title="Roundup 工单", version="0.1.0", recovery_version="0.1.0",
        instructions="查询工单时要求编号。创建工单时保留用户给出的标题并等待批准。",
        tools=adapter(BusinessSessionContext("discovery")).tools(writes),
        for_context=lambda context: adapter(context).tools(writes),
        reconciler_for_context=adapter, lifespan=connector.lifespan,
        connector_summary=config.public_summary(),
        suggestions=("查询工单 1", "创建一张标题为「补货报表加载失败」的工单"),
    )
