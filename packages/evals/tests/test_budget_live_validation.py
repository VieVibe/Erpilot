import json
import re
import runpy
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
from agent_core.llm import LLMConfig, StreamEnd, TextDelta, ToolCall, Usage
from evals import context_workflow as cw

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/validate_order_budget_live.py"


class ScriptedClient:
    config = LLMConfig("offline-fixture", model="glm-5.3-flash",
                       base_url="https://flashcoding.ai/v1")

    def __init__(self, scope, *, wrong=False, unknown=False):
        self.scope, self.wrong, self.unknown = scope, wrong, unknown

    async def stream_chat(self, messages, tools=None):
        index = max(i for i, m in enumerate(messages) if m["role"] == "user")
        question = messages[index]["content"]
        names = {c["id"]: c["function"]["name"] for m in messages[index + 1:]
                 for c in m.get("tool_calls", [])}
        out = {names[m["tool_call_id"]]: json.loads(m["content"])
               for m in messages[index + 1:] if m["role"] == "tool"}
        name, args, text = None, {}, "已完成。"
        sku_a, sku_b = self.scope["SKUS"]
        if question == self.scope["CREATE"]:
            if "prepare_order_draft" not in out:
                name, args = "prepare_order_draft", {
                    "customer": self.scope["CUSTOMER"], "note": self.scope["NOTE"],
                    "items": [{"sku": sku_a, "quantity": 3}],
                    "budget_limit": 9999 if self.wrong else 3000,
                }
        elif "get_order_task" not in out:
            name = "get_order_task"
        elif "最终只输出JSON对象" in question:
            if "get_order_draft" not in out:
                name, args = "get_order_draft", {
                    "draft_id": re.search(r"草稿 ([a-f0-9]{32})", question)[1],
                }
            elif "get_order_draft_history" not in out:
                name, args = "get_order_draft_history", {
                    "draft_id": out["get_order_draft"]["draft_id"],
                }
            else:
                draft = out["get_order_draft"]
                text = json.dumps(dict(customer=draft["customer"], note=draft["note"],
                    revision=draft["revision"], budget_limit=draft["budget_limit"],
                    original_budget_limit=3000, total_amount=draft["total_amount"],
                    first_quantity=draft["items"][0]["quantity"],
                    second_quantity=draft["items"][1]["quantity"], status=draft["status"],
                    order_id=draft["order_id"]))
        else:
            draft = out["get_order_task"]["draft"]
            if "等待人工审批" in question:
                if "submit_order_draft" not in out:
                    name, args = "submit_order_draft", {
                        "draft_id": draft["draft_id"], "revision": draft["revision"],
                    }
            elif "revise_order_draft" not in out:
                name, args = "revise_order_draft", {
                    "draft_id": draft["draft_id"], "expected_revision": draft["revision"],
                    "customer": draft["customer"], "note": draft["note"],
                    "items": [{"sku": sku_a, "quantity": 3}, {"sku": sku_b, "quantity": 1}],
                }
                if question == self.scope["RAISE"]:
                    args["budget_limit"] = 3500
        if name:
            yield ToolCall(f"scripted-{len(names)}", name, json.dumps(args))
        else:
            yield TextDelta(text)
        yield StreamEnd(None if self.unknown else Usage(100, 10, 110))


@pytest.mark.parametrize("fault", [None, "wrong", "unknown"])
async def test_frozen_budget_journey_and_failure_audits(tmp_path, monkeypatch, fault):
    scope = runpy.run_path(str(SCRIPT))
    monkeypatch.setattr(cw, "ROOT", tmp_path)
    client = ScriptedClient(scope, wrong=fault == "wrong", unknown=fault == "unknown")
    output = tmp_path / "batch"
    report = await scope["run"](client.config, output, client_override=client,
                                 limit=Decimal("0.05"))
    assert report["passed"] is (fault is None)
    assert report["budget"]["unknown_usage"] is (fault == "unknown")
    if fault is None:
        assert len(report["steps"]) == 10
        assert len(report["steps"][-1]["after"]["orders"]) == 1
    else:
        assert len(report["steps"]) == 1 and not report["complete"]
    audited = scope["audit"](output.with_suffix(".json"), tmp_path / "audit.json")
    assert audited["record_consistency_passed"]
    if fault == "unknown":
        assert report["journal_audit"]["requests"] == 1
        assert not audited["metering_complete"]
    with pytest.raises(ValueError, match="already exists"):
        await scope["run"](client.config, output, client_override=client)
    with pytest.raises(ValueError, match="overwrite"):
        scope["audit"](output.with_suffix(".json"), output.with_suffix(".json"))
    damaged = deepcopy(report)
    damaged["steps"][0]["after"]["stocks"][0]["quantity"] -= 1
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(damaged), encoding="utf-8")
    for suffix in ("-requests.jsonl", "-inputs.jsonl"):
        tampered.with_name(tampered.stem + suffix).write_bytes(
            output.with_name(output.name + suffix).read_bytes(),
        )
    assert not scope["audit"](tampered, tmp_path / "tampered-audit.json")[
        "record_consistency_passed"
    ]
