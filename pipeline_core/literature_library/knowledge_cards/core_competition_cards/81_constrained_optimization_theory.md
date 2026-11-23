# 知识卡：约束优化理论

来源文献：
- Nocedal2006NumericalOptimization
- Boyd2004ConvexOptimization

知识类别：
约束优化 / 最优性条件

适用题型：
带等式约束、不等式约束、边界约束、资源约束优化

引用等级：
A_advanced_optimization_textbook

核心思想：
约束优化需要同时考虑目标下降和约束满足。理论分析包括可行方向、约束资格条件、Lagrange 乘子、KKT 条件和二阶条件。

适用场景：
- fmincon 类求解。
- 复杂约束优化。
- 需要解释最优性。
- 需要检查约束是否激活。

生产线调用：
- theory-checker
- optimizer-arena
- model-critic
- output-contract-validator

风险：
若约束资格条件不成立，KKT 条件可能失效。论文中应避免过度理论化。

需要人工查书补充：
公式细节需查原书对应章节。
