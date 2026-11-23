# 展示版转正式版阻断规则

状态：`PASS_WITH_LIMITATION / QUALITY_GATE_BLOCK_RESPONSE_READY`

生成日期：2026-06-17

## 1. 规则目的

展示版材料可以用于老师沟通、队员交流、训练和选拔展示，但不得直接升级为正式论文或正式提交材料。

展示版向正式版升级前，必须通过独立质量门和人工 gate。

## 2. 必须阻断的情况

出现以下任一情况，必须阻断正式化：

1. 展示材料仍含 `DRAFT_ONLY / NOT_FOR_FINAL_SUBMISSION`；
2. 展示材料存在边界措辞风险；
3. 图表标题、坐标轴或图例仍有未解释英文残留；
4. 表格缺少中文展示版或正式论文版；
5. AI 使用记录仍处于 `showcase_mode`；
6. 文献仍为 placeholder；
7. 优秀论文仍只作为 structure_sample；
8. human gate 未人工确认；
9. `final_submit_allowed=false`；
10. 模型结果仍为 baseline / dry-run / minimal loop only。

## 3. 边界措辞检查

禁止将展示版写成：

- 正式提交版本；
- 最终正式论文；
- 完整解决赛题；
- 全局最优方案；
- 自动写论文系统；
- 自动提交系统；
- 已完成未实际处理的问题；
- 预测完成率就是真实完成率。

## 4. baseline / heuristic 检查

以下表述必须保留限制：

- baseline 只是基准方案；
- heuristic 只是启发式方案；
- 预测概率、预测风险、预测完成率是模型输出，不是实际事实；
- 当前结果需要验证、敏感性分析和人工复核。

## 5. 正式版升级前条件

展示版升级为正式版前，至少需要：

1. 质量门不再返回阻断状态；
2. 文献引用已人工核验；
3. AI 使用披露模式切换到 `official_submission_mode`；
4. 图表、表格、公式、摘要、结论和附录均完成正式版审计；
5. human gate 明确人工确认；
6. 仍不得由程序自动创建 `final_submit/`。

## 6. 当前 2017B 适用结论

2017B 当前仍为展示/训练样例，不允许正式化。

原因：

- `evaluate_active_showcase()` 返回 `BLOCKED / SHOWCASE_QUALITY_GATE_FAILED`；
- human gate 仍为 `PENDING_MANUAL_REVIEW`；
- 文献仍未进入正式 citation；
- 当前结果只覆盖问题1/问题2阶段成果。
