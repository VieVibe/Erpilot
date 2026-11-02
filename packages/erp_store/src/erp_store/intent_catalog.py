"""Bounded exact catalogue index for a single analysis; never caches business facts."""

from dataclasses import dataclass

from sqlalchemy import func, select

from erp_store.db import ProductRow


@dataclass(frozen=True)
class Candidate:
    sku: str
    name: str
    status: str


class ExactCatalog:
    """At most two reads, with full name counts and only the first 20 rows per name.

    A SKU collision with a different product's name counts as another exact match.
    The display limit never changes uniqueness or permits selection from a truncated set.
    """

    def __init__(self, session, sku_queries, name_queries):
        self.by_sku, self.by_name, self.counts = {}, {}, {}
        if sku_queries:
            rows = session.execute(select(ProductRow.sku, ProductRow.name, ProductRow.status)
                                   .where(ProductRow.sku.in_(sorted(sku_queries))))
            self.by_sku = {r.sku: Candidate(r.sku, r.name, r.status) for r in rows}
        if name_queries:
            ranked = select(
                ProductRow.sku, ProductRow.name, ProductRow.status,
                func.count().over(partition_by=ProductRow.name).label("total"),
                func.row_number().over(partition_by=ProductRow.name,
                                       order_by=ProductRow.sku).label("rank"),
            ).where(ProductRow.name.in_(sorted(name_queries))).subquery()
            for row in session.execute(select(ranked).where(ranked.c.rank <= 20)):
                self.counts[row.name] = row.total
                self.by_name.setdefault(row.name, []).append(Candidate(
                    row.sku, row.name, row.status))

    def lookup(self, query, *, sku_only=False):
        exact = self.by_sku.get(query)
        rows = {} if sku_only else {r.sku: r for r in self.by_name.get(query, [])}
        total = 0 if sku_only else self.counts.get(query, 0)
        if exact:
            rows[exact.sku] = exact
            if sku_only or exact.name != query:
                total += 1
        return total, sorted(rows.values(), key=lambda r: r.sku)[:20]
