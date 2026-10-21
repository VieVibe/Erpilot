import importlib.util
import json
import re
from pathlib import Path

import pytest
from agent_core.llm import LLMConfig, StreamEnd, TextDelta, ToolCall, Usage
from erp_store.drafts import ErpDrafts
from evals import context_workflow as cw
from evals.model import CaseCategory, CaseResult, ToolResult

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "validate_draft_history_live.py"
SPEC = importlib.util.spec_from_file_location("history_live_validation", SCRIPT)
live = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(live)
AUDIT_SPEC = importlib.util.spec_from_file_location("order_live_audit", SCRIPT.with_name(
    "audit_order_live.py",
))
auditor = importlib.util.module_from_spec(AUDIT_SPEC)
AUDIT_SPEC.loader.exec_module(auditor)


class OfflineClient:
    config = LLMConfig("offline-test-key", model="glm-5.3-flash",
                       base_url="https://flashcoding.ai/v1")

    async def stream_chat(self, messages, tools=None):
        question = next(m["content"] for m in messages if m["role"] == "user")
        names = {call["id"]: call["function"]["name"]
                 for message in messages for call in message.get("tool_calls", [])}
        outputs = [(names[m["tool_call_id"]], json.loads(m["content"]))
                   for m in messages if m["role"] == "tool"]
        draft_id = re.search(r"草稿 ([0-9a-f]{32})", question).group(1)
        name, args = None, {}
        if "全部修改记录" in question:
            pages = [output for tool, output in outputs if tool == "get_order_draft_history"]
            if not outputs:
                name = "get_order_task"
            elif not pages or pages[-1]["has_more"]:
                name = "get_order_draft_history"
                args = {"draft_id": draft_id, "limit": 1}
                if pages:
                    args["before_revision"] = pages[-1]["next_before_revision"]
            else:
                versions = {item["revision"]: item for page in pages for item in page["items"]}
                first, second, latest = (versions[n] for n in (1, 2, 3))
                diff = next(c for c in second["change_record"]["changes"]
                            if c["field"] == "quantity")
                yield TextDelta(json.dumps({
                    "revision_count": len(versions),
                    "original_customer": first["task_constraints"]["fields"]["customer"]["value"],
                    "original_note": first["task_constraints"]["fields"]["note"]["value"],
                    "second_quantity_before": diff["before"],
                    "second_quantity_after": diff["after"],
                    "latest_customer": latest["task_constraints"]["fields"]["customer"]["value"],
                    "latest_note": latest["task_constraints"]["fields"]["note"]["value"],
                    "latest_source": latest["change_record"]["source"]["kind"],
                    "original_user_request": first["change_record"]["source"]["user_request"],
                }, ensure_ascii=False))
        elif not outputs:
            name, args = "get_order_draft_history", {"draft_id": draft_id}
        else:
            yield TextDelta(json.dumps({"available": "error" not in outputs[-1][1]}))
        if name:
            yield ToolCall(f"offline-{len(outputs)}", name, json.dumps(args))
        yield StreamEnd(Usage(100, 10, 110))


def previous(path, *, unknown=False, spent="0.01"):
    report = {"model": "glm-5.3-flash", "source": cw.source_fingerprint(),
              "budget": {"currency": "CNY", "limit": "0.10", "unknown_usage": unknown,
                         "known_spent": spent},
              "journal_audit": {"known_spent": spent, "unknown_requests": int(unknown)}}
    path.write_text(json.dumps(report), encoding="utf-8")


async def test_real_mcp_offline_fixture_pages_exact_answers_and_remaining_budget(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cw, "ROOT", tmp_path)
    prior = tmp_path / "previous.json"
    previous(prior)
    report = await live.run(OfflineClient.config, prior, tmp_path / "new-batch",
                            client_override=OfflineClient())
    assert report["complete"] and report["source_consistent"]
    assert all(row["result"]["passed"] for row in report["results"]), report["results"]
    assert report["budget"]["limit"] == "0.09" and not report["budget"]["unknown_usage"]
    assert report["journal_audit"]["requests"] == 4
    audit = auditor.audit_requests(tmp_path / "new-batch.json")
    assert audit["records_consistent"] and audit["metering_complete"]
    assert all(row["before_sha256"] == row["after_sha256"] for row in report["results"])
    assert all(len(row["all_tables"]) >= 7 for row in report["results"])
    with pytest.raises(ValueError, match="already exists"):
        await live.run(OfflineClient.config, prior, tmp_path / "new-batch")


@pytest.mark.parametrize("unknown,spent,reason", [
    (True, "0.001", "unknown usage"), (False, "0.10", "exhausted"),
])
async def test_frozen_or_exhausted_prior_batch_cannot_make_new_requests(
    tmp_path, unknown, spent, reason,
):
    prior = tmp_path / "previous.json"
    previous(prior, unknown=unknown, spent=spent)
    with pytest.raises(ValueError, match=reason):
        await live.run(OfflineClient.config, prior, tmp_path / "blocked")
    assert not (tmp_path / "blocked-requests.jsonl").exists()


async def test_interrupted_history_never_mislabels_system_prompt_as_final_answer(
    tmp_path, monkeypatch,
):
    class Interrupted(OfflineClient):
        async def stream_chat(self, messages, tools=None):
            raise RuntimeError("synthetic interrupted stream")
            yield  # pragma: no cover

    monkeypatch.setattr(cw, "ROOT", tmp_path)
    prior = tmp_path / "previous.json"
    previous(prior)
    report = await live.run(OfflineClient.config, prior, tmp_path / "interrupted",
                            client_override=Interrupted())
    assert report["budget"]["unknown_usage"] and not report["complete"]
    assert len(report["results"]) == 1 and report["results"][0]["final_answer"] == ""


def test_exact_visible_answer_does_not_hide_mutations_or_missing_history_pages():
    result = CaseResult(case_id="history-live-foreign", category=CaseCategory.EDGE, passed=True,
                        visible_text='{"available":false}', tool_attempts=["adjust_stock"])
    assert "mutation_requested" in live.check_result(result.case_id, result, {}, {})
    result.tool_attempts = []
    assert "all_tables_must_be_unchanged" in live.check_result(
        result.case_id, result, {}, {"x": []},
    )
    assert "exact_visible_answer" in live.check_result(
        result.case_id, result, {}, {}, '{"available":0}',
    )
    result.tool_results = [ToolResult(call_id="page", name="get_order_draft_history", ok=True,
                                     arguments={"limit": 2}, content={"items": [{"revision": 3}]})]
    assert "complete_ordered_exclusive_pagination" in live.check_result(
        "history-live-pages", result, {}, {},
    )


def test_independent_origin_audit_rejects_changed_source_quote(tmp_path):
    engine = cw.seed_workflow(tmp_path / "erp.db", cw.WORKFLOWS[0], "s1")
    try:
        before = cw.snapshot_all(engine)
        args = {"customer": "记录验收客户甲", "note": "按原要求保留",
                "items": [{"sku": cw.SKUS[0], "quantity": 2}]}
        proof = live.validate_draft_arguments("prepare_order_draft", args,
                                             [{"role": "user", "content": live.CREATE}], [])
        ErpDrafts(engine).prepare("s1", args["customer"], [(cw.SKUS[0], 2)], args["note"],
                                 evidence=proof)
        after = cw.snapshot_all(engine)
        path = tmp_path / "origins.json"
        data = {"results": [{"turns": [{"question": live.CREATE, "result": {
            "case_id": "fixture", "tool_results": [], "state_evidence": {
                "drafts_before": before["order_drafts"], "drafts_after": after["order_drafts"],
            },
        }}]}]}
        path.write_text(json.dumps(data), encoding="utf-8")
        assert auditor.audit_origins(path)["origins_consistent"]
        changed = next(row for row in after["order_drafts"] if row["session_id"] == "s1")
        snapshot = json.loads(changed["snapshot"])
        snapshot["task_constraints"]["fields"]["customer"]["origin"]["quote"] = "不存在的原话"
        changed["snapshot"] = json.dumps(snapshot)
        path.write_text(json.dumps(data), encoding="utf-8")
        assert not auditor.audit_origins(path)["origins_consistent"]
    finally:
        engine.dispose()
