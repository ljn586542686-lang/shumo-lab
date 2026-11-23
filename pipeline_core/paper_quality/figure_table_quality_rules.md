# 图表质量规则

状态：`PASS_WITH_LIMITATION / PAPER_QUALITY_LAYER_BASELINE_READY`

## 图像规则

1. 图表标题必须中文。
2. 坐标轴必须中文。
3. 图例必须中文。
4. 图编号规范，例如“图1”“图2”。
5. 图注说明数据来源、变量含义和口径。
6. 图像必须能追溯到 `output/figures/`。
7. 不得插入未生成或无法打开的图。
8. 正式图必须绑定已批准的 `result_card` 和人工通过的 Gate 3 证据。
9. `matlab_figure_spec`、MATLAB 脚本、执行 receipt 和图像必须分别记录 SHA-256，Track C 必须复核四者一致。
10. 图像、脚本和 receipt 只能位于当前 case 的 `output/figures/`，禁止绝对外部路径和路径穿越。
11. `first_discussion` 必须对应唯一正文小节，渲染器应在该小节首次讨论后插图。
12. PNG 必须检查尺寸、DPI、像素变化和透明度；PDF 必须先真实渲染，再进行空白页检查。
13. 正式论文采用 `STRONG_EVIDENCE_STORY`：每一问开场说明后、首组关键公式前，必须放置一张与题目对象直接对应的模型引导图；不允许用抽象总览图或通用流程图替代。
14. 模型引导图必须标出后续公式真实使用的至少两个对象或变量，绑定核心模型与首组公式，并由开场文字说明“为什么这样建模”和“图中哪一关系将进入推导”。
    模型引导图必须声明 `task_visual_family`，并按题型路由：`SPATIAL_GEOMETRY` / `NETWORK_STRUCTURE` 使用构型或拓扑；`DYNAMIC_PROCESS` 使用轨迹或状态图；`DATA_PATTERN` 使用散点、热力或等值关系；`OPTIMIZATION_DECISION` 使用可行域或目标地形；`SCHEDULING_RESOURCE` 使用 `interval_timeline` 或资源热力图；`STATISTICAL_UNCERTAINTY` 使用误差条或不确定性带；`EVALUATION_COMPARISON` 使用分组比较或指标热力图。`task_visual_family`、`explanatory_form` 与 `figure_type` 必须相容。
    `object_bindings` 必须能在图形实际数据中找到：构型图对应真实 `object_id`，调度图对应真实 `lane_id`，曲线/散点对应真实系列名或坐标轴变量，场图对应真实坐标轴或色标变量，比较图对应真实类别或系列；不得仅靠说明文字伪造绑定。
15. 正式解释图总数不少于 `task_count+1`；至少一个重点任务在模型引导图之外另有过程或诊断图。
16. 全篇必须覆盖三类视觉角色：`MODEL_STRUCTURE`（模型结构/几何机理）、`INTERMEDIATE_PROCESS`（候选/状态/迭代过程）和 `RESULT_DIAGNOSTIC`（结果比较/误差诊断）。
17. 每张解释图必须绑定 `task_id`、`reasoning_chain_id`、`reasoning_role`、`reader_question`、`public_takeaway` 和 `logic_links`；其中 `logic_links` 至少跨越模型/公式、中间结果、最终结果三层中的两层。
18. 正文必须出现每张图的 `public_takeaway`，并说明模型如何产生中间状态、中间状态如何支持最终结果；仅展示最终数字、装饰性流程图或与正文推导无关的图片不得通过。

## 表格规则

1. 论文表头必须中文。
2. 展示版表格使用 `output/tables_showcase/` 或展示 Excel。
3. `output/tables/` 原始机器 CSV 可以保留英文字段。
4. 论文草稿不得直接插入未翻译机器表头。
5. 表格编号规范，例如“表1”“表2”。
6. 表注说明字段含义、单位和来源。

## 风险判定

| 情况 | 风险 |
|---|---|
| 图表不存在或无法追溯 | HIGH |
| 图表英文且用于展示或论文 | MEDIUM |
| 表格机器字段直接进论文 | MEDIUM |
| 图注缺来源 | MEDIUM |
| 任一题目缺少模型→中间状态→最终结果的解释性图 | HIGH / BLOCK |
| 图存在但未绑定公式、中间证据或最终结果 | HIGH / BLOCK |
| 任一问在首组公式前没有题目对象关系图，或用抽象总览/通用流程图代替 | HIGH / BLOCK |
| 正式解释图少于 `task_count+1` 或缺少任一视觉角色族 | HIGH / BLOCK |
| 没有重点任务双图，或图没有明确回答读者问题 | HIGH / BLOCK |
