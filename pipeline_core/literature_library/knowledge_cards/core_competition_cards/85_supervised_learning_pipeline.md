# 知识卡：监督学习建模流程

来源文献：
- Zhou2016MachineLearning

知识类别：
机器学习流程

适用题型：
分类、回归、预测、模式识别

引用等级：
A_machine_learning_textbook

核心思想：
监督学习根据带标签样本训练模型，并在独立验证数据上评价泛化能力。比赛论文中，机器学习应作为数据驱动模型或对照模型，而不是替代题意建模。

适用场景：
- 分类预测。
- 回归预测。
- 模式识别。
- 机器学习对照模型。
- 多特征数据建模。

生产线调用：
- problem-type-router
- model-route-generator
- model-critic
- sensitivity-runner

风险：
只报告训练集效果属于高风险。必须报告测试误差或交叉验证结果。

需要人工查书补充：
公式细节需查原书对应章节。
