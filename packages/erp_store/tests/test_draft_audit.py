import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from erp_store.db import OrderDraftRow, ProductRow, StockRow, init_db, make_engine
from erp_store.drafts import ErpDrafts
from erp_store.mutations import MutationError
from sqlalchemy import event, select
from sqlalchemy.orm import Session

CUSTOMER = "完整业务客户甲"
NOTE = "保留完整要求"
TEXT = f"给{CUSTOMER}保存报价草稿；备注原文「{NOTE}」"


def evidence(text=TEXT, *, customer=CUSTOMER, note=NOTE):
    return {
        "contract": "draft-literals-1", "source_user_text": text,
        "source_user_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "fields": {
            "customer": {"kind": "user_literal", "value": customer, "quote": text.split("；")[0]},
            "note": {"kind": "user_literal", "value": note, "quote": f"备注原文「{note}」"},
        },
    }


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "erp.db"
    engine = make_engine(path)
    init_db(engine)
    with Session(engine) as session:
        for sku in ("A", "B"):
            session.add(ProductRow(sku=sku, name=sku, category="测试", price=100, status="在售"))
            session.add(StockRow(sku=sku, quantity=1000, warehouse="主仓"))
        session.commit()
    yield path, engine, ErpDrafts(engine)
    engine.dispose()


def snapshots(engine):
    with Session(engine) as session:
        return [(row.revision, row.snapshot) for row in session.scalars(
            select(OrderDraftRow).order_by(OrderDraftRow.revision)
        )]


def latest_change(drafts, draft_id):
    return drafts.history("s1", draft_id, limit=1)["items"][0]["change_record"]


def test_origins_survive_new_engine_lost_chat_and_quantity_edits(store):
    path, engine, drafts = store
    first = drafts.prepare("s1", CUSTOMER, [("A", 2)], NOTE, evidence=evidence())
    raw = snapshots(engine)
    restored_engine = make_engine(path)
    try:
        restored = ErpDrafts(restored_engine)
        changed = restored.revise_quantity("s1", first["draft_id"], 1, 1, 5, expected_sku="A")
        assert changed["task_constraints"] == first["task_constraints"]
        assert changed["task_constraints"]["unverified_fields"] == ["items"]
        record = latest_change(restored, first["draft_id"])
        assert record["source"] == {"kind": "unattributed"}
        assert {"field": "quantity", "sku": "A", "before": 2, "after": 5} in (
            record["changes"]
        )
        assert raw == snapshots(engine)[:1]
        history = restored.history("s1", first["draft_id"])
        assert [item["revision"] for item in history["items"]] == [2, 1]
        assert history["items"][1]["change_record"]["source"]["user_request"] == TEXT
    finally:
        restored_engine.dispose()
    with Session(engine) as session:
        assert session.get(StockRow, "A").quantity == 1000


@pytest.mark.parametrize("field,new_value", [("customer", "缩写客户"), ("note", None)])
def test_saved_verified_requirements_cannot_be_dropped_without_new_evidence(
    store, field, new_value,
):
    _, engine, drafts = store
    first = drafts.prepare("s1", CUSTOMER, [("A", 2)], NOTE, evidence=evidence())
    args = {"customer": CUSTOMER, "note": NOTE}
    args[field] = new_value
    before = snapshots(engine)
    with pytest.raises(MutationError) as error:
        drafts.revise("s1", first["draft_id"], 1, args["customer"], [("A", 3)], args["note"])
    assert error.value.code == "constraint_changed" and snapshots(engine) == before


def test_explicit_new_requirement_replaces_only_changed_field_origin(store):
    _, _, drafts = store
    first = drafts.prepare("s1", CUSTOMER, [("A", 2)], NOTE, evidence=evidence())
    source = evidence("客户改为新客户；备注原文「保留完整要求」", customer="新客户")
    second = drafts.revise("s1", first["draft_id"], 1, "新客户", [("A", 3)], NOTE, evidence=source)
    fields = second["task_constraints"]["fields"]
    assert fields["customer"]["value"] == "新客户" and fields["customer"]["origin"]["revision"] == 2
    assert fields["note"] == first["task_constraints"]["fields"]["note"]
    assert {"field": "customer", "before": CUSTOMER, "after": "新客户"} in (
        latest_change(drafts, first["draft_id"])["changes"]
    )


def test_manual_form_has_explicit_source_and_can_change_or_clear_fields(store):
    _, _, drafts = store
    first = drafts.prepare("s1", CUSTOMER, [("A", 2)], NOTE, evidence=evidence())
    second = drafts.revise("s1", first["draft_id"], 1, "表单客户", [("A", 2)], None,
                           evidence={"contract": "draft-form-1"})
    assert latest_change(drafts, first["draft_id"])["source"] == {"kind": "manual_form"}
    for field in ("customer", "note"):
        assert second["task_constraints"]["fields"][field]["origin"]["kind"] == "manual_form"


@pytest.mark.parametrize("fault", [
    "digest", "quote", "value", "unknown_field", "observed_revision",
])
def test_invalid_evidence_cannot_commit_revision(store, fault):
    _, engine, drafts = store
    first = drafts.prepare("s1", CUSTOMER, [("A", 2)], NOTE, evidence=evidence())
    source = evidence()
    if fault == "digest":
        source["source_user_sha256"] = "0" * 64
    elif fault == "quote":
        source["fields"]["customer"]["quote"] = "未出现的原话"
    elif fault == "value":
        source["fields"]["customer"]["value"] = "其他客户"
    elif fault == "unknown_field":
        source["fields"]["items"] = {"kind": "user_literal", "value": []}
    else:
        source["fields"]["customer"] = {
            "kind": "observed_draft", "value": CUSTOMER,
            "draft_id": first["draft_id"], "revision": 99,
        }
    before = snapshots(engine)
    with pytest.raises(MutationError) as error:
        drafts.revise("s1", first["draft_id"], 1, CUSTOMER, [("A", 3)], NOTE, evidence=source)
    assert error.value.code == "constraint_evidence_invalid" and snapshots(engine) == before


def test_revision_and_origins_roll_back_together_after_insert_failure(store):
    _, engine, drafts = store
    first = drafts.prepare("s1", CUSTOMER, [("A", 2)], NOTE, evidence=evidence())
    before = snapshots(engine)

    def fail_after_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO order_drafts"):
            raise RuntimeError("injected failure after database insert")

    event.listen(engine, "after_cursor_execute", fail_after_insert)
    try:
        with pytest.raises(RuntimeError, match="injected failure"):
            drafts.revise_quantity("s1", first["draft_id"], 1, 1, 5)
    finally:
        event.remove(engine, "after_cursor_execute", fail_after_insert)
    assert snapshots(engine) == before
    assert len(drafts.history("s1", first["draft_id"])["items"]) == 1


def test_legacy_history_is_unknown_and_quantity_edit_does_not_invent_user_source(store):
    _, engine, drafts = store
    first = drafts.prepare("s1", CUSTOMER, [("A", 2)], NOTE)
    with Session(engine) as session:
        row = session.get(OrderDraftRow, (first["draft_id"], 1))
        payload = json.loads(row.snapshot)
        payload.pop("task_constraints")
        payload.pop("change_record")
        row.snapshot = json.dumps(payload)
        session.commit()
    before = snapshots(engine)
    history = drafts.history("s1", first["draft_id"])
    assert snapshots(engine) == before
    assert history["items"][0]["change_record"] is None
    assert history["items"][0]["task_constraints"]["fields"]["customer"]["origin"]["kind"] == (
        "legacy_snapshot"
    )
    changed = drafts.revise_quantity("s1", first["draft_id"], 1, 1, 5)
    assert not changed["task_constraints"]["fields"]["customer"]["verified"]


def test_history_cursor_is_read_only_and_session_scoped(store):
    _, engine, drafts = store
    first = drafts.prepare("s1", CUSTOMER, [("A", 2)], NOTE)
    for revision in range(1, 7):
        drafts.revise_quantity("s1", first["draft_id"], revision, 1, revision + 2)
    before = snapshots(engine)
    cursor, seen = None, []
    while True:
        page = drafts.history("s1", first["draft_id"], limit=2, before_revision=cursor)
        seen.extend(item["revision"] for item in page["items"])
        if not page["has_more"]:
            break
        cursor = page["next_before_revision"]
    assert seen == list(range(7, 0, -1)) and snapshots(engine) == before
    for draft_id in (first["draft_id"], "missing"):
        with pytest.raises(MutationError) as error:
            drafts.history("s2", draft_id)
        assert error.value.code == "not_found"


@pytest.mark.parametrize("options", [
    {"limit": 0}, {"limit": 21}, {"limit": True}, {"before_revision": True},
    {"before_revision": 0}, {"before_revision": 1.2},
])
def test_invalid_history_paging_is_rejected(store, options):
    _, _, drafts = store
    with pytest.raises(MutationError) as error:
        drafts.history("s1", "any", **options)
    assert error.value.code == "invalid_argument"


def test_concurrent_revision_has_one_matching_audit_and_keeps_origin(store):
    _, engine, drafts = store
    first = drafts.prepare("s1", CUSTOMER, [("A", 2)], NOTE, evidence=evidence())

    def change(quantity):
        try:
            return drafts.revise_quantity("s1", first["draft_id"], 1, 1, quantity)
        except MutationError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(change, [5, 9]))
    changed = next(result for result in results if isinstance(result, dict))
    assert "draft_changed" in results and len(snapshots(engine)) == 2
    assert changed["task_constraints"] == first["task_constraints"]
    assert {"field": "quantity", "sku": "A", "before": 2,
            "after": changed["items"][0]["quantity"]} in (
                latest_change(drafts, first["draft_id"])["changes"]
            )


def test_quote_refresh_and_reorder_are_computed_from_actual_snapshots(store):
    _, engine, drafts = store
    first = drafts.prepare("s1", CUSTOMER, [("A", 2), ("B", 3)], NOTE)
    with Session(engine) as session:
        session.get(ProductRow, "A").price = 120
        session.commit()
    second = drafts.revise("s1", first["draft_id"], 1, CUSTOMER, [("B", 3), ("A", 2)], NOTE)
    assert second["last_change"]["change_count"] > 0
    changes = latest_change(drafts, first["draft_id"])["changes"]
    assert {"field": "base_price", "sku": "A", "before": 100, "after": 120} in changes
    assert {"field": "item_order", "before": ["A", "B"], "after": ["B", "A"]} in changes
    assert not any(change["field"] == "quantity" for change in changes)
