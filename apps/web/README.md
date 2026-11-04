# web

React + TypeScript + Vite SPA，使用 Ant Design 6、Ant Design X 2 和 X Markdown。

## 工作台

左侧会话历史，中间流式对话，右侧按需展示订单草稿、审批和业务结果。窄屏通过抽屉访问历史与业务面板。主题集中在 src/theme.ts，布局在 WorkspaceShell.tsx 和 App.css。

保留自有 SSE 和会话恢复协议，不引入新的 Agent 后端。订单报价、库存影响和审批内容使用服务端快照；恢复失败、待审批或未完成任务时锁定新消息，避免重复写入。模型 Markdown 转义原始 HTML，并过滤危险链接。

## 运行

推荐在仓库根目录运行 `python scripts/quickstart.py` 启动无密钥完整演示。
真实模型开发时按[开发指南](../../docs/development.md)准备配置及 PostgreSQL，运行
`uv run --package erpilot-api python -m erpilot_api`，再在 apps/web 运行 `npm ci` 和 `npm run dev`。
该 Python 入口负责加载 `.env` 并兼容 Windows 的 PostgreSQL 事件循环；Vite 将 /api 代理到 127.0.0.1:8000。

npm run build 执行类型检查和生产构建；npm test 执行协议和组件回归。安装浏览器：npx playwright install chromium；运行页面验收：npm run test:ui。可通过 PLAYWRIGHT_CHROMIUM_EXECUTABLE 指定已安装的 Chromium。

`test:ui` 使用测试 API，覆盖桌面/手机布局、键盘发送、Markdown 安全、审批锁定和历史恢复失败；
其中两个真实语义预览用例需显式提供独立实验服务，默认跳过。
`test:demo` 连接正式离线 API 和临时 ERP，验证建单、预算、拒绝和刷新恢复。
`test:deployment` 连接[带账号容器](../../docs/deployment.md)，覆盖登录、账号隔离、只读角色和容器重建。
使用前须指定独立测试 Compose 项目，该测试会重建其 API。认证使用同源 cookie 与内存 CSRF token，
当前会话 ID 按账号分开保存。

## 事件协议

fetch + ReadableStream 解析 POST SSE，类型和解析见 src/protocol.ts，恢复与状态处理见 src/session.ts。后端协议变更须同步修改两侧定义。

## 已知限制

组件库增加了首屏资源量，2026-11-04 带登录构建入口约 449 KB gzip；后续功能增长时可按业务区域拆分加载。
依赖安全状态需针对锁文件独立审查，不用旧审计结论代替当前验证。
