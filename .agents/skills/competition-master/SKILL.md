---
name: competition-master
description: 数学建模竞赛流程调度 skill。用于读取当前 case 的机器状态、人工 Gate 和证据产物，选择下一项已接线的专项工作；不替代专项 skill、人工 Gate 或本地执行器。
---

# Competition Master

## 职责边界

本 skill 只负责判断当前阶段、核对进入条件并指出下一执行入口，不直接替代专项
skill、MATLAB/Python 执行器、人工 Gate 或独立红队。

- GPT 负责审查与裁决。
- 本地执行器负责代码、运行、证据和 review packet。
- 独立红队只审查，不修改仓库、不授权阶段推进。
- `case_config.json` 是机器状态事实源。
- case 声明 `state_authority=case_config.json` 时，`CURRENT_HANDOFF.md` 只能是
  `STATUS_MIRROR_CASE_CONFIG` 的可读镜像；不一致时停止。
- 不调用当前仓库不存在的程序、skill 或阶段。

## 四个人工 Gate

1. Gate 1：题意与问题合同。
2. Gate 2：模型路线选择。
3. Gate 3：结果验收。
4. Gate 4：最终提交。

任何 Gate 都不得由本 skill 自动勾选、补写确认人或伪造通过状态。

## 当前生产链

```text
case intake → problem contract → Gate 1
→ route generation/review → Gate 2
→ MODEL_BUILDING_HANDOFF → formal model/solver
→ result + validation + evidence → Gate 3
→ Track A 技术可信收口
→ literature status closure
→ Track B 竞赛适配审查
→ TRACK_B_TO_TRACK_C authorization
→ MATLAB figure receipt → Track C → renderer → XeLaTeX
→ citation audit + AI disclosure + post-writer reentry
→ Gate 4 → case-freezer
```

Track A 是 correctness、provenance、claim boundary、writer protection 和
post-writer reentry 的组合契约，不得仅凭一句“Track A 已运行”判为完成。

## 调度步骤

每次调度严格按以下顺序执行：

1. 从用户指定 case 或 `case_config.json` 确认唯一 case 边界。
2. 读取 `case_config.json`、case-local `CURRENT_HANDOFF.md`、对应 Gate 文件和上一阶段回执。
3. 若机器状态与可读镜像不一致，输出实际错误并停止。
4. 核对上一阶段产物、哈希、Gate 和阻断项；不得用提示词补齐缺失证据。
5. 只选择一个当前合法的下一阶段。
6. 先由唯一内核生成 transition authorization，再把授权路径交给执行器。
7. 若该阶段没有程序执行器，返回
   `AWAITING_EXECUTOR / stage_completed=false / stop_before_downstream=true`；
   不得声称已完成。
8. 执行器完成后，由唯一内核生成 completion receipt 并 commit；只有 commit PASS
   才能宣布阶段完成。

## 唯一状态转换协议

声明 `state_authority=case_config.json` 的 case 必须使用：

```text
transition_case.py prepare
→ VS Code / MATLAB / Python / skill executor
→ transition_case.py receipt
→ transition_case.py commit
```

- `prepare` 从 `case_config.json` 读取当前阶段，拒绝提示词或 CLI 自报的伪阶段。
- authorization 绑定 case、当前阶段、状态版本、Gate、config/handoff SHA256 和执行器。
- authorization 的 executor 必须写真实入口标识，例如公开模型入口使用
  `run_pipeline:model`；不得用模糊的“agent”代替实际执行入口。
- executor 只能生成产物，不能修改 `current_stage`。
- receipt 必须绑定实际产物 SHA256 和执行状态。
- `commit` 重新核验 Gate、状态版本、授权和产物后，原子更新 `case_config.json`
  与 `CURRENT_HANDOFF.md` 镜像。
- `run_case.py`、`run_pipeline.py`、直接 `main.m` 或任意 skill 的成功输出都不等于
  阶段完成；未 commit 时固定为 `stage_completed=false`。
- 非 authoritative legacy case 可以继续读取，但新 case 必须从启用该协议的
  `cases/case_template` 初始化。

## 阶段条件

### 正式建模

必须读取 `handoff/MODEL_BUILDING_HANDOFF.md`，核对 approved route、
Modeling Boundary、Knowledge Support、Gate 2 条件和输入 SHA256。缺失或不一致时
fail-closed。

### 结果与 Gate 3

result 生成不等于结果可信。Gate 3 前至少核对
`output/result_summary.txt`、`output/result_summary.json`、关键图表、表格、
运行日志、output contract、evidence trace、正确性、边界与稳定性风险。
Gate 3 必须人工决定。

### 文献

文献状态必须显式为 `NOT_RUN`、`GAP_OPEN`、`VERIFIED` 或
`NOT_REQUIRED`。selection/formal tier 中，`NOT_RUN` 或 `GAP_OPEN` 必须
阻止 writer。不得编造文献、搜索当前赛题答案或把候选条目写成正式 references。

### Track B 与 Track C

进入 Track C 前必须同时存在：

- Track B verdict 为 `PASS` 或 `PASS_WITH_LIMITATIONS`；
- `handoff/TRACK_B_TO_TRACK_C.md`；
- `TRACK_C_EXECUTION_AUTHORIZATION=YES`；
- writer manifest 对 Track B 文件与授权文件的哈希绑定。

然后才能执行：

```text
MATLAB figure receipt → Track C → renderer → XeLaTeX
```

新 case 的 Track C 和 renderer 必须消费 `controlled_writing_layer_v5`，同时校验
`argument_blueprint_contract_v1` 与 `visual_reasoning_contract_v1`。蓝图逐问绑定本题对象、
模型、公式含义、假设作用、题目设问、结果卡、跨问依赖、失效条件和自由声明的推理路径；
蓝图是审查依据而不是正文顺序。绑定不存在、正文标记不可见、结果或设问不匹配、跨问依赖
逆序、符号冲突时必须停止；自动修复最多 2 次，之后转人工审查，禁止表面同义改写过检。
实体密度、句面相似度和“是否自然”不是机器硬闸门。

同时按 `STRONG_EVIDENCE_STORY` 生成完整视觉叙事：
每一问进入建模时，先生成一张与题目对象直接对应、位于首组关键公式之前的关系图，
用于解释本问为何采用该模型以及图中关系如何进入公式。先按 `task_visual_family` 路由
图形原型：空间/网络采用构型或拓扑，动态采用轨迹，数据采用变量关系，优化采用可行域
或目标地形，调度采用资源时间图，统计采用不确定性图，评价采用指标关系。不得把某一
case 的几何图固定复用到下一题，也不得用抽象方框总览图或通用流程图充当模型入口图。
正式解释图总数不少于 `task_count+1`，至少一个重点任务另有过程或诊断图，并覆盖模型
结构、中间过程和结果诊断三类视觉。每张图须写明读者问题、正文可见结论和跨层逻辑绑定；
`task_visual_family`、`explanatory_form` 与实际 MATLAB `figure_type` 不兼容时必须停止。

### 最终冻结

只有 Gate 4、引用审计、证据链、论证蓝图回执、可视推理报告、AI 披露、PDF 人工目检和人工提交授权全部满足，
才允许调用 `case-freezer`。不得自动创建 `final_submit/`。

## 复杂度与实验原则

- 路线数量由真实比较决定，不固定为 2–4 条。
- 模型复杂度由题目需要决定，不为高级感叠加算法。
- 灵敏度档位由参数含义和风险决定，不固定为某组三档百分比。
- 未真实运行的实验、数值和比较不得写入证据或论文。
- 禁止 humanizer、AI detector bypass 或以降低 AI 检测率为目标的改写。

## 输出格式

每次调度只输出以下机器可读摘要：

```text
CASE_ID=
CURRENT_STAGE=
STATE_AUTHORITY_STATUS=
LAST_GATE_STATUS=
BLOCKERS=
NEXT_STAGE=
EXECUTOR_STATUS=
STAGE_COMPLETED=false
STOP_BEFORE_DOWNSTREAM=true
```

只有 `transition_case.py commit` 返回 `STAGE_TRANSITION_COMMITTED` 后，才可将
`STAGE_COMPLETED` 改为 `true`。
