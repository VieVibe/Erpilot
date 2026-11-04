"""Reproducible local demo and offline verification; Python standard library only."""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


class SetupError(RuntimeError):
    pass


def executable(name):
    # Prefer the batch entry point over PowerShell's npm.ps1 on Windows.
    return shutil.which(name + ".cmd") or shutil.which(name)


def uv_command():
    if path := executable("uv"):
        return [path]
    if importlib.util.find_spec("uv") is not None:
        return [sys.executable, "-m", "uv"]
    raise SetupError("缺少 uv。请先安装：https://docs.astral.sh/uv/getting-started/installation/")


def preflight(root, *, install):
    if sys.version_info < (3, 12):  # noqa: UP036 -- bootstrap runs before project installation
        raise SetupError("需要 Python 3.12 或更新版本。")
    for name in ("uv.lock", "apps/web/package-lock.json", "scripts/demo.py"):
        if not (root / name).is_file():
            raise SetupError(f"项目文件不完整：{name}")
    node, npm = executable("node"), executable("npm")
    if not node or not npm:
        raise SetupError("需要 Node.js 22+ 和 npm。安装后重新打开终端。")
    version = subprocess.run([node, "--version"], capture_output=True, text=True, check=True)
    if not (match := re.fullmatch(r"v(\d+)\.\d+\.\d+\s*", version.stdout)):
        raise SetupError("无法识别 Node.js 版本。请使用 Node.js 22+ 正式版本。")
    if int(match[1]) < 22:
        raise SetupError(f"需要 Node.js 22+，当前为 {version.stdout.strip()}。")
    uv = uv_command() if install else None
    python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not install and not python.is_file():
        raise SetupError("尚未安装工作区依赖。请去掉 --skip-install 后重试。")
    return {"python": str(python), "npm": npm, "uv": uv, "node_version": version.stdout.strip()}


def check_port(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise SetupError(f"本地端口 {port} 不可用，请使用 --port 指定其他端口。") from exc


def run_step(label, command, *, cwd, records, output=None, ci=False):
    print(f"\n→ {label}", flush=True)
    started = time.monotonic()
    record = {"name": label, "command": list(map(str, command)), "status": "running"}
    records.append(record)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    if ci:
        env["CI"] = "1"  # Browser verification must own its servers, never reuse another app.
    log = (output / f"{len(records):02d}.log").open("w", encoding="utf-8") if output else None
    try:
        with subprocess.Popen(
            command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        ) as process:
            try:
                for line in process.stdout:
                    print(line, end="", flush=True)
                    if log:
                        log.write(line)
                        log.flush()
                code = process.wait()
            except BaseException:
                # Do not leave a server or package manager behind on interruption.
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                raise
        record.update(status="passed" if code == 0 else "failed", exit_code=code)
        if code:
            raise subprocess.CalledProcessError(code, command)
    except BaseException:
        if record["status"] == "running":
            record["status"] = "interrupted" if sys.exc_info()[0] is KeyboardInterrupt else "failed"
        raise
    finally:
        record["duration_seconds"] = round(time.monotonic() - started, 3)
        if log:
            log.close()


def prepare(root, commands, run, *, install):
    if install:
        run("安装 Python 锁定依赖", [*commands["uv"], "sync", "--all-packages", "--frozen"])
        run("安装前端锁定依赖", [commands["npm"], "--prefix", str(root / "apps/web"),
                              "ci", "--no-audit", "--no-fund"])


def verify(root, commands, *, install, browser):
    output = root / "artifacts" / "verification" / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "format": "erpilot-offline-verification-1", "status": "running", "steps": [],
        "started_at": datetime.now(UTC).isoformat(), "live_model_evaluation": False,
        "postgres_configured": bool(os.environ.get("ERPILOT_TEST_CHECKPOINT_DATABASE_URL")),
        "browser_requested": browser,
        "lock_sha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                        for name in ("uv.lock", "apps/web/package-lock.json")},
    }

    def run(label, command):
        run_step(label, command, cwd=root, records=report["steps"], output=output, ci=True)

    python, npm = commands["python"], commands["npm"]
    web = [npm, "--prefix", str(root / "apps/web")]
    try:
        prepare(root, commands, run, install=install)
        run("Python 静态检查", [python, "-m", "ruff", "check", "."])
        run("Python 离线测试", [python, "-m", "pytest", "-m", "not eval",
                            f"--basetemp={output / 'tmp'}", "-o", f"cache_dir={output / 'cache'}",
                            f"--junitxml={output / 'python.xml'}"])
        run("前端协议与渲染测试", [*web, "test"])
        run("构建前端", [*web, "run", "build"])
        run("十一场景离线业务验收", [python, "scripts/demo.py"])
        if browser:
            run("安装匹配的 Chromium", [*web, "exec", "--", "playwright", "install", "chromium"])
            run("浏览器工作台回归", [*web, "run", "test:ui", "--", "--workers=2"])
            run("真实 API 离线浏览器验收", [*web, "run", "test:demo"])
        report["status"] = "passed"
    except BaseException:
        report["status"] = "interrupted" if sys.exc_info()[0] is KeyboardInterrupt else "failed"
        raise
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        (output / "verification.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"\n验收记录：{output / 'verification.json'}", flush=True)
        if not report["postgres_configured"]:
            print("PostgreSQL 未配置：相关集成测试会跳过，具体数量见 python.xml。", flush=True)


def port_number(value):
    number = int(value)
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError("端口须在 1–65535 之间")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action")
    subparsers.add_parser("doctor", help="只读检查运行环境，不读取 .env")
    demo = subparsers.add_parser("demo", help="安装、构建并启动无密钥演示（默认）")
    demo.add_argument("--port", type=port_number, default=8765)
    demo.add_argument("--skip-install", action="store_true", help="复用已安装的依赖，仍重新构建")
    verification = subparsers.add_parser("verify", help="运行离线工程验收并保存日志")
    verification.add_argument("--skip-install", action="store_true")
    verification.add_argument("--browser", action="store_true", help="另含 Chromium 和浏览器验收")
    args = parser.parse_args(argv)
    action = args.action or "demo"
    install = not getattr(args, "skip_install", False)
    try:
        commands = preflight(ROOT, install=install)
        if action == "doctor":
            print(f"环境可用：Python {sys.version.split()[0]} · "
                  f"Node {commands['node_version']} · npm · uv")
            print("演示不需要 .env、模型密钥或 PostgreSQL。首次安装需要网络。")
            return 0
        if action == "verify":
            verify(ROOT, commands, install=install, browser=args.browser)
            return 0
        port = getattr(args, "port", 8765)
        check_port(port)
        records = []

        def run(label, command):
            run_step(label, command, cwd=ROOT, records=records)

        prepare(ROOT, commands, run, install=install)
        run("构建前端", [commands["npm"], "--prefix", str(ROOT / "apps/web"), "run", "build"])
        check_port(port)
        print(f"\n打开 http://127.0.0.1:{port} · 按 Ctrl+C 结束", flush=True)
        run("启动离线脚本演示", [commands["python"], str(ROOT / "scripts/demo.py"),
                            "--serve", "--port", str(port)])
        return 0
    except SetupError as exc:
        print(f"启动检查未通过：{exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"步骤失败（退出码 {exc.returncode}）。请检查上方输出。", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"无法运行本地工具：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已停止。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    # Keep redirected Windows output readable and able to print Node's Unicode logs.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
