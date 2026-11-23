# 知识卡：模型评价与误差分析

来源文献：

* Tan2005MathModel
* Zhang2024ProbabilityStatistics
* Xia2016MathModelMATLAB
* Si2015ModelingAlgorithmsApplications

知识类别：质量控制
适用题型：所有论文
引用等级：A_textbook

核心思想：
模型不仅要能求解，还要经过合理性、误差、稳定性和适用性检验。

检查内容：

1. 数值是否合理。
2. 单位是否一致。
3. 约束是否满足。
4. 残差是否异常。
5. 参数是否敏感。
6. 结果是否符合常识。
7. 对照模型是否支持结论。
8. 是否存在过拟合。
9. 是否存在口径不一致。

常见指标：

* 绝对误差。
* 相对误差。
* 均方误差。
* 标准差。
* 置信区间。
* 残差图。
* 稳健性指标。

适合 skill：

* model-critic
* output-contract-validator
* evidence-trace-checker
* sensitivity-runner

风险：
没有检验的结果不能直接写成可靠结论。

## 公式细节边界

公式细节需查原书对应章节。知识卡仅作为方法选择和风险审查依据。不得直接替代论文中的模型推导。
