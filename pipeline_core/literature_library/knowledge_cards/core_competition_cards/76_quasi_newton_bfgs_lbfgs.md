# 知识卡：拟牛顿、BFGS 与 L-BFGS

来源文献：
- Nocedal2006NumericalOptimization

知识类别：
数值优化算法

适用题型：
无约束优化、大规模优化、参数估计、机器学习训练

引用等级：
A_advanced_optimization_textbook

核心思想：
拟牛顿方法通过迭代更新 Hessian 或其逆的近似矩阵，在避免精确二阶导数计算的同时获得较快收敛速度。L-BFGS 适用于大规模问题。

适用场景：
- 梯度可计算。
- Hessian 计算成本高。
- 变量维度较大。
- 需要比梯度下降更快的收敛。

生产线调用：
- optimizer-arena
- model-critic
- theory-checker

风险：
尺度、初值和线搜索参数会影响结果。非凸问题仍可能陷入局部最优。

需要人工查书补充：
公式细节需查原书对应章节。
