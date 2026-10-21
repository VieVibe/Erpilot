"""Session-owned immutable quote revisions and atomic approved submission."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from erp_store.db import OrderDraftRow, ProductRow, StockRow
from erp_store.draft_audit import snapshot_constraints, with_draft_audit
from erp_store.draft_budget import (
    UNSET,
    budget_check,
    budget_issues,
    ensure_budget,
    normalize_budget,
)
from erp_store.models import Order, ProductStatus
from erp_store.mutations import ErpMutations, MutationError
from erp_store.pricing import PricingError, price_for_quantity

_REFRESH_HINT = "请修改或刷新草稿，取得新版本后重新审批；操作未执行"


class ErpDrafts(ErpMutations):
    def __init__(self, engine, *, clock=None):
        super().__init__(engine)
        self._clock = clock or (lambda: datetime.now(UTC))
        # Legacy databases gain this additive table without changing existing orders.
        # Concurrent first readers recheck under the same write lock as submissions.
        if not inspect(engine).has_table(OrderDraftRow.__tablename__):
            with self._write_session() as session:
                self._ensure_table(session)
                session.commit()

    def _now(self):
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("draft clock must include timezone")
        return now.astimezone(UTC)

    def _ensure_table(self, session):
        OrderDraftRow.__table__.create(session.connection(), checkfirst=True)

    @staticmethod
    def _normalize(session_id, customer, items, note):
        if not session_id or len(session_id) > 128:
            raise MutationError("invalid_argument", "会话标识须为 1~128 个字符")
        if not items or len(items) > 100:
            raise MutationError("invalid_argument", "草稿须包含 1~100 行商品")
        for sku, quantity in items:
            if not isinstance(sku, str) or not sku.strip() or len(sku) > 32:
                raise MutationError("invalid_argument", "商品 SKU 须为 1~32 个非空字符")
            if type(quantity) is not int or quantity < 1:
                raise MutationError("invalid_argument", "商品数量须为正整数")
        if len(customer) > 64 or (note is not None and len(note) > 255):
            raise MutationError("invalid_argument", "客户名最多 64 字符，备注最多 255 字符")
        return ErpMutations._create_order_request(customer, items, note)[:2]

    def _snapshot(self, session, customer, merged, note):
        lines = []
        for sku, quantity in merged.items():
            product = session.get(ProductRow, sku)
            if product is None:
                raise MutationError("not_found", f"商品不存在：{sku}", "请确认商品 SKU")
            if product.status != ProductStatus.ON_SALE.value:
                raise MutationError("invalid_transition", f"商品已下架：{sku}", "请换在售商品")
            stock = session.get(StockRow, sku)
            have = stock.quantity if stock else 0
            if have < quantity:
                raise MutationError(
                    "insufficient_stock",
                    f"商品 {sku} 库存不足，当前 {have}",
                    "请减少数量或选择其他商品",
                )
            try:
                discount, unit, total = price_for_quantity(product.price, quantity)
            except PricingError as exc:
                raise MutationError("invalid_argument", str(exc), "请检查商品现价") from exc
            lines.append(
                dict(
                    sku=sku,
                    name=product.name,
                    quantity=quantity,
                    base_price=product.price,
                    discount=discount,
                    unit_price=unit,
                    line_total=total,
                    stock_before=have,
                    stock_after=have - quantity,
                )
            )
        return {
            "customer": customer,
            "note": note,
            "items": lines,
            "total_amount": round(sum(line["line_total"] for line in lines), 2),
        }

    def _latest(self, session, session_id, draft_id):
        row = session.scalar(
            select(OrderDraftRow)
            .where(
                OrderDraftRow.draft_id == draft_id,
                OrderDraftRow.session_id == session_id,
            )
            .order_by(OrderDraftRow.revision.desc())
            .limit(1)
        )
        if row is None:
            raise MutationError("not_found", "草稿不存在或不属于此会话", "请重新查询当前会话草稿")
        return row

    def _payload(self, row, *, latest_revision=None):
        snapshot = json.loads(row.snapshot)
        change_record = snapshot.pop("change_record", None)
        status = (
            "submitted"
            if row.order_id
            else "superseded"
            if latest_revision is not None and latest_revision != row.revision
            else "expired"
            if datetime.fromisoformat(row.expires_at) <= self._now()
            else "open"
        )
        return {
            **snapshot,
            "budget_check": budget_check(snapshot),
            "pending_issues": budget_issues(snapshot),
            "item_count": len(snapshot["items"]),
            "items": [dict(item, item_number=number)
                      for number, item in enumerate(snapshot["items"], 1)],
            "last_change": {
                "revision": row.revision, "operation": change_record["operation"],
                "change_count": len(change_record["changes"]),
            } if change_record is not None else None,
            "draft_id": row.draft_id,
            "revision": row.revision,
            "expires_at": row.expires_at,
            "status": status,
            "order_id": row.order_id,
            "inventory_effect": "projection",
            "inventory_note": (
                "库存前后值为此版本报价时测算的预计提交前后库存，不是实时库存；"
                "保存草稿不预留、不扣减库存，实际变更以订单提交结果为准。"
            ),
        }

    def _save_revision(
        self, session, session_id, draft_id, revision, snapshot, *,
        operation="create", evidence=None, previous=None,
    ):
        now = self._now()
        snapshot = with_draft_audit(
            snapshot, draft_id=draft_id, revision=revision, operation=operation,
            evidence=evidence, previous=previous,
        )
        row = OrderDraftRow(
            draft_id=draft_id,
            revision=revision,
            session_id=session_id,
            snapshot=json.dumps(snapshot, ensure_ascii=False),
            created_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=30)).isoformat(),
            order_id=None,
        )
        session.add(row)
        session.flush()
        return self._payload(row)

    def prepare(self, session_id, customer, items, note=None, *, evidence=None, budget_limit=None):
        customer, merged = self._normalize(session_id, customer, items, note)
        with self._write_session() as session:
            self._ensure_table(session)
            snapshot = self._snapshot(session, customer, merged, note)
            snapshot["budget_limit"] = normalize_budget(budget_limit)
            result = self._save_revision(
                session, session_id, uuid4().hex, 1, snapshot, evidence=evidence,
            )
            session.commit()
            return result

    def get(self, session_id, draft_id, revision=None):
        with Session(self._engine) as session:
            self._ensure_table(session)
            latest = self._latest(session, session_id, draft_id)
            row = latest if revision is None else session.get(OrderDraftRow, (draft_id, revision))
            if row is None or row.session_id != session_id:
                raise MutationError("not_found", "草稿版本不存在", "请查询当前草稿版本")
            return self._payload(row, latest_revision=latest.revision)

    def list_latest(self, session_id):
        with Session(self._engine) as session:
            self._ensure_table(session)
            versions = select(OrderDraftRow.draft_id, func.max(OrderDraftRow.revision).label("rev"))
            versions = versions.where(OrderDraftRow.session_id == session_id)
            versions = versions.group_by(OrderDraftRow.draft_id).subquery()
            rows = session.scalars(
                select(OrderDraftRow)
                .join(
                    versions,
                    (OrderDraftRow.draft_id == versions.c.draft_id)
                    & (OrderDraftRow.revision == versions.c.rev),
                )
                .order_by(OrderDraftRow.created_at.desc(), OrderDraftRow.draft_id)
                .limit(20)
            )
            return [self._payload(row) for row in rows]

    def history(self, session_id, draft_id, *, before_revision=None, limit=5):
        if type(limit) is not int or not 1 <= limit <= 20 or (
            before_revision is not None
            and (type(before_revision) is not int or before_revision < 1)
        ):
            raise MutationError("invalid_argument", "版本游标或分页大小无效")
        with Session(self._engine) as session:
            latest = self._latest(session, session_id, draft_id)
            query = select(OrderDraftRow).where(
                OrderDraftRow.draft_id == draft_id, OrderDraftRow.session_id == session_id,
                OrderDraftRow.revision <= latest.revision,
            )
            if before_revision is not None:
                query = query.where(OrderDraftRow.revision < before_revision)
            rows = list(session.scalars(
                query.order_by(OrderDraftRow.revision.desc()).limit(limit + 1)
            ))
            items = []
            for row in rows[:limit]:
                snapshot = json.loads(row.snapshot)
                items.append({
                    "draft_id": row.draft_id, "revision": row.revision,
                    "created_at": row.created_at,
                    "task_constraints": snapshot_constraints(snapshot, row.revision),
                    "change_record": snapshot.get("change_record"),
                    "budget_constraint": snapshot.get("budget_constraint"),
                })
            return {
                "schema_version": 1, "draft_id": draft_id, "latest_revision": latest.revision,
                "items": items, "has_more": len(rows) > limit,
                "next_before_revision": items[-1]["revision"] if len(rows) > limit else None,
            }

    def revise(
        self, session_id, draft_id, expected_revision, customer, items, note=None, *, evidence=None,
        budget_limit=UNSET,
    ):
        customer, merged = self._normalize(session_id, customer, items, note)
        with self._write_session() as session:
            self._ensure_table(session)
            latest = self._latest(session, session_id, draft_id)
            if latest.order_id:
                raise MutationError("draft_submitted", "草稿已提交，不能修改", "请创建新的草稿")
            if latest.revision != expected_revision:
                raise MutationError("draft_changed", "草稿已有新版本", _REFRESH_HINT)
            snapshot = self._snapshot(session, customer, merged, note)
            previous = json.loads(latest.snapshot)
            snapshot["budget_limit"] = (previous.get("budget_limit") if budget_limit is UNSET
                                        else normalize_budget(budget_limit))
            result = self._save_revision(
                session, session_id, draft_id, latest.revision + 1, snapshot,
                operation="revise", evidence=evidence, previous=previous,
            )
            session.commit()
            return result

    def task_state(self, session_id):
        with Session(self._engine) as session:
            versions = (
                select(OrderDraftRow.draft_id, func.max(OrderDraftRow.revision).label("rev"))
                .where(OrderDraftRow.session_id == session_id)
                .group_by(OrderDraftRow.draft_id)
                .subquery()
            )
            rows = list(session.scalars(
                select(OrderDraftRow)
                .join(
                    versions,
                    (OrderDraftRow.draft_id == versions.c.draft_id)
                    & (OrderDraftRow.revision == versions.c.rev),
                )
                .where(OrderDraftRow.order_id.is_(None))
                .order_by(OrderDraftRow.created_at.desc(), OrderDraftRow.draft_id)
            ))
            payloads = [self._payload(row) for row in rows[:20]]
            return {
                "status": "none" if not rows else "active" if len(rows) == 1 else "ambiguous",
                "draft": payloads[0] if len(rows) == 1 else None,
                "candidates": [
                    {key: payload[key] for key in ("draft_id", "revision", "customer", "status")}
                    for payload in payloads
                ],
                "has_more": len(rows) > 20,
            }

    def revise_quantity(
        self, session_id, draft_id, expected_revision, item_number, quantity, *, expected_sku=None,
        evidence=None,
    ):
        with self._write_session() as session:
            latest = self._latest(session, session_id, draft_id)
            if latest.order_id:
                raise MutationError("draft_submitted", "草稿已提交，不能修改", "请创建新的草稿")
            for value in (expected_revision, item_number, quantity):
                if type(value) is not int or value < 1:
                    raise MutationError("invalid_argument", "版本、行号和数量须为正整数")
            if latest.revision != expected_revision:
                raise MutationError("draft_changed", "草稿已有新版本", _REFRESH_HINT)
            previous = json.loads(latest.snapshot)
            if item_number > len(previous["items"]):
                raise MutationError("invalid_argument", "商品行号超出草稿明细范围")
            if expected_sku is not None:
                if (not isinstance(expected_sku, str) or not expected_sku.strip()
                        or len(expected_sku) > 32):
                    raise MutationError("invalid_argument", "目标 SKU 须为 1~32 个非空字符")
                if previous["items"][item_number - 1]["sku"] != expected_sku.strip():
                    raise MutationError(
                        "draft_item_changed", "指定 SKU 与当前版本的商品行不一致",
                        "请重新查询草稿并按用户确认的 SKU 定位商品；本次未修改",
                    )
            items = [
                (line["sku"], quantity if index == item_number else line["quantity"])
                for index, line in enumerate(previous["items"], 1)
            ]
            customer, merged = self._normalize(
                session_id, previous["customer"], items, previous["note"]
            )
            snapshot = self._snapshot(session, customer, merged, previous["note"])
            snapshot["budget_limit"] = previous.get("budget_limit")
            result = self._save_revision(
                session, session_id, draft_id, latest.revision + 1, snapshot,
                operation="revise_quantity", evidence=evidence, previous=previous,
            )
            session.commit()
            return result

    @staticmethod
    def _submission_request(session_id, draft_id, revision):
        return ["submit_order_draft", session_id, draft_id, revision]

    def submit(self, session_id, draft_id, revision, client_token):
        with self._write_session() as session:
            self._ensure_table(session)
            latest = self._latest(session, session_id, draft_id)
            request = self._submission_request(session_id, draft_id, revision)
            prior = self._prior(session, client_token, request)
            if prior is not None:
                return Order.model_validate(prior)
            if latest.revision != revision:
                raise MutationError("draft_changed", "批准版本已不是当前草稿", _REFRESH_HINT)
            if latest.order_id:
                result = Order.model_validate(json.loads(latest.submission_result))
                self._remember(session, client_token, request, result.model_dump(mode="json"))
                session.commit()
                return result
            if datetime.fromisoformat(latest.expires_at) <= self._now():
                raise MutationError("draft_expired", "报价草稿已过期", _REFRESH_HINT)
            snapshot = json.loads(latest.snapshot)
            ensure_budget(snapshot)
            merged = {line["sku"]: line["quantity"] for line in snapshot["items"]}
            for line in snapshot["items"]:
                product = session.get(ProductRow, line["sku"])
                if product is None or product.status != ProductStatus.ON_SALE.value:
                    raise MutationError("quote_changed", "商品已不存在或不在售", _REFRESH_HINT)
                if product.price != line["base_price"] or product.name != line["name"]:
                    raise MutationError("quote_changed", "商品价格或名称已变化", _REFRESH_HINT)
                try:
                    current_price = price_for_quantity(product.price, line["quantity"])
                except PricingError as exc:
                    raise MutationError("quote_changed", "计价条件已变化", _REFRESH_HINT) from exc
                if current_price != (line["discount"], line["unit_price"], line["line_total"]):
                    raise MutationError("quote_changed", "折扣规则已变化", _REFRESH_HINT)
                stock = session.get(StockRow, line["sku"])
                if stock is None or stock.quantity != line["stock_before"]:
                    raise MutationError("stock_changed", "库存已变化，需重新确认", _REFRESH_HINT)
            result = self._create_order_in_session(
                session,
                snapshot["customer"],
                merged,
                snapshot["note"],
                client_token,
                request,
            )
            latest.order_id = result.order_id
            latest.submission_result = result.model_dump_json()
            session.commit()
            return result

    def lookup_submission(self, session_id, arguments, token):
        with Session(self._engine) as session:
            self._ensure_table(session)
            try:
                self._latest(session, session_id, arguments["draft_id"])
            except MutationError:
                return "absent", None
            request = self._submission_request(
                session_id,
                arguments["draft_id"],
                arguments["revision"],
            )
            try:
                result = self._prior(session, token, request)
            except MutationError:
                return "conflict", None
            return ("found", result) if result is not None else ("absent", None)
