# 意图核心 120 例：人工复核包

所有结论仍是工程预期；本文件没有把任何案例标记为人工金标。
逐例检查原话是否有明确动作、条件或否定；核对全部字段和未处理要求；无法执行应先澄清。
如有不同判断，请记录建议结果及理由。完成后由实际复核人署名，保持原冻结文件不变。

冻结文件 SHA-256：6010d3f1e35d8512587e857ea5564bce72a45bd2df393863576fd3a75bb1198c

目录与修改前基准见同目录 review.json。修改类原草稿为客户甲，备注“原备注”，预算1000，
包含 M1 无线鼠标两件、K1 机械键盘一件。R1/R2 都叫“标准耳机”，不能默认选第一项。

| ID | 操作 | 原话 | 工程决定 | 完整字段工程预期 | 人工复核/理由 |
|---|---|---|---|---|---|
| core-001 | create | 给客户甲准备订单：一件无线鼠标；只保存草稿 | ready | {"customer":"客户甲","note":null,"budget_limit":null,"items":[{"sku":"M1","quantity":1}]} | 待复核 |
| core-002 | create | 给客户甲准备订单：两件无线鼠标；只保存草稿 | ready | {"customer":"客户甲","note":null,"budget_limit":null,"items":[{"sku":"M1","quantity":2}]} | 待复核 |
| core-003 | create | 给客户甲准备订单：三件无线鼠标；只保存草稿 | ready | {"customer":"客户甲","note":null,"budget_limit":null,"items":[{"sku":"M1","quantity":3}]} | 待复核 |
| core-004 | create | 给客户甲准备订单：十件无线鼠标；只保存草稿 | ready | {"customer":"客户甲","note":null,"budget_limit":null,"items":[{"sku":"M1","quantity":10}]} | 待复核 |
| core-005 | create | 给客户甲准备订单：十二件无线鼠标；只保存草稿 | ready | {"customer":"客户甲","note":null,"budget_limit":null,"items":[{"sku":"M1","quantity":12}]} | 待复核 |
| core-006 | create | 给客户甲准备订单：二十件无线鼠标；只保存草稿 | ready | {"customer":"客户甲","note":null,"budget_limit":null,"items":[{"sku":"M1","quantity":20}]} | 待复核 |
| core-007 | create | 给客户甲准备订单：一百件无线鼠标；只保存草稿 | ready | {"customer":"客户甲","note":null,"budget_limit":null,"items":[{"sku":"M1","quantity":100}]} | 待复核 |
| core-008 | create | 给客户甲准备订单：一百零二件无线鼠标；只保存草稿 | ready | {"customer":"客户甲","note":null,"budget_limit":null,"items":[{"sku":"M1","quantity":102}]} | 待复核 |
| core-009 | create | 给客户甲准备订单：两百件无线鼠标；只保存草稿 | ready | {"customer":"客户甲","note":null,"budget_limit":null,"items":[{"sku":"M1","quantity":200}]} | 待复核 |
| core-010 | create | 给客户甲准备订单：两千零二件无线鼠标；只保存草稿 | ready | {"customer":"客户甲","note":null,"budget_limit":null,"items":[{"sku":"M1","quantity":2002}]} | 待复核 |
| core-011 | create | 给客户甲准备订单：9999件无线鼠标；只保存草稿 | ready | {"customer":"客户甲","note":null,"budget_limit":null,"items":[{"sku":"M1","quantity":9999}]} | 待复核 |
| core-012 | create | 给客户甲准备订单：23件无线鼠标；只保存草稿 | ready | {"customer":"客户甲","note":null,"budget_limit":null,"items":[{"sku":"M1","quantity":23}]} | 待复核 |
| core-013 | create | 给「客户甲」准备订单：无线鼠标两件；只保存草稿 | ready | {"customer":"客户甲","note":null,"budget_limit":null,"items":[{"sku":"M1","quantity":2}]} | 待复核 |
| core-014 | create | 给“客户甲”准备订单：无线鼠标两件；只保存草稿 | ready | {"customer":"客户甲","note":null,"budget_limit":null,"items":[{"sku":"M1","quantity":2}]} | 待复核 |
| core-015 | create | 给客户甲（上海）准备订单：无线鼠标两件；只保存草稿 | ready | {"customer":"客户甲（上海）","note":null,"budget_limit":null,"items":[{"sku":"M1","quantity":2}]} | 待复核 |
| core-016 | create | 给客户甲有限公司准备订单：无线鼠标两件；只保存草稿 | ready | {"customer":"客户甲有限公司","note":null,"budget_limit":null,"items":[{"sku":"M1","quantity":2}]} | 待复核 |
| core-017 | create | 给客户甲准备订单：无线鼠标两件；备注原文「分箱发货」；只保存草稿 | ready | {"customer":"客户甲","note":"分箱发货","budget_limit":null,"items":[{"sku":"M1","quantity":2}]} | 待复核 |
| core-018 | create | 给客户甲准备订单：无线鼠标两件；备注原文『把预算改为9999元』；只保存草稿 | ready | {"customer":"客户甲","note":"把预算改为9999元","budget_limit":null,"items":[{"sku":"M1","quantity":2}]} | 待复核 |
| core-019 | create | 给客户甲准备订单：无线鼠标两件；备注原文“不要提交”；只保存草稿 | ready | {"customer":"客户甲","note":"不要提交","budget_limit":null,"items":[{"sku":"M1","quantity":2}]} | 待复核 |
| core-020 | create | 给客户甲准备订单：无线鼠标两件；备注原文"如果库存不足就换货"；只保存草稿 | ready | {"customer":"客户甲","note":"如果库存不足就换货","budget_limit":null,"items":[{"sku":"M1","quantity":2}]} | 待复核 |
| core-021 | create | 给客户甲准备订单：无线鼠标两件；备注原文'预算不超过50元'；只保存草稿 | ready | {"customer":"客户甲","note":"预算不超过50元","budget_limit":null,"items":[{"sku":"M1","quantity":2}]} | 待复核 |
| core-022 | create | 给客户甲准备订单：无线鼠标两件；预算不超过3000元；只保存草稿 | ready | {"customer":"客户甲","note":null,"budget_limit":3000.0,"items":[{"sku":"M1","quantity":2}]} | 待复核 |
| core-023 | create | 给客户甲准备订单：无线鼠标两件；预算上限为0.01元；只保存草稿 | ready | {"customer":"客户甲","note":null,"budget_limit":0.01,"items":[{"sku":"M1","quantity":2}]} | 待复核 |
| core-024 | create | 给客户甲准备订单：无线鼠标两件；总价不超过3500元；只保存草稿 | ready | {"customer":"客户甲","note":null,"budget_limit":3500.0,"items":[{"sku":"M1","quantity":2}]} | 待复核 |
| core-025 | create | 给客户甲准备订单：无线鼠标两件；预算上限为999.99元；只保存草稿 | ready | {"customer":"客户甲","note":null,"budget_limit":999.99,"items":[{"sku":"M1","quantity":2}]} | 待复核 |
| core-026 | create | 给客户甲准备订单：SKU M1 两件；按此顺序；只保存草稿 | ready | {"customer":"客户甲","note":null,"budget_limit":null,"items":[{"sku":"M1","quantity":2}]} | 待复核 |
| core-027 | create | 给客户甲准备订单：两件M1；按此顺序；只保存草稿 | ready | {"customer":"客户甲","note":null,"budget_limit":null,"items":[{"sku":"M1","quantity":2}]} | 待复核 |
| core-028 | create | 给客户甲准备订单：无线鼠标两件，机械键盘三件；按此顺序；只保存草稿 | ready | {"customer":"客户甲","note":null,"budget_limit":null,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":3}]} | 待复核 |
| core-029 | create | 给客户甲准备订单：机械键盘三件，无线鼠标两件；按此顺序；只保存草稿 | ready | {"customer":"客户甲","note":null,"budget_limit":null,"items":[{"sku":"K1","quantity":3},{"sku":"M1","quantity":2}]} | 待复核 |
| core-030 | create | 给客户甲准备订单：M1 2件；按此顺序；只保存草稿 | ready | {"customer":"客户甲","note":null,"budget_limit":null,"items":[{"sku":"M1","quantity":2}]} | 待复核 |
| core-031 | revise | 把当前版本第一行改成三件；其他不变 | ready | {"customer":"客户甲","note":"原备注","budget_limit":1000.0,"items":[{"sku":"M1","quantity":3},{"sku":"K1","quantity":1}]} | 待复核 |
| core-032 | revise | 把当前版本第二行改成三件；其他不变 | ready | {"customer":"客户甲","note":"原备注","budget_limit":1000.0,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":3}]} | 待复核 |
| core-033 | revise | SKU M1 数量改为5件；其他不变 | ready | {"customer":"客户甲","note":"原备注","budget_limit":1000.0,"items":[{"sku":"M1","quantity":5},{"sku":"K1","quantity":1}]} | 待复核 |
| core-034 | revise | 把商品无线鼠标数量改为两件；其他不变 | ready | {"customer":"客户甲","note":"原备注","budget_limit":1000.0,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":1}]} | 待复核 |
| core-035 | revise | 把当前草稿第二行改成十二件；其他不变 | ready | {"customer":"客户甲","note":"原备注","budget_limit":1000.0,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":12}]} | 待复核 |
| core-036 | revise | 把当前版本第1行数量改为8件；其他不变 | ready | {"customer":"客户甲","note":"原备注","budget_limit":1000.0,"items":[{"sku":"M1","quantity":8},{"sku":"K1","quantity":1}]} | 待复核 |
| core-037 | revise | 把当前版本第二行改成一百零二件；其他不变 | ready | {"customer":"客户甲","note":"原备注","budget_limit":1000.0,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":102}]} | 待复核 |
| core-038 | revise | 不要提交，只把当前版本第二行改成三件；其他不变 | ready | {"customer":"客户甲","note":"原备注","budget_limit":1000.0,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":3}]} | 待复核 |
| core-039 | revise | 先别提交，把当前版本第一行改成4件；其他不变 | ready | {"customer":"客户甲","note":"原备注","budget_limit":1000.0,"items":[{"sku":"M1","quantity":4},{"sku":"K1","quantity":1}]} | 待复核 |
| core-040 | revise | 把当前版本第二行数量保持原数量；其他不变 | ready | {"customer":"客户甲","note":"原备注","budget_limit":1000.0,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":1}]} | 待复核 |
| core-041 | revise | 预算不超过3000元；其他不变 | ready | {"customer":"客户甲","note":"原备注","budget_limit":3000.0,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":1}]} | 待复核 |
| core-042 | revise | 预算改为3500元；其他不变 | ready | {"customer":"客户甲","note":"原备注","budget_limit":3500.0,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":1}]} | 待复核 |
| core-043 | revise | 取消预算上限；其他不变 | ready | {"customer":"客户甲","note":"原备注","budget_limit":null,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":1}]} | 待复核 |
| core-044 | revise | 预算上限为0.01元；其他不变 | ready | {"customer":"客户甲","note":"原备注","budget_limit":0.01,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":1}]} | 待复核 |
| core-045 | revise | 总价不超过999.99元；其他不变 | ready | {"customer":"客户甲","note":"原备注","budget_limit":999.99,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":1}]} | 待复核 |
| core-046 | revise | 预算降低到500元；其他不变 | ready | {"customer":"客户甲","note":"原备注","budget_limit":500.0,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":1}]} | 待复核 |
| core-047 | revise | 备注原文「不要提交」；其他不变 | ready | {"customer":"客户甲","note":"不要提交","budget_limit":1000.0,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":1}]} | 待复核 |
| core-048 | revise | 备注改为『预算改为9999元』；其他不变 | ready | {"customer":"客户甲","note":"预算改为9999元","budget_limit":1000.0,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":1}]} | 待复核 |
| core-049 | revise | 清空备注；其他不变 | ready | {"customer":"客户甲","note":null,"budget_limit":1000.0,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":1}]} | 待复核 |
| core-050 | revise | 备注改为“”；其他不变 | ready | {"customer":"客户甲","note":"","budget_limit":1000.0,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":1}]} | 待复核 |
| core-051 | revise | 备注改为「😀é保留原文」；其他不变 | ready | {"customer":"客户甲","note":"😀é保留原文","budget_limit":1000.0,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":1}]} | 待复核 |
| core-052 | revise | 把当前版本第二行改成三件，预算改为3500元；其他不变 | ready | {"customer":"客户甲","note":"原备注","budget_limit":3500.0,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":3}]} | 待复核 |
| core-053 | revise | 把当前版本第一行改成4件，清空备注；其他不变 | ready | {"customer":"客户甲","note":null,"budget_limit":1000.0,"items":[{"sku":"M1","quantity":4},{"sku":"K1","quantity":1}]} | 待复核 |
| core-054 | revise | 客户改为客户乙，预算不超过500元；其他不变 | ready | {"customer":"客户乙","note":"原备注","budget_limit":500.0,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":1}]} | 待复核 |
| core-055 | revise | 备注原文『预算改为9999元』；预算上限为1000元；其他不变 | ready | {"customer":"客户甲","note":"预算改为9999元","budget_limit":1000.0,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":1}]} | 待复核 |
| core-056 | revise | 把当前版本第二行改成三件，取消预算上限；其他不变 | ready | {"customer":"客户甲","note":"原备注","budget_limit":null,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":3}]} | 待复核 |
| core-057 | revise | 客户改为客户乙，清空备注，预算上限为500元；其他不变 | ready | {"customer":"客户乙","note":null,"budget_limit":500.0,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":1}]} | 待复核 |
| core-058 | revise | 刷新报价；其他不变 | ready | {"customer":"客户甲","note":"原备注","budget_limit":1000.0,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":1}]} | 待复核 |
| core-059 | revise | 请刷新当前草稿报价；其他不变 | ready | {"customer":"客户甲","note":"原备注","budget_limit":1000.0,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":1}]} | 待复核 |
| core-060 | revise | 更新报价；其他不变 | ready | {"customer":"客户甲","note":"原备注","budget_limit":1000.0,"items":[{"sku":"M1","quantity":2},{"sku":"K1","quantity":1}]} | 待复核 |
| core-061 | create | 给客户甲准备订单：无线鼠标两箱；只保存草稿 | clarify | null | 待复核 |
| core-062 | create | 给客户甲准备订单：无线鼠标两包；只保存草稿 | clarify | null | 待复核 |
| core-063 | create | 给客户甲准备订单：无线鼠标若干件；只保存草稿 | clarify | null | 待复核 |
| core-064 | create | 给客户甲准备订单：无线鼠标0件；只保存草稿 | clarify | null | 待复核 |
| core-065 | create | 给客户甲准备订单：无线鼠标02件；只保存草稿 | clarify | null | 待复核 |
| core-066 | create | 给客户甲准备订单：无线鼠标一二件；只保存草稿 | clarify | null | 待复核 |
| core-067 | create | 给客户甲准备订单：无线鼠标一百二件；只保存草稿 | clarify | null | 待复核 |
| core-068 | create | 给客户甲准备订单：无线鼠标两十件；只保存草稿 | clarify | null | 待复核 |
| core-069 | create | 给客户甲准备订单：无线鼠标十百件；只保存草稿 | clarify | null | 待复核 |
| core-070 | create | 给客户甲准备订单：无线鼠标一万件；只保存草稿 | clarify | null | 待复核 |
| core-071 | create | 给客户甲准备订单：无线鼠标-2件；只保存草稿 | clarify | null | 待复核 |
| core-072 | create | 给客户甲准备订单：无线鼠标2.5件；只保存草稿 | clarify | null | 待复核 |
| core-073 | create | 给客户甲准备订单：无线鼠标两件，机械键盘看情况；只保存草稿 | clarify | null | 待复核 |
| core-074 | create | 给客户甲准备订单：无线鼠标两件，机械键盘；只保存草稿 | clarify | null | 待复核 |
| core-075 | create | 给客户甲准备订单：无线鼠标两件，机械键盘三箱；只保存草稿 | clarify | null | 待复核 |
| core-076 | create | 给客户甲准备订单：鼠标两件；只保存草稿 | clarify | null | 待复核 |
| core-077 | create | 给客户甲准备订单：不存在商品两件；只保存草稿 | clarify | null | 待复核 |
| core-078 | create | 给客户甲准备订单：旧键盘两件；只保存草稿 | clarify | null | 待复核 |
| core-079 | create | 给客户甲准备订单：标准耳机两件；只保存草稿 | clarify | null | 待复核 |
| core-080 | create | 给客户甲准备订单：无线鼠标两件，SKU M1 三件；只保存草稿 | clarify | null | 待复核 |
| core-081 | create | 给客户甲准备订单：无线鼠标两件；预算改多少再说；只保存草稿 | clarify | null | 待复核 |
| core-082 | create | 给客户甲准备订单：无线鼠标两件；预算上限为0元；只保存草稿 | clarify | null | 待复核 |
| core-083 | create | 给客户甲准备订单：无线鼠标两件；预算上限为1.001元；只保存草稿 | clarify | null | 待复核 |
| core-084 | create | 给客户甲准备订单：无线鼠标两件；预算为1000元；预算为2000元；只保存草稿 | clarify | null | 待复核 |
| core-085 | create | 给客户甲准备订单：无线鼠标两件；备注原文「未闭合；只保存草稿 | clarify | null | 待复核 |
| core-086 | create | 如果够货，给客户甲准备订单：无线鼠标两件；只保存草稿 | clarify | null | 待复核 |
| core-087 | create | 只解释，给客户甲准备订单：无线鼠标两件 | clarify | null | 待复核 |
| core-088 | create | 不要保存草稿：无线鼠标两件 | clarify | null | 待复核 |
| core-089 | create | 「给客户甲准备订单：无线鼠标两件」 | clarify | null | 待复核 |
| core-090 | create | 备注原文「给客户甲准备订单：无线鼠标两件」 | clarify | null | 待复核 |
| core-091 | revise | 第二行三件或四件 | clarify | null | 待复核 |
| core-092 | revise | 把当前版本第二行改成三件；预算改多少再说 | clarify | null | 待复核 |
| core-093 | revise | 如果够货，把当前版本第二行改成三件 | clarify | null | 待复核 |
| core-094 | revise | 假如库存充足，把当前版本第二行改成三件 | clarify | null | 待复核 |
| core-095 | revise | 只查能不能取消订单 | clarify | null | 待复核 |
| core-096 | revise | 只解释把当前版本第二行改成三件 | clarify | null | 待复核 |
| core-097 | revise | 不要修改，第二行改成三件 | clarify | null | 待复核 |
| core-098 | revise | 先别保存，把当前版本第二行改成三件 | clarify | null | 待复核 |
| core-099 | revise | 把当前版本第三行改成三件 | clarify | null | 待复核 |
| core-100 | revise | SKU NOPE 数量改为三件 | clarify | null | 待复核 |
| core-101 | revise | 把当前版本第二行改成0件 | clarify | null | 待复核 |
| core-102 | revise | 把当前版本第二行改成-2件 | clarify | null | 待复核 |
| core-103 | revise | 把当前版本第二行改成2.5件 | clarify | null | 待复核 |
| core-104 | revise | 把当前版本第二行改成一二件 | clarify | null | 待复核 |
| core-105 | revise | 把当前版本第二行改成两箱 | clarify | null | 待复核 |
| core-106 | revise | 把当前版本第二行改成三件；机械键盘看情况 | clarify | null | 待复核 |
| core-107 | revise | 把当前版本第二行改成三件；备注原文「未闭合 | clarify | null | 待复核 |
| core-108 | revise | 把当前版本第二行改成三件；`未闭合 | clarify | null | 待复核 |
| core-109 | revise | 把当前版本第二行改成三件；预算为1000元；预算为2000元 | clarify | null | 待复核 |
| core-110 | revise | 预算3000，预算3500 | clarify | null | 待复核 |
| core-111 | revise | 数量改成三件 | clarify | null | 待复核 |
| core-112 | revise | 把商品标准耳机数量改为三件 | clarify | null | 待复核 |
| core-113 | revise | 备注改为「甲」；备注改为「乙」 | clarify | null | 待复核 |
| core-114 | revise | 客户改为甲；客户改为乙 | clarify | null | 待复核 |
| core-115 | revise | 预算上限为0元 | clarify | null | 待复核 |
| core-116 | revise | 预算上限为-1元 | clarify | null | 待复核 |
| core-117 | revise | 预算上限为1.001元 | clarify | null | 待复核 |
| core-118 | revise | 预算上限为3000元；取消预算上限 | clarify | null | 待复核 |
| core-119 | revise | 选第二个 | clarify | null | 待复核 |
| core-120 | revise | 把当前版本第二行改成三件；然后删除第一行 | clarify | null | 待复核 |
