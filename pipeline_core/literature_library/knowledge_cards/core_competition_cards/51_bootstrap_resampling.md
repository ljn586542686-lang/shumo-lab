# 知识卡：Bootstrap 重抽样方法

来源文献：
- Zhou2016MachineLearning

* Si2015ModelingAlgorithmsApplications
* Zhang2024ProbabilityStatistics

知识类别：统计推断 / 不确定性评估
适用题型：样本量有限、估计误差、置信区间、模型稳定性
引用等级：A_competition_algorithm_textbook

核心思想：
Bootstrap 通过从原始样本中有放回抽样构造重复样本，用于估计统计量分布、置信区间和模型结果稳定性。

适用场景：

* 样本量不大。
* 理论分布未知。
* 需要估计置信区间。
* 需要评估模型稳定性。
* 需要验证预测误差。

常见输出：

* 统计量均值。
* 标准误。
* 置信区间。
* 稳定性区间。
* 重抽样分布图。

风险：
样本本身若有系统偏差，Bootstrap 不能消除偏差。

## 公式细节边界

公式细节需查原书对应章节。知识卡仅作为方法选择和风险审查依据，不得直接替代论文中的模型推导。
