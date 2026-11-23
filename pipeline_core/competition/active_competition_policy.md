# active_competition 隔离策略

更新时间：2026-06-16

状态：`PASS_WITH_LIMITATION / README_LOCALIZATION_FOR_SHOWCASE_READY`

## 1. 用途

`cases/active_competition/` 是当前正式赛题或模拟赛赛题的隔离工作区。该目录必须与历史压测 case 和机制 fixture 分离，避免把回归测试结果误写入当前赛题。

## 2. 当前状态

当前 `current_case/` 已接入 2017 年 CUMCM B 题“拍照赚钱”的任务定价。

当前成果只覆盖问题1统计分析 baseline 和问题2 baseline 定价方案，已完成图表中文化、表格中文展示版导出和展示层基础验收。当前尚未生成完整展示包、正式论文、正式 PDF 或 `final_submit/`。

## 3. 隔离规则

1. `active_competition` 只能包含当前赛题材料。
2. 不得把 `case_03` 至 `case_11` 的数据、结果、草稿、验证报告或输出表作为当前赛题证据。
3. 不得引用 `case_06`、`case_07`、`case_08` 机制 fixture。
4. 不得把历史压测结果写入当前赛题论文或展示材料。
5. 不得把历史 raw 数据、模型输出、论文草稿或提交材料复制进 active 工作区。
6. 当前赛题 raw 数据必须放在 `cases/active_competition/current_case/data/raw/`。

## 4. final_submit 规则

当前阶段不得创建 `final_submit/`。

只有在以下条件全部满足后，才可进入后续最终提交冻结流程：

1. 当前 case 输出契约验证通过。
2. 当前 case 证据链审计通过。
3. 如涉及引用或理论审查，相应检查通过。
4. AI 使用披露完整。
5. `human_review/gate_4_final_submission.md` 已由人工明确确认。

## 5. 人工 gate 规则

human_review gate 必须人工确认。自动化工具不得写入人工批准标记，不得写入 `APPROVED` 或等效批准状态。

## 6. PDF 规则

当前展示前阶段不得生成正式 PDF。后续如生成展示 PDF 或 PPT，必须明确为展示材料，不得标记为正式提交材料。

## 7. 机制 fixture 规则

机制 fixture 只用于安全机制回归测试。其数据、输出和报告不得作为当前正式赛题证据。
