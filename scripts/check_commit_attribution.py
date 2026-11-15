"""Reject known AI attribution in Git commit identities and co-author trailers."""

import re
import subprocess
import sys
from pathlib import Path

AI_NAMES = {
    "aider", "chatgpt", "claudecode", "codex", "codeium", "copilot", "cursor", "cursoragent",
    "devinai", "geminicli", "githubcopilot", "openaicodex", "windsurf",
}
AI_EMAILS = {
    "cursoragent@cursor.com", "agent@cursor.com", "copilot@github.com",
    "codex@openai.com", "noreply@anthropic.com",
}
IDENTITY = re.compile(r"^(.*?)\s*<([^>]+)>")
CO_AUTHOR = re.compile(r"^co-authored-by:[ \t]*(.*)$", re.IGNORECASE | re.MULTILINE)


def git(*args):
    return subprocess.check_output(["git", *args]).decode("utf-8", errors="replace")


def canonical_name(name):
    return re.sub(r"[^a-z0-9]", "", name.strip().lower().removesuffix("[bot]"))


def check_identity(identity, label):
    match = IDENTITY.match(identity)
    if match is None:
        return True
    name, email = match.groups()
    email = email.strip().lower()
    mailbox, _, domain = email.rpartition("@")
    github_name = re.sub(r"^\d+\+", "", mailbox)
    if (
        canonical_name(name) in AI_NAMES
        or email in AI_EMAILS
        or (domain == "users.noreply.github.com" and canonical_name(github_name) in AI_NAMES)
    ):
        print(f"{label}: AI attribution is prohibited ({name.strip()} <{email}>).", file=sys.stderr)
        return False
    return True


def check_co_authors(message, label):
    return all(check_identity(match[1], label) for match in CO_AUTHOR.finditer(message))


def check_message(path):
    author = check_identity(git("var", "GIT_AUTHOR_IDENT"), "Author")
    committer = check_identity(git("var", "GIT_COMMITTER_IDENT"), "Committer")
    message = Path(path).read_text(encoding="utf-8", errors="replace")
    return check_co_authors(message, "Co-author") and author and committer


def check_range(base, head):
    revisions = [head]
    if base.strip("0") and subprocess.run(
        ["git", "cat-file", "-e", f"{base}^{{commit}}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    ).returncode == 0:
        revisions.append(f"^{base}")
    fields = git(
        "log", "-z", "--format=%h%x00%an <%ae>%x00%cn <%ce>%x00%B", *revisions, "--",
    ).removesuffix("\0")
    if not fields:
        return True
    parts = fields.split("\0")
    for start in range(0, len(parts), 4):
        oid, author, committer, message = parts[start:start + 4]
        if not check_identity(author, f"{oid} author"):
            return False
        if not check_identity(committer, f"{oid} committer"):
            return False
        if not check_co_authors(message, f"{oid} co-author"):
            return False
    return True


def check_push():
    for line in sys.stdin:
        _, head, _, base = line.split()
        if head.strip("0") and not check_range(base, head):
            return False
    return True


def main():
    match sys.argv[1:]:
        case ["message", path]:
            valid = check_message(path)
        case ["range", base, head]:
            valid = check_range(base, head)
        case ["push"]:
            valid = check_push()
        case _:
            raise SystemExit(
                "Usage: check_commit_attribution.py {message PATH|range BASE HEAD|push}"
            )
    raise SystemExit(0 if valid else 1)


if __name__ == "__main__":
    main()
