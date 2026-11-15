# Erpilot 代码贡献

- 提交和推送时使用本仓库已核实的本人 Git 身份。
- 禁止将 AI 工具或代理填入 Git 作者、提交者或 `Co-authored-by`。不要追加 AI 共同作者尾注。
- 启用本地校验时，在仓库根目录执行 `git config --local core.hooksPath .githooks`。钩子需要 PATH 中可用的 Python。
- GitHub 的 `Commit attribution` 检查会校验新提交的署名。将其设为 `main` 的必需状态检查，可在合并前拦截违规提交。
