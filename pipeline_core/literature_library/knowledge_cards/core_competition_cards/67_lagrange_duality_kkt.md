# 知识卡：Lagrange 对偶与 KKT 条件

来源文献：
- Boyd2004ConvexOptimization
- Nocedal2006NumericalOptimization

知识类别：
最优性条件 / 理论审查

适用题型：
约束优化、资源约束解释、灵敏度分析、理论证明

引用等级：
A_advanced_optimization_textbook

核心思想：
Lagrange 对偶为约束优化提供下界、资源影子价格和理论解释。KKT 条件给出约束优化问题的最优性必要条件，在凸优化并满足适当条件时也是充分条件。

适用场景：
- 模型求解理论说明。
- 资源边际价值解释。
- 约束有效性分析。
- 最优性条件说明。
- 对偶间隙检查。

生产线调用：
- theory-checker
- model-critic
- optimizer-arena
- sensitivity-runner

风险：
非凸问题中 KKT 条件通常只是必要条件，不能直接证明全局最优。

需要人工查书补充：
公式细节需查原书对应章节。
