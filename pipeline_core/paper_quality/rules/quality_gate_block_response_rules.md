# 质量门阻断响应规则

状态：`PASS_WITH_LIMITATION / QUALITY_GATE_BLOCK_RESPONSE_READY`

生成日期：2026-06-17

## 1. 规则目的

当任一质量门返回 `BLOCKED`、`FAILED`、`HIGH_RISK` 或等价状态时，生产线必须进入阻断响应流程，不得继续正式化。

该规则适用于：

- 展示层质量门；
- 论文质量门；
- 文献引用质量门；
- 输出契约质量门；
- 证据链质量门；
- AI 使用披露质量门；
- human gate。

## 2. 阻断后必须停止的行为

一旦质量门阻断，必须停止：

1. 将文稿排版为正式交付版本。
2. 正式 references 生成；
3. `final_submit/` 创建；
4. 自动通过 human gate；
5. 将展示版升级为正式版；
6. 将 placeholder citation 写成正式 citation；
7. 将 structure_sample 写成 method citation。

## 3. 阻断后允许的行为

质量门阻断后允许：

1. 生成整改任务清单；
2. 生成风险说明；
3. 继续作为训练样例或展示样例，但必须带 limitation；
4. 只读复核现有证据链；
5. 在人工明确要求后，针对阻断项做局部修复。

## 4. 通用阻断响应字段

每次阻断响应必须记录：

- `block_status`
- `block_reason`
- `risk_level`
- `affected_scope`
- `training_showcase_allowed`
- `formalization_allowed`
- `manual_review_required`
- `final_submit_allowed`
- `remediation_tasks`

## 5. 当前默认判定

| 情况 | 风险等级 | formalization_allowed |
|---|---|---|
| 边界措辞越界 | HIGH | false |
| 图表语言残留 | MEDIUM | false，直到修复并复核 |
| 预测结果写成真实结果 | HIGH | false |
| baseline / heuristic 写成全局最优 | HIGH | false |
| 展示版被当成正式版 | HIGH | false |
| citation placeholder 写成正式 citation | HIGH | false |
| human gate 自动通过 | HIGH | false |

## 6. human gate 规则

质量门阻断后：

- `human_gate_status` 必须保持 `PENDING_MANUAL_REVIEW`；
- 不得写入 `APPROVED`；
- 不得写入 `MANUAL_APPROVAL_BY_USER`；
- 不得自动创建 gate_4；
- 不得自动授权 `final_submit/`。

## 7. 与 M 步执行器的关系

模型路线执行器 baseline 必须读取质量门阻断状态。如果存在未处理阻断项，执行器只能生成 dry-run 计划和整改计划，不得正式执行建模、论文或提交流程。
