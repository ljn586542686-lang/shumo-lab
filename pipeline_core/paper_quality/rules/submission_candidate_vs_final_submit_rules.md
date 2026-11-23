# Submit Candidate 与 Final Submit 分离规则

状态：`DRAFT_ONLY / NOT_FOR_OFFICIAL_SUBMISSION`

## 1. 基本定义

- `submit_candidate`：提交格式候选样稿，用于水平比对、版式训练、人工复核训练和初稿基底评估。
- `final_submit/`：正式提交包，只能在最终冻结流程中创建。

两者必须严格分离。

## 2. 必须阻断的情况

1. submit candidate 被自动复制或移动到 `final_submit/`；
2. `final_submit_allowed=false` 时创建 `final_submit/`；
3. human gate 未通过时创建 `final_submit/`；
4. `official_submission_mode` 未人工开启时创建正式提交包；
5. 参考文献未正式核验时生成正式 references；
6. AI 使用披露未完成时标记为可提交；
7. 旧题候选稿被标记为当前真实比赛终稿。

## 3. final_submit 创建前置条件

必须同时满足：

- 当前为真实正式比赛 case；
- `official_submission_mode` 已由人工确认开启；
- `human_gate_status=APPROVED`；
- `final_submit_allowed=true`；
- references 已人工核验；
- AI 使用披露已按当年规则填写；
- evidence map、citation audit、output contract 均通过；
- 最终 PDF 与最终 supporting-material package 已由独立 artifact inspection
  实际读取并检查通过；执行器自报 `PASS` 不得替代该检查；
- 用户明确授权冻结。

## 3.1 DAY1_SUBMISSION_READY 完成定义

`DAY1_SUBMISSION_READY_FREEZE` 只能在以下全部条件满足后使用：

- 论文正文完整；
- 完整、可运行的代码附录已纳入；
- supporting-material file list 已形成；
- supporting materials ZIP 已实际生成；
- run instructions 已纳入并可按其定位运行入口；
- 匿名性检查已完成；
- 版式检查已完成；
- 最终 PDF 和最终 supporting-material package 已由独立检查读取实际产物，
  完成越界、孤标题、大块无效空白、图表穿模、长路径/代码行溢出、身份信息、
  内部术语及文件清单与 ZIP 一致性检查。

仅“模型完成 + 论文正文完成”不得标记 `FINAL` 或 `SUBMISSION_READY`。

为保持现有状态体系最小变化，允许用以下等价顺序表示进度：

`TECHNICAL_FREEZE -> PAPER_FREEZE -> SUBMISSION_PACKAGE_FREEZE`

其中 `DAY1_SUBMISSION_READY_FREEZE` 只对应最后一个状态，不改变
`official_submission_mode`、`human_gate_status` 或 `final_submit/` 的人工控制规则。

## 4. 允许行为

- 生成 `submit_candidate_v*`；
- 为 submit candidate 生成 PDF；
- 为 submit candidate 生成人工复核表；
- 将 submit candidate 作为训练样本和初稿基底；
- 保持 `final_submit_allowed=false` 并阻断正式化。

## 5. 状态码建议

- `PASS_WITH_LIMITATION / SUBMISSION_CANDIDATE_READY_FINAL_SUBMIT_BLOCKED`
- `BLOCKED / FINAL_SUBMIT_CREATION_NOT_ALLOWED`
- `BLOCKED / SUBMISSION_CANDIDATE_MISUSED_AS_FINAL_SUBMIT`
