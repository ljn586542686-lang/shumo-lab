# 参考文献正式化守卫规则

状态：`DRAFT_ONLY / NOT_FOR_OFFICIAL_SUBMISSION`

## 1. 目的

本规则用于防止 citation placeholder、文件名级文献资产或待核验条目被伪装成正式 references。

## 2. 当前 `citation_ready=false` 时必须阻断

当 `citation_ready=false` 或 `formal_reference_allowed=false` 时：

1. 不得生成正式 references；
2. 不得写“已核验”；
3. 不得伪造 DOI、页码、卷期、作者、期刊或出版社；
4. 不得把 placeholder 写成正式条目；
5. 不得把知识卡当作正式参考文献；
6. 不得把优秀论文 `structure_sample` 当作正式参考文献；
7. 同题旧题材料必须标记为旧题复盘风险；
8. 方法论文文件名级资产不得直接进入正式 references。

## 3. 允许行为

- 生成参考文献候选条目；
- 生成人工复核表；
- 在 submit candidate 中保留候选参考文献；
- 在正文中做少量候选引用标注；
- 保持 `citation_ready=false`、`formal_reference_allowed=false`，直到人工复核完成。

## 4. 正式化前人工核验字段

每条正式 references 至少需要人工核验：

- 作者；
- 题名；
- 文献类型；
- 来源；
- 年份；
- 卷期；
- 页码；
- DOI 或 URL；
- 正文引用位置；
- 是否为同题旧题材料；
- 是否允许进入正式 references；
- 复核人；
- 复核日期。

## 5. 阻断状态建议

- `BLOCKED / REFERENCE_FORMALIZATION_NOT_ALLOWED`
- `BLOCKED / FAKE_CITATION_RISK_FOUND`
- `PASS_WITH_LIMITATION / REFERENCE_CANDIDATES_READY_PENDING_REVIEW`
