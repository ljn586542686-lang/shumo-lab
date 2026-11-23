# 题型路由器输出 Schema

状态：`PASS_WITH_LIMITATION / ROUTER_ASSET_REUSE_KNOWLEDGE_CARD_PATCH_READY`

本文档说明 `pipeline_core/router/problem_type_router.py` 生成的 JSON 输出结构。路由器只提供建模路线建议，不运行模型，不生成论文，不授权 `final_submit`。

## 1. 顶层字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `case_id` | string | 当前 case 标识，优先来自 `case_config.json`。 |
| `problem_name` | string | 题目名称，优先来自 `case_config.json`。 |
| `input_files_checked` | array | 路由器检查过的 current_case 输入文件清单。 |
| `primary_problem_type` | string | 主题型，取值为 10 类规范题型之一。 |
| `secondary_problem_types` | array | 辅助题型，用于混合题和多阶段问题拆解。 |
| `type_scores` | array | 各候选题型得分和命中依据。 |
| `confidence` | string | 路由置信度：`LOW`、`MEDIUM`、`HIGH`。 |
| `recommended_baseline_models` | array | 推荐 baseline 模型候选。 |
| `recommended_advanced_models` | array | 推荐 advanced 模型候选。 |
| `recommended_validation_metrics` | array | 推荐验证指标。 |
| `recommended_sensitivity_checks` | array | 推荐敏感性检查。 |
| `recommended_paper_sections` | array | 推荐论文结构章节。 |
| `recommended_problem_routes` | array | 分问题建模路线建议。 |
| `routing_risks` | array | 路由风险与处理建议。 |
| `reused_assets` | array | 本次路由显式复用的生产线资产。 |
| `missing_assets` | array | 当前缺失或未能复用的资产。 |
| `knowledge_card_support` | array | 知识卡主题、对应问题、支撑路线、支撑验证指标、缺口和人工复核要求。 |
| `model_library_support` | array | 模型库资产读取与用途说明。 |
| `paper_quality_support` | array | 论文质量层资产读取与用途说明。 |
| `ai_disclosure_mode_support` | object | AI 使用披露双模式支持：`showcase_mode` 与 `official_submission_mode`。 |
| `asset_reuse_note_path` | string | 资产复用说明文件路径。 |
| `asset_reuse_status` | string | 资产复用状态，例如 `KNOWLEDGE_CARD_ASSET_MISSING`。 |
| `human_gate_required` | boolean | 是否需要人工确认。固定为 `true`。 |
| `final_submit_allowed` | boolean | 是否允许最终提交。固定为 `false`。 |
| `generated_at` | string | UTC ISO 时间戳。 |
| `status` | string | 路由状态，例如 `ROUTED_WITH_LIMITATION`。 |

## 2. 规范题型

路由器输出使用以下题型 ID：

- `statistical_analysis`
- `prediction_timeseries`
- `optimization_planning`
- `evaluation_decision`
- `graph_network_spatial`
- `dynamic_scheduling`
- `mechanism_fitting`
- `classification_clustering`
- `multi_objective_decision`
- `mixed_problem`

## 3. 知识卡字段

`knowledge_card_support` 中每个对象至少包含：

- `theme`：知识卡主题。
- `problem_scope`：支撑哪个子问题或阶段。
- `model_route_supported`：支撑的模型路线。
- `validation_supported`：支撑的验证指标或敏感性设计。
- `cards`：知识卡 ID、路径和存在性。
- `knowledge_card_gap`：是否存在缺失卡片。
- `human_review_required`：是否需要人工复核。

知识卡只用于理论支撑、路线解释、敏感性设计和边界提醒，不等于正式参考文献，也不支撑具体赛题数值结论。

## 4. 安全边界

- `problem_type_routing_result.json` 不是模型结果。
- `model_route_recommendation.md` 不是最终模型选择。
- `paper_structure_recommendation.md` 不是论文草稿。
- `final_submit_allowed` 必须保持 `false`。
- 低置信度或多题型接近时必须进入人工确认。
