# 优秀论文结构吸收规则

当前状态：`PASS_WITH_LIMITATION / EXCELLENT_PAPER_STRUCTURE_ABSORPTION_READY`

## 1. 规则定位

本规则用于把优秀论文样本转化为生产线结构检查项，而不是转化为论文正文、参考文献或模型结论。当前优秀论文样本均为 `structure_sample`，不得作为方法理论主支撑。

## 2. 抽取深度限制

当前阶段：

- `extraction_depth = filename_metadata_only`
- `content_level_structure_extraction = blocked`
- `needs_pdf_text_parser_or_manual_review = true`

因此所有结构范式均是框架级规则，不是原文级抽取。

## 3. 模块接入规则

### 3.1 paper_outline_generator

应增加题型化 outline：

1. 优化类：问题拆解、变量定义、目标函数、约束、求解流程、验证与敏感性。
2. 统计类：数据审计、描述统计、特征构造、模型解释、验证、异常处理。
3. 统计+优化混合类：统计诊断、预测或评价、优化决策、方案比较、边界说明。
4. fallback 类：复杂题型抽象、最小闭环、近似说明、扩展边界和风险验证。

不得生成可直接提交的正文段落。

### 3.2 abstract_generator

摘要必须检查是否包含：

1. 研究对象和问题范围。
2. 数据来源和样本规模。
3. 核心方法或模型组合。
4. 关键结果和评价指标。
5. 验证、敏感性或误差分析。
6. 结论边界和非正式提交标记。

不得复制优秀论文摘要句式。

### 3.3 model_assumption_checker

模型假设必须对应：

1. 数据缺失或异常。
2. 变量简化。
3. 约束近似。
4. 求解可行性。
5. 预测模型进入优化模型的误差边界。

空泛假设应标记为 `NEEDS_REVISION`。

### 3.4 evidence_chain_builder

每条核心结论必须绑定：

1. 输入数据或题面来源。
2. 代码或计算脚本。
3. 输出表或图。
4. 验证报告。
5. 人工决策或假设说明。

优秀论文样本不能作为当前赛题结果的证据。

### 3.5 figure_table_quality_checker

必须检查：

1. 图表标题、坐标轴、图例是否中文。
2. 表格是否有机器版和展示版边界。
3. 图表是否有数据来源。
4. 图表结论是否在正文中被正确解释。
5. 是否存在照搬优秀论文图表结构的风险。

### 3.6 sensitivity_analysis_gate

必须按题型检查：

1. 优化类：权重、预算、容量、时间、资源上限。
2. 统计类：异常值、划分方式、阈值、评价指标。
3. 混合类：预测误差、成本权衡、目标权重。
4. fallback 类：边权、速度、时间步长、物理近似或网络连通性。

不得生成未运行出的定量敏感性结果。

### 3.7 limitation_and_validation_writer

必须写明：

1. baseline 或 heuristic 的局限。
2. 预测结果不等于真实结果。
3. 近似条件对结论的影响。
4. 未处理问题和不覆盖范围。
5. 正式提交前仍需人工复核。

### 3.8 final_human_review_gate

必须检查：

1. 是否复制或仿写优秀论文原文。
2. 是否把优秀论文样本作为正式理论依据。
3. 是否把结构样本写成 citation。
4. 是否把 baseline 写成全局最优。
5. 是否存在 final_submit 自动创建。
6. 是否自动通过 human gate。

## 4. 验收规则

只有当结构吸收结果仍保持“规则、清单、模板、质量门”层级时，才允许进入后续生产线。若输出可直接提交的论文正文，必须标记为 `BLOCKED / EXCELLENT_PAPER_ABSORPTION_SAFETY_VIOLATION`。

