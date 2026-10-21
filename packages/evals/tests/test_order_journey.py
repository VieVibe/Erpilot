"""Scripted model exercises production runtime/MCP, not supplier accuracy."""

import json
import re
import runpy
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
from agent_core.llm import LLMConfig, StreamEnd, TextDelta, ToolCall, Usage
from evals import context_workflow as cw
from evals import order_journey as journey


class ScriptedClient:
    config = LLMConfig("offline-fixture", model="glm-5.3-flash",
                       base_url="https://flashcoding.ai/v1")

    def __init__(self, *, wrong_item=False, unknown=False):
        self.wrong_item, self.unknown = wrong_item, unknown

    async def stream_chat(self, messages, tools=None):
        index = max(i for i, m in enumerate(messages) if m["role"] == "user")
        question = messages[index]["content"]
        names = {c["id"]: c["function"]["name"] for m in messages[index + 1:]
                 for c in m.get("tool_calls", [])}
        outputs = {names[m["tool_call_id"]]: json.loads(m["content"])
                   for m in messages[index + 1:] if m["role"] == "tool"}
        name, args, text = None, {}, "已核对。草稿未提交；审批拒绝或条件变化时操作未执行。"
        is_verify = "最终只输出JSON对象" in question
        if "保存报价草稿" in question:
            if "prepare_order_draft" not in outputs:
                name = "prepare_order_draft"
                customer = journey.OTHER if journey.OTHER in question else journey.CUSTOMER
                args = {"customer": customer,
                        "note": journey.NOTE, "items": [{"sku": s, "quantity": q}
                            for s, q in zip(cw.SKUS[:2], (2, 3), strict=True)]}
        elif "get_order_task" not in outputs:
            name = "get_order_task"
        else:
            task = outputs["get_order_task"]
            draft = outputs.get("get_order_draft") or task.get("draft")
            if is_verify:
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
                text = "有多份草稿，请明确客户。"
            elif task["status"] == "ambiguous" and "get_order_draft" not in outputs:
                name, args = "get_order_draft", {"draft_id": next(
                    c["draft_id"] for c in task["candidates"] if c["customer"] == journey.CUSTOMER
                )}
            elif "等待人工审批" in question:
                if "submit_order_draft" not in outputs:
                    name, args = "submit_order_draft", {
                        "draft_id": draft["draft_id"], "revision": draft["revision"],
                    }
            elif "刷新" in question:
                if "revise_order_draft" not in outputs:
                    name, args = "revise_order_draft", {
                        "draft_id": draft["draft_id"], "expected_revision": draft["revision"],
                        "customer": draft["customer"], "note": draft["note"],
                        "items": [{"sku": x["sku"], "quantity": x["quantity"]}
                                  for x in draft["items"]],
                    }
            elif "revise_order_draft_quantity" not in outputs:
                item = 2 if "第二" in question else 1
                if self.wrong_item:
                    item = 3 - item
                name, args = "revise_order_draft_quantity", {
                    "draft_id": draft["draft_id"], "expected_revision": draft["revision"],
                    "item_number": item, "quantity": 50 if "50" in question else 12,
                    "expected_sku": draft["items"][item - 1]["sku"],
                }
        if name:
            yield ToolCall(f"scripted-{len(names)}", name, json.dumps(args))
        else:
            yield TextDelta(text)
        yield StreamEnd(None if self.unknown else Usage(100, 10, 110))


@pytest.mark.parametrize("name", journey.JOURNEYS)
async def test_whole_order_with_real_approval_revalidation_and_restore(tmp_path, name):
    rows = await journey.run_journey(name, client=ScriptedClient(), directory=tmp_path,
                                     trace_dir=tmp_path / "traces")
    assert len(rows) == len(journey.JOURNEYS[name]), [r["failed_checks"] for r in rows]
    assert all(r["passed"] for r in rows), [r["failed_checks"] for r in rows]
    assert rows[-1]["after"]["orders"] and len(rows[-1]["after"]["orders"]) == 1


async def test_wrong_row_stops_whole_order_before_any_approval(tmp_path):
    rows = await journey.run_journey("restore-pending", client=ScriptedClient(wrong_item=True),
                                     directory=tmp_path, trace_dir=tmp_path / "traces")
    assert len(rows) == 2 and "target_constraints_and_all_items" in rows[-1]["failed_checks"]
    assert not rows[-1]["after"]["orders"]


@pytest.mark.parametrize("unknown", [False, True])
async def test_batch_counts_all_planned_cases_and_freezes_unknown_usage(
    tmp_path, monkeypatch, unknown,
):
    monkeypatch.setattr(cw, "ROOT", tmp_path)
    client = ScriptedClient(unknown=unknown)
    report = await journey.run_live(client.config, limit=Decimal("0.1"), currency="CNY",
                                     output=tmp_path / "batch", client_override=client)
    assert report["budget"]["unknown_usage"] is unknown
    if unknown:
        assert report["journal_audit"]["requests"] == 1
        assert not report["complete"] and len(report["results"]) == 1
    else:
        assert report["complete"] and all(r["passed"] for r in report["results"])
        assert sum(len(r["steps"]) for r in report["results"]) == 28
        audit = runpy.run_path(str(Path(__file__).resolve().parents[3]
                                  / "scripts" / "audit_order_live.py"))
        assert audit["audit_journeys"](tmp_path / "batch.json")["records_consistent"]
        assert audit["audit_requests"](tmp_path / "batch.json")["records_consistent"]
        damaged = deepcopy(report)
        damaged["results"][0]["steps"][-1]["passed"] = False
        tampered = tmp_path / "tampered.json"
        tampered.write_text(json.dumps(damaged), encoding="utf-8")
        assert not audit["audit_journeys"](tampered)["records_consistent"]
        # Check independently that silent stock damage cannot pass a completed task.
        row = deepcopy(report["results"][0]["steps"][-1])
        row["after"]["stocks"][0]["quantity"] -= 1
        assert "exact_inventory" in journey.check_step(
            journey.JOURNEYS["selection-and-denial"][-1], row["before"], row["after"],
            pending=None, history=row["tool_history"], answer=row["final_answer"], price=10,
        )
    with pytest.raises(ValueError, match="already exists"):
        await journey.run_live(client.config, limit=Decimal("0.1"), currency="CNY",
                                output=tmp_path / "batch", client_override=client)


async def test_separate_explicit_confirmation_scope_uses_only_remaining_budget(
    tmp_path, monkeypatch,
):
    script = (Path(__file__).resolve().parents[3] / "scripts"
              / "validate_explicit_confirmation_live.py")
    wrapper = runpy.run_path(str(script))
    monkeypatch.setattr(cw, "ROOT", tmp_path)
    parent = tmp_path / "parent.json"
    parent.write_text(json.dumps({"model": ScriptedClient.config.model,
        "source": cw.source_fingerprint(),
        "budget": {"currency": "CNY", "known_spent": "0.079920", "unknown_usage": False},
        "journal_audit": {"known_spent": "0.079920", "unknown_requests": 0}}), encoding="utf-8")
    report = await wrapper["run"](ScriptedClient.config, [parent], tmp_path / "explicit",
                                   client_override=ScriptedClient())
    assert report["complete"] and report["results"][0]["passed"]
    assert report["budget"]["limit"] == "0.020080"
    assert "approval-drift" in journey.JOURNEYS and len(journey.JOURNEYS) == 3
    audit = wrapper["audit"](tmp_path / "explicit.json", tmp_path / "audit.json")
    assert audit["records_consistent"] and audit["within_combined_limit"]
    with pytest.raises(ValueError, match="duplicate"):
        await wrapper["run"](ScriptedClient.config, [parent, parent], tmp_path / "blocked")
