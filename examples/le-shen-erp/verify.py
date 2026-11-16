"""Send an authenticated Erpilot chat request to the real local ERP."""

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path
from secrets import token_urlsafe
from tempfile import TemporaryDirectory

import httpx2
from agent_core.dotenv import load_dotenv
from agent_core.testing import USAGE, chunk, make_client, sse_response, tool_call_chunks
from erpilot_api.auth_store import AuthStore
from erpilot_api.connectors import HttpConnectorConfig
from erpilot_api.main import create_app
from erpilot_api.modules import BusinessSessionContext, load_module
from langgraph.checkpoint.memory import InMemorySaver
from le_shen_erp import DEFAULT_HOST_USER, DEFAULT_URL, Query, query_categories


def scripted_model(request):
    messages = json.loads(request.content)["messages"]
    if messages[-1]["role"] == "tool":
        result = json.loads(messages[-1]["content"])
        if result["status"] == "ok":
            count = len(result["categories"])
            items = "、".join(row["categoryName"] for row in result["categories"])
            answer = f"乐深 ERP 共返回 {result['total']} 个商品类别。当前显示 {count} 项：{items}"
        else:
            answer = "查询未成功，请确认测试账号权限和 ERP 连接。"
        return sse_response([chunk(delta={"content": answer}), chunk(usage=USAGE)])
    return sse_response(tool_call_chunks(
        "leshen-test", "list_leshen_categories", '{"limit":5}',
    ))


async def run_chat(app, username, password):
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="http://erpilot-test",
    ) as client:
        login = await client.post("/api/auth/login", json={
            "username": username, "password": password,
        })
        login.raise_for_status()
        csrf = login.json()["csrf_token"]
        response = await client.post(
            "/api/chat/stream", json={"message": "查询前五个商品类别"},
            headers={"X-CSRF-Token": csrf},
        )
        response.raise_for_status()
    event_name = None
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event_name = line[7:]
        elif event_name == "tool_finished" and line.startswith("data: "):
            event = json.loads(line[6:])
            if event["name"] == "list_leshen_categories" and event["ok"]:
                return json.loads(event["content"])
    raise RuntimeError("Erpilot did not produce a successful category tool event")


async def main():
    load_dotenv(Path(__file__).with_name(".env.local"))
    module = await load_module("le_shen_erp:create_module")
    config = HttpConnectorConfig.from_env("LESHEN_ERP", default_url=DEFAULT_URL)
    for token in (None, "leshen-invalid-" + token_urlsafe(12)):
        denied = await query_categories(replace(config, service_token=token), Query())
        if denied != {"status": "erp_access_denied", "erp_code": 401}:
            raise RuntimeError("Unexpected ERP response to missing or invalid credential")
    print("ERP: missing and invalid Authorization both rejected (401)")

    bound_user = os.getenv("LESHEN_ERP_HOST_USER", DEFAULT_HOST_USER).strip().lower()
    other_user = "leshen-unmapped-" + token_urlsafe(8).lower()
    password = token_urlsafe(24)
    with TemporaryDirectory(prefix="erpilot-leshen-") as directory:
        root = Path(directory)
        auth = AuthStore(root / "runs.db")
        user = auth.add_user(bound_user, password)
        unmapped = auth.add_user(other_user, password)
        await verify_tools(module, user, unmapped)
        model = make_client(scripted_model)
        app = create_app(
            business_module=module, auth_store=auth, run_store_path=auth.path,
            trace_dir=root / "trace", checkpointer=InMemorySaver(),
            client_factory=lambda: model, scripted_demo=True,
        )
        try:
            allowed = await run_chat(app, bound_user, password)
            if allowed["status"] != "ok":
                raise RuntimeError(f"ERP category read did not succeed: {allowed['status']}")
            print(f"Erpilot: category read succeeded, total={allowed['total']}, "
                  f"returned={len(allowed['categories'])}")
            denied = await run_chat(app, other_user, password)
            if denied != {"status": "host_access_denied"}:
                raise RuntimeError("Unmapped Erpilot account was not denied")
            print("Erpilot: unmapped authenticated account denied")
        finally:
            await model.aclose()


async def verify_tools(module, user, unmapped):
    missing = "leshen-no-match-" + token_urlsafe(12)
    cases = {
        "list_leshen_categories": ("categories", "name"),
        "list_leshen_products": ("products", "name"),
        "list_leshen_warehouses": ("warehouses", "name"),
        "list_leshen_stock": ("stock", "product_name"),
    }
    context = BusinessSessionContext("verify", user.user_id, user.username, user.role)
    tools = module.tools_for(context)
    if {tool.name for tool in tools} != cases.keys():
        raise RuntimeError("Unexpected Le Shen tool registry")
    for tool in tools:
        result_key, search_field = cases[tool.name]
        first = await tool.handler(tool.params_model(limit=1))
        second = await tool.handler(tool.params_model(page=2, limit=1))
        filtered = await tool.handler(tool.params_model(**{search_field: missing}))
        if first["status"] != "ok" or len(first[result_key]) != min(first["total"], 1):
            raise RuntimeError(f"Unexpected first page for {tool.name}")
        if (second["status"] != "ok" or second["page"] != 2
                or len(second[result_key]) != min(max(second["total"] - 1, 0), 1)
                or (second[result_key] and second[result_key] == first[result_key])):
            raise RuntimeError(f"Unexpected second page for {tool.name}")
        if filtered["status"] != "ok" or filtered["total"] != 0 or filtered[result_key]:
            raise RuntimeError(f"Unexpected filtered results for {tool.name}")
        print(f"{tool.name}: pagination and filtering passed, total={first['total']}")

    context = BusinessSessionContext(
        "verify", unmapped.user_id, unmapped.username, unmapped.role,
    )
    for tool in module.tools_for(context):
        if await tool.handler(tool.params_model()) != {"status": "host_access_denied"}:
            raise RuntimeError(f"{tool.name} did not deny the unmapped account")


if __name__ == "__main__":
    asyncio.run(main())
