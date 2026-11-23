# active_competition 隔离检查表

更新时间：2026-06-16

状态：`PASS_WITH_LIMITATION / README_LOCALIZATION_FOR_SHOWCASE_READY`

本检查表用于确认 active_competition 当前赛题是否仍与历史压测 case 和机制 fixture 隔离。

| 检查项 | 要求状态 | 当前状态 |
|---|---|---|
| `cases/active_competition/` 是否存在 | 是 | 是 |
| `current_case/` 是否存在 | 是 | 是 |
| `data/raw/` 是否存在 | 是 | 是 |
| 当前是否已接入真实赛题 | 是 | 2017B “拍照赚钱”的任务定价 |
| 是否复制历史 case 数据 | 否 | 否 |
| 是否复制机制 fixture 数据 | 否 | 否 |
| 是否引用历史 case 结果 | 否 | 否 |
| 是否引用机制 fixture 结果 | 否 | 否 |
| 是否存在 `final_submit/` | 否 | 否 |
| 是否存在正式 PDF | 否 | 否 |
| 是否存在自动 gate 通过 | 否 | 否 |
| 是否自动写入人工批准标记 | 否 | 否 |
| `final_submit_allowed` | false | false |

## 后续展示前检查

1. 继续保持 `final_submit_allowed=false`。
2. 展示材料只能使用当前 case 的数据、代码、输出和验证报告。
3. 不得导入历史压测输出。
4. 不得引用机制 fixture。
5. 展示包必须标记为 `DRAFT_ONLY / NOT_FOR_FINAL_SUBMISSION`。
6. 所有 gate 在人工复核前保持未自动通过。
