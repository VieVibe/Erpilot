"""Opt-in rule/state adapter; intent evidence is never a tool argument."""

import json
import logging
import re
from copy import deepcopy
from dataclasses import replace

from erp_store.intent_continuation import preserved_fields, question_fields
from erp_store.intent_contract import digest, scan_source
from erp_store.intent_execution import (
    CatalogChanged,
    catalog_fingerprint,
    make_evidence,
    reconstruct_creation,
)
from erp_store.intent_items import needs_display_reference
from erp_store.intent_recovery import IntentRecovery
from erp_store.intent_references import load_reference, previous_display
from erp_store.intent_rules import (
    WITHDRAW,
    analyze_rules,
    field_values,
    quantity_reply,
    selection_reply,
    slot_reply,
)
from erp_store.intent_state import IntentStore, source_message
from erp_store.order_request import needs_resolution, parse_request, resolve_request
from sqlalchemy.orm import Session

from mcp_erp.draft_constraints import _observed_draft, ensure_task_selection
from mcp_erp.intent_diagnostics import IntentDiagnostics
from mcp_erp.order_language import ResolveOrderItemsParams, current_text

_LOG = logging.getLogger(__name__)
_DRAFT_WRITES = {"prepare_order_draft", "revise_order_draft", "revise_order_draft_quantity"}
_ORDER_WRITES = {"submit_order_draft", "create_order", "cancel_order"}
_BUSINESS_PREVIEWS = {"prepare_cancel_order", "prepare_stock_adjustment"}
INTENT_GUIDANCE = (
    "订单草稿已启用持久化意图规则。resolve_order_items 返回的 resolved 结果是本轮有效的"
    "完整要求，包括应用从有效待澄清请求恢复的客户、全部商品、备注和预算。"
    "候选编号回复无需用户重述原清单；按本轮 resolved 的完整字段调用 prepare_order_draft。"
    "编号后可追加明确的客户、备注、预算或所选商品件数要求，应用核验全部增量后合并保存。"
    "有唯一待完成请求时，可单独补充客户、备注、预算；完整绑定后可改原商品数量。"
    "初始商品缺少件数或使用箱、包等单位时，逐项补问最终件数，不推测包装换算。"
    "另有已保存草稿时，独立回复须明确『待完成请求：…』，避免改错对象。"
    "明确『刚才展示的第几行』按实际旧展示 SKU 定位；未限定版本且新旧行号不同时先澄清。"
    "数量与预算等复合修改必须合并为一次 revise_order_draft，不能分次保存。"
    "clarification_required 的候选顺序由应用展示；只补当前待选项，不追加建单或取消权限。"
)


def clarification(message, source, **extra):
    return {"status": "clarification_required", "source_user_sha256": digest(source),
            "items": [], "issues": [{"code": "intent_clarification", "message": message}],
            **extra}


def render_clarification(result):
    if not result.get("ok"):
        return None
    data = json.loads(result["content"])
    if data.get("status") != "clarification_required":
        return None
    lines = ["本轮未保存新草稿或修改草稿，请核对以下信息："]
    if data.get("question"):
        lines.append(data["question"])
    else:
        lines.extend(issue["message"] for issue in data.get("issues", []))
    for i, item in enumerate(data.get("display_candidates", []), 1):
        lines.append(f"{i}. {item['name']}（SKU {item['sku']}，{item['status']}）")
    if data.get("display_candidates"):
        lines.append("请回复“选第几个，其他照旧”。原客户、完整清单、备注和预算将保留。")
    if data.get("preserved_fields"):
        fields = data["preserved_fields"]
        lines.append("已保留的完整请求：")
        for key, label in (("customer", "客户"), ("note", "备注"), ("budget_limit", "预算上限")):
            if key in fields:
                value = fields[key]
                lines.append(f"{label}：{value if value is not None else '未设置'}")
        for index, row in enumerate(fields.get("items", []), 1):
            name = row.get("product_query") or row.get("sku", "待核对商品")
            count = f"{row['quantity']} 件" if row.get("quantity") is not None else "件数待补充"
            lines.append(f"第 {index} 项：{name}，{count}")
    return "\n".join(lines)


class OrderIntentAnalyzer:
    def __init__(self, engine, *, mode="off", clock=None, ttl_seconds=600):
        if mode not in {"off", "shadow", "enforce"}:
            raise ValueError("intent_mode must be off, shadow or enforce")
        self.engine, self.mode = engine, mode
        self.diagnostics = IntentDiagnostics(mode)
        self.semantic = None
        self.store = (IntentStore(engine, clock=clock, ttl_seconds=ttl_seconds)
                      if mode == "enforce" else None)
        self.recovery = IntentRecovery(engine, clock=clock)
        if mode != "enforce":
            self.recovery.suspend()

    def resolve(self, session_id, source, *, turn_id=None):
        result = self._resolve(session_id, source, turn_id=turn_id)
        self.diagnostics.record(session_id, "rule_resolution", {
            "candidate_source": "rules_and_pending_state", "source_sha256": digest(source),
            "decision": "ready" if result.get("status") == "resolved" else "clarify",
            "unresolved": result.get("issues", []), "pending_ref": result.get("pending_ref"),
        })
        return result

    def _resolve(self, session_id, source, *, turn_id=None):
        pending = self.store.current(session_id)
        if (pending and pending.get("semantic_preview_version") == 1
                and pending["status"] in {"pending", "ready"}
                and (pending["messages"][0]["message_id"] == turn_id
                     or (turn_id is None and pending["messages"][0]["text"] == source)
                     or selection_reply(source) or slot_reply(source) is not None)):
            return clarification("此请求包含待核对的语义候选，请在业务面板核对完整原话和字段；"
                                 "编号或简短补充不能确认这张表单。", source,
                                 pending_ref={"pending_id": pending["pending_id"],
                                              "state_version": pending["state_version"]})
        if (pending and pending["status"] in {"pending", "ready"} and (
                (turn_id and pending["messages"][-1]["message_id"] == turn_id)
                or (turn_id is None
                    and pending["messages"][-1]["text"] == source))):
            try:
                with Session(self.engine) as session:
                    _, analyzed = reconstruct_creation(session, self._data(pending))
            except ValueError as exc:
                self.store.update(session_id, pending["pending_id"], pending["state_version"],
                                  self._data(pending), status="superseded")
                return clarification(str(exc), source)
            if analyzed:
                return self._ready(source, pending, analyzed[1])
            from erp_store.intent_execution import replay_selection_messages

            _, remaining = replay_selection_messages(pending)
            return self._question(source, pending, remaining[0])
        if WITHDRAW.fullmatch(source):
            if pending and pending["status"] in {"pending", "ready"}:
                self.store.update(session_id, pending["pending_id"], pending["state_version"],
                                  self._data(pending), status="cancelled")
                return clarification("已撤回待完成请求。", source)
            return clarification("没有有效的待完成请求可撤回。", source)
        choice, slot = selection_reply(source), slot_reply(source)
        if choice or slot is not None:
            if (pending is None and slot is not None
                    and self.store.recent_slot_consumed(session_id, source)):
                return clarification("该补充要求已处理，本轮不重复应用；"
                                     "如需新的修改，请说明完整新要求。", source)
            if (not pending or pending["status"] not in {"pending", "ready"}
                    or (choice and pending["status"] != "pending")):
                return clarification("没有唯一有效的待完成请求或候选问题，"
                                     "请重新核对完整要求。", source)
            if not choice and slot[1] == 0:
                from erp_store.drafts import ErpDrafts

                if ErpDrafts(self.engine).task_state(session_id)["status"] != "none":
                    return self._retry_question(
                        source, pending,
                        "另有已保存草稿。修改待完成请求请明确『待完成请求：…』；"
                        "修改已保存草稿请先撤回待完成请求。",
                        turn_id=turn_id,
                    )
            data = self._data(pending)
            data["messages"].append(source_message(source, turn_id))
            try:
                with Session(self.engine) as session:
                    resolution, analyzed = reconstruct_creation(session, data)
                if analyzed is None:
                    # Advance one question at a time, preserving the original display order.
                    from erp_store.intent_execution import replay_selection_messages

                    _, remaining = replay_selection_messages(data)
                    if not remaining:
                        question = self._retry_question(source, pending,
                                                        resolution["issues"][0]["message"],
                                                        turn_id=turn_id)
                        return {**question, "issues": resolution["issues"]}
                    data["preserved_fields"] = resolution["preserved_fields"]
                    result = self.store.update(session_id, pending["pending_id"],
                                               pending["state_version"], data, status="pending")
                    return self._question(source, result, remaining[0])
                data["preserved_fields"] = preserved_fields(data, analyzed)
                pending = self.store.update(session_id, pending["pending_id"],
                                            pending["state_version"], data, status="ready")
                return self._ready(source, pending, analyzed[1])
            except ValueError as exc:
                if isinstance(exc, CatalogChanged):
                    self.store.update(session_id, pending["pending_id"], pending["state_version"],
                                      self._data(pending), status="superseded")
                    return clarification(str(exc), source)
                return self._retry_question(source, pending, str(exc), turn_id=turn_id)
        self.store.abandon_unrelated(session_id, source)
        with Session(self.engine) as session:
            try:
                parsed = parse_request(source, extended=True, allow_incomplete=True)
                incomplete = any(r["quantity"] is None for r in parsed["requirements"])
                resolution = resolve_request(session, source, extended=True,
                                             allow_incomplete=incomplete)
                # Parse all scalar fields and coverage before persisting any choice.
                placeholder = {**resolution, "status": "resolved", "items": []}
                if not placeholder.get("creation_guard"):
                    placeholder["creation_guard"] = {"requirements": []}
                frame, _, literals, _ = analyze_rules(
                    source, session_id=session_id, request_id="precheck", resolution=placeholder,
                    allow_incomplete_creation=incomplete,
                )
                if frame.decision != "ready":
                    return clarification(frame.unresolved[0].question, source)
                issues = resolution.get("issues", [])
                if any(i["code"] not in {"ambiguous_product", "missing_quantity"}
                       or i.get("has_more") for i in issues):
                    return resolution
                data = {"session_id": session_id, "messages": [source_message(source, turn_id)],
                        "initial_resolution": resolution,
                        "catalog_fingerprint": catalog_fingerprint(session, resolution),
                        "preserved_fields": {k: v["value"] for k, v in literals.items()}}
                if incomplete:
                    data["creation_slots_version"] = 1
                data["preserved_fields"]["items"] = parsed["requirements"]
                pending = self.store.replace(session_id, data,
                                             status="pending" if issues else "ready")
                if issues:
                    return self._question(source, pending, issues[0])
                _, analyzed = reconstruct_creation(session, data)
                return self._ready(source, pending, analyzed[1])
            except ValueError as exc:
                return clarification(str(exc), source)

    @staticmethod
    def _data(pending):
        return deepcopy({k: v for k, v in pending.items() if k not in {
            "pending_id", "state_version", "status", "expires_at",
        }})

    @staticmethod
    def _ready(source, pending, expected):
        return {"status": "resolved", "source_user_sha256": digest(source), **expected,
                "pending_ref": {"pending_id": pending["pending_id"],
                                "state_version": pending["state_version"]}}

    @staticmethod
    def _question(source, pending, issue):
        fields = question_fields(issue)
        return clarification(issue["message"], source,
                             question=fields["question"], display_candidates=fields["candidates"],
                             preserved_fields=pending["preserved_fields"],
                             pending_ref={"pending_id": pending["pending_id"],
                                          "state_version": pending["state_version"]})

    def _retry_question(self, source, pending, error, *, turn_id=None):
        from erp_store.intent_execution import replay_selection_messages

        _, remaining = replay_selection_messages(pending)
        attempts = self.store.record_clarification(
            pending["session_id"], pending, source, turn_id,
        )
        if not remaining:
            return clarification(error, source, preserved_fields=pending["preserved_fields"],
                                 clarification_attempts=attempts,
                                 pending_ref={"pending_id": pending["pending_id"],
                                              "state_version": pending["state_version"]})
        question = self._question(source, pending, remaining[0])
        question["question"] = error + "\n" + question["question"]
        question["clarification_attempts"] = attempts
        if attempts >= 2:
            question["question"] += "\n可在业务面板打开完整请求核对，逐项填写后保存草稿。"
        return question

    def pending_slot(self, session_id, source):
        slot = slot_reply(source)
        count = quantity_reply(source)
        bare_count = count and not source[count.start():count.start("number")].strip()
        return slot is not None and (bare_count or slot[1] > 0
                                     or self.store.current(session_id) is not None
                                     or self.store.recent_slot_consumed(session_id, source))

    def validate(self, session_id, name, arguments, messages, history):
        try:
            evidence = self._validate(session_id, name, arguments, messages, history)
        except ValueError:
            self.diagnostics.record(session_id, "intent_validation", {
                "tool": name, "accepted": False, "rejection_code": "intent_argument_rejected",
            })
            raise
        self.diagnostics.record(session_id, "intent_validation", {
            "tool": name, "accepted": True, "contract": evidence.get("contract"),
        })
        return evidence

    def _validate(self, session_id, name, arguments, messages, history):
        source = current_text(messages)
        if name != "revise_order_draft_quantity":
            items = arguments.get("items")
            if (not isinstance(items, list) or any(
                not isinstance(item, dict) or set(item) != {"sku", "quantity"}
                or not isinstance(item["sku"], str) or type(item["quantity"]) is not int
                or item["quantity"] < 1 for item in items
            )):
                raise ValueError("每项商品必须有明确 SKU 和正整数件数，不能用布尔值或改写清单")
        if name == "prepare_order_draft":
            observed = next((json.loads(r["content"]) for r in reversed(history)
                             if r["name"] == "resolve_order_items" and r.get("ok")), {})
            pending = self.store.current(session_id)
            if (observed.get("status") != "resolved" or not pending
                    or observed.get("source_user_sha256") != digest(source)
                    or observed.get("pending_ref") != {
                        "pending_id": pending["pending_id"],
                        "state_version": pending["state_version"]}
                    or pending["status"] != "ready"):
                raise ValueError("先完成本轮完整清单核对与候选选择，再保存草稿")
            with Session(self.engine) as session:
                _, analyzed = reconstruct_creation(session, self._data(pending))
            expected = analyzed[1]
            actual = {k: arguments.get(k) for k in ("customer", "note", "budget_limit", "items")}
            if actual != expected:
                raise ValueError("工具参数与完整原始请求不一致，未保存部分清单")
            return make_evidence(session_id, pending["messages"], pending=pending)
        previous = _observed_draft(arguments, history)
        task = next((json.loads(r["content"]) for r in reversed(history)
                     if r["name"] == "get_order_task" and r.get("ok")), None)
        if task is None:
            raise ValueError("请先在本轮读取当前任务，再选择目标草稿")
        ensure_task_selection(arguments["draft_id"], source, task)
        reference = (previous_display(messages, arguments["draft_id"])
                     if needs_display_reference(source) else None)
        with Session(self.engine) as session:
            observed = load_reference(session, session_id, reference,
                                      target_id=arguments["draft_id"],
                                      current_revision=previous["revision"])
        frame, expected, _, _ = analyze_rules(source, session_id=session_id,
                                              request_id="revision", previous=previous,
                                              reference=observed)
        if frame.decision != "ready":
            raise ValueError(frame.unresolved[0].question)
        if name == "revise_order_draft_quantity":
            actual = field_values(previous)
            number = arguments["item_number"]
            if (type(number) is not int or not 1 <= number <= len(actual["items"])
                    or arguments["expected_sku"] != actual["items"][number - 1]["sku"]):
                raise ValueError("行号与本轮实际 SKU 不一致")
            actual["items"][number - 1]["quantity"] = arguments["quantity"]
        else:
            actual = {k: arguments.get(k) for k in ("customer", "note", "items")}
            budget = arguments.get("budget_limit")
            actual["budget_limit"] = (None if arguments.get("clear_budget") else budget
                                      if budget is not None else previous.get("budget_limit"))
            if arguments.get("clear_budget") and arguments.get("budget_limit") is not None:
                raise ValueError("不能同时设置和取消预算")
        if expected != actual:
            raise ValueError("请使用完整修改一次保存；不能分次保存数量、预算或其他字段")
        return make_evidence(session_id, [source_message(source)], previous=previous,
                             reference=reference)

    def bind(self, tools, session_id):
        if self.mode == "off":
            return self._bind_recovery(tools, session_id)
        bound = []
        for tool in tools:
            if self.mode == "enforce" and tool.name in _DRAFT_WRITES | {"resolve_order_items"}:
                tool = replace(tool, description=tool.description + "\n" + INTENT_GUIDANCE)
            original = tool.call_validator

            def validate(arguments, messages, history, *, name=tool.name, old=original):
                source = current_text(messages)
                if self.mode == "shadow":
                    if name in _DRAFT_WRITES:
                        try:
                            previous = (_observed_draft(arguments, history)
                                        if name != "prepare_order_draft" else None)
                            with Session(self.engine) as session:
                                resolution = (resolve_request(session, source, extended=True)
                                              if not previous else None)
                            frame, _, _, _ = analyze_rules(
                                source, session_id=session_id, request_id="shadow",
                                previous=previous, resolution=resolution,
                            )
                            _LOG.info("intent_shadow %s", frame.model_dump_json())
                            self.diagnostics.record(session_id, "shadow_analysis", {
                                "candidate_source": "rules", "frame": frame.model_dump(mode="json"),
                            })
                        except (ValueError, KeyError):
                            _LOG.info("intent_shadow unsupported")
                            self.diagnostics.record(session_id, "shadow_analysis", {
                                "candidate_source": "rules", "decision": "unsupported",
                                "rejection_code": "unsupported_rule",
                            })
                    return old(arguments, messages, history) if old else {}
                if name in _DRAFT_WRITES:
                    return self.validate(session_id, name, arguments, messages, history)
                if name in _ORDER_WRITES:
                    text = scan_source(source).command_text
                    # This rollout licenses only draft changes. Other writes retain
                    # their old preflight, plus explicit scope/phase restrictions.
                    if (name == "create_order" or selection_reply(source)
                            or self.pending_slot(session_id, source)
                            or needs_resolution(source)
                            or re.search(
                                r"如果|假如|只查|只读|解释|先别|不要|不提交|只保存草稿", text)):
                        raise ValueError("本轮仅允许核对或保存草稿，不允许申请建单或取消")
                return old(arguments, messages, history) if old else {}

            def permitted_impl(messages, history, *, name=tool.name, risk=tool.risk):
                source = current_text(messages)
                text = scan_source(source).command_text
                # A pending field value (e.g. a customer named "请提交订单") is data,
                # and cannot license another mutation, preview or approval request.
                if (name != "prepare_order_draft"
                        and (risk is not None
                             or name in _ORDER_WRITES | _BUSINESS_PREVIEWS | _DRAFT_WRITES)
                        and (selection_reply(source) or self.pending_slot(session_id, source))):
                    return False
                if name in _ORDER_WRITES:
                    explicit = (re.search(r"(?:^|[，,；;。])\s*(?:请)?取消订单", text)
                                if name == "cancel_order" else
                                re.search(r"提交|建单|下单|申请人工审批", text))
                    return bool(explicit) and not (
                                name == "create_order" or selection_reply(source)
                                or needs_resolution(source) or re.search(
                                    r"如果|假如|只查|只读|解释|先别|不要|不提交|只保存草稿", text))
                if name == "prepare_order_draft":
                    return any(r["name"] == "resolve_order_items" and r.get("ok")
                               and json.loads(r["content"]).get("status") == "resolved"
                               for r in history)
                if name in _DRAFT_WRITES:
                    return any(r["name"] == "get_order_task" and r.get("ok") for r in history)
                return True

            def permitted(messages, history, *, name=tool.name, check=permitted_impl):
                allowed = check(messages, history)
                self.diagnostics.policy(session_id, history, name, allowed)
                return allowed

            if self.mode == "enforce" and tool.name == "resolve_order_items":
                async def resolve_handler(args, evidence):
                    result = self.resolve(session_id, evidence["source_user_text"],
                                          turn_id=evidence["turn_id"])
                    if self.semantic is not None and result.get("status") != "resolved":
                        result = await self.semantic.propose(
                            session_id, evidence["source_user_text"], evidence["turn_id"], result)
                    return result

                def resolution_evidence(arguments, messages, history):
                    source = current_text(messages)
                    users = [m["content"] for m in messages if m.get("role") == "user"]
                    return {"source_user_text": source, "source_user_sha256": digest(source),
                            "turn_id": digest(json.dumps(users, ensure_ascii=False))}

                def required(messages, history):
                    source = current_text(messages)
                    self.store.abandon_unrelated(session_id, source)
                    if any(r["name"] == "resolve_order_items" for r in history):
                        return None
                    try:
                        parse_request(source, extended=True)
                        create = True
                    except ValueError:
                        create = needs_resolution(source)
                    if (create or selection_reply(source) or WITHDRAW.fullmatch(source)
                            or self.pending_slot(session_id, source)):
                        return {}
                    if self.semantic is not None:
                        from erp_store.intent_semantic import semantic_eligible

                        if semantic_eligible(source):
                            return {}
                    return None

                bound.append(replace(tool, validated_handler=resolve_handler,
                                     call_validator=resolution_evidence,
                                     required_read=required, params_model=ResolveOrderItemsParams,
                                     direct_response=render_clarification))
            else:
                required_read = tool.required_read
                if self.mode == "enforce" and tool.name == "get_order_task":
                    def required_task(messages, history, old=required_read):
                        source = current_text(messages)
                        text = scan_source(source).command_text
                        if (not needs_resolution(source) and not selection_reply(source)
                                and not self.pending_slot(session_id, source)
                                and re.search(r"改成|改为|修改|刷新.*报价|预算", text)
                                and not any(r["name"] == "get_order_task" for r in history)):
                            return {}
                        return old(messages, history) if old else None

                    required_read = required_task
                bound.append(replace(tool, call_validator=validate, required_read=required_read,
                                     stage_policy=permitted if self.mode == "enforce" else
                                     tool.stage_policy))
        return self._bind_recovery(bound, session_id)

    def _bind_recovery(self, tools, session_id):
        if not self.recovery.available:
            return tools
        result = []
        for tool in tools:
            if tool.name != "resolve_order_items":
                result.append(tool)
                continue

            def required(messages, history, old=tool.required_read):
                if self.recovery.blocks_reply(session_id, current_text(messages)):
                    return {}
                return old(messages, history) if old else None

            def validate(arguments, messages, history, old=tool.call_validator):
                if self.recovery.blocks_reply(session_id, current_text(messages)):
                    return {"intent_recovery": True, "source_user_text": current_text(messages)}
                return old(arguments, messages, history) if old else {}

            async def handler(args, evidence, old=tool.validated_handler, fallback=tool.handler):
                if evidence.get("intent_recovery"):
                    from erp_store.intent_recovery import RECHECK_MESSAGE

                    return clarification(RECHECK_MESSAGE, evidence["source_user_text"],
                                         question=RECHECK_MESSAGE, recovery_required=True)
                return await old(args, evidence) if old else await fallback(args)

            def render(value, old=tool.direct_response):
                if value.get("ok"):
                    try:
                        if json.loads(value["content"]).get("recovery_required"):
                            return render_clarification(value)
                    except (ValueError, TypeError, AttributeError):
                        pass
                return old(value) if old else None

            result.append(replace(tool, required_read=required, call_validator=validate,
                                  validated_handler=handler, direct_response=render))
        return result
