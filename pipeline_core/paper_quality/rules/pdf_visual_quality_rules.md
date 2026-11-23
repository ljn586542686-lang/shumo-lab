# PDF 视觉与版式质量规则

状态：`DRAFT_ONLY / NOT_FOR_OFFICIAL_SUBMISSION`

## 1. 目的

本规则用于防止论文 PDF 出现页码压图、图表漂移、断表、图题分离、大面积空白页和工程状态暴露等问题。TeX 或 Markdown 源文件通过不等于 PDF 观感通过，最终 PDF 必须人工目检。

## 2. 必须阻断的问题

以下问题出现时，不得升级为正式材料：

1. 页码压到图片、表格或正文；
2. 图表进入页脚区域；
3. 图题和图片严重分离；
4. 图在上一页、解释文字在下一页，造成阅读顺序混乱；
5. 表格跨页断裂且表头不重复；
6. 列名被拆成不自然断字；
7. 大面积空白页影响论文观感；
8. 章节标题后立即分页，标题孤立在页末；
9. 图表顺序与正文引用顺序不一致；
10. PDF 主体出现 `SUBMISSION_CANDIDATE`、`HUMAN_AUTHORIZATION_REQUIRED` 等工程状态列表。

## 3. 建议通过条件

- 页码位于底部居中或竞赛要求位置，不遮挡内容；
- 页眉页脚简洁，不干扰正文；
- 图表跟随正文解释；
- 章节、图、表之间有合理间距；
- 宽表经过拆表、缩放、横向页或附录处理；
- PDF 逐页人工目检通过；
- 候选版说明使用正式段落，不以工程状态列表进入论文主体。

## 4. TeX 推荐设置

可根据论文模板调整，但默认建议：

```tex
\usepackage{placeins}
\usepackage{float}
\usepackage{tabularx}
\usepackage{booktabs}
\usepackage{longtable}
\usepackage{array}
\usepackage{caption}
\usepackage{fancyhdr}

\pagestyle{fancy}
\fancyhf{}
\cfoot{\thepage}
\renewcommand{\headrulewidth}{0pt}
\renewcommand{\footrulewidth}{0pt}
```

连续图表或重要章节切换处可使用：

```tex
\FloatBarrier
\clearpage
```

## 5. 人工目检清单

- PDF 是否可以打开；
- 页数是否合理；
- 首页、摘要页、结论页是否自然；
- 图表是否完整显示；
- 页码是否压图；
- 表格是否超宽或断裂；
- 图注是否跟随图片；
- 参考文献区是否像论文参考文献；
- AI 使用说明是否像正式填写提醒，而不是系统日志；
- 候选稿和正式稿边界是否清楚。

## 6. 状态码建议

- `PASS_WITH_LIMITATION / PDF_VISUAL_QUALITY_READY`
- `PASS_WITH_LIMITATION / PDF_VISUAL_QUALITY_READY_MANUAL_REVIEW_REQUIRED`
- `BLOCKED / PDF_VISUAL_QUALITY_FAILED`
