"""Single-process, authenticated deployment with persistent ERP and checkpoints."""

import asyncio
import os
import sys
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from erpilot_api.bootstrap import build_live_app
from erpilot_api.offline import create_demo_app, seed_demo_database


def initialize_database(database: Path):
    if database.exists():
        return
    temporary = database.with_name(f".seed-{uuid4().hex}.db")
    try:
        seed_demo_database(temporary)
        with suppress(FileExistsError):
            # Publish a complete file exclusively, never replace an existing ERP database.
            os.link(temporary, database)
    finally:
        temporary.unlink(missing_ok=True)


async def build_app():
    mode = os.getenv("ERPILOT_RUNTIME", "scripted")
    if mode not in {"scripted", "live"}:
        raise ValueError("ERPILOT_RUNTIME must be scripted or live")
    directory = Path(os.getenv("ERPILOT_DATA_DIR", "data/deployed"))
    directory.mkdir(parents=True, exist_ok=True)
    database = directory / "erpilot.db"
    initialize_database(database)  # Only this deployment's new synthetic database.
    trace_dir = directory / "traces"
    if mode == "scripted":
        app = await create_demo_app(database, trace_dir, persistent=True, auth_enabled=True)
    else:
        app = await build_live_app(
            database=database,
            run_store_path=database.with_name("erpilot-runs.db"),
            trace_dir=trace_dir,
            auth_enabled=True,
        )
    app.state.deployment_mode = mode

    @app.get("/readyz")
    async def ready():
        try:
            async with asyncio.timeout(3):
                await app.state.service._checkpointer.aget_tuple(
                    {"configurable": {"thread_id": "deployment-readiness"}}
                )
                with app.state.auth_store.connect() as db:
                    db.execute("SELECT 1")
            return {"status": "ready", "runtime": mode, "authentication": True}
        except Exception:
            return JSONResponse({"status": "dependencies_unavailable"}, status_code=503)

    root = Path(__file__).resolve().parents[4]
    web = Path(os.getenv("ERPILOT_WEB_DIST", str(root / "apps/web/dist")))
    if not (web / "index.html").is_file():
        raise RuntimeError("Build the frontend before starting the deployment")
    app.mount("/", StaticFiles(directory=web, html=True))
    return app


def main():
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    app = asyncio.run(build_app())
    uvicorn.run(
        app,
        host=os.getenv("ERPILOT_HOST", "127.0.0.1"),
        port=int(os.getenv("ERPILOT_PORT", "8000")),
        loop="none",
        workers=1,
        timeout_graceful_shutdown=15,
    )


if __name__ == "__main__":
    main()
