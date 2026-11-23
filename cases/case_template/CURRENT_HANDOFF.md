# Current Case Handoff

`CURRENT_HANDOFF_STATUS=STATUS_MIRROR_CASE_CONFIG`

- `CASE_ID`: `case_template`
- `CURRENT_STAGE`: `CASE_INTAKE`
- `STATE_AUTHORITY`: `case_config.json`
- `TRANSITION_AUTHORITY`: `transition_case_v1`
- `STATE_REVISION`: `0`
- `NEXT_ALLOWED_STAGE`: `PROBLEM_UNDERSTANDING`
- `LAST_TRANSITION_ID`: `NONE`

本文件只镜像 `case_config.json` 的当前机器状态，不保存阶段叙事或历史状态。
转换历史见 `handoff/stage_transition_log.jsonl`；阶段产物与限制见对应审查报告。
任何阶段推进必须由 `pipeline_core/python_tools/transition_case.py` 提交。
