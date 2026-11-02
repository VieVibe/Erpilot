import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx2
import pytest
from erp_store.db import ProductRow, StockRow, init_db, make_engine
from erp_store.intent_state import IntentClarificationRow, PendingIntentRow
from erp_store.mutations import MutationError
from erpilot_api.main import create_app
from evals.context_workflow import snapshot_all
from langgraph.checkpoint.memory import InMemorySaver
from mcp_erp.session_tools import SessionErpTools
from sqlalchemy import select
from sqlalchemy.orm import Session

SOURCE = ("给客户甲准备订单：标准耳机，机械键盘两件；备注原文『不要提交』；"
          "预算不超过1000元；只保存草稿")
BASE = "/api/sessions/s/pending-intent"


def whole_database(engine):
    with engine.connect() as connection:
        metadata = {t.name: [tuple(r) for r in connection.execute(
                        select(t).order_by(*t.primary_key))]
                    for t in (PendingIntentRow.__table__, IntentClarificationRow.__table__)}
    return {**snapshot_all(engine), **metadata}


@pytest.fixture
async def host(tmp_path):
    path = tmp_path / "preview.db"
    engine = make_engine(path)
    init_db(engine)
    with Session(engine) as session:
        for sku, name, price in [("R1", "标准耳机", 50), ("R2", "标准耳机", 80),
                                 ("K1", "机械键盘", 200)]:
            session.add(ProductRow(sku=sku, name=name, price=price, status="在售", category="测试"))
            session.add(StockRow(sku=sku, quantity=1000, warehouse="主仓"))
        session.commit()
    provider = await SessionErpTools.create(path, intent_mode="enforce")
    calls = []

    def client_factory():
        calls.append("unexpected")
        raise AssertionError("Preview reads and explicit confirmation must not initialize a model")

    app = create_app(client_factory=client_factory, session_erp_tools=provider,
                     trace_dir=tmp_path / "traces", checkpointer=InMemorySaver())
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app),
                                  base_url="http://test") as client:
        response = await client.post("/api/chat/stream",
                                     json={"session_id": "s", "message": SOURCE})
        assert "event: done" in response.text and calls == []
        yield client, provider, engine, calls, app
    engine.dispose()


def form(provider):
    pending = provider.intent_analysis.store.current("s")
    return {"pending_ref": {"pending_id": pending["pending_id"],
                            "state_version": pending["state_version"]},
            "customer": "核对后的完整客户", "note": "预算改为9999元，只是备注",
            "budget_limit": 1500, "items": [{"sku": "K1", "quantity": 3},
                                            {"sku": "R2", "quantity": 4}]}


async def test_full_preview_restore_and_selected_but_missing_count_are_read_only(host):
    client, provider, engine, calls, _ = host
    provider.intent_analysis.resolve("s", "选第二个", turn_id="choice")
    before = whole_database(engine)
    response = await client.get(BASE + "/preview")
    preview = response.json()["preview"]
    assert preview["can_confirm"] and preview["status"] == "pending"
    assert preview["customer"] == "客户甲" and preview["note"] == "不要提交"
    assert preview["budget_limit"] == 1000
    assert [(r["sku"], r["quantity"]) for r in preview["items"]] == [("R2", None), ("K1", 2)]
    assert [r["sku"] for r in preview["items"][0]["candidates"]] == ["R1", "R2"]
    assert (await client.get(BASE.replace("/s/", "/other/") + "/preview")).json() == {
        "preview": None,
    }
    restored = await SessionErpTools.create(provider.db_path, intent_mode="enforce")
    from erp_store.intent_preview import request_preview

    assert request_preview(restored.intent_analysis.store, "s") == preview
    assert whole_database(engine) == before and calls == []


async def test_complete_edited_form_saves_once_with_manual_origins_and_raw_context(host):
    client, provider, engine, calls, _ = host
    body = form(provider)
    before = snapshot_all(engine)
    response = await client.post(BASE + "/confirm", json=body)
    assert response.status_code == 201
    draft = response.json()
    assert draft["customer"] == body["customer"] and draft["note"] == body["note"]
    assert draft["budget_limit"] == body["budget_limit"]
    assert [{k: r[k] for k in ("sku", "quantity")} for r in draft["items"]] == body["items"]
    assert draft["revision"] == 1 and draft["item_constraint"]["origin"]["kind"] == "manual_form"
    assert draft["task_constraints"]["unverified_fields"] == []
    record = provider.drafts.history("s", draft["draft_id"])["items"][0]["change_record"]
    assert record["source"]["kind"] == "manual_form"
    context = record["source"]["intent_preview"]
    assert context["pending_id"] == body["pending_ref"]["pending_id"]
    assert [m["text"] for m in context["messages"]] == [SOURCE]
    assert "intent_evidence" not in record  # A form is not a claim of literal verification.
    assert snapshot_all(engine)["orders"] == before["orders"]
    assert snapshot_all(engine)["stocks"] == before["stocks"] and calls == []
    assert (await client.get(BASE + "/preview")).json() == {"preview": None}
    committed = whole_database(engine)
    replay = await client.post(BASE + "/confirm", json=body)
    assert replay.status_code == 409 and whole_database(engine) == committed
    off = await SessionErpTools.create(provider.db_path, intent_mode="off")
    assert off.drafts.submit("s", draft["draft_id"], 1, "approved-form").items


@pytest.mark.parametrize("fault", ["session", "version", "expired", "catalogue"])
async def test_old_foreign_expired_or_changed_preview_cannot_save(host, fault):
    client, provider, engine, calls, _ = host
    body, base = form(provider), BASE
    if fault == "session":
        base = BASE.replace("/s/", "/other/")
    elif fault == "version":
        provider.intent_analysis.resolve("s", "预算改为1600元", turn_id="new-budget")
    elif fault == "expired":
        future = datetime.now(UTC) + timedelta(minutes=11)
        provider.intent_analysis.store.clock = lambda: future
        provider.drafts._clock = lambda: future
    else:
        with Session(engine) as session:
            session.get(ProductRow, "R2").price = 81
            session.commit()
    before = whole_database(engine)
    response = await client.post(base + "/confirm", json=body)
    assert response.status_code == 409 and whole_database(engine) == before and calls == []
    if fault in {"expired", "catalogue"}:
        preview = (await client.get(BASE + "/preview")).json()["preview"]
        assert not preview["can_confirm"]
        assert whole_database(engine) == before


@pytest.mark.parametrize("fault", ["missing_note", "missing_budget", "boolean", "duplicate",
                                  "extra",
                                  "extra_item", "extra_ref", "zero_version"])
async def test_form_requires_explicit_complete_fields_and_strict_items(host, fault):
    client, provider, engine, _, _ = host
    body = form(provider)
    if fault == "missing_note":
        del body["note"]
    elif fault == "missing_budget":
        del body["budget_limit"]
    elif fault == "boolean":
        body["items"][0]["quantity"] = True
    elif fault == "duplicate":
        body["items"].append(dict(body["items"][0]))
    elif fault == "extra":
        body["submit"] = True
    elif fault == "extra_item":
        body["items"][0]["price"] = 1
    elif fault == "extra_ref":
        body["pending_ref"]["verified"] = True
    else:
        body["pending_ref"]["state_version"] = 0
    before = whole_database(engine)
    response = await client.post(BASE + "/confirm", json=body)
    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_argument"
    assert whole_database(engine) == before


async def test_failed_snapshot_then_retry_consumes_only_on_success(host):
    client, provider, engine, _, _ = host
    body = form(provider)
    body["items"][0]["quantity"] = 2000
    before = whole_database(engine)
    assert (await client.post(BASE + "/confirm", json=body)).status_code == 422
    assert whole_database(engine) == before
    body["items"][0]["quantity"] = 4
    body["budget_limit"], body["note"] = None, None
    response = await client.post(BASE + "/confirm", json=body)
    assert response.status_code == 201
    assert response.json()["budget_limit"] is None and response.json()["note"] is None


async def test_database_failure_after_consumption_rolls_back_form_and_draft(host, monkeypatch):
    _, provider, engine, _, _ = host
    body = form(provider)
    before = whole_database(engine)

    def fail(*args, **kwargs):
        raise RuntimeError("injected after insert")

    monkeypatch.setattr(provider.drafts, "_payload", fail)
    with pytest.raises(RuntimeError, match="injected"):
        provider.drafts.prepare(
            "s", body["customer"], [(r["sku"], r["quantity"]) for r in body["items"]],
            body["note"], budget_limit=body["budget_limit"],
            evidence={"contract": "intent-form-1", "pending_ref": body["pending_ref"]})
    assert whole_database(engine) == before


async def test_concurrent_form_saves_consume_one_request_at_most_once(host):
    _, provider, engine, _, _ = host
    body = form(provider)

    def save(_):
        try:
            return provider.drafts.prepare(
                "s", body["customer"], [(r["sku"], r["quantity"]) for r in body["items"]],
                body["note"], budget_limit=body["budget_limit"],
                evidence={"contract": "intent-form-1", "pending_ref": body["pending_ref"]})
        except MutationError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, range(2)))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert "pending_intent_changed" in results
    assert len(snapshot_all(engine)["order_drafts"]) == 1


async def test_active_or_unfinished_session_blocks_confirmation(host, monkeypatch):
    client, provider, engine, _, app = host
    before = whole_database(engine)
    lock = app.state.service.lock("s")
    await lock.acquire()
    try:
        response = await client.post(BASE + "/confirm", json=form(provider))
        assert response.status_code == 409 and response.json()["error"]["code"] == "session_busy"
    finally:
        lock.release()

    async def state(_):
        await asyncio.sleep(0)
        return SimpleNamespace(next=("pending_execution",))

    monkeypatch.setattr(app.state.service, "_runtime", lambda _: SimpleNamespace(get_state=state))
    response = await client.post(BASE + "/confirm", json=form(provider))
    assert response.status_code == 409 and response.json()["error"]["code"] == "unfinished_run"
    assert whole_database(engine) == before


async def test_failed_turns_offer_preview_without_changing_or_replaying_accepted_request(host):
    client, provider, engine, calls, _ = host
    accepted = provider.intent_analysis.store.current("s")
    business = snapshot_all(engine)
    for reply in ["选第九个", "选第八个"]:
        response = await client.post("/api/chat/stream", json={"session_id": "s", "message": reply})
        assert "event: done" in response.text and calls == []
    preview = (await client.get(BASE + "/preview")).json()["preview"]
    assert preview["clarification_attempts"] == 2
    assert provider.intent_analysis.store.current("s") == accepted
    assert snapshot_all(engine) == business
    from erp_store.intent_contract import digest

    turn = digest(json.dumps([SOURCE, "选第九个", "选第八个"], ensure_ascii=False))
    provider.intent_analysis.resolve("s", "选第八个", turn_id=turn)
    assert provider.intent_analysis.store.clarification_attempts(accepted) == 2
    before = whole_database(engine)
    await client.get(BASE + "/preview")
    assert whole_database(engine) == before


@pytest.mark.parametrize("mode", ["off", "shadow"])
async def test_rollback_modes_expose_only_readonly_recheck_and_cannot_confirm(host, mode):
    client, provider, engine, _, _ = host
    old_form = form(provider)
    # Rollback explicitly suspends the old pending request; GET remains read-only.
    fallback = await SessionErpTools.create(provider.db_path, intent_mode=mode)
    app = create_app(session_erp_tools=fallback, trace_dir=provider.db_path.parent / mode,
                     checkpointer=InMemorySaver())
    before = whole_database(engine)
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app),
                                  base_url="http://test") as http:
        preview = (await http.get(BASE + "/preview")).json()["preview"]
        assert preview["status"] == "needs_recheck" and not preview["can_confirm"]
        assert (await http.post(BASE + "/confirm", json=old_form)).status_code == 503
        assert not (await http.get("/api/runtime")).json().get("intent_preview_enabled")
    assert whole_database(engine) == before
