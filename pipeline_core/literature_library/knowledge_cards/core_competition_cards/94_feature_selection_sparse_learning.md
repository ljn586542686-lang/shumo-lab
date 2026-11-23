# 知识卡：特征选择与稀疏学习

来源文献：
- Zhou2016MachineLearning
- Boyd2004ConvexOptimization

知识类别：
特征工程 / 正则化

适用题型：
高维数据、小样本、指标筛选、解释性建模

引用等级：
A_cross_textbook

核心思想：
特征选择用于从候选特征中筛出有用变量，稀疏学习通过正则化或约束使模型只依赖少量重要特征。

适用场景：
- 指标过多。
- 样本量有限。
- 需要解释关键因素。
- 需要防止过拟合。
- 需要降低模型复杂度。

生产线调用：
- data-auditor
- model-route-generator
- model-critic
- sensitivity-runner

风险：
特征选择必须在训练数据内完成，不能使用测试集信息，否则会数据泄漏。

需要人工查书补充：
公式细节需查原书对应章节。
