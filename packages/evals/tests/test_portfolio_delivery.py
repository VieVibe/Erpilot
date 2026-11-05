import importlib
import zipfile
from pathlib import Path

import pytest


@pytest.fixture
def delivery(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts"))
    module = importlib.import_module("portfolio_delivery")
    root = tmp_path / "source"
    media = root / "artifacts" / "portfolio"
    media.mkdir(parents=True)
    (root / "README.md").write_text("test source", encoding="utf-8")
    for name in module.MEDIA:
        (media / name).write_bytes(b"scripted offline demo fixture")
    monkeypatch.setattr(module.source, "source_names", lambda root: ["README.md"])
    return module, root


def test_delivery_is_repeatable_and_checks_nested_source(delivery, tmp_path):
    module, root = delivery
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    one, two = module.build(root, first), module.build(root, second)
    assert one["sha256"] == two["sha256"]
    assert module.verify(first)
    with zipfile.ZipFile(first) as archive:
        assert set(archive.namelist()) == module.NAMES | {module.MANIFEST}
        assert "脚本模型" in archive.read("START-HERE.md").decode()
    with pytest.raises(ValueError, match="already exists"):
        module.build(root, first)


def test_delivery_rejects_media_tampering(delivery, tmp_path):
    module, root = delivery
    original = tmp_path / "original.zip"
    module.build(root, original)
    changed = tmp_path / "changed.zip"
    with zipfile.ZipFile(original) as incoming, zipfile.ZipFile(changed, "x") as outgoing:
        for name in incoming.namelist():
            data = b"tampered" if name == "erpilot-demo.webm" else incoming.read(name)
            outgoing.writestr(name, data)
    with pytest.raises(ValueError, match="content mismatch"):
        module.verify(changed)


def test_selected_recording_is_packaged_without_replacing_historical_media(delivery, tmp_path):
    module, root = delivery
    current = root / "artifacts" / "current-recording"
    current.mkdir()
    for name in module.MEDIA:
        (current / name).write_bytes(b"current recording")
    output = tmp_path / "current.zip"
    module.build(root, output, media_dir="artifacts/current-recording")
    with zipfile.ZipFile(output) as archive:
        assert archive.read("erpilot-demo.webm") == b"current recording"
    assert (root / "artifacts/portfolio/erpilot-demo.webm").read_bytes() != b"current recording"
    with pytest.raises(ValueError, match="escapes"):
        module.build(root, tmp_path / "outside.zip", media_dir=tmp_path)
