# 论文草稿人工复核 gate 模板

状态：`PENDING_MANUAL_REVIEW`

> 注意：本模板不得自动写入 `MANUAL_APPROVAL_BY_USER`，不得自动通过。

## 1. 审题复核

- [ ] 题面每一问均已对应。
- [ ] 未覆盖问题已说明。

## 2. 数据复核

- [ ] raw、processed、schema 证据完整。
- [ ] 缺失值和异常值处理已披露。

## 3. 模型复核

- [ ] 模型与题型匹配。
- [ ] baseline 与 advanced model 已区分。
- [ ] 未把启发式写成全局最优。

## 4. 代码结果复核

- [ ] 关键数值来自代码输出。
- [ ] `result_summary` 与表格一致。

## 5. 图表复核

- [ ] 图表中文、编号、图注完整。
- [ ] 图表能追溯到输出文件。

## 6. 公式符号复核

- [ ] 变量定义完整。
- [ ] 单位和参数来源清楚。

## 7. 引用复核

- [ ] 未伪造文献。
- [ ] 引用与正文内容相关。
- [ ] 待检索文献未写成已引用文献。

## 8. AI 使用披露复核

- [ ] 已选择 `showcase_mode` 或 `official_submission_mode`。
- [ ] 披露方式符合当前模式。

## 9. 结论边界复核

- [ ] 未写正式提交版本。
- [ ] 未写完整解决全部问题。
- [ ] 未写无证据结论。

## 10. final_submit 禁止确认

- [ ] 当前未创建 `final_submit/`。
- [ ] 当前 `final_submit_allowed=false`。

## 11. 人工签名占位

复核人：

日期：

结论：`PENDING_MANUAL_REVIEW`

