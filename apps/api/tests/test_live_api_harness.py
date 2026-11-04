"""The real-HTTP acceptance reader must close pending SSE, not wait for approval."""

import asyncio
import importlib.util
from pathlib import Path

import httpx2
import pytest
from fastapi import FastAPI
from sse_starlette.sse import EventSourceResponse

SPEC = importlib.util.spec_from_file_location(
    "live_api_acceptance", Path(__file__).resolve().parents[3] / "scripts/live_api_acceptance.py"
)
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


@pytest.mark.parametrize("event", ["approval_pending", "done"])
async def test_network_reader_closes_at_a_complete_event_without_waiting_for_eof(event):
    app, closed = FastAPI(), asyncio.Event()

    @app.post("/stream")
    def stream():
        async def events():
            try:
                yield {"event": event, "data": '{"ok":true}'}
                await asyncio.Event().wait()
            finally:
                closed.set()

        return EventSourceResponse(events())

    async with harness.local_server(app) as address, httpx2.AsyncClient(base_url=address) as http:
        status, body = await asyncio.wait_for(
            harness.post_stream(http, "/stream", {}, pending=True), timeout=3
        )
        assert status == 200 and f"event: {event}" in body
        await asyncio.wait_for(closed.wait(), timeout=3)
