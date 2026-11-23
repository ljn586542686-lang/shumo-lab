"""Regression tests for BUG-004 recovery final-state preservation."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
RUN_CASE = ROOT / "run_case.py"
GATE2_TEMPLATE = ROOT / "cases" / "case_template" / "human_review" / "gate_2_model_choice.md"


def invoke(case: Path, authorization_input: Path) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    completed = subprocess.run(
        [
            sys.executable,
            str(RUN_CASE),
            "--mode",
            "competition",
            "--case-dir",
            str(case),
            "--current-stage",
            "MODEL_SELECTION",
            "--requested-stage",
            "MODEL_IMPLEMENTATION",
            "--stage-action",
            "TEST_MODEL_EXECUTION",
            "--authorization-input",
            str(authorization_input),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return completed, json.loads(completed.stdout)


def make_case(root: Path, case_id: str) -> tuple[Path, Path]:
    case = root / case_id
    case.mkdir()
    human_review = case / "human_review"
    human_review.mkdir()
    gate2 = human_review / "gate_2_model_choice.md"
    shutil.copy2(GATE2_TEMPLATE, gate2)
    lines = gate2.read_text(encoding="utf-8").splitlines()
    in_decision = False
    decision_index = 0
    updated: list[str] = []
    for line in lines:
        if re.match(r"^\s*#{1,6}\s+.*(?:决策|decision)", line, flags=re.IGNORECASE):
            in_decision = True
        checkbox = re.match(r"^(\s*-\s+)\[([ xX])\](\s+.*)$", line)
        if checkbox:
            if in_decision:
                mark = "x" if decision_index == 0 else " "
                decision_index += 1
            else:
                mark = "x"
            line = f"{checkbox.group(1)}[{mark}]{checkbox.group(3)}"
        if re.match(r"^\s*(?:确认人|confirmed_by)\s*[:：=]", line, flags=re.IGNORECASE):
            line = "确认人：Human Reviewer"
        elif re.match(r"^\s*(?:时间|confirmed_at)\s*[:：=]", line, flags=re.IGNORECASE):
            line = "时间：2026-08-11T10:00:00+08:00"
        updated.append(line)
    gate2.write_text("\n".join(updated) + "\n", encoding="utf-8")
    (case / "case_config.json").write_text(
        json.dumps({"case_id": case_id}), encoding="utf-8"
    )
    return case, case / "authorization_input.json"


def write_valid_authorization(path: Path, case_id: str) -> None:
    path.write_text(
        json.dumps(
            {
                "case_id": case_id,
                "current_stage": "MODEL_SELECTION",
                "authorized_next_stage": "MODEL_IMPLEMENTATION",
                "requested_stage": "MODEL_IMPLEMENTATION",
                "required_gate": "GATE2",
                "gate_status": "PASS",
                "control_decision": "ALLOWED",
            }
        ),
        encoding="utf-8",
    )


class Bug004RecoveryFinalStateTests(unittest.TestCase):
    def test_valid_authorization_input_preserves_successful_recovery(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bug004_recovery_success_") as temp:
            case, authorization_input = make_case(Path(temp), "recovery_success")
            write_valid_authorization(authorization_input, "recovery_success")

            completed, payload = invoke(case, authorization_input)

            self.assertEqual(completed.returncode, 0)
            self.assertEqual(payload["control_decision"], "ALLOWED")
            self.assertTrue(payload["authorization_verified"])
            self.assertTrue(payload["execution_marker_created"])
            self.assertTrue((case / "execution_marker.txt").exists())

    def test_invalid_authorization_input_blocks_without_execution_side_effect(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bug004_recovery_invalid_") as temp:
            case, authorization_input = make_case(Path(temp), "recovery_invalid")
            authorization_input.write_text(
                json.dumps({"case_id": "recovery_invalid", "control_decision": "BLOCKED"}),
                encoding="utf-8",
            )

            completed, payload = invoke(case, authorization_input)

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(payload["control_decision"], "BLOCKED")
            self.assertFalse((case / "execution_marker.txt").exists())

    def test_recovery_with_authorization_input_does_not_require_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bug004_recovery_no_output_") as temp:
            case, authorization_input = make_case(Path(temp), "recovery_no_output")
            write_valid_authorization(authorization_input, "recovery_no_output")

            completed, payload = invoke(case, authorization_input)

            self.assertEqual(completed.returncode, 0)
            self.assertEqual(payload["control_decision"], "ALLOWED")
            self.assertNotEqual(payload.get("reason"), "AUTHORIZATION_OUTPUT_REQUIRED")
            self.assertNotIn("AUTHORIZATION_OUTPUT_REQUIRED", completed.stdout)
            self.assertFalse((case / "authorization_output.json").exists())


if __name__ == "__main__":
    unittest.main()
