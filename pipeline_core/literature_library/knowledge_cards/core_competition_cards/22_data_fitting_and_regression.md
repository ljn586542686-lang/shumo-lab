# 知识卡：数据拟合与回归分析

来源文献：
- Boyd2004ConvexOptimization
- Nocedal2006NumericalOptimization

* Tan2005MathModel
* Zhang2024ProbabilityStatistics
* Xia2016MathModelMATLAB
* Si2015ModelingAlgorithmsApplications

知识类别：数据建模
适用题型：趋势拟合、参数估计、预测、实验数据分析
引用等级：A_textbook

核心思想：
通过建立自变量和因变量之间的函数关系，使模型尽可能拟合观测数据，并用残差和误差指标评价模型效果。

常见方法：

* 线性回归。
* 多项式拟合。
* 非线性拟合。
* 最小二乘。
* 分段拟合。
* 插值拟合。

MATLAB 实现：

* polyfit。
* polyval。
* fit。
* lsqcurvefit。
* interp1。
* spline。

常见评价：

* 残差。
* 均方误差。
* 平均绝对误差。
* 相对误差。
* 拟合优度。

风险：
过拟合是主要风险。必须设置验证集、误差分析或模型复杂度控制。

## 公式细节边界

公式细节需查原书对应章节。知识卡仅作为方法选择和风险审查依据。不得直接替代论文中的模型推导。
