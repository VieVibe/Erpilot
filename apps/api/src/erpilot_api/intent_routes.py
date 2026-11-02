"""Explicit, version-bound confirmation of the entire pending creation form."""

from erp_store.intent_contract import StrictModel
from erp_store.intent_preview import IntentPreviewRef, request_preview
from fastapi import FastAPI
from pydantic import ConfigDict, Field, model_validator

from erpilot_api.draft_routes import DraftItemRequest, DraftRequest
from erpilot_api.service import SessionError, thread_id_for_session


class IntentFormItem(DraftItemRequest):
    model_config = ConfigDict(extra="forbid", strict=True)
    quantity: int = Field(gt=0, le=999999999)


class ConfirmIntentForm(DraftRequest):
    model_config = ConfigDict(extra="forbid", strict=True)
    pending_ref: IntentPreviewRef
    items: list[IntentFormItem] = Field(min_length=1, max_length=100)
    note: str | None = Field(max_length=255)
    budget_limit: float | None = Field(gt=0, le=1e12)
    target: "RevisionTarget | None" = None

    @model_validator(mode="after")
    def unique_skus(self):
        if len({r.sku.strip() for r in self.items}) != len(self.items):
            raise ValueError("请逐项核对唯一 SKU，不合并重复行")
        return self


class RevisionTarget(StrictModel):
    draft_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    revision: int = Field(ge=1)


class RevisionPreviewRequest(StrictModel):
    target: RevisionTarget
    source: str = Field(min_length=1, max_length=32000)
    request_id: str = Field(pattern=r"^[0-9a-f-]{32,36}$")


def register_intent_routes(app: FastAPI, provider, service):
    def store():
        analyzer = getattr(provider, "intent_analysis", None)
        if analyzer is None or analyzer.store is None:
            raise SessionError(503, "intent_preview_disabled", "当前环境未启用待完成请求核对")
        return analyzer.store

    @app.get("/api/sessions/{session_id}/pending-intent/preview")
    def preview(session_id: str):
        analyzer = getattr(provider, "intent_analysis", None)
        recovery = getattr(analyzer, "recovery", None)
        if analyzer is not None and analyzer.store is not None:
            return {"preview": request_preview(store(), session_id) or recovery.preview(session_id)}
        if recovery is not None and recovery.available:
            return {"preview": recovery.preview(session_id)}
        store()

    async def idle(session_id):
        snapshot = await service._runtime(session_id).get_state(thread_id_for_session(session_id))
        if snapshot.next:
            raise SessionError(409, "unfinished_run", "请先处理会话中尚未完成的操作")

    @app.post("/api/sessions/{session_id}/pending-intent/revise-preview")
    async def revise_preview(session_id: str, req: RevisionPreviewRequest):
        current_store = store()
        analyzer = provider.intent_analysis
        if analyzer.semantic is None:
            raise SessionError(503, "intent_semantic_disabled", "当前环境未启用原话修改候选")
        lock = service.lock(session_id)
        if lock.locked():
            raise SessionError(409, "session_busy", "会话正在处理请求，请完成后再核对")
        async with lock:
            await idle(session_id)
            current = current_store.current(session_id)
            if current and current["status"] != "expired":
                raise SessionError(409, "pending_intent_changed", "请先处理或撤回待完成请求")
            try:
                result = await analyzer.semantic.propose_revision(
                    session_id, req.source, req.request_id, req.target.model_dump())
            except ValueError as exc:
                raise SessionError(409, "draft_changed",
                                   "目标草稿已变化，请刷新后重新核对") from exc
            if not result.get("pending_ref"):
                raise SessionError(422, "intent_candidate_rejected", "未生成可核对的修改候选，"
                                   "请使用完整表单或明确原话；草稿未修改")
            return {"preview": request_preview(current_store, session_id)}

    @app.post("/api/sessions/{session_id}/pending-intent/confirm", status_code=201)
    async def confirm(session_id: str, req: ConfirmIntentForm):
        store()
        lock = service.lock(session_id)
        if lock.locked():
            raise SessionError(409, "session_busy", "会话正在处理请求，请完成后再核对")
        async with lock:
            await idle(session_id)
            pending = store().current(session_id)
            revising = pending and pending.get("semantic_operation") == "revise"
            if revising:
                target = pending["semantic_target"]
                if req.target is None or req.target.model_dump() != {
                        k: target[k] for k in ("draft_id", "revision")}:
                    raise SessionError(409, "intent_preview_changed",
                                       "请核对目标草稿和版本后再确认")
                return provider.drafts.revise(
                    session_id, req.target.draft_id, req.target.revision,
                    req.customer, [(r.sku, r.quantity) for r in req.items], req.note,
                    budget_limit=req.budget_limit, evidence={
                        "contract": "intent-revise-form-1",
                        "pending_ref": req.pending_ref.model_dump()},
                )
            if req.target is not None:
                raise SessionError(409, "intent_preview_changed", "目标请求已变化，请重新核对")
            return provider.drafts.prepare(
                session_id, req.customer, [(r.sku, r.quantity) for r in req.items], req.note,
                budget_limit=req.budget_limit,
                evidence={"contract": "intent-form-1", "pending_ref": req.pending_ref.model_dump()},
            )
