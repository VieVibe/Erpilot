import json
import sqlite3
from types import SimpleNamespace

import httpx2
import pytest
from agent_core.events import ApprovalPending
from agent_core.llm import LLMConfig
from agent_core.testing import USAGE, chunk, make_client, sse_response, tool_call_chunks
from agent_core.tools import Tool
from erpilot_api.auth_store import AuthStore
from erpilot_api.bootstrap import build_live_app
from erpilot_api.connectors import HttpConnectorConfig
from erpilot_api.examples.expense import ExpenseInput, create_module
from erpilot_api.examples.expense_backend import create_app as backend_app
from erpilot_api.main import create_app
from erpilot_api.modules import BusinessModule, BusinessSessionContext, load_module
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel


def expense_llm(request):
    messages = json.loads(request.content)["messages"]
    if messages[-1]["role"] == "tool":
        return sse_response([chunk(delta={"content": "处理结果已返回"}), chunk(usage=USAGE)])
    return sse_response(tool_call_chunks(
        "expense-write", "submit_expense", '{"purpose":"客户拜访","amount_cents":3800}',
    ))


@pytest.mark.parametrize("approved", [True, False])
async def test_module_approval_http_restart_and_session_isolation(tmp_path, approved):
    transport = httpx2.ASGITransport(backend_app(tmp_path / "expenses.db"))
    saver = InMemorySaver()

    def host():
        return create_app(
            business_module=create_module(writes=True, transport=transport),
            client_factory=lambda: make_client(expense_llm),
            trace_dir=tmp_path / "trace", checkpointer=saver,
        )

    app = host()
    assert "订单" not in app.state.service._system_prompt()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="http://host",
    ) as client:
        runtime = (await client.get("/api/runtime")).json()
        assert runtime["module"]["name"] == "expense-example"
        assert runtime["module"]["connector"] == {
            "scheme": "http", "authenticated": False,
            "verify_tls": True, "timeout_seconds": 10.0,
        }
        assert not runtime["drafts_enabled"]
        assert runtime["writes_enabled"]
        assert not any("order-drafts" in route.path for route in app.routes)
        stream = app.state.service.stream_run("s1", "报销客户拜访交通费38元")
        async for event in stream:
            if isinstance(event, ApprovalPending):
                await stream.aclose()
                break
        state = (await client.get("/api/sessions/s1/state")).json()
        pending = state["pending_approvals"][0]

    module = app.state.business_module
    reconciler = module.reconciler("s1")
    token, arguments = pending["arguments"]["client_token"], pending["arguments"]
    assert (await reconciler.lookup("submit_expense", arguments, token)).status == "absent"
    # Reconstruct the host while the persisted graph is waiting for confirmation.
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(host()), base_url="http://host",
    ) as client:
        result = await client.post("/api/chat/approve/stream", json={
            "session_id": "s1", "pending_id": pending["pending_id"], "approved": approved,
        })
        assert result.status_code == 200 and "event: done" in result.text
        replay = await client.post("/api/chat/approve/stream", json={
            "session_id": "s1", "pending_id": pending["pending_id"], "approved": True,
        })
        assert replay.status_code == 409
    lookup = await reconciler.lookup("submit_expense", arguments, token)
    assert lookup.status == ("found" if approved else "absent")
    if approved:
        repeated = await reconciler.submit(ExpenseInput(**arguments))
        assert repeated == lookup.result
        assert repeated["status"] == "pending_erp_approval"
        conflict = {**arguments, "amount_cents": 3900}
        assert (await reconciler.lookup("submit_expense", conflict, token)).status == "conflict"
        with pytest.raises(httpx2.HTTPStatusError):
            await reconciler.submit(ExpenseInput(**conflict))
    other = module.reconciler("s2")
    assert (await other.lookup("submit_expense", arguments, token)).status == "absent"
    other_actor = module.reconciler_for(BusinessSessionContext(
        "s1", user_id="another-user", username="other", role="approver",
    ))
    assert (await other_actor.lookup(
        "submit_expense", arguments, token,
    )).status == "absent"


async def test_authenticated_module_receives_verified_identity_and_fails_closed(tmp_path):
    class ReadArgs(BaseModel):
        value: str

    class WriteArgs(BaseModel):
        value: str
        client_token: str | None = None

    async def handler(args):
        return args.model_dump()

    class Reconciler:
        def __init__(self, context):
            self.context = context

        async def lookup(self, tool_name, arguments, client_token):
            return None

    read = Tool("read", "read", ReadArgs, handler)
    write = Tool("write", "write", WriteArgs, handler, risk="single_confirm")
    contexts = []

    def tools_for(context):
        contexts.append(context)
        return [read, write]

    module = BusinessModule(
        name="identity", title="Identity", instructions="Test identity binding.",
        tools=[read, write], for_context=tools_for, reconciler_for_context=Reconciler,
    )
    database = tmp_path / "runs.db"
    auth = AuthStore(database)
    approver = auth.add_user("approver-user", "correct horse battery", "approver")
    operator = auth.add_user("operator-user", "correct horse staple!", "operator")
    approver_session = auth.new_session(approver)
    operator_session = auth.new_session(operator)
    app = create_app(
        business_module=module, auth_store=auth, run_store_path=database,
        trace_dir=tmp_path / "trace", checkpointer=InMemorySaver(),
    )
    contexts.clear()  # Ignore startup contract validation.

    assert [tool.name for tool in app.state.service._session_tools_factory(
        approver_session
    )] == ["read", "write"]
    assert contexts[-1] == BusinessSessionContext(
        approver_session, approver.user_id, approver.username, approver.role,
    )
    assert [tool.name for tool in app.state.service._session_tools_factory(
        operator_session
    )] == ["read"]
    reconciler = app.state.service._session_reconciler_factory(approver_session)
    assert reconciler.context == BusinessSessionContext(
        approver_session, approver.user_id, approver.username, approver.role,
    )

    auth.disable_user(approver.username)
    assert app.state.service._session_tools_factory(approver_session) == []
    assert app.state.service._session_reconciler_factory(approver_session) is None
    assert app.state.service._session_tools_factory("unknown-session") == []


async def test_module_startup_ignores_erp_database_and_uses_readonly_default(tmp_path, monkeypatch):
    monkeypatch.setenv("ERPILOT_MODULE", "erpilot_api.examples.expense:create_module")
    monkeypatch.setenv("ERPILOT_TOOLS", "mcp")
    monkeypatch.setenv("ERPILOT_WRITES", "0")
    app = await build_live_app(
        config=LLMConfig(api_key="test", model="test", base_url="http://model"),
        database=tmp_path / "missing.db", trace_dir=tmp_path,
        checkpointer=InMemorySaver(),
    )
    assert [tool.name for tool in app.state.service._tools] == ["get_expense"]
    assert app.state.service._drafts is None
    assert not (tmp_path / "missing.db").exists()
    with pytest.raises(ValueError, match="not installed"):
        await load_module("bad-spec")
    with pytest.raises(ModuleNotFoundError):
        await load_module("missing_company_plugin:create")


async def test_remote_failure_stays_unknown(tmp_path):
    def unavailable(request):
        return httpx2.Response(503)

    module = create_module(writes=True, transport=httpx2.MockTransport(unavailable))
    with pytest.raises(httpx2.HTTPStatusError):
        await module.reconciler("s1").lookup(
            "submit_expense", {"purpose": "travel", "amount_cents": 100}, "token",
        )


async def test_http_connector_credentials_identity_and_health_are_safe(tmp_path):
    config = HttpConnectorConfig(
        "http://127.0.0.1/", service_token="test-service-secret", timeout_seconds=2,
    )
    context = BusinessSessionContext(
        "s1", user_id="user-1", username="operator-one", role="operator",
    )
    headers = config.headers(context)
    assert config.base_url == "http://127.0.0.1"
    assert headers["Authorization"] == "Bearer test-service-secret"
    assert headers["X-Erpilot-User-Id"] == "user-1"
    assert headers["X-Erpilot-Role"] == "operator"
    assert "test-service-secret" not in repr(config)
    assert "test-service-secret" not in json.dumps(config.public_summary())
    with pytest.raises(ValueError, match="require HTTPS"):
        HttpConnectorConfig("http://erp.example.test", service_token="secret")

    transport = httpx2.ASGITransport(backend_app(
        tmp_path / "secured-expenses.db", service_token="test-service-secret",
    ))
    module = create_module(config=config, writes=False, transport=transport)
    await module.health_check()
    app = create_app(
        business_module=module, client_factory=lambda: make_client(expense_llm),
        trace_dir=tmp_path / "trace", checkpointer=InMemorySaver(),
    )
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="http://host",
    ) as client:
        runtime = await client.get("/api/runtime")
    assert runtime.status_code == 200
    assert runtime.json()["module"]["connector"]["authenticated"] is True
    assert "test-service-secret" not in runtime.text

    invalid = create_module(
        config=HttpConnectorConfig(
            "http://127.0.0.1", service_token="wrong-service-secret",
        ),
        writes=False,
        transport=transport,
    )
    with pytest.raises(httpx2.HTTPStatusError):
        await invalid.health_check()


def test_http_connector_environment_parsing(monkeypatch):
    monkeypatch.setenv("COMPANY_ERP_URL", "https://erp.example.test/api/")
    monkeypatch.setenv("COMPANY_ERP_SERVICE_TOKEN", "server-token")
    monkeypatch.setenv("COMPANY_ERP_TIMEOUT", "7.5")
    monkeypatch.setenv("COMPANY_ERP_VERIFY_TLS", "0")
    config = HttpConnectorConfig.from_env("COMPANY_ERP")
    assert config.base_url == "https://erp.example.test/api"
    assert config.timeout_seconds == 7.5
    assert config.verify_tls is False
    assert config.public_summary()["authenticated"] is True

    monkeypatch.setenv("COMPANY_ERP_VERIFY_TLS", "sometimes")
    with pytest.raises(ValueError, match="COMPANY_ERP_VERIFY_TLS"):
        HttpConnectorConfig.from_env("COMPANY_ERP")


async def test_installed_plugin_entry_point_and_contract_validation(monkeypatch):
    module = create_module(writes=False, transport=httpx2.MockTransport(
        lambda request: httpx2.Response(404)
    ))
    point = SimpleNamespace(name="expense", load=lambda: lambda: module)
    monkeypatch.setattr("erpilot_api.modules.entry_points", lambda **kwargs: [point])
    loaded = await load_module("expense")
    assert loaded is module
    assert loaded.manifest()["contract"] == module.manifest()["contract"]


async def test_module_rejects_session_schema_drift_and_unreconciled_writes(tmp_path):
    class ReadArgs(BaseModel):
        value: str

    class ChangedArgs(BaseModel):
        value: int

    class WriteArgs(BaseModel):
        value: str
        client_token: str | None = None

    async def handler(args):
        return args.model_dump()

    read = Tool("read", "read", ReadArgs, handler)
    changed = Tool("read", "read", ChangedArgs, handler)
    drifting = BusinessModule(
        name="drifting", title="Drifting", instructions="Read values.", tools=[read],
        for_session=lambda session_id: [changed],
    )
    with pytest.raises(ValueError, match="preserve tool names"):
        create_app(business_module=drifting, trace_dir=tmp_path,
                   checkpointer=InMemorySaver())

    write = Tool("write", "write", WriteArgs, handler, risk="single_confirm")
    unreconciled = BusinessModule(
        name="unsafe", title="Unsafe", instructions="Write values.", tools=[write],
        for_session=lambda session_id: [write],
    )
    with pytest.raises(ValueError, match="must provide a reconciler"):
        create_app(business_module=unreconciled, trace_dir=tmp_path,
                   checkpointer=InMemorySaver())


async def test_committed_write_with_lost_response_recovers_from_backend(tmp_path):
    path = tmp_path / "expenses.db"
    backend = httpx2.ASGITransport(backend_app(path))

    class LostResponse(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request):
            response = await backend.handle_async_request(request)
            if request.method == "POST":
                await response.aclose()
                raise httpx2.ReadError("connection lost after commit", request=request)
            return response

    args = ExpenseInput(purpose="travel", amount_cents=100, client_token="stable-token")
    module = create_module(writes=True, transport=LostResponse())
    with pytest.raises(httpx2.ReadError):
        await module.reconciler("s1").submit(args)
    # Reopen the independent backend database and reconstruct the adapter.
    restarted = create_module(writes=True, transport=httpx2.ASGITransport(backend_app(path)))
    adapter = restarted.reconciler("s1")
    recovered = await adapter.lookup("submit_expense", args.model_dump(), args.client_token)
    assert recovered.status == "found"
    assert await adapter.submit(args) == recovered.result


async def test_expense_backend_migrates_legacy_session_records(tmp_path):
    path = tmp_path / "expenses.db"
    legacy = {
        "expense_id": "legacy-expense", "purpose": "travel", "amount_cents": 100,
        "client_token": "legacy-token", "status": "pending_erp_approval",
    }
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE expenses (session_id TEXT, token TEXT, payload TEXT NOT NULL, "
            "PRIMARY KEY (session_id, token))"
        )
        connection.execute(
            "INSERT INTO expenses VALUES (?, ?, ?)",
            ("s1", "legacy-token", json.dumps(legacy)),
        )

    module = create_module(
        writes=True, transport=httpx2.ASGITransport(backend_app(path)),
    )
    result = await module.reconciler("s1").query(
        SimpleNamespace(client_token="legacy-token")
    )
    assert result == legacy
    with sqlite3.connect(path) as connection:
        columns = [row[1] for row in connection.execute(
            "PRAGMA table_info(expenses)"
        ).fetchall()]
    assert columns == ["actor_id", "session_id", "token", "payload"]
