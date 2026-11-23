# 知识卡：非线性方程与求根

来源文献：
- Nocedal2006NumericalOptimization

知识类别：
数值求解 / 方程组

适用题型：
平衡条件、KKT 方程、非线性系统、参数反解

引用等级：
A_advanced_optimization_textbook

核心思想：
非线性方程求解用于求解满足 F(x)=0 的变量，可通过 Newton 法、Broyden 法、线搜索或信赖域方法增强稳定性。

适用场景：
- 平衡方程。
- KKT 方程。
- 物理系统反解。
- 参数校准。
- 多变量非线性方程组。

生产线调用：
- optimizer-arena
- theory-checker
- model-critic

风险：
Newton 类方法依赖初值，可能不收敛。需要残差检查和多初值验证。

需要人工查书补充：
公式细节需查原书对应章节。
