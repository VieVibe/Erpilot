"""Workspace composition: load local credentials without printing them, then collect."""

from pathlib import Path

from agent_core.dotenv import load_dotenv
from rag.collect import main

if __name__ == "__main__":
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    main()
