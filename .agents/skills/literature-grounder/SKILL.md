---
name: literature-grounder
description: 数学建模文献支撑 skill。用于从本地 literature/selected_references.bib 与 pipeline_core/literature_library 中匹配方法依据，生成 citation_matrix.md 和 method_support.md；禁止编造文献、装饰性引用或搜索当前赛题答案。
---

# Literature Grounder

## 定位

为模型方法、误差分析和算法选择提供本地文献依据，不用于寻找现成赛题答案。

## 输入

- `literature/selected_references.bib`
- `pipeline_core/literature_library/`
- `model_routes.md`
- `paper/sections/`

## 输出

- `literature/citation_matrix.md`
- `literature/method_support.md`
- `literature/rejected_references.md`

## 工作规则

- 每个核心方法至少匹配 1 条可解释文献依据。
- 每条引用必须说明支撑论文中的哪一句、哪一节或哪一类方法。
- 弱相关文献进入 `rejected_references.md`，不得堆进参考文献。
- 没有本地文献时，输出缺失报告，等待用户补充。

## 禁止事项

- 禁止编造文献。
- 禁止只看标题就引用。
- 禁止引用不能支撑正文主张的文献。
- 禁止自动搜索、浏览或讨论当年赛题答案。
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
## literature-grounder 的八教材文献支撑规则

文献支撑优先级：

1. 方法理论优先使用八教材知识卡；
2. 高级算法可使用经典教材或专题论文；
3. CNKI 只作为中文应用背景、指标体系、国内场景补充；
4. 不强制每篇论文使用 CNKI；
5. 不强制每篇论文引用三本高级教材。

高级教材调用边界：

1. Boyd 只在涉及凸优化、KKT、对偶、鲁棒拟合、内点法等内容时调用；
2. Nocedal 只在涉及数值优化、线搜索、信赖域、BFGS、SQP、约束优化等内容时调用；
3. Zhou 只在涉及机器学习建模、评估、交叉验证、分类、聚类、降维、特征选择等内容时调用。

输出 `selected_knowledge_cards.md` 或 `citation_matrix.md` 时，应说明：

1. 调用的知识卡；
2. 支撑的正文位置；
3. 不能支撑的内容；
4. 是否需要补充专题文献；
5. 是否存在装饰性引用风险。