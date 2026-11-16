"""Serve a local authenticated workbench against the real Le Shen ERP."""

import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from agent_core.dotenv import load_dotenv
from agent_core.llm import LLMClient, LLMConfig
from erpilot_api.auth_store import AuthStore
from erpilot_api.main import create_app
from erpilot_api.modules import BusinessSessionContext, load_module
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.memory import InMemorySaver
from le_shen_erp import DEFAULT_HOST_USER


def main():
    project = Path(__file__).resolve().parents[2]
    load_dotenv(Path(__file__).with_name(".env.local"))
    load_dotenv(project / ".env")
    config = LLMConfig.from_env()
    username = os.getenv("LESHEN_ERP_HOST_USER", DEFAULT_HOST_USER).strip().lower()
    password = os.environ.pop("LESHEN_DEMO_PASSWORD")
    module = asyncio.run(load_module("le_shen_erp:create_module"))
    web = project / "apps/web/dist"
    if not (web / "index.html").is_file():
        raise RuntimeError("Build the Erpilot web frontend first")

    with TemporaryDirectory(prefix="erpilot-leshen-workbench-") as directory:
        root = Path(directory)
        auth = AuthStore(root / "runs.db")
        user = auth.add_user(username, password)
        tools = module.tools_for(BusinessSessionContext(
            "startup", user.user_id, user.username, user.role,
        ))
        for tool in tools:
            result = asyncio.run(tool.handler(tool.params_model(limit=1)))
            if result["status"] != "ok":
                raise RuntimeError(f"Le Shen ERP startup check failed for "
                                   f"{tool.name}: {result['status']}")
        app = create_app(
            business_module=module,
            auth_store=auth,
            run_store_path=auth.path,
            trace_dir=root / "trace",
            checkpointer=InMemorySaver(),
            client_factory=lambda: LLMClient(config, request_timeout=45),
            model=config.model,
        )
        app.mount("/", StaticFiles(directory=web, html=True))
        print(f"Le Shen ERP workbench using {config.model} with {len(tools)} read-only tools")
        uvicorn.run(app, host="127.0.0.1", port=8769)


if __name__ == "__main__":
    main()
