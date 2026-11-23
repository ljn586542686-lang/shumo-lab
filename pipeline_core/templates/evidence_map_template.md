# evidence_map_template

复制到具体 case 的 `paper/evidence_map.md` 后使用。

| conclusion_id | conclusion_text | source_file | source_location | support_type | risk_level | note |
|---|---|---|---|---|---|---|
| C001 |  | output/result_summary.txt |  | numeric |  |  |
| C002 |  | output/tables/ |  | table |  |  |
| C003 |  | output/figures/ |  | figure |  |  |
| C004 |  | code/ |  | code |  |  |
| C005 |  | paper/decision_log.md |  | decision |  |  |

## risk_level

- `low`：结论有明确数值、表格、图像或代码来源。
- `medium`：来源存在，但位置不够精确，需补充行列、图号或变量名。
- `high`：无法找到来源，或与 output、表格、图像、代码不一致。

高风险结论不得进入论文定稿。
