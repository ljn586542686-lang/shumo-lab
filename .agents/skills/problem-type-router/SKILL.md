---
name: problem-type-router
description: 数学建模赛题题型路由 skill。用于读取新赛题题面和附件摘要，识别优化、评价、预测、仿真、几何、调度、图论、微分方程、多目标决策等题型，输出 problem_type.json 和题型识别说明；适用于进入建模前的第一阶段，不写代码、不生成论文、不锁定最终模型。
---

# Problem Type Router

## 定位

作为新 case 的第一道题型路由器，只判断赛题属于哪些建模类型，并提出后续需要人工确认的问题。

## 输入

- `case_config.json`
- `data/raw/` 中的题面 PDF、附件说明或用户提供的题面文本
- 已有 `problem_analysis.md` 时可作为参考，但不得被其结论绑死

## 输出

- `problem_type.json`
- `problem_analysis.md` 中的“题型识别与流程规划”部分
- 需要人工确认的问题清单

## 必查维度

- 优化类、评价类、预测类、统计建模类
- 微分方程类、图论网络类、机理仿真类
- 多目标决策类、整数规划类、排队/调度类
- 空间几何类、时间序列类、机器学习类
- 数据依赖强度、计算风险、可解释性要求、提交文件要求

## 输出格式

`problem_type.json` 至少包含：

```json
{
  "primary_type": "",
  "secondary_types": [],
  "data_dependency": "low|medium|high",
  "risk_level": "low|medium|high",
  "recommended_model_families": [],
  "human_questions": []
}
```

## 禁止事项

- 不写代码。
- 不生成论文正文。
- 不锁定最终模型路线。
- 不套用历史 case 模板，尤其不得从 `case_01` 或 `case_02` 复制模型结构。
- 缺少题面或附件时，必须停止并提示缺少输入材料。

## 人工闸门

完成后必须等待 `human_review/gate_1_problem_understanding.md` 确认，才能进入模型路线生成阶段。
## 八教材知识卡库 v3.0 接入

本 skill 可读取并参考以下知识库文件：

- `pipeline_core/literature_library/knowledge_cards/knowledge_card_router.md`
- `pipeline_core/literature_library/knowledge_cards/core_competition_cards/`
- `pipeline_core/literature_library/knowledge_cards/README.md`
- `pipeline_core/literature_library/literature_index.md`
- `pipeline_core/literature_library/references.bib`

使用原则：

1. 不得修改知识卡内容、文献索引或 BibTeX 条目。
2. 不得把知识卡中的方法依据写成具体赛题结论。
3. 不得把高级教材作为装饰性引用。
4. 若正文没有涉及凸性、KKT、对偶、收敛性、泛化误差、交叉验证、过拟合等内容，不应强制引用 Boyd、Nocedal 或 Zhou。
5. 若模型涉及理论风险，应交由 `theory-checker` 复核。
6. 知识卡只用于方法选择、理论边界、算法路由、敏感性设计和引用审计，不替代数据、代码和输出证据。
## problem-type-router 的知识卡输出规则

题型识别后，必须读取或参考：

- `pipeline_core/literature_library/knowledge_cards/knowledge_card_router.md`

输出 `problem_type.json` 时，除原有字段外，必须增加：

```json
{
  "recommended_knowledge_cards": [],
  "knowledge_card_reasoning": "",
  "high_risk_theory_flags": []
}
```
推荐规则：

1. 若识别为优化类题目，推荐：
   - `64_convexity_recognition`
   - `84_optimization_solver_selection`
   - `101_theory_checker_for_optimization`

2. 若识别为机器学习类题目，推荐：
   - `85_supervised_learning_pipeline`
   - `86_model_evaluation_cross_validation`
   - `99_ml_baseline_and_interpretability`
   - `102_theory_checker_for_machine_learning`

3. 若识别为非线性优化题目，推荐：
   - `73_line_search_methods`
   - `74_trust_region_methods`
   - `76_quasi_newton_bfgs_lbfgs`
   - `82_penalty_augmented_lagrangian_sqp`

4. 若题目涉及凸性、KKT、对偶、全局最优、机器学习泛化或统计假设，应在 `high_risk_theory_flags` 中标记，并要求后续调用 `theory-checker`。