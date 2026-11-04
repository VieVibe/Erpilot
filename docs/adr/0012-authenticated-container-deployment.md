# ADR-0012：同源容器部署与最小账号权限

日期：2026-11-04；状态：已实现，验证结果见[本批报告](../../reports/acceptance/2026-11-04-production-readiness.md)。
更新 ADR-0011 的“仅 PostgreSQL 容器、可信单用户”交付范围；保留 ADR-0010 的单 API 执行进程约束和原评审状态。

## 问题

Agent 写入的演示已有业务幂等与审批，但匿名 session_id 只能关联任务，不能证明操作者身份。
求职展示还需要可重现的前端/API启动与重建恢复。扩展框架或迁移向量库无法补上这两个缺口。

## 决策

1. 同一个镜像包含构建后的 React 页面和 FastAPI，单端口同源提供服务；PostgreSQL 为第二个服务。
   减少反向代理、跨域 cookie 和独立前端服务器配置。多阶段构建不在最终镜像保留 Node 工具链。
2. 默认部署用共享的离线脚本客户端、真实 MCP/ERP 和 PostgreSQL；真实模型通过显式 Compose 覆盖启用。
   默认部署完全不读入模型密钥，离线成功不冒充真实模型质量证据。
3. 使用标准库 PBKDF2-HMAC-SHA256、每账号独立随机盐、600,000 次派生；拒绝默认账号和明文密码。
   本轮采用可解释的标准库实现，避免增加认证服务；没有自行设计密码学算法。
4. 登录使用 256 bit 随机 opaque token，数据库只存 token 的 SHA-256 摘要、固定到期时间与账号引用。
   浏览器 cookie 为 HttpOnly / Strict，POST 等变更必须同时通过同步 CSRF token 和来源检查。
   昂贵密码派生在线程池执行，不阻塞正在流式输出的事件循环。
5. 账号、登录、会话归属和审计与 RunStore 共用 SQLite；历史查询直接按 owner 分页，不先查全量再过滤。
   新会话只由服务端签发，不认领旧匿名 ID。API 在路由之前检查会话及 run 归属，避免新增路由忘记保护。
6. operator 只能查询/准备草稿；approver 才可执行自己任务的写审批。工具构造时去掉 operator 的风险工具，
   审批接口再独立检查角色。前端可用性不构成授权，模型文本不能提升角色。
7. API 单进程，容器非 root、根只读，持久业务/账号数据和图状态分别进卷。健康检查实查依赖，不请求模型。

口令参数参考 [OWASP Password Storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)，
cookie 与 CSRF 边界参考 [Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
和 [CSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)。

## 取舍与边界

- 共享门店 ERP 事实，隔离账号的工作会话与审批；没有租户、组织、SSO、找回密码、跨用户代审批或完整 RBAC。
- 不使用 JWT：本地会话可直接撤销，8 小时固定到期；SQLite 随业务卷备份。凭证不进 localStorage。
- 进程内执行权保留，PostgreSQL checkpoint 不等于分布式锁。镜像入口固定 `workers=1`，不能横向扩展。
- 审计记录操作者、资源路径、审批 ID、决策与 HTTP 状态；SSE 的 200 不是 ERP 事务成功证明。
- 本机 HTTP 为方便复现保留非 Secure cookie；公网扩展必须配置 HTTPS / Secure cookie 和独立运维保障。
  默认端口仍只绑定回环，没有发布公开写 API。
- 不引入 Redis、消息队列、向量数据库和 Kubernetes。当前数据规模和并发需求不需要承担这些运维成本。

## 验证方式

API 正反例覆盖匿名、cookie/CSRF/Origin、会话与 run 越权、旧会话认领、只读账号的模型和直接写入、
审批重放、到期撤销、限流与审计。浏览器连接真实容器，覆盖两个视口、账号切换、重建恢复与业务提交。
保留原离线/真实模型批次；新真实模型在未知 usage 后停止，不改变历史结果。
