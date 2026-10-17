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
from evals.policy_cases import POLICY_CASES
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
                        choices=["baseline", "b4", "b4-reference", "b5"],
                        help="case 集：既有基线 / b4 原 7 场景 / b4-reference 新指代 3 场景 / b5")
    parser.add_argument("--timeout", type=float, default=30,
                        help="单次请求总截止秒数，包含连接和整个流")
    args = parser.parse_args()
    suite_cases = {"baseline": BASELINE_CASES, "b4": TASK_CASES,
                   "b4-reference": REFERENCE_CASES, "b5": POLICY_CASES}[args.suite]
    cases = [c for c in suite_cases if not args.case or c.id in args.case]
    unknown = set(args.case) - {c.id for c in cases}
    if unknown or args.budget <= 0 or args.timeout <= 0:
        parser.error(f"未知 case {sorted(unknown)} 或预算/超时非正数")
    config = LLMConfig.from_env()
    # 单层重试：runner 可见每次尝试，避免 SDK 隐式重试扩大耗时与费用。
    sdk_timeout = {"timeout": args.timeout, "max_retries": 0}
    harness = None
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
    raise SystemExit(0 if all(r.passed for r in results) else 1)


if __name__ == "__main__":
    main()
