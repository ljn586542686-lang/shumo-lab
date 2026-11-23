# 逐问题目专属论证蓝图契约

## 目的

`argument_blueprint_contract_v1` 用于防止正式论文把固定排版结构误用为固定论证模板。
它只检查可客观复核的绑定、正文可见性和引用一致性，不自动判断模型是否正确、论证是否
自然或评委是否喜欢。语义质量仍由人工 Track C 审查。

新 case 在 `case_config.json` 中固定声明：

```json
{
  "writing_contract_version": "controlled_writing_layer_v5",
  "argument_blueprint_required": true
}
```

## 每问必需信息

每个 `task_contract.tasks[].task_id` 必须且只能有一条蓝图。蓝图是写作前审查依据，
不是正文段落顺序。

- `complexity_mode`：`FULL` 或 `SIMPLE`。简单问须给 `simple_mode_reason`。
- `research_objects`：本问真实研究对象及正文可见标记。
- `model_ids`：必须引用本问 `paper_evidence_contract.model_blocks` 中真实存在的模型。
- `key_relations`：关系必须至少连接两个已声明对象，可绑定正式公式。
- `equation_meanings`：必须逐一覆盖本问全部 `equation_label`，说明对象含义并给正文标记；无公式时须说明原因。
- `assumption_links`：覆盖本问模型实际引用的全部假设，并绑定到公式或关键关系；无假设时显式声明 `NONE_DECLARED`。
- `result_answer`：原样绑定 `answer_cards` 的题目设问、回答标记、结果卡和来源。
- `task_conflict`：`FULL` 任务必须把本题矛盾绑定到设问和正文；`SIMPLE` 可退化。
- `upstream_dependency`：显式声明 `NONE` 或引用更早小问的实际结果卡；禁止自依赖、逆序依赖和虚构结果。
- `failure_modes`：`FULL` 任务至少一项，并绑定假设、公式或关系；`SIMPLE` 可说明不适用原因。
- `reasoning_path`：允许 `DECLARED`、`MIXED` 或 `CUSTOM`，步骤数量和顺序由题目决定；每步必须绑定对象、模型、关系、公式或结果之一，并在正文可见。

全局还必须提供：

- `symbol_consistency_review`：覆盖结构化推导中出现的全部变量符号；同一符号定义冲突时停止。
- `abstract_task_bindings`：摘要逐问绑定问题、方法、结果和结果卡。
- `abstract_synthesis_marker`：摘要最后一段必须有整体综合结论。

## 真正的硬阻断

以下情况由 `writing_policy`、Track C、renderer、阶段转换和 `case-freezer` 共同阻断：

1. 蓝图缺失、版本错误、任务遗漏或重复；
2. 引用的对象、模型、公式、假设、结果卡或上游任务不存在；
3. 蓝图声明的关键标记未出现在对应正文或摘要；
4. 正式公式未逐一解释，模型假设未逐一绑定；
5. 题目设问、回答标记、结果卡或来源与结果验收契约不一致；
6. 跨问依赖自指、逆序或引用非上游结果；
7. 推理步骤没有任何真实绑定，或正文顺序与声明顺序冲突；
8. 同一数学符号在不同结构化推导中出现冲突定义；
9. 自动修复超过 2 次、未转人工审查，或试图用表面同义改写过检；
10. renderer 回执缺失，或 `main.tex`、writer manifest 的 SHA256 与回执不一致。

## 不得伪装成硬检查的项目

以下内容程序只能提供人工审查材料，当前不得自动阻断：

- `task_conflict` 是否确实抓住本题核心矛盾；
- 模型选择理由是否成立；
- 推理顺序是否自然、是否符合评委首读直觉；
- 是否存在为了过检而机械堆砌题目名词；
- 段落实体密度、跨问 n-gram 相似度和所谓“AI 味”分数。

不得为了降低相似度进行同义词洗稿。后续只有在至少两种不同题型上完成误伤校准后，
才可把密度或相似度作为提示；仍不得冒充语义正确性证明。

## 有界修复

```json
{
  "repair_policy": {
    "max_automatic_retries": 2,
    "on_exhaustion": "HUMAN_REVIEW_REQUIRED",
    "surface_rewrite_forbidden": true
  }
}
```

失败后应修复真实绑定或正文论证链。连续两次仍失败时停止自动生成，保留失败代码并进入
人工审查，不得无限重写或绕过 Track C。

## 最终回执

v5 renderer 必须生成 `paper/argument_blueprint_trace_report.json`。该回执包含蓝图校验结果、
`main.tex` SHA256 和正式 writer manifest 路径及 SHA256。`PAPER_RENDER` 阶段提交与最终冻结
都会重新校验该回执；只手工创建一个写着 `PASS` 的 JSON 无法通过哈希检查。
