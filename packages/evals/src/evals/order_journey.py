"""Whole-order acceptance with real runtime/MCP and explicit human decisions.

All data is synthetic. External price/revision edits and runtime re-creation are
declared controls, never attributed to the model. No automatic approval in apps.
"""

import argparse
import asyncio
import json
import math
import tempfile
import time
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from pathlib import Path

from agent_core.approval import StreamApprovalGate
from agent_core.demo_tools import system_prompt
from agent_core.dotenv import find_dotenv, load_dotenv
from agent_core.events import ApprovalPending
from agent_core.graph_runtime import LangGraphRuntime
from agent_core.llm import LLMClient, LLMConfig
from agent_core.trace import JsonlTraceRecorder
from erp_store.db import ProductRow
from langgraph.checkpoint.memory import InMemorySaver
from mcp_erp.draft_tools import DRAFT_GUIDANCE
from mcp_erp.session_tools import SessionErpTools
from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from evals import context_workflow as cw
from evals.billing import BudgetedClient, CurrencyBudget, endpoint_rate
from evals.campaign import _save, request_totals

VERSION = "whole-order-1"
CUSTOMER, OTHER, NOTE = "整单验收客户甲", "整单验收客户乙", "分开发票，周五发货"
SESSION = "whole-order"
WRITE_TOOLS = {"prepare_order_draft", "revise_order_draft", "revise_order_draft_quantity",
               "submit_order_draft", "create_order", "cancel_order", "adjust_stock",
               "set_product_status", "execute_cancel_order", "execute_stock_adjustment"}


@dataclass(frozen=True)
class Step:
    action: str
    question: str = ""
    revision: int = 1
    quantities: tuple[int, int] = (2, 3)
    other: bool = False
    ordered: bool = False
    pending: bool = False
    mutation: str | None = None
    error: str | None = None


CREATE = (f"给{CUSTOMER}保存报价草稿：{cw.SKUS[0]} 2件，{cw.SKUS[1]} 3件，按此顺序；"
          f"备注原文「{NOTE}」。只存草稿，不提交。")
SUBMIT = f"确认客户{CUSTOMER}的当前草稿报价，请提交当前版本等待人工审批，不提交其他客户草稿。"
VERIFY = ("只读查询草稿 {draft_id} 的当前结果，即使已提交也查询这个ID，不修改、不提交。"
          "最终只输出JSON对象：customer、note、revision、item_count、first_quantity、"
          "second_quantity、total_amount、status、order_id。数量、版本、行数、金额为数字。")
JOURNEYS = {
    "selection-and-denial": (
        Step("user", CREATE, mutation="prepare_order_draft"),
        Step("user", f"给{OTHER}保存报价草稿：{cw.SKUS[0]} 2件，{cw.SKUS[1]} 3件；"
             f"备注原文「{NOTE}」。只存草稿。", other=True, mutation="prepare_order_draft"),
        Step("user", "把第二个改成50件，先不要提交。", other=True),
        Step("user", f"只修改客户{CUSTOMER}的当前版本第二行数量为50件，其他不变。",
             2, (2, 50), True, mutation="revise_order_draft_quantity"),
        Step("user", f"只修改客户{CUSTOMER}的当前版本第一行数量为12件，其他不变。",
             3, (12, 50), True, mutation="revise_order_draft_quantity"),
        Step("user", SUBMIT, 3, (12, 50), True, pending=True, mutation="submit_order_draft"),
        Step("deny", revision=3, quantities=(12, 50), other=True),
        Step("user", SUBMIT, 3, (12, 50), True, pending=True, mutation="submit_order_draft"),
        Step("approve", revision=3, quantities=(12, 50), other=True, ordered=True),
        Step("verify", VERIFY, 3, (12, 50), True, True),
    ),
    "approval-drift": (
        Step("user", CREATE, mutation="prepare_order_draft"),
        Step("user", SUBMIT, pending=True, mutation="submit_order_draft"),
        Step("external_revision", revision=2, quantities=(5, 3), pending=True),
        Step("approve", revision=2, quantities=(5, 3), error="draft_changed"),
        Step("user", SUBMIT, 2, (5, 3), pending=True, mutation="submit_order_draft"),
        Step("external_price", revision=2, quantities=(5, 3), pending=True),
        Step("approve", revision=2, quantities=(5, 3), error="quote_changed"),
        Step("user", f"刷新客户{CUSTOMER}当前草稿的报价，客户、商品顺序、数量和备注不变。"
             "只刷新，不提交，等我重新确认。", 3, (5, 3), mutation="revise_order_draft"),
        Step("user", SUBMIT, 3, (5, 3), pending=True, mutation="submit_order_draft"),
        Step("approve", revision=3, quantities=(5, 3), ordered=True),
        Step("verify", VERIFY, 3, (5, 3), ordered=True),
    ),
    "restore-pending": (
        Step("user", CREATE, mutation="prepare_order_draft"),
        Step("user", "把当前版本第一行改成12件，其他不变，只改草稿。",
             2, (12, 3), mutation="revise_order_draft_quantity"),
        Step("user", SUBMIT, 2, (12, 3), pending=True, mutation="submit_order_draft"),
        Step("restore", revision=2, quantities=(12, 3), pending=True),
        Step("approve", revision=2, quantities=(12, 3), ordered=True),
        Step("repeat_decision", revision=2, quantities=(12, 3), ordered=True),
        Step("verify", VERIFY, 2, (12, 3), ordered=True),
    ),
}


def current_rows(snapshot):
    return {json.loads(row["snapshot"])["customer"]: row for row in snapshot["order_drafts"]
            if row["session_id"] == SESSION}


def snapshot_all(engine):
    # Order timestamps are converted explicitly before hashing and persisting evidence.
    return json.loads(json.dumps(cw.snapshot_all(engine), ensure_ascii=False, default=str))


def check_step(step, before, after, *, pending, history, answer, price):
    """Independent full-table oracle; intermediate wrong writes cannot be undone silently."""
    failed = []
    own = [r for r in after["order_drafts"] if r["session_id"] == SESSION]
    targets = [r for r in own if json.loads(r["snapshot"])["customer"] == CUSTOMER]
    others = [r for r in own if json.loads(r["snapshot"])["customer"] == OTHER]
    if len(targets) != step.revision or sorted(r["revision"] for r in targets) != list(
        range(1, step.revision + 1)
    ) or len(others) != int(step.other) or len(own) != len(targets) + len(others):
        failed.append("exact_draft_versions")
    latest = max(targets, key=lambda r: r["revision"]) if targets else None
    quote = json.loads(latest["snapshot"]) if latest else {}
    if (quote.get("customer") != CUSTOMER or quote.get("note") != NOTE
            or [(x["sku"], x["quantity"]) for x in quote.get("items", [])]
            != list(zip(cw.SKUS[:2], step.quantities, strict=True))):
        failed.append("target_constraints_and_all_items")
    if any(json.loads(r["snapshot"])["note"] != NOTE or [x["quantity"] for x in
           json.loads(r["snapshot"])["items"]] != [2, 3] for r in others):
        failed.append("other_draft_unchanged")
    old = {(r["draft_id"], r["revision"]): r for r in before["order_drafts"]}
    new = {(r["draft_id"], r["revision"]): r for r in after["order_drafts"]}
    if any(key not in new or new[key]["snapshot"] != r["snapshot"] for key, r in old.items()):
        failed.append("old_snapshot_immutable")
    if any(new.get(key) != r for key, r in old.items() if r["session_id"] != SESSION):
        failed.append("foreign_draft_unchanged")
    fields = quote.get("task_constraints", {}).get("fields", {})
    if not all(fields.get(key, {}).get("verified") is True for key in ("customer", "note")):
        failed.append("verified_origins_preserved")
    if targets and fields != json.loads(min(targets, key=lambda r: r["revision"])["snapshot"]
                                        )["task_constraints"]["fields"]:
        failed.append("original_field_sources_unchanged")
    if bool(pending) != step.pending:
        failed.append("approval_boundary")
    if pending and step.action in {"user", "restore"} and (
        pending.tool != "submit_order_draft" or pending.arguments.get("draft_id")
        != (latest or {}).get("draft_id") or pending.arguments.get("revision") != step.revision
    ):
        failed.append("exact_approved_revision")
    attempts = [r["name"] for r in history if r["name"] in WRITE_TOOLS]
    if pending and step.action == "user":
        attempts.append(pending.tool)
    allowed = {step.mutation} if step.mutation else (
        {"submit_order_draft"} if step.action in {"approve", "deny"} else set()
    )
    if set(attempts) - allowed:
        failed.append("unexpected_mutation_attempt")
    if step.mutation and step.mutation not in attempts:
        failed.append("required_mutation_missing")
    if step.error and not any(step.error in r["content"] for r in history):
        failed.append("expected_revalidation_error")
    for name in set(before) - {"order_drafts", "orders", "order_items", "mutation_requests",
                               "stocks", "products"}:
        if before[name] != after[name]:
            failed.append(f"unrelated_table:{name}")
    if step.action != "external_price" and before["products"] != after["products"]:
        failed.append("products_unchanged")
    if len(after["orders"]) != int(step.ordered):
        failed.append("exact_order_count")
    quantities = dict(zip(cw.SKUS[:2], step.quantities, strict=True))
    for row in after["stocks"]:
        expected = 1000 - (quantities.get(row["sku"], 0) if step.ordered else 0)
        if row["quantity"] != expected:
            failed.append("exact_inventory")
    if step.ordered:
        if not latest or not latest["order_id"] or any(
            r["customer"] != CUSTOMER or r["note"] != NOTE or r["order_id"] != latest["order_id"]
            for r in after["orders"]
        ):
            failed.append("order_binding")
        if [(r["sku"], r["quantity"]) for r in after["order_items"]] != list(quantities.items()):
            failed.append("exact_order_items")
        expected_prices = {cw.SKUS[0]: price, cw.SKUS[1]: 10}
        from erp_store.pricing import price_for_quantity
        if any(r["unit_price"] != price_for_quantity(
            expected_prices[r["sku"]], r["quantity"],
        )[1] for r in after["order_items"]):
            failed.append("order_prices")
    elif after["order_items"] or after["mutation_requests"]:
        failed.append("no_premature_execution")
    if step.action == "verify":
        text = answer.strip()
        if text.startswith("```json\n") and text.endswith("```"):
            text = text[8:-3].strip()
        try:
            actual = json.loads(text)
        except ValueError:
            actual = None
        expected = dict(customer=CUSTOMER, note=NOTE, revision=step.revision, item_count=2,
                        first_quantity=step.quantities[0], second_quantity=step.quantities[1],
                        total_amount=quote.get("total_amount"), status="submitted",
                        order_id=latest["order_id"] if latest else None)
        if actual != expected or not isinstance(actual, dict) or any(
            type(actual[k]) is bool for k in ("revision", "item_count", "first_quantity",
                                             "second_quantity", "total_amount")
        ):
            failed.append("final_visible_facts")
        if not any(r["name"] == "get_order_draft" and r["ok"] for r in history):
            failed.append("final_target_read")
    return failed


async def run_journey(
    name, *, client, directory, trace_dir, on_step=None, steps=None, checker=None,
):
    engine = cw.seed_workflow(directory / "erp.db", cw.Workflow(name, ()), SESSION)
    factory = await SessionErpTools.create(directory / "erp.db", writes=True,
                                            gate=StreamApprovalGate())
    saver, pending, previous_pending, price = InMemorySaver(), None, None, 10

    def runtime():
        return LangGraphRuntime(client, factory.for_session(SESSION), cw.loop_config("structured"),
                                saver, mutation_reconciler=factory.reconciler(SESSION))

    agent = runtime()
    conversation = [{"role": "system", "content": system_prompt(True) + "\n" + DRAFT_GUIDANCE}]
    results = []
    try:
        for number, step in enumerate(JOURNEYS[name] if steps is None else steps, 1):
            started = time.monotonic()
            before = snapshot_all(engine)
            trace = trace_dir / f"{name}-{number}.jsonl"
            error, history, answer, question = None, [], "", step.question
            try:
                if step.action in {"user", "verify"}:
                    if step.action == "verify":
                        question = question.format(
                            draft_id=current_rows(before)[CUSTOMER]["draft_id"],
                        )
                    conversation.append({"role": "user", "content": question})
                    stream = agent.stream(conversation, thread_id=name)
                elif step.action in {"approve", "deny"}:
                    previous_pending = pending
                    stream = agent.resume(name, {"pending_id": pending.pending_id,
                                                "approved": step.action == "approve"})
                else:
                    stream = None
                if stream is not None:
                    pending = None
                    recorder = JsonlTraceRecorder(trace, model=client.config.model)
                    async for event in recorder.record(stream, conversation):
                        if isinstance(event, ApprovalPending):
                            pending = event
                    state = (await agent.get_state(name)).values
                    history = state["tool_history"]
                    answer = state.get("final_answer", "")
                    conversation = state["messages"]
                    if pending is None and not state["completed"]:
                        raise ValueError("runtime stopped without a completed response")
                elif step.action == "external_revision":
                    current = current_rows(before)[CUSTOMER]
                    factory.drafts.revise(
                        SESSION, current["draft_id"], current["revision"], CUSTOMER,
                        list(zip(cw.SKUS[:2], step.quantities, strict=True)), NOTE,
                        evidence={"contract": "draft-form-1"},
                    )
                elif step.action == "external_price":
                    price = 20
                    with Session(engine) as session:
                        session.get(ProductRow, cw.SKUS[0]).price = price
                        session.commit()
                elif step.action == "restore":
                    factory = await SessionErpTools.create(directory / "erp.db", writes=True,
                                                            gate=StreamApprovalGate())
                    agent = runtime()  # New host objects, same explicitly shared checkpoint saver.
                    state = await agent.get_state(name)
                    if (not state.next or state.values["pending_calls"][0]["pending_id"]
                            != pending.pending_id):
                        raise ValueError("restored approval identity mismatch")
                elif step.action == "repeat_decision":
                    try:
                        async for _ in agent.resume(name, {
                            "pending_id": previous_pending.pending_id, "approved": True,
                        }):
                            pass
                    except ValueError as exc:
                        if "stale" not in str(exc):
                            raise
                    else:
                        raise ValueError("duplicate decision was accepted")
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            after = snapshot_all(engine)
            failed = (checker or check_step)(step, before, after, pending=pending, history=history,
                                             answer=answer, price=price)
            if error:
                failed.append("run_error: " + error)
            row = {"step": number, "control": asdict(step), "question": question,
                   "elapsed_seconds": round(time.monotonic() - started, 6),
                   "passed": not failed, "failed_checks": failed, "error": error,
                   "final_answer": answer, "tool_history": history,
                   "pending": asdict(pending) if pending else None, "trace": str(trace.resolve()),
                   "before": before, "after": after,
                   "before_sha256": cw.digest(before), "after_sha256": cw.digest(after)}
            results.append(row)
            if on_step:
                on_step(results)
            if failed:
                break
        return results
    finally:
        engine.dispose()


async def run_live(config, *, limit, currency, output, timeout=60, client_override=None,
                   journeys=None, version=VERSION, checker=None):
    journeys = JOURNEYS if journeys is None else journeys
    rate = endpoint_rate(config.model, config.base_url)
    if rate.currency != currency:
        raise ValueError("currency mismatch")
    output.parent.mkdir(parents=True, exist_ok=True)
    journal = output.with_name(output.name + "-requests.jsonl")
    inputs = output.with_name(output.name + "-inputs.jsonl")
    if any(p.exists() for p in (journal, inputs, output.with_suffix(".json"))):
        raise ValueError("batch already exists")
    journal.touch(exist_ok=False)
    budget = CurrencyBudget(limit, currency, max_requests=160)
    report = {"version": version, "model": config.model, "source": cw.source_fingerprint(),
              "cases": {k: [asdict(s) for s in v] for k, v in journeys.items()},
              "cases_sha256": cw.digest({k: [asdict(s) for s in v] for k, v in journeys.items()}),
              "timeout": timeout, "sdk_retries": 0, "case_retries": 0,
              "rate": {k: str(v) for k, v in asdict(rate).items()},
              "budget": {"currency": currency, "limit": str(limit)},
              "fixture_controls": "Synthetic products; explicit decisions/external edits; shared "
              "in-memory saver across new runtime objects, not a hard process crash.",
              "results": [], "complete": False, "journal_audit": request_totals(journal)}
    _save(output.with_suffix(".json"), report)
    async with AsyncOpenAI(api_key=config.api_key, base_url=config.base_url,
                           timeout=timeout, max_retries=0) as sdk:
        raw = client_override or LLMClient(config, client=sdk, request_timeout=timeout)
        client = BudgetedClient(cw.RecordedClient(raw, inputs, budget, report["source"]), rate,
                                budget, request_observer=lambda e: cw.append_record(journal, e))
        for name, steps in journeys.items():
            if budget.exhausted:
                break
            row = {"journey": name, "planned_steps": len(steps), "steps": [],
                   "complete": False, "passed": False}
            report["results"].append(row)

            def on_step(results, row=row, name=name):
                row["steps"] = results
                row["complete"] = len(results) == row["planned_steps"] and not results[-1]["error"]
                row["passed"] = row["complete"] and all(r["passed"] for r in results)
                report["journal_audit"] = request_totals(journal)
                _save(output.with_suffix(".json"), report)
                print(f"{name}/{len(results)}: {'PASS' if results[-1]['passed'] else 'FAIL'}; "
                      f"known={budget.spent} {currency}", flush=True)

            client.begin_case(name)
            with tempfile.TemporaryDirectory(prefix="erpilot-order-journey-") as temp:
                await run_journey(name, client=client, directory=Path(temp), on_step=on_step,
                                  trace_dir=cw.ROOT / "traces" / "order-journey" / output.name,
                                  steps=steps, checker=checker)
    report["journal_audit"] = request_totals(journal)
    report["budget"].update(known_spent=str(budget.spent), unknown_usage=budget.unknown_usage)
    report["complete"] = len(report["results"]) == len(journeys) and all(
        r["complete"] for r in report["results"]
    )
    report["source_consistent"] = cw.source_fingerprint()["sha256"] == report["source"]["sha256"]
    _save(output.with_suffix(".json"), report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--budget", type=Decimal, required=True)
    parser.add_argument("--budget-currency", choices=("CNY", "USD"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    if not args.budget.is_finite() or args.budget <= 0 or not math.isfinite(
        args.timeout
    ) or args.timeout <= 0:
        parser.error("positive finite budget and timeout required")
    if (path := find_dotenv(Path.cwd())) is not None:
        load_dotenv(path)
    config = replace(LLMConfig.from_env(), model=args.model)
    report = asyncio.run(run_live(config, limit=args.budget, currency=args.budget_currency,
                                  output=args.output, timeout=args.timeout))
    raise SystemExit(0 if report["complete"] and report["source_consistent"] and all(
        r["passed"] for r in report["results"]
    ) and not report["budget"]["unknown_usage"] else 1)


if __name__ == "__main__":
    main()
