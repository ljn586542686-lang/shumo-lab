# 知识卡：信赖域方法

来源文献：
- Nocedal2006NumericalOptimization

知识类别：
数值优化算法

适用题型：
非线性优化、局部优化、Hessian 不稳定、非凸局部模型

引用等级：
A_advanced_optimization_textbook

核心思想：
信赖域方法在当前点附近建立近似模型，并限制步长在可信区域内，通过实际下降与预测下降比值调整信赖域半径。

适用场景：
- 二阶信息可用或可近似。
- Newton 法可能不稳定。
- Hessian 可能不正定。
- 需要稳健局部搜索。

生产线调用：
- optimizer-arena
- theory-checker
- model-critic

风险：
仍然是局部优化方法，不能单独证明非凸问题全局最优。

需要人工查书补充：
公式细节需查原书对应章节。
