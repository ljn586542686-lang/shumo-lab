---
name: model-route-generator
description: 数学建模多路线建模方案生成 skill。用于在题型识别和题意拆解后，为每个新赛题生成候选建模路线（数量由真实比较情况决定）、评分矩阵和人工选择问题，输出 model_routes.md 与 model_route_score.xlsx；不直接决定最终模型，不编造数据或结果，不为凑数编造未真实比较过的路线。
---

# Model Route Generator

## Executor XLSX authoring contract

The executor must author `model_route_score.xlsx` from explicit scoring data.
Use `pipeline_core/model_route/model_route_score_writer.py` as the
deterministic serialization helper:

```python
write_model_route_score_xlsx(output_path, criteria, routes)
```

Each `criteria` item must provide `name`, `direction`, and finite non-negative
`weight`. Each `routes` item must provide the unchanged full `route_name`, a
complete `scores` mapping with one finite numeric score for every criterion,
and non-empty `comparison_reasoning`. The helper writes `Route Scores` and
`Scoring Rules` sheets. Scores are executor-provided contribution points, so
`Total Score` is the deterministic direct sum `sum(score)`; criterion weights
are recorded scoring-rule metadata and are not applied a second time. It is
not a default or synthesized score.

The helper must fail closed for empty inputs, duplicate route or criterion
names, missing direction or weight, non-finite values, incomplete score
dimensions, non-numeric scores, missing comparison reasoning, or a supplied
reported total inconsistent with the contribution-point sum. It must not read
`model_routes.md`, invent scores, invent weights, invent directions, or invent
reasoning. `model_route_score.xlsx` is an executor-authored artifact;
`model_route_stage.py` validates it but must not create or overwrite it.

## 定位

为队员降低从零建模负担，生成可比较的候选模型路线，而不是替队员直接决定最终路线。

## 输入

- `problem_type.json`
- `problem_analysis.md`
- `data_audit_report.md` 或数据可用性说明
- `case_config.json`

## 输出

- `model_routes.md`
- `model_route_score.xlsx`
- `human_review/gate_2_model_choice.md` 的待确认问题

## 候选路线槽位（数量由真实比较情况决定）

以下五个槽位仅作参考分类，不要求全部填满。只记录真实比较过的路线；
未被真实考虑的槽位应保持空白或删除，不得为凑数编造"曾经尝试但失败"
的探索过程。若某题只有一到两个合理路线，记录一到两条即可，不必强行
凑出五条。见 `docs/design/HUMAN_FIRST_MODELING_LOGIC_CONTRACT.md`
原则 B（模型选择依据）。

1. 路线 A：保守基准模型
2. 路线 B：主流竞赛模型
3. 路线 C：增强优化模型
4. 路线 D：创新组合模型
5. 路线 E：低风险快速提交模型

## 每条被记录的路线需说明

- 模型思想
- 适用子问题
- 决策变量、目标函数、约束条件
- 核心公式框架
- 所需数据
- 求解算法
- 实现难度与时间成本
- 预期论文表现
- 失败风险
- 可解释性
- 是否适合冲奖

## 评分矩阵

默认权重：

- 题意匹配：30
- 可实现性：20
- 结果可信度：20
- 创新性：15
- 论文表达：10
- 合规风险：5

## 禁止事项

- 不编造数据。
- 不把未运行模型写成已验证模型。
- 不绕过人工模型选择。
- 本步骤停在路线比较，编码求解和正文撰写留给后续阶段。

## 人工闸门

必须由 `human_review/gate_2_model_choice.md` 记录最终路线选择和取舍理由。
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

## model-route-generator 的知识卡调用规则

生成模型路线前，必须读取 `problem_type.json` 中的：

- `recommended_knowledge_cards`
- `knowledge_card_reasoning`
- `high_risk_theory_flags`

每条模型路线必须注明：

1. 调用哪些知识卡；
2. 知识卡支撑的内容；
3. 知识卡不能支撑的内容；
4. 该路线的理论风险；
5. 是否需要 `theory-checker` 复核。

输出 `model_routes.md` 时增加章节：

## 知识卡调用记录

| 路线 | 调用知识卡 | 支撑内容 | 风险 | 是否需 theory-checker |
|---|---|---|---|---|

禁止：

1. 禁止把高级知识卡当成装饰性引用。
2. 禁止把知识卡中的通用方法写成本题已验证结论。
3. 禁止将非凸局部优化路线直接描述为全局最优路线。
