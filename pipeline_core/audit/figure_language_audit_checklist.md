# 图表中文化审计清单

状态：`PASS_WITH_LIMITATION / FIGURE_LANGUAGE_AUDIT_CHECKLIST_READY`

## 1. 语言检查

| 检查项 | 要求 |
|---|---|
| 标题 | 中文，不能保留无解释英文 |
| 横轴 | 中文，必要时带单位 |
| 纵轴 | 中文，必要时带单位 |
| 图例 | 中文 |
| 图注 | 中文，说明口径 |
| 英文缩写 | 仅保留 AUC、CSV、JSON、baseline、DRAFT_ONLY 等必要缩写 |

## 2. 内容检查

| 检查项 | 要求 |
|---|---|
| 图表数值 | 不得修改模型结果 |
| 图表结论 | 不得新增无证据结论 |
| 预测类图表 | 必须说明预测不等于真实 |
| baseline 图表 | 必须说明不是全局最优 |
| 异常数据 | 必须披露处理口径 |

## 3. 安全检查

1. 不创建 `final_submit/`。
2. 不生成正式 PDF。
3. 不自动通过 human_review gate。
4. 不写入人工批准标记。
5. 不读取或重跑历史 case。
6. 不覆盖历史 case 图表。

## 4. 审计结论等级

1. `PASS_WITH_LIMITATION / FIGURE_LOCALIZATION_FOR_SHOWCASE_READY`：图表中文化完成，可进入展示包准备。
2. `PASS_WITH_LIMITATION / FIGURE_LOCALIZATION_FONT_LIMITATION`：图表已尽量中文化，但字体环境存在风险。
3. `BLOCKED / FIGURE_LOCALIZATION_INCOMPLETE`：仍大量存在英文标题、英文坐标轴或英文图例。
