# 论文质量层总览

状态：`PASS_WITH_LIMITATION / PAPER_QUALITY_LAYER_BASELINE_READY`

## 1. 作用

论文质量层用于检查数学建模论文草稿是否具备可进入人工复核的基本质量。它覆盖结构、证据链、公式符号、图表表格、引用、摘要、结论边界、AI 使用披露和最终提交风险。

## 2. 它不负责什么

1. 不伪造论文。
2. 不自动生成正式比赛论文。
3. 不自动提交。
4. 不替代人工 gate。
5. 不替模型补造未运行结果。
6. 不把展示材料包装成正式论文。

## 3. 必须依赖的证据来源

论文草稿进入质量检查前，至少应具备以下证据来源：

1. `evidence_map.md`
2. `output/result_summary.txt`
3. `output/result_summary.json`
4. `output/validation/`
5. `output/tables/`
6. `output/figures/`
7. `human_review/`
8. 如有引用，则必须有 `literature/citation_matrix.md` 或等价引用审计文件。

## 4. 展示版与正式论文分流

| 类型 | 用途 | AI 使用披露 | final_submit |
|---|---|---|---|
| 展示版材料 | 老师沟通、队员交流、暑假训练、选拔展示 | `showcase_mode`，可概括表述 | 禁止 |
| 正式比赛论文 | 竞赛提交 | `official_submission_mode`，按规则清楚披露 | 仅可由人工 gate 和冻结流程授权 |

## 5. 人工 gate

论文质量层只能给出风险和修改建议。是否采纳、是否进入下一阶段、是否提交，必须由人工复核决定。

默认结论：

`final_submit_allowed = false`

