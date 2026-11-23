# 知识卡：鲁棒拟合与正则化

来源文献：
- Boyd2004ConvexOptimization
- Nocedal2006NumericalOptimization
- Zhou2016MachineLearning

知识类别：
数据拟合 / 过拟合控制 / 鲁棒优化

适用题型：
回归拟合、参数估计、异常值数据、机器学习预测

引用等级：
A_cross_textbook

核心思想：
当数据存在噪声、异常值或模型过拟合风险时，可通过鲁棒损失函数、正则化项或约束控制模型复杂度，提高泛化能力和稳定性。

适用场景：
- 数据噪声明显。
- 参数较多。
- 样本量有限。
- 模型容易过拟合。
- 需要增强预测稳定性。

生产线调用：
- model-route-generator
- sensitivity-runner
- model-critic
- result-interpreter

风险：
正则化参数必须解释或通过验证集、交叉验证、敏感性分析确定。

需要人工查书补充：
公式细节需查原书对应章节。
