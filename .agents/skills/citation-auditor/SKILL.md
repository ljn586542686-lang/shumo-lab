---
name: citation-auditor
description: 数学建模引用审计 skill。用于检查正文引用、references.bib、citation_matrix.md 与 AI 工具披露是否一致，识别缺失引用、装饰性引用、伪造文献和未支撑正文的引用；输出 citation_audit_report.md。
---

# Citation Auditor

## 定位

在论文终稿前检查引用是否真实、必要、可追溯。

## 输入

- `paper/main.tex`
- `paper/sections/`
- `paper/references.bib` 或 `literature/selected_references.bib`
- `literature/citation_matrix.md`
- `ai_disclosure/`

## 输出

- `output/validation/citation_audit_report.md`

## 检查项

- 正文引用是否存在于 BibTeX 文件。
- BibTeX 中是否有正文未引用文献。
- 每条引用是否支撑对应正文。
- 是否存在装饰性引用。
- 是否存在明显伪造或字段缺失文献。
- AI 工具引用或披露是否按比赛要求记录。

## 判定

- `pass`：引用完整且支撑关系明确。
- `warning`：有弱支撑或未引用文献。
- `fail`：有伪造、缺失或无法追溯引用。

## 禁止事项

- 不自动新增虚假引用。
- 不为凑数量保留弱相关文献。
- 不把 AI 工具使用隐藏到普通文献中。
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
## citation-auditor 的高级教材引用审计规则

引用审计时，应检查正文引用、`citation_matrix.md`、`selected_knowledge_cards.md` 与实际模型内容是否一致。

重点检查：

1. 引用是否真实支撑正文；
2. 高级教材是否被装饰性引用；
3. 教材是否被错误用于支撑具体赛题数值结论；
4. 正文是否存在无来源理论主张；
5. 正文是否引用了未在文献库登记的来源。

高级教材引用边界：

1. 若正文没有凸性、KKT、对偶、收敛或内点法内容，不应强制引用 Boyd。
2. 若正文没有数值优化、线搜索、信赖域、BFGS、SQP 或约束优化内容，不应强制引用 Nocedal。
3. 若正文没有机器学习评估、交叉验证、泛化误差、过拟合控制或模型解释内容，不应强制引用 Zhou。

如果引用审计失败，不允许进入 final_submit。

输出审计结果时应包含：

## 高级教材引用审计

| 引用 | 正文位置 | 是否支撑 | 风险 |
|---|---|---|---|