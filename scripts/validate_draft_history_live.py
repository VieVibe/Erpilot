"""Explicitly synthetic history fixtures; real model choice, MCP calls and answer.

Only the remaining budget from a completed, fully metered workflow batch is used.
An interrupted or unknown-usage batch cannot authorize more requests here.
"""

import argparse
import asyncio
import hashlib
import json
import tempfile
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path

from agent_core.approval import AutoDenyGate
from agent_core.demo_tools import system_prompt
from agent_core.dotenv import find_dotenv, load_dotenv
from agent_core.llm import LLMClient, LLMConfig
from agent_core.runtime_config import LoopConfig
from erp_store.drafts import ErpDrafts
from evals import context_workflow as cw
from evals.billing import BudgetedClient, CurrencyBudget, endpoint_rate
from evals.campaign import _save, request_totals
from evals.model import CaseCategory, EvalCase
from evals.runner import run_case
from mcp_erp import build_agent_tools_async
from mcp_erp.draft_constraints import validate_draft_arguments
from mcp_erp.draft_tools import DRAFT_GUIDANCE
from openai import AsyncOpenAI

VERSION = "draft-history-live-2"
MUTATIONS = (*cw.FORBIDDEN, "prepare_order_draft", "revise_order_draft",
             "revise_order_draft_quantity", "prepare_cancel_order", "prepare_stock_adjustment")
CREATE = f"给记录验收客户甲保存报价草稿：{cw.SKUS[0]} 2件；备注原文「按原要求保留」"
CHANGE = "把当前版本第一行改成5件，其他要求保留。"


def fixture(path):
    # The three revisions are fixtures, not fabricated model dialogue or results.
    engine = cw.seed_workflow(path, cw.Workflow("history-fixture", ()), "history-live")
    drafts = ErpDrafts(engine)
    args = {"customer": "记录验收客户甲", "note": "按原要求保留",
            "items": [{"sku": cw.SKUS[0], "quantity": 2}]}
    proof = validate_draft_arguments("prepare_order_draft", args,
                                     [{"role": "user", "content": CREATE}], [])
    first = drafts.prepare("history-live", args["customer"], [(cw.SKUS[0], 2)], args["note"],
                           evidence=proof)
    args = {"draft_id": first["draft_id"], "expected_revision": 1,
            "item_number": 1, "quantity": 5, "expected_sku": cw.SKUS[0]}
    proof = validate_draft_arguments(
        "revise_order_draft_quantity", args, [{"role": "user", "content": CHANGE}],
        [{"name": "get_order_task", "ok": True,
          "content": json.dumps(drafts.task_state("history-live"), ensure_ascii=False)}],
    )
    drafts.revise_quantity("history-live", first["draft_id"], 1, 1, 5,
                           expected_sku=cw.SKUS[0], evidence=proof)
    drafts.revise("history-live", first["draft_id"], 2, "记录验收客户乙", [(cw.SKUS[0], 5)], None,
                  evidence={"contract": "draft-form-1"})
    foreign = drafts.prepare("other-history-session", "隔离测试客户", [(cw.SKUS[0], 3)],
                             "隔离测试备注")
    return engine, first["draft_id"], foreign["draft_id"]


def cases(draft_id, foreign_id):
    return [
        EvalCase(
            id="history-live-pages", category=CaseCategory.MULTI,
            question=(f"只读查询草稿 {draft_id} 的全部修改记录。先查询当前任务；"
                      "历史工具每次 limit=1，按返回游标读取更早版本，直到没有更多。"
                      "不修改草稿、不建单。最终仅输出JSON对象，字段为："
                      "revision_count、original_customer、original_note、"
                      "second_quantity_before、second_quantity_after、latest_customer、"
                      "latest_note、latest_source、original_user_request。"
                      "数量和版本数为数字；latest_source复制工具中的source.kind；"
                      "original_user_request复制第1版来源的完整user_request。"),
            points="Model reads all real MCP history pages; fixture revisions are explicit.",
            expect_tools_all=["get_order_task", "get_order_draft_history"],
            expect_successful_tools=["get_order_task", "get_order_draft_history"],
            forbid_tools=list(MUTATIONS), max_steps=7,
        ),
        EvalCase(
            id="history-live-foreign", category=CaseCategory.EDGE,
            question=(f"只读查询草稿 {foreign_id} 的修改记录。仅输出JSON对象，"
                      "字段available为布尔值；不存在或不属于当前会话时为false。"
                      "不要输出其他字段，不创建或修改任何草稿。"),
            points="Foreign history fails without leaking synthetic private fields.",
            expect_tools_all=["get_order_draft_history"], expect_error_codes=["not_found"],
            must_not_mention=["隔离测试客户", "隔离测试备注"],
            forbid_tools=list(MUTATIONS), max_steps=5,
        ),
    ]


def check_result(case_id, result, before, after, final_answer=None):
    checks = []
    if before != after:
        checks.append("all_tables_must_be_unchanged")
    if set(result.tool_attempts or []) & set(MUTATIONS):
        checks.append("mutation_requested")
    text = (result.visible_text if final_answer is None else final_answer).strip()
    if text.startswith("```json\n") and text.endswith("```"):
        text = text[8:-3].strip()
    try:
        answer = json.loads(text)
    except ValueError:
        answer = None
    expected = ({"revision_count": 3, "original_customer": "记录验收客户甲",
                 "original_note": "按原要求保留", "second_quantity_before": 2,
                 "second_quantity_after": 5, "latest_customer": "记录验收客户乙",
                 "latest_note": None, "latest_source": "manual_form",
                 "original_user_request": CREATE} if case_id == "history-live-pages"
                else {"available": False})
    if (not isinstance(answer, dict) or set(answer) != set(expected)
            or any(type(answer[key]) is not type(value) or answer[key] != value
                   for key, value in expected.items())):
        checks.append("exact_visible_answer")
    if case_id == "history-live-pages":
        pages = [r for r in result.tool_results
                 if r.name == "get_order_draft_history" and r.succeeded]
        versions = [item["revision"] for page in pages for item in page.content.get("items", [])]
        if (versions != [3, 2, 1] or any(r.arguments.get("limit") != 1 for r in pages)
                or [r.arguments.get("before_revision") for r in pages] != [None, 3, 2]):
            checks.append("complete_ordered_exclusive_pagination")
    return checks


async def run(config, previous_path, output, *, client_override=None):
    previous = json.loads(previous_path.read_bytes())
    if previous["model"] != config.model:
        raise ValueError("previous model mismatch")
    if (
        "known_spent" not in previous["budget"]
        or type(previous["budget"].get("unknown_usage")) is not bool
        or Decimal(previous["budget"]["known_spent"])
        != Decimal(previous["journal_audit"]["known_spent"])
    ):
        raise ValueError("previous batch is not closed and settled")
    limit = Decimal(previous["budget"]["limit"])
    spent = Decimal(previous["journal_audit"]["known_spent"])
    remaining = limit - spent
    if previous["budget"].get("unknown_usage") or previous["journal_audit"]["unknown_requests"]:
        raise ValueError("previous batch has unknown usage; no further requests permitted")
    if remaining <= 0:
        raise ValueError("previous budget exhausted")
    source = cw.source_fingerprint()
    if source["sha256"] != previous["source"]["sha256"]:
        raise ValueError("source changed after workflow batch")
    output.parent.mkdir(parents=True, exist_ok=True)
    journal = output.with_name(output.name + "-requests.jsonl")
    inputs = output.with_name(output.name + "-inputs.jsonl")
    if any(path.exists() for path in (journal, inputs, output.with_suffix(".json"))):
        raise ValueError("batch already exists")
    journal.touch(exist_ok=False)
    rate = endpoint_rate(config.model, config.base_url)
    if rate.currency != previous["budget"]["currency"]:
        raise ValueError("currency mismatch")
    budget = CurrencyBudget(remaining, rate.currency, max_requests=16)
    report = {"version": VERSION, "model": config.model, "source": source,
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "previous_batch": str(previous_path), "timeout": 60, "thinking": config.thinking,
              "rate": {key: str(value) for key, value in asdict(rate).items()},
              "budget": {"currency": rate.currency, "limit": str(remaining),
                         "previous_spent": str(spent), "combined_limit": str(limit)},
              "case_scope_sha256": cw.digest([
                  case.model_dump(mode="json") for case in cases("fixture-draft", "fixture-foreign")
              ]),
              "results": [], "complete": False, "fixture_revisions": True}
    _save(output.with_suffix(".json"), report)
    async with AsyncOpenAI(api_key=config.api_key, base_url=config.base_url,
                           timeout=60, max_retries=0) as sdk:
        raw = client_override or LLMClient(config, client=sdk, request_timeout=60)
        recorded = cw.RecordedClient(raw, inputs, budget, source)
        client = BudgetedClient(recorded, rate, budget,
                                request_observer=lambda row: cw.append_record(journal, row))
        with tempfile.TemporaryDirectory(prefix="erpilot-live-history-") as directory:
            path = Path(directory) / "erp.db"
            engine, draft_id, foreign_id = fixture(path)
            try:
                report["cases"] = [
                    case.model_dump(mode="json") for case in cases(draft_id, foreign_id)
                ]
                _save(output.with_suffix(".json"), report)
                tools = await build_agent_tools_async(path, session_id="history-live", writes=True,
                                                      approval_gate=AutoDenyGate())
                for case in cases(draft_id, foreign_id):
                    if budget.exhausted:
                        break
                    before = cw.snapshot_all(engine)
                    conversation = [{"role": "system", "content":
                                     system_prompt(True) + "\n" + DRAFT_GUIDANCE}]
                    result, trace = await run_case(
                        case, client=client, tools=tools, resolved={}, retries=0,
                        trace_dir=cw.ROOT / "traces" / "history-live" / output.name,
                        loop_config=LoopConfig(max_steps=case.max_steps or 7),
                        system_prompt=system_prompt(True) + "\n" + DRAFT_GUIDANCE,
                        conversation=conversation,
                    )
                    after = cw.snapshot_all(engine)
                    final_answer = (
                        conversation[-1].get("content") or ""
                        if conversation[-1].get("role") == "assistant" and result.completed
                        else ""
                    )
                    result.failed_checks.extend(check_result(
                        case.id, result, before, after, final_answer,
                    ))
                    result.passed = not result.failed_checks
                    report["results"].append({
                        "case": case.id, "result": result.model_dump(mode="json"),
                        "trace": str(trace), "final_answer": final_answer,
                        "before_sha256": cw.digest(before), "after_sha256": cw.digest(after),
                        "all_tables": sorted(before),
                    })
                    report["journal_audit"] = request_totals(journal)
                    _save(output.with_suffix(".json"), report)
                    print(f"{case.id}: {'PASS' if result.passed else 'FAIL'}; "
                          f"known={budget.spent} {rate.currency}", flush=True)
            finally:
                engine.dispose()
    report["journal_audit"] = request_totals(journal)
    report["budget"].update(known_spent=str(budget.spent), unknown_usage=budget.unknown_usage,
                             combined_known_spent=str(spent + budget.spent))
    report["complete"] = len(report["results"]) == 2 and all(
        row["result"]["completed"] for row in report["results"]
    )
    report["source_consistent"] = source["sha256"] == cw.source_fingerprint()["sha256"]
    _save(output.with_suffix(".json"), report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (path := find_dotenv(Path.cwd())) is not None:
        load_dotenv(path)
    previous = json.loads(args.previous.read_bytes())
    config = replace(LLMConfig.from_env(), model=previous["model"])
    report = asyncio.run(run(config, args.previous, args.output))
    raise SystemExit(0 if report["complete"] and all(
        row["result"]["passed"] for row in report["results"]
    ) and not report["budget"]["unknown_usage"] else 1)


if __name__ == "__main__":
    main()
