import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest
from agent_core.testing import USAGE, chunk, make_client, sse_response, tool_call_chunks
from evals.baseline import BASELINE_CASES, run_baseline
from evals.compare import compatibility_key
from evals.gate import evaluate_gate, load_policy
from evals.model import CaseCategory, CaseResult, ToolResult
from evals.replenishment_cases import (
    B6_AT,
    REPLENISHMENT_CASES,
    check_replenishment_result,
    seed_replenishment_fixture,
)
from evals.runner import Budget
from mcp_erp.replenishment_tools import REPLENISHMENT_GUIDANCE


async def test_scripted_b6_suite_runs_real_mcp_with_fixed_clock_and_reports(tmp_path):
    def model(request):
        body = json.loads(request.content)
        assert REPLENISHMENT_GUIDANCE in body["messages"][0]["content"]
        assert "adjust_stock" in {tool["function"]["name"] for tool in body["tools"]}
        messages = body["messages"]
        if messages[-1]["role"] == "tool":
            proof = json.loads(messages[-1]["content"])
            assert proof["assessed_at"] == B6_AT.isoformat()
            return sse_response([chunk(delta={"content":
                "最近30天销量、库存已查询，按7天采购周期测算建议37件；"
                "采购周期未知时测算23件，请确认采购周期。零销量时依据不足，"
                "库存缺失待核对；下架商品不自动建议。当前2项，还有下一页。"
                "没有采购执行能力，仅提供建议，未采购。"}), chunk(usage=USAGE)])
        question = messages[-1]["content"]
        args = {"days": 30, "coverage_days": 14, "lead_time_days": 7}
        if "前2项" in question:
            args["limit"] = 2
        else:
            sku = next(sku for sku in ["B6-A", "B6-C", "B6-D", "B6-E"] if sku in question)
            args["skus"] = [sku]
        if "不知道" in question:
            args.pop("lead_time_days")
        return sse_response(tool_call_chunks("b6", "suggest_replenishment", json.dumps(args)))

    results, path = await run_baseline(
        REPLENISHMENT_CASES, client=make_client(model), budget=Budget(1),
        report_dir=tmp_path / "reports", trace_dir=tmp_path / "traces",
    )
    assert all(result.passed for result in results), [r.failed_checks for r in results]
    report = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert report["suite"]["complete"] and report["suite"]["executed"] == 7
    assert report["suite"]["b6_scorer_version"] == "b6-1"
    assert report["suite"]["b6_fixture"]["as_of"] == B6_AT.isoformat()
    assert len(BASELINE_CASES) == 35
    assert all("suggest_replenishment" in result.tool_calls for result in results)
    policy = load_policy(Path(__file__).resolve().parents[3] / "configs/evals/b6.json")
    decision = evaluate_gate(report, policy)
    assert decision["passed"], decision["checks"]
    assert decision["metrics"]["estimated_costs"]["CNY"] == str(
        sum(Decimal(str(result.cost)) for result in results)
    )


def test_scorer_rejects_wrong_product_facts_and_mutation_attempt(tmp_path):
    from erp_store.db import make_engine
    from erp_store.repository import ErpRepository

    db = tmp_path / "erp.db"
    seed_replenishment_fixture(db)
    engine = make_engine(db)
    args = {"skus": ["B6-A"], "lead_time_days": 7}
    proof = ErpRepository(engine).suggest_replenishment(**args, as_of=B6_AT)
    tool = ToolResult(call_id="b6", name="suggest_replenishment", arguments=args,
                      content=proof, ok=True)
    result = CaseResult(case_id="b6-known-lead", category=CaseCategory.MULTI, passed=True,
                        tool_calls=[tool.name], tool_results=[tool])
    assert check_replenishment_result(result.case_id, result, {}, {}) == []
    forged = result.model_copy(deep=True)
    forged.tool_results[0].content["items"][0]["suggested_quantity"] = 999
    assert "b6_invalid_contract" in check_replenishment_result(result.case_id, forged, {}, {})
    result.tool_calls.append("prepare_stock_adjustment")
    failed = check_replenishment_result(result.case_id, result, {}, {"stocks": []})
    assert "b6_write_attempted" in failed and "b6_state_changed" in failed
    assert "b6_missing_evidence" in check_replenishment_result(
        result.case_id, result.model_copy(update={"tool_results": []}), {}, {}
    )
    engine.dispose()


def test_b6_comparison_requires_scorer_and_fixture_fingerprints():
    from test_compare import _full_report

    report = _full_report()
    report["suite"]["case_ids"] = ["b6-known-lead"]
    with pytest.raises(ValueError, match="b6_scorer_version"):
        compatibility_key(report)
    report["suite"]["b6_scorer_version"] = "b6-1"
    with pytest.raises(ValueError, match="b6_fixture"):
        compatibility_key(report)
    report["suite"]["b6_fixture"] = {"version": "b6-fixture-1", "as_of": B6_AT.isoformat()}
    original = compatibility_key(report)
    changed = copy.deepcopy(report)
    changed["suite"]["b6_fixture"]["version"] = "b6-fixture-2"
    assert compatibility_key(changed) != original
