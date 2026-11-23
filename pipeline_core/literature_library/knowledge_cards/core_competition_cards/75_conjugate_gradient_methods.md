# 知识卡：共轭梯度方法

来源文献：
- Nocedal2006NumericalOptimization

知识类别：
大规模优化 / 线性系统 / 二次优化

适用题型：
大规模线性方程、二次优化、无约束优化、稀疏问题

引用等级：
A_advanced_optimization_textbook

核心思想：
共轭梯度方法利用共轭方向高效求解对称正定线性系统或凸二次优化问题，非线性共轭梯度可用于一般无约束优化。

适用场景：
- 变量维度较高。
- Hessian 无法显式存储。
- 稀疏线性系统。
- 二次型优化。
- 大规模无约束优化。

生产线调用：
- optimizer-arena
- model-critic
- theory-checker

风险：
线性 CG 需要正定性；非线性 CG 对线搜索和重启策略敏感。

需要人工查书补充：
公式细节需查原书对应章节。
