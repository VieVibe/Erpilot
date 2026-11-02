import asyncio
import json
from copy import deepcopy
from uuid import uuid4

import httpx2
import pytest
from agent_core.llm import StreamEnd, TextDelta, Usage
from erp_store.db import ProductRow, StockRow
from erp_store.intent_preview import request_preview
from evals.context_workflow import snapshot_all
from sqlalchemy.orm import Session
from test_intent_semantic_api import Model, host, journal, span

SOURCE = "调整机械键盘到六个，预算改为1200元，备注改为『加急😀』。"


class RevisionModel(Model):
    async def stream_chat(self, messages, tools=None):
        self.calls.append(deepcopy(messages))
        message = json.loads(messages[-1]["content"])

        def field(quote):
            return {"value": quote, "evidence": span(message, quote)}

        proposal = {
            "schema_version": 1,
            "operation": "revise",
            "action": span(message, "调整"),
            "customer": None,
            "note": field("加急😀"),
            "budget_limit": {"value": "1200", "evidence": span(message, "1200元")},
            "items": [
                {
                    "product": field("机械键盘"),
                    "quantity": {"value": 6, "evidence": span(message, "六个")},
                }
            ],
            "unresolved": [],
        }
        if self.behavior == "quoted_product":
            proposal["items"][0]["product"] = field("加急😀")
            proposal["note"] = None
        elif self.behavior == "invented":
            proposal["items"][0]["quantity"]["value"] = 60
        elif self.behavior == "missing_quantity":
            proposal["items"][0]["quantity"] = None
        elif self.behavior == "extra":
            proposal["target"] = {"draft_id": "forged"}
        yield TextDelta(json.dumps(proposal, ensure_ascii=False))
        yield StreamEnd(None if self.behavior == "missing_usage" else Usage(11, 3, 14))


async def setup(tmp_path, behavior="valid"):
    app, provider, engine, model, factories = await host(tmp_path, RevisionModel(behavior))
    with Session(engine) as session:
        session.add(ProductRow(sku="M1", name="鼠标", category="测试", price=20, status="在售"))
        session.add(StockRow(sku="M1", quantity=1000, warehouse="主仓"))
        session.commit()
    draft = provider.drafts.prepare(
        "s",
        "原客户",
        [("K1", 2), ("M1", 3)],
        "原备注",
        evidence={"contract": "draft-form-1"},
        budget_limit=1000,
    )
    request = {
        "source": SOURCE,
        "target": {k: draft[k] for k in ("draft_id", "revision")},
        "request_id": uuid4().hex,
    }
    return app, provider, engine, model, request


def fields(preview):
    return {
        "pending_ref": {k: preview[k] for k in ("pending_id", "state_version")},
        "target": preview["target"],
        "customer": preview["customer"],
        "note": None,
        "budget_limit": None,
        "items": [{"sku": row["sku"], "quantity": row["quantity"]} for row in preview["items"]],
    }


async def test_revision_preview_preserves_unmentioned_fields_and_confirms_once(tmp_path):
    app, p, engine, model, req = await setup(tmp_path)
    try:
        before = snapshot_all(engine)
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app), base_url="http://test"
        ) as c:
            response = await c.post("/api/sessions/s/pending-intent/revise-preview", json=req)
            assert response.status_code == 200, response.text
            preview = response.json()["preview"]
            assert snapshot_all(engine) == before and len(model.calls) == 1
            assert preview["operation"] == "revise" and preview["target"] == req["target"]
            assert preview["items"][0]["item_number"] == 1
            assert preview["items"][0]["name"] == "机械键盘"
            assert preview["items"][0]["candidates"] == []
            assert preview["customer"] == "原客户" and preview["note"] == "加急😀"
            assert preview["budget_limit"] == 1200
            assert [(r["sku"], r["quantity"]) for r in preview["items"]] == [("K1", 6), ("M1", 3)]
            assert preview["before_fields"]["items"][0]["quantity"] == 2
            assert (await c.get("/api/sessions/s/pending-intent/preview")).json()[
                "preview"
            ] == preview
            # A second preview cannot supersede an active request or spend again.
            assert (
                await c.post("/api/sessions/s/pending-intent/revise-preview", json=req)
            ).status_code == 409
            body = fields(preview)
            wrong = {**body, "target": {**body["target"], "revision": 2}}
            assert (
                await c.post("/api/sessions/s/pending-intent/confirm", json=wrong)
            ).status_code == 409
            outcomes = await asyncio.gather(
                *[c.post("/api/sessions/s/pending-intent/confirm", json=body) for _ in range(2)]
            )
            assert sorted(r.status_code for r in outcomes) == [201, 409]
            saved = next(r.json() for r in outcomes if r.status_code == 201)
            assert saved["draft_id"] == req["target"]["draft_id"] and saved["revision"] == 2
            assert saved["note"] is None and saved["budget_limit"] is None
        history = p.drafts.history("s", saved["draft_id"])["items"][0]["change_record"]
        assert history["source"]["kind"] == "manual_form"
        assert history["source"]["intent_preview"]["target"]["revision"] == 1
        assert (
            history["source"]["intent_preview"]["semantic_proposal"]["items"][0]["quantity"][
                "value"
            ]
            == 6
        )
        after = snapshot_all(engine)
        assert after["stocks"] == before["stocks"] and after["orders"] == []
        assert len(model.calls) == 1 and len(journal(engine)) == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "fault", ["target", "catalogue", "source", "expired", "session", "target_expired"]
)
async def test_changed_previews_cannot_be_consumed(tmp_path, fault):
    app, p, engine, model, req = await setup(tmp_path)
    try:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app), base_url="http://test"
        ) as c:
            preview = (
                await c.post("/api/sessions/s/pending-intent/revise-preview", json=req)
            ).json()["preview"]
            if fault == "target":
                p.drafts.revise(
                    "s",
                    req["target"]["draft_id"],
                    1,
                    "变更客户",
                    [("K1", 1)],
                    evidence={"contract": "draft-form-1"},
                )
            elif fault == "target_expired":
                from erp_store.db import OrderDraftRow

                with Session(engine) as session:
                    session.get(
                        OrderDraftRow, (req["target"]["draft_id"], 1)
                    ).expires_at = "2000-01-01T00:00:00+00:00"
                    session.commit()
            elif fault == "catalogue":
                with Session(engine) as session:
                    session.get(ProductRow, "M1").price = 30
                    session.commit()
            elif fault in {"source", "expired"}:
                from erp_store.intent_state import PendingIntentRow

                with Session(engine) as session:
                    row = session.get(PendingIntentRow, preview["pending_id"])
                    if fault == "expired":
                        row.expires_at = "2000-01-01T00:00:00+00:00"
                    else:
                        data = json.loads(row.payload)
                        data["messages"][0]["text"] = "篡改"
                        row.payload = json.dumps(data)
                    session.commit()
            before = snapshot_all(engine)
            sid = "foreign" if fault == "session" else "s"
            response = await c.post(
                f"/api/sessions/{sid}/pending-intent/confirm", json=fields(preview)
            )
            assert response.status_code == 409, response.text
            assert snapshot_all(engine) == before
            if fault != "session":
                updated = request_preview(p.intent_analysis.store, "s")
                assert not updated["can_confirm"]
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "behavior", ["quoted_product", "invented", "missing_quantity", "extra", "missing_usage"]
)
async def test_invalid_model_changes_are_rejected_without_mutation(tmp_path, behavior):
    app, p, engine, model, req = await setup(tmp_path, behavior)
    try:
        before = snapshot_all(engine)
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app), base_url="http://test"
        ) as c:
            response = await c.post("/api/sessions/s/pending-intent/revise-preview", json=req)
            assert response.status_code == 422
            if behavior == "missing_usage":
                req["request_id"] = uuid4().hex
                assert (
                    await c.post("/api/sessions/s/pending-intent/revise-preview", json=req)
                ).status_code == 422
                assert p.intent_analysis.semantic.budget.unknown_usage
        assert snapshot_all(engine) == before
        assert p.intent_analysis.store.current("s") is None and len(model.calls) == 1
    finally:
        engine.dispose()


async def test_confirmation_failure_rolls_back_revision_and_request_consumption(
    tmp_path, monkeypatch
):
    from erp_store.mutations import MutationError

    app, p, engine, model, req = await setup(tmp_path)
    try:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app), base_url="http://test"
        ) as c:
            preview = (
                await c.post("/api/sessions/s/pending-intent/revise-preview", json=req)
            ).json()["preview"]
            before = snapshot_all(engine)
            original = p.drafts._payload

            def fail(*args, **kwargs):
                raise MutationError("fixture_failure", "事务故障注入")

            monkeypatch.setattr(p.drafts, "_payload", fail)
            assert (
                await c.post("/api/sessions/s/pending-intent/confirm", json=fields(preview))
            ).status_code == 422
            assert snapshot_all(engine) == before
            pending = p.intent_analysis.store.current("s")
            assert pending["state_version"] == 1 and pending["status"] == "pending"
            monkeypatch.setattr(p.drafts, "_payload", original)
            saved = await c.post("/api/sessions/s/pending-intent/confirm", json=fields(preview))
            assert saved.status_code == 201 and saved.json()["revision"] == 2
    finally:
        engine.dispose()


async def test_preview_restores_without_reinitializing_model(tmp_path):
    app, p, engine, model, req = await setup(tmp_path)
    try:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app), base_url="http://test"
        ) as c:
            preview = (
                await c.post("/api/sessions/s/pending-intent/revise-preview", json=req)
            ).json()["preview"]
        restored, p2, e2, m2, factories = await host(tmp_path, existing=True)
        try:
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(restored), base_url="http://test"
            ) as c:
                assert (await c.get("/api/sessions/s/pending-intent/preview")).json()[
                    "preview"
                ] == preview
                saved = await c.post("/api/sessions/s/pending-intent/confirm", json=fields(preview))
                assert saved.status_code == 201 and saved.json()["revision"] == 2
            assert factories == [] and m2.calls == []
        finally:
            e2.dispose()
    finally:
        engine.dispose()


async def test_model_response_cannot_replace_a_changed_target(tmp_path):
    app, p, engine, model, req = await setup(tmp_path)
    stream = model.stream_chat

    async def change_target(messages, tools=None):
        p.drafts.revise(
            "s",
            req["target"]["draft_id"],
            1,
            "新客户",
            [("K1", 1)],
            evidence={"contract": "draft-form-1"},
        )
        async for event in stream(messages, tools=tools):
            yield event

    model.stream_chat = change_target
    try:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app), base_url="http://test"
        ) as c:
            result = await c.post("/api/sessions/s/pending-intent/revise-preview", json=req)
            assert result.status_code == 422
        assert p.intent_analysis.store.current("s") is None
        assert p.drafts.get("s", req["target"]["draft_id"])["customer"] == "新客户"
        assert len(model.calls) == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "fault",
    [
        "conditional",
        "submit",
        "foreign",
        "version",
        "negated",
        "negated_short",
        "question",
        "readonly",
    ],
)
async def test_preflight_rejects_without_model_request(tmp_path, fault):
    app, p, engine, model, req = await setup(tmp_path)
    try:
        if fault == "conditional":
            req["source"] = "如果客户确认，" + SOURCE
        elif fault == "negated":
            req["source"] = "不调整机械键盘，预算改为1200元。"
        elif fault == "negated_short":
            req["source"] = "别改机械键盘，预算改为1200元。"
        elif fault == "question":
            req["source"] = "能不能调整机械键盘到六个？"
        elif fault == "readonly":
            req["source"] = "只看调整机械键盘到六个后的报价。"
        elif fault == "submit":
            req["source"] = SOURCE + "提交订单"
        elif fault == "foreign":
            req["target"]["draft_id"] = uuid4().hex
        else:
            req["target"]["revision"] = 2
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app), base_url="http://test"
        ) as c:
            response = await c.post("/api/sessions/s/pending-intent/revise-preview", json=req)
            assert response.status_code in {409, 422}
        assert model.calls == []
    finally:
        engine.dispose()
