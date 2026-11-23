# figures

存放正式图像。

正式论文图像应为白色背景、黑色文字、浅色网格。

正式绘图由 `run_case.py` 的 paper build 入口统一执行：

1. `writer_manifest.json` 在 `writing_policy.figure_placement.figures` 中声明 `matlab_figure_spec`；
2. `source_binding.sha256` 必须等于对应已批准 `result_card` 的规范化 SHA-256；
3. Gate 3 必须由人工通过；
4. MATLAB 脚本、图片和执行 receipt 只能生成到当前 case 的 `output/figures/`；
5. Track C 会复核 Gate 3、结果合同、规格、脚本和图片的完整哈希链；
6. 图片按照 `first_discussion` 放到对应小节首次讨论之后。

不得手工填写 `status=PASS` receipt，也不得使用 case 外部图片路径。PDF 图像还必须能够被真实渲染检查；缺少 PDF renderer 时应改用 300 DPI 以上 PNG，或先补齐受控运行环境。
