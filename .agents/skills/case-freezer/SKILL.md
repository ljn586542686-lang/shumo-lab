---
name: case-freezer
description: 数学建模最终提交冻结 skill。用于在提交前检查 PDF、表格、证据链、引用审计、AI 使用披露、人工闸门和输出契约，全部通过后生成 final_submit；未通过验证的 case 不得冻结。
---

# Case Freezer

## 定位

最终提交包冻结器，只做提交前检查与归档，不修模型、不润色论文。

## 输入

- `paper/main.pdf`
- `output/result_summary.json`
- `output/validation/`
- `evidence_map.md`
- `literature/citation_matrix.md`
- `ai_disclosure/`
- `human_review/`
- 提交表格和附件

## 输出

- `final_submit/`
- `output/validation/final_freeze_report.md`

## 冻结前必过闸门

- `output-contract-validator`
- `evidence-trace-checker`
- `citation-auditor`
- `model-critic`
- `human_review/gate_4_final_submission.md`
- `ai-disclosure-logger`
- `paper/visual_reasoning_trace_report.json`（必须为 `PASS`）
- 新 case 的 `paper/argument_blueprint_trace_report.json`（必须为 `PASS`，并绑定 `main.tex` 与 writer manifest）

## 检查项

- PDF 是否存在且能打开。
- 提交表格是否存在。
- 图表和正文引用是否一致。
- 是否满足 `STRONG_EVIDENCE_STORY`：每一问在首组关键公式前有题目对象关系图，图数不少于 `task_count+1`，重点任务另有过程或诊断图，模型结构/中间过程/结果诊断三类齐全；`task_visual_family`、`explanatory_form` 和实际图形类型相容，且真实语义对象与 `object_bindings` 一致；抽象总览图、通用流程图和错题型图不得冒充模型引导图。
- 若 `argument_blueprint_required=true`，逐问蓝图是否覆盖研究对象、模型、公式含义、假设作用、题目设问、结果卡、跨问依赖、失效条件和自选推理路径；trace 是否由 renderer 生成且哈希未被篡改。
- 核心结论是否有证据链。
- 引用是否有 citation matrix 支撑。
- AI 使用详情是否齐全。
- 人工确认是否完成。

## 禁止事项

- 不冻结未通过验证的 case。
- 不修改模型结果。
- 不删除失败记录。
- 不从旧 case 复制提交材料。
