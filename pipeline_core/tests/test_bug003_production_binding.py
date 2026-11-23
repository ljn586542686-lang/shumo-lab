from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pipeline_core.tests.test_human_gate_decision_semantics import (
    GATE_FILES,
    rewrite_decision,
    write_gate_case,
)


ROOT = Path(__file__).resolve().parents[2]


class Bug003ProductionBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="bug003_production_repo_")
        cls.sandbox = Path(cls.temp_dir.name) / "repo"

        def ignore(_directory: str, names: list[str]) -> set[str]:
            ignored = {
                ".git",
                ".venv",
                ".pytest_cache",
                ".mypy_cache",
                ".ruff_cache",
                "__pycache__",
                "work_tmp",
                "candidate_submit",
                "candidate_emergency",
                "final_submit",
                "tmp",
            }
            return {name for name in names if name in ignored}

        shutil.copytree(ROOT, cls.sandbox, ignore=ignore)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def make_case(self, name: str, *, gate_state: str = "approve", model_type: str = "statistics.linear_regression") -> Path:
        case = self.sandbox / "cases" / name
        write_gate_case(case, 2, 0)
        (case / "case_config.json").write_text(
            json.dumps({"case_id": name}, ensure_ascii=False), encoding="utf-8"
        )
        if gate_state == "pending":
            shutil.copy2(
                self.sandbox / "cases" / "case_template" / "human_review" / GATE_FILES[2],
                case / "human_review" / GATE_FILES[2],
            )
        elif gate_state == "reject":
            rewrite_decision(case, 2, 1)

        (case / "data" / "raw").mkdir(parents=True, exist_ok=True)
        (case / "data" / "processed").mkdir(parents=True, exist_ok=True)
        (case / "data" / "raw" / "sample.csv").write_text("ok\n1\n", encoding="utf-8")
        (case / "data" / "processed" / "regression.csv").write_text(
            "x1,x2,y\n1,2,6\n2,1,7\n3,4,14\n4,3,15\n5,5,20\n",
            encoding="utf-8",
        )
        (case / "model_config.json").write_text(
            json.dumps(
                {
                    "model_type": model_type,
                    "input_path": "data/processed/regression.csv",
                    "target": "y",
                    "features": ["x1", "x2"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return case

    def run_production_action(self, case: Path, *, current_stage: str = "MODEL_IMPLEMENTATION", requested_stage: str = "SOLVER_EXECUTION") -> tuple[subprocess.CompletedProcess[str], dict]:
        command = [
            sys.executable,
            str(self.sandbox / "run_case.py"),
            "--mode",
            "competition",
            "--case-dir",
            str(case),
            "--current-stage",
            current_stage,
            "--requested-stage",
            requested_stage,
            "--stage-action",
            "RUN_MODEL_SOLVER",
            "--authorization-output",
            str(case / "authorization.json"),
            "--timeout-seconds",
            "30",
        ]
        completed = subprocess.run(command, cwd=self.sandbox, capture_output=True, text=True)
        return completed, json.loads(completed.stdout)

    def test_positive_competition_execution_promotes_real_result(self) -> None:
        case = self.make_case("bug003_positive")
        completed, payload = self.run_production_action(case)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(payload["control_decision"], "ALLOWED")
        execution = payload["execution"]
        self.assertTrue(execution["production_execution"])
        self.assertEqual(execution["model_id"], "statistics.linear_regression")
        summary_path = case / "output" / "result_summary.json"
        self.assertTrue(summary_path.is_file())
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual(summary["model_id"], "statistics.linear_regression")
        self.assertEqual(summary["status"], "ok")
        self.assertTrue(summary["success"])
        self.assertTrue(summary["primary_results"])
        self.assertFalse((case / "execution_marker.txt").exists())
        self.assertFalse((self.sandbox / "final_submit").exists())

    def test_gate_reject_blocks_real_production_run(self) -> None:
        case = self.make_case("bug003_reject", gate_state="reject")
        completed, payload = self.run_production_action(case)

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(payload["control_decision"], "BLOCKED")
        self.assertFalse((case / "output" / "result_summary.json").exists())
        self.assertFalse((case / "execution_marker.txt").exists())

    def test_gate_pending_blocks_real_production_run(self) -> None:
        case = self.make_case("bug003_pending", gate_state="pending")
        completed, payload = self.run_production_action(case)

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(payload["control_decision"], "BLOCKED")
        self.assertFalse((case / "output" / "result_summary.json").exists())
        self.assertFalse((case / "execution_marker.txt").exists())

    def test_wrong_stage_blocks_production_action(self) -> None:
        case = self.make_case("bug003_wrong_stage")
        completed, payload = self.run_production_action(
            case,
            current_stage="MODEL_SELECTION",
            requested_stage="MODEL_IMPLEMENTATION",
        )

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(payload["control_decision"], "BLOCKED")
        self.assertEqual(payload["reason"], "ACTION_STAGE_MISMATCH")
        self.assertFalse((case / "output" / "result_summary.json").exists())
        self.assertFalse((case / "execution_marker.txt").exists())

    def test_invalid_model_is_blocked_without_successful_promotion(self) -> None:
        case = self.make_case("bug003_invalid_model", model_type="unregistered.invalid")
        completed, payload = self.run_production_action(case)

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(payload["control_decision"], "BLOCKED")
        self.assertIn("PRODUCTION_MODEL_EXECUTION_FAILED", payload["reason"])
        summary_path = case / "output" / "result_summary.json"
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertFalse(summary.get("success"))
        self.assertFalse((case / "execution_marker.txt").exists())


if __name__ == "__main__":
    unittest.main()
