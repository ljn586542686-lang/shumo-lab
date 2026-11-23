# output

模型运行后的正式输出放在本目录。

- `figures`：正式图片。
- `tables`：正式表格。
- `sensitivity`：灵敏度与稳健性分析记录。
- `validation`：输出契约、理论审查、证据链、引用审计和 final_submit 前检查。

空项目测试时，本目录应保持无正式结果状态。
## V3.0 模板说明

本 case_template 已接入八教材知识卡库 v3.0。

新建 case 后，必须完成：

1. `literature/selected_knowledge_cards.md`
2. `literature/citation_matrix.md`
3. `output/validation/theory_check_report.md`
4. `output/validation/knowledge_card_trace.md`
5. `output/validation/national_award_score_estimate.md`
6. `output/sensitivity/sensitivity_report.md`
7. `output/validation/final_submit_checklist.md`
8. `ai_disclosure/AI工具使用详情.md`
9. `human_review/gate_*.md`

若 `theory_check_report.md` 为 FAIL，不得进入 final_submit。  
若 `citation_matrix.md` 未完成，不得进入 final_submit。  
若 AI 使用记录未完成，不得进入 final_submit。
