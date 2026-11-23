# Python Code

Place case-specific Python scripts here. Shared tools should live in `pipeline_core/python_tools`.

## 图表中文化

Python 建模脚本生成论文图表前，应先调用本目录的 `plot_style.py`：

```python
from plot_style import setup_chinese_plot_style

plot_style_status = setup_chinese_plot_style()
```

要求：

- 图表标题、坐标轴、图例和注释默认使用中文。
- 图表文件名可以使用英文，避免路径编码问题。
- 如果返回 `font_available=False`，必须在 `output/validation/output_contract_report.md` 中说明“中文字体不可用”。
- 图表中文化只允许影响渲染样式，不得改变模型算法、评分、额度、利率或任何数值结果。
