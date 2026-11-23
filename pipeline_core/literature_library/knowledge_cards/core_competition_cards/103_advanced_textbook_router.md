# 知识卡：高级教材路由规则

来源文献：
- Nocedal2006NumericalOptimization
- Boyd2004ConvexOptimization
- Zhou2016MachineLearning

知识类别：
文献路由 / 生产线控制

适用题型：
优化、凸优化、机器学习、预测、拟合、分类

引用等级：
pipeline_rule

核心思想：
三本高级教材应按问题类型精准调用，避免文献泛化堆砌。

适用场景与调用规则：
- 若题目是凸优化、对偶、KKT、鲁棒拟合、内点法，调用 Boyd2004ConvexOptimization。
- 若题目是非线性求解、线搜索、信赖域、BFGS、L-BFGS、无导数优化、SQP，调用 Nocedal2006NumericalOptimization。
- 若题目使用分类、回归、聚类、SVM、神经网络、集成学习、特征选择，调用 Zhou2016MachineLearning。
- 若模型为 SVM、正则化回归、逻辑回归，同时调用 Boyd 与 Zhou。
- 若模型为非凸启发式优化，同时调用 Nocedal 与竞赛算法教材，但不得用 Boyd 证明全局最优。
- 若模型只是普通线性规划或基础预测，优先调用原五教材，不必强制引用高级教材。

生产线调用：
- problem-type-router
- model-route-generator
- literature-grounder
- citation-auditor
- model-critic

风险：
高级教材引用必须服务于正文具体主张，不得装饰性引用。

需要人工查书补充：
无。该卡为路由规则卡。
