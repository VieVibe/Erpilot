import hashlib
import json
from copy import deepcopy

import pytest
from erp_store.db import OrderDraftRow, ProductRow, StockRow, init_db, make_engine
from erp_store.drafts import ErpDrafts
from erp_store.mutations import MutationError
from sqlalchemy import select
from sqlalchemy.orm import Session

TEXT = "把当前版本第一行改为5件，其他不变"


@pytest.fixture
def store(tmp_path):
    engine = make_engine(tmp_path / "item-guard.db")
    init_db(engine)
    with Session(engine) as session:
        for sku in ("A", "B", "C"):
            session.add(ProductRow(sku=sku, name=sku, category="测试", price=100, status="在售"))
            session.add(StockRow(sku=sku, quantity=1000, warehouse="主仓"))
        session.commit()
    drafts = ErpDrafts(engine)
    draft = drafts.prepare("s", "完整客户", [("A", 2), ("B", 3)], "保留备注",
                           evidence={"contract": "draft-form-1"})
    yield engine, drafts, draft
    engine.dispose()


def evidence(draft, items=(("A", 5), ("B", 3)), *, text=TEXT, requirements=None):
    return {
        "contract": "draft-literals-1", "source_user_text": text,
        "source_user_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "fields": {},
        "item_guard": {
            "contract": "draft-items-1", "draft_id": draft["draft_id"],
            "revision": draft["revision"],
            "before": [{"sku": line["sku"], "quantity": line["quantity"]}
                       for line in draft["items"]],
            "after": [{"sku": sku, "quantity": quantity} for sku, quantity in items],
            "requirements": ([{"sku": "A", "quantity": 5, "quote": TEXT}]
                             if requirements is None else requirements),
        },
    }


def raw(engine):
    with Session(engine) as session:
        return {
            "drafts": [(row.draft_id, row.revision, row.snapshot)
                       for row in session.scalars(select(OrderDraftRow).order_by(
                           OrderDraftRow.draft_id, OrderDraftRow.revision))],
            "stock": [(row.sku, row.quantity)
                      for row in session.scalars(select(StockRow).order_by(StockRow.sku))],
        }


def test_guard_persists_with_actual_quantity_change_and_restored_history(store):
    engine, drafts, first = store
    before = raw(engine)
    source = evidence(first)
    expected = deepcopy(source["item_guard"])
    second = drafts.revise_quantity("s", first["draft_id"], 1, 1, 5, expected_sku="A",
                                    evidence=source)
    source["item_guard"]["after"][0]["quantity"] = 99
    restored = ErpDrafts(engine)
    record = restored.history("s", first["draft_id"])["items"][0]["change_record"]
    assert record["item_guard"] == expected
    assert record["source"]["user_request"] == TEXT
    assert {"field": "quantity", "sku": "A", "before": 2, "after": 5} in record["changes"]
    assert second["task_constraints"]["fields"] == first["task_constraints"]["fields"]
    assert second["item_constraint"]["verified"]
    assert second["item_constraint"]["value"] == expected["after"]
    assert raw(engine)["drafts"][:1] == before["drafts"]
    assert raw(engine)["stock"] == before["stock"]


def test_refresh_guard_allows_price_change_and_preserves_form_item_verification(store):
    engine, drafts, first = store
    with Session(engine) as session:
        session.get(ProductRow, "A").price = 120
        session.commit()
    source = evidence(first, (("A", 2), ("B", 3)), text="刷新报价，其他不变", requirements=[])
    second = drafts.revise("s", first["draft_id"], 1, "完整客户", [("A", 2), ("B", 3)],
                           "保留备注", evidence=source)
    record = drafts.history("s", first["draft_id"])["items"][0]["change_record"]
    assert record["item_guard"]["requirements"] == []
    assert record["item_guard"]["before"] == record["item_guard"]["after"]
    assert {"field": "base_price", "sku": "A", "before": 100, "after": 120} in record["changes"]
    assert second["items"][0]["unit_price"] == 120
    assert second["item_constraint"] == first["item_constraint"]


@pytest.mark.parametrize("fault", [
    "contract", "draft_id", "revision", "bool_revision", "before", "after", "bool_quantity",
    "reordered_before", "unknown_key", "missing_requirement", "duplicate_requirement",
    "wrong_requirement_quantity", "bool_requirement_quantity", "unknown_requirement_sku",
    "missing_quote", "blank_quote", "invalid_requirements", "invalid_before", "invalid_guard",
])
def test_tampered_guard_cannot_create_revision_or_change_inventory(store, fault):
    engine, drafts, first = store
    source = evidence(first)
    guard = source["item_guard"]
    if fault in {"contract", "draft_id"}:
        guard[fault] = "other"
    elif fault == "revision":
        guard["revision"] = 9
    elif fault == "bool_revision":
        guard["revision"] = True
    elif fault in {"before", "after"}:
        guard[fault][0]["quantity"] = 9
    elif fault == "bool_quantity":
        guard["after"][1]["quantity"] = True
    elif fault == "reordered_before":
        guard["before"].reverse()
    elif fault == "unknown_key":
        guard["unexpected"] = True
    elif fault == "missing_requirement":
        guard["requirements"] = []
    elif fault == "duplicate_requirement":
        guard["requirements"].append(deepcopy(guard["requirements"][0]))
    elif fault == "wrong_requirement_quantity":
        guard["requirements"][0]["quantity"] = 9
    elif fault == "bool_requirement_quantity":
        guard["requirements"][0]["quantity"] = True
    elif fault == "unknown_requirement_sku":
        guard["requirements"][0]["sku"] = "C"
    elif fault == "missing_quote":
        guard["requirements"][0]["quote"] = "没有出现在原话中"
    elif fault == "blank_quote":
        guard["requirements"][0]["quote"] = ""
    elif fault == "invalid_requirements":
        guard["requirements"] = {}
    elif fault == "invalid_before":
        guard["before"] = None
    else:
        source["item_guard"] = None
    before = raw(engine)
    with pytest.raises(MutationError) as error:
        drafts.revise_quantity("s", first["draft_id"], 1, 1, 5, expected_sku="A",
                               evidence=source)
    assert error.value.code == "constraint_evidence_invalid"
    assert raw(engine) == before


@pytest.mark.parametrize("items,requirements", [
    ((("A", 5), ("B", 4)), [{"sku": "A", "quantity": 5, "quote": TEXT}]),
    ((("B", 3), ("A", 5)), [{"sku": "A", "quantity": 5, "quote": TEXT}]),
    ((("A", 5),), [{"sku": "A", "quantity": 5, "quote": TEXT}]),
    ((("A", 5), ("B", 3), ("C", 1)), [{"sku": "A", "quantity": 5, "quote": TEXT}]),
])
def test_guard_rejects_unrelated_quantity_and_sku_membership_or_order_changes(
    store, items, requirements,
):
    engine, drafts, first = store
    source = evidence(first, items, requirements=requirements)
    before = raw(engine)
    with pytest.raises(MutationError) as error:
        drafts.revise("s", first["draft_id"], 1, "完整客户", list(items), "保留备注",
                      evidence=source)
    assert error.value.code == "constraint_evidence_invalid"
    assert raw(engine) == before


def test_creation_cannot_claim_an_observed_previous_item_guard(store):
    engine, drafts, first = store
    before = raw(engine)
    with pytest.raises(MutationError) as error:
        drafts.prepare("s", "新客户", [("A", 5), ("B", 3)], evidence=evidence(first))
    assert error.value.code == "constraint_evidence_invalid"
    assert raw(engine) == before


def test_explicit_append_guard_preserves_old_items_and_persists_new_requirement(store):
    engine, drafts, first = store
    before = raw(engine)
    text = "在当前草稿新增商品 C 1件，放在第三行；原有商品、客户和全部要求保持不变"
    requirements = [{"sku": "C", "quantity": 1, "quote": "在当前草稿新增商品 C 1件"}]
    items = [("A", 2), ("B", 3), ("C", 1)]
    source = evidence(first, items, text=text, requirements=requirements)
    second = drafts.revise("s", first["draft_id"], 1, "完整客户", items, "保留备注",
                           evidence=source)
    assert [(item["sku"], item["quantity"]) for item in second["items"]] == items
    record = drafts.history("s", first["draft_id"])["items"][0]["change_record"]
    assert record["item_guard"] == source["item_guard"]
    assert record["item_guard"]["requirements"] == requirements
    assert record["confirmed_fields"] == []
    assert second["item_constraint"]["verified"]
    assert raw(engine)["drafts"][:1] == before["drafts"]
    assert raw(engine)["stock"] == before["stock"]


@pytest.mark.parametrize("items,requirements", [
    ([("A", 2), ("B", 3), ("C", 1)], []),
    ([("A", 2), ("B", 3), ("C", 1)], [{"sku": "C", "quantity": 2, "quote": "新增C"}]),
    ([("A", 5), ("B", 3), ("C", 1)], [{"sku": "C", "quantity": 1, "quote": "新增C"}]),
    ([("A", 2), ("B", 4), ("C", 1)], [{"sku": "C", "quantity": 1, "quote": "新增C"}]),
    ([("B", 3), ("A", 2), ("C", 1)], [{"sku": "C", "quantity": 1, "quote": "新增C"}]),
    ([("A", 2), ("C", 1), ("B", 3)], [{"sku": "C", "quantity": 1, "quote": "新增C"}]),
    ([("A", 2), ("C", 1)], [{"sku": "C", "quantity": 1, "quote": "新增C"}]),
])
def test_append_requires_new_sku_authorization_without_authorizing_other_changes(
    store, items, requirements,
):
    engine, drafts, first = store
    source = evidence(first, items, text="新增C", requirements=requirements)
    before = raw(engine)
    with pytest.raises(MutationError) as error:
        drafts.revise("s", first["draft_id"], 1, "完整客户", items, "保留备注", evidence=source)
    assert error.value.code == "constraint_evidence_invalid"
    assert raw(engine) == before


@pytest.mark.parametrize("source", [None, {"contract": "draft-form-1"}])
def test_legacy_and_manual_revisions_do_not_invent_runtime_guard(store, source):
    engine, drafts, first = store
    second = drafts.revise("s", first["draft_id"], 1, "完整客户", [("B", 4), ("A", 5)],
                           "保留备注", evidence=source)
    with Session(engine) as session:
        record = json.loads(session.get(OrderDraftRow, (first["draft_id"], 2)).snapshot)
    assert "item_guard" not in record["change_record"]
    assert [item["sku"] for item in second["items"]] == ["B", "A"]
