"""Exact catalogue batching preserves counts, boundaries and the legacy result."""

import pytest
from erp_store.db import ProductRow, init_db, make_engine
from erp_store.order_request import resolve_request
from sqlalchemy import event
from sqlalchemy.orm import Session


@pytest.fixture
def catalogue(tmp_path):
    engine = make_engine(tmp_path / "catalogue.db")
    init_db(engine)
    with Session(engine) as session:
        rows = [(f"P{i:03}", f"商品{i:03}") for i in range(100)]
        rows += [(f"D{i:03}", "同名商品") for i in range(100)]
        rows += [("X", "另外的商品"), ("Y", "X"), ("B", "维生素B"), ("B10", "维生素B十"),
                 ("BOX", "商品000两箱"), ("SELF", "SELF")]
        session.add_all(ProductRow(sku=sku, name=name, status="在售", price=10, category="测试")
                        for sku, name in rows)
        session.commit()
    yield engine
    engine.dispose()


@pytest.mark.parametrize("items", [
    "商品000两件，商品099三件", "SKU X 两件", "X两件", "SELF两件", "同名商品两件",
    "维生素B十二件", "十二件维生素B", "%两件", "商品000两件，SKU P000 三件",
])
def test_batch_preserves_exact_legacy_results(catalogue, items):
    with Session(catalogue) as session:
        source = f"给客户甲准备订单：{items}；只保存草稿"
        assert resolve_request(session, source, extended=True) == resolve_request(session, source)


def test_hundred_items_need_two_reads_and_names_do_not_hide_sku_collisions(catalogue):
    queries = []

    def count(conn, cursor, statement, params, context, executemany):
        queries.append(statement)

    event.listen(catalogue, "before_cursor_execute", count)
    try:
        with Session(catalogue) as session:
            source = "给客户甲准备订单：" + "，".join(f"商品{i:03} 两件" for i in range(100))
            ready = resolve_request(session, source, extended=True)
            assert ready["status"] == "resolved" and len(ready["items"]) == 100
            assert len(queries) == 2
            issue = resolve_request(session, "给客户甲准备订单：X两件", extended=True)["issues"][0]
            assert issue["exact_matches"] == 2
            assert [r["sku"] for r in issue["candidates"]] == ["X", "Y"]
    finally:
        event.remove(catalogue, "before_cursor_execute", count)


def test_full_counts_and_unit_name_boundaries_survive_display_truncation(catalogue):
    with Session(catalogue) as session:
        issue = resolve_request(session, "给客户甲准备订单：同名商品两件", extended=True,
                                selections={"1": "D000"})["issues"][0]
        assert issue["has_more"] and issue["exact_matches"] == 100
        assert len(issue["candidates"]) == 20  # A truncated set cannot be chosen by ordinal.
        result = resolve_request(session, "给客户甲准备订单：商品000两箱", extended=True,
                                 allow_incomplete=True)
        assert result["issues"][0]["code"] == "quantity_boundary_ambiguous"
