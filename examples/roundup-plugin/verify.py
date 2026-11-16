"""Run the adapter against a disposable Roundup HTTP process."""

import argparse
import asyncio
import json
import os
import socket
import subprocess
from contextlib import suppress
from pathlib import Path
from secrets import token_urlsafe
from tempfile import TemporaryDirectory
from time import perf_counter, sleep
from uuid import uuid4

import httpx2
from agent_core.llm import TextDelta, ToolCall
from erpilot_api.auth_store import AuthStore
from erpilot_api.main import create_app
from erpilot_api.modules import load_module
from erpilot_api.plugin import BusinessSessionContext
from erpilot_api.plugin_testing import assert_write_tool_contract
from langgraph.checkpoint.memory import InMemorySaver


async def exercise(url, writer, observer, root):
    store = AuthStore(root / "runs.db")
    password = token_urlsafe(24)
    writer_id = store.add_user("writer", password, "approver").user_id
    observer_id = store.add_user("observer", password, "operator").user_id
    os.environ["ERPILOT_ROUNDUP_URL"] = url + "/rest"
    os.environ["ERPILOT_ROUNDUP_ACCOUNTS"] = json.dumps({
        writer_id: ["writer", writer], observer_id: ["observer", observer],
    })
    os.environ["ERPILOT_WRITES"] = "1"
    started = perf_counter()
    module = await load_module("roundup")
    print(f"Plugin entry point loaded in {perf_counter() - started:.3f}s")
    write_context = BusinessSessionContext("roundup-contract", writer_id, role="approver")
    title = f"Erpilot test {uuid4().hex}"
    async with module.lifespan():
        await assert_write_tool_contract(
            module, context=write_context, tool_name="create_issue",
            arguments={"title": title}, conflicting_arguments={"title": title + " changed"},
        )
        async with httpx2.AsyncClient(
            base_url=url + "/rest/", auth=("writer", writer), trust_env=False,
        ) as roundup:
            response = await roundup.get("data/issue", params={"title": title})
            response.raise_for_status()
            issue, = response.json()["data"]["collection"]
            path = f"data/issue/{issue['id']}"
            original = await roundup.get(path)
            original.raise_for_status()
            changed = await roundup.put(
                path, json={"erpilot_request": "changed"}, headers={
                    "If-Match": original.headers["etag"], "X-Requested-With": "Erpilot",
                    "Origin": url.rsplit("/", 1)[0],
                },
            )
            assert changed.status_code in {401, 403}, changed.status_code
            reread = await roundup.get(path)
            reread.raise_for_status()
            assert (reread.json()["data"]["attributes"]["erpilot_request"]
                    == original.json()["data"]["attributes"]["erpilot_request"])
        # Host role alone cannot grant a mapped actor write access at Roundup.
        upstream_read_only = BusinessSessionContext("roundup-deny", observer_id, role="approver")
        write = next(tool for tool in module.tools_for(upstream_read_only) if tool.risk)
        try:
            await write.handler(write.params_model(title="Access denied", client_token="test"))
        except ValueError as exc:
            assert "HTTP 403" in str(exc), str(exc)
        else:
            raise AssertionError("Roundup allowed the read-only account to create an issue")

    class Model:
        async def stream_chat(self, messages, tools=None):
            message = messages[-1]
            if message["role"] == "tool":
                yield TextDelta("Completed.")
            elif message["content"] == "read":
                assert {tool["function"]["name"] for tool in tools} == {"get_issue"}
                yield ToolCall("read", "get_issue", json.dumps({"issue_id": issue["id"]}))
            else:
                yield ToolCall("write", "create_issue", '{"title":"Approved through Erpilot"}')

    app = create_app(
        business_module=module, client_factory=Model, model="scripted",
        run_store_path=store.path, auth_store=store, trace_dir=root / "traces",
        checkpointer=InMemorySaver(),
    )
    async with app.router.lifespan_context(app):
        for name, message in [("observer", "read"), ("writer", "create")]:
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app), base_url="http://erpilot.test",
            ) as erpilot:
                login = await erpilot.post("/api/auth/login", json={
                    "username": name, "password": password,
                })
                login.raise_for_status()
                erpilot.headers["X-CSRF-Token"] = login.json()["csrf_token"]
                session = await erpilot.post("/api/sessions")
                session.raise_for_status()
                session_id = session.json()["session_id"]
                content = {"session_id": session_id, "message": message}
                if message == "create":
                    stream = asyncio.create_task(erpilot.post("/api/chat/stream", json=content))
                    try:
                        async with asyncio.timeout(10):
                            while not stream.done():
                                state = await erpilot.get(f"/api/sessions/{session_id}/state")
                                if (state.status_code == 404
                                        and state.json()["error"]["code"] == "session_not_found"):
                                    await asyncio.sleep(0.05)
                                    continue
                                state.raise_for_status()
                                pending = state.json()["pending_approvals"]
                                if pending:
                                    break
                                await asyncio.sleep(0.05)
                            else:
                                raise AssertionError("Erpilot returned before requesting approval")
                    finally:
                        stream.cancel()
                        with suppress(asyncio.CancelledError):
                            await stream
                    response = await erpilot.post("/api/chat/approve/stream", json={
                        "session_id": session_id, "pending_id": pending[0]["pending_id"],
                        "approved": True,
                    })
                else:
                    response = await erpilot.post("/api/chat/stream", json=content)
                response.raise_for_status()
                assert "event: done" in response.text and "plugin_result" in response.text
    print("Roundup and Erpilot API passed: login, scoped read, approval and recovery.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bin", type=Path, help="Roundup venv bin or Scripts directory")
    args = parser.parse_args()
    extension = ".exe" if os.name == "nt" else ""
    admin = args.bin.resolve() / f"roundup-admin{extension}"
    server = args.bin.resolve() / f"roundup-server{extension}"
    with TemporaryDirectory(prefix="erpilot-roundup-") as temp:
        home = Path(temp) / "tracker"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}/tickets"

        def configure(*command):
            result = subprocess.run(
                [str(admin), "-i", str(home), *command], stdout=subprocess.DEVNULL, check=False,
            )
            if result.returncode:
                raise RuntimeError(f"roundup-admin {command[0]} failed ({result.returncode})")

        configure("install", "classic", "sqlite")
        schema = home / "schema.py"
        source = schema.read_text(encoding="utf8")
        source = source.replace(
            'issue = IssueClass(db, "issue",',
            'issue = IssueClass(db, "issue", erpilot_request=String(), erpilot_title=String(),',
        )
        schema.write_text(source + """
issue.setkey('erpilot_request')
search = db.security.addPermission(name='Search', klass='issue')
for role in ('Observer', 'ErpilotWriter'):
    db.security.addRole(name=role, description='Restricted Erpilot API account')
    db.security.addPermissionToRole(role, 'Web Access')
    db.security.addPermissionToRole(role, 'Rest Access')
    db.security.addPermissionToRole(role, 'View', 'issue')
    db.security.addPermissionToRole(role, search)
db.security.addPermissionToRole('ErpilotWriter', 'Create', 'issue')
""", encoding="utf8")
        config = home / "config.ini"
        config.write_text(config.read_text(encoding="utf8")
                          .replace("#web = NO DEFAULT", f"web = {url}/")
                          .replace("#domain = NO DEFAULT", "domain = localhost")
                          .replace("#host = NO DEFAULT", "host = localhost"), encoding="utf8")
        writer, observer = token_urlsafe(24), token_urlsafe(24)
        configure("initialise", token_urlsafe(24))
        configure("create", "user", "username=writer", f"password={writer}", "roles=ErpilotWriter")
        configure("create", "user", "username=observer", f"password={observer}", "roles=Observer")
        with (Path(temp) / "roundup.log").open("w+", encoding="utf8") as log:
            process = subprocess.Popen(
                [str(server), "-n", "127.0.0.1", "-p", str(port), "-t", "none",
                 "-V", "HTTP/1.0", f"tickets={home}"],
                stdout=log, stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                with httpx2.Client(auth=("writer", writer), trust_env=False, timeout=1) as probe:
                    for _ in range(30):
                        if process.poll() is not None:
                            raise RuntimeError("Roundup server exited before serving requests")
                        try:
                            if probe.get(url + "/rest/data/issue").is_success:
                                break
                        except httpx2.TransportError:
                            pass
                        sleep(0.2)
                    else:
                        raise RuntimeError("Roundup REST endpoint did not become ready")
                asyncio.run(exercise(url, writer, observer, Path(temp)))
            except Exception:
                log.seek(0)
                print(log.read())
                raise
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)


if __name__ == "__main__":
    main()
