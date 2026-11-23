# 无 AI / 无 Codex 兜底方案

当前状态：`PASS_WITH_LIMITATION / COMPETITION_RED_TEAM_CONTINGENCY_READY`

本方案规定在 AI、Codex 或自动化生产线不可用时，哪些流程可以人工完成，哪些文件可以作为人工指南。

## 1. 可完全人工完成的流程

1. 审题拆解；
2. 数据字段识别；
3. 缺失值和异常值记录；
4. 题型路由；
5. 模型路线选择；
6. Excel 基础统计；
7. Python/MATLAB 最小模型手写；
8. 图表制作；
9. 论文撰写；
10. 引用整理；
11. AI 使用披露；
12. final_submit 人工控制。

## 2. 可作为人工指南的文件

1. `pipeline_core/meta/current_status_single_source_of_truth.md`
2. `pipeline_core/meta/global_asset_reuse_index.md`
3. `pipeline_core/model_library/model_route_registry.json`
4. `pipeline_core/literature_library/knowledge_cards/`
5. `pipeline_core/paper_quality/`
6. `pipeline_core/safety/competition_failure_mode_register.md`
7. `pipeline_core/safety/manual_fallback_protocol.md`
8. `pipeline_core/evaluation/ai_usage_disclosure_rules.md`

## 3. 人工题型路由

人工按以下顺序执行：

1. 列出问题数量和任务；
2. 判断是否涉及优化、预测、评价、仿真、图论、统计、聚类、微分方程；
3. 对每类问题选择一个 baseline；
4. 记录不处理的扩展问题；
5. 交给建模负责人确认。

## 4. 人工模型路线选择

优先顺序：

1. 可解释 baseline；
2. 可在 Excel/Python/MATLAB 中快速复现；
3. 有本地知识卡或教材依据；
4. 能输出核心结果和验证；
5. 能在论文中说明局限性。

## 5. 人工论文质量检查

按 `paper_quality/` 逐项检查：

1. 摘要是否有方法、结果、结论；
2. 公式符号是否定义；
3. 图表是否中文；
4. 结果是否可追溯；
5. 引用是否真实；
6. AI 披露是否符合模式；
7. baseline 是否被夸大。

## 6. 人工 AI 披露

展示材料使用 `showcase_mode`，正式比赛使用 `official_submission_mode`。正式比赛不得用模糊表述替代具体工具、版本、机构、日期、使用环节和人工校验方式。

## 7. final_submit 人工控制

没有 AI/Codex 时仍不得跳过 gate。`final_submit/` 只能在人工终审完成后创建。
