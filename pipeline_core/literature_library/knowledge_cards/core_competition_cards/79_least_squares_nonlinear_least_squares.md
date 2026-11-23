# 知识卡：线性与非线性最小二乘

来源文献：
- Nocedal2006NumericalOptimization
- Boyd2004ConvexOptimization

知识类别：
拟合 / 参数估计

适用题型：
曲线拟合、参数辨识、模型校准、残差最小化

引用等级：
A_cross_textbook

核心思想：
最小二乘通过最小化观测值与模型输出之间的残差平方和来估计参数。非线性最小二乘常用 Gauss-Newton 或 Levenberg-Marquardt 方法。

适用场景：
- 参数估计。
- 曲线拟合。
- 系统辨识。
- 预测模型校准。
- 误差最小化。

生产线调用：
- model-route-generator
- optimizer-arena
- result-interpreter
- model-critic

风险：
异常值会显著影响 L2 最小二乘，需要残差图和鲁棒拟合对照。

需要人工查书补充：
公式细节需查原书对应章节。
