from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline_core.python_tools.case_initializer import initialize_case
from pipeline_core.python_tools.run_pipeline import run_stage
from pipeline_core.python_tools.transition_case import (
    PUBLIC_STAGE_ALLOWED_CURRENT,
    REQUIRED_ARTIFACT_GROUPS,
    SUBMISSION_READY_BOUND_FILES,
    SUBMISSION_READY_RECORD,
    SUBMISSION_READY_SCHEMA,
    STAGE_TRANSITIONS,
    authorize_transition,
    build_completion_receipt,
    commit_transition,
    validate_authoritative_state,
    write_completion_receipt,
    write_transition_authorization,
    _required_artifact_groups,
)


ROOT = Path(__file__).resolve().parents[2]
TRANSITION_CLI = ROOT / "pipeline_core" / "python_tools" / "transition_case.py"


def approve_gate(case: Path, gate_number: int) -> None:
    names = {
        1: "gate_1_problem_understanding.md",
        2: "gate_2_model_choice.md",
        3: "gate_3_result_acceptance.md",
        4: "gate_4_final_submission.md",
    }
    path = case / "human_review" / names[gate_number]
    lines = path.read_text(encoding="utf-8").splitlines()
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
            line = "时间：2026-08-23T12:00:00+08:00"
        updated.append(line)
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")


class TransitionCaseKernelTests(unittest.TestCase):
    def make_case(self, root: Path, case_id: str = "transition_test") -> Path:
        template_target = root / "cases" / "case_template"
        template_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROOT / "cases" / "case_template", template_target)
        case = initialize_case(root, case_id)
        (case / "problem").mkdir(parents=True, exist_ok=True)
        (case / "problem" / "problem_contract.md").write_text(
            "# Problem Contract\n\nBounded test contract.\n",
            encoding="utf-8",
        )
        return case

    def complete_transition(
        self,
        case: Path,
        *,
        target: str,
        artifacts: list[str],
        executor: str = "test-executor",
    ) -> dict:
        authorization = authorize_transition(case, requested_stage=target, executor=executor)
        self.assertEqual(authorization["status"], "PASS", authorization)
        authorization_path = write_transition_authorization(case, authorization)
        receipt = build_completion_receipt(
            case,
            authorization_path=authorization_path,
            artifacts=artifacts,
            executor_status="PASS",
        )
        self.assertEqual(receipt["status"], "PASS", receipt)
        receipt_path = write_completion_receipt(case, receipt)
        return commit_transition(
            case,
            authorization_path=authorization_path,
            completion_receipt_path=receipt_path,
        )

    def test_initializer_enables_single_authority_and_updates_case_identity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="transition_initializer_") as temp:
            root = Path(temp)
            case = self.make_case(root, "fresh_case")
            config = json.loads((case / "case_config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["case_id"], "fresh_case")
            self.assertEqual(config["current_stage"], "CASE_INTAKE")
            self.assertEqual(config["state_authority"], "case_config.json")
            self.assertEqual(config["transition_authority"], "transition_case_v1")
            self.assertIn("`CASE_ID`: `fresh_case`", (case / "CURRENT_HANDOFF.md").read_text(encoding="utf-8"))
            self.assertEqual(validate_authoritative_state(case)["status"], "PASS")
            completed = subprocess.run(
                [sys.executable, str(TRANSITION_CLI), "--case", str(case), "status"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["status"], "PASS")

    def test_only_kernel_commit_changes_stage_and_updates_mirror(self) -> None:
        with tempfile.TemporaryDirectory(prefix="transition_commit_") as temp:
            root = Path(temp)
            case = self.make_case(root)
            before = json.loads((case / "case_config.json").read_text(encoding="utf-8"))
            # Creating arbitrary executor output alone cannot change state.
            (case / "output" / "result_summary.json").write_text("{}\n", encoding="utf-8")
            unchanged = json.loads((case / "case_config.json").read_text(encoding="utf-8"))
            self.assertEqual(unchanged["current_stage"], before["current_stage"])

            result = self.complete_transition(
                case,
                target="PROBLEM_UNDERSTANDING",
                artifacts=["problem/problem_contract.md", "problem_analysis.md"],
            )
            self.assertEqual(result["status"], "PASS", result)
            config = json.loads((case / "case_config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["current_stage"], "PROBLEM_UNDERSTANDING")
            self.assertEqual(config["state_revision"], 1)
            handoff = (case / "CURRENT_HANDOFF.md").read_text(encoding="utf-8")
            self.assertIn("`CURRENT_STAGE`: `PROBLEM_UNDERSTANDING`", handoff)
            self.assertIn("`NEXT_ALLOWED_STAGE`: `MODEL_ROUTE_GENERATION`", handoff)
            self.assertIn("`STATE_REVISION`: `1`", handoff)
            self.assertIn(f"`LAST_TRANSITION_ID`: `{result['authorization_id']}`", handoff)
            self.assertNotIn("NOT_STARTED", handoff)

    def test_skip_transition_and_declared_current_stage_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory(prefix="transition_skip_") as temp:
            case = self.make_case(Path(temp))
            skipped = authorize_transition(case, requested_stage="MODEL_IMPLEMENTATION", executor="test")
            self.assertEqual(skipped["reason"], "AUTHORIZED_NEXT_STAGE_MISMATCH")
            forged = authorize_transition(
                case,
                requested_stage="PROBLEM_UNDERSTANDING",
                executor="test",
                declared_current_stage="MODEL_SELECTION",
            )
            self.assertEqual(forged["reason"], "DECLARED_CURRENT_STAGE_MISMATCH")

    def test_canonical_graph_contains_track_a_literature_and_track_b_before_track_c(self) -> None:
        self.assertEqual(STAGE_TRANSITIONS["RESULT_ACCEPTANCE"], "TRACK_A")
        self.assertEqual(STAGE_TRANSITIONS["TRACK_A"], "LITERATURE_GROUNDING")
        self.assertEqual(STAGE_TRANSITIONS["LITERATURE_GROUNDING"], "TRACK_B")
        self.assertEqual(STAGE_TRANSITIONS["TRACK_B"], "TRACK_C")
        self.assertEqual(STAGE_TRANSITIONS["FINAL_REVIEW"], "SUBMISSION_READY")
        self.assertNotIn("SUBMISSION_READY", STAGE_TRANSITIONS)
        self.assertNotIn("RESULT_ACCEPTANCE", PUBLIC_STAGE_ALLOWED_CURRENT["paper"])
        self.assertIn(
            ("paper/pdf_check/manual_visual_review.json",),
            REQUIRED_ARTIFACT_GROUPS["PAPER_RENDER"],
        )
        self.assertIn(
            ("paper/visual_reasoning_trace_report.json",),
            REQUIRED_ARTIFACT_GROUPS["PAPER_RENDER"],
        )
        self.assertIn(
            ("paper/argument_blueprint_trace_report.json",),
            _required_artifact_groups({"argument_blueprint_required": True}, "PAPER_RENDER"),
        )
        self.assertNotIn(
            ("paper/argument_blueprint_trace_report.json",),
            _required_artifact_groups({}, "PAPER_RENDER"),
        )

        with tempfile.TemporaryDirectory(prefix="transition_track_skip_") as temp:
            case = self.make_case(Path(temp))
            config_path = case / "case_config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["current_stage"] = "PHASE_4_RESULT_VALIDATION_READY_FOR_GATE3"
            config["status"] = "PHASE_4_RESULT_VALIDATION_READY_FOR_GATE3"
            config["next_allowed_stage"] = "TRACK_A"
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            handoff_path = case / "CURRENT_HANDOFF.md"
            handoff = handoff_path.read_text(encoding="utf-8")
            handoff = handoff.replace("`CURRENT_STAGE`: `CASE_INTAKE`", "`CURRENT_STAGE`: `PHASE_4_RESULT_VALIDATION_READY_FOR_GATE3`")
            handoff = handoff.replace("`NEXT_ALLOWED_STAGE`: `PROBLEM_UNDERSTANDING`", "`NEXT_ALLOWED_STAGE`: `TRACK_A`")
            handoff_path.write_text(handoff, encoding="utf-8")
            skipped = authorize_transition(case, requested_stage="TRACK_C", executor="test")
            self.assertEqual(skipped["reason"], "AUTHORIZED_NEXT_STAGE_MISMATCH")
            self.assertEqual(skipped["authorized_next_stage"], "TRACK_A")

    def _set_final_review_state(self, case: Path) -> None:
        config_path = case / "case_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["current_stage"] = "FINAL_REVIEW"
        config["status"] = "FINAL_REVIEW"
        config["next_allowed_stage"] = "SUBMISSION_READY"
        config["official_submission_mode"] = "locked"
        config["final_submit_allowed"] = False
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        handoff_path = case / "CURRENT_HANDOFF.md"
        handoff = handoff_path.read_text(encoding="utf-8")
        handoff = handoff.replace("`CURRENT_STAGE`: `CASE_INTAKE`", "`CURRENT_STAGE`: `FINAL_REVIEW`")
        handoff = handoff.replace("`NEXT_ALLOWED_STAGE`: `PROBLEM_UNDERSTANDING`", "`NEXT_ALLOWED_STAGE`: `SUBMISSION_READY`")
        handoff_path.write_text(handoff, encoding="utf-8")
        approve_gate(case, 4)
        gate_path = case / "human_review" / "gate_4_final_submission.md"
        gate_path.write_text(
            gate_path.read_text(encoding="utf-8")
            + "\nHUMAN_GATE_STATUS=APPROVED\nAPPROVED_BY=Human Reviewer\nMANUAL_CONFIRMATION=YES\n",
            encoding="utf-8",
        )

    def _write_submission_ready_record(self, case: Path, *, gate4_approved: bool = True) -> None:
        for relative in SUBMISSION_READY_BOUND_FILES:
            path = case / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text(f"test artifact: {relative}\n", encoding="utf-8")
        bindings = {
            relative: hashlib.sha256((case / relative).read_bytes()).hexdigest()
            for relative in SUBMISSION_READY_BOUND_FILES
        }
        record = {
            "schema_version": SUBMISSION_READY_SCHEMA,
            "status": "READY_WITHOUT_PACKAGE",
            "gate4_approved": gate4_approved,
            "final_submit_created": False,
            "package_creation_authorized": False,
            "official_submission_mode_locked": True,
            "artifact_sha256": bindings,
        }
        path = case / SUBMISSION_READY_RECORD
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def test_gate4_can_close_submission_ready_without_creating_final_submit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="transition_submission_ready_") as temp:
            case = self.make_case(Path(temp))
            self._set_final_review_state(case)
            self._write_submission_ready_record(case)
            result = self.complete_transition(case, target="SUBMISSION_READY", artifacts=[SUBMISSION_READY_RECORD])
            self.assertEqual(result["status"], "PASS", result)
            config = json.loads((case / "case_config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["current_stage"], "SUBMISSION_READY")
            self.assertEqual(config["human_gate_status"], "APPROVED")
            self.assertEqual(config["submission_package_status"], "NOT_CREATED_BY_USER_BOUNDARY")
            self.assertFalse(config["final_submit_allowed"])
            self.assertEqual(config["official_submission_mode"], "locked")
            self.assertFalse((case / "final_submit").exists())

    def test_submission_ready_record_fails_closed_when_gate4_flag_is_false(self) -> None:
        with tempfile.TemporaryDirectory(prefix="transition_submission_ready_invalid_") as temp:
            case = self.make_case(Path(temp))
            self._set_final_review_state(case)
            self._write_submission_ready_record(case, gate4_approved=False)
            authorization = authorize_transition(case, requested_stage="SUBMISSION_READY", executor="test")
            self.assertEqual(authorization["status"], "PASS", authorization)
            authorization_path = write_transition_authorization(case, authorization)
            receipt = build_completion_receipt(
                case,
                authorization_path=authorization_path,
                artifacts=[SUBMISSION_READY_RECORD],
                executor_status="PASS",
            )
            receipt_path = write_completion_receipt(case, receipt)
            result = commit_transition(case, authorization_path=authorization_path, completion_receipt_path=receipt_path)
            self.assertEqual(result["reason"], "SUBMISSION_READY_RECORD_FLAG_INVALID:gate4_approved")
            self.assertEqual(json.loads((case / "case_config.json").read_text(encoding="utf-8"))["current_stage"], "FINAL_REVIEW")
            self.assertFalse((case / "final_submit").exists())

    def test_semantically_stale_mirror_fields_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory(prefix="transition_stale_mirror_") as temp:
            case = self.make_case(Path(temp))
            handoff_path = case / "CURRENT_HANDOFF.md"
            original = handoff_path.read_text(encoding="utf-8")

            handoff_path.write_text(
                original.replace(
                    "`NEXT_ALLOWED_STAGE`: `PROBLEM_UNDERSTANDING`",
                    "`NEXT_ALLOWED_STAGE`: `TRACK_A`",
                ),
                encoding="utf-8",
            )
            result = validate_authoritative_state(case)
            self.assertEqual(result["reason"], "CURRENT_HANDOFF_NEXT_STAGE_MISMATCH")

            handoff_path.write_text(
                original.replace("`STATE_REVISION`: `0`", "`STATE_REVISION`: `9`"),
                encoding="utf-8",
            )
            result = validate_authoritative_state(case)
            self.assertEqual(result["reason"], "CURRENT_HANDOFF_STATE_REVISION_MISMATCH")

    def test_gate_and_case_config_hash_are_rechecked_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="transition_hash_") as temp:
            case = self.make_case(Path(temp))
            first = self.complete_transition(
                case,
                target="PROBLEM_UNDERSTANDING",
                artifacts=["problem/problem_contract.md", "problem_analysis.md"],
            )
            self.assertEqual(first["status"], "PASS")
            pending = authorize_transition(case, requested_stage="MODEL_ROUTE_GENERATION", executor="test")
            self.assertEqual(pending["reason"], "REQUIRED_HUMAN_GATE_NOT_PASS")

            approve_gate(case, 1)
            allowed = authorize_transition(case, requested_stage="MODEL_ROUTE_GENERATION", executor="test")
            self.assertEqual(allowed["status"], "PASS", allowed)
            auth_path = write_transition_authorization(case, allowed)
            config_path = case / "case_config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["unrelated_mutation"] = True
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            receipt = build_completion_receipt(
                case,
                authorization_path=auth_path,
                artifacts=["problem/problem_contract.md"],
                executor_status="PASS",
            )
            self.assertEqual(receipt["status"], "FAIL")
            self.assertEqual(receipt["reason"], "AUTHORIZATION_CASE_CONFIG_HASH_MISMATCH")

    def test_authoritative_model_entry_requires_transition_authorization(self) -> None:
        with tempfile.TemporaryDirectory(prefix="transition_public_entry_") as temp:
            case = self.make_case(Path(temp))
            committed = self.complete_transition(
                case,
                target="PROBLEM_UNDERSTANDING",
                artifacts=["problem/problem_contract.md", "problem_analysis.md"],
            )
            self.assertEqual(committed["status"], "PASS")
            blocked = run_stage(case, "model")
            self.assertEqual(blocked["status"], "BLOCKED")
            self.assertEqual(blocked["reason"], "TRANSITION_AUTHORIZATION_REQUIRED")

            approve_gate(case, 1)
            wrong_executor = authorize_transition(
                case,
                requested_stage="MODEL_ROUTE_GENERATION",
                executor="some-other-entry",
            )
            wrong_path = write_transition_authorization(case, wrong_executor)
            wrong = run_stage(
                case,
                "model",
                transition_authorization_path=str(wrong_path),
            )
            self.assertEqual(wrong["reason"], "TRANSITION_EXECUTOR_MISMATCH")

            correct_executor = authorize_transition(
                case,
                requested_stage="MODEL_ROUTE_GENERATION",
                executor="run_pipeline:model",
            )
            correct_path = write_transition_authorization(case, correct_executor)
            executed = run_stage(
                case,
                "model",
                transition_authorization_path=str(correct_path),
            )
            self.assertFalse(executed["stage_completed"])
            self.assertTrue(executed["transition_required"])
            self.assertEqual(validate_authoritative_state(case)["current_stage"], "PROBLEM_UNDERSTANDING")

    def test_duplicate_commit_and_artifact_tampering_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory(prefix="transition_replay_") as temp:
            case = self.make_case(Path(temp))
            authorization = authorize_transition(
                case,
                requested_stage="PROBLEM_UNDERSTANDING",
                executor="test-executor",
            )
            auth_path = write_transition_authorization(case, authorization)
            receipt = build_completion_receipt(
                case,
                authorization_path=auth_path,
                artifacts=["problem/problem_contract.md", "problem_analysis.md"],
                executor_status="PASS",
            )
            receipt_path = write_completion_receipt(case, receipt)
            (case / "problem_analysis.md").write_text("tampered after receipt\n", encoding="utf-8")
            tampered = commit_transition(
                case,
                authorization_path=auth_path,
                completion_receipt_path=receipt_path,
            )
            self.assertEqual(tampered["reason"], "COMPLETION_ARTIFACT_HASH_MISMATCH")
            self.assertEqual(validate_authoritative_state(case)["current_stage"], "CASE_INTAKE")

            # A fresh authorization and receipt can commit once, but the same
            # authorization cannot be replayed after the state revision moves.
            authorization = authorize_transition(
                case,
                requested_stage="PROBLEM_UNDERSTANDING",
                executor="test-executor-2",
            )
            auth_path = write_transition_authorization(case, authorization)
            receipt = build_completion_receipt(
                case,
                authorization_path=auth_path,
                artifacts=["problem/problem_contract.md", "problem_analysis.md"],
                executor_status="PASS",
            )
            receipt_path = write_completion_receipt(case, receipt)
            committed = commit_transition(
                case,
                authorization_path=auth_path,
                completion_receipt_path=receipt_path,
            )
            self.assertEqual(committed["status"], "PASS")
            replay = commit_transition(
                case,
                authorization_path=auth_path,
                completion_receipt_path=receipt_path,
            )
            self.assertEqual(replay["status"], "FAIL")
            self.assertEqual(replay["reason"], "AUTHORIZATION_CURRENT_STAGE_MISMATCH")
            self.assertEqual(validate_authoritative_state(case)["state_revision"], 1)

    def test_state_bundle_write_failure_rolls_back_every_state_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="transition_rollback_") as temp:
            case = self.make_case(Path(temp))
            authorization = authorize_transition(
                case,
                requested_stage="PROBLEM_UNDERSTANDING",
                executor="test-executor",
            )
            auth_path = write_transition_authorization(case, authorization)
            receipt = build_completion_receipt(
                case,
                authorization_path=auth_path,
                artifacts=["problem/problem_contract.md", "problem_analysis.md"],
                executor_status="PASS",
            )
            receipt_path = write_completion_receipt(case, receipt)
            config_path = case / "case_config.json"
            mirror_path = case / "CURRENT_HANDOFF.md"
            original_config = config_path.read_text(encoding="utf-8")
            original_mirror = mirror_path.read_text(encoding="utf-8")

            from pipeline_core.python_tools import transition_case as kernel

            original_atomic_write = kernel._atomic_write
            failed = False

            def fail_journal_once(path: Path, text: str) -> None:
                nonlocal failed
                if path.name == "stage_transition_log.jsonl" and not failed:
                    failed = True
                    raise OSError("simulated journal failure")
                original_atomic_write(path, text)

            with patch.object(kernel, "_atomic_write", side_effect=fail_journal_once):
                result = commit_transition(
                    case,
                    authorization_path=auth_path,
                    completion_receipt_path=receipt_path,
                )

            self.assertFalse(result["stage_changed"])
            self.assertTrue(result["reason"].startswith("STATE_BUNDLE_WRITE_FAILED:"))
            self.assertEqual(config_path.read_text(encoding="utf-8"), original_config)
            self.assertEqual(mirror_path.read_text(encoding="utf-8"), original_mirror)
            self.assertFalse((case / "handoff" / "stage_transition_log.jsonl").exists())
            self.assertEqual(validate_authoritative_state(case)["current_stage"], "CASE_INTAKE")
            self.assertEqual(validate_authoritative_state(case)["state_revision"], 0)

    def test_production_python_has_one_current_stage_dict_writer(self) -> None:
        writers: list[str] = []
        excluded_parts = {".git", "__pycache__", "archive", "tests", "work_tmp"}
        candidates = [
            path
            for path in ROOT.rglob("*.py")
            if not excluded_parts.intersection(path.relative_to(ROOT).parts)
        ]
        for path in candidates:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.slice, ast.Constant)
                        and target.slice.value == "current_stage"
                    ):
                        writers.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(writers, ["pipeline_core/python_tools/transition_case.py"])


if __name__ == "__main__":
    unittest.main()
