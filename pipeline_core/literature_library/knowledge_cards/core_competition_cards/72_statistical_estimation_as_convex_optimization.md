# 知识卡：统计估计的凸优化表达

来源文献：
- Boyd2004ConvexOptimization
- Zhou2016MachineLearning

知识类别：
统计建模 / 机器学习优化

适用题型：
参数估计、分类、回归、实验设计、假设检验

引用等级：
A_cross_textbook

核心思想：
很多统计估计问题可以表述为优化问题，例如最小二乘、最大似然、正则化估计、分类间隔最大化和实验设计。

适用场景：
- 参数估计。
- 回归拟合。
- 分类器训练。
- 最优实验设计。
- 检测器设计。
- 正则化预测模型。

生产线调用：
- model-route-generator
- optimizer-arena
- model-critic
- sensitivity-runner

风险：
统计估计结果必须有数据划分、误差指标和泛化能力验证，不能只报告训练误差。

需要人工查书补充：
公式细节需查原书对应章节。
