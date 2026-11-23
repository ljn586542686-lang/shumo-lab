# 知识卡：规则学习

来源文献：
- Zhou2016MachineLearning

知识类别：
可解释机器学习

适用题型：
分类规则、风险规则、专家规则提取

引用等级：
B_machine_learning_textbook

核心思想：
规则学习通过生成 if-then 规则描述数据中的决策逻辑，适合需要可解释性的分类或评价问题。

适用场景：
- 需要明确规则。
- 数据特征离散或可离散化。
- 需要和人工判断结合。
- 需要模型可解释。

生产线调用：
- model-route-generator
- result-interpreter
- model-critic

风险：
规则过多会降低可读性；规则过少会欠拟合。需要验证集检验。

需要人工查书补充：
公式细节需查原书对应章节。
