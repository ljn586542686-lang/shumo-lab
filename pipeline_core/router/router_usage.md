# 题型路由器使用说明

状态：`PASS_WITH_LIMITATION / ROUTER_ASSET_REUSE_KNOWLEDGE_CARD_PATCH_READY`

## 1. 基本命令

先执行 dry-run：

```bash
python pipeline_core/router/problem_type_router.py --case-dir cases/active_competition/current_case --dry-run
```

人工确认输出合理后，再写入 current_case 的 `routing/` 目录：

```bash
python pipeline_core/router/problem_type_router.py --case-dir cases/active_competition/current_case --write
```

## 2. dry-run 与 write 的区别

- `--dry-run`：只在终端打印 JSON，不写入 case 文件。
- `--write`：写入 `cases/active_competition/current_case/routing/` 下的路由结果和建议报告。

## 3. 路由器能做什么

- 读取题面抽取、数据审计、字段映射和模型库注册表。
- 根据关键词、字段结构、目标、约束和输出要求给出题型候选。
- 推荐 baseline 模型、advanced 模型、验证指标、敏感性检查和论文结构。
- 对 2017B 明确识别为混合型问题，并拆分问题1、问题2、问题3、问题4路线。
- 显式记录 `reused_assets` 和 `missing_assets`，避免已有模型库、论文质量层、AI 披露规则和知识卡资产被忽略。
- 输出 `knowledge_card_support`，说明知识卡主题如何支撑路线解释和验证设计。

## 4. 路由器不能做什么

- 不运行模型。
- 不训练预测模型。
- 不执行优化。
- 不生成论文。
- 不创建 `final_submit/`。
- 不自动通过任何 human_review gate。
- 不把路线建议写成最终模型选择。

## 5. 2017B 当前使用边界

当前 2017B 只完成问题1统计分析和问题2 baseline 定价优化的阶段性成果。题型路由器可以建议问题3任务打包和问题4新项目定价路线，但不得生成问题3/4结论。

## 6. 资产复用输出

执行 `--write` 后会额外生成：

- `routing/asset_reuse_note.md`
- `routing/knowledge_card_support_report.md`

如果 `pipeline_core/knowledge_cards/` 下的显式映射文件不存在，路由器会写入 `missing_assets`，并使用 `pipeline_core/literature_library/knowledge_cards/` 中已有的知识卡入口作为可用支撑，不会伪造缺失资产。
