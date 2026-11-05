"""Build/verify a deterministic source snapshot without changing Git state.

Only explicitly allowed project paths are included. Local environments, databases,
traces, installed dependencies and personal Codex configuration are excluded.
"""

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {"README.md", "pyproject.toml", "uv.lock", "compose.yaml",
              "compose.live.yaml", "Dockerfile", ".dockerignore",
              ".env.example", ".gitignore", ".gitattributes", ".python-version"}
DIRECTORIES = {"apps", "packages", "scripts", "docs", "tasks", "configs", ".github", "reports"}
EXCLUDED = {"node_modules", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache",
            "dist", "build", ".test-build", ".git", ".codex", ".agents", ".aws",
            ".playwright-mcp", "test-results", "playwright-report"}
SUFFIXES = {".py", ".toml", ".json", ".jsonl", ".md", ".ts", ".tsx", ".mjs",
            ".css", ".html", ".yml", ".yaml", ".lock", ".txt", ".svg", ".xml", ".csv"}
MANIFEST = "RELEASE-MANIFEST.json"


def allowed(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        return False
    if name in ROOT_FILES:
        return True
    return (len(path.parts) > 1 and path.parts[0] in DIRECTORIES
            and not set(path.parts).intersection(EXCLUDED)
            and not any(part.startswith(".env") for part in path.parts)
            and (path.suffix in SUFFIXES
                 or ((path.parts[:2] == ("docs", "assets") or path.parts[0] == "reports")
                     and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"})))


def source_names(root):
    result = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                            cwd=root, capture_output=True, check=True)
    return sorted({name for name in result.stdout.decode("utf-8").split("\0") if allowed(name)})


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def build(root, output, *, names=None):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("snapshot already exists; use a new output path")
    payloads, entries = {}, []
    for name in sorted(source_names(root) if names is None else names):
        if not allowed(name):
            raise ValueError(f"disallowed snapshot path: {name}")
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f"path escapes source root: {name}")
        if not path.is_file():
            continue  # A locally deleted tracked file stays deleted in the snapshot.
        data = path.read_bytes()
        payloads[name] = data
        entries.append({"path": name, "size": len(data), "sha256": sha(data)})
    if not entries:
        raise ValueError("empty snapshot")
    manifest = {"format": "erpilot-source-snapshot-1", "files": entries,
                "content_sha256": sha(canonical(entries)),
                "scope": "source, locks, documentation and recorded reports; no runtime data"}
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents replacement; stable metadata makes repeat builds reproducible.
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in [*sorted(payloads.items()), (MANIFEST, canonical(manifest))]:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    verify(output)
    return {"path": str(output), "files": len(entries), "sha256": sha(output.read_bytes()),
            "content_sha256": manifest["content_sha256"]}


def verify(path):
    path = Path(path)
    if path.is_dir():
        manifest = json.loads((path / MANIFEST).read_bytes())

        def read(name):
            target = path / name
            if target.is_symlink() or not target.resolve().is_relative_to(path.resolve()):
                raise ValueError("snapshot path escapes root")
            return target.read_bytes()

        names = [entry["path"] for entry in manifest["files"]]
        verify_manifest(manifest, names, read)
    else:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or names.count(MANIFEST) != 1:
                raise ValueError("duplicate entries or missing manifest")
            manifest = json.loads(archive.read(MANIFEST))
            verify_manifest(manifest, [n for n in names if n != MANIFEST], archive.read)
    return manifest["content_sha256"]


def verify_manifest(manifest, names, read):
    entries = manifest["files"]
    expected_names = [entry["path"] for entry in entries]
    if manifest.get("format") != "erpilot-source-snapshot-1":
        raise ValueError("unsupported snapshot format")
    if (len(expected_names) != len(set(expected_names)) or set(names) != set(expected_names)
            or not all(allowed(n) for n in expected_names)):
        raise ValueError("unexpected snapshot entries")
    if sha(canonical(entries)) != manifest["content_sha256"]:
        raise ValueError("manifest content digest mismatch")
    for entry in entries:
        data = read(entry["path"])
        if len(data) != entry["size"] or sha(data) != entry["sha256"]:
            raise ValueError(f"snapshot content mismatch: {entry['path']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        print(json.dumps({"verified": True, "content_sha256": verify(args.verify)}))
    else:
        print(json.dumps(build(ROOT, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
