# 无网络兜底方案

当前状态：`PASS_WITH_LIMITATION / COMPETITION_RED_TEAM_CONTINGENCY_READY`

本方案用于比赛期间网络不可用、在线检索不可用或外部文献无法访问时的本地化处理。

## 1. 赛前必须离线化的资产

1. 常用教材知识卡；
2. 常用模型方法说明；
3. 常见题型模型路线；
4. 已确认的参考文献；
5. 优秀论文样例；
6. 论文结构模板；
7. AI 使用披露模板；
8. PDF/Word/LaTeX 本地工具链。

## 2. 本地资产位置

优先读取：

1. `pipeline_core/literature_library/knowledge_cards/`
2. `pipeline_core/model_library/`
3. `pipeline_core/paper_quality/`
4. 当前 case 的 `literature/` 或 `evidence_map`，如存在；
5. `pipeline_core/templates/`

## 3. 禁止依赖临时联网搜索

正式比赛期间不得把关键方法依据建立在临时搜索上。若本地没有文献原文，不得编造作者、期刊、年份或 DOI。

## 4. 无网络时的参考文献处理

1. 使用本地已确认文献；
2. 对无法确认的文献列为待人工核验；
3. 不把检索关键词写成已引用文献；
4. 正式提交前必须人工补齐引用；
5. 若引用无法补齐，删除相关无支撑论断或改为方法局限。

## 5. citation gaps 处理

无网络时可以建立 citation gaps 清单，写明：

1. 需要支撑的模型或结论；
2. 缺失的文献类型；
3. 暂时使用的本地知识卡；
4. 正式提交前必须补齐的内容；
5. 是否阻断正式提交。

## 6. 结论

无网络不应阻断最小建模闭环，但会增加正式引用和优秀论文支撑风险。正式提交前，引用缺口必须人工处理。
