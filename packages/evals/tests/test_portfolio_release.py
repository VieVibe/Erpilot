import importlib.util
import json
import zipfile
from pathlib import Path

import pytest


@pytest.mark.parametrize("name", ["Dockerfile", ".dockerignore", "compose.live.yaml"])
def test_deployment_entrypoints_are_in_source_snapshot(name):
    assert release.allowed(name)

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "portfolio_release.py"
spec = importlib.util.spec_from_file_location("portfolio_release", SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def test_snapshot_is_reproducible_and_verifies_after_extraction(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "README.md").write_text("source", encoding="utf-8")
    first, second = tmp_path / "one.zip", tmp_path / "two.zip"
    one = release.build(root, first, names=["README.md"])
    two = release.build(root, second, names=["README.md"])
    assert one["sha256"] == two["sha256"]
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(first) as archive:
        archive.extractall(extracted)
    assert release.verify(extracted) == one["content_sha256"]
    (extracted / "README.md").write_text("modified", encoding="utf-8")
    with pytest.raises(ValueError, match="content mismatch"):
        release.verify(extracted)


@pytest.mark.parametrize("name", [".env", "apps/api/.env.local", "../README.md",
                                 "apps/web/node_modules/key.json", "data/erp.db",
                                 "packages/.aws/credentials", "C:/secrets.json"])
def test_sensitive_or_escaping_paths_are_rejected(name):
    assert not release.allowed(name)


def test_document_screenshots_survive_snapshot_with_their_references(tmp_path):
    image = tmp_path / "docs/assets/workspace.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"recorded UI screenshot fixture")
    (tmp_path / "README.md").write_text("![demo](docs/assets/workspace.png)", encoding="utf-8")
    (tmp_path / ".gitattributes").write_text("reports/** -text\n", encoding="utf-8")
    output = tmp_path / "source.zip"
    release.build(tmp_path, output, names=["README.md", "docs/assets/workspace.png",
                                         ".gitattributes"])
    with zipfile.ZipFile(output) as archive:
        assert archive.read("docs/assets/workspace.png") == image.read_bytes()
        assert archive.read(".gitattributes") == (tmp_path / ".gitattributes").read_bytes()
    assert not release.allowed("reports/old-screenshot.png")
    assert not release.allowed("artifacts/verification/screenshot.png")


def test_archive_rejects_extra_entries_and_tampered_manifest(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "README.md").write_text("source", encoding="utf-8")
    output = tmp_path / "source.zip"
    release.build(root, output, names=["README.md"])
    with zipfile.ZipFile(output, "a") as archive:
        archive.writestr("apps/extra.py", "print('extra')")
    with pytest.raises(ValueError, match="unexpected"):
        release.verify(output)
    manifest = {"format": "erpilot-source-snapshot-1", "files": [], "content_sha256": "fake"}
    (root / release.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        release.verify(root)
