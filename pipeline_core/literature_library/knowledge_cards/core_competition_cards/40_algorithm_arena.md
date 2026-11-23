# 知识卡：算法擂台与模型对照

来源文献：
- Nocedal2006NumericalOptimization

* Tan2005MathModel
* Pu2024OperationsResearch
* Xia2016MathModelMATLAB
* Si2015ModelingAlgorithmsApplications

知识类别：竞赛质量提升
适用题型：所有需要冲奖的题目
引用等级：A_textbook

核心思想：
不要只跑一个模型。高质量论文通常需要基准模型、主模型、对照模型和稳健性检验，证明选择主模型是有依据的。

推荐结构：

1. Baseline：简单基准模型。
2. Main model：主模型。
3. Alternative model：替代模型。
4. Stress test：扰动或极端条件测试。
5. Final selection：说明为何选择最终模型。

适用场景：

* 优化类题。
* 预测类题。
* 评价类题。
* 含随机性的题。
* 含多目标权衡的题。

输出：

* optimizer_comparison.xlsx
* model_comparison.md
* convergence_report.md
* best_model_selection.md

风险：
不要把没有实际运行的对照算法写成已运行。

## 公式细节边界

公式细节需查原书对应章节。知识卡仅作为方法选择和风险审查依据。不得直接替代论文中的模型推导。
