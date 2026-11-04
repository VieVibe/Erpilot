"""Manage local accounts without default passwords or credentials in shell history."""

import argparse
import getpass
import os
from pathlib import Path

from erpilot_api.auth_store import ROLES, AuthStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("add", "disable"))
    parser.add_argument("username")
    parser.add_argument("--role", choices=sorted(ROLES), default="operator")
    parser.add_argument(
        "--database", type=Path, default=Path(os.getenv("ERPILOT_RUN_STORE_PATH", "data/runs.db"))
    )
    args = parser.parse_args()
    store = AuthStore(args.database)
    try:
        if args.command == "add":
            password = getpass.getpass("Password (12+ characters): ")
            if password != getpass.getpass("Repeat password: "):
                raise ValueError("passwords do not match")
            identity = store.add_user(args.username, password, args.role)
            print(f"Created {identity.username} ({identity.role})")
        else:
            store.disable_user(args.username)
            print("Account disabled; all its logins revoked")
    except ValueError as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    main()
