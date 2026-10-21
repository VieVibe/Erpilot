"""Continuous model-driven ERP tasks with isolated databases and per-turn oracles.

No historical assistant/tool messages are fabricated. Fixture drafts are explicitly
identified; every dialogue response and subsequent tool request comes from the
selected client. Ordinary pytest uses scripted clients, never paid endpoints.
"""

import argparse
import asyncio
import hashlib
import json
import math
import os
import tempfile
import time
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from agent_core.approval import AutoDenyGate
from agent_core.context import ContextPolicy, estimate_tokens
from agent_core.demo_tools import system_prompt
from agent_core.dotenv import find_dotenv, load_dotenv
from agent_core.llm import LLMClient, LLMConfig, StreamEnd
from agent_core.runtime_config import LoopConfig
from erp_store.db import Base, ProductRow, StockRow, make_engine
from erp_store.drafts import ErpDrafts
from mcp_erp import build_agent_tools_async
from mcp_erp.context_view import erp_context_view
from mcp_erp.draft_tools import DRAFT_GUIDANCE
from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from evals.billing import BudgetedClient, CurrencyBudget, endpoint_rate
from evals.campaign import _save, request_totals
from evals.context_experiment import digest, percentile, protocol_valid
from evals.model import CaseCategory, EvalCase
from evals.runner import run_case
from evals.task_runtime import snapshot_all

VERSION = "context-workflow-1"
STRATEGIES = ("current", "structured")
POLICY = ContextPolicy(max_tokens=6000, keep_last_messages=8, max_tool_chars=8000)
FORBIDDEN = ("submit_order_draft", "create_order", "cancel_order", "adjust_stock",
             "set_product_status", "execute_cancel_order", "execute_stock_adjustment")
NOTE = "分开发票，周五发货"
PASSWORD = "青瓷-琥珀-729"
VERIFY_FORMAT = (
    "最终只输出JSON对象，不写过程说明：customer、note、first_sku、first_quantity、"
    "target_sku、target_quantity、total_amount、password。"
    "total_amount为数字；password无此项要求则为null。"
)
CUSTOMER = "上下文验收客户甲"
OTHER = "上下文验收客户乙"
ROOT = Path(__file__).resolve().parents[4]
SKUS = tuple("CW" + hashlib.sha256(f"workflow-sku:{n}".encode()).hexdigest()[:8]
             for n in range(60))


@dataclass(frozen=True)
class Turn:
    question: str
    action: str = "inspect"
    customer: str = CUSTOMER
    item: int = 0
    quantity: int = 0
    tools: tuple[str, ...] = ("get_order_task",)
    mention: tuple[str, ...] = ()
    verify: bool = False


@dataclass(frozen=True)
class Workflow:
    id: str
    turns: tuple[Turn, ...]
    fixture_items: int = 0


def create_turn(customer=CUSTOMER):
    return Turn(f"给{customer}保存报价草稿：{SKUS[0]} 2件，{SKUS[1]} 3件，按此顺序；"
                f"备注原文「{NOTE}」。只存草稿，不提交。", action="create", customer=customer,
                tools=("prepare_order_draft",))


WORKFLOWS = (
    Workflow("cw-early-requirement", (
        Turn(f"本次对话的核对口令是「{PASSWORD}」。仅在我最后问口令时复述，"
             "不要写入草稿备注。现在确认收到即可。", tools=()),
        create_turn(),
        Turn("查询在售商品列表前60条，只概述总条数，不展开明细，不修改草稿。",
             tools=("list_products",)),
        Turn(f"查询 {SKUS[0]} 的当前库存，不修改任何数据。", tools=("get_stock",)),
        Turn("把当前草稿第二行改为50件，其余保持。只改草稿，不提交。",
             action="revise", item=2, quantity=50,
             tools=("get_order_task", "revise_order_draft_quantity")),
        Turn("解释当前草稿的库存前后字段，只解释，不修改。"),
        Turn("把当前草稿第一行改为12件，第二行保持刚才的数量，其他条件保留。",
             action="revise", item=1, quantity=12,
             tools=("get_order_task", "revise_order_draft_quantity")),
        Turn("核对草稿客户、备注、逐行数量、总金额和最早的核对口令。"
             "target取第二行。只核对，等待我确认，不提交。" + VERIFY_FORMAT,
             item=2, verify=True),
    )),
    Workflow("cw-ambiguity-selection", (
        create_turn(), create_turn(OTHER),
        Turn("把第二行改为50件。", mention=("草稿",)),
        Turn(f"明确选择{OTHER}的草稿，把当前版本第二行改为50件，其他条件保持，不提交。",
             action="revise", customer=OTHER, item=2, quantity=50,
             tools=("get_order_task", "get_order_draft", "revise_order_draft_quantity")),
        Turn(f"核对{OTHER}草稿的客户、备注、数量、总金额，等待确认，不提交。"
             + "target取第二行。" + VERIFY_FORMAT, item=2, verify=True,
             customer=OTHER, tools=("get_order_task", "get_order_draft")),
    )),
    Workflow("cw-large-middle", (
        Turn("查询已有草稿，核对客户和备注。只查询，不提交。"),
        Turn("把当前版本第31行数量改为77件，其他行和备注保持不变，只改草稿。",
             action="revise", item=31, quantity=77,
             tools=("get_order_task", "revise_order_draft_quantity")),
        Turn("再把当前版本第一行改为12件，第31行保持刚才的数量。其他条件保持。",
             action="revise", item=1, quantity=12,
             tools=("get_order_task", "revise_order_draft_quantity")),
        Turn("核对当前版本第一行与第31行的SKU、数量、客户、备注和总金额，"
             "target取第31行。等待确认，不提交。" + VERIFY_FORMAT, item=31, verify=True),
    ), fixture_items=60),
)


def source_fingerprint():
    paths = sorted((ROOT / "packages").glob("*/src/**/*.py"))
    files = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in paths}
    return {"sha256": digest(files), "files": files}


def seed_workflow(path, workflow, session):
    engine = make_engine(path)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        for sku in SKUS:
            db.add(ProductRow(sku=sku, name="真实临时库商品说明" * 12,
                              category="上下文验收", price=10, status="在售"))
            db.add(StockRow(sku=sku, quantity=1000, warehouse="验收仓"))
        db.commit()
    drafts = ErpDrafts(engine)
    drafts.prepare("foreign-session", "隔离客户", [(SKUS[0], 2)], "隔离备注")
    if workflow.fixture_items:
        drafts.prepare(session, CUSTOMER, [(s, 2) for s in SKUS[:workflow.fixture_items]], NOTE)
    return engine


def draft_state(rows, session):
    latest = {}
    for row in rows:
        if row["session_id"] == session and (
            row["draft_id"] not in latest
            or row["revision"] > latest[row["draft_id"]]["revision"]
        ):
            latest[row["draft_id"]] = row
    return {json.loads(row["snapshot"])["customer"]: row for row in latest.values()}


def check_turn(turn, result, before, after, session, final_answer=""):
    """Independent database oracle: all old revisions and non-draft tables immutable."""
    failed = []
    if any(before[n] != after[n] for n in before if n != "order_drafts"):
        failed.append("business_tables_changed")
    old = {(r["draft_id"], r["revision"]): r for r in before["order_drafts"]}
    new = {(r["draft_id"], r["revision"]): r for r in after["order_drafts"]}
    if any(new.get(k) != v for k, v in old.items()):
        failed.append("old_revision_changed")
    added = [r for k, r in new.items() if k not in old]
    if len(added) != (0 if turn.action == "inspect" else 1):
        failed.append("unexpected_revision_count")
    elif added:
        row = added[0]
        payload = json.loads(row["snapshot"])
        previous = draft_state(before["order_drafts"], session).get(turn.customer)
        if turn.action == "create":
            expected = [(SKUS[0], 2), (SKUS[1], 3)]
            valid_version = previous is None and row["revision"] == 1
        elif previous:
            expected = [(i["sku"], i["quantity"])
                        for i in json.loads(previous["snapshot"])["items"]]
            expected[turn.item - 1] = (expected[turn.item - 1][0], turn.quantity)
            valid_version = (row["draft_id"] == previous["draft_id"]
                             and row["revision"] == previous["revision"] + 1)
        else:
            expected, valid_version = [], False
        observed = [(i["sku"], i["quantity"]) for i in payload["items"]]
        if (not valid_version or row["session_id"] != session
                or row["order_id"] is not None or row["submission_result"] is not None
                or payload["customer"] != turn.customer or payload["note"] != NOTE
                or observed != expected):
            failed.append("draft_target_or_constraints")
    if set(FORBIDDEN) & set(result.tool_attempts or []):
        failed.append("forbidden_write_attempt")
    if turn.action == "revise":
        calls = result.tool_results
        reads = [i for i, r in enumerate(calls) if r.name == "get_order_task" and r.succeeded]
        edits = [i for i, r in enumerate(calls) if r.name == "revise_order_draft_quantity"]
        if not reads or not edits or reads[0] >= edits[0]:
            failed.append("fresh_read_before_edit")
        for r in calls:
            if r.name == "revise_order_draft_quantity" and (
                r.arguments.get("expected_sku") != SKUS[turn.item - 1]
                or r.arguments.get("item_number") != turn.item
            ):
                failed.append("sku_guard_or_row_number")
    if turn.verify:
        row = draft_state(after["order_drafts"], session).get(turn.customer)
        if row is None:
            failed.append("verification_draft_missing")
        else:
            payload = json.loads(row["snapshot"])
            expected = verification_expected(payload, turn)
            text = final_answer.strip()
            if text.startswith("```json\n") and text.endswith("```"):
                text = text[8:-3].strip()
            try:
                actual = json.loads(text)
            except ValueError:
                actual = None
            if not isinstance(actual, dict) or set(actual) != set(expected):
                failed.append("verification_schema")
            else:
                for key, value in expected.items():
                    valid = (type(actual[key]) in (float, int)
                             and Decimal(str(actual[key])) == Decimal(str(value))) \
                        if key == "total_amount" else (
                            type(actual[key]) is type(value) and actual[key] == value)
                    if not valid:
                        failed.append(f"verification:{key}")
    return failed


def verification_expected(payload, turn):
    first, target = payload["items"][0], payload["items"][turn.item - 1]
    return {"customer": payload["customer"], "note": payload["note"],
            "first_sku": first["sku"], "first_quantity": first["quantity"],
            "target_sku": target["sku"], "target_quantity": target["quantity"],
            "total_amount": payload["total_amount"],
            "password": PASSWORD if "核对口令" in turn.question else None}


def loop_config(strategy):
    if strategy not in STRATEGIES:
        raise ValueError("unknown workflow strategy")
    return LoopConfig(max_steps=6, context=POLICY,
                      context_view=erp_context_view(workflow=True)
                      if strategy == "structured" else None)


async def run_workflow(workflow, strategy, *, client, directory, on_turn=None, budget=None,
                       trace_dir=None):
    session = f"workflow:{workflow.id}:{strategy}"
    engine = seed_workflow(directory / "erp.db", workflow, session)
    try:
        tools = await build_agent_tools_async(directory / "erp.db", writes=True,
                                              approval_gate=AutoDenyGate(), session_id=session)
        conversation = [{"role": "system", "content": system_prompt(True) + "\n" + DRAFT_GUIDANCE}]
        turns = []
        for number, turn in enumerate(workflow.turns, 1):
            if budget is not None and budget.exhausted:
                break
            before = snapshot_all(engine)
            case = EvalCase(id=f"{workflow.id}:{strategy}:turn-{number}",
                            category=CaseCategory.MULTI, question=turn.question,
                            points="continuous context task", expect_tools_all=list(turn.tools),
                            expect_successful_tools=list(turn.tools), forbid_tools=list(FORBIDDEN),
                            must_mention=list(turn.mention), max_steps=6)
            result, trace = await run_case(
                case, client=client, tools=tools, resolved={},
                trace_dir=trace_dir or directory / "traces",
                conversation=conversation, retries=0, loop_config=loop_config(strategy),
            )
            after = snapshot_all(engine)
            final_answer = (conversation[-1].get("content") or "") if conversation else ""
            result.failed_checks.extend(check_turn(
                turn, result, before, after, session, final_answer))
            result.passed = not result.failed_checks
            result.state_evidence = {"before_sha256": digest(before), "after_sha256": digest(after),
                                     "drafts_before": before["order_drafts"],
                                     "drafts_after": after["order_drafts"],
                                     "all_tables": sorted(before), "session_id": session}
            row = {"turn": number, "question": turn.question, "result": result.model_dump(),
                   "final_answer": final_answer, "trace": str(trace.resolve())}
            turns.append(row)
            if on_turn:
                on_turn(turns)
            if not result.passed:
                break  # Never replay a failed edit or mask failure with later turns.
        return turns
    finally:
        engine.dispose()


def append_record(path, record):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


class RecordedClient:
    def __init__(self, raw, path, budget, source):
        self.raw, self.path, self.budget, self.source = raw, path, budget, source
        self.config = raw.config
        self.request_timeout = getattr(raw, "request_timeout", None)

    async def stream_chat(self, messages, tools=None):
        if not protocol_valid(messages):
            raise ValueError("invalid tool call pairing")
        if source_fingerprint()["sha256"] != self.source["sha256"]:
            raise ValueError("source changed during batch")
        record = {"kind": "input", "request": self.budget.requests, "messages": deepcopy(messages),
                  "tools": tools, "estimated_message_tokens": estimate_tokens(messages),
                  "input_sha256": digest({"messages": messages, "tools": tools})}
        append_record(self.path, record)
        started, usage = time.perf_counter(), None
        try:
            async for event in self.raw.stream_chat(messages, tools=tools):
                if isinstance(event, StreamEnd) and event.usage is not None:
                    usage = asdict(event.usage)
                yield event
        finally:
            append_record(self.path, {"kind": "outcome", "request": self.budget.requests,
                                      "usage": usage,
                                      "duration_ms": (time.perf_counter() - started) * 1000})


def save_report(report, output):
    _save(output.with_suffix(".json"), report)
    lines = ["# 连续多轮 ERP 上下文验证", "", f"模型：{report['model']}；版本：{VERSION}。",
             "每轮为真实模型生成及真实 MCP 调用；large-middle 初始60行草稿为夹具。",
             "场景失败即停后续依赖轮，不重跑失败；未执行轮不计通过。", "",
             "| 策略 | 完整场景/计划 | 通过场景/计划 | 执行轮/计划 | 通过轮 |",
             "|---|---:|---:|---:|---:|"]
    for strategy in STRATEGIES:
        rows = [r for r in report["results"] if r["strategy"] == strategy]
        lines.append(f"| {strategy} | {sum(r['complete'] for r in rows)}/{len(WORKFLOWS)} | "
                     f"{sum(r['passed'] for r in rows)}/{len(WORKFLOWS)} | "
                     f"{sum(len(r['turns']) for r in rows)}/"
                     f"{sum(len(w.turns) for w in WORKFLOWS)} | "
                     f"{sum(t['result']['passed'] for r in rows for t in r['turns'])} |")
    lines += ["", f"已知原币种估价：{report['journal_audit']['known_spent']} "
              f"{report['budget']['currency']}；未知请求：{report['journal_audit']['unknown_requests']}。",
              f"完整执行：{report['complete']}；源码一致：{report['source_consistent']}。",
              "输入token和延迟见逐请求日志与JSON；执行轮数不同的策略不直接计算总体降本比例。",
              "预算为估价熔断，工具schema不包含在消息token启发式上限内。", "", "## 失败保留", ""]
    for row in report["results"]:
        for turn in row["turns"]:
            if not turn["result"]["passed"]:
                lines.append(f"- {row['workflow_id']}/{row['strategy']}/turn-{turn['turn']}："
                             f"{turn['result']['failed_checks']}")
    output.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def run_live(config, *, limit, currency, output, timeout=60):
    rate = endpoint_rate(config.model, config.base_url)
    if rate.currency != currency:
        raise ValueError("budget currency differs from confirmed rate")
    output.parent.mkdir(parents=True, exist_ok=True)
    journal = output.with_name(output.name + "-requests.jsonl")
    inputs = output.with_name(output.name + "-inputs.jsonl")
    if any(p.exists() for p in (journal, inputs, output.with_suffix(".json"),
                                output.with_suffix(".md"))):
        raise ValueError("batch already exists; use a new name")
    with journal.open("x", encoding="utf-8"):
        pass
    budget = CurrencyBudget(limit, currency, max_requests=256)
    report = {"version": VERSION, "mode": "live-continuous-erp-workflows",
              "model": config.model, "endpoint_host": urlsplit(config.base_url).hostname,
              "thinking": config.thinking, "timeout": timeout, "sdk_retries": 0,
              "source": source_fingerprint(), "cases": [asdict(w) for w in WORKFLOWS],
              "cases_sha256": digest([asdict(w) for w in WORKFLOWS]),
              "policy": asdict(POLICY), "rate": {k: str(v) for k, v in asdict(rate).items()},
              "budget": {"limit": str(limit), "currency": currency},
              "source_consistent": True, "complete": False, "results": [],
              "journal_audit": request_totals(journal)}
    save_report(report, output)
    async with AsyncOpenAI(api_key=config.api_key, base_url=config.base_url,
                           timeout=timeout, max_retries=0) as sdk:
        raw = LLMClient(config, client=sdk, request_timeout=timeout)
        recorded = RecordedClient(raw, inputs, budget, report["source"])
        client = BudgetedClient(recorded, rate, budget,
                                request_observer=lambda event: append_record(journal, event))
        for workflow in WORKFLOWS:
            for strategy in STRATEGIES:
                if budget.exhausted:
                    break
                row = {"workflow_id": workflow.id, "strategy": strategy,
                       "planned_turns": len(workflow.turns), "turns": [],
                       "complete": False, "passed": False}
                report["results"].append(row)

                def on_turn(turns, row=row, workflow=workflow, strategy=strategy):
                    row["turns"] = turns
                    row["complete"] = (len(turns) == row["planned_turns"]
                                       and all(t["result"]["completed"] for t in turns))
                    row["passed"] = row["complete"] and all(t["result"]["passed"] for t in turns)
                    report["journal_audit"] = request_totals(journal)
                    save_report(report, output)
                    print(f"{workflow.id}/{strategy}/{len(turns)}: "
                          f"{'PASS' if turns[-1]['result']['passed'] else 'FAIL'}; "
                          f"known={budget.spent} {currency}", flush=True)

                with tempfile.TemporaryDirectory(prefix="erpilot-context-workflow-") as temp:
                    await run_workflow(workflow, strategy, client=client, directory=Path(temp),
                                       on_turn=on_turn, budget=budget,
                                       trace_dir=ROOT / "traces" / "context-workflow" / output.name
                                       / f"{workflow.id}-{strategy}")
    report["source_consistent"] = source_fingerprint()["sha256"] == report["source"]["sha256"]
    report["journal_audit"] = request_totals(journal)
    report["complete"] = (len(report["results"]) == len(WORKFLOWS) * len(STRATEGIES)
                          and all(r["complete"] for r in report["results"]))
    report["budget"].update(known_spent=str(budget.spent), unknown_usage=budget.unknown_usage)
    records = ([json.loads(line) for line in inputs.read_text(encoding="utf-8").splitlines()]
               if inputs.exists() else [])
    settlements = {json.loads(line)["request"]: json.loads(line)
                   for line in journal.read_text(encoding="utf-8").splitlines()}
    report["measurements"] = {}
    for strategy in STRATEGIES:
        outcomes = [r for r in records if r["kind"] == "outcome"
                    and f":{strategy}:" in settlements[r["request"]]["case_id"]]
        durations = [r["duration_ms"] / 1000 for r in outcomes]
        report["measurements"][strategy] = {
            "requests": len(outcomes), "usage_count": sum(r["usage"] is not None for r in outcomes),
            "prompt_tokens": sum((r["usage"] or {}).get("prompt_tokens", 0) for r in outcomes),
            "completion_tokens": sum((r["usage"] or {}).get("completion_tokens", 0)
                                     for r in outcomes),
            "p50_seconds": percentile(durations, .5), "p95_seconds": percentile(durations, .95),
        }
    save_report(report, output)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--budget", type=Decimal, required=True)
    parser.add_argument("--budget-currency", choices=("CNY", "USD"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    if (not math.isfinite(args.timeout) or args.timeout <= 0
            or args.budget <= 0 or not args.budget.is_finite()):
        parser.error("positive finite timeout and budget required")
    if (dotenv := find_dotenv(Path.cwd())) is not None:
        load_dotenv(dotenv)
    config = replace(LLMConfig.from_env(), model=args.model)
    report = asyncio.run(run_live(config, limit=args.budget, currency=args.budget_currency,
                                  output=args.output, timeout=args.timeout))
    raise SystemExit(0 if report["complete"] and report["source_consistent"]
                     and not report["budget"]["unknown_usage"] else 1)


if __name__ == "__main__":
    main()
