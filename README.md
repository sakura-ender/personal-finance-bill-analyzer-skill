# Personal Finance Bill Analyzer Skill

一个面向 Codex 的隐私优先个人账单分析 Skill。上传微信支付、支付宝、银行卡账单以及可选的旅游记账后，可完成清洗去重、收支分类、退款与 AA 核销、二手转售差值处理，并生成可复核的数据表和独立可视化网页。

## 能做什么

- 合并微信、支付宝和银行卡等多来源账单
- 识别银行卡与支付平台之间的镜像交易，保留单一平台独立交易
- 将退款从原消费中核销，支持部分退款
- 将群收款、AA 返还作为消费补回，而不是普通收入
- 将资产购买与二手转售按净成本统计
- 排除本人账户之间的内部转账
- 汇总支出分类、收入来源及月度趋势
- 将个人旅游账与平台流水交叉核对，避免重复统计
- 生成本地独立可视化网页，并指导生成 Excel 工作簿
- 在分享前扫描常见个人标识和误打包的原始账单

## 安装

克隆到 Codex 的 Skills 目录：

```bash
git clone https://github.com/sakura-ender/personal-finance-bill-analyzer-skill.git ~/.codex/skills/personal-finance-bill-analyzer
```

也可以下载仓库 ZIP，解压后把整个目录放入 Codex 的 Skills 目录。

## 使用

上传账单文件后输入：

```text
使用 $personal-finance-bill-analyzer 分析这些账单，统计指定日期范围，完成去重、退款、AA 和二手转售处理，并生成 Excel 和可视化网页。
```

Skill 会先检查实际表头，再把各类导出文件映射到统一数据结构。不同银行的版式并不固定，因此不会仅凭文件名猜测字段。

## 统计口径

```text
实际总支出 = 原始消费支出 - 已匹配退款 - AA/群收款补回 - 已关联资产转售回款
真实总收入 = 普通收入 - 本人账户间转账
收支净额 = 真实总收入 - 实际总支出
```

只有证据充分的跨平台重复、退款或资产关联才会自动处理。不确定项保留在审计表中，不会为了让数字看起来整齐而强行匹配。

## 隐私设计

本仓库只包含通用规则和处理脚本，不包含任何真实账单、姓名、账号、交易号、金额、站点地址或本地路径。

- 原始账单只在用户指定的本地目录处理
- 公开网页默认不展示账号、交易号、对方名称和备注原文
- `scripts/privacy_audit.py` 可在分享前检查常见隐私泄漏
- Skill 不会自行上传账单、发布网站或修改外部系统

## 主要文件

- `SKILL.md`：Skill 入口和完整工作流程
- `references/accounting-rules.md`：去重、退款、AA、转售和旅游统计规则
- `references/data-contract.md`：统一字段和输入映射
- `references/privacy-and-delivery.md`：隐私分层与安全交付要求
- `scripts/analyze_normalized.py`：生成汇总、审计表和独立网页
- `scripts/privacy_audit.py`：分享前隐私扫描

## 许可证

[MIT License](LICENSE)
