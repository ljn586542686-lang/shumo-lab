# 知识卡：集成学习

来源文献：
- Zhou2016MachineLearning

知识类别：
机器学习增强模型

适用题型：
分类、回归、预测、算法对照

引用等级：
A_machine_learning_textbook

核心思想：
集成学习通过组合多个学习器提高泛化性能，常见思路包括 bagging、boosting 和随机森林。

适用场景：
- 单模型不稳定。
- 需要提高预测精度。
- 数据量较充足。
- 可作为主模型或对照模型。

生产线调用：
- model-route-generator
- optimizer-arena
- model-critic

风险：
模型解释性下降。必须报告特征重要性、误差指标和对照模型。

需要人工查书补充：
公式细节需查原书对应章节。
