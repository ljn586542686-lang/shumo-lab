# 知识卡：无导数优化

来源文献：
- Nocedal2006NumericalOptimization

知识类别：
黑箱优化 / 模式搜索

适用题型：
不可导目标、仿真黑箱、噪声目标、复杂工程模型

引用等级：
A_advanced_optimization_textbook

核心思想：
无导数优化适用于无法获得梯度或目标函数不光滑的情况，通过坐标搜索、模式搜索、Nelder-Mead、插值模型等方式寻找改进方向。

适用场景：
- 目标函数来自仿真。
- 梯度不可得。
- 目标存在噪声。
- 函数不光滑。
- 快速寻找可行较优解。

生产线调用：
- optimizer-arena
- model-route-generator
- model-critic
- sensitivity-runner

风险：
无导数优化通常计算成本高，且难以证明全局最优。必须设置预算、重复实验和对照算法。

需要人工查书补充：
公式细节需查原书对应章节。
