# 知识卡：主动集方法

来源文献：
- Nocedal2006NumericalOptimization
- Boyd2004ConvexOptimization

知识类别：
约束优化算法

适用题型：
二次规划、边界约束、少量活跃约束问题

引用等级：
A_advanced_optimization_textbook

核心思想：
主动集方法通过猜测并更新最优点处的活跃约束集合，将不等式约束问题转化为一系列等式约束子问题。

适用场景：
- 二次规划。
- 约束数量适中。
- 需要识别哪些约束起作用。
- 资源边界或容量边界问题。

生产线调用：
- optimizer-arena
- theory-checker
- model-critic

风险：
活跃集识别错误会增加迭代次数。大规模问题可能不如内点法稳健。

需要人工查书补充：
公式细节需查原书对应章节。
