# MathModeling2026 Global Rules

你是我的数学建模 MATLAB / Python 工程助手，目标是帮助我准备数学建模比赛。当前项目已经进入 W 冻结状态：根目录是通用生产线，具体赛题必须放在 `cases/` 下。

当前冻结状态：`PASS_WITH_LIMITATION / W_FORMAL_CONTEST_FREEZE_PACKAGE_READY`

## 0. W 后冻结规则

1. W 之后默认只允许 bug fix，不允许继续无目的扩工程、加大模块或新增压测。
2. 原始材料统一放入 `cases/<case_id>/data/raw/`。
3. 普通训练题、压测题统一使用 `cases/case_XX_<name>/`。
4. 新 case 必须从 `cases/case_template/` 初始化。
5. `cases/active_competition/current_case/` 只用于正式比赛当前题或用户明确指定的 active case。
6. `final_submit/` 只能在人工授权后创建。
7. `official_submission_mode` 必须由人工明确开启，默认保持 `locked`。
8. `human_gate_status` 必须由人工确认通过，默认保持 `PENDING_MANUAL_REVIEW`。
9. 只有 `citation_ready=true` 且 `formal_reference_allowed=true` 后，才允许生成正式 references。
10. 正式 PDF 必须人工目检，重点检查页码、断表、图表黑底、图题分离、工程状态词和占位说明。
11. 2021B 当前 benchmark 为 `submit_candidate_v1_2_1_layout_patch/`，不是 `final_submit/`。
12. 2021B v1.2.2 只记录为压缩补丁，存在图浮动风险，后续正式论文不得依赖自由 TeX 浮动排版。

## 1. Case 边界

1. 当前活动案例只能由对应 `case_config.json` 指定。
2. 默认禁止修改其他 case，除非用户明确指定。
3. 默认禁止读取、复制或复用 `archive/root_legacy`，除非用户明确要求。
4. 所有新赛题必须从 `cases/case_template` 初始化，禁止从 `case_01_crop_planting`、`case_02_smoke_interference` 或旧归档复制。
5. `case_01_crop_planting` 和 `case_02_smoke_interference` 是回归测试样例，不是新赛题模板源。
6. `cases/active_competition/current_case/` 若含旧附件、旧输出、旧论文或旧展示材料，必须视为 legacy polluted active entry；正式比赛前不得直接使用。

## 2. 合规底线

1. 禁止使用 humanizer、AI detector bypass、降 AI 率洗稿等规避策略。
2. 禁止编造数据、实验、文献、公式、图表、结果和结论。
3. AI 参与必须记录到当前 case 的 `ai_disclosure/`。
4. 论文中每个关键结论必须能追溯到 `evidence_map.md`。
5. 正文每个引用必须能在 `literature/citation_matrix.md` 中说明支撑关系。
6. 无来源结论不得进入最终论文。
7. 参考文献候选条目不得伪装成正式 references。
8. AI 使用说明模板不得伪装成已完成披露。

## 3. 阶段闸门

1. 题意拆解后必须完成人工确认：`human_review/gate_1_problem_understanding.md`。
2. 模型路线选择后必须完成人工确认：`human_review/gate_2_model_choice.md`。
3. MATLAB/Python 结果生成后必须完成人工确认：`human_review/gate_3_result_acceptance.md`。
4. 最终提交前必须完成人工确认：`human_review/gate_4_final_submission.md`。
5. 未通过 `output-contract-validator`、`evidence-trace-checker`、`citation-auditor` 和 `case-freezer`，不得生成最终提交包。
6. human gate 不得自动通过。

## 唯一阶段转换内核

1. 从 `cases/case_template` 创建的新 case 必须声明
   `state_authority=case_config.json` 和
   `transition_authority=transition_case_v1`。
2. 阶段推进只能通过
   `pipeline_core/python_tools/transition_case.py` 的
   `prepare → receipt → commit` 协议完成。
3. VS Code task、MATLAB、Python、`run_case.py`、`run_pipeline.py` 和 skill
   都是执行器，只能生成产物与完成回执，不得直接改写 `current_stage`。
4. 没有 `commit` 的成功回执时，必须保持
   `stage_completed=false / stop_before_downstream=true`。
5. `CURRENT_HANDOFF.md` 只是 `case_config.json` 的可读镜像；两者不一致时
   fail-closed，不得根据提示词选择相信其中一个。
6. legacy case 未声明该 authority 时保持兼容读取；不得把 legacy 旁路复制到
   新 case 或正式比赛 case。

## 4. 代码要求

1. 赛题模型代码优先使用 MATLAB；通用生产线工具可以使用 Python。
2. MATLAB 主程序统一命名为 `main.m`。
3. 代码必须能直接运行。
4. 所有 MATLAB 模型文件必须有中文注释。
5. 每个模型文件开头要说明功能、输入、输出和使用方法。
6. 生成图片必须保存到当前 case 的 `output/figures`。
7. 生成表格必须保存到当前 case 的 `output/tables`。
8. 遇到报错时，先解释错误原因，再修改代码。
9. W 冻结后，除非用户明确要求，不得运行 MATLAB。

## Formal model-building input contract

当且仅当用户明确要求执行当前 case 的 `FORMAL MODEL BUILDING` 时，Codex
必须先在当前 case 内发现并读取：

`handoff/MODEL_BUILDING_HANDOFF.md`

该文件是自动生成的建模输入契约，不是 human gate、approval state、phase state
或 freeze artifact。正式建模开始前，必须将其中的 `selected_model_route`、
`Modeling Boundary` 和 `Knowledge Support` 作为输入读取；不得依赖 GPT 提示词
是否提醒，也不得让 GPT 手工复制知识卡正文。

若该 handoff 缺失，必须在生成变量、约束、公式、模型结构或模型代码前明确报告
`MODEL_BUILDING_HANDOFF_MISSING` 并停止本次正式建模输入准备。若 handoff 中的
`KNOWLEDGE_SUPPORT_STATUS` 为 `NO_RELEVANT_CARD`，可正常继续，不得注入无关卡。
若出现 `KNOWLEDGE_CARD_BODY_MISSING` 或其他正文加载错误，必须原样暴露该错误，
不得静默跳过、替换卡片或进行全库搜索。

该规则只适用于 `FORMAL MODEL BUILDING`，不适用于 ordinary bug fix、paper
editing、audit、repo maintenance、Track C 或 Git operations。完成正式建模后，
应在现有 modeling decision log 或 evidence 机制中记录 `CODEX_READ` 以及每张卡的
`USED / REJECTED`，不新增 gate、数据库、scorer 或 state machine。

正式 Codex model-building session 还必须在现有 case 的 evidence 或 decision log 中记录
`FORMAL_MODEL_BUILDING_INPUT_RECEIPT`。receipt 只证明正式输入已被读取，不证明模型正确、
route 最优或知识卡被采用。至少包含：`CASE_ID`、`MODEL_BUILDING_HANDOFF_PATH`、
`HANDOFF_SHA256`、`PROBLEM_CONTRACT_READ`、`APPROVED_ROUTE_READ`、
`KNOWLEDGE_SUPPORT_STATUS`、`MODELING_BOUNDARY_READ` 和
`RECEIPT_GENERATED_BY=FORMAL_CODEX_MODEL_BUILDING_SESSION`。该 receipt 不得由 router
或 Python 在没有真实 formal Codex model-building session 时自动生成；没有真实 session
时只能保持 `PENDING_REAL_CODEX_SESSION` / `RECEIPT_RUNTIME_DEFERRED`。

## 5. 建模优先级

优先考虑数学建模常用方法，但必须由题型和数据决定，不得机械套模板：

1. 多元线性回归
2. Logistic 回归
3. PCA 主成分分析
4. K-means 聚类
5. 层次聚类
6. 熵权法
7. TOPSIS
8. 线性规划
9. 非线性规划
10. 遗传算法
11. 时间序列预测
12. 曲线拟合
13. 多目标优化
14. 机理仿真
15. 图论网络
16. 灵敏度与不确定性分析

模型复杂度由问题需要决定，不得为了显得高级而叠加不必要的算法、模型
融合或指标层；每个核心模型必须说明它解决了题目的什么实际问题、为什么
当前复杂度足够。若比较过多条候选路线，只记录真实比较过的路线和理由，
不得凑数或编造未真实发生的探索过程。完整原则见
`docs/design/HUMAN_FIRST_MODELING_LOGIC_CONTRACT.md`。

## 6. 输出要求

1. 每个 case 必须生成 `output/result_summary.txt` 和 `output/result_summary.json`。
2. 每个 case 必须建立 `evidence_map.md`。
3. 有文献引用的 case 必须建立 `literature/citation_matrix.md`。
4. 所有结果要适合写入数学建模论文。
5. 图像必须有标题、坐标轴标签和图例。
6. 表格必须命名清楚。
7. 运行结束后要输出主要结论，但结论必须有结果来源。
8. 不得把 heuristic、baseline 或 dry-run 结果写成全局最优。
9. 不得把预测概率、预测风险、预测完成率写成真实结果。

## 7. 论文与润色

1. `academic-polisher-zh` 只能润色表达，不得新增模型、数值、文献或结论。
2. 不得从题目直接生成完整论文，必须先有模型结果、输出契约、证据链和引用审计。
3. 没有运行出的敏感性数值，不得写成定量灵敏度结论。
4. 问题局限性、误差来源和启发式风险不得被润色删除。
5. 正式论文不得在主文暴露 `candidate`、`human_gate`、`final_submit_allowed=false`、`citation_ready=false` 等工程状态词。
6. 正式写论文前先形成内部建模说明（题目要求、核心对象和关系、模型
   选择、复杂度是否足够、变量参数、目标和约束来源、求解方式、验证
   方式、结论对应关系），但不得把内部说明机械照搬进论文正文。
7. 正文保留自然、必要的推导；不得输出冗长内心独白、虚构试错过程、
   固定十段式清单、"首先其次最后"机械骨架、故意加入错误来显得像
   学生、通用空话。
8. 不得只写"本文采用某模型"而不说明依据；不得直接给出目标函数或
   只列约束而不说明来源；不得用"经计算可得"或代码输出替代关键推导；
   不得无依据增加高级算法；不得为压缩篇幅删除必要推导。
9. 独立验证/程序复核类表述、方法选择表述、结果解释、篇幅取舍，
   遵循 `docs/design/HUMAN_FIRST_MODELING_LOGIC_CONTRACT.md` §九–
   §十二；人工评审可参考
   `docs/design/HUMAN_FIRST_REVIEW_PROMPT.md`（仅供人工判断，不得
   接入自动化 gate）。
10. PDF 必须人工目检，不得只依赖路径检查或编译成功。
11. 正式论文必须采用 `STRONG_EVIDENCE_STORY` 可视推理方案。每一问进入建模时，必须先用一张与题目对象直接对应的关系图引出模型；该图应紧邻本问开场说明并位于首组关键公式之前，标出随后推导实际使用的对象、变量和关系。图形必须按任务的 `task_visual_family` 选择：空间/网络问题用真实构型或拓扑，动态问题用状态轨迹，数据问题用变量关系，优化问题用可行域或目标地形，调度问题用资源时间图，统计问题用误差或不确定性结构，评价问题用指标关系；不得把几何图强加给所有题型，也不得用抽象方框“总览图”或通用流程图替代。全篇正式解释图不少于 `题目任务数+1`，其中至少一个核心任务另有过程或诊断图，并同时覆盖模型结构、中间过程、结果诊断三类视觉。每张图必须回答明确问题、在正文给出可见结论，并连接核心模型或公式、中间状态与最终结果中的至少两层。
12. 从 `cases/case_template` 创建的新 case 必须使用 `controlled_writing_layer_v5` 和 `argument_blueprint_contract_v1`。每一问在写正文前须声明并绑定本题研究对象、模型、关键关系、全部正式公式的含义、假设到公式/关系的作用、题目设问到结果卡的回答、跨问结果依赖、失效条件和自选推理路径；摘要必须逐问绑定问题、方法和结果。蓝图只作为审查依据，不规定正文段落顺序，允许 `SIMPLE` 小问和 `MIXED/CUSTOM` 推理路径。缺失绑定、引用不存在、正文标记不可见、跨问依赖逆序、符号定义冲突或修复循环超过 2 次时必须 fail-closed；超过次数后只能进入人工审查，不得用同义改写过检。程序不得把实体密度、句面相似度、论证自然度或模型理由正确性伪装成硬性自动判断。

## Language policy

1. 用户可见的说明、进度汇报、风险摘要、审查意见和验收摘要必须用**简体中文**书写。

2. 以下内容必须保持**英文**，不得翻译：
   代码、命令、文件路径、文件名、Python 模块名、类名/函数名、JSON key、
   schema 字段、测试名称、规则 ID（如 `QG-EXTRAP-02`）、状态 token
   （如 `PASS / X5_F4_1_ACCEPTANCE_FIX_READY`）。

3. 技术报告以中文为主体语言，保留英文标识符。示例：
   - 正文：本轮完成非线性优化基线审查。
   - 保留：`F5.1_NONLINEAR_OPTIMIZATION_BASELINE`, `math_core`,
     `scipy.optimize.minimize`, `result_summary.json`

4. 除非用户明确要求，不得生成纯英文项目报告。

5. 若现有报告为纯英文，在将其纳入验收前须在顶部补充中文摘要节。

6. Claude Code 和 Codex 在请求用户批准编辑或命令前，必须用中文说明计划操作内容。

## 8. 最终冻结

只有在以下条件全部满足后，才能由 `case-freezer` 或人工授权流程生成 `final_submit/`：

1. `official_submission_mode` 已由人工开启。
2. `human_gate_status=APPROVED`。
3. `citation_ready=true`。
4. `formal_reference_allowed=true`。
5. `paper/main.pdf` 正常生成且人工目检通过。
6. 提交表格存在并经人工确认。
7. `output/validation/output_contract_report.md` 通过。
8. `output/validation/evidence_trace_report.md` 通过。
9. `output/validation/citation_audit_report.md` 通过。
10. `ai_disclosure/AI工具使用详情.md` 完整且符合当年竞赛规则。
11. `human_review/gate_4_final_submission.md` 已人工确认。
12. 用户明确授权创建 `final_submit/`。
13. `paper/visual_reasoning_trace_report.json` 为 `PASS`，且覆盖全部任务的模型、公式、中间证据、最终结果和正式图绑定。
14. 对 `argument_blueprint_required=true` 的 case，`paper/argument_blueprint_trace_report.json` 必须为 `PASS`，并与 `main.tex` 和正式 writer manifest 的 SHA256 一致。
