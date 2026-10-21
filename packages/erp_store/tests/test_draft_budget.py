import json

import pytest
from erp_store.db import OrderDraftRow, ProductRow, StockRow, init_db, make_engine
from erp_store.drafts import ErpDrafts
from erp_store.mutations import MutationError
from erp_store.repository import ErpRepository
from sqlalchemy import select
from sqlalchemy.orm import Session

FORM = {"contract": "draft-form-1"}


@pytest.fixture
def store(tmp_path):
    engine = make_engine(tmp_path / "budget.db")
    init_db(engine)
    with Session(engine) as session:
        for sku, price in (("A", 1000), ("B", 200)):
            session.add(ProductRow(sku=sku, name=sku, category="测试", price=price, status="在售"))
            session.add(StockRow(sku=sku, quantity=100, warehouse="主仓"))
        session.commit()
    yield engine, ErpDrafts(engine), ErpRepository(engine)
    engine.dispose()


def raw(engine):
    with Session(engine) as session:
        return [(r.revision, r.snapshot) for r in session.scalars(select(OrderDraftRow))]


def test_budget_journey_3000_3200_3500_and_exact_boundary(store):
    engine, drafts, repo = store
    first = drafts.prepare("s", "完整客户", [("A", 3)], "保留备注",
                           budget_limit=3000, evidence=FORM)
    assert first["budget_check"]["status"] == "within_budget" and not first["pending_issues"]
    initial = raw(engine)
    second = drafts.revise("s", first["draft_id"], 1, "完整客户", [("A", 3), ("B", 1)],
                           "保留备注", evidence=FORM)
    assert second["budget_limit"] == 3000 and second["total_amount"] == 3200
    assert second["budget_check"]["over_amount"] == 200
    assert second["pending_issues"][0]["code"] == "budget_exceeded"
    assert second["budget_constraint"] == first["budget_constraint"]
    with pytest.raises(MutationError) as exc:
        drafts.submit("s", first["draft_id"], 2, "over-budget")
    assert exc.value.code == "budget_exceeded" and repo.count_orders() == 0
    assert repo.get_stock("A").quantity == 100 and raw(engine)[:1] == initial
    third = drafts.revise("s", first["draft_id"], 2, "完整客户", [("A", 3), ("B", 1)],
                          "保留备注", evidence=FORM, budget_limit=3500)
    assert not third["pending_issues"] and third["budget_constraint"]["origin"]["revision"] == 3
    with pytest.raises(MutationError) as exc:
        drafts.submit("s", first["draft_id"], 2, "old-confirmation")
    assert exc.value.code == "draft_changed"
    order = drafts.submit("s", first["draft_id"], 3, "approved")
    assert order.total_amount == 3200 and order.note == "保留备注"
    assert drafts.submit("s", first["draft_id"], 3, "approved").order_id == order.order_id
    assert repo.count_orders() == 1 and repo.get_stock("A").quantity == 97
    assert repo.get_stock("B").quantity == 99
    history = drafts.history("s", first["draft_id"])
    assert {"field": "budget_limit", "before": 3000, "after": 3500} in (
        history["items"][0]["change_record"]["changes"]
    )
    assert drafts.get("s", first["draft_id"], 1)["budget_limit"] == 3000


@pytest.mark.parametrize("value", [True, "3000", 0, -1, 1.001, float("inf"), float("nan"), 1e13])
def test_invalid_budget_never_creates_a_revision(store, value):
    engine, drafts, _ = store
    before = raw(engine)
    with pytest.raises(MutationError):
        drafts.prepare("s", "客户", [("A", 1)], budget_limit=value, evidence=FORM)
    assert raw(engine) == before


@pytest.mark.parametrize("value", [None, 3500, 2500])
def test_unattributed_raw_changes_cannot_erase_or_replace_saved_budget(store, value):
    engine, drafts, _ = store
    first = drafts.prepare("s", "客户", [("A", 3)], budget_limit=3000, evidence=FORM)
    before = raw(engine)
    with pytest.raises(MutationError) as exc:
        drafts.revise("s", first["draft_id"], 1, "客户", [("A", 3)], budget_limit=value)
    assert exc.value.code == "constraint_changed" and raw(engine) == before


def test_quantity_price_refresh_restore_and_explicit_clear_preserve_origins(store):
    engine, drafts, _ = store
    first = drafts.prepare("s", "客户", [("A", 2)], budget_limit=3000, evidence=FORM)
    second = drafts.revise_quantity("s", first["draft_id"], 1, 1, 4, expected_sku="A")
    with Session(engine) as session:
        session.get(ProductRow, "A").price = 1100
        session.commit()
    restored = ErpDrafts(engine)
    third = restored.revise("s", first["draft_id"], 2, "客户", [("A", 4)])
    assert third["budget_check"]["over_amount"] == 1400
    assert first["budget_constraint"] == second["budget_constraint"] == third["budget_constraint"]
    assert restored.task_state("s")["draft"]["pending_issues"]
    cleared = restored.revise("s", first["draft_id"], 3, "客户", [("A", 4)],
                              budget_limit=None, evidence=FORM)
    assert cleared["budget_limit"] is None and not cleared["pending_issues"]
    assert restored.get("s", first["draft_id"], 3)["budget_limit"] == 3000
    with pytest.raises(MutationError):
        restored.get("other", first["draft_id"])


def test_legacy_drafts_do_not_invent_a_budget(store):
    engine, drafts, _ = store
    first = drafts.prepare("s", "客户", [("A", 1)])
    with Session(engine) as session:
        row = session.get(OrderDraftRow, (first["draft_id"], 1))
        payload = json.loads(row.snapshot)
        payload.pop("budget_limit")
        payload.pop("budget_constraint")
        row.snapshot = json.dumps(payload)
        session.commit()
    assert drafts.get("s", first["draft_id"])["budget_check"]["status"] == "not_set"
    assert drafts.revise_quantity("s", first["draft_id"], 1, 1, 2)["budget_limit"] is None
