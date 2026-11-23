# U3 S0 正式提交阻断规则

状态：`PASS_WITH_LIMITATION / U3_COMPETITIVE_RED_TEAM_AND_HIGH_AWARD_GATE_READY`

## 1. S0 定义

S0 是正式提交阻断项。命中后禁止生成正式提交版，只允许保留 `DRAFT_ONLY` 或 `SUBMISSION_CANDIDATE`，并生成整改清单。

命中状态：

`BLOCKED / FORMAL_SUBMISSION_S0_RED_TEAM_BLOCKED`

## 2. 文本残留阻断

正式论文正文、首页、脚注、附录、参考文献区、AI 使用说明区不得残留：

- 候选稿；
- 评测稿；
- 用于水平比对；
- 人工复核训练；
- 待补全；
- 占位；
- `citation_ready=false`；
- `formal_reference_allowed=false`；
- `final_submit_allowed=false`；
- `human_gate`；
- `final_submit`；
- `official_submission_mode`；
- `SUBMISSION_CANDIDATE`；
- `HUMAN_AUTHORIZATION_REQUIRED`；
- `AI_DISCLOSURE_REQUIRED`；
- `REFERENCE_MANUAL_REVIEW_REQUIRED`；
- `NOT_CURRENT_CONTEST_SUBMISSION`。

## 3. 引用与 AI 阻断

- 参考文献 placeholder 被写成正式 references；
- AI 使用说明模板被写成已完成披露；
- 伪造 AI 工具名称、版本、日期或关键交互记录；
- 未核验文献被写成已核验。

## 4. 版式与证据阻断

- 未完成说明、工程状态说明、内部路径说明进入正式论文主体；
- 图表错位、页码压图、编号错乱、表格断裂；
- 结论与数据或模型输出冲突；
- 预测结果被写成实验真实结果；
- baseline、heuristic、候选排序被写成全局最优。

## 5. 流程阻断

- human gate 未人工通过却进入正式导出；
- `final_submit/` 被自动创建；
- `official_submission_mode` 未人工开启却输出正式提交材料。

## 6. 允许输出

S0 命中后只允许输出：

- 阻断报告；
- 整改清单；
- 人工复核清单；
- submit candidate 或 draft；
- 不允许 final submit。
