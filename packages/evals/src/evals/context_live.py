"""One immutable, metered batch for the frozen historical-context probes.

No ERP writes or embeddings. Each case/policy gets exactly one request; completed
failures are never retried. The journal preserves even an unclosed request.
"""

import argparse
import asyncio
import json
import os
import time
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from agent_core.dotenv import find_dotenv, load_dotenv
from agent_core.llm import LLMClient, LLMConfig, StreamEnd, TextDelta, ToolCall
from openai import AsyncOpenAI

from evals.billing import BudgetedClient, CurrencyBudget, endpoint_rate
from evals.campaign import _save, request_totals
from evals.context_experiment import (
    CASES,
    POLICIES,
    POLICY,
    VERSION,
    digest,
    fixture,
    percentile,
    project,
    protocol_valid,
    run_offline,
    source_fingerprint,
)


def grade(text, expected):
    text = text.strip()
    if text.startswith("```json\n") and text.endswith("```"):
        text = text[8:-3].strip()
    try:
        actual = json.loads(text)
    except ValueError:
        return ["invalid_json"]
    if not isinstance(actual, dict) or set(actual) != set(expected):
        return ["field_schema"]
    return [key for key in expected
            if type(actual[key]) is not type(expected[key]) or actual[key] != expected[key]]


def write_summary(report, output):
    _save(output.with_suffix(".json"), report)
    lines = ["# 历史上下文真实模型对照", "", f"模型：{report['model']}。",
             "每个固定 case / 策略一次请求，未重试失败。只测历史快照答题，不测实时业务执行。",
             "完整性、预算、源码一致性与答题通过分别记录；full 为不裁剪的参考输入。", "",
             "| 策略 | 已执行/计划 | 答题通过 | 输入 token | 输出 token | p50/p95 秒 | 估价 |",
             "|---|---:|---:|---:|---:|---|---|"]
    for strategy in POLICIES:
        rows = [r for r in report["results"] if r["strategy"] == strategy]
        durations = [r["duration_ms"] / 1000 for r in rows]
        known = sum((Decimal(r["billing"]["estimated_amount"]) for r in rows
                     if r.get("billing") and r["billing"]["estimated_amount"] is not None),
                    Decimal(0))
        median, tail = percentile(durations, .5), percentile(durations, .95)
        timing = f"{median:.2f}/{tail:.2f}" if durations else "未执行"
        lines.append(f"| {strategy} | {len(rows)}/{len(CASES)} | "
                     f"{sum(r['passed'] for r in rows)} | "
                     f"{sum((r.get('usage') or {}).get('prompt_tokens', 0) for r in rows)} | "
                     f"{sum((r.get('usage') or {}).get('completion_tokens', 0) for r in rows)} | "
                     f"{timing} | {known} {report['budget']['currency']} |")
    lines += ["", f"完整执行：{report['complete']}；源码一致：{report['source_consistent']}。",
              f"请求审计：`{report['journal_audit']}`。", "", "## 失败保留", ""]
    lines += [f"- {r['case_id']} / {r['strategy']}：{r['failed_checks']}。"
              for r in report["results"] if not r["passed"]]
    if report.get("stop_reason"):
        lines.append(f"- 停止原因：{report['stop_reason']}。")
    lines += ["", "预算为原币种估价熔断线，缺失 usage 不计零；不核销旧批次未知费用。",
              "全部输入与可见回答保存在本批 JSON 和 inputs.json；没有通用语义裁判。",
              f"用例指纹：`{report['cases_sha256']}`。",
              f"相关源码指纹：`{report['source']['sha256']}`。"]
    output.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def run_live(config, *, limit, currency, output, timeout=60):
    rate = endpoint_rate(config.model, config.base_url)
    if rate.currency != currency:
        raise ValueError("budget currency differs from confirmed rate")
    offline = run_offline()
    plan = [(case, strategy) for case in CASES for strategy in POLICIES]
    inputs = [{"case_id": c.id, "strategy": s, "messages": project(fixture(c)[0], s)}
              for c, s in plan]
    output.parent.mkdir(parents=True, exist_ok=True)
    journal = output.with_name(output.name + "-requests.jsonl")
    inputs_path = output.with_name(output.name + "-inputs.json")
    for path in (output.with_suffix(".json"), output.with_suffix(".md"), journal, inputs_path):
        if path.exists():
            raise ValueError("batch already exists; results are immutable, use a new name")
    # Exclusive reservation also prevents another process from starting this batch.
    with journal.open("x", encoding="utf-8"):
        pass
    _save(inputs_path, {"version": VERSION, "inputs": inputs})
    budget = CurrencyBudget(limit, currency, max_requests=len(plan))
    report = {"version": VERSION, "mode": "live-historical-context-probes",
              "model": config.model, "endpoint_host": urlsplit(config.base_url).hostname,
              "thinking": config.thinking, "timeout": timeout, "sdk_retries": 0,
              "cases_sha256": offline["cases_sha256"], "source": offline["source"],
              "policy": asdict(POLICY), "rate": {k: str(v) for k, v in asdict(rate).items()},
              "budget": {"limit": str(limit), "currency": currency},
              "complete": False, "source_consistent": True, "results": [],
              "journal_audit": {"requests": 0, "known_spent": "0", "unknown_requests": 0}}
    active_input = None

    def observe(event):
        with journal.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({**event, "input_sha256": digest(active_input)},
                                    ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    write_summary(report, output)
    async with AsyncOpenAI(api_key=config.api_key, base_url=config.base_url,
                           timeout=timeout, max_retries=0) as sdk:
        client = BudgetedClient(LLMClient(config, client=sdk, request_timeout=timeout),
                                rate, budget, request_observer=observe)
        for (case, strategy), request in zip(plan, inputs, strict=True):
            if source_fingerprint()["sha256"] != report["source"]["sha256"]:
                report.update(source_consistent=False, stop_reason="source_changed")
                break
            if budget.exhausted:
                report["stop_reason"] = budget.reason()
                break
            active_input = request["messages"]
            if not protocol_valid(active_input):
                report["stop_reason"] = "invalid_input_tool_protocol"
                break
            client.begin_case(f"{case.id}:{strategy}")
            started = time.perf_counter()
            text, usage, calls, error = "", None, [], None
            try:
                async for event in client.stream_chat(active_input):
                    if isinstance(event, TextDelta):
                        text += event.text
                    elif isinstance(event, StreamEnd):
                        usage = asdict(event.usage) if event.usage else None
                    elif isinstance(event, ToolCall):
                        calls.append(asdict(event))
            except Exception as exc:
                # Exception bodies may echo credentials; only record the class.
                error = type(exc).__name__
            expected = fixture(case)[1]
            failed = grade(text, expected)
            if error:
                failed.append(f"upstream:{error}")
            if calls:
                failed.append("forbidden_tool_request")
            if usage is None:
                failed.append("unknown_usage")
            billing = client.case_billing()
            report["results"].append({
                "case_id": case.id, "strategy": strategy, "passed": not failed,
                "failed_checks": failed, "visible_text": text, "expected": expected,
                "usage": usage, "billing": billing.model_dump() if billing else None,
                "duration_ms": (time.perf_counter() - started) * 1000,
                "tool_attempts": calls, "input_sha256": digest(active_input),
            })
            report["journal_audit"] = request_totals(journal)
            report["complete"] = len(report["results"]) == len(plan)
            write_summary(report, output)
            print(f"{case.id}/{strategy}: {'PASS' if not failed else 'FAIL'}; "
                  f"known={budget.spent} {currency}", flush=True)
    report["source_consistent"] &= source_fingerprint()["sha256"] == report["source"]["sha256"]
    report["journal_audit"] = request_totals(journal)
    report["budget"]["unknown_usage"] = budget.unknown_usage
    report["budget"]["known_spent"] = str(budget.spent)
    write_summary(report, output)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--budget", type=Decimal, required=True)
    parser.add_argument("--budget-currency", choices=("CNY", "USD"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    if args.timeout <= 0 or args.budget <= 0 or not args.budget.is_finite():
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
