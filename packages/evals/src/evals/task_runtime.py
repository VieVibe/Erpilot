"""B4 多轮装配：真实 MCP、隔离会话、逐轮 trace 和全表状态证据。"""

import hashlib
import json
from pathlib import Path

from agent_core.demo_tools import system_prompt
from erp_store.db import Base, ProductRow, StockRow
from erp_store.drafts import ErpDrafts
from mcp_erp.draft_tools import DRAFT_GUIDANCE
from sqlalchemy import select

from evals.billing import BudgetedClient, _merge_estimates
from evals.model import CaseResult
from evals.runner import run_case
from evals.task_cases import (
    CUSTOMER_A,
    CUSTOMER_B,
    DRAFT_NOTE,
    FOREIGN_CUSTOMER,
    TaskCase,
    check_task_turn,
)
from evals.task_reference_cases import ReferenceTurn, check_reference_turn


def snapshot_all(engine):
    with engine.connect() as connection:
        return {
            name: [dict(r) for r in connection.execute(
                select(table).order_by(*table.primary_key)
            ).mappings()]
            for name, table in sorted(Base.metadata.tables.items())
        }


def prepare_task_fixture(case: TaskCase, engine, session_id):
    with engine.connect() as connection:
        skus = tuple(connection.scalars(
            select(ProductRow.sku).join(StockRow, ProductRow.sku == StockRow.sku)
            .where(ProductRow.status == "在售", StockRow.quantity >= 80)
            .order_by(ProductRow.sku).limit(2)
        ))
    if len(skus) != 2:
        raise ValueError("B4 种子库需要两件库存至少 80 的在售商品")
    drafts = ErpDrafts(engine)
    items = list(zip(skus, (2, 3), strict=True))
    drafts.prepare("b4-foreign", FOREIGN_CUSTOMER, items, DRAFT_NOTE)
    if case.fixture != "none":
        drafts.prepare(session_id, CUSTOMER_A, items, DRAFT_NOTE)
    if case.fixture == "multiple":
        drafts.prepare(session_id, CUSTOMER_B, items, DRAFT_NOTE)
    return skus


def _digest(state):
    return hashlib.sha256(json.dumps(state, ensure_ascii=False, sort_keys=True,
                                    default=str).encode()).hexdigest()


async def run_task_case(
    case: TaskCase, *, client, tools, engine, resolved, trace_dir: Path, session_id: str,
    budget=None,
):
    skus = prepare_task_fixture(case, engine, session_id)
    resolved = {**resolved, "b4_sku_a": skus[0], "b4_sku_b": skus[1]}
    prompt = system_prompt(True) + "\n" + DRAFT_GUIDANCE
    conversation = [{"role": "system", "content": prompt}]
    turns, traces = [], []
    for turn in case.turns:
        if turn.reset_history:
            conversation = [{"role": "system", "content": prompt}]
        if turn.refresh_before:
            # 受控外部草稿编辑：旧聊天中的明细顺序不再是当前版本。
            drafts = ErpDrafts(engine)
            current = drafts.task_state(session_id)["draft"]
            skus = tuple(reversed(skus))
            drafts.revise(session_id, current["draft_id"], current["revision"],
                          CUSTOMER_A, list(zip(skus, (2, 3), strict=True)), DRAFT_NOTE)
        before = snapshot_all(engine)
        if budget is not None and budget.exhausted:
            turns.append(CaseResult(case_id=turn.id, category=turn.category, passed=False,
                                    failed_checks=["not_run: 多轮任务预算已尽，本轮未执行"]))
            break
        result, trace = await run_case(
            turn, client=client, tools=tools, resolved=resolved, trace_dir=trace_dir,
            system_prompt=prompt, state_engine=engine, conversation=conversation, retries=0,
        )
        if budget is not None and not isinstance(client, BudgetedClient):
            budget.record(result.cost)
        after = snapshot_all(engine)
        result.failed_checks.extend(check_task_turn(
            turn, result, before, after, session_id=session_id, skus=skus,
        ))
        if isinstance(turn, ReferenceTurn):
            result.failed_checks.extend(check_reference_turn(turn, result, resolved=resolved))
        result.passed = not result.failed_checks
        result.state_evidence = {
            "before_sha256": _digest(before), "after_sha256": _digest(after),
            "drafts_before": before["order_drafts"], "drafts_after": after["order_drafts"],
            "history_reset": turn.reset_history, "external_revision": turn.refresh_before,
        }
        turns.append(result)
        traces.append(str(trace.resolve()))
        if not result.passed:
            break  # 不用后续轮次掩盖先前失败，不重放草稿变更。
    failed = [f"{r.case_id}: {check}" for r in turns for check in r.failed_checks]
    if len(turns) != len(case.turns):
        failed.append(f"b4_turns_incomplete: 执行 {len(traces)}/{len(case.turns)} 轮")
    costs = [r.cost for r in turns if r.cost is not None]
    estimates = [r.billing for r in turns if r.billing is not None]
    return CaseResult(
        case_id=case.id, category=case.category, passed=not failed, failed_checks=failed,
        completed=len(traces) == len(case.turns) and all(r.completed for r in turns),
        steps=sum(r.steps for r in turns), tool_calls=[n for r in turns for n in r.tool_calls],
        tool_results=[t for r in turns for t in r.tool_results],
        total_tokens=sum(r.total_tokens for r in turns), cost=sum(costs) if costs else None,
        cost_complete=all(r.cost_complete for r in turns),
        billing=_merge_estimates(estimates) if estimates else None,
        duration_ms=sum(r.duration_ms for r in turns), error=next(
            (r.error for r in turns if r.error is not None), None,
        ), turn_results=turns, turn_traces=traces,
    ), Path(traces[0]) if traces else Path()
