import json
import tomllib

import httpx2
import pytest
from agent_core.testing import USAGE, chunk, make_client, sse_response, tool_call_chunks
from erpilot_api.bootstrap import build_live_app
from erpilot_api.modules import ENTRY_POINT_GROUP, BusinessSessionContext, load_module
from erpilot_cli.main import app
from langgraph.checkpoint.memory import InMemorySaver
from typer.testing import CliRunner


async def test_generated_ticketing_plugin_runs_through_live_api(tmp_path, monkeypatch):
    args = ["plugin", "init", "acme-ticketing", "--output", str(tmp_path)]
    runner = CliRunner()
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output

    target = tmp_path / "acme-ticketing"
    project = tomllib.loads((target / "pyproject.toml").read_text(encoding="utf-8"))
    spec = project["project"]["entry-points"][ENTRY_POINT_GROUP]["acme-ticketing"]
    assert spec == "erpilot_plugin_acme_ticketing:create_module"
    monkeypatch.syspath_prepend(str(target / "src"))
    module = await load_module(spec)
    tool, = module.tools_for(BusinessSessionContext("session"))
    assert tool.risk is None
    missing = await tool.handler(tool.params_model(ticket_id="MISSING"))
    assert missing["status"] == "not_found"

    def ticket_llm(request):
        message = json.loads(request.content)["messages"][-1]
        if message["role"] == "tool":
            assert json.loads(message["content"]) == {
                "status": "found", "ticket_id": "DEMO-001",
                "subject": "示例工单", "ticket_status": "处理中",
            }
            return sse_response([chunk(delta={"content": "工单正在处理中"}), chunk(usage=USAGE)])
        return sse_response(tool_call_chunks(
            "ticket-query", "get_ticket", '{"ticket_id":"DEMO-001"}',
        ))

    monkeypatch.setenv("ERPILOT_MODULE", spec)
    model = make_client(ticket_llm, max_retries=0)
    host = await build_live_app(
        config=model.config, client_factory=lambda: model, auth_enabled=False,
        run_store_path=tmp_path / "runs.db", trace_dir=tmp_path / "traces",
        checkpointer=InMemorySaver(),
    )
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(host), base_url="http://host",
    ) as client:
        runtime = (await client.get("/api/runtime")).json()
        assert runtime["module"]["name"] == "acme-ticketing"
        assert not runtime["drafts_enabled"] and not runtime["writes_enabled"]
        response = await client.post("/api/chat/stream", json={"message": "查询工单 DEMO-001"})
        assert response.status_code == 200, response.text
        assert "工单正在处理中" in response.text and "event: done" in response.text

    readme = target / "README.md"
    readme.write_text("local edits", encoding="utf-8")
    assert runner.invoke(app, args).exit_code == 1
    assert readme.read_text(encoding="utf-8") == "local edits"


@pytest.mark.parametrize("name", ["../outside", "Acme_ERP", "expense-example"])
def test_scaffold_rejects_invalid_or_reserved_names(tmp_path, name):
    result = CliRunner().invoke(app, ["plugin", "init", name, "-o", str(tmp_path)])
    assert result.exit_code == 2
    assert list(tmp_path.iterdir()) == []
