---
name: evidence-trace-checker
description: 数学建模证据链审计 skill。用于建立 evidence_map.md，逐条检查论文核心结论、关键数值、图表和方法说明是否能追溯到代码、输出、表格、图像、文献或人工决策文件；无来源结论必须标记高风险。
---

# Evidence Trace Checker

## 定位

保证论文不是“先写结论再找证据”，而是由输出、代码、文献和人工决策共同支撑。

## 输入

- `paper/main.tex` 和 `paper/sections/`
- `output/result_summary.txt`
- `output/result_summary.json`
- `output/tables/`
- `output/figures/`
- `literature/citation_matrix.md`
- `human_review/`

## 输出

- `evidence_map.md`
- `output/validation/evidence_trace_report.md`

## evidence_map 表头

```markdown
| 论文位置 | 结论/数值 | 来源代码 | 来源输出 | 来源表格 | 来源图像 | 文献依据 | 人工决策 | 风险 |
|---|---|---|---|---|---|---|---|---|
```

## 风险判定

- 高风险：没有来源、来源和正文数值不一致、图表不存在。
- 中风险：来源存在但口径不同、近似条件未说明。
- 低风险：来源完整且口径一致。

## 禁止事项

- 无来源结论不得通过。
- 不替论文新增结论。
- 不修改模型输出。
