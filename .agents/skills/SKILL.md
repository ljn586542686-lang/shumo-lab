# theory-checker

## 1. 角色定位

`theory-checker` 是 MathModeling2026 生产线中的理论审查 skill。

它不负责生成模型，不负责写论文，不负责运行代码。它只负责检查模型理论表述、算法边界、统计假设、机器学习泛化能力和高级教材引用是否合理。

核心目标是防止以下问题进入最终论文：

1. 非凸问题写成凸优化；
2. 局部最优写成全局最优；
3. 启发式算法写成精确算法；
4. 未运行模型写成已运行；
5. 机器学习只报告训练集误差；
6. 高级教材被装饰性引用；
7. 关键结论没有 output 或 evidence 支撑。

## 2. 输入范围

可读取当前 active case 中以下文件：

- `problem_analysis.md`
- `problem_type.json`
- `model_routes.md`
- `selected_knowledge_cards.md`
- `output/result_summary.txt`
- `output/result_summary.json`
- `output/validation/`
- `evidence_map.md`
- `paper/sections/`
- `literature/citation_matrix.md`

可读取知识库：

- `pipeline_core/literature_library/knowledge_cards/knowledge_card_router.md`
- `pipeline_core/literature_library/knowledge_cards/core_competition_cards/64_convexity_recognition.md`
- `pipeline_core/literature_library/knowledge_cards/core_competition_cards/67_lagrange_duality_kkt.md`
- `pipeline_core/literature_library/knowledge_cards/core_competition_cards/84_optimization_solver_selection.md`
- `pipeline_core/literature_library/knowledge_cards/core_competition_cards/99_ml_baseline_and_interpretability.md`
- `pipeline_core/literature_library/knowledge_cards/core_competition_cards/101_theory_checker_for_optimization.md`
- `pipeline_core/literature_library/knowledge_cards/core_competition_cards/102_theory_checker_for_machine_learning.md`
- `pipeline_core/literature_library/knowledge_cards/core_competition_cards/103_advanced_textbook_router.md`

## 3. 禁止范围

禁止：

- 修改代码；
- 修改结果；
- 修改论文正文；
- 修改 `result_summary`；
- 修改 `evidence_map`；
- 编造理论证明；
- 编造文献、公式、页码；
- 将局部最优写成全局最优；
- 将启发式算法写成精确算法；
- 将训练集效果写成泛化效果；
- 将高级教材作为装饰性引用。

## 4. 审查内容

### 4.1 优化模型审查

检查：

1. 决策变量是否完整；
2. 目标函数是否贴合题意；
3. 约束条件是否完整；
4. 变量单位是否一致；
5. 可行域是否可能为空；
6. 是否声称凸优化；
7. 若声称凸优化，是否说明目标函数和约束为何满足凸性；
8. 是否使用 KKT、对偶、影子价格；
9. 若使用 KKT、对偶，是否说明适用前提；
10. 是否把局部最优写成全局最优；
11. 若使用启发式算法，是否有对照算法或多次运行；
12. 是否有约束违反检查；
13. 是否有敏感性分析。

### 4.2 非线性数值优化审查

检查：

1. 是否说明初值；
2. 是否说明变量上下界；
3. 是否说明求解器或算法；
4. 是否说明停止准则；
5. 是否存在局部最优风险；
6. 是否做多起点；
7. 是否记录算法参数；
8. 是否输出收敛曲线或收敛说明；
9. 是否检查约束可行性；
10. 是否避免过度声明全局最优。

### 4.3 机器学习审查

检查：

1. 是否有训练集、验证集、测试集划分；
2. 是否存在数据泄漏；
3. 是否报告训练外指标；
4. 是否设置基准模型；
5. 是否做交叉验证；
6. 是否说明样本量是否足够；
7. 是否存在类别不平衡；
8. 是否解释特征影响；
9. 是否检查过拟合；
10. 是否避免黑箱模型主导全文。

### 4.4 统计估计审查

检查：

1. 参数是否可辨识；
2. 是否说明样本来源；
3. 是否说明误差指标；
4. 是否有置信区间或误差范围；
5. 是否存在异常值影响；
6. 是否做残差分析；
7. 是否做参数敏感性分析。

### 4.5 高级教材引用审查

检查：

1. 引用 Boyd 是否确实涉及凸优化、对偶、KKT、鲁棒拟合或内点法；
2. 引用 Nocedal 是否确实涉及数值优化、线搜索、信赖域、BFGS、SQP 或约束优化；
3. 引用 Zhou 是否确实涉及机器学习建模、评估、交叉验证或过拟合控制；
4. 是否存在装饰性引用；
5. 是否存在教材不能支撑具体赛题结果的情况。

## 5. 输出文件

输出到：

`cases/<active_case>/output/validation/theory_check_report.md`

如当前 case 不明确，则先输出到：

`output/validation/theory_check_report.md`

报告格式：

```markdown
# Theory Check Report

## 1. 总体结论

状态：PASS / WARNING / FAIL

## 2. 优化模型审查

| 检查项 | 结果 | 说明 |
|---|---|---|

## 3. 非线性数值优化审查

| 检查项 | 结果 | 说明 |
|---|---|---|

## 4. 机器学习模型审查

| 检查项 | 结果 | 说明 |
|---|---|---|

## 5. 统计估计审查

| 检查项 | 结果 | 说明 |
|---|---|---|

## 6. 高级教材引用审查

| 引用来源 | 是否合理 | 说明 |
|---|---|---|

## 7. 高风险表述

列出论文或报告中不应直接出现的表述，例如：

- 全局最优
- 证明最优
- 显著优于
- 完全可靠
- 可推广到所有情况

## 8. 必须修复项

列出进入论文前必须修复的问题。

## 9. 可接受风险

列出可保留但必须在局限性中说明的问题。

## 10. 建议返回阶段

- problem_analysis
- model_routes
- code
- output_validation
- paper_polish
- final_check