"""New full-task cases use the production runtime; fake usage is explicitly offline."""

import json
import re
from copy import deepcopy
from decimal import Decimal

import pytest
from agent_core.llm import LLMConfig, StreamEnd, TextDelta, ToolCall, Usage
from evals import context_workflow as cw
from evals import order_journey as journey
from evals import order_reliability as suite


class ScriptedClient:
    config = LLMConfig("offline-fixture", model="glm-5.3-flash",
                       base_url="https://flashcoding.ai/v1")

    def __init__(self, *, creation_mistake=False, unknown=False):
        self.creation_mistake, self.unknown = creation_mistake, unknown

    async def stream_chat(self, messages, tools=None):
        index = max(i for i, message in enumerate(messages) if message["role"] == "user")
        question = messages[index]["content"]
        names = {call["id"]: call["function"]["name"] for message in messages[index + 1:]
                 for call in message.get("tool_calls", [])}
        outputs = {names[message["tool_call_id"]]: json.loads(message["content"])
                   for message in messages[index + 1:] if message["role"] == "tool"}
        name, args, text = None, {}, "已核对；有待确认事项时不提交。"
        if "保存报价草稿" in question:
            result = outputs.get("prepare_order_draft")
            if result is None or "error" in result:
                quantities = (2, 4) if self.creation_mistake and result is None else (2, 3)
                name, args = "prepare_order_draft", {
                    "customer": journey.OTHER if journey.OTHER in question else journey.CUSTOMER,
                    "note": journey.NOTE,
                    "items": [{"sku": sku, "quantity": count}
                              for sku, count in zip(cw.SKUS[:2], quantities, strict=True)],
                }
                if "预算上限为45元" in question:
                    args["budget_limit"] = 45
        elif "get_order_task" not in outputs:
            name = "get_order_task"
        else:
            task = outputs["get_order_task"]
            draft = outputs.get("get_order_draft") or task.get("draft")
            if "最终只输出JSON对象" in question:
                if "get_order_draft" not in outputs:
                    name, args = "get_order_draft", {
                        "draft_id": re.search(r"草稿 ([a-f0-9]{32})", question)[1],
                    }
                else:
                    text = json.dumps(dict(
                        customer=draft["customer"], note=draft["note"], revision=draft["revision"],
                        item_count=draft["item_count"],
                        first_quantity=draft["items"][0]["quantity"],
                        second_quantity=draft["items"][1]["quantity"],
                        total_amount=draft["total_amount"], status=draft["status"],
                        order_id=draft["order_id"],
                    ), ensure_ascii=False)
            elif task["status"] == "ambiguous" and journey.CUSTOMER not in question:
                text = "有多份草稿，请明确客户或草稿标识。"
            elif task["status"] == "ambiguous" and "get_order_draft" not in outputs:
                name, args = "get_order_draft", {"draft_id": next(
                    item["draft_id"] for item in task["candidates"]
                    if item["customer"] == journey.CUSTOMER
                )}
            elif "等待人工审批" in question:
                if "submit_order_draft" not in outputs:
                    name, args = "submit_order_draft", {
                        "draft_id": draft["draft_id"], "revision": draft["revision"],
                    }
            elif "只读" in question:
                if draft.get("budget_check", {}).get("status") == "over_budget":
                    check = draft["budget_check"]
                    text = f"报价{draft['total_amount']}元，预算{check['limit']}元，超额"
                    text += f"{check['over_amount']}元；请明确调整后重新核对，未提交、未改库存。"
                else:
                    text = "报价条件已变化，请明确刷新报价后重新核对和审批；未建单、未扣库存。"
            elif "刷新" in question or "预算改为75元" in question:
                if "revise_order_draft" not in outputs:
                    name, args = "revise_order_draft", {
                        "draft_id": draft["draft_id"], "expected_revision": draft["revision"],
                        "customer": draft["customer"], "note": draft["note"],
                        "items": [{"sku": row["sku"], "quantity": row["quantity"]}
                                  for row in draft["items"]],
                    }
                    if "预算改为75元" in question:
                        args["budget_limit"] = 75
            elif "revise_order_draft_quantity" not in outputs:
                name, args = "revise_order_draft_quantity", {
                    "draft_id": draft["draft_id"], "expected_revision": draft["revision"],
                    "item_number": 2, "quantity": 5, "expected_sku": cw.SKUS[1],
                }
        if name:
            yield ToolCall(f"script-{len(names)}", name, json.dumps(args))
        else:
            yield TextDelta(text)
        yield StreamEnd(None if self.unknown else Usage(100, 10, 110))


@pytest.mark.parametrize("name", suite.CASES)
async def test_new_full_tasks_use_real_approval_and_exact_business_oracle(tmp_path, name):
    rows = await journey.run_journey(
        name, client=ScriptedClient(creation_mistake=True), directory=tmp_path,
        trace_dir=tmp_path / "traces", steps=suite.CASES[name], checker=suite.check_step,
    )
    assert len(rows) == len(suite.CASES[name]), [row["failed_checks"] for row in rows]
    assert all(row["passed"] for row in rows), [row["failed_checks"] for row in rows]
    # A wrong first proposal followed by correction must still save only one v1.
    assert [row["ok"] for row in rows[0]["tool_history"]] == [False, True]
    assert len(rows[-1]["after"]["orders"]) == 1
    damaged = deepcopy(rows[-1]["after"])
    damaged["stocks"][0]["quantity"] -= 1
    assert "exact_inventory" in suite.check_step(
        suite.CASES[name][-1], rows[-1]["before"], damaged,
        pending=None, history=rows[-1]["tool_history"], answer=rows[-1]["final_answer"],
        price=20 if name == "price-change-review" else 10,
    )


@pytest.mark.parametrize("unknown", [False, True])
async def test_report_accounts_for_all_tasks_and_unknown_usage_stops_requests(
    tmp_path, monkeypatch, unknown,
):
    assert suite.MANIFEST_SHA256 == (
        "22636e82a29a4b07d764cec73691aca9a9cc87e1693dcc6f4330e23f340ae972"
    )
    monkeypatch.setattr(cw, "ROOT", tmp_path)
    client = ScriptedClient(unknown=unknown)
    report = await suite.run(client.config, limit=Decimal("0.1"), currency="CNY",
                             output=tmp_path / "batch", client_override=client)
    assert report["manifest_sha256"] == suite.MANIFEST_SHA256
    assert report["metrics"]["planned_tasks"] == 4
    assert report["metrics"]["planned_stages"] == 34
    assert report["budget"]["unknown_usage"] is unknown
    if unknown:
        assert report["journal_audit"]["requests"] == 1 and not report["complete"]
        assert report["metrics"]["executed_stages"] == 1
    else:
        assert report["complete"] and report["metrics"]["passed_tasks"] == 4
        assert report["metrics"]["passed_stages"] == 34
        assert report["metrics"]["wrong_draft_stages"] == 0
        assert report["metrics"]["wrong_order_stages"] == 0


@pytest.mark.parametrize("name", suite.CASES)
async def test_v2_explicit_read_and_safe_budget_stop_preserve_all_state_checks(tmp_path, name):
    from evals import order_reliability_v2 as v2

    rows = await journey.run_journey(
        name, client=ScriptedClient(), directory=tmp_path, trace_dir=tmp_path / "traces",
        steps=v2.CASES[name], checker=suite.check_step,
    )
    assert len(rows) == len(v2.CASES[name])
    assert all(row["passed"] for row in rows), [r["failed_checks"] for r in rows]
    if name == "budget-denial-restore":
        blocked = rows[1]
        assert blocked["before"] == blocked["after"] and blocked["pending"] is None
        assert [t["name"] for t in blocked["tool_history"]] == ["get_order_task"]
    submission = next((i, s) for i, s in enumerate(v2.CASES[name]) if s.pending
                      and s.action == "user")
    index, step = submission
    row = rows[index]
    # Removing the current-turn observation must still fail the v2 contract.
    assert "actual_pending_issues_read" in suite.check_step(
        step, row["before"], row["after"], pending=type("Pending", (), row["pending"])(),
        history=[], answer=row["final_answer"], price=10,
    )
    assert suite.MANIFEST_SHA256 == (
        "22636e82a29a4b07d764cec73691aca9a9cc87e1693dcc6f4330e23f340ae972"
    )
    assert v2.MANIFEST_SHA256 != suite.MANIFEST_SHA256
