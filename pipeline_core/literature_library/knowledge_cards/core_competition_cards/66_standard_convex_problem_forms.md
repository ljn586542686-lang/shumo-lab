# 知识卡：标准凸优化问题形式

来源文献：
- Boyd2004ConvexOptimization

知识类别：
优化模型分类

适用题型：
LP、QP、QCQP、GP、SOCP、SDP、向量优化

引用等级：
A_convex_optimization_textbook

核心思想：
许多数模问题可以整理为标准凸优化形式。识别标准形式有利于选择求解器、解释可解性和构建论文模型层次。

适用场景：
- 资源分配。
- 最小二乘拟合。
- 投资组合。
- 鲁棒优化。
- 距离几何。
- 控制与估计问题。

生产线调用：
- problem-type-router
- model-route-generator
- optimizer-arena
- theory-checker

风险：
不应为了套高级名词强行把普通模型包装成 SDP/SOCP。只有确实符合结构时才使用。

需要人工查书补充：
公式细节需查原书对应章节。
