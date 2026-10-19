# 上下文策略离线对照

固定输入检查，不代表真实模型任务成功率。
token 为 len/3 估算；耗时仅为本地投影，不含模型请求；费用未发生。

压力预算为 4000 估算 token，正式默认仍为 24000；没有修改默认策略。

| 策略 | 快照与约束完整保留 | 工具协议完整 | 预算内 | 估算输入总量 |
|---|---:|---:|---:|---:|
| full | 10/10 | 10/10 | 1/10 | 65852 |
| current | 2/10 | 10/10 | 9/10 | 35897 |
| structured | 9/10 | 10/10 | 9/10 | 15860 |

## 保留的失败与限制

- current / ctx-long-24：缺失 ['status', 'customer', 'note', 'target_sku', 'target_quantity']；预算内=True。
- current / ctx-large-middle：缺失 ['status', 'customer', 'note', 'target_sku', 'target_quantity']；预算内=True。
- current / ctx-combined-48：缺失 ['status', 'customer', 'note', 'target_sku', 'target_quantity']；预算内=True。
- current / ctx-revision：缺失 ['status', 'customer', 'note', 'target_sku', 'target_quantity']；预算内=True。
- current / ctx-ambiguous：缺失 ['status']；预算内=True。
- current / ctx-none：缺失 ['status']；预算内=True。
- current / ctx-latest-error：缺失 ['status']；预算内=True。
- current / ctx-unpersisted-constraint：缺失 ['status', 'customer', 'note', 'target_sku', 'target_quantity', 'constraint']；预算内=True。
- structured / ctx-unpersisted-constraint：缺失 ['constraint']；预算内=True。
- current / ctx-protected-overflow：缺失 []；预算内=False。
- structured / ctx-protected-overflow：缺失 []；预算内=False。

structured 仅保留最近消息、最近任务工具交换及其用户原话。
早期未持久化的独立约束仍可能丢失；受保护系统内容可能超预算。
历史摘要明确要求重新查询，不能作为实时库存或写入授权。

用例指纹：`abb945849e8a256bc86d586a189c004fdb47b0b784bebbe2a949d9ab719dbc7b`。
相关源码指纹：`54b14d0607c3b2ee7ea6ff86a2f6fc357fb9d29546dad20f3507faff1ac7c175`。
