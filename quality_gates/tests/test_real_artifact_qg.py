from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from quality_gates import run_quality_gates
from pipeline_core.tests.runner_workspace import RunnerWorkspaceTestCase


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _copy_fixture(root: Path, name: str, target_name: str) -> Path:
    src = FIXTURES / name
    dst = root / "work_tmp" / target_name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return dst


def _rule(report: dict, rule_id: str) -> dict:
    for item in report["results"]:
        if item["rule_id"] == rule_id:
            return item
    raise AssertionError(f"rule not found: {rule_id}")


class RealArtifactQualityGateTests(RunnerWorkspaceTestCase):
    def test_real_artifact_fixture_exercises_paper_figure_ref_and_abstract(self) -> None:
        case_dir = _copy_fixture(self.root, "real_artifact_case", "real_artifact_case_runtime")
        run_dir = self.root / "work_tmp" / "real_artifact_case_run"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        report = run_quality_gates(root=self.root, case_dir=case_dir, run_dir=run_dir, run_id="real_artifact_qg_run")
        self.assertTrue(report["promotion_allowed"])
        self.assertEqual(report["hard_block_count"], 0)

        fig = _rule(report, "QG-FIG-08")
        self.assertEqual(fig["status"], "PASS")
        self.assertTrue(fig["manual_review_required"])
        self.assertIn("diagnostic_prediction", fig["figure_id"])
        self.assertTrue(any("diagnostic_prediction.png" in item for item in fig["evidence_files"]))

        ref = _rule(report, "QG-REF-09")
        self.assertEqual(ref["status"], "MANUAL_REVIEW_REQUIRED")
        self.assertTrue(ref["manual_review_required"])
        self.assertIn("background_only", ref["message"])

        abst = _rule(report, "QG-ABST-10")
        self.assertEqual(abst["status"], "PASS")
        self.assertTrue(abst["manual_review_required"])

        self.assertEqual(_rule(report, "QG-SYM-06")["status"], "PASS")
        self.assertEqual(_rule(report, "QG-PREP-07")["status"], "PASS")
        self.assertEqual(_rule(report, "QG-MODEL-05")["status"], "MANUAL_REVIEW_REQUIRED")

        manual = json.loads((run_dir / "output" / "quality" / "manual_review_todo.json").read_text(encoding="utf-8"))
        expected = json.loads((case_dir / "expected" / "manual_review_todo_expected.json").read_text(encoding="utf-8"))
        rule_ids = {item["rule_id"] for item in manual["items"]}
        for rule_id in expected["expected_rule_ids"]:
            self.assertIn(rule_id, rule_ids)

    def test_emergency_export_still_does_not_create_final_submit(self) -> None:
        case_dir = _copy_fixture(self.root, "real_artifact_case", "real_artifact_emergency_case")
        result = self.run_case("emergency-export", case_dir, 30)
        self.assertEqual(result["stage"], "emergency-export")
        self.assertFalse(result["final_submit_created"])
        self.assertFalse((self.root / "final_submit").exists())

    def test_hard_block_still_does_not_promote(self) -> None:
        case_dir = _copy_fixture(self.root, "qg_fail_case", "real_artifact_hard_block_case")
        result = self.run_case("validate", case_dir, 30)
        self.assertEqual(result["status"], "fatal")
        self.assertFalse(result["final_submit_created"])
        self.assertFalse((case_dir / "output" / "quality" / "qg_report.json").exists())
        run_dir = self.root / result["run_dir"]
        self.assertTrue((run_dir / "output" / "quality" / "qg_report.json").exists())


if __name__ == "__main__":
    unittest.main()
