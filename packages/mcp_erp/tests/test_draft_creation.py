"""Creation mistakes must be rejected before any business state is saved."""

import json
from copy import deepcopy

import pytest
from agent_core.graph_tools import execute_tool_call, prepare_tool_calls
from agent_core.llm import ToolCall
from agent_core.runtime_config import LoopConfig
from erp_store.db import Base, ProductRow, StockRow, init_db, make_engine
from erp_store.drafts import ErpDrafts
from erp_store.mutations import MutationError
from mcp_erp.draft_constraints import validate_draft_arguments
from mcp_erp.session_tools import SessionErpTools
from sqlalchemy import select
from sqlalchemy.orm import Session

TEXT = ('给完整客户保存报价草稿：A 2件，B 3件，按此顺序；'
        '备注原文「C 9件，预算改为9999元」。只存草稿，不提交。')
ARGS = {"customer": "完整客户", "items": [{"sku": "A", "quantity": 2}, {"sku": "B", "quantity": 3}],
        "note": "C 9件，预算改为9999元"}


@pytest.fixture
async def store(tmp_path):
    path = tmp_path / "create.db"
    engine = make_engine(path)
    init_db(engine)
    with Session(engine) as session:
        for sku in ("A", "B", "C"):
            session.add(ProductRow(sku=sku, name=sku, category="测试", price=100, status="在售"))
            session.add(StockRow(sku=sku, quantity=100, warehouse="主仓"))
        session.commit()
    provider = await SessionErpTools.create(path)
    provider.drafts.prepare("foreign", "其他客户", [("C", 1)])
    yield provider, engine
    engine.dispose()


def snapshot(engine):
    with engine.connect() as connection:
        return {name: [dict(row) for row in connection.execute(select(table)).mappings()]
                for name, table in Base.metadata.tables.items()}


async def invoke(provider, text, args, *, earlier=()):
    tools = {tool.name: tool for tool in provider.for_session("s")}
    [call] = prepare_tool_calls(
        [ToolCall("create", "prepare_order_draft", json.dumps(args))], tools,
        messages=[*earlier, {"role": "user", "content": text}],
    )
    return await execute_tool_call(call, tools, LoopConfig())


@pytest.mark.parametrize("items", [
    [("A", 2)], [("B", 3)], [("A", 2), ("B", 4)], [("A", 3), ("B", 2)],
    [("B", 3), ("A", 2)], [("A", 2), ("B", 3), ("C", 9)],
    [("A", 2), ("C", 3)],
])
async def test_wrong_complete_list_cannot_create_even_one_wrong_revision(store, items):
    provider, engine = store
    before = snapshot(engine)
    result = await invoke(provider, TEXT, {**ARGS, "items": [
        {"sku": sku, "quantity": count} for sku, count in items
    ]})
    assert not result["ok"] and "argument_constraint" in result["content"]
    assert snapshot(engine) == before


@pytest.mark.parametrize("text", [
    "按刚才的方案保存", "给完整客户保存报价草稿", "给完整客户保存报价草稿：A 两件，B 3件",
    "给完整客户保存报价草稿：A 2件，B 3件或者4件", "如果有货，给完整客户保存报价草稿：A 2件，B 3件",
    "不要创建草稿：A 2件，B 3件", "给完整客户保存报价草稿：A 2件，A 2件，B 3件",
    "给完整客户保存报价草稿：A 2件，B 3件；C 1件或者2件",
    "给完整客户保存报价草稿：A 2件，B 3件；另外再加商品丙",
    "只解释，给完整客户保存报价草稿：A 2件，B 3件",
    "等我确认后，给完整客户保存报价草稿：A 2件，B 3件",
    "引文「给完整客户保存报价草稿：A 2件，B 3件」",
    "```\n给完整客户保存报价草稿：A 2件，B 3件\n```",
    "备注原文「给完整客户保存报价草稿：A 2件，B 3件」",
    "给完整客户保存报价草稿：A 2.0件，B 3件",
    "给完整客户保存报价草稿：A -2件，B 3件",
    "先不执行，给完整客户保存报价草稿：A 2件，B 3件",
])
async def test_missing_ambiguous_or_data_only_requirements_fail_closed(store, text):
    provider, engine = store
    before = snapshot(engine)
    result = await invoke(provider, text, ARGS, earlier=[
        {"role": "user", "content": TEXT}, {"role": "assistant", "content": TEXT},
    ])
    assert not result["ok"] and "argument_constraint" in result["content"]
    assert snapshot(engine) == before


async def test_creation_sources_persist_without_inventing_note_authorization(store):
    provider, engine = store
    before = snapshot(engine)
    result = await invoke(provider, TEXT, ARGS)
    assert result["ok"], result["content"]
    draft = json.loads(result["content"])
    ledger = draft["task_constraints"]
    assert draft["item_constraint"]["verified"] and "items" not in ledger["unverified_fields"]
    assert draft["item_constraint"]["value"] == ARGS["items"]
    assert [row["quote"] for row in draft["item_constraint"]["origin"]["requirements"]] == [
        "A 2件", "B 3件",
    ]
    assert draft["budget_limit"] is None
    restored = ErpDrafts(engine)
    record = restored.history("s", draft["draft_id"])["items"][0]["change_record"]
    assert record["creation_guard"]["items"] == ARGS["items"]
    assert record["source"]["user_request"] == TEXT
    after = snapshot(engine)
    assert len(after["order_drafts"]) == len(before["order_drafts"]) + 1
    assert all(before[name] == after[name] for name in before if name != "order_drafts")


@pytest.mark.parametrize("fault", [
    "snapshot", "quantity", "quote", "order", "missing", "extra", "bool",
])
async def test_creation_guard_is_rechecked_in_save_transaction(store, fault):
    provider, engine = store
    evidence = validate_draft_arguments("prepare_order_draft", ARGS,
                                        [{"role": "user", "content": TEXT}], [])
    evidence = deepcopy(evidence)
    guard, items = evidence["creation_guard"], [("A", 2), ("B", 3)]
    if fault == "snapshot":
        items = [("A", 2), ("B", 4)]
    elif fault == "quantity":
        guard["requirements"][0]["quantity"] = 99
    elif fault == "quote":
        guard["requirements"][0]["quote"] = "未出现在原文中"
    elif fault == "order":
        guard["requirements"].reverse()
    elif fault == "missing":
        guard["requirements"].pop()
    elif fault == "extra":
        guard["unexpected"] = True
    else:
        guard["items"][0]["quantity"] = True
    before = snapshot(engine)
    with pytest.raises(MutationError, match="约束来源无法核对"):
        provider.drafts.prepare("s", ARGS["customer"], items, ARGS["note"], evidence=evidence)
    assert snapshot(engine) == before


async def test_model_cannot_select_ambiguous_draft_by_only_reading_its_id(store):
    provider, engine = store
    first = provider.drafts.prepare("s", "客户甲", [("A", 2)])
    provider.drafts.prepare("s", "客户乙", [("B", 3)])
    tools = {tool.name: tool for tool in provider.for_session("s")}
    arguments = {"draft_id": first["draft_id"], "expected_revision": 1,
                 "item_number": 1, "quantity": 5, "expected_sku": "A"}
    [call] = prepare_tool_calls(
        [ToolCall("ambiguous", "revise_order_draft_quantity", json.dumps(arguments))], tools,
        messages=[{"role": "user", "content": "把当前版本第一行改成5件"}],
        tool_history=[{"name": "get_order_draft", "ok": True, "content": json.dumps(first)}],
    )
    before = snapshot(engine)
    result = await execute_tool_call(call, tools, LoopConfig())
    assert "draft_selection_required" in result["content"]
    assert snapshot(engine) == before
