"""ASGI authorization without buffering SSE responses or changing graph execution."""

import hmac
import json
import os
import re

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from erpilot_api.auth_store import COOKIE, AuthError
from erpilot_api.service import SessionError

MUTATING = {"POST", "PATCH", "PUT", "DELETE"}
APPROVAL_PATHS = {"/api/chat/approve", "/api/chat/approve/stream"}


class AuthMiddleware:
    def __init__(self, app, *, store, service):
        self.app, self.store, self.service = app, store, service

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        request, body, identity = Request(scope), {}, None
        path, method = scope["path"], scope["method"]
        try:
            if method in MUTATING:
                origin = request.headers.get("origin")
                if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
                    raise AuthError(403, "origin_rejected", "请求来源不匹配")
                data = bytearray()
                while True:
                    event = await receive()
                    if event["type"] == "http.disconnect":
                        return
                    data.extend(event.get("body", b""))
                    if len(data) > 2 * 1024 * 1024:
                        raise AuthError(413, "request_too_large", "请求内容过长")
                    if not event.get("more_body"):
                        break
                if data:
                    if "application/json" not in request.headers.get("content-type", ""):
                        raise AuthError(415, "json_required", "请使用 JSON 请求")
                    try:
                        body = json.loads(data)
                    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
                        raise AuthError(422, "invalid_json", "请求内容无效") from exc
                    if not isinstance(body, dict):
                        raise AuthError(422, "invalid_json", "请求须为 JSON 对象")
                elif path == "/api/auth/login":
                    raise AuthError(422, "invalid_json", "请提供账号与密码")
                original_receive, replayed = receive, False

                async def replay():
                    nonlocal replayed
                    if not replayed:
                        replayed = True
                        return {"type": "http.request", "body": bytes(data), "more_body": False}
                    return await original_receive()

                receive = replay
            if path in {"/api/auth/status", "/api/auth/login"}:
                return await self.app(scope, receive, send)
            identity, csrf = self.store.authenticate(request.cookies.get(COOKIE))
            scope.setdefault("state", {})["identity"] = identity
            scope["state"]["csrf_token"] = csrf
            if method in MUTATING and not hmac.compare_digest(
                request.headers.get("x-csrf-token", "").encode("utf-8"),
                csrf.encode("utf-8"),
            ):
                raise AuthError(403, "csrf_rejected", "安全校验已失效，请刷新后重试")
            session_match = re.match(r"^/api/sessions/([^/]+)(?:/|$)", path)
            if session_match:
                self.store.require_owner(identity, session_match[1])
            if path.startswith("/api/chat/"):
                session_id = body.get("session_id")
                if session_id:
                    self.store.require_owner(identity, session_id)
                elif path != "/api/chat/stream":
                    raise AuthError(422, "session_required", "请指定当前会话")
            if run_match := re.fullmatch(r"/api/runs/([^/]+)/events", path):
                run = self.service._run_store.get_run(run_match[1])
                if run is None:
                    raise AuthError(404, "run_not_found", "运行记录不存在")
                self.store.require_owner(identity, run["session_id"])
            needs_approval = path in APPROVAL_PATHS or bool(
                re.search(
                    r"/(confirm/stream|resume/stream)$",
                    path,
                )
            )
            if needs_approval and identity.role != "approver":
                raise AuthError(403, "approval_forbidden", "当前账号没有审批执行权限")
            if path == "/api/chat/approve":
                state = await self.service.session_state(body["session_id"])
                if not any(
                    p["pending_id"] == body.get("pending_id") for p in state["pending_approvals"]
                ):
                    raise AuthError(404, "approval_not_found", "审批不存在或已处理")
        except (AuthError, SessionError) as exc:
            if identity is not None:
                self.store.audit(
                    identity, "access_denied", path, {"http_status": exc.status, "code": exc.code}
                )
            response = JSONResponse(
                {"error": {"code": exc.code, "message": str(exc)}},
                status_code=exc.status,
                headers={"Cache-Control": "no-store"},
            )
            return await response(scope, receive, send)
        status = 500

        async def authenticated_send(event):
            nonlocal status
            if event["type"] == "http.response.start":
                status = event["status"]
                event["headers"] = [*event.get("headers", []), (b"cache-control", b"no-store")]
            await send(event)

        try:
            await self.app(scope, receive, authenticated_send)
        finally:
            if method in MUTATING:
                # HTTP acceptance is not business success. Never record prompts/passwords/tokens.
                detail = {
                    k: body[k]
                    for k in ("pending_id", "approved", "expected_revision")
                    if k in body and isinstance(body[k], (str, int, bool))
                }
                detail["http_status"] = status
                self.store.audit(
                    identity, "approval_request" if needs_approval else "api_request", path, detail
                )


def register_auth(app, store):
    @app.get("/api/auth/status")
    def auth_status():
        return {"enabled": store is not None}

    if store is None:
        return

    @app.post("/api/auth/login")
    async def login(request: Request):
        body = await request.json()
        try:
            identity, token, csrf = await run_in_threadpool(
                store.login, body.get("username"), body.get("password")
            )
        except AuthError as exc:
            return JSONResponse(
                {"error": {"code": exc.code, "message": exc.message}},
                status_code=exc.status,
                headers={"Cache-Control": "no-store"},
            )
        response = JSONResponse(
            {"user": identity.public(), "csrf_token": csrf}, headers={"Cache-Control": "no-store"}
        )
        response.set_cookie(
            COOKIE,
            token,
            max_age=store.ttl,
            httponly=True,
            samesite="strict",
            secure=os.getenv("ERPILOT_COOKIE_SECURE", "0") == "1",
            path="/",
        )
        return response

    @app.get("/api/auth/me")
    def me(request: Request):
        return {"user": request.state.identity.public(), "csrf_token": request.state.csrf_token}

    @app.post("/api/auth/logout")
    def logout(request: Request):
        store.logout(request.cookies[COOKIE])
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/", samesite="strict")
        return response

    @app.post("/api/sessions", status_code=201)
    def new_session(request: Request):
        return {"session_id": store.new_session(request.state.identity)}

    @app.get("/api/auth/audit")
    def audit(request: Request):
        return {"items": store.audits(request.state.identity)}
