"""evals 测试共享件：小规模确定性种子库 + 占位符解析 + MCP 工具面。

LLM 在 HTTP 边界 mock（agent_core.testing）的用例不烧 token；真实链路评测
在 test_evals_live.py（marker `eval`，默认排除）。
"""

import os
from pathlib import Path

import pytest
from erp_store.db import make_engine
from erp_store.repository import ErpRepository
from erp_store.seed import seed_database
from mcp_erp import build_agent_tools


@pytest.fixture(scope="module")
def live_environment():
    """Load local credentials only for selected live tests, then restore the environment."""
    from agent_core.dotenv import find_dotenv, load_dotenv

    original = dict(os.environ)
    try:
        if (dotenv := find_dotenv(Path(__file__).resolve())) is not None:
            load_dotenv(dotenv)
        yield
    finally:
        for key in set(os.environ) - original.keys():
            del os.environ[key]
        os.environ.update(original)


@pytest.fixture(scope="module")
def live_llm_config(live_environment):
    from agent_core.llm import LLMConfig

    try:
        return LLMConfig.from_env()
    except RuntimeError as exc:
        if str(exc).startswith("缺少 LLM_API_KEY"):
            pytest.skip("未配置模型密钥，跳过真实链路评测")
        raise


@pytest.fixture(scope="package")
def seeded_db(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("evals") / "erp.db"
    seed_database(path, n_products=60, n_orders=80)
    return path


@pytest.fixture(scope="package")
def repo(seeded_db) -> ErpRepository:
    return ErpRepository(make_engine(seeded_db))


@pytest.fixture(scope="package")
def resolved(repo) -> dict[str, str]:
    from evals.context import resolve

    return resolve(repo)


@pytest.fixture(scope="package")
def tools(seeded_db) -> list:
    return build_agent_tools(seeded_db)


@pytest.fixture(scope="package")
def write_tools(seeded_db) -> list:
    """写工具面 + AutoDenyGate（评测环境无真人，ADR-0005 决策 6）。"""
    from agent_core.approval import AutoDenyGate

    return build_agent_tools(seeded_db, writes=True, approval_gate=AutoDenyGate())
