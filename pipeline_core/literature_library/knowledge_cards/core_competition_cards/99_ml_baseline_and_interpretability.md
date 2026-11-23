# 知识卡：机器学习基准模型与可解释性

来源文献：
- Zhou2016MachineLearning
- Zhang2024ProbabilityStatistics

知识类别：
机器学习审查

适用题型：
所有使用机器学习的题目

引用等级：
pipeline_rule

核心思想：
机器学习模型必须有基准模型和解释性分析。不能只报告一个高精度黑箱模型。

适用场景：
- 分类模型。
- 回归模型。
- 集成学习模型。
- 神经网络模型。
- 所有机器学习预测任务。

生产线调用：
- model-critic
- output-contract-validator
- result-interpreter
- sensitivity-runner

风险：
数据量小、指标不清、无验证集时，机器学习模型容易成为论文弱点。

需要人工查书补充：
无。该卡为生产线规则卡。
