# 题型路由风险登记

状态：`PASS_WITH_LIMITATION / PROBLEM_ROUTING_MODEL_LIBRARY_BASELINE_READY`

| 风险 | 风险等级 | 触发场景 | 处理要求 |
|---|---|---|---|
| 题型误判 | HIGH | 只凭题名、忽略数据结构和输出要求 | 必须结合题面、数据、约束和输出综合判断 |
| 混合题被单一路由 | HIGH | 多问递进题只给一个模型路线 | 必须拆分主題型和辅助题型 |
| 数据字段缺失仍强行建模 | HIGH | 核心字段无法映射 | 进入 `DATA_SCHEMA_INCOMPLETE` 或人工确认 |
| 约束不完整仍做优化 | HIGH | 目标或约束缺失 | 缺约束安全终止 |
| 启发式写成最优 | HIGH | 贪心、规则、nearest baseline 被写成最优 | 只能写 baseline 或候选方案 |
| 预测结果写成真实结果 | HIGH | 预测完成率或未来值被写成实际结果 | 必须写明预测口径 |
| 评价权重主观性未披露 | MEDIUM | 综合评分或 AHP/TOPSIS 权重未说明 | 补充权重来源和敏感性 |
| 图网络距离单位不明 | HIGH | 坐标距离或边权单位未确认 | 阻断正式结论，先做边权审计 |
| 机理拟合缺物理约束 | MEDIUM | 只有曲线拟合，无边界条件 | 标记 `PARTIAL_MODEL_ONLY` |
| 论文结构与题型不匹配 | MEDIUM | 优化题写成统计题或评价题写成预测题 | 按题型论文结构重排 |
| AI 辅助边界未区分模式 | HIGH | showcase 和 official disclosure 混用 | 按 `showcase_mode` 与 `official_submission_mode` 分流 |
| final_submit 被自动授权 | HIGH | 工具或报告写入授权 | 保持 `final_submit_allowed=false`，必须人工 gate |

