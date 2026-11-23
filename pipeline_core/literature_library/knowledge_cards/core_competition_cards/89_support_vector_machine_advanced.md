# 知识卡：支持向量机与核方法

来源文献：
- Zhou2016MachineLearning
- Boyd2004ConvexOptimization

知识类别：
分类 / 回归 / 凸优化

适用题型：
小样本分类、高维分类、非线性分类、故障诊断

引用等级：
A_cross_textbook

核心思想：
支持向量机通过最大间隔原则构造分类器，并利用核函数处理非线性分类。其训练过程可与凸二次规划联系起来。

适用场景：
- 样本量中小。
- 维度较高。
- 分类边界复杂。
- 需要较强泛化能力。
- 需要机器学习对照模型。

生产线调用：
- model-route-generator
- optimizer-arena
- model-critic
- theory-checker

风险：
核函数和参数选择会显著影响结果。必须进行参数搜索和交叉验证。

需要人工查书补充：
公式细节需查原书对应章节。
