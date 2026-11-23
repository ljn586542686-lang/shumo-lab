# 知识卡：敏感性分析

来源文献：
- Boyd2004ConvexOptimization
- Nocedal2006NumericalOptimization

* Tan2005MathModel
* Pu2024OperationsResearch
* Zhang2024ProbabilityStatistics
* Si2015ModelingAlgorithmsApplications

知识类别：稳健性分析
适用题型：优化、预测、评价、仿真、随机模型
引用等级：A_textbook

核心思想：
通过扰动参数、权重、阈值、步长或随机种子，观察模型结果是否稳定，从而判断结论可靠性。

常见扰动：

* 参数扰动。
* 权重扰动。
* 阈值扰动。
* 时间步长扰动。
* 网格密度扰动。
* 随机种子扰动。
* 样本扰动。

输出：

* 敏感性表。
* 敏感性曲线。
* 参数影响排序。
* 稳定区间。
* 风险参数说明。

论文表达：
“对关键参数进行扰动实验，以检验模型结论对参数变化的敏感程度。”

风险：
不能写未运行出的敏感性结果。所有敏感性数值必须来自 output/sensitivity。

## 公式细节边界

公式细节需查原书对应章节。知识卡仅作为方法选择和风险审查依据。不得直接替代论文中的模型推导。
