"""Run the API with an event loop compatible with psycopg on Windows."""

import asyncio
import sys
from pathlib import Path

import uvicorn
from agent_core.dotenv import find_dotenv, load_dotenv


def main() -> None:
    env_path = find_dotenv(Path.cwd())
    if env_path:
        load_dotenv(env_path)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    # Let asyncio.run() honor the Selector policy above; newer Uvicorn versions
    # otherwise pass an explicit Proactor loop factory on Windows.
    from erpilot_api.bootstrap import build_live_app

    app = asyncio.run(build_live_app())
    uvicorn.run(app, host="127.0.0.1", port=8000, loop="none", workers=1)


if __name__ == "__main__":
    main()
