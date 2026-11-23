# 论文草稿总检查清单

状态：`PASS_WITH_LIMITATION / PAPER_QUALITY_LAYER_BASELINE_READY`

| 检查项 | 要求 | 风险等级 |
|---|---|---|
| 摘要 | 包含问题、方法、结果、结论边界 | HIGH |
| 问题分析 | 对应题面每一问，不漏问 | HIGH |
| 模型假设 | 必要、合理、可解释 | MEDIUM |
| 题型匹配 | 模型与题型路由一致 | HIGH |
| 公式变量 | 变量、参数、单位定义完整 | HIGH |
| 数据处理 | 能追溯到 raw、processed、schema | HIGH |
| 结果来源 | 结果来自代码、result_summary、output/tables | HIGH |
| 图表 | 中文标题、编号、图注、来源完整 | MEDIUM |
| 验证与敏感性 | 有验证指标和扰动分析；未运行不得编造 | HIGH |
| 结论边界 | 不夸大、不越界、不写无证据结论 | HIGH |
| 局限性 | 写清未覆盖问题、模型限制和数据风险 | MEDIUM |
| AI 使用披露 | 按 `showcase_mode` 或 `official_submission_mode` | HIGH |
| 草稿标记 | 保留 `DRAFT_ONLY / NOT_FOR_FINAL_SUBMISSION` | HIGH |
| final_submit | 保持禁止，不创建 `final_submit/` | HIGH |

## 必查结论

1. 草稿不是正式比赛论文。
2. baseline 不是全局最优。
3. 预测值不是真实值。
4. 局部最小闭环不是完整赛题结果。
5. 没有证据来源的内容不得进入正文。

## Track C v1 写作约束

以下内容是现有 writer 的表达约束，不创建新的质量系统、审查阶段或状态层。

### Hard Rules

- `H01 CLAIM_STRENGTH_FOLLOWS_EVIDENCE`：结论强度服从证据。有限搜索、启发式、`best observed`、模拟均值最小不得自动写成 `global`、`unique` 或 `proven optimum`。
- `H02 KEY_MATH_NEEDS_RATIONALE`：作者自行引入的关键数学结构第一次出现时，正文先说明它描述的现实或数学对象、当前需要它的原因，以及后续承担的作用。题面直接给出的公式、普通定义和简单符号声明除外。
- `H03 INTERNAL_PROCESS_INVISIBLE`：论文不得出现 `gate`、`provenance`、`pipeline`、`review token`、`internal audit`、`AI workflow` 等内部生产语言。
- `H04 PRECISION_FOLLOWS_UNCERTAINTY`：数值保留位数和 uncertainty expression 由真实误差或随机性决定。不得根据程序输出位数制造假精度，也不得把“模型中有随机变量”机械等同于“所有结果必须 CI”。
- `H05 EVALUATION_CLAIMS_REQUIRE_EVIDENCE`：任何“准确、可靠、稳定、鲁棒、推广性强”等评价，必须有匹配的实际证据。模型评价应围绕实际评价对象给出相应证据，例如模型能力、适用边界、关键假设影响或误差表现；不要求机械覆盖固定项目。不评价论文层次、数学严谨或软件先进。历史 `H07` / `NO_UNSUPPORTED_SELF_PRAISE` 引用统一按本条解释，不形成重复 Hard Rule。
- `H06 NO_SOFTWARE_AS_EVIDENCE`：MATLAB、Python、SPSS、Excel、Lingo 等软件的使用不能作为模型正确或可靠的证据。
- `H08 RESULT_MUST_RETURN_TO_PROBLEM`：结果最终翻译回题目中的对象、方案和决策，不停留在模型名、算法名或内部指标。
- `H09 FROZEN_RESULT_PROTECTION`：writer 不得修改 frozen result，不得改数字、改方案、改模型含义或扩大 claim。
- `H10 QUESTION_RELATION_MUST_BE_CLEAR`：当后一问继承、增加、放宽或替换前问内容时，必须让读者知道什么沿用、什么改变；不要求在论文中输出内部关系标签。

### Valves

- `THEORY_DEPTH_VALVE`：L0 标准定义/关系保持简洁；L1 简短推导保留关键步骤；L2 核心建模关系给出完整逻辑；L3 定理/证明只有在能增强关键 claim、给出上/下界、说明可行性/最优性、单调性、缩小搜索空间或解释重要非直觉结果时才升级。机械代数、教材式证明和标准算法原理应压缩或放入附录。
- `FIGURE_FUNCTION_TEST`：图至少服务于几何/空间关系、模型结构/状态变化、趋势、方案比较、误差/不确定性/诊断、可行域/边界或题目特有算法结构。
- `REMOVE_FIGURE_TEST`：删除图后若理解没有明显损失，且无需大量文字替代，优先不保留该图。
- 不建立“禁止流程图”Hard Rule；流程图按是否承载实际信息判断。

### Soft Checklist

- `S01` 问题分析只承担困难识别和路线选择，不提前完整推导。
- `S02` 分问题原则上让读者在离开该问前知道结果；允许共享公共模型。
- `S03` 证据尽量靠近对应 claim。
- `S04` 推荐后解释为什么推荐。
- `S05` 普通假设保持简短；关键 consequential assumption 才补理由、尺度或 limitation。
- `S06` 摘要优先“问题—关键方法—关键结果”，不按算法数量评价。
- `S07` 精确参数优先表；趋势、结构、比较和诊断优先图；避免正文、表、图机械重复。
- `S08` 结论回到题目语言，不复制摘要。

Track C 不承载 Modeling、Validation 或 Technical Trust 范围的 profile；相关规则不在本清单中重复。Track C 写作规则的外部论文证据限于 CUMCM 官方展示论文或经人工确认适合作为竞赛表达样本的优秀竞赛论文，并可结合自身竞赛实践进行反证；学术论文、硕博论文和教材不得作为 Track C 写作规则的 provenance。
