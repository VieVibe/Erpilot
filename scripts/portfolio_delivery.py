"""Package the existing recording and current source with a verifiable entry point."""

import argparse
import io
import json
import tempfile
import zipfile
from pathlib import Path

import portfolio_release as source

MANIFEST = "DELIVERY-MANIFEST.json"
MEDIA = ("erpilot-demo.webm", "erpilot-demo.vtt", "chapters.json",
         "order-approved.png", "replenishment.png")
NAMES = {"START-HERE.md", "erpilot-source.zip", *MEDIA}
START = """# Erpilot · 会请示的 ERP 智能体

面向 Agent 应用 / Python 后端工程的求职作品。

1. 播放 [演示视频](erpilot-demo.webm)，中文字幕见 erpilot-demo.vtt。
2. 解压 erpilot-source.zip，阅读 docs/application-kit.md：简历表述、三个技术取舍和证据入口。
3. 按源码 docs/demo-guide.md 启动无密钥本地演示。

视频使用脚本模型、演示向量和临时库，展示正式 API / LangGraph / MCP
链路。连续多轮真实模型结果见源码 docs/context-workflow.md 和对应报告；两类证据分别列示。
最新订单与政策验收、原始失败及后续版本见源码 docs/project-status.md，各批次独立列示。
另可按 docs/deployment.md 运行带账号、持久恢复的同源容器；仍限制单 API 执行进程，ERP 为共享门店。
视频展示临时匿名演示；账号和部署能力以源码及对应验收为准。

核验交付包：在解压的源码目录运行
`python scripts/portfolio_delivery.py --verify <本交付ZIP绝对路径>`。
再运行 `python scripts/portfolio_release.py --verify .` 核验解压后的源码。

源码快照包含构建时的工作区内容，内容身份由逐文件 SHA-256 清单确定；Git 提交号只作历史定位。
包中不包含真实业务库、密钥、安装依赖或原始 trace。
源码保留仓库已跟踪的政策向量索引；真实查询仍需匹配的嵌入配置，离线演示不使用它。
"""


def build(root, output, *, media_dir=None):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("delivery already exists; use a new output path")
    directory = (root / (media_dir or "artifacts/portfolio")).resolve()
    if not directory.is_relative_to(root):
        raise ValueError("media path escapes source root")
    payloads = {"START-HERE.md": START.encode("utf-8")}
    for name in MEDIA:
        path = directory / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("media path escapes source root")
        payloads[name] = path.read_bytes()
    with tempfile.TemporaryDirectory(prefix="erpilot-delivery-") as temp:
        snapshot = Path(temp) / "source.zip"
        metadata = source.build(root, snapshot)
        payloads["erpilot-source.zip"] = snapshot.read_bytes()
    entries = [{"path": n, "size": len(data), "sha256": source.sha(data)}
               for n, data in sorted(payloads.items())]
    manifest = {"format": "erpilot-portfolio-delivery-1", "files": entries,
                "content_sha256": source.sha(source.canonical(entries)),
                "source_content_sha256": metadata["content_sha256"],
                "recording": "reused-scripted-offline-demo; not a new real-model recording"}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in [*sorted(payloads.items()), (MANIFEST, source.canonical(manifest))]:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    verify(output)
    return {"path": str(output), "sha256": source.sha(output.read_bytes()),
            "source_content_sha256": metadata["content_sha256"], "files": len(entries)}


def verify(path):
    with zipfile.ZipFile(path) as archive:
        if (set(archive.namelist()) != NAMES | {MANIFEST}
                or len(archive.namelist()) != len(NAMES) + 1):
            raise ValueError("unexpected or duplicate delivery entries")
        manifest = json.loads(archive.read(MANIFEST))
        entries = manifest["files"]
        if (manifest["format"] != "erpilot-portfolio-delivery-1"
                or len(entries) != len(NAMES) or {e["path"] for e in entries} != NAMES
                or source.sha(source.canonical(entries)) != manifest["content_sha256"]):
            raise ValueError("invalid delivery manifest")
        for entry in entries:
            data = archive.read(entry["path"])
            if len(data) != entry["size"] or source.sha(data) != entry["sha256"]:
                raise ValueError(f"delivery content mismatch: {entry['path']}")
        with zipfile.ZipFile(io.BytesIO(archive.read("erpilot-source.zip"))) as snapshot:
            inner = json.loads(snapshot.read(source.MANIFEST))
            names = snapshot.namelist()
            if len(names) != len(set(names)) or names.count(source.MANIFEST) != 1:
                raise ValueError("invalid nested source entries")
            source.verify_manifest(inner, [n for n in names if n != source.MANIFEST], snapshot.read)
            if inner["content_sha256"] != manifest["source_content_sha256"]:
                raise ValueError("nested source identity mismatch")
    return manifest["content_sha256"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    parser.add_argument("--media-dir", type=Path,
                        help="录屏目录（相对仓库根目录，默认 artifacts/portfolio）")
    args = parser.parse_args()
    result = ({"verified": True, "content_sha256": verify(args.verify)} if args.verify
              else build(source.ROOT, args.output, media_dir=args.media_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
