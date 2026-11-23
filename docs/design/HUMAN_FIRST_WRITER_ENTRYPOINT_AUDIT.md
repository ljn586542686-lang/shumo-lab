# Human-First Writer Entrypoint Audit

## 状态

`AUDIT_COMPLETE_INDEPENDENT_VERIFICATION`。本文件是从当前仓库实际文件出发的独立核验结果，不采信此前 `HUMAN_FIRST_MODELING_LOGIC_CONTRACT.md` §一现状审计表的叙述，作为交叉核对。

## 一、结论：架构判定

本仓库的"生产 writer"是**文档与约定系统**（人 / Claude Code 会话读取指导性文档手动撰写论文正文），**不是**自动化的"读取模板 → 调用 LLM → 生成 LaTeX"代码路径。证据：

- `run_case.py` 全文对 `.tex`/`template` 的检索命中，均与论文内容生成无关（唯一相关的 `_ensure_static_templates` 只生成 `offline_ai_import_template.md`、`release_authorization.json` 等工程模板，非论文正文）。
- `package_candidate.py` 对 `paper/` 的全部操作都是**只读校验**：
  - `paper_config_path = case_dir / "paper" / "paper_preflight_config.json"`，读取已声明的论文源文件清单；
  - 调用 `validation.paper_hard_error_preflight.check_paper_hard_errors()` 扫描**已存在**的 LaTeX/PDF 文本，检查身份泄漏、内部工程术语、反检测文本；
  - `find_paper_pdf()`（`x5_surgical_utils.py`）只检查 `paper/main.pdf`、`paper.pdf`、`output/paper.pdf` 三个候选路径是否存在。
  - 全仓 `.py` 文件对 `final_paper_template.tex` 的引用为 **零**。
- `cases/case_template/` 唯一的程序消费者是 `pipeline_core/python_tools/case_initializer.py` 的 `shutil.copytree`，只做目录整体复制，不解析或改写其中任何文件内容。
- `.agents/skills/*/SKILL.md`（17个文件）是 Claude Code 原生 skill 定义，由 agent/人工会话按需加载执行，不是被 Python 脚本调用的批处理提示词。

**结论**：`AGENTS.md`、`docs/design/HUMAN_FIRST_MODELING_LOGIC_CONTRACT.md`、`pipeline_core/freeze/formal_contest_freeze/final_paper_template.tex`、`cases/case_template/*.md`、`.agents/skills/*/SKILL.md` 共同构成"写作阶段指导文档集合"，接入 Human-First 原则 = 修改这些文档本身，不涉及任何 Python 代码改动。这与 Contract 自身 §八"本契约是写作阶段的指导性文档，不是可强制执行的 gate"的定位完全一致。

## 二、真实入口清单

| 文件 | 角色 | 实际被消费方式 |
|---|---|---|
| `AGENTS.md` | 主 prompt / 项目全局规则 | 人 / Claude Code 会话启动时读取，作为系统级指导 |
| `docs/design/HUMAN_FIRST_MODELING_LOGIC_CONTRACT.md` | 写作阶段内容质量契约 | `AGENTS.md` §5 链接引用；被 `model_routes.md`、`model_critique.md` 引用为依据 |
| `pipeline_core/freeze/formal_contest_freeze/final_paper_template.tex` | 静态 LaTeX 骨架 | 无程序消费者；人工/Claude Code 手动复制作为新论文的起点 |
| `cases/case_template/model_routes.md` | 建模路线记录模板 | 随 `case_initializer.py` 的 `shutil.copytree` 整体复制到新 case，内容由人/agent 手动填写 |
| `cases/case_template/human_review/gate_2_model_choice.md` | 人工确认清单 | 同上，人工手动勾选，不被程序解析 |
| `cases/case_template/model_critique.md` | 模型评审清单 | 同上，人工/评审者手动对照 |
| `.agents/skills/model-route-generator/SKILL.md` | Claude Code skill 定义 | Claude Code 会话按需加载，指导 agent 生成 `model_routes.md` 草稿 |

## 三、纯文档、无消费者（不属于本轮修改范围）

- `pipeline_core/paper_template/README.md`：只有4条基础要求，未被任何程序解析，信息量不构成"现有要求"，本轮不改动。
- `pipeline_core/freeze/formal_contest_freeze/W_FINAL_PAPER_TEMPLATE_RULES.md`：纯排版规范，与建模逻辑内容质量是不同维度，职责边界合理，本轮不改动。

## 四、本轮发现的真实断裂（此前审计遗漏）

**`.agents/skills/model-route-generator/SKILL.md` 第25-31行**仍硬编码：

```
## 必须生成的五类路线

1. 路线 A：保守基准模型
2. 路线 B：主流竞赛模型
3. 路线 C：增强优化模型
4. 路线 D：创新组合模型
5. 路线 E：低风险快速提交模型
```

这与已经软化的 `cases/case_template/model_routes.md`（"Only record routes that were genuinely considered. Do not fabricate a 'tried and failed' exploration story to fill unused route slots"）和 `human_review/gate_2_model_choice.md`（"已记录真实比较过的候选建模路线（数量由实际比较情况决定，不得为凑数编造未真实发生的探索过程）"）直接矛盾——若 Claude Code 会话按此 skill 定义执行，会重新引入 Contract 明确禁止的"制度性鼓励虚构比较过程"问题。此前 `HUMAN_FIRST_MODELING_LOGIC_CONTRACT.md` §一"现状审计表"的写作链审计未覆盖 `.agents/skills/` 目录，因而遗漏了这处不一致。**本轮必须修复。**

## 五、本轮准备修改的文件及理由

1. `.agents/skills/model-route-generator/SKILL.md` —— 修复与 `model_routes.md`/`gate_2_model_choice.md` 的路线数量矛盾，这是本轮唯一的真实生产路径断裂修复。
2. `docs/design/HUMAN_FIRST_MODELING_LOGIC_CONTRACT.md` —— 补充任务书本轮要求但 Contract 尚未覆盖的三块内容（验证边界表述、方法选择表述规范、结果解释规范）及篇幅优先级，并新增样本1/样本2的实例性证据链章节（明确标注非检查清单）。
3. `docs/design/HUMAN_FIRST_REVIEW_PROMPT.md`（新建）—— 将样本2专属的 `work_tmp/.../REUSABLE_HUMAN_FIRST_REVIEW_PROMPT.md` 提炼为仓库级通用人工评审模板，加入长度判断维度。

不修改 `final_paper_template.tex`、`model_routes.md`、`gate_2_model_choice.md`、`model_critique.md` 本身——这些文件在上一阶段已经完成软化，本轮无需重复改动，只需要让 `.agents/skills/` 与它们保持一致。
