---
name: output-contract-validator
description: 数学建模输出契约验证 skill。用于检查 result_summary.txt、result_summary.json、图表、提交表格和验证报告是否完整一致，输出 output_contract_report.md；适用于正文撰写前和最终冻结前，只验证不改结果。
---

# Output Contract Validator

## 定位

作为结果进入论文阶段前的强制质量闸门。

## 输入

- `output/result_summary.txt`
- `output/result_summary.json`
- `output/tables/`
- `output/figures/`
- `case_config.json`

## 输出

- `output/validation/output_contract_report.md`

## 检查项

- 每一问是否有关键结果。
- 每一问是否有结果来源。
- 提交表是否存在。
- 关键图是否存在。
- 关键表是否存在。
- JSON 是否可解析。
- 论文引用的结果是否存在。
- 是否有未解释的 `NaN` / `Inf`。
- 是否有单位缺失。

## 判定

- `pass`：可进入论文证据链阶段。
- `warning`：可继续，但必须在论文局限性中说明。
- `fail`：暂缓正文撰写和排版，先复核缺失或不一致的输出。

## 禁止事项

- 不修改结果。
- 不替用户补造缺失表格。
- 不把警告降级为通过。
