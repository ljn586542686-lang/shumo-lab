# 知识卡：线搜索方法

来源文献：
- Nocedal2006NumericalOptimization

知识类别：
数值优化算法

适用题型：
无约束优化、非线性优化、参数估计、局部搜索

引用等级：
A_advanced_optimization_textbook

核心思想：
线搜索方法先确定下降方向，再沿该方向选择合适步长。其核心是兼顾充分下降和收敛稳定性。

适用场景：
- 目标函数可计算梯度。
- 需要局部优化。
- 需要解释步长选择。
- 使用 fminunc、fmincon 或自定义梯度优化。

生产线调用：
- optimizer-arena
- model-critic
- theory-checker

风险：
初值和尺度会显著影响结果，需要多起点或尺度归一化。

需要人工查书补充：
公式细节需查原书对应章节。
