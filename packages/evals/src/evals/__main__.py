import argparse
import asyncio
import os
from decimal import Decimal
from pathlib import Path

from agent_core.dotenv import find_dotenv, load_dotenv
from agent_core.llm import LLMClient, LLMConfig
from openai import AsyncOpenAI

from evals.baseline import BASELINE_CASES, run_baseline
from evals.billing import BudgetedClient, CurrencyBudget, endpoint_rate
from evals.gate import (
    REFERENCE_INTEGRITY_CHECKS,
    evaluate_gate,
    load_json,
    load_policy,
    validate_scope,
    write_gate,
)
from evals.grounding_cases import (
    GROUNDING_CASES,
    GROUNDING_CASES_V1,
    STRUCTURED_GROUNDING_CASES,
    STRUCTURED_GROUNDING_CASES_V2,
    STRUCTURED_GROUNDING_CASES_V3,
)
from evals.policy_cases import POLICY_CASES
from evals.replenishment_cases import REPLENISHMENT_CASES
from evals.runner import Budget
from evals.task_cases import TASK_CASES
from evals.task_reference_cases import REFERENCE_CASES


def main() -> None:
    if (dotenv := find_dotenv(Path.cwd())) is not None:
        load_dotenv(dotenv)
    parser = argparse.ArgumentParser(description="固定范围的 Erpilot 完整/定点评测")
    parser.add_argument("--budget", type=float,
                        default=float(os.environ.get("ERPILOT_EVAL_BUDGET", "0.1")))
    parser.add_argument("--budget-currency", default=None, choices=["CNY", "USD"],
                        help="按原币种计费预算运行（缺省保留旧 CNY cost 预算路径）")
    parser.add_argument("--case", action="append", default=[], help="case id，可重复；缺省完整集")
    parser.add_argument("--suite", default="baseline",
                        choices=["baseline", "b4", "b4-reference", "b5", "b6",
                                 "rag-grounding", "rag-grounding-v1", "rag-grounding-structured",
                                 "rag-grounding-structured-v2", "rag-grounding-structured-v3"],
                        help="case 集，rag-grounding 为受控检索下的证据充分性 / 引用验收")
    parser.add_argument("--timeout", type=float, default=30,
                        help="单次请求总截止秒数，包含连接和整个流")
    parser.add_argument("--gate", type=Path, help="评测后执行质量门槛 JSON；用例范围须完全匹配")
    parser.add_argument("--regression-baseline", type=Path,
                        help="同模型旧报告；与 --gate 合用，拦截逐用例退步")
    parser.add_argument("--campaign", type=Path,
                        help="结构化政策验收的持久化批次清单；范围和总预算在首次运行冻结")
    parser.add_argument("--resume-campaign", action="store_true",
                        help="显式续跑 --campaign，只补上游中断或尚未执行的用例")
    parser.add_argument("--resume-with-unknown", action="store_true",
                        help="保留旧未知费用并启动新分段；不解除旧分段冻结")
    parser.add_argument("--segment-cases", type=int, help="本分段最多执行的用例数")
    parser.add_argument("--segment-budget", type=Decimal, help="本分段估价预算，不超过批次剩余额度")
    args = parser.parse_args()
    suite_cases = {"baseline": BASELINE_CASES, "b4": TASK_CASES,
                   "b4-reference": REFERENCE_CASES, "b5": POLICY_CASES,
                   "b6": REPLENISHMENT_CASES, "rag-grounding": GROUNDING_CASES,
                   "rag-grounding-v1": GROUNDING_CASES_V1,
                   "rag-grounding-structured": STRUCTURED_GROUNDING_CASES,
                   "rag-grounding-structured-v2": STRUCTURED_GROUNDING_CASES_V2,
                   "rag-grounding-structured-v3": STRUCTURED_GROUNDING_CASES_V3}[args.suite]
    cases = [c for c in suite_cases if not args.case or c.id in args.case]
    unknown = set(args.case) - {c.id for c in cases}
    if unknown or args.budget <= 0 or args.timeout <= 0:
        parser.error(f"未知 case {sorted(unknown)} 或预算/超时非正数")
    if args.regression_baseline and not args.gate:
        parser.error("--regression-baseline requires --gate")
    if args.campaign:
        if (args.suite not in {"rag-grounding-structured", "rag-grounding-structured-v2",
                              "rag-grounding-structured-v3"}
                or not args.budget_currency or args.gate):
            parser.error(
                "--campaign requires a structured grounding suite, native currency, and no --gate"
            )
    elif (args.resume_campaign or args.resume_with_unknown or args.segment_cases is not None
          or args.segment_budget is not None):
        parser.error("campaign continuation/segment options require --campaign")
    if args.resume_with_unknown and not args.resume_campaign:
        parser.error("--resume-with-unknown requires --resume-campaign")
    gate_policy = None
    reference = None
    try:
        if args.gate:
            gate_policy = load_policy(args.gate)
            validate_scope(gate_policy, [case.id for case in cases])
            if args.regression_baseline:
                reference = load_json(args.regression_baseline)
                reference_gate = evaluate_gate(reference, gate_policy)
                invalid = [check["code"] for check in reference_gate["checks"]
                           if not check["passed"] and check["code"] in REFERENCE_INTEGRITY_CHECKS]
                if invalid:
                    raise ValueError(f"regression baseline lacks required evidence: {invalid}")
    except (OSError, ValueError) as exc:
        parser.error(f"Invalid gate input: {exc}")
    config = LLMConfig.from_env()
    # 单层重试：runner 可见每次尝试，避免 SDK 隐式重试扩大耗时与费用。
    sdk_timeout = {"timeout": args.timeout, "max_retries": 0}
    harness = None
    if args.suite in {"rag-grounding", "rag-grounding-v1", "rag-grounding-structured",
                      "rag-grounding-structured-v2", "rag-grounding-structured-v3"}:
        from evals.grounding_runtime import build_grounding_harness

        harness = build_grounding_harness()
    if args.suite == "b5":
        # 真实 B5 运行装配：磁盘真实索引 + 固定查询一次嵌入（跨运行共享）、
        # unavailable/conflict 受控故障、injected-result 工具响应投毒。
        from evals.policy_runtime import build_b5_harness

        harness = build_b5_harness(Path("traces/evals"))
    if args.budget_currency:
        # 指定币种后先解析价目并校验币种，未知价目早退出；不发任何模型请求。
        rate = endpoint_rate(config.model, config.base_url)
        if rate.currency != args.budget_currency:
            parser.error(
                f"当前模型 {config.model} 的价目币种是 {rate.currency}"
                f"，--budget-currency 应为 {rate.currency}（不同币种不换算也不混算）"
            )
        sdk = AsyncOpenAI(api_key=config.api_key, base_url=config.base_url, **sdk_timeout)
        if args.campaign:
            from evals.campaign import run_campaign

            try:
                summary, path = asyncio.run(run_campaign(
                    cases, client=LLMClient(config, client=sdk, request_timeout=args.timeout),
                    rate=rate, limit=Decimal(str(args.budget)), manifest_path=args.campaign,
                    case_harness=harness, report_dir=Path("reports/evals"),
                    trace_dir=Path("traces/evals"), resume=args.resume_campaign,
                    allow_unknown=args.resume_with_unknown, case_limit=args.segment_cases,
                    segment_budget=args.segment_budget,
                ))
            except ValueError as exc:
                parser.error(str(exc))
            print(f"Campaign: {path}")
            print(f"Finished {summary['finished']}/{summary['planned']}; "
                  f"passed {summary['passed']}; unknown requests {summary['unknown_requests']}; "
                  f"known estimate {summary['known_spent']} {summary['currency']}")
            raise SystemExit(0 if summary["acceptance_passed"] else 1)
        budget = CurrencyBudget(
            Decimal(str(args.budget)), rate.currency,
            max_requests=max(40, 8 * len(cases)),
        )
        client = BudgetedClient(
            LLMClient(config, client=sdk, request_timeout=args.timeout), rate, budget,
        )
        results, path = asyncio.run(run_baseline(
            cases, client=client, currency_budget=budget, case_harness=harness,
            report_dir=Path("reports/evals"), trace_dir=Path("traces/evals"),
        ))
    else:
        sdk = AsyncOpenAI(api_key=config.api_key, base_url=config.base_url, **sdk_timeout)
        results, path = asyncio.run(run_baseline(
            cases, client=LLMClient(config, client=sdk, request_timeout=args.timeout),
            budget=Budget(args.budget),
            case_harness=harness,
            report_dir=Path("reports/evals"), trace_dir=Path("traces/evals"),
        ))
    print(f"Report: {path}")
    gate_passed = True
    if gate_policy is not None:
        decision = evaluate_gate(load_json(path.with_suffix(".json")), gate_policy,
                                 baseline=reference)
        gate_path = Path("reports/gates") / path.stem
        write_gate(decision, gate_path)
        gate_passed = decision["passed"]
        print(f"Quality gate: {'PASS' if gate_passed else 'FAIL'} ({gate_path.with_suffix('.md')})")
    accepted = gate_passed if gate_policy is not None else all(r.passed for r in results)
    raise SystemExit(0 if accepted else 1)


if __name__ == "__main__":
    main()
