from datetime import UTC, datetime, timedelta

import pytest
from erp_store.db import Base, ProductRow, StockRow, init_db, make_engine
from erp_store.drafts import ErpDrafts
from erp_store.mutations import MutationError
from sqlalchemy import select
from sqlalchemy.orm import Session


@pytest.fixture
def store(tmp_path):
    engine = make_engine(tmp_path / "review.db")
    init_db(engine)
    with Session(engine) as session:
        session.add(ProductRow(sku="A", name="商品甲", category="测试", price=100, status="在售"))
        session.add(StockRow(sku="A", quantity=100, warehouse="主仓"))
        session.commit()
    yield engine
    engine.dispose()


def all_rows(engine):
    with engine.connect() as connection:
        return {name: list(connection.execute(select(table)))
                for name, table in Base.metadata.tables.items()}


def test_review_collects_all_blockers_read_only_and_refresh_does_not_clear_budget(store):
    now = datetime(2026, 10, 27, tzinfo=UTC)
    drafts = ErpDrafts(store, clock=lambda: now)
    first = drafts.prepare("s", "客户", [("A", 3)], budget_limit=200)
    with Session(store) as session:
        session.get(ProductRow, "A").price = 120
        session.get(StockRow, "A").quantity = 50
        session.commit()
    now += timedelta(minutes=31)
    before = all_rows(store)
    current = drafts.get("s", first["draft_id"])
    assert {row["code"] for row in current["pending_issues"]} == {
        "draft_expired", "budget_exceeded", "quote_changed", "stock_changed",
    }
    assert all(row["revision"] == 1 and row["blocking"] for row in current["pending_issues"])
    assert current["submission_check"]["can_submit"] is False
    assert current["items"] == first["items"] and current["total_amount"] == 300
    assert all_rows(store) == before
    with pytest.raises(MutationError) as exc:
        drafts.check_submission("s", first["draft_id"], 1)
    assert exc.value.code == "draft_expired" and all_rows(store) == before
    second = drafts.revise("s", first["draft_id"], 1, "客户", [("A", 3)])
    assert [row["code"] for row in second["pending_issues"]] == ["budget_exceeded"]
    assert second["total_amount"] == 360 and not second["submission_check"]["can_submit"]
    third = drafts.revise("s", first["draft_id"], 2, "客户", [("A", 3)], budget_limit=400,
                          evidence={"contract": "draft-form-1"})
    assert not third["pending_issues"] and third["submission_check"]["can_submit"]
    assert drafts.get("s", first["draft_id"], 1)["pending_issues"][0]["code"] == "draft_changed"
    with pytest.raises(MutationError) as exc:
        drafts.check_submission("s", first["draft_id"], 2)
    assert exc.value.code == "draft_changed"
    assert drafts.check_submission("s", first["draft_id"], 3)["revision"] == 3


@pytest.mark.parametrize("change", ["price", "name", "status", "stock", "missing_stock"])
def test_preflight_and_actual_submission_use_same_current_facts(store, change):
    drafts = ErpDrafts(store)
    draft = drafts.prepare("s", "客户", [("A", 3)])
    with Session(store) as session:
        if change == "price":
            session.get(ProductRow, "A").price = 110
        elif change == "name":
            session.get(ProductRow, "A").name = "新名称"
        elif change == "status":
            session.get(ProductRow, "A").status = "下架"
        elif change == "stock":
            session.get(StockRow, "A").quantity = 2
        else:
            session.delete(session.get(StockRow, "A"))
        session.commit()
    before = all_rows(store)
    draft = drafts.get("s", draft["draft_id"])
    code = "stock_changed" if "stock" in change else "quote_changed"
    assert draft["pending_issues"][0]["code"] == code
    for operation in (lambda: drafts.check_submission("s", draft["draft_id"], 1),
                      lambda: drafts.submit("s", draft["draft_id"], 1, "approved")):
        with pytest.raises(MutationError) as exc:
            operation()
        assert exc.value.code == code and all_rows(store) == before
    with pytest.raises(MutationError):
        drafts.get("other", draft["draft_id"])


def test_task_ambiguity_has_action_and_submitted_order_is_not_a_pending_issue(store):
    drafts = ErpDrafts(store)
    first = drafts.prepare("s", "客户甲", [("A", 1)])
    drafts.prepare("s", "客户乙", [("A", 1)])
    task = drafts.task_state("s")
    assert task["status"] == "ambiguous" and task["draft"] is None
    assert task["pending_issues"][0]["code"] == "draft_selection_required"
    order = drafts.submit("s", first["draft_id"], 1, "once")
    submitted = drafts.get("s", first["draft_id"])
    assert not submitted["pending_issues"] and not submitted["submission_check"]["can_submit"]
    assert drafts.submit("s", first["draft_id"], 1, "once").order_id == order.order_id
