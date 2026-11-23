---
name: sensitivity-runner
description: 数学建模灵敏度与稳健性实验 skill。用于设计并执行参数扰动、权重扰动、阈值扰动、时间步长扰动、随机种子扰动、采样密度扰动等实验，输出 sensitivity_report.md、sensitivity_table.xlsx 和必要图像；禁止生成未运行出的敏感性数值。
---

# Sensitivity Runner

## 定位

把论文中的灵敏度分析从定性说明升级为可复现实验。

## 输入

- 已运行的主模型代码
- `output/result_summary.json`
- `model_critique.md`
- `case_config.json`

## 输出

- `output/sensitivity/sensitivity_report.md`
- `output/sensitivity/sensitivity_table.xlsx`
- `output/sensitivity/sensitivity_figures/`

## 至少选择一种扰动

- 参数扰动
- 权重扰动
- 阈值扰动
- 时间步长扰动
- 随机种子扰动
- 数据缺失扰动
- 采样密度扰动

## 报告要求

- 说明扰动变量、取值范围、固定条件和评价指标。
- 区分已运行数值与未运行建议。
- 记录实验脚本、运行入口和输出文件。
- 对不稳定结果标记风险，不得掩盖。

## 禁止事项

- 不生成未运行出的灵敏度数值。
- 不把单次运行结果包装成稳健性结论。
- 不修改主模型结果。
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
## sensitivity-runner 的知识卡驱动敏感性设计

敏感性分析应根据知识卡选择扰动对象，包括：

- 参数扰动；
- 权重扰动；
- 阈值扰动；
- 初值扰动；
- 随机种子扰动；
- 步长扰动；
- 网格密度扰动；
- 正则化参数扰动。

若使用优化模型，应检查：

1. 关键参数；
2. 约束右端项；
3. 权重；
4. 初值；
5. 求解器参数。

若使用机器学习，应检查：

1. 数据划分；
2. 随机种子；
3. 模型参数；
4. 特征选择；
5. 正则化参数。

输出 `sensitivity_report.md` 时增加：

## 知识卡驱动的敏感性设计

| 扰动对象 | 对应知识卡 | 扰动范围 | 目的 |
|---|---|---|---|

必须区分：

1. 已实际运行的敏感性结果；
2. 理论上建议补充但尚未运行的敏感性检查。

未运行的敏感性实验不得写成已完成结论。