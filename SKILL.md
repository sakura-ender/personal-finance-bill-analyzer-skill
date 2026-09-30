---
name: personal-finance-bill-analyzer
description: Consolidate personal WeChat Pay, Alipay, bank, and optional travel-ledger exports into a privacy-safe, deduplicated finance report, workbook, and interactive dashboard. Use when the user supplies personal bill files and asks for total spending or income, category analysis, AA/group-collection offsets, refund netting, asset-resale netting, travel analysis, or a cutoff-date refresh.
---

# Personal Finance Bill Analyzer

把用户导出的账单变成一套可复核、可追溯、默认不泄露隐私的个人财务分析。日期范围必须可配置，不把任何一次分析中的姓名、账号、金额、路径或分类特例固化进 Skill。

## 必读资料

- 每次任务都先读 [references/data-contract.md](references/data-contract.md)，确认原始字段到统一字段的映射。
- 每次任务都读 [references/accounting-rules.md](references/accounting-rules.md)，按证据等级处理去重、退款、AA、转售和旅游账。
- 只有在准备分享 Skill、网页或分析结果时，才读 [references/privacy-and-delivery.md](references/privacy-and-delivery.md) 并执行隐私检查。

## 工作流程

1. 确认分析开始日、截止日和币种。用户已经给出时直接采用；未给出截止日时使用账单内最新日期，不擅自延伸到未来。
2. 清点微信、支付宝、银行卡和可选的个人旅游记账文件。保留原文件只读，不覆盖、不改名、不把原始数据复制进 Skill 包或代码仓库。
3. 动态识别各文件表头并统一成 `normalized.csv`。不同银行版式不固定，先检查实际列名，不根据文件名猜测字段。
4. 对 PDF 或图片账单，先用 PDF/文档能力抽取成表格，再按同一数据合同规范化。对 Excel/CSV，使用电子表格能力读取和校验。
5. 从 Skill 目录运行：

   ```bash
   python3 scripts/analyze_normalized.py \
     --input /absolute/path/normalized.csv \
     --output /absolute/path/output \
     --start YYYY-MM-DD \
     --end YYYY-MM-DD \
     [--travel /absolute/path/travel.csv]
   ```

6. 复核脚本输出的候选匹配。只有强证据匹配才自动去重或归类；不确定项保留并列入审计，不为凑总额强行匹配。
7. 使用电子表格能力把输出 CSV/JSON 制作为 `个人财务全量分析.xlsx`，至少包含总览、月度趋势、支出分类、收入来源、旅游模块、清洗审计、完整明细七类工作表；金额和行数必须与脚本输出一致。
8. 默认交付脚本生成的本地独立网页 `dashboard/index.html`。仅当用户明确要求上线时再使用网站发布能力；发布版默认不包含姓名、账号、完整交易号、原始文件名、备注原文或商户明细。
9. 在交付前运行：

   ```bash
   python3 scripts/privacy_audit.py /absolute/path/to/share
   ```

   阻断级问题未清零前不得把 Skill 包、网页或结果上传到外部服务。

## 必须保持的口径

- 实际总支出 = 原始消费支出 − 已匹配退款 − 明确的 AA/群收款补回 − 资产转售回款。
- 真实收入不包含退款、AA 补回、资产转售和本人账户间转账；这些项目分别在调整项中展示。
- 跨平台重复只删除镜像记录，不删除纯银行卡、纯微信或纯支付宝交易。
- 未分配到具体消费的 AA 补回可以冲减总支出，但不能凭空摊到餐饮、交通等分类；分类总额与实际总支出的差额必须单列解释。
- 旅游记账用于独立模块和交叉核对，不得在总支出中重复加算。截止日之后或未完成的旅程不计入已发生总额。
- 分类、收入来源、总账明细默认按金额从大到小排序，并允许网页切换时间和收支维度。

## 完成标准

- 总览、分类、月度、收入、调整项、旅游和明细能够互相勾稽。
- 清洗审计能说明每笔删除、核销或保留候选的原因与证据。
- 网页包含总览卡片、月度趋势、支出占比、收入分布、调整项、旅游模块和可筛选明细。
- 最终回复直接给出总支出、真实收入、净额、最大支出板块、未决候选数量，并提供可点击的 Excel、网页和审计文件路径。
