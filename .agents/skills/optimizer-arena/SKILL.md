---
name: optimizer-arena
description: 数学建模算法擂台 skill。用于为优化、预测、评价和调度类赛题建立 baseline、main、alternative 与 stress-test 模型对照，生成 optimizer_comparison.xlsx、convergence_report.md 和 best_model_selection.md；适用于模型运行后或主模型定型前的算法对比阶段。
---

# Optimizer Arena

## 定位

建立内部算法对照，避免只运行一个模型就进入论文阶段。

## 输入

- `model_routes.md`
- 当前代码运行结果
- `output/result_summary.json`
- `case_config.json`

## 输出

- `output/optimizer_arena/optimizer_comparison.xlsx`
- `output/optimizer_arena/convergence_report.md`
- `output/optimizer_arena/best_model_selection.md`

## 最低对照结构

- `baseline_model`
- `main_model`
- `alternative_model_1`
- `stress_test_model`

## 可选算法池

优化题：

- 网格搜索
- 启发式规划
- 遗传算法
- 粒子群
- 模拟退火
- 多起点局部搜索
- 整数规划 / 非线性规划

预测题：

- 线性回归
- 随机森林 / XGBoost
- ARIMA / Prophet
- 组合预测

评价题：

- 熵权法
- TOPSIS
- 层次分析法
- PCA
- 聚类评价

## 记录要求

- 未运行的算法必须标记为“候选未运行”。
- 失败模型必须记录失败原因，而不是删除痕迹。
- 最终论文不必展示全部算法，但内部必须保留比较报告。

## 禁止事项

- 不覆盖正式输出。
- 不把未运行算法写成已运行。
- 不因主模型效果差而删除对照结果。
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
## optimizer-arena 的知识卡驱动算法选择规则

选择算法前，应根据知识卡判断模型结构：

- 线性规划；
- 整数规划；
- 凸优化；
- 非线性可导优化；
- 非线性不可导优化；
- 黑箱优化；
- 多目标优化；
- 机器学习优化。

必须设置：

1. baseline；
2. main model；
3. alternative model。

若使用启发式算法，必须说明：

1. 随机种子；
2. 运行次数；
3. 终止条件；
4. 不能宣称全局最优；
5. 需要 `sensitivity-runner` 或 repeated-run 验证。

输出报告中必须加入：

## 算法选择依据

| 算法 | 对应知识卡 | 适用原因 | 风险 |
|---|---|---|---|

如果发现算法选择涉及凸性、KKT、对偶、局部最优或全局最优表述，必须要求 `theory-checker` 复核。