# 跨模型评测对照

| 模型 | 成功率 | 执行 | 成本 | tokens | 延迟(s) | 重试 | revision | 报告 |
|---|---|---|---|---|---|---|---|---|
| glm-5.3-flash | 100% | 7/7 | ≈¥0.0073 CNY | 64538 | 96 | 0 | e2236d2c6cb9 | 20261017-135412-1340f8.json |
| deepseek-v4.1-flash | 100% | 7/7 | ≈$0.0034 USD | 65645 | 45 | 0 | e2236d2c6cb9 | 20261017-135559-d286f0.json |

## 可比性核对

- strict 退出码：0；两模型各完整执行 B5 7 项、全部通过。
- source_sha256：`97a73ea3ea97dae96109eee6679098a1561995f9180df5dc9653f9db519cfb83`。
- case_sha256：`c21811f56834093cf3ae4802c6f866843abfa1f07dab16cbe55d83e8bcfc8372`。
- policy_index_sha256：`2bfab2cd9255872c49dd102752a3424690e19ff6cb0fe4c5b7980df25137444a`。
- query_vector_sha256：`1ef39ac7a1b1a8c18b6c0328ed6d8f98aac75eef7ba3cd2c171af5a591cb36a9`。
- scorer v2.6 / b5-1，固定 seed、无重试、零业务写入尝试、各自原币种计量完整，完整运行各新增嵌入 0。
- 嵌入模型 qwen3.7-text-embedding-flash，阈值 0.57；新旧运行条件不同，不将旧失败改判或拼接。
