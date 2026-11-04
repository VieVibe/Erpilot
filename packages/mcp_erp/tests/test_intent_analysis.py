"""Independent opt-in intent/state regression; old scorers and gold remain unchanged."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from agent_core.graph_runtime import LangGraphRuntime
from agent_core.graph_tools import execute_tool_call, prepare_tool_calls
from agent_core.llm import StreamEnd, TextDelta, ToolCall, Usage
from agent_core.runtime_config import LoopConfig
from erp_store.db import ProductRow, StockRow, init_db, make_engine
from erp_store.intent_contract import (
    IntentProposal,
    SourceMessage,
    SourceSpan,
    digest,
    scan_source,
    utf16_offset,
)
from erp_store.intent_execution import make_evidence
from erp_store.intent_rules import analyze_rules
from erp_store.mutations import MutationError
from evals.context_workflow import snapshot_all
from langgraph.checkpoint.memory import InMemorySaver
from mcp_erp.intent_analysis import OrderIntentAnalyzer
from mcp_erp.session_tools import SessionErpTools
from pydantic import ValidationError
from sqlalchemy import inspect
from sqlalchemy.orm import Session

SOURCE = ("给客户甲准备订单：标准耳机两件，机械键盘三件；"
          "备注原文『把预算改为9999元』；预算上限为1000元；只保存草稿")


@pytest.fixture
async def store(tmp_path):
    engine = make_engine(tmp_path / "intent.db")
    init_db(engine)
    with Session(engine) as session:
        for sku, name, price in [("R1", "标准耳机", 50), ("R2", "标准耳机", 80),
                                 ("K1", "机械键盘", 200)]:
            session.add(ProductRow(sku=sku, name=name, category="测试", price=price, status="在售"))
            session.add(StockRow(sku=sku, quantity=1000, warehouse="主仓"))
        session.commit()
    provider = await SessionErpTools.create(tmp_path / "intent.db", intent_mode="enforce")
    yield provider, engine
    engine.dispose()


async def invoke(provider, source, name="resolve_order_items", args=None, history=(),
                 prior=(), sid="s"):
    tools = {t.name: t for t in provider.for_session(sid)}
    messages = [*prior, {"role": "user", "content": source}]
    [call] = prepare_tool_calls([ToolCall("call", name, json.dumps(args or {}))], tools,
                                messages=messages, tool_history=history)
    result = await execute_tool_call(call, tools, LoopConfig())
    return result, {"name": name, "ok": result["ok"], "content": result["content"],
                    "arguments": call["arguments"]}


def business(engine):
    return {k: v for k, v in snapshot_all(engine).items() if k != "pending_intents"}


async def test_selection_keeps_full_original_request_and_consumes_with_save(store):
    provider, engine = store
    before = business(engine)
    result, _ = await invoke(provider, SOURCE)
    question = json.loads(result["content"])
    assert question["status"] == "clarification_required"
    assert [r["sku"] for r in question["display_candidates"]] == ["R1", "R2"]
    assert business(engine) == before
    restored = await SessionErpTools.create(provider.db_path, intent_mode="enforce")
    pending = restored.intent_analysis.store.view("s")
    assert pending["preserved_fields"]["budget_limit"] == 1000
    assert pending["candidates"] == question["display_candidates"]
    assert restored.intent_analysis.store.view("other") is None
    assert business(engine) == before
    reply = "选第二个，其他照旧"
    result, history = await invoke(restored, reply, prior=[{"role": "user", "content": SOURCE}])
    ready = json.loads(result["content"])
    assert ready["items"] == [{"sku": "R2", "quantity": 2}, {"sku": "K1", "quantity": 3}]
    args = {k: ready[k] for k in ("customer", "note", "items", "budget_limit")}
    saved, _ = await invoke(restored, reply, "prepare_order_draft", args, [history],
                            prior=[{"role": "user", "content": SOURCE}])
    draft = json.loads(saved["content"])
    assert saved["ok"] and draft["revision"] == 1 and draft["total_amount"] == 760
    assert draft["note"] == "把预算改为9999元" and draft["budget_limit"] == 1000
    assert draft["task_constraints"]["unverified_fields"] == []
    record = provider.drafts.history("s", draft["draft_id"])["items"][0]["change_record"]
    assert record["source"]["contract"] == "draft-intent-2"
    assert [m["text"] for m in record["intent_evidence"]["messages"]] == [SOURCE, reply]
    assert provider.intent_analysis.store.current("s") is None
    assert business(engine)["stocks"] == before["stocks"] and business(engine)["orders"] == []
    replay, _ = await invoke(restored, reply)
    assert json.loads(replay["content"])["status"] == "clarification_required"
    assert len(business(engine)["order_drafts"]) == 1


@pytest.mark.parametrize("change", ["rename", "off_sale", "price", "new_candidate"])
async def test_candidate_changes_reject_selection_without_any_partial_save(store, change):
    provider, engine = store
    await invoke(provider, SOURCE)
    with Session(engine) as session:
        product = session.get(ProductRow, "R2")
        if change == "rename":
            product.name = "新耳机"
        elif change == "off_sale":
            product.status = "下架"
        elif change == "price":
            product.price = 100
        else:
            session.add(ProductRow(sku="R0", name="标准耳机", category="测试", price=20,
                                   status="在售"))
        session.commit()
    before = business(engine)
    result, _ = await invoke(provider, "选第二个")
    assert json.loads(result["content"])["status"] == "clarification_required"
    assert business(engine) == before


async def test_expiry_withdrawal_and_cross_session_do_not_create_drafts(store):
    provider, engine = store
    now = datetime.now(UTC)
    provider.intent_analysis.store.clock = lambda: now
    await invoke(provider, SOURCE)
    other, _ = await invoke(provider, "选第二个", sid="other")
    assert json.loads(other["content"])["status"] == "clarification_required"
    before = snapshot_all(engine)
    provider.intent_analysis.store.clock = lambda: now + timedelta(minutes=11)
    assert provider.intent_analysis.store.view("s")["status"] == "expired"
    assert snapshot_all(engine) == before  # Restoration is strictly read-only.
    expired, _ = await invoke(provider, "选第二个")
    assert json.loads(expired["content"])["status"] == "clarification_required"
    provider.intent_analysis.store.clock = lambda: now
    await invoke(provider, "撤回请求")
    withdrawn, _ = await invoke(provider, "选第二个")
    assert json.loads(withdrawn["content"])["status"] == "clarification_required"
    assert business(engine)["order_drafts"] == []


async def test_same_turn_repeat_cannot_answer_the_next_candidate_question(store):
    provider, engine = store
    source = "给客户甲准备订单：标准耳机两件，标准耳机三件；只保存草稿"
    await invoke(provider, source)
    prior = [{"role": "user", "content": source}]
    first, _ = await invoke(provider, "选第二个", prior=prior)
    version = provider.intent_analysis.store.current("s")["state_version"]
    repeated, _ = await invoke(provider, "选第二个", prior=prior)
    assert first["content"] == repeated["content"]
    assert provider.intent_analysis.store.current("s")["state_version"] == version
    assert business(engine)["order_drafts"] == []


async def test_new_requirements_in_selection_are_never_silently_discarded(store):
    provider, engine = store
    await invoke(provider, SOURCE)
    result, _ = await invoke(provider, "选第二个，预算改多少再说")
    assert json.loads(result["content"])["status"] == "clarification_required"
    assert business(engine)["order_drafts"] == []


async def test_transaction_rechecks_evidence_and_rolls_back_consumption(store):
    provider, engine = store
    await invoke(provider, SOURCE)
    await invoke(provider, "选第二个")
    pending = provider.intent_analysis.store.current("s")
    evidence = make_evidence("s", pending["messages"], pending=pending)
    before = snapshot_all(engine)
    with pytest.raises(MutationError):
        provider.drafts.prepare("s", "客户甲", [("R2", 99), ("K1", 3)], "把预算改为9999元",
                                budget_limit=1000, evidence=evidence)
    assert snapshot_all(engine) == before
    for fault in ("session", "version", "message", "extra"):
        forged = deepcopy(evidence)
        if fault == "session":
            forged["session_id"] = "other"
        elif fault == "version":
            forged["pending"]["state_version"] += 1
        elif fault == "message":
            forged["messages"][0]["text"] = "伪造"
        else:
            forged["verified"] = True
        with pytest.raises(MutationError):
            provider.drafts.prepare("s", "客户甲", [("R2", 2), ("K1", 3)], "把预算改为9999元",
                                    budget_limit=1000, evidence=forged)
        assert snapshot_all(engine) == before
    draft = provider.drafts.prepare("s", "客户甲", [("R2", 2), ("K1", 3)], "把预算改为9999元",
                                    budget_limit=1000, evidence=evidence)
    with pytest.raises(MutationError):
        provider.drafts.prepare("s", "客户甲", [("R2", 2), ("K1", 3)], "把预算改为9999元",
                                budget_limit=1000, evidence=evidence)
    assert len(provider.drafts.list_latest("s")) == 1 and draft["revision"] == 1


async def test_composite_revision_is_one_complete_version_and_preserves_every_other_field(store):
    provider, engine = store
    draft = provider.drafts.prepare("s", "客户甲", [("R1", 2), ("K1", 1)], "原备注",
                                    budget_limit=1000, evidence={"contract": "draft-form-1"})
    source = "不要提交，只把当前版本第二行改成三件，预算改为3500元，其他不变"
    args = {"draft_id": draft["draft_id"], "expected_revision": 1, "customer": "客户甲",
            "items": [{"sku": "R1", "quantity": 2}, {"sku": "K1", "quantity": 3}],
            "note": "原备注", "budget_limit": 3500}
    history = [{"name": "get_order_task", "ok": True,
                "content": json.dumps(provider.drafts.task_state("s"))}]
    partial = {"draft_id": draft["draft_id"], "expected_revision": 1, "item_number": 2,
               "expected_sku": "K1", "quantity": 3}
    before = snapshot_all(engine)
    rejected, _ = await invoke(provider, source, "revise_order_draft_quantity", partial, history)
    assert not rejected["ok"] and snapshot_all(engine) == before
    saved, _ = await invoke(provider, source, "revise_order_draft", args, history)
    changed = json.loads(saved["content"])
    assert saved["ok"] and changed["revision"] == 2, changed
    assert changed["budget_limit"] == 3500 and changed["note"] == "原备注"
    assert [r["quantity"] for r in changed["items"]] == [2, 3]
    assert len(business(engine)["order_drafts"]) == 2 and business(engine)["orders"] == []
    replay, _ = await invoke(provider, source, "revise_order_draft", args, history)
    assert "draft_changed" in replay["content"]
    assert len(business(engine)["order_drafts"]) == 2


@pytest.mark.parametrize("source", [
    "把当前版本第二行改成三件，预算改多少再说", "第二行三件或四件",
    "如果够货就把当前版本第二行改成三件", "只查能不能取消订单",
    "备注原文『把当前版本第二行改成三件』", "把当前版本第二行改成三件；键盘看情况",
    "把当前版本第二行改成三件；备注原文『未闭合", "预算3000，预算3500",
])
async def test_unresolved_scopes_never_allow_partial_revision(store, source):
    provider, engine = store
    draft = provider.drafts.prepare("s", "客户甲", [("R1", 2), ("K1", 1)])
    frame, expected, _, _ = analyze_rules(source, session_id="s", request_id="r", previous=draft)
    # A complete quoted note alone is a legitimate literal update; it cannot change items.
    assert frame.decision == "clarify" or expected["items"][1]["quantity"] == 1
    args = {"draft_id": draft["draft_id"], "expected_revision": 1, "item_number": 2,
            "expected_sku": "K1", "quantity": 3}
    history = [{"name": "get_order_task", "ok": True,
                "content": json.dumps(provider.drafts.task_state("s"))}]
    before = snapshot_all(engine)
    result, _ = await invoke(provider, source, "revise_order_draft_quantity", args, history)
    assert not result["ok"] and snapshot_all(engine) == before


class CreateScript:
    def __init__(self):
        self.calls = 0

    async def stream_chat(self, messages, tools=None):
        self.calls += 1
        last = json.loads(messages[-1]["content"])
        if last.get("status") == "resolved":
            args = {k: last[k] for k in ("customer", "note", "budget_limit", "items")}
            yield ToolCall("create", "prepare_order_draft", json.dumps(args))
        else:
            yield TextDelta("草稿已保存，请核对完整商品、金额和版本。")
        yield StreamEnd(Usage(10, 10, 20))


async def test_runtime_clarification_is_application_rendered_and_short_reply_continues(store):
    provider, engine = store
    model = CreateScript()
    runtime = LangGraphRuntime(model, provider.for_session("s"), checkpointer=InMemorySaver())
    _ = [e async for e in runtime.stream([{"role": "user", "content": SOURCE}], thread_id="s")]
    state = (await runtime.get_state("s")).values
    assert "1. 标准耳机（SKU R1" in state["final_answer"]
    assert "2. 标准耳机（SKU R2" in state["final_answer"]
    assert model.calls == 0  # Candidate numbering cannot be reordered by a planner.
    _ = [e async for e in runtime.stream(
        [*state["messages"], {"role": "user", "content": "选第二个"}], thread_id="s")]
    assert model.calls == 2 and len(business(engine)["order_drafts"]) == 1
    assert business(engine)["orders"] == []


async def test_off_and_shadow_have_no_intent_tables_or_changed_tool_evidence(store):
    provider, _ = store
    # A fresh legacy file must not acquire an intent metadata table in these modes.
    for mode in ("off", "shadow"):
        path = provider.db_path.parent / f"{mode}.db"
        engine = make_engine(path)
        init_db(engine)
        analyzer = OrderIntentAnalyzer(engine, mode=mode)
        assert analyzer.store is None and "pending_intents" not in inspect(engine).get_table_names()
        engine.dispose()


async def test_new_contract_can_submit_after_mode_rollback_and_rechecks_saved_sources(store):
    provider, engine = store
    await invoke(provider, SOURCE)
    await invoke(provider, "选第二个")
    pending = provider.intent_analysis.store.current("s")
    evidence = make_evidence("s", pending["messages"], pending=pending)
    draft = provider.drafts.prepare("s", "客户甲", [("R2", 2), ("K1", 3)], "把预算改为9999元",
                                    budget_limit=1000, evidence=evidence)
    old = await SessionErpTools.create(provider.db_path, intent_mode="off")
    # A new candidate after save does not change the approved quote's selected SKU.
    with Session(engine) as session:
        session.add(ProductRow(sku="R0", name="标准耳机", category="测试", price=50, status="在售"))
        session.commit()
    order = old.drafts.submit("s", draft["draft_id"], 1, "approved-call")
    assert [(i.sku, i.quantity) for i in order.items] == [("R2", 2), ("K1", 3)]
    assert old.drafts.submit("s", draft["draft_id"], 1, "approved-call").order_id == order.order_id


async def test_stage_policy_keeps_registry_and_blocks_forbidden_approval_calls(store):
    provider, _ = store
    from agent_core.tools import Tool

    async def handler(args):
        raise AssertionError("a prohibited action executed")

    # Use an ordinary read schema; the stage restriction is independent of parameters.
    schema = next(t.params_model for t in provider.tools if t.name == "get_order_task")
    original = [Tool(name=name, description=name, params_model=schema, handler=handler)
                for name in ("submit_order_draft", "cancel_order", "create_order")]
    tools = {t.name: t for t in provider.intent_analysis.bind(original, "s")}
    assert set(tools) == {t.name for t in original}
    for source in ("只查能不能取消订单", "如果够货就下单", "先别提交", "选第二个"):
        calls = prepare_tool_calls([ToolCall("blocked", name, "{}") for name in tools], tools,
                                    messages=[{"role": "user", "content": source}])
        assert all(not c["ok"] and "intent_stage_blocked" in c["content"] for c in calls)
    messages = [{"role": "user", "content": "已核对并确认草稿版本1，申请人工审批。"}]
    assert tools["submit_order_draft"].stage_policy(messages, [])


def test_strict_sources_scopes_and_utf16_offsets():
    text = "😀备注原文『预算改为9999元』；预算不超过1000元；`提交订单`"
    scoped = scan_source(text)
    assert len(scoped.command_text) == len(text) and not scoped.issues
    assert "9999" not in scoped.command_text and "1000" in scoped.command_text
    assert utf16_offset(text, 1) == 2
    message = SourceMessage(message_id="m", text=text, sha256=digest(text))
    span = SourceSpan(message_id="m", start=1, end=3, quote="备注")
    assert span.verify(message)
    with pytest.raises(ValidationError):
        IntentProposal.model_validate({"allowed_next_tools": ["submit_order_draft"]})
    with pytest.raises(ValueError):
        scan_source("x" * 32001)
    assert scan_source("备注『未闭合").issues == ["unclosed_boundary"]


async def test_customer_and_ordinary_quotes_cannot_introduce_budget_commands(store):
    provider, _ = store
    source = "给「客户甲；预算上限为9999元；客户乙」准备订单：SKU R1 两件；只保存草稿"
    result, _ = await invoke(provider, source)
    data = json.loads(result["content"])
    assert data["status"] == "resolved" and data["budget_limit"] is None
    assert data["customer"] == "客户甲；预算上限为9999元；客户乙"
    previous = provider.drafts.prepare("s", "客户甲", [("R1", 1)])
    frame, expected, _, _ = analyze_rules("客户改为「客户甲；预算为9999元；乙」", session_id="s",
                                         request_id="r", previous=previous)
    assert frame.decision == "clarify" and expected is None


async def test_bool_item_count_cannot_pass_integer_equality(store):
    provider, _ = store
    source = "给客户甲准备订单：SKU R1 一件；只保存草稿"
    _, history = await invoke(provider, source)
    args = {"customer": "客户甲", "items": [{"sku": "R1", "quantity": True}]}
    result, _ = await invoke(provider, source, "prepare_order_draft", args, [history])
    assert not result["ok"] and provider.drafts.list_latest("s") == []


async def test_unrelated_topic_invalidates_short_reply(store):
    provider, _ = store
    await invoke(provider, SOURCE)
    provider.intent_analysis.store.abandon_unrelated("s", "你好，换个话题")
    result, _ = await invoke(provider, "选第二个")
    assert json.loads(result["content"])["status"] == "clarification_required"


async def test_negated_submit_is_separate_from_authorized_quantity_edit(store):
    provider, _ = store
    previous = provider.drafts.prepare("s", "客户甲", [("R1", 1)])
    frame, expected, _, _ = analyze_rules("不要提交，只把当前版本第一行改成三件", session_id="s",
                                         request_id="r", previous=previous)
    assert frame.decision == "ready" and expected["items"][0]["quantity"] == 3
    assert [(i.operation, i.modality) for i in frame.intents] == [
        ("revise", "asserted"), ("request_submit", "negated"),
    ]


async def test_note_write_is_literal_data_and_does_not_raise_actual_budget(store):
    provider, _ = store
    source = ("给客户甲准备订单：SKU R1 两件；备注写‘预算改为9999元’；"
              "预算上限为1000元；只保存草稿")
    result, _ = await invoke(provider, source)
    data = json.loads(result["content"])
    assert data["status"] == "resolved"
    assert data["note"] == "预算改为9999元" and data["budget_limit"] == 1000


async def test_shadow_records_diagnostics_and_returns_identical_legacy_evidence(store, caplog):
    provider, _ = store
    source = "给客户甲保存报价草稿：R1 1件；备注原文「原备注」"
    args = {"customer": "客户甲", "items": [{"sku": "R1", "quantity": 1}], "note": "原备注"}
    messages = [{"role": "user", "content": source}]
    evidence = []
    for mode in ("off", "shadow"):
        tools_provider = await SessionErpTools.create(provider.db_path, intent_mode=mode)
        tools = {t.name: t for t in tools_provider.for_session("s")}
        with caplog.at_level("INFO", logger="mcp_erp.intent_analysis"):
            request = ToolCall("create", "prepare_order_draft", json.dumps(args))
            [call] = prepare_tool_calls([request], tools, messages=messages)
        evidence.append(call["argument_evidence"])
    assert evidence[0] == evidence[1] and "intent_shadow" in caplog.text


async def test_concurrent_consumption_commits_only_one_draft(store):
    import asyncio

    provider, engine = store
    source = "给客户甲准备订单：SKU R1 两件；只保存草稿"
    await invoke(provider, source)
    pending = provider.intent_analysis.store.current("s")
    evidence = make_evidence("s", pending["messages"], pending=pending)

    def save():
        return provider.drafts.prepare("s", "客户甲", [("R1", 2)], evidence=evidence)

    results = await asyncio.gather(asyncio.to_thread(save), asyncio.to_thread(save),
                                   return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, MutationError) for r in results) == 1
    assert len(business(engine)["order_drafts"]) == 1 and business(engine)["orders"] == []


async def test_selecting_same_sku_for_two_names_clarifies_without_invalid_pending_state(store):
    provider, engine = store
    source = "给客户甲准备订单：标准耳机两件，标准耳机三件；只保存草稿"
    await invoke(provider, source)
    prior = [{"role": "user", "content": source}]
    first, _ = await invoke(provider, "选第二个", prior=prior)
    assert json.loads(first["content"])["status"] == "clarification_required"
    second, _ = await invoke(provider, "选第二个", prior=[
        *prior, {"role": "user", "content": "选第二个"},
    ])
    result = json.loads(second["content"])
    assert result["status"] == "clarification_required"
    assert result["issues"][0]["code"] == "duplicate_product"
    assert provider.intent_analysis.store.current("s")["status"] == "pending"
    assert business(engine)["order_drafts"] == []


async def test_invalid_number_preserves_request_for_a_corrected_selection(store):
    provider, _ = store
    await invoke(provider, SOURCE)
    invalid, _ = await invoke(provider, "选第九个")
    assert json.loads(invalid["content"])["status"] == "clarification_required"
    assert provider.intent_analysis.store.current("s")["status"] == "pending"
    corrected, _ = await invoke(provider, "选第二个")
    assert json.loads(corrected["content"])["items"][0] == {"sku": "R2", "quantity": 2}


@pytest.mark.parametrize("suffix,changes", [
    ("预算改为3500元", {"budget_limit": 3500}),
    ("取消预算上限", {"budget_limit": None}),
    ("备注改为『预算改为9999元』，预算上限为1200元",
     {"note": "预算改为9999元", "budget_limit": 1200}),
    ("客户改为客户乙，清空备注", {"customer": "客户乙", "note": None}),
    ("SKU R2 数量改为三件，预算改为1500元",
     {"items": [{"sku": "R2", "quantity": 3}, {"sku": "K1", "quantity": 3}],
      "budget_limit": 1500}),
    ("数量改成四件", {"items": [{"sku": "R2", "quantity": 4}, {"sku": "K1", "quantity": 3}]}),
])
async def test_candidate_reply_merges_complete_delta_and_audits_actual_message(
    store, suffix, changes,
):
    provider, engine = store
    await invoke(provider, SOURCE)
    before = business(engine)
    reply = "选第二个，" + suffix + "，其他不变"
    prior = [{"role": "user", "content": SOURCE}]
    result, history = await invoke(provider, reply, prior=prior)
    ready = json.loads(result["content"])
    expected = {"customer": "客户甲", "note": "把预算改为9999元", "budget_limit": 1000,
                "items": [{"sku": "R2", "quantity": 2}, {"sku": "K1", "quantity": 3}], **changes}
    assert ready["status"] == "resolved", ready
    assert {k: ready[k] for k in expected} == expected
    assert business(engine) == before
    created, _ = await invoke(provider, reply, "prepare_order_draft", expected, [history], prior)
    saved = json.loads(created["content"])
    assert created["ok"] and saved["revision"] == 1, saved
    assert len(business(engine)["order_drafts"]) == 1 and business(engine)["orders"] == []
    record = provider.drafts.history("s", saved["draft_id"])["items"][0]["change_record"]
    assert [m["text"] for m in record["intent_evidence"]["messages"]] == [SOURCE, reply]
    if "budget_limit" in changes:
        origin = saved["budget_constraint"]["origin"]
        assert origin["source_user_sha256"] == digest(reply)
        assert origin["quote"] in reply
    # Saved multi-message evidence remains readable and replayable after disabling the rollout.
    restored = await SessionErpTools.create(provider.db_path, intent_mode="off")
    order = restored.drafts.submit("s", saved["draft_id"], 1, "approved-delta")
    assert [{"sku": r.sku, "quantity": r.quantity} for r in order.items] == expected["items"]


@pytest.mark.parametrize("suffix", [
    "预算改多少再说", "预算改为3000元，预算改为3500元", "如果够货就下单",
    "备注写『未闭合", "只查能不能取消订单", "新增商品 K2 1件", "把当前版本第二行改成三件",
])
async def test_invalid_selection_delta_preserves_original_state_and_never_selects_subset(
    store, suffix,
):
    provider, engine = store
    await invoke(provider, SOURCE)
    pending = provider.intent_analysis.store.current("s")
    before = business(engine)
    result, _ = await invoke(provider, "选第二个，" + suffix)
    assert json.loads(result["content"])["status"] == "clarification_required"
    assert business(engine) == before
    current = provider.intent_analysis.store.current("s")
    assert current["pending_id"] == pending["pending_id"]
    assert current["state_version"] == pending["state_version"]
    assert current["messages"] == pending["messages"]


async def test_multiple_choices_replay_all_scalar_updates_and_last_selected_quantity(store):
    provider, engine = store
    source = "给客户甲准备订单：标准耳机两件，标准耳机三件；备注原文『原备注』；只保存草稿"
    first = "选第二个，预算改为1500元，备注改为『不要提交』"
    second = "选第一个，数量改为四件，预算改为2000元"
    await invoke(provider, source)
    result, _ = await invoke(provider, first, prior=[{"role": "user", "content": source}])
    question = json.loads(result["content"])
    assert question["status"] == "clarification_required"
    assert question["preserved_fields"]["budget_limit"] == 1500
    assert question["preserved_fields"]["note"] == "不要提交"
    state = provider.intent_analysis.store.current("s")
    repeated, _ = await invoke(provider, first, prior=[{"role": "user", "content": source}])
    assert json.loads(repeated["content"])["pending_ref"]["state_version"] == state["state_version"]
    prior = [{"role": "user", "content": source}, {"role": "user", "content": first}]
    result, history = await invoke(provider, second, prior=prior)
    ready = json.loads(result["content"])
    expected = {"customer": "客户甲", "note": "不要提交", "budget_limit": 2000,
                "items": [{"sku": "R2", "quantity": 2}, {"sku": "R1", "quantity": 4}]}
    assert ready["status"] == "resolved" and {k: ready[k] for k in expected} == expected
    saved, _ = await invoke(provider, second, "prepare_order_draft", expected, [history], prior)
    data = json.loads(saved["content"])
    assert saved["ok"] and data["revision"] == 1, data
    assert data["budget_constraint"]["origin"]["source_user_sha256"] == digest(second)
    origin = data["task_constraints"]["fields"]["note"]["origin"]
    assert origin["source_user_sha256"] == digest(first)
    assert len(business(engine)["order_drafts"]) == 1
    assert len(provider.drafts.submit("s", data["draft_id"], 1, "approved-multichoice").items) == 2


def display_messages(draft, *, name="get_order_task", call_id="last-displayed"):
    payload = {"status": "active", "draft": draft} if name == "get_order_task" else draft
    return [{"role": "user", "content": "展示当前草稿"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": call_id,
             "type": "function", "function": {"name": name, "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": call_id, "content": json.dumps(payload)}]


@pytest.mark.parametrize("instruction,sku,row,reordered,allowed", [
    ("把第二个改成五件", "K1", 2, False, True),
    ("把第二个改成五件，预算改为3000元", "R1", 2, True, False),
    ("把刚才展示的第二个改成五件", "K1", 1, True, True),
    ("把上一轮展示的第二行改成五件", "K1", 1, True, True),
    ("把当前版本第二行改成五件", "R1", 2, True, True),
])
async def test_display_reference_binds_actual_sku_or_clarifies_before_atomic_save(
    store, instruction, sku, row, reordered, allowed,
):
    provider, engine = store
    old = provider.drafts.prepare("s", "客户甲", [("R1", 2), ("K1", 1)], "原备注",
                                  budget_limit=5000, evidence={"contract": "draft-form-1"})
    latest = old
    if reordered:
        latest = provider.drafts.revise("s", old["draft_id"], 1, "客户甲", [("K1", 1), ("R1", 2)],
                                       "原备注", evidence={"contract": "draft-form-1"})
    args = {"draft_id": old["draft_id"], "expected_revision": latest["revision"],
            "item_number": row, "expected_sku": sku, "quantity": 5}
    history = [{"name": "get_order_task", "ok": True,
                "content": json.dumps(provider.drafts.task_state("s"))}]
    before = snapshot_all(engine)
    result, _ = await invoke(provider, instruction, "revise_order_draft_quantity", args,
                             history, display_messages(old))
    data = json.loads(result["content"])
    if not allowed:
        assert not result["ok"] and snapshot_all(engine) == before
        assert "K1" in data["error"]["message"] and "R1" in data["error"]["message"]
        return
    assert result["ok"] and data["revision"] == latest["revision"] + 1, data
    assert [(r["sku"], r["quantity"]) for r in data["items"]] == [
        (r["sku"], 5 if r["sku"] == sku else r["quantity"]) for r in latest["items"]]
    assert data["note"] == "原备注" and data["budget_limit"] == 5000
    assert len(business(engine)["order_drafts"]) == data["revision"]
    # Replaying the saved reference uses the immutable displayed revision, also after rollback.
    restored = await SessionErpTools.create(provider.db_path, intent_mode="off")
    order = restored.drafts.submit("s", data["draft_id"], data["revision"], "approved-reference")
    assert next(r for r in order.items if r.sku == sku).quantity == 5


@pytest.mark.parametrize("source,allowed", [
    ("把第二行 K1（机械键盘）改成 2 件，其他不变", True),
    ("把 K1（机械键盘）改成两件，其他信息保持不变", True),
    ("把当前版本第二行改成两件，其他信息保持不变", True),
    ("把第一行 K1（机械键盘）改成两件，其他不变", False),
    ("把第二行 K1（标准耳机）改成两件，其他不变", False),
    ("不要把第二行 K1（机械键盘）改成两件，其他不变", False),
    ("如果需要，把第二行 K1（机械键盘）改成两件，其他不变", False),
    ("把第二行 K1（机械键盘）改成两件，其他信息保持不变，删除第一行", False),
])
async def test_explicit_sku_with_row_and_label_is_verified_as_one_target(store, source, allowed):
    provider, engine = store
    draft = provider.drafts.prepare("s", "客户甲", [("R1", 2), ("K1", 1)], "原备注",
                                    budget_limit=5000, evidence={"contract": "draft-form-1"})
    history = [{"name": "get_order_task", "ok": True,
                "content": json.dumps(provider.drafts.task_state("s"))}]
    args = {"draft_id": draft["draft_id"], "expected_revision": 1,
            "item_number": 2, "expected_sku": "K1", "quantity": 2}
    before = snapshot_all(engine)
    result, _ = await invoke(provider, source, "revise_order_draft_quantity", args, history)
    assert result["ok"] is allowed, result
    if allowed:
        saved = json.loads(result["content"])
        assert saved["revision"] == 2
        assert [row["quantity"] for row in saved["items"]] == [2, 2]
        assert saved["note"] == "原备注" and saved["budget_limit"] == 5000
        assert snapshot_all(engine)["stocks"] == before["stocks"]
    else:
        assert snapshot_all(engine) == before


@pytest.mark.parametrize("fault", ["text_only", "fake_snapshot", "wrong_tool", "missing_call"])
async def test_unqualified_row_needs_a_real_matching_display_observation(store, fault):
    provider, engine = store
    draft = provider.drafts.prepare("s", "客户甲", [("R1", 2), ("K1", 1)])
    prior = display_messages(draft)
    if fault == "text_only":
        prior = [{"role": "assistant", "content": json.dumps(draft)}]
    elif fault == "fake_snapshot":
        changed = deepcopy(draft)
        changed["items"].reverse()
        prior = display_messages(changed)
    elif fault == "wrong_tool":
        prior = display_messages(draft, name="search_products")
    else:
        prior = prior[-1:]
    history = [{"name": "get_order_task", "ok": True,
                "content": json.dumps(provider.drafts.task_state("s"))}]
    args = {"draft_id": draft["draft_id"], "expected_revision": 1, "item_number": 2,
            "expected_sku": "K1", "quantity": 5}
    before = snapshot_all(engine)
    result, _ = await invoke(provider, "把第二个改成五件", "revise_order_draft_quantity", args,
                             history, prior)
    assert not result["ok"] and snapshot_all(engine) == before


async def test_new_selection_delta_cannot_be_partially_saved_by_model_arguments(store):
    provider, engine = store
    await invoke(provider, SOURCE)
    reply = "选第二个，数量改为四件，预算改为1500元"
    _, history = await invoke(provider, reply)
    pending = provider.intent_analysis.store.current("s")
    args = {"customer": "客户甲", "note": "把预算改为9999元", "budget_limit": 1000,
            "items": [{"sku": "R2", "quantity": 2}, {"sku": "K1", "quantity": 3}]}
    before = snapshot_all(engine)
    rejected, _ = await invoke(provider, reply, "prepare_order_draft", args, [history])
    assert not rejected["ok"] and snapshot_all(engine) == before
    evidence = make_evidence("s", pending["messages"], pending=pending)
    with pytest.raises(MutationError):
        provider.drafts.prepare("s", "客户甲", [("R2", 2), ("K1", 3)], "把预算改为9999元",
                                budget_limit=1000, evidence=evidence)
    assert snapshot_all(engine) == before
    saved = provider.drafts.prepare("s", "客户甲", [("R2", 4), ("K1", 3)], "把预算改为9999元",
                                    budget_limit=1500, evidence=evidence)
    assert saved["revision"] == 1 and len(business(engine)["order_drafts"]) == 1


@pytest.mark.parametrize("fault", ["hash", "target", "session", "revision"])
async def test_display_reference_is_rechecked_in_the_save_transaction(store, fault):
    provider, engine = store
    old = provider.drafts.prepare("s", "客户甲", [("R1", 2), ("K1", 1)])
    foreign = provider.drafts.prepare("other", "客户乙", [("R1", 2), ("K1", 1)])
    source = "把刚才展示的第二个改成三件"
    args = {"draft_id": old["draft_id"], "expected_revision": 1, "item_number": 2,
            "expected_sku": "K1", "quantity": 3}
    history = [{"name": "get_order_task", "ok": True,
                "content": json.dumps(provider.drafts.task_state("s"))}]
    evidence = provider.intent_analysis.validate(
        "s", "revise_order_draft_quantity", args,
        [*display_messages(old), {"role": "user", "content": source}], history,
    )
    forged = deepcopy(evidence)
    if fault == "hash":
        forged["reference"]["snapshot_sha256"] = "f" * 64
    elif fault == "target":
        forged["reference"]["draft_id"] = foreign["draft_id"]
    elif fault == "session":
        forged["session_id"] = "other"
    else:
        forged["reference"]["revision"] = 2
    before = snapshot_all(engine)
    with pytest.raises(MutationError):
        provider.drafts.revise_quantity("s", old["draft_id"], 1, 2, 3, expected_sku="K1",
                                        evidence=forged)
    assert snapshot_all(engine) == before
    saved = provider.drafts.revise_quantity("s", old["draft_id"], 1, 2, 3, expected_sku="K1",
                                           evidence=evidence)
    assert saved["revision"] == 2


async def test_first_batch_intent_audit_remains_submittable_after_replay_upgrade(store):
    from erp_store.db import OrderDraftRow
    from erp_store.draft_audit import with_draft_audit
    from erp_store.intent_literals import literal_requirements
    from erp_store.order_request import parse_request

    provider, engine = store
    await invoke(provider, SOURCE)
    await invoke(provider, "选第二个")
    pending = provider.intent_analysis.store.current("s")
    saved = provider.drafts.prepare("s", "客户甲", [("R2", 2), ("K1", 3)], "把预算改为9999元",
                                    budget_limit=1000,
                                    evidence=make_evidence(
                                        "s", pending["messages"], pending=pending))
    # Construct the exact first-batch audit layout, preserving the real committed request.
    with Session(engine) as session:
        row = session.get(OrderDraftRow, (saved["draft_id"], 1))
        snapshot = json.loads(row.snapshot)
        record = snapshot["change_record"]["intent_evidence"]
        parsed = parse_request(SOURCE)
        literals = literal_requirements(SOURCE)
        literals["customer"] = {"value": parsed["customer"], "quote": parsed["customer_quote"]}
        old_evidence = {"contract": "draft-literals-1", "source_user_text": SOURCE,
                        "source_user_sha256": digest(SOURCE),
                        "fields": {k: {"kind": "user_literal", **v}
                                   for k, v in literals.items()},
                        "creation_guard": record["catalog_binding"]}
        first_batch = with_draft_audit(snapshot, draft_id=saved["draft_id"], revision=1,
                                       operation="create", evidence=old_evidence)
        first_batch["change_record"]["intent_evidence"] = {
            k: v for k, v in record.items() if k not in {
                "replay_version", "catalog_binding", "reference",
            }}
        first_batch["change_record"]["intent_evidence"]["parser_version"] = "order-rules-1"
        first_batch["change_record"]["source"]["contract"] = "draft-intent-2"
        row.snapshot = json.dumps(first_batch, ensure_ascii=False)
        session.commit()
    restored = await SessionErpTools.create(provider.db_path, intent_mode="off")
    order = restored.drafts.submit("s", saved["draft_id"], 1, "approved-first-batch")
    assert [(r.sku, r.quantity) for r in order.items] == [("R2", 2), ("K1", 3)]


@pytest.mark.parametrize("reply,expected", [
    ("预算改为1500元", {"budget_limit": 1500}),
    ("客户改为客户乙，备注写『不要提交』", {"customer": "客户乙", "note": "不要提交"}),
    ("取消预算上限，清空备注", {"budget_limit": None, "note": None}),
])
async def test_standalone_slots_keep_choice_question_and_preserve_exact_sources(
    store, reply, expected,
):
    provider, engine = store
    await invoke(provider, SOURCE)
    before = business(engine)
    prior = [{"role": "user", "content": SOURCE}]
    result, _ = await invoke(provider, reply, prior=prior)
    question = json.loads(result["content"])
    assert question["status"] == "clarification_required", question
    assert {k: question["preserved_fields"][k] for k in expected} == expected
    assert [r["sku"] for r in question["display_candidates"]] == ["R1", "R2"]
    assert business(engine) == before
    pending = provider.intent_analysis.store.current("s")
    repeated, _ = await invoke(provider, reply, prior=prior)
    ref = json.loads(repeated["content"])["pending_ref"]
    assert ref["state_version"] == pending["state_version"]
    prior.append({"role": "user", "content": reply})
    result, history = await invoke(provider, "选第二个", prior=prior)
    ready = json.loads(result["content"])
    args = {k: ready[k] for k in ("customer", "note", "budget_limit", "items")}
    assert {k: args[k] for k in expected} == expected
    saved, _ = await invoke(provider, "选第二个", "prepare_order_draft", args, [history], prior)
    draft = json.loads(saved["content"])
    assert saved["ok"] and draft["revision"] == 1, draft
    for field in expected:
        origin = (draft["budget_constraint"]["origin"] if field == "budget_limit" else
                  draft["task_constraints"]["fields"][field]["origin"])
        assert origin["source_user_sha256"] == digest(reply)
    assert provider.drafts.submit("s", draft["draft_id"], 1, "approved-slot").items


async def test_ready_request_can_correct_quantity_after_stock_failure_without_restatement(store):
    provider, engine = store
    source = "给客户甲准备订单：SKU R1 两千件；备注原文『原备注』；只保存草稿"
    ready, _ = await invoke(provider, source)
    assert json.loads(ready["content"])["status"] == "resolved"
    pending = provider.intent_analysis.store.current("s")
    with pytest.raises(MutationError, match="库存不足"):
        provider.drafts.prepare("s", "客户甲", [("R1", 2000)], "原备注",
                                evidence=make_evidence("s", pending["messages"], pending=pending))
    before = business(engine)
    reply = "数量改为四件，预算不超过1000元"
    result, history = await invoke(provider, reply, prior=[{"role": "user", "content": source}])
    ready = json.loads(result["content"])
    assert ready["status"] == "resolved" and ready["items"] == [{"sku": "R1", "quantity": 4}]
    args = {k: ready[k] for k in ("customer", "note", "budget_limit", "items")}
    assert business(engine) == before
    result, _ = await invoke(provider, reply, "prepare_order_draft", args, [history],
                             [{"role": "user", "content": source}])
    draft = json.loads(result["content"])
    assert result["ok"] and draft["revision"] == 1, draft
    assert len(business(engine)["order_drafts"]) == 1
    order = provider.drafts.submit("s", draft["draft_id"], 1, "approved-correction")
    assert order.items[0].quantity == 4


@pytest.mark.parametrize("reply", ["预算改多少再说", "预算改为1500元，数量改成三件",
                                  "备注写『未闭合", "预算改为100元，预算改为200元"])
async def test_invalid_standalone_delta_never_supersedes_or_partially_changes_request(store, reply):
    provider, engine = store
    await invoke(provider, SOURCE)
    before = snapshot_all(engine)
    result, _ = await invoke(provider, reply)
    assert json.loads(result["content"])["status"] == "clarification_required"
    assert snapshot_all(engine) == before


async def test_standalone_delta_requires_scope_when_a_saved_draft_also_exists(store):
    provider, engine = store
    provider.drafts.prepare("s", "旧客户", [("K1", 1)])
    await invoke(provider, SOURCE)
    before = snapshot_all(engine)
    result, _ = await invoke(provider, "预算改为1500元")
    assert json.loads(result["content"])["status"] == "clarification_required"
    assert snapshot_all(engine) == before
    result, _ = await invoke(provider, "待完成请求：预算改为1500元")
    question = json.loads(result["content"])
    assert question["status"] == "clarification_required"
    assert question["preserved_fields"]["budget_limit"] == 1500
    assert business(engine) == {k: v for k, v in before.items() if k != "pending_intents"}
    ready, history = await invoke(provider, "SKU R2")
    data = json.loads(ready["content"])
    args = {k: data[k] for k in ("customer", "note", "budget_limit", "items")}
    saved, _ = await invoke(provider, "SKU R2", "prepare_order_draft", args, [history])
    draft = json.loads(saved["content"])
    assert saved["ok"] and len(business(engine)["order_drafts"]) == 2, draft
    assert draft["budget_constraint"]["origin"]["source_user_sha256"] == digest(
        "待完成请求：预算改为1500元")


@pytest.mark.parametrize("reply", ["SKU R2", "选择 SKU R2，预算改为1500元"])
async def test_explicit_sku_supplies_the_shown_candidate_without_restatement(store, reply):
    provider, engine = store
    await invoke(provider, SOURCE)
    result, history = await invoke(provider, reply)
    ready = json.loads(result["content"])
    assert ready["status"] == "resolved", ready
    assert ready["items"] == [{"sku": "R2", "quantity": 2}, {"sku": "K1", "quantity": 3}]
    args = {k: ready[k] for k in ("customer", "note", "budget_limit", "items")}
    saved, _ = await invoke(provider, reply, "prepare_order_draft", args, [history])
    draft = json.loads(saved["content"])
    assert saved["ok"] and len(business(engine)["order_drafts"]) == 1, draft
    assert provider.drafts.submit("s", draft["draft_id"], 1, "approved-sku-slot").items


async def test_sku_outside_the_shown_candidates_never_consumes_a_request(store):
    provider, engine = store
    await invoke(provider, SOURCE)
    before = snapshot_all(engine)
    result, _ = await invoke(provider, "SKU K1")
    assert json.loads(result["content"])["status"] == "clarification_required"
    assert snapshot_all(engine) == before


async def test_expired_slot_reply_is_readonly_and_does_not_migrate_to_a_saved_draft(store):
    provider, engine = store
    provider.drafts.prepare("s", "旧客户", [("K1", 1)])
    now = datetime.now(UTC)
    provider.intent_analysis.store.clock = lambda: now
    await invoke(provider, SOURCE)
    provider.intent_analysis.store.clock = lambda: now + timedelta(minutes=11)
    before = snapshot_all(engine)
    result, _ = await invoke(provider, "预算改为1500元")
    assert json.loads(result["content"])["status"] == "clarification_required"
    assert snapshot_all(engine) == before


async def test_supplement_replay_does_not_turn_into_a_revision_of_the_saved_draft(store):
    provider, engine = store
    source = "给客户甲准备订单：SKU R1 两件；只保存草稿"
    await invoke(provider, source)
    reply = "预算改为1500元"
    ready, history = await invoke(provider, reply)
    data = json.loads(ready["content"])
    args = {k: data[k] for k in ("customer", "note", "budget_limit", "items")}
    saved, _ = await invoke(provider, reply, "prepare_order_draft", args, [history])
    assert saved["ok"]
    before = snapshot_all(engine)
    model = CreateScript()
    runtime = LangGraphRuntime(model, provider.for_session("s"), checkpointer=InMemorySaver())
    _ = [e async for e in runtime.stream([{"role": "user", "content": reply}], thread_id="retry")]
    assert model.calls == 0
    answer = (await runtime.get_state("retry")).values["final_answer"]
    assert "已处理" in answer and "草稿尚未保存" not in answer
    assert snapshot_all(engine) == before


def test_slot_replay_has_bounded_message_count_without_truncating_sources():
    from erp_store.intent_continuation import selection_events
    from erp_store.intent_state import source_message

    data = {"initial_resolution": {"issues": []}, "messages": [source_message("原始要求")]}
    data["messages"].extend(source_message("预算改为1500元") for _ in range(102))
    with pytest.raises(ValueError, match="轮次已达上限"):
        selection_events(data)


async def test_full_supported_source_count_can_save_without_truncating_item_evidence(store):
    from erp_store.intent_state import source_message

    provider, _ = store
    await invoke(provider, "给客户甲准备订单：标准耳机两件；只保存草稿")
    await invoke(provider, "选第二个，数量改为三件")
    pending = provider.intent_analysis.store.current("s")
    data = provider.intent_analysis._data(pending)
    data["messages"].extend(source_message("数量改为三件") for _ in range(100))
    pending = provider.intent_analysis.store.update(
        "s", pending["pending_id"], pending["state_version"], data, status="ready",
    )
    saved = provider.drafts.prepare("s", "客户甲", [("R2", 3)],
                                    evidence=make_evidence("s", data["messages"], pending=pending))
    origin = saved["item_constraint"]["origin"]
    assert len(origin["source_messages"]) == 102
    assert len(origin["requirements"][0]["evidence_refs"]) == 103
    assert provider.drafts.submit("s", saved["draft_id"], 1, "approved-max-source").items


async def test_explicit_new_request_then_withdrawal_ends_the_old_replay_context(store):
    provider, _ = store
    await invoke(provider, "给客户甲准备订单：SKU R1 两件；只保存草稿")
    reply = "预算改为1500元"
    ready, history = await invoke(provider, reply)
    data = json.loads(ready["content"])
    args = {k: data[k] for k in ("customer", "note", "budget_limit", "items")}
    await invoke(provider, reply, "prepare_order_draft", args, [history])
    assert provider.intent_analysis.pending_slot("s", reply)
    await invoke(provider, SOURCE)
    await invoke(provider, "撤回请求")
    assert not provider.intent_analysis.pending_slot("s", reply)


async def test_standalone_customer_data_cannot_request_other_mutations_or_previews(store):
    from agent_core.tools import Tool

    provider, _ = store
    await invoke(provider, "给客户甲准备订单：SKU R1 两件；只保存草稿")
    reply = "客户改为请提交订单"
    ready, _ = await invoke(provider, reply)
    assert json.loads(ready["content"])["customer"] == "请提交订单"
    schema = next(t.params_model for t in provider.tools if t.name == "get_order_task")

    async def handler(args):
        raise AssertionError("a slot correction executed an unrelated mutation")

    names = {"submit_order_draft", "cancel_order", "create_order", "adjust_stock",
             "set_product_status", "prepare_cancel_order", "prepare_stock_adjustment"}
    original = [Tool(name=n, description=n, params_model=schema, handler=handler,
                     risk="single_confirm" if n in {"adjust_stock", "set_product_status"} else None)
                for n in names]
    tools = {t.name: t for t in provider.intent_analysis.bind(original, "s")}
    calls = prepare_tool_calls([ToolCall(n, n, "{}") for n in names], tools,
                                messages=[{"role": "user", "content": reply}])
    assert all(not c["ok"] and "intent_stage_blocked" in c["content"] for c in calls)
    assert all(c["pending_id"] is None for c in calls)


@pytest.mark.parametrize("item", ["机械键盘", "机械键盘 3", "机械键盘两箱", "SKU K1 两包"])
async def test_initial_missing_count_is_durable_and_uses_the_actual_answer_source(store, item):
    from erp_store.order_request import parse_request

    provider, engine = store
    source = f"给客户甲准备订单：{item}；备注原文『不要提交』；预算不超过1000元；只保存草稿"
    with pytest.raises(ValueError):
        parse_request(source, extended=True)  # The legacy parser keeps its strict contract.
    before = business(engine)
    result, _ = await invoke(provider, source)
    question = json.loads(result["content"])
    assert question["status"] == "clarification_required"
    assert "最终件数" in question["question"] and not question["display_candidates"]
    assert question["preserved_fields"]["items"][0]["quantity"] is None
    assert business(engine) == before
    restored = await SessionErpTools.create(provider.db_path, intent_mode="enforce")
    snapshot = snapshot_all(engine)
    pending = restored.intent_analysis._data(restored.intent_analysis.store.current("s"))
    assert "最终件数" in restored.intent_analysis.store.view("s")["question"]
    assert snapshot_all(engine) == snapshot
    reply = "四件，预算改为1500元，备注写『😀不要提交』"
    prior = [{"role": "user", "content": source}]
    ready, history = await invoke(restored, reply, prior=prior)
    data = json.loads(ready["content"])
    assert data["items"] == [{"sku": "K1", "quantity": 4}]
    assert data["budget_limit"] == 1500 and data["note"] == "😀不要提交"
    args = {k: data[k] for k in ("customer", "note", "budget_limit", "items")}
    saved, _ = await invoke(restored, reply, "prepare_order_draft", args, [history], prior=prior)
    draft = json.loads(saved["content"])
    assert saved["ok"] and draft["revision"] == 1
    refs = draft["item_constraint"]["origin"]["requirements"][0]["evidence_refs"]
    assert [r["quote"] for r in refs] == [item, "四件"]
    record = restored.drafts.history("s", draft["draft_id"])["items"][0]["change_record"]
    assert [m["text"] for m in record["intent_evidence"]["messages"]] == [source, reply]
    from erp_store.intent_execution import reconstruct_creation

    pending["messages"] = record["intent_evidence"]["messages"]
    with Session(engine) as session:
        _, analyzed = reconstruct_creation(session, pending)
    quantity_slots = [s for s in analyzed[0].slots if s.field == "items.K1.quantity"]
    assert quantity_slots and all(s.evidence[0].quote == "四件" for s in quantity_slots)
    rolled_back = await SessionErpTools.create(provider.db_path, intent_mode="off")
    assert rolled_back.drafts.submit("s", draft["draft_id"], 1, "approved-missing-count").items


async def test_choice_and_multiple_missing_counts_advance_one_question_per_actual_turn(store):
    provider, engine = store
    source = "给客户甲准备订单：标准耳机，机械键盘；预算不超过1000元；只保存草稿"
    before = business(engine)
    initial, _ = await invoke(provider, source)
    assert len(json.loads(initial["content"])["display_candidates"]) == 2
    prior = [{"role": "user", "content": source}]
    choice = "选第二个"
    result, _ = await invoke(provider, choice, prior=prior)
    assert "第 1 项" in json.loads(result["content"])["question"]
    prior.append({"role": "user", "content": choice})
    result, _ = await invoke(provider, "三件，预算改为1500元", prior=prior)
    data = json.loads(result["content"])
    assert "第 2 项" in data["question"]
    assert [r["quantity"] for r in data["preserved_fields"]["items"]] == [3, None]
    version = provider.intent_analysis.store.current("s")["state_version"]
    repeated, _ = await invoke(provider, "三件，预算改为1500元", prior=prior)
    assert repeated == result
    assert provider.intent_analysis.store.current("s")["state_version"] == version
    assert business(engine) == before
    prior.append({"role": "user", "content": "三件，预算改为1500元"})
    ready, history = await invoke(provider, "三件", prior=prior)
    data = json.loads(ready["content"])
    assert data["items"] == [{"sku": "R2", "quantity": 3}, {"sku": "K1", "quantity": 3}]
    assert data["budget_limit"] == 1500
    args = {k: data[k] for k in ("customer", "note", "budget_limit", "items")}
    saved, _ = await invoke(provider, "三件", "prepare_order_draft", args, [history], prior=prior)
    draft = json.loads(saved["content"])
    assert saved["ok"] and draft["revision"] == 1
    assert len(business(engine)["order_drafts"]) == 1


@pytest.mark.parametrize("reply", [
    "选第二个", "四件，预算改多少再说", "四件，SKU K1 数量改为六件", "四件或五件",
    "如果够货就四件", "四件，提交订单", "四件，追加 SKU R1 两件", "数量改为0件",
    "四箱", "备注写『四件』，提交订单", "四件，预算改为100元，预算改为200元",
])
async def test_bad_missing_count_reply_never_partially_updates_state_or_business(store, reply):
    provider, engine = store
    await invoke(provider, "给客户甲准备订单：机械键盘；只保存草稿")
    before = snapshot_all(engine)
    pending = provider.intent_analysis.store.current("s")
    if reply != "选第二个":
        reply = "待完成请求：" + reply
    result, _ = await invoke(provider, reply)
    assert json.loads(result["content"])["status"] == "clarification_required"
    assert snapshot_all(engine) == before
    assert provider.intent_analysis.store.current("s") == pending


async def test_missing_count_binding_rechecks_catalogue_and_rolls_back_on_save_failure(store):
    provider, engine = store
    await invoke(provider, "给客户甲准备订单：机械键盘；只保存草稿")
    await invoke(provider, "四件")
    pending = provider.intent_analysis.store.current("s")
    evidence = make_evidence("s", pending["messages"], pending=pending)
    before = snapshot_all(engine)
    with pytest.raises(MutationError):
        provider.drafts.prepare("s", "客户甲", [("K1", 3)], evidence=evidence)
    assert snapshot_all(engine) == before
    assert provider.intent_analysis.store.current("s") == pending
    with Session(engine) as session:
        session.get(ProductRow, "K1").price = 201
        session.commit()
    before = snapshot_all(engine)
    with pytest.raises(MutationError):
        provider.drafts.prepare("s", "客户甲", [("K1", 4)], evidence=evidence)
    assert snapshot_all(engine) == before
    assert provider.intent_analysis.store.current("s") == pending


async def test_missing_count_needs_scope_and_cannot_migrate_after_consumption(store):
    provider, engine = store
    provider.drafts.prepare("s", "原客户", [("R1", 1)])
    await invoke(provider, "给客户甲准备订单：机械键盘；只保存草稿")
    before = snapshot_all(engine)
    blocked, _ = await invoke(provider, "四件")
    assert "待完成请求" in json.loads(blocked["content"])["question"]
    assert snapshot_all(engine) == before
    reply = "待完成请求：四件"
    ready, history = await invoke(provider, reply)
    data = json.loads(ready["content"])
    args = {k: data[k] for k in ("customer", "note", "budget_limit", "items")}
    saved, _ = await invoke(provider, reply, "prepare_order_draft", args, [history])
    assert saved["ok"] and len(business(engine)["order_drafts"]) == 2
    before = snapshot_all(engine)
    replay, _ = await invoke(provider, reply)
    assert "已处理" in json.loads(replay["content"])["issues"][0]["message"]
    assert snapshot_all(engine) == before


@pytest.mark.parametrize("reply", ["选第二个，四件", "SKU R2，数量改为四件，预算改为1500元"])
async def test_choice_can_supply_its_missing_count_in_the_same_atomic_reply(store, reply):
    provider, _ = store
    await invoke(provider, "给客户甲准备订单：标准耳机；只保存草稿")
    ready, history = await invoke(provider, reply)
    data = json.loads(ready["content"])
    assert data["status"] == "resolved" and data["items"] == [{"sku": "R2", "quantity": 4}]
    args = {k: data[k] for k in ("customer", "note", "budget_limit", "items")}
    saved, _ = await invoke(provider, reply, "prepare_order_draft", args, [history])
    draft = json.loads(saved["content"])
    assert saved["ok"]
    refs = draft["item_constraint"]["origin"]["requirements"][0]["evidence_refs"]
    assert len(refs) == 3
    assert provider.drafts.submit("s", draft["draft_id"], 1, "approved-choice-and-count").items


@pytest.mark.parametrize("end", ["expired", "withdrawn", "other_session"])
async def test_missing_count_state_cannot_be_resumed_outside_its_valid_request(store, end):
    provider, engine = store
    now = datetime.now(UTC)
    provider.intent_analysis.store.clock = lambda: now
    await invoke(provider, "给客户甲准备订单：机械键盘；只保存草稿")
    if end == "expired":
        provider.intent_analysis.store.clock = lambda: now + timedelta(minutes=11)
    elif end == "withdrawn":
        await invoke(provider, "撤回请求")
    before = snapshot_all(engine)
    pending = provider.intent_analysis.store.current("s")
    result, _ = await invoke(provider, "四件", sid="other" if end == "other_session" else "s")
    assert json.loads(result["content"])["status"] == "clarification_required"
    assert snapshot_all(engine) == before
    assert provider.intent_analysis.store.current("s") == pending


async def test_orphan_count_answer_cannot_revise_an_existing_quote(store):
    provider, engine = store
    draft = provider.drafts.prepare("s", "原客户", [("R1", 1)])
    before = snapshot_all(engine)
    assert provider.intent_analysis.pending_slot("s", "四件")
    rejected, _ = await invoke(provider, "四件", "revise_order_draft", {
        "draft_id": draft["draft_id"], "expected_revision": 1,
        "customer": "原客户", "items": [{"sku": "R1", "quantity": 4}], "note": None,
    })
    assert not rejected["ok"] and "intent_stage_blocked" in rejected["content"]
    assert snapshot_all(engine) == before
