from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from quality_gates import run_quality_gates
from review.manual_review_audit import audit_manual_review
from validation.evidence_tier import E4_ADVERSARIAL_NEAR_MISS, classify_qg_result
from x5_surgical_utils import ROOT


FIXTURE = ROOT / "quality_gates" / "tests" / "fixtures" / "near_miss_cases" / "disclosed_extrapolation_and_background_ref"


def _rule(report: dict, rule_id: str) -> dict:
    for item in report["results"]:
        if item["rule_id"] == rule_id:
            return item
    raise AssertionError(f"rule not found: {rule_id}")


class NearMissBenchmarkTests(unittest.TestCase):
    def test_near_miss_cases_do_not_escalate_disclosed_risks_to_hard_fail(self) -> None:
        case_dir = ROOT / "work_tmp" / "near_miss_disclosed_runtime"
        if case_dir.exists():
            shutil.rmtree(case_dir)
        shutil.copytree(FIXTURE, case_dir)
        run_dir = ROOT / "work_tmp" / "near_miss_disclosed_run"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        report = run_quality_gates(case_dir=case_dir, run_dir=run_dir, run_id="near_miss_disclosed_run")
        self.assertTrue(report["promotion_allowed"])
        self.assertEqual(report["hard_block_count"], 0)
        self.assertEqual(_rule(report, "QG-EXTRAP-02")["status"], "MANUAL_REVIEW_REQUIRED")
        self.assertEqual(_rule(report, "QG-MODEL-05")["status"], "MANUAL_REVIEW_REQUIRED")
        self.assertEqual(_rule(report, "QG-REF-09")["status"], "MANUAL_REVIEW_REQUIRED")
        self.assertEqual(_rule(report, "QG-ABST-10")["status"], "PASS")

        classified = classify_qg_result(_rule(report, "QG-REF-09"), E4_ADVERSARIAL_NEAR_MISS)
        self.assertEqual(classified["evidence_tier"], E4_ADVERSARIAL_NEAR_MISS)
        self.assertEqual(classified["error_category"], "PAPER_SEMANTIC_MANUAL_REVIEW")

        manual_audit = audit_manual_review(
            run_dir / "output" / "quality" / "qg_report.json",
            run_dir / "output" / "quality" / "manual_review_todo.json",
            run_dir / "output" / "review",
        )
        self.assertGreaterEqual(manual_audit["manual_review_required_count"], 3)
        self.assertGreaterEqual(manual_audit["skipped_count"], 1)
        self.assertTrue(manual_audit["manual_review_required_is_not_auto_pass"])

        manual = json.loads((run_dir / "output" / "quality" / "manual_review_todo.json").read_text(encoding="utf-8"))
        rule_ids = {item["rule_id"] for item in manual["items"]}
        self.assertIn("QG-EXTRAP-02", rule_ids)
        self.assertIn("QG-MODEL-05", rule_ids)
        self.assertIn("QG-REF-09", rule_ids)


if __name__ == "__main__":
    unittest.main()
