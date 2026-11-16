# Erpilot Roundup 工单插件

这个目录可单独打包成 Python 插件。Erpilot 通过 `roundup` 入口加载，查询和创建在独立运行的 Roundup 工单系统执行。插件按 Erpilot 用户 ID 选择 Roundup 账号，由 Roundup 的权限决定结果。

## 在隔离测试环境验收

以下命令从 Erpilot 仓库根目录执行，使用 PowerShell。两个虚拟环境分别安装宿主和 Roundup 2.6.0。

```powershell
$env:UV_PROJECT_ENVIRONMENT = '.venv/host-only'
uv sync --package erpilot-api --no-dev --frozen
uv pip install --python .venv/host-only/Scripts/python.exe --no-deps -e ./examples/roundup-plugin
uv venv .venv/roundup
uv pip install --python .venv/roundup/Scripts/python.exe roundup==2.6.0
.venv/host-only/Scripts/python.exe examples/roundup-plugin/verify.py .venv/roundup/Scripts
Remove-Item Env:UV_PROJECT_ENVIRONMENT
```

验收临时启动一个只监听本机的 Roundup，生成两个权限不同的测试账号，再关闭进程并清理测试库。通过 Erpilot 的真实登录、查询和审批接口验证账号映射与结果卡。通过脚本模型和隔离运行时验证审批拒绝、上游权限拒绝、恢复字段不可改写、响应丢失后的核对、同请求号内容冲突和唯一工单。

## 连接一个测试实例

- 将 `ERPILOT_MODULE` 设为 `roundup`，`ERPILOT_ROUNDUP_URL` 设为该 tracker 的 REST 根地址。
- 在服务端配置 `ERPILOT_ROUNDUP_ACCOUNTS`。值是 JSON 字典，键为 Erpilot 的稳定用户 ID，值是 Roundup 的 `[username, password]`。启用 Erpilot 登录。非本机连接使用 HTTPS。
- 创建工单需要 `ERPILOT_WRITES=1`、Erpilot 的审批权限和 Roundup 的创建权限。
- 写入契约要求 Roundup 的 issue 类具有唯一键 `erpilot_request` 和字符串字段 `erpilot_title`。字段和原始请求号随工单写入，作为恢复依据。测试 schema 见 `verify.py`。复用现有 tracker 时，需先配置有权查询这两个字段的测试角色，避免让普通用户修改它们。

[Roundup REST 接口与权限](https://www.roundup-tracker.org/docs/rest.html) · [Roundup 的唯一键](https://www.roundup-tracker.org/docs/reference.html)
