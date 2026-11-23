from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipeline_core.python_tools.workflow_state import validate_public_stage, validate_workflow_state


class WorkflowStateAuthorityTests(unittest.TestCase):
    def test_config_authority_requires_matching_handoff_mirror(self) -> None:
        with tempfile.TemporaryDirectory(prefix="workflow_state_") as temp:
            case_dir = Path(temp)
            config = {
                "case_id": "workflow_state_test",
                "status": "RESULT_VALIDATION",
                "state_authority": "case_config.json",
                "transition_authority": "transition_case_v1",
                "current_stage": "RESULT_VALIDATION",
                "next_allowed_stage": "RESULT_ACCEPTANCE",
                "state_revision": 0,
                "current_handoff_path": "CURRENT_HANDOFF.md",
            }
            (case_dir / "case_config.json").write_text(
                json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (case_dir / "CURRENT_HANDOFF.md").write_text(
                "`CURRENT_HANDOFF_STATUS=STATUS_MIRROR_CASE_CONFIG`\n"
                "- `CASE_ID`: `workflow_state_test`\n"
                "- `CURRENT_STAGE`: `RESULT_VALIDATION`\n"
                "- `STATE_AUTHORITY`: `case_config.json`\n"
                "- `TRANSITION_AUTHORITY`: `transition_case_v1`\n"
                "- `STATE_REVISION`: `0`\n"
                "- `NEXT_ALLOWED_STAGE`: `RESULT_ACCEPTANCE`\n"
                "- `LAST_TRANSITION_ID`: `NONE`\n",
                encoding="utf-8",
            )
            self.assertEqual(validate_workflow_state(case_dir, config)["status"], "PASS")

            config["current_stage"] = "MODEL_REVIEW"
            result = validate_workflow_state(case_dir, config)
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(result["reason"], "CURRENT_STAGE_STATE_MISMATCH")

    def test_legacy_case_without_declared_authority_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory(prefix="workflow_state_legacy_") as temp:
            result = validate_workflow_state(Path(temp), {"current_stage": "LEGACY"})
            self.assertEqual(result["status"], "NOT_REQUIRED")

    def test_model_stage_cannot_move_result_validation_case_backwards(self) -> None:
        state = {
            "status": "PASS",
            "current_stage": "PHASE_4_RESULT_VALIDATION_READY_FOR_GATE3",
        }
        result = validate_public_stage("model", state)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["reason"], "PUBLIC_MODEL_STAGE_NOT_ALLOWED_BY_CURRENT_STATE")


if __name__ == "__main__":
    unittest.main()
