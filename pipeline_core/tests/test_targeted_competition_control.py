"""Black-box verification for the competition authorization-to-dispatch path."""

from __future__ import annotations

import hashlib
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
REAL_CASE = ROOT / "cases" / "case_template"
GATE_FILES = {
    1: "gate_1_problem_understanding.md",
    2: "gate_2_model_choice.md",
    3: "gate_3_result_acceptance.md",
    4: "gate_4_final_submission.md",
}


def file_snapshot(root: Path) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        data = path.read_bytes()
        relative = path.relative_to(root).as_posix()
        stat = path.stat()
        snapshot[relative] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "mtime_ns": stat.st_mtime_ns,
        }
    return snapshot


def snapshot_diff(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    before_names = set(before)
    after_names = set(after)
    return {
        "FILES_CREATED": sorted(after_names - before_names),
        "FILES_DELETED": sorted(before_names - after_names),
        "FILES_MODIFIED": sorted(
            name for name in before_names & after_names if before[name] != after[name]
        ),
    }


def copy_real_gate_templates(case: Path) -> None:
    human_review = case / "human_review"
    human_review.mkdir(parents=True)
    source = REAL_CASE / "human_review"
    for filename in GATE_FILES.values():
        shutil.copy2(source / filename, human_review / filename)


def set_gate2_complete(case: Path, gate_number: int = 2) -> None:
    path = case / "human_review" / GATE_FILES[gate_number]
    lines = path.read_text(encoding="utf-8").splitlines()
    in_decision = False
    decision_index = 0
    updated: list[str] = []
    for line in lines:
        if re.search(r"(?:决策|decision)", line, flags=re.IGNORECASE):
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
            line = "时间：2026-08-10T22:00:00+08:00"
        updated.append(line)
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")


def prepare_case(case: Path, *, case_id: str, gate1: bool = False, gate2: bool = False) -> None:
    case.mkdir(parents=True)
    copy_real_gate_templates(case)
    if gate1:
        set_gate2_complete(case, 1)
    if gate2:
        set_gate2_complete(case, 2)
    (case / "case_config.json").write_text(
        json.dumps({"case_id": case_id}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def invoke(
    case: Path,
    *,
    current_stage: str,
    requested_stage: str,
    action: str | None = "TEST_MODEL_EXECUTION",
    authorization_output: Path | None = None,
    authorization_input: Path | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    command = [
        sys.executable,
        str(RUN_CASE),
        "--mode",
        "competition",
        "--case-dir",
        str(case),
        "--current-stage",
        current_stage,
        "--requested-stage",
        requested_stage,
    ]
    if action is not None:
        command.extend(["--stage-action", action])
    if authorization_output is not None:
        command.extend(["--authorization-output", str(authorization_output)])
    if authorization_input is not None:
        command.extend(["--authorization-input", str(authorization_input)])
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    payload = json.loads(completed.stdout)
    return completed, payload


def assert_no_execution_side_effects(
    testcase: unittest.TestCase,
    *,
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
    case: Path,
    authorization_path: Path,
) -> dict[str, list[str]]:
    diff = snapshot_diff(before, after)
    testcase.assertNotIn("execution_marker.txt", diff["FILES_CREATED"])
    testcase.assertFalse((case / "execution_marker.txt").exists())
    testcase.assertFalse(authorization_path.exists())
    testcase.assertEqual(diff["FILES_DELETED"], [])
    testcase.assertEqual(diff["FILES_MODIFIED"], [])
    return diff


class TargetedCompetitionControlBlackBoxTests(unittest.TestCase):
    def test_gate2_negative_and_positive_real_execution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="competition_stage_binding_") as temp:
            base = Path(temp)
            negative = base / "gate2_pending"
            prepare_case(negative, case_id="blackbox_negative", gate1=True)
            negative_auth = negative / "authorization.json"
            before_negative = file_snapshot(negative)
            blocked, blocked_payload = invoke(
                negative,
                current_stage="MODEL_SELECTION",
                requested_stage="MODEL_IMPLEMENTATION",
                authorization_output=negative_auth,
            )
            after_negative = file_snapshot(negative)
            negative_diff = assert_no_execution_side_effects(
                self,
                before=before_negative,
                after=after_negative,
                case=negative,
                authorization_path=negative_auth,
            )
            self.assertEqual(blocked.returncode, 2)
            self.assertEqual(blocked_payload["control_decision"], "BLOCKED")

            positive = base / "gate2_pass"
            prepare_case(positive, case_id="blackbox_positive", gate1=True, gate2=True)
            positive_auth = positive / "authorization.json"
            before_positive = file_snapshot(positive)
            allowed, allowed_payload = invoke(
                positive,
                current_stage="MODEL_SELECTION",
                requested_stage="MODEL_IMPLEMENTATION",
                authorization_output=positive_auth,
            )
            after_positive = file_snapshot(positive)
            positive_diff = snapshot_diff(before_positive, after_positive)
            self.assertEqual(allowed.returncode, 0)
            self.assertEqual(allowed_payload["control_decision"], "ALLOWED")
            self.assertTrue(positive_auth.exists())
            self.assertTrue((positive / "execution_marker.txt").exists())
            self.assertEqual(positive_diff["FILES_CREATED"], ["authorization.json", "execution_marker.txt"])
            self.assertEqual(positive_diff["FILES_DELETED"], [])
            self.assertEqual(positive_diff["FILES_MODIFIED"], [])
            self.assertTrue(allowed_payload["execution"]["authorization_verified"])
            print(
                json.dumps(
                    {
                        "GATE2_NEGATIVE_REAL_EXECUTION": {
                            "returncode": blocked.returncode,
                            "control_decision": blocked_payload["control_decision"],
                            "stdout": blocked.stdout,
                            "stderr": blocked.stderr,
                            "pre_snapshot": before_negative,
                            "post_snapshot": after_negative,
                            "independent_diff": negative_diff,
                        },
                        "GATE2_POSITIVE_REAL_EXECUTION": {
                            "returncode": allowed.returncode,
                            "control_decision": allowed_payload["control_decision"],
                            "stdout": allowed.stdout,
                            "stderr": allowed.stderr,
                            "pre_snapshot": before_positive,
                            "post_snapshot": after_positive,
                            "independent_diff": positive_diff,
                            "marker_sha256": hashlib.sha256(
                                (positive / "execution_marker.txt").read_bytes()
                            ).hexdigest(),
                        },
                    },
                    ensure_ascii=False,
                )
            )

    def test_wrong_stage_and_wrong_case_are_blocked_by_consumer(self) -> None:
        with tempfile.TemporaryDirectory(prefix="competition_stage_binding_attacks_") as temp:
            base = Path(temp)
            source = base / "source_case"
            prepare_case(source, case_id="source_case", gate1=True, gate2=True)
            source_auth = source / "authorization.json"
            create_auth, _ = invoke(
                source,
                current_stage="MODEL_SELECTION",
                requested_stage="MODEL_IMPLEMENTATION",
                action=None,
                authorization_output=source_auth,
            )
            self.assertEqual(create_auth.returncode, 0)

            wrong_stage_auth = source / "wrong_stage_authorization.json"
            shutil.copy2(source_auth, wrong_stage_auth)
            before_wrong_stage = file_snapshot(source)
            wrong_stage, wrong_stage_payload = invoke(
                source,
                current_stage="MODEL_SELECTION",
                requested_stage="PAPER_GENERATION",
                authorization_input=wrong_stage_auth,
            )
            after_wrong_stage = file_snapshot(source)
            wrong_stage_diff = snapshot_diff(before_wrong_stage, after_wrong_stage)
            self.assertEqual(wrong_stage.returncode, 2)
            self.assertEqual(wrong_stage_payload["control_decision"], "BLOCKED")
            self.assertFalse((source / "execution_marker.txt").exists())
            self.assertEqual(wrong_stage_diff["FILES_CREATED"], [])
            self.assertEqual(wrong_stage_diff["FILES_MODIFIED"], [])

            other = base / "other_case"
            prepare_case(other, case_id="other_case", gate1=True, gate2=True)
            other_auth = other / "authorization.json"
            shutil.copy2(source_auth, other_auth)
            before_wrong_case = file_snapshot(other)
            wrong_case, wrong_case_payload = invoke(
                other,
                current_stage="MODEL_SELECTION",
                requested_stage="MODEL_IMPLEMENTATION",
                authorization_input=other_auth,
            )
            after_wrong_case = file_snapshot(other)
            wrong_case_diff = snapshot_diff(before_wrong_case, after_wrong_case)
            self.assertEqual(wrong_case.returncode, 2)
            self.assertEqual(wrong_case_payload["control_decision"], "BLOCKED")
            self.assertFalse((other / "execution_marker.txt").exists())
            self.assertEqual(wrong_case_diff["FILES_CREATED"], [])
            self.assertEqual(wrong_case_diff["FILES_MODIFIED"], [])
            print(
                json.dumps(
                    {
                        "WRONG_STAGE_STATUS": wrong_stage_payload,
                        "WRONG_STAGE_STDOUT": wrong_stage.stdout,
                        "WRONG_STAGE_STDERR": wrong_stage.stderr,
                        "WRONG_STAGE_DIFF": wrong_stage_diff,
                        "WRONG_STAGE_PRE_SNAPSHOT": before_wrong_stage,
                        "WRONG_STAGE_POST_SNAPSHOT": after_wrong_stage,
                        "WRONG_CASE_STATUS": wrong_case_payload,
                        "WRONG_CASE_STDOUT": wrong_case.stdout,
                        "WRONG_CASE_STDERR": wrong_case.stderr,
                        "WRONG_CASE_DIFF": wrong_case_diff,
                        "WRONG_CASE_PRE_SNAPSHOT": before_wrong_case,
                        "WRONG_CASE_POST_SNAPSHOT": after_wrong_case,
                    },
                    ensure_ascii=False,
                )
            )

    def test_real_gate2_parser_attacks_fail_closed(self) -> None:
        attacks = {
            "empty_gate": lambda text: "",
            "partial_checkbox": lambda text: text.replace("- [ ]", "- [x]", 1),
            "both_decisions_selected": lambda text: re.sub(
                r"(- \[[ xX]\].*?)(\n- \[[ xX]\].*?)", lambda match: match.group(1).replace("[ ]", "[x]") + match.group(2).replace("[ ]", "[x]"), text, count=1, flags=re.DOTALL
            ),
            "missing_confirmer": lambda text: re.sub(r"^(\s*确认人\s*[:：=]).*$", r"\1", text, flags=re.MULTILINE),
            "missing_confirmed_at": lambda text: re.sub(r"^(\s*时间\s*[:：=]).*$", r"\1", text, flags=re.MULTILINE),
            "malformed_gate": lambda text: "not a gate",
            "fake_status_without_checklist": lambda text: "HUMAN_GATE_STATUS: APPROVED\n",
        }
        with tempfile.TemporaryDirectory(prefix="competition_gate2_attacks_") as temp:
            base = Path(temp)
            for name, mutate in attacks.items():
                case = base / name
                prepare_case(case, case_id=f"attack_{name}", gate1=True)
                gate2 = case / "human_review" / GATE_FILES[2]
                original = gate2.read_text(encoding="utf-8")
                gate2.write_text(mutate(original), encoding="utf-8")
                auth = case / "authorization.json"
                before = file_snapshot(case)
                completed, payload = invoke(
                    case,
                    current_stage="MODEL_SELECTION",
                    requested_stage="MODEL_IMPLEMENTATION",
                    authorization_output=auth,
                )
                after = file_snapshot(case)
                self.assertEqual(completed.returncode, 2, name)
                self.assertEqual(payload["control_decision"], "BLOCKED", name)
                self.assertFalse(auth.exists(), name)
                self.assertFalse((case / "execution_marker.txt").exists(), name)
                self.assertEqual(snapshot_diff(before, after)["FILES_CREATED"], [], name)
            print(json.dumps({"GATE_TEMPLATE_ATTACKS": list(attacks), "status": "BLOCKED"}, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
