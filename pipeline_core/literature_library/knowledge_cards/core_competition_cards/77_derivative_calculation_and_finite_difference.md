# 知识卡：导数计算、有限差分与自动微分

来源文献：
- Nocedal2006NumericalOptimization

知识类别：
数值导数 / 优化实现

适用题型：
梯度优化、敏感性分析、黑箱模型、参数估计

引用等级：
A_advanced_optimization_textbook

核心思想：
优化算法依赖梯度、Jacobian 或 Hessian。若解析导数难以获得，可使用有限差分、稀疏差分或自动微分近似或计算导数。

适用场景：
- 目标函数复杂。
- 代码可运行但难推导导数。
- 需要敏感性分析。
- 需要比较解析梯度和数值梯度。

生产线调用：
- optimizer-arena
- sensitivity-runner
- model-critic
- theory-checker

风险：
有限差分步长过大导致截断误差，过小导致舍入误差。必须做导数检查。

需要人工查书补充：
公式细节需查原书对应章节。
