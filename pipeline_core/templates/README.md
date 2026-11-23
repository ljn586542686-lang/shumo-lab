# templates

本目录用于存放跨 case 复用的初始化模板和流程记录模板。

当前推荐的新赛题初始化方式：

```powershell
Copy-Item "cases\case_template" "cases\case_02_new_problem" -Recurse
```

复制后必须先填写新 case 的 `case_config.json`，并将赛题和附件放入 `data/raw`。如果缺少赛题或数据，生产线只能进行目录完整性检查，不能生成假模型、假图表或假论文结论。

## 可复用记录模板

- [decision_log_template.md](decision_log_template.md)：用于记录模型选择、参数设定、人工修正和阶段放行。
- [evidence_map_template.md](evidence_map_template.md)：用于记录论文核心结论与 output、图表、表格和代码变量的对应关系。

## Python 图表模板规则

当前 `pipeline_core/templates/` 下没有独立 Python 代码模板。新 case 的 Python 图表中文化规则以 `cases/case_template/code/python/plot_style.py` 为准：

- 论文图表标题、坐标轴、图例和注释默认使用中文。
- 图表文件名允许使用英文，避免路径编码问题。
- 若中文字体不可用，必须在 `output/validation/output_contract_report.md` 中说明。
- 图表中文化不得改变模型计算逻辑或任何数值结果。
