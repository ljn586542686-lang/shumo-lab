# 数模实验台

我参加数学建模比赛时做了这套工具。和 AI 聊过几种解法后，我想把选模型的理由记下来。写论文时，看到表里的一个数，也能回头查到它是哪次计算得出来的。

下面用一个弹簧标定的例子走一遍。你可以直接跑代码看看刚度是多少，也可以自己选一次模型，再把核对过的结果写进一份短稿。例子用的是合成数据。比赛时用过的储能预测、滚动调度、文献核对和 AI 使用记录等工具也放在仓库里。[赛时功能对照表](docs/功能对照表.md)

## 先算一根弹簧的刚度

打开仓库根目录，在终端运行下面两行。这个例子在 Windows 和 Python 3.12.0 上跑过，可以直接在本地计算。

```powershell
python -m pip install -r requirements-dev.txt
python -m math_core.examples.spring_calibration
```

假设弹簧拉长 1、2、3 mm 时，拉力分别是 2、4、6 N。程序用这三个点拟合直线，把第四个点留到最后。下面是一次运行的输出。

```text
Slope: 2 N/mm
Intercept: 3.6e-16 N
Prediction at 4 mm: 8 N
Held-out reference: 8 N
Absolute error: 0 N
Check (tolerance 1e-09 N): PASS
```

拟合出的刚度是 2 N/mm，截距接近零。用这条直线预测 4 mm 处的拉力，算出 8 N，和事先留出的值相同。4 mm 已经超出了用来拟合的 1–3 mm 区间。要知道真实弹簧再拉长一些会怎样，还得在那个范围里测一测。

## 自己选一次模型

这三组数据看起来都在一条直线上，但我们没有测过 0 mm 时的拉力。拟合时要不要让直线一定经过原点？下面这个例子把选择留给你。在仓库根目录运行下面这条命令。

```powershell
python -m examples.spring_workshop
```

第一次运行会创建 `cases/synthetic_spring/`。看到 `数据盘点 ok` 和 `等待复核：REQUIRED_HUMAN_GATE_NOT_PASS` 后，程序会以退出码 1 停下来，等你核对题意。下面的路径都从这个案例目录算起。每填完一份确认文件，就回到仓库根目录再运行一次上面的命令。

1. **看数据。** 打开 `data/raw/spring.csv` 和 `problem_analysis.md`，分清哪三个点用来拟合，哪个点用来检验。在 `human_review/gate_1_problem_understanding.md` 逐项核对，选一个决定，填上确认人和时间。有问题也可以选退回。
2. **选模型。** 对照 `model_routes.md` 和 `model_route_score.xlsx`，在 `human_review/gate_2_model_choice.md` 的“选择路线”处填入完整路线名 `R1：带截距直线` 或 `R2：过原点直线`。“选择理由”里写明你对零点的假设，以及为什么放下另一种解法。按文件要求填写决定、确认人和时间。文件里还需要标题 `## 批准后的正式建模/求解前强制 fail-closed 条件`，在下面用列表写至少一条你要求满足的条件，例如保留第四个点做独立检验。
3. **对数字。** 选 R1 时，程序调用 `math_core.registry.run_model` 估计斜率和截距。选 R2 时，截距固定为零，用最小二乘法算斜率。两条路线都会用第四个点检验预测，再独立算一遍。打开 `output/result_summary.json`、`output/tables/calibration.csv` 和 `output/figures/calibration.png`，对照 `evidence_map.md` 核对后，填写 `human_review/gate_3_result_acceptance.md`。
4. **读教学稿。** 第三次确认后再运行一次，稿子在 `learning_draft/tex/main.tex`。成稿前程序会重查数据、选定的模型、结果文件和图表。比如删掉结果文件，它就会停下来。这个教学例子走到 `RESULT_ACCEPTANCE` 为止，正式交付另需签署。

想和 AI 讨论第二步，可以直接问它下面这两个问题。

> 我有三组弹簧读数，位移和拉力分别是 (1 mm, 2 N)、(2 mm, 4 N)、(3 mm, 6 N)，另留了 (4 mm, 8 N) 做检验。我没测过零点。让直线过原点和让数据决定截距，各自假设了什么？只凭这几个点，我能把结论说到什么程度？

如果用了 AI，把用的工具、它的建议和你采纳了哪几条记在案例的 `ai_disclosure/` 里。要查看当前进度，可以在仓库根目录运行下面这条命令。

```powershell
python -m pipeline_core.python_tools.transition_case --case cases/synthetic_spring status
```

要看排版后的 PDF，先装好 XeLaTeX 和模板用到的字体。等 `main.tex` 生成后，从仓库根目录运行下面三行。

```powershell
Set-Location cases/synthetic_spring/learning_draft/tex
xelatex -halt-on-error -interaction=nonstopmode main.tex
xelatex -halt-on-error -interaction=nonstopmode main.tex
```

## 比赛时用到的其他工具

| 想做什么 | 从哪里开始 |
| --- | --- |
| 用自己的数据建一个案例 | 在根目录运行 `python -m pipeline_core.python_tools.case_initializer case_demo`，把有权使用的材料放进新案例的 `data/raw/`，再填写题意和配置。 |
| 求解、验证、成稿或打包 | 用 `python run_case.py --help` 查看参数。各阶段怎样授权、填写回执和提交，可以查 `python -m pipeline_core.python_tools.transition_case --help`。 |
| 用本地 Codex 辅助建模 | 在本地会话中打开案例。项目约定在 `AGENTS.md`，工具说明和提示词在 `.agents/skills/` 和 `pipeline_core/prompt_templates/stage_prompts.md`。正式建模时先读交接文件里的路线、边界和知识卡，再记下实际用了哪些。 |
| 请别人挑一挑结论里的问题 | 给独立审稿者或外部 AI 的提示在 `pipeline_core/claim_check/prompts/` 和 `pipeline_core/red_team/`。运行 `python pipeline_core/claim_check/run_claim_check_dryrun.py` 可以看看程序如何用一个合成反例标出疑点，等人判断。 |
| 看储能问题怎么做预测和调度 | `examples/storage/code/python/` 里有物理约束、预测、分层优化、滚动调度和账本。Q1 从同目录的 `run_pipeline.py` 运行，使用合成数据也需要授权。Q2 到 Q4 的输入要求各不相同。 |
| 检查正式论文和交付材料 | `pipeline_core/paper_template/` 生成论文，`pipeline_core/paper_quality/` 做检查。用 MATLAB 出图需要本机装有 MATLAB。交付前要自己看过 PDF、核对 AI 使用记录和待交文件，再单独授权。 |

储能例子的写表脚本在 `examples/storage/code/javascript/`。给 Q3 或 Q4 写表前，要从独立审批记录取得模板的 SHA-256，按所用模板设置 `Q3_TEMPLATE_SHA256`、`Q4_2_TEMPLATE_SHA256` 或 `Q4_3_TEMPLATE_SHA256`。程序核对模板后才会读取年度账本并写表。复核脚本在同级的 `python/` 目录。

## 运行测试

测试需要 Git，会自行创建临时案例和临时 Git 仓库。可以在干净副本的根目录运行下面这条命令。各项检查的结果和跳过原因见[验证记录](docs/验证记录.md)。

```powershell
python -m pytest -q -rs -p no:cacheprovider pipeline_core/tests math_core/tests validation/tests quality_gates/tests examples/storage/code/python/tests
```

## 许可证

本项目中维护者有权授权的原创代码采用 MIT 许可证。第三方材料保留原有署名与许可，依赖包遵循各自的许可证。[MIT 许可证全文](LICENSE)

## 写在最后

这套程序在比赛里帮我和队友核对过模型和计算结果，并帮我们拿到了省级二等奖。
