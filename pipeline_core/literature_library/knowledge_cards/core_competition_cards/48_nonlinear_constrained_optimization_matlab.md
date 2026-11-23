# 知识卡：非线性约束优化与 MATLAB 求解

来源文献：
- Nocedal2006NumericalOptimization

* Si2015ModelingAlgorithmsApplications
* Xia2016MathModelMATLAB
* Tan2005MathModel

知识类别：非线性规划 / 软件实现
适用题型：几何优化、轨迹优化、能耗优化、非线性约束、多变量连续优化
引用等级：A_competition_algorithm_textbook

核心思想：
非线性规划用于处理目标函数或约束为非线性的优化问题。MATLAB 可通过 fmincon、fminsearch、遗传算法工具箱等方式求解。

常见形式：
min f(x)
s.t. c(x) <= 0
ceq(x) = 0
lb <= x <= ub

适用场景：

* 几何距离约束。
* 航迹优化。
* 参数调优。
* 非线性物理模型。
* 多变量连续决策。

生产线要求：

1. 必须记录初值、边界和约束。
2. 必须输出可行性检查。
3. 必须做多起点搜索或算法对照。
4. 必须做敏感性分析。

风险：
容易陷入局部最优。论文中不能直接宣称全局最优。

## 公式细节边界

公式细节需查原书对应章节。知识卡仅作为方法选择和风险审查依据，不得直接替代论文中的模型推导。
