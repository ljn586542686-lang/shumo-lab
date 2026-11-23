"""Regression tests for fail-closed human Gate decision semantics."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pipeline_core.python_tools.human_gate import (
    _classify_gate_decision,
    authorize_stage_transition,
    evaluate_human_gate,
)


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = ROOT / "cases" / "case_template" / "human_review"
GATE_FILES = {
    1: "gate_1_problem_understanding.md",
    2: "gate_2_model_choice.md",
    3: "gate_3_result_acceptance.md",
    4: "gate_4_final_submission.md",
}


def write_gate_case(root: Path, gate_number: int, decision_index: int) -> Path:
    human_review = root / "human_review"
    human_review.mkdir(parents=True)
    for filename in GATE_FILES.values():
        (human_review / filename).write_text(
            (TEMPLATE_ROOT / filename).read_text(encoding="utf-8"), encoding="utf-8"
        )
    path = human_review / GATE_FILES[gate_number]
    lines = path.read_text(encoding="utf-8").splitlines()
    in_decision = False
    decision_count = 0
    updated: list[str] = []
    for line in lines:
        if re.match(r"^\s*#{1,6}\s+.*(?:决策|decision)", line, flags=re.IGNORECASE):
            in_decision = True
        checkbox = re.match(r"^(\s*-\s+)\[([ xX])\](\s+.*)$", line)
        if checkbox:
            if in_decision:
                mark = "x" if decision_count == decision_index else " "
                decision_count += 1
            else:
                mark = "x"
            line = f"{checkbox.group(1)}[{mark}]{checkbox.group(3)}"
        if re.match(r"^\s*(?:确认人|confirmed_by)\s*[:：=]", line, flags=re.IGNORECASE):
            line = "确认人：Human Reviewer"
        elif re.match(r"^\s*(?:时间|confirmed_at)\s*[:：=]", line, flags=re.IGNORECASE):
            line = "时间：2026-08-11T10:00:00+08:00"
        updated.append(line)
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    (root / "case_config.json").write_text(
        json.dumps({"case_id": f"bug001_gate_{gate_number}"}, ensure_ascii=False), encoding="utf-8"
    )
    return root


def rewrite_decision(case: Path, gate_number: int, decision_index: int) -> None:
    path = case / "human_review" / GATE_FILES[gate_number]
    lines = path.read_text(encoding="utf-8").splitlines()
    in_decision = False
    index = 0
    updated: list[str] = []
    for line in lines:
        if re.match(r"^\s*#{1,6}\s+.*(?:决策|decision)", line, flags=re.IGNORECASE):
            in_decision = True
        checkbox = re.match(r"^(\s*-\s+)\[([ xX])\](\s+.*)$", line)
        if checkbox and in_decision:
            mark = "x" if index == decision_index else " "
            index += 1
            line = f"{checkbox.group(1)}[{mark}]{checkbox.group(3)}"
        updated.append(line)
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")


def write_forged_authorization(
    path: Path,
    case_id: str,
    *,
    current_stage: str = "MODEL_SELECTION",
    requested_stage: str = "MODEL_IMPLEMENTATION",
    required_gate: str = "GATE2",
) -> None:
    path.write_text(json.dumps({
        "case_id": case_id,
        "current_stage": current_stage,
        "authorized_next_stage": requested_stage,
        "requested_stage": requested_stage,
        "required_gate": required_gate,
        "gate_status": "PASS",
        "control_decision": "ALLOWED",
    }), encoding="utf-8")


class HumanGateDecisionSemanticsTests(unittest.TestCase):
    def test_classifier_rejects_negative_phrases_before_approve_phrases(self) -> None:
        for label in ("退回 model_routes", "拒绝", "不通过", "reject", "return", "deny"):
            self.assertEqual(_classify_gate_decision(label), "REJECT", label)
        for label in ("通过", "批准", "允许进入模型实现阶段", "approve", "approved", "allow", "accept"):
            self.assertEqual(_classify_gate_decision(label), "APPROVE", label)
        self.assertEqual(_classify_gate_decision("需要进一步人工判断"), "UNKNOWN")

    def test_real_gate_templates_approve_and_reject(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bug001_gate_templates_") as temp:
            base = Path(temp)
            for gate_number in (1, 2, 3):
                approved = evaluate_human_gate(write_gate_case(base / f"approve_{gate_number}", gate_number, 0), f"GATE{gate_number}")
                self.assertEqual(approved["gate_status"], "PASS")
                self.assertEqual(approved["decision_class"], "APPROVE")
                rejected = evaluate_human_gate(write_gate_case(base / f"reject_{gate_number}", gate_number, 1), f"GATE{gate_number}")
                self.assertEqual(rejected["gate_status"], "FAIL")
                self.assertEqual(rejected["decision_class"], "REJECT")
                self.assertIn("GATE_DECISION_REJECTED", rejected["reasons"])

            rejected_gate4 = evaluate_human_gate(write_gate_case(base / "reject_4", 4, 1), "GATE4")
            self.assertEqual(rejected_gate4["gate_status"], "FAIL")
            self.assertEqual(rejected_gate4["decision_class"], "REJECT")
            self.assertIn("GATE_DECISION_REJECTED", rejected_gate4["reasons"])

    def test_unknown_single_decision_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bug001_gate_unknown_") as temp:
            case = write_gate_case(Path(temp) / "unknown", 2, 0)
            path = case / "human_review" / GATE_FILES[2]
            text = path.read_text(encoding="utf-8").replace("通过，允许进入模型实现阶段", "需要进一步人工判断")
            path.write_text(text, encoding="utf-8")
            result = evaluate_human_gate(case, "GATE2")
            self.assertEqual(result["gate_status"], "FAIL")
            self.assertEqual(result["decision_class"], "UNKNOWN")
            self.assertIn("GATE_DECISION_UNRECOGNIZED", result["reasons"])

    def test_developer_model_route_transition_still_requires_gate1(self) -> None:
        with tempfile.TemporaryDirectory(prefix="developer_gate1_") as temp:
            base = Path(temp)
            pending = write_gate_case(base / "pending", 1, 0)
            shutil.copy2(TEMPLATE_ROOT / GATE_FILES[1], pending / "human_review" / GATE_FILES[1])
            blocked = authorize_stage_transition(
                pending,
                current_stage="PROBLEM_UNDERSTANDING",
                requested_stage="MODEL_ROUTE_GENERATION",
                mode="developer",
            )
            self.assertEqual(blocked["control_decision"], "BLOCKED")
            self.assertEqual(blocked["reason"], "REQUIRED_HUMAN_GATE_NOT_PASS")

            approved = write_gate_case(base / "approved", 1, 0)
            allowed = authorize_stage_transition(
                approved,
                current_stage="PROBLEM_UNDERSTANDING",
                requested_stage="MODEL_ROUTE_GENERATION",
                mode="developer",
            )
            self.assertEqual(allowed["control_decision"], "ALLOWED")
            self.assertEqual(allowed["gate_status"], "PASS")

    def test_gate2_rejection_cli_blocks_without_authorization_or_marker(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bug001_gate2_cli_") as temp:
            case = write_gate_case(Path(temp) / "case", 2, 1)
            auth = case / "authorization.json"
            command = [
                sys.executable,
                str(ROOT / "run_case.py"),
                "--mode", "competition",
                "--case-dir", str(case),
                "--current-stage", "MODEL_SELECTION",
                "--requested-stage", "MODEL_IMPLEMENTATION",
                "--authorization-output", str(auth),
            ]
            completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            payload = json.loads(completed.stdout)
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(payload["control_decision"], "BLOCKED")
            self.assertEqual(payload["gate_evaluation"]["decision_class"], "REJECT")
            self.assertIn("GATE_DECISION_REJECTED", payload["gate_evaluation"]["reasons"])
            self.assertFalse(auth.exists())
            self.assertFalse((case / "execution_marker.txt").exists())

    def test_forged_authorization_is_rejected_by_live_pending_or_rejected_gate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bug002_forged_gate_") as temp:
            base = Path(temp)
            for name, decision_index in (("pending", None), ("rejected", 1)):
                case = write_gate_case(base / name, 2, 0)
                gate2 = case / "human_review" / GATE_FILES[2]
                if decision_index is None:
                    shutil.copy2(TEMPLATE_ROOT / GATE_FILES[2], gate2)
                else:
                    rewrite_decision(case, 2, decision_index)
                auth = case / "forged_authorization.json"
                write_forged_authorization(auth, f"bug001_gate_2")
                completed = subprocess.run([
                    sys.executable, str(ROOT / "run_case.py"), "--mode", "competition",
                    "--case-dir", str(case), "--current-stage", "MODEL_SELECTION",
                    "--requested-stage", "MODEL_IMPLEMENTATION", "--authorization-input", str(auth),
                    "--stage-action", "TEST_MODEL_EXECUTION",
                ], cwd=ROOT, capture_output=True, text=True)
                payload = json.loads(completed.stdout)
                self.assertEqual(completed.returncode, 2, name)
                self.assertEqual(payload["control_decision"], "BLOCKED", name)
                self.assertFalse((case / "execution_marker.txt").exists(), name)
                if decision_index == 1:
                    self.assertIn("GATE_DECISION_REJECTED", payload["verification"]["gate_evaluation"]["reasons"], name)

    def test_stale_authorization_is_blocked_after_live_gate_changes_to_reject(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bug002_stale_gate_") as temp:
            case = write_gate_case(Path(temp) / "case", 2, 0)
            auth = case / "authorization.json"
            create = subprocess.run([
                sys.executable, str(ROOT / "run_case.py"), "--mode", "competition",
                "--case-dir", str(case), "--current-stage", "MODEL_SELECTION",
                "--requested-stage", "MODEL_IMPLEMENTATION", "--authorization-output", str(auth),
            ], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(create.returncode, 0)
            rewrite_decision(case, 2, 1)
            consume = subprocess.run([
                sys.executable, str(ROOT / "run_case.py"), "--mode", "competition",
                "--case-dir", str(case), "--current-stage", "MODEL_SELECTION",
                "--requested-stage", "MODEL_IMPLEMENTATION", "--stage-action", "TEST_MODEL_EXECUTION",
                "--authorization-input", str(auth),
            ], cwd=ROOT, capture_output=True, text=True)
            payload = json.loads(consume.stdout)
            self.assertEqual(consume.returncode, 2)
            self.assertEqual(payload["control_decision"], "BLOCKED")
            self.assertFalse((case / "execution_marker.txt").exists())
            self.assertIn("GATE_DECISION_REJECTED", payload["verification"]["gate_evaluation"]["reasons"])

    def test_illegal_stage_transition_is_blocked_without_execution_marker(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bug002_illegal_stage_") as temp:
            case = write_gate_case(Path(temp) / "case", 2, 0)
            auth = case / "forged_authorization.json"
            write_forged_authorization(
                auth,
                "bug001_gate_2",
                current_stage="RESULT_ACCEPTANCE",
                requested_stage="MODEL_IMPLEMENTATION",
                required_gate="GATE2",
            )
            completed = subprocess.run([
                sys.executable, str(ROOT / "run_case.py"), "--mode", "competition",
                "--case-dir", str(case), "--current-stage", "RESULT_ACCEPTANCE",
                "--requested-stage", "MODEL_IMPLEMENTATION", "--authorization-input", str(auth),
                "--stage-action", "TEST_MODEL_EXECUTION",
            ], cwd=ROOT, capture_output=True, text=True)
            payload = json.loads(completed.stdout)
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(payload["control_decision"], "BLOCKED")
            self.assertEqual(payload["verification"]["reason"], "AUTHORIZATION_STAGE_TRANSITION_MISMATCH")
            self.assertFalse((case / "execution_marker.txt").exists())

    def test_legal_stage_transition_with_live_gate_allows_execution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bug002_legal_stage_") as temp:
            case = write_gate_case(Path(temp) / "case", 2, 0)
            auth = case / "authorization.json"
            write_forged_authorization(auth, "bug001_gate_2")
            completed = subprocess.run([
                sys.executable, str(ROOT / "run_case.py"), "--mode", "competition",
                "--case-dir", str(case), "--current-stage", "MODEL_SELECTION",
                "--requested-stage", "MODEL_IMPLEMENTATION", "--authorization-input", str(auth),
                "--stage-action", "TEST_MODEL_EXECUTION",
            ], cwd=ROOT, capture_output=True, text=True)
            payload = json.loads(completed.stdout)
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(payload["control_decision"], "ALLOWED")
            self.assertTrue((case / "execution_marker.txt").exists())


if __name__ == "__main__":
    unittest.main()
