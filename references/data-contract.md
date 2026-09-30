# 数据合同与输入映射

## 统一交易文件 `normalized.csv`

CSV 使用 UTF-8，首行为字段名。必填字段：

| 字段 | 说明 |
|---|---|
| `date` | `YYYY-MM-DD`，实际交易日 |
| `source` | `wechat`、`alipay`、`bank` 或其他稳定来源名 |
| `direction` | `expense`、`income`、`refund` |
| `amount` | 正数金额，不带货币符号 |

建议字段：

| 字段 | 用途 |
|---|---|
| `time` | `HH:MM:SS`，提升跨平台匹配精度 |
| `transaction_id` | 原平台交易号，只在本地审计文件中保存 |
| `related_order_id` | 原消费订单号，用于退款、AA 或镜像匹配 |
| `account` | 账号或卡号尾号；规范化后可为空 |
| `payment_method` | 付款方式或扣款卡信息 |
| `counterparty` | 商户或对方名称 |
| `description` | 商品、交易摘要或用途 |
| `category_hint` | 用户已有分类；优先于关键词自动分类 |
| `notes` | 仅本地审计使用的备注 |
| `asset_id` | 连接同一资产的购买与转售 |
| `travel_id` | 连接某次旅行和平台流水 |
| `status` | 成功、关闭、处理中等原始状态 |

取消、关闭、失败的交易在规范化阶段保留状态，分析阶段排除并写入审计。金额统一为正数，收支方向只由 `direction` 表示。

## 常见字段映射

- 微信：交易时间→`date`+`time`，交易类型/收支→`direction`，金额→`amount`，交易对方→`counterparty`，商品→`description`，支付方式→`payment_method`，交易单号→`transaction_id`，商户单号→`related_order_id`。
- 支付宝：交易创建时间或付款时间→`date`+`time`，收/支→`direction`，金额→`amount`，交易对方→`counterparty`，商品说明→`description`，交易订单号→`transaction_id`，商家订单号→`related_order_id`。
- 银行：交易日期→`date`，交易时间→`time`，借/贷或收入/支出→`direction`，交易金额→`amount`，对方户名/商户→`counterparty`，摘要/附言→`description`，卡号尾号→`account`，流水号→`transaction_id`。

不同导出版本可能改变列名、表头行和编码。必须先检查文件实际内容，再映射；不能把以上别名当成固定列号。银行卡借方通常是支出、贷方通常是收入，但仍需抽样核对。

## 可选旅游文件 `travel.csv`

| 字段 | 必填 | 说明 |
|---|---|---|
| `travel_id` | 是 | 稳定行程编号 |
| `name` | 是 | 行程名称，可用泛化名称 |
| `start_date` | 是 | `YYYY-MM-DD` |
| `end_date` | 是 | `YYYY-MM-DD` |
| `personal_net_expense` | 是 | 已扣除同行人返还后的个人净支出 |
| `status` | 是 | `completed`、`planned`、`cancelled` |
| `notes` | 否 | 本地核对备注，不进入公开网页 |

## 脚本输出

- `summary.json`：总览、分类、收入、月份、调整项和旅游核对。
- `cleaned_transactions.csv`：清洗后的本地完整明细。
- `dedupe_audit.csv`：已删除重复和仅提示的候选。
- `refund_matches.csv`：退款匹配及未匹配退款。
- `aa_offsets.csv`：AA/群收款是否已分配到具体消费。
- `category_summary.csv`、`income_summary.csv`、`monthly_summary.csv`：制作 Excel 和图表的数据源。
- `dashboard/index.html`：默认不含交易号、账号、对方名称或备注的独立可视化网页。

Excel 文件不是脚本的输入。先运行清洗脚本，再用电子表格能力从上述输出制作可复核工作簿。
