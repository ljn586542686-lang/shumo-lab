# U3 红队规则接入计划

状态：`PASS_WITH_LIMITATION / U3_COMPETITIVE_RED_TEAM_AND_HIGH_AWARD_GATE_READY`

## 1. 当前阶段

本步先生成文档级规则，不直接改 `evaluate_case_quality.py`。下一步为：

`U4_COMPETITIVE_RED_TEAM_EVALUATOR_INTEGRATION`

## 2. 适合自动扫描的规则

- S0 文本残留：候选稿、评测稿、占位、状态码、`citation_ready=false` 等；
- `final_submit/` 是否被创建；
- `official_submission_mode` 是否被错误开启；
- `human_gate_status` 是否被自动置为 approved；
- TeX / Markdown 中是否出现工程路径；
- TeX 中是否出现过大 `\includegraphics`；
- PDF 编译日志是否出现 Overfull hbox、missing figure；
- references 是否含 placeholder、待核验和伪造字段；
- AI 使用说明是否仍是模板却被标记完成。

## 3. 必须人工目检的规则

- 图表是否真正可读；
- 页码是否实际压图；
- 图表和解释是否错位；
- 论文是否有 AI 半成品味；
- 创新点是否真正可辩护；
- 机理解释是否可信；
- 参考文献是否适合真实比赛引用。

## 4. 需要 MATLAB / Python 输出支持的规则

- 边界附近邻域敏感性；
- Bootstrap / 蒙特卡洛扰动；
- 候选方案鲁棒排序；
- 预测区间或置信区间；
- 衰减率或稳定性指标；
- 受约束优化可行域验证。

## 5. 会阻断正式提交的规则

- S0 全部规则；
- references 伪造；
- AI 披露伪造；
- final_submit 自动创建；
- human gate 未通过；
- 预测结果写成真实结果；
- baseline / heuristic 写成全局最优。

## 6. 只阻断高奖候选标记的规则

- S1 全部规则；
- 缺少竞争点；
- 边界最优未解决；
- 不确定性量化不足；
- 时间序列 / 稳定性分析过浅；
- 机理启发不足。

## 7. 应进入后续交接的规则

- U4 是下一步；
- U3 当前是文档级规则；
- 2021B 暴露的“高下限、低上限”风险必须保留；
- submit candidate 不等于 final submit；
- 高奖候选必须有竞争点。

## 8. U4 最小接入建议

1. 增加红队规则路径登记；
2. 对论文 Markdown / TeX / PDF 文本做 S0 文本扫描；
3. 对 result summary 和论文结论做预测/真实结果边界扫描；
4. 对候选推荐点检查是否位于边界；
5. 对有时间字段的 case 触发稳定性检查；
6. 输出 `red_team_evaluator_report.md` 和机器可读 summary；
7. 保留人工目检字段，不把自动扫描写成最终批准。
