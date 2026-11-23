# 知识卡：贝叶斯分类器

来源文献：
- Zhou2016MachineLearning
- Zhang2024ProbabilityStatistics

知识类别：
概率分类模型

适用题型：
分类、风险概率、文本分类、故障诊断

引用等级：
A_cross_textbook

核心思想：
贝叶斯分类器基于先验概率、条件概率和后验概率进行分类。朴素贝叶斯通过条件独立假设简化计算。

适用场景：
- 特征可概率建模。
- 需要输出类别概率。
- 文本、离散特征或小样本分类。
- 作为机器学习基准模型。

生产线调用：
- model-route-generator
- optimizer-arena
- model-critic

风险：
朴素贝叶斯的条件独立假设较强，必须说明其近似性质。

需要人工查书补充：
公式细节需查原书对应章节。
