# 知识卡：内点法

来源文献：
- Boyd2004ConvexOptimization
- Nocedal2006NumericalOptimization

知识类别：
约束优化算法

适用题型：
线性规划、二次规划、凸优化、带不等式约束问题

引用等级：
A_advanced_optimization_textbook

核心思想：
内点法通过构造 barrier function 或 primal-dual KKT 系统，在可行域内部逐步逼近最优解，适合求解大类凸优化问题。

适用场景：
- LP/QP/SOCP/SDP。
- 凸约束优化。
- 大量不等式约束。
- 需要可靠数值求解。

生产线调用：
- optimizer-arena
- theory-checker
- model-critic
- result-validator

风险：
内点法需要可行性条件和数值稳定性检查。对非凸问题不能直接保证全局最优。

需要人工查书补充：
公式细节需查原书对应章节。
