# Final Submit Checklist

## 1. 总体状态

状态：pending

## 2. 冻结前必查项

| 检查项 | 状态 | 说明 |
|---|---|---|
| `paper/main.pdf` 正常生成 | pending |  |
| 提交表格存在 | pending |  |
| `output/validation/output_contract_report.md` 通过 | pending |  |
| `output/validation/evidence_trace_report.md` 通过 | pending |  |
| `output/validation/citation_audit_report.md` 通过 | pending |  |
| `output/validation/theory_check_report.md` 非 FAIL | pending |  |
| `output/sensitivity/sensitivity_report.md` 已完成或说明不适用 | pending |  |
| `ai_disclosure/AI工具使用详情.md` 完整 | pending |  |
| `human_review/gate_4_final_submission.md` 已人工确认 | pending |  |
| `paper/visual_reasoning_trace_report.json` 为 `PASS` | pending | 必须逐题覆盖模型、公式、中间证据、最终结果和正式图。 |
| `STRONG_EVIDENCE_STORY` 强展示达标 | pending | 每一问在首组公式前有题目对象关系图；图数不少于 `task_count+1`，重点任务另有过程/诊断图，三类视觉角色齐全。 |

## 3. 禁止冻结条件

- 输出契约未通过。
- 证据链未通过。
- 引用审计未通过。
- theory-checker 为 FAIL。
- AI 使用披露缺失。
- 人工最终提交闸门未确认。
- 任一题目缺少“模型/公式 → 中间状态 → 最终结果”的解释性正式图，或可视推理报告不是 `PASS`。
- 任一问缺少公式前的题目对象关系图、使用抽象总览/通用流程图冒充、图数不足、缺少重点任务过程/诊断图、缺少任一视觉角色族，或图没有明确读者问题与正文结论。

## 4. 结论

是否允许生成 `final_submit/`：pending
