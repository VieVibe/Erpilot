# 项目当前状态

更新于 2026-11-16。项目定位为面向企业业务系统的可插拔 Agent 框架。真实 API 默认以未配置业务模块的模式启动，使用 `ERPILOT_MODULE` 显式选择订单、报销或开发者提供的插件。工单脚手架使用固定数据验证非 ERP 工具的接入。接入协议、运行方式及当前兼容层范围见[业务模块开发](business-modules.md)。

## 当前能力

- [业务模块开发](business-modules.md)涵盖已安装插件入口、模块工厂、宿主验证的用户与角色上下文，以及会话工具和提交对账绑定。
- [订单示例](demo-guide.md)覆盖报价草稿、版本审批、幂等提交、执行恢复、政策原文与只读补货。
- [语义预览](semantic-parsing.md)支持受约束的创建与修改候选，保存前核对来源、目录与字段范围。
- [容器部署](deployment.md)提供同源登录、会话及审批归属隔离、业务数据与 PostgreSQL checkpoint 持久化。部署范围为共享业务门店和单 API 进程。
- [开发指南](development.md)列出离线回归、浏览器验收和启用真实模型评测的条件。

历史批次可查看[验收报告](https://github.com/VieVibe/Erpilot/tree/b076ee4ff16c4338130f25db7e320abb62fc7e87/reports/acceptance)与[历次交付记录](https://github.com/VieVibe/Erpilot/blob/b076ee4ff16c4338130f25db7e320abb62fc7e87/docs/project-status.md)。
