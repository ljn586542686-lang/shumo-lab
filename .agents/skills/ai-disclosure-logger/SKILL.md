---
name: ai-disclosure-logger
description: 数学建模 AI 使用披露记录 skill。用于记录 AI 工具名称、版本、使用环节、提示词摘要、输出摘要、采纳情况和人工修改情况，生成 ai_usage_log.json、key_prompts.md、adopted_outputs.md、human_modifications.md 与 AI工具使用详情.md。
---

# AI Disclosure Logger

## 定位

记录 AI 参与过程，服务合规披露和队员最终责任确认。

## 输入

- 当前 case 的阶段记录
- `human_review/`
- 关键提示词摘要
- 代码、计算输出以及文稿撰写和修改记录

## 输出

- `ai_disclosure/ai_usage_log.json`
- `ai_disclosure/key_prompts.md`
- `ai_disclosure/adopted_outputs.md`
- `ai_disclosure/human_modifications.md`
- `ai_disclosure/AI工具使用详情.md`

## 必记字段

- 工具名称与版本
- 使用日期和阶段
- 使用目的
- 输入材料摘要
- 输出内容摘要
- 采纳情况
- 人工复核与修改情况
- 对应证据文件

## 禁止事项

- 不隐藏 AI 使用。
- 不伪造人工修改。
- 不记录完整敏感题面之外的无关对话。
- 不把 AI 输出直接声明为人工原创推导。
