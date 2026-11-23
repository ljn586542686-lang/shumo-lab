# decision_log_template

复制到具体 case 的 `paper/decision_log.md` 后使用。

## 记录原则

每次模型选择、参数设定、人工修正或阶段切换都必须记录。没有记录的关键取舍，不应进入论文定稿。

## 条目模板

```text
时间：
阶段：
触发原因：

已完成内容：

候选方案：
- 方案 A：
- 方案 B：
- 方案 C：

最终选择：

选择理由：

放弃方案及原因：

涉及参数：

证据来源：
- output/result_summary.txt：
- output/model_log.txt：
- output/sensitivity_report.txt：
- output/tables：
- output/figures：
- code 变量或函数：

人工确认：
- 确认人：
- 确认结论：
- 是否允许进入下一阶段：

如果不通过，返回阶段：
```

## 必记事项

- 题目拆解是否通过；
- 数据审计是否通过；
- 模型路线为何选择；
- 人工设定参数的依据；
- MATLAB 初次结果是否异常；
- `model-critic` 高风险项如何处理；
- 论文初稿是否允许润色；
- 是否允许进入 `humanizer`。
