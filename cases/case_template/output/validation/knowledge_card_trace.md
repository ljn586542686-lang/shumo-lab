# Knowledge Card Trace

## 1. 调用链路

题型识别 → 知识卡推荐 → 模型路线生成 → 算法选择 → 敏感性设计 → 模型批判 → 引用审计

## 2. 阶段记录

| 阶段 | 输入文件 | 输出文件 | 调用知识卡 | 是否通过 |
|---|---|---|---|---|
| problem-type-router | problem_analysis.md | problem_type.json |  | pending |
| model-route-generator | problem_type.json | model_routes.md |  | pending |
| optimizer-arena | model_routes.md | result_summary.json |  | pending |
| sensitivity-runner | result_summary.json | sensitivity_report.md |  | pending |
| model-critic | paper/sections, output | model_critique.md |  | pending |
| citation-auditor | citation_matrix.md | citation_audit_report.md |  | pending |

## 3. 异常记录

| 问题 | 阶段 | 处理方式 |
|---|---|---|