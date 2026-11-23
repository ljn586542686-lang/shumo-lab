# 知识卡：罚函数、增广拉格朗日与 SQP

来源文献：
- Nocedal2006NumericalOptimization

知识类别：
非线性约束优化算法

适用题型：
非线性约束、难可行问题、工程优化、复杂目标函数

引用等级：
A_advanced_optimization_textbook

核心思想：
罚函数法将约束违反加入目标函数；增广拉格朗日法结合乘子估计和罚项；SQP 通过连续求解二次规划近似原非线性约束优化问题。

适用场景：
- 非线性约束优化。
- 约束不易直接处理。
- MATLAB fmincon 或自定义约束优化。
- 需要解释求解算法。

生产线调用：
- optimizer-arena
- theory-checker
- model-critic

风险：
罚参数选择会影响数值稳定性。SQP 结果依赖初值和局部近似，不保证非凸问题全局最优。

需要人工查书补充：
公式细节需查原书对应章节。
