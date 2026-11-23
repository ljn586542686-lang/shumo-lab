# 知识卡：非线性规划

来源文献：
- Nocedal2006NumericalOptimization

* Tan2005MathModel
* Xia2016MathModelMATLAB
* Si2015ModelingAlgorithmsApplications

知识类别：连续优化
适用题型：几何优化、能耗优化、物理过程、非线性约束、多变量优化
引用等级：A_textbook

核心思想：
当目标函数或约束中存在乘积、平方、距离、三角函数、指数、分式等非线性关系时，应建立非线性规划模型。

常见结构：
max/min f(x)
s.t. g_i(x) <= 0
h_j(x) = 0

常见求解：

* 多起点局部搜索。
* 梯度法。
* 模式搜索。
* 遗传算法。
* 粒子群。
* 模拟退火。
* MATLAB fmincon。
* Python scipy.optimize。

风险：
容易陷入局部最优；必须说明初值、边界、算法设置，并最好设置算法擂台。

## 公式细节边界

公式细节需查原书对应章节。知识卡仅作为方法选择和风险审查依据。不得直接替代论文中的模型推导。
