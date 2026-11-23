# Case Template

This directory is the only approved source for creating new MathModeling2026 cases.

## Initialization Rule

From the repository root, run `python -m pipeline_core.python_tools.case_initializer case_demo`. The initializer copies this template and sets the case ID in both `case_config.json` and `CURRENT_HANDOFF.md`. Then fill in the new case configuration.

Do not initialize a new case from `case_01_crop_planting`, `case_02_smoke_interference`, or any archived case.

## Required Flow

1. Put the problem statement and attachments in `data/raw/`.
2. Run problem type routing and problem analysis.
3. Complete `human_review/gate_1_problem_understanding.md`.
4. Generate model routes and complete `human_review/gate_2_model_choice.md`.
5. Record selected knowledge cards in `literature/selected_knowledge_cards.md`.
6. Run data audit, modeling, output validation, theory checking, sensitivity analysis, evidence tracing, literature grounding, citation audit, AI disclosure, national award score estimation, and final freezing in order.

If the problem statement or data are missing, the pipeline must stop and report missing input materials.

## V3.0 Template Gates

New cases created from this template must keep the following structures:

- `output/validation/knowledge_card_trace.md`
- `output/validation/theory_check_report.md`
- `output/sensitivity/sensitivity_report.md`
- `literature/citation_matrix.md`
- `ai_disclosure/AI工具使用详情.md`
- `output/validation/national_award_score_estimate.md`
- `output/validation/final_submit_checklist.md`

Do not create `final_submit/` until all validation reports, human review gates, citation audit, evidence tracing, and AI disclosure are complete.

## Python 图表规范

新 case 的 Python 建模脚本应复用 `code/python/plot_style.py` 中的 `setup_chinese_plot_style()`。

- 论文图表标题、坐标轴、图例和注释默认使用中文。
- 图表文件名允许使用英文，避免路径编码问题。
- 若本机中文字体不可用，模型脚本可以继续运行，但必须在 `output/validation/output_contract_report.md` 中说明“中文字体不可用”。
- 不允许为了图表中文化改变模型结果、风险评分、授信额度、利率、优化目标值或任何已经生成的数值结论。
