# AI 使用披露守卫规则

状态：`DRAFT_ONLY / NOT_FOR_OFFICIAL_SUBMISSION`

## 1. 目的

本规则用于防止 AI 使用说明被预填、伪造或误认为已完成正式披露。

## 2. 必须阻断的问题

以下情况出现时，不得进入正式提交：

1. 伪造 AI 工具名称；
2. 伪造模型版本；
3. 伪造开发机构；
4. 伪造使用日期；
5. 伪造关键交互记录；
6. 把 AI 使用模板当成已完成披露；
7. 缺少采纳内容、人工修改和人工复核记录；
8. official submission 模式下故意用“智能辅助工具”等泛称替代具体工具事实；
9. showcase 模式下不应暴露具体工具名却写入具体工具名。

## 3. 允许行为

- 生成 AI 使用详情模板；
- 保留待人工填写字段；
- 在候选稿中写“正式提交前按当年规则填写”；
- 按双模式规则分别处理校内展示和正式比赛披露。

## 4. 正式比赛披露模式需要字段

- AI 工具名称；
- 版本/型号；
- 开发机构/公司；
- 使用日期；
- 使用环节；
- 生成或辅助内容；
- 采纳内容；
- 人工修改情况；
- 人工复核方式；
- 队员独立完成核心建模与分析的说明；
- 当年竞赛规则核验记录。

## 5. 状态码建议

- `PASS_WITH_LIMITATION / AI_DISCLOSURE_TEMPLATE_READY_PENDING_MANUAL_FILL`
- `PASS_WITH_LIMITATION / AI_DISCLOSURE_READY_FOR_MANUAL_REVIEW`
- `BLOCKED / AI_DISCLOSURE_FAKE_OR_INCOMPLETE`
