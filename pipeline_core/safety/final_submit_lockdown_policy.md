# final_submit 锁定策略

当前状态：`PASS_WITH_LIMITATION / COMPETITION_RED_TEAM_CONTINGENCY_READY`

本策略强化正式提交前的锁定规则。`final_submit/` 是人工授权后的提交准备目录，不是任何自动流程的默认输出。

## 1. 默认禁止

1. 默认不得创建 `final_submit/`。
2. 任何脚本不得自动创建 `final_submit/`。
3. runner 不得创建 `final_submit/`。
4. freeze 不等于 `final_submit` 授权。
5. `official_submission_mode` 通过不等于 `final_submit` 授权。
6. AI 辅助流程不得自动通过 human gate。

## 2. 允许进入人工准备的条件

只有同时满足以下条件，才允许人工准备 `final_submit/`：

1. 队长明确授权；
2. gate_4 已人工确认；
3. 正式论文 PDF 可打开；
4. 提交表格完整；
5. 数据检查通过；
6. 模型检查通过；
7. 论文质量检查通过；
8. 引用完整性检查通过；
9. AI 使用披露检查通过；
10. 队员人工终审完成。

## 3. 阻断条件

出现任一情况，必须阻断：

1. `final_submit/` 未授权出现；
2. 正式论文仍包含 `DRAFT_ONLY / NOT_FOR_FINAL_SUBMISSION`；
3. AI 使用披露缺失或模式错误；
4. 引用缺失或伪造；
5. 关键结论无证据链；
6. baseline 被写成全局最优；
7. human gate 未人工确认；
8. 当前赛题引用历史 case 或机制 fixture。

## 4. 应急处理

若误创建 `final_submit/`：

1. 立即停止自动流程；
2. 记录出现时间和来源；
3. 人工检查其中是否有正式提交材料；
4. 未授权材料不得提交；
5. 修复后重新执行提交前检查。

## 5. 结论

`final_submit/` 是最后人工动作，不是生产线自动功能。
