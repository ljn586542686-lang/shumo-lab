# 知识卡：优化求解器选择策略

来源文献：
- Nocedal2006NumericalOptimization
- Boyd2004ConvexOptimization
- Si2015ModelingAlgorithmsApplications

知识类别：
生产线算法选择

适用题型：
所有优化类题目

引用等级：
pipeline_rule

核心思想：
不同优化模型应匹配不同求解策略。先判断变量类型、目标函数、约束结构、凸性、规模和导数可用性，再选择求解器或算法。

适用场景：
- 线性规划。
- 整数规划。
- 凸二次规划。
- 非线性可导优化。
- 无导数黑箱优化。
- 大规模光滑优化。
- 多目标优化。
- 不确定优化。

生产线调用：
- problem-type-router
- model-route-generator
- optimizer-arena
- model-critic
- theory-checker

风险：
不得只因某算法“高级”就使用。算法必须匹配模型结构。

需要人工查书补充：
具体求解器参数需结合实际软件文档。
