from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from validation.result_summary_adapter import adapt_result_summary, detect_legacy_schema
from validation.unified_validation_report import write_unified_validation_report
from x5_surgical_utils import ROOT


class ResultSummaryAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case_dir = ROOT / "work_tmp" / "adapter_legacy_case"
        if self.case_dir.exists():
            shutil.rmtree(self.case_dir)
        (self.case_dir / "output" / "tables").mkdir(parents=True)
        (self.case_dir / "output" / "tables" / "top.csv").write_text("id,score\nS229,0.93\n", encoding="utf-8")
        self.source_path = self.case_dir / "output" / "result_summary.json"
        self.source_payload = {
            "case_id": "adapter_legacy_case",
            "case_status": "PASS_WITH_LIMITATION / LEGACY",
            "generated_at": "2026-01-01T00:00:00+00:00",
            "source_file": "data/raw/sample.xlsx",
            "supplier_count": 402,
            "week_count": 240,
            "top50_count": 50,
            "top_supplier": {
                "supplier_id": "S229",
                "baseline_importance_score": 0.931818,
                "fulfillment_rate": 0.986112,
                "rank": 1,
            },
            "validation_passed": True,
            "final_submit_allowed": False,
        }
        self.source_path.write_text(json.dumps(self.source_payload), encoding="utf-8")

    def test_detects_legacy_missing_fields(self) -> None:
        detected = detect_legacy_schema(self.source_path)
        self.assertTrue(detected["legacy_schema_detected"])
        self.assertIn("run_id", detected["missing_unified_validation_fields"])
        self.assertIn("model_outputs", detected["missing_minimum_fields"])

    def test_adapter_writes_adapted_copy_without_modifying_source(self) -> None:
        source_before = self.source_path.read_text(encoding="utf-8")
        output_dir = ROOT / "work_tmp" / "adapter_legacy_run"
        if output_dir.exists():
            shutil.rmtree(output_dir)
        report = adapt_result_summary(self.source_path, output_dir, synthetic_run_id="synthetic_adapter_run")
        adapted_path = output_dir / "adapted_result_summary.json"
        self.assertTrue(adapted_path.exists())
        self.assertTrue((output_dir / "result_summary_adapter_report.json").exists())
        self.assertEqual(source_before, self.source_path.read_text(encoding="utf-8"))
        adapted = json.loads(adapted_path.read_text(encoding="utf-8"))
        self.assertTrue(adapted["schema_adapted"])
        self.assertEqual(adapted["run_id"], "synthetic_adapter_run")
        self.assertEqual(adapted["case_id"], "adapter_legacy_case")
        self.assertEqual(adapted["fatal_count"], 0)
        self.assertEqual(adapted["metrics"]["baseline_importance_score"], 0.931818)
        self.assertIn("legacy_source", adapted)
        self.assertTrue(adapted["legacy_source"]["sha256"])
        self.assertIn("run_id missing", " ".join(adapted["warnings"]))
        self.assertTrue(report["validation_continue_allowed"])

    def test_adapted_summary_allows_unified_validation_to_continue(self) -> None:
        output_dir = ROOT / "work_tmp" / "adapter_unified_run"
        if output_dir.exists():
            shutil.rmtree(output_dir)
        adapt_result_summary(self.source_path, output_dir, synthetic_run_id="synthetic_adapter_run")
        unified = write_unified_validation_report(output_dir, output_dir / "adapted_result_summary.json")
        self.assertEqual(unified["counts"]["hard_fatal"], 0)
        self.assertTrue(unified["schema_adapted"])
        self.assertTrue(unified["legacy_source"]["sha256"])
        self.assertIn(unified["validation_status"], {"PASS", "PASS_WITH_WARNING"})

    def test_unreadable_source_remains_fatal(self) -> None:
        output_dir = ROOT / "work_tmp" / "adapter_missing_run"
        if output_dir.exists():
            shutil.rmtree(output_dir)
        report = adapt_result_summary(self.case_dir / "output" / "missing_result_summary.json", output_dir)
        adapted = json.loads((output_dir / "adapted_result_summary.json").read_text(encoding="utf-8"))
        self.assertFalse(report["validation_continue_allowed"])
        self.assertGreaterEqual(adapted["fatal_count"], 1)
        self.assertIsNone(adapted["case_id"])


if __name__ == "__main__":
    unittest.main()
