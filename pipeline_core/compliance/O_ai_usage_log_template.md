# O 步 AI 使用记录表模板

状态：`PASS_WITH_LIMITATION / AI_USAGE_LOG_TEMPLATE_READY`

本模板用于正式比赛披露前的人工记录，不预填任何工具名称、模型版本或官方规则来源。

| 字段 | 说明 | 是否必填 |
|---|---|---|
| `date_time` | 使用日期和时间 | 是 |
| `tool_name` | AI 工具名称 | 正式披露时必填 |
| `provider` | 服务提供方；未知时填 `UNKNOWN` | 是 |
| `model_or_version` | 模型或版本 | 正式披露时必填 |
| `prompt_summary` | 提示词摘要，不记录完整敏感对话 | 是 |
| `used_for` | 使用环节 | 是 |
| `output_type` | 输出类型，如代码、文字、检查建议 | 是 |
| `accepted_or_rejected` | 是否采纳 | 是 |
| `human_editor` | 人工编辑或复核人 | 是 |
| `human_review_status` | 人工复核状态 | 是 |
| `risk_note` | 风险说明 | 是 |
| `rule_compliance_checked` | 是否按官方规则检查 | 正式披露前必填 |
| `official_rule_source` | 官方规则来源文件或链接 | 正式披露前必填 |
| `official_rule_checked_by` | 官方规则核验人 | 正式披露前必填 |
| `official_rule_checked_at` | 官方规则核验日期 | 正式披露前必填 |

当前状态：`official_rules_current_verified=false`，因此本表不能作为正式披露记录直接提交。

## 元数据记录边界

每次外部高端模型或 AI 调用至少实时记录 `tool_name`、`model_or_version`、
`provider`、`date_time` 和 `used_for`。未知字段必须保持 `UNKNOWN` 或 `PENDING`，
不得根据上下文猜测、事后补写为已确认事实；后续人工核验应保留原始未知状态及核验结果。
