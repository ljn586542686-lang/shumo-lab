# 知识卡：模型评估、交叉验证与过拟合控制

来源文献：
- Zhou2016MachineLearning

知识类别：
机器学习评估

适用题型：
分类、回归、机器学习预测、算法比较

引用等级：
A_machine_learning_textbook

核心思想：
机器学习模型必须评价泛化能力。训练误差不能代表真实性能，需要通过留出法、交叉验证、自助法或测试集评估模型。

适用场景：
- 分类任务。
- 回归任务。
- 算法比较。
- 机器学习预测。
- 数据量有限时的模型评估。

生产线调用：
- model-critic
- sensitivity-runner
- result-interpreter
- output-contract-validator

风险：
数据泄漏、样本量不足、类别不平衡会导致评价失真。

需要人工查书补充：
公式细节需查原书对应章节。
