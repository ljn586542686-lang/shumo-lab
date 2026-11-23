from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from run_case import run_case
from validation.unified_validation_report import write_unified_validation_report
from x5_surgical_utils import ROOT


def _snapshot_files(path: Path) -> dict[str, tuple[int, int]]:
    if not path.exists():
        return {}
    return {str(item.relative_to(path)): (item.stat().st_size, item.stat().st_mtime_ns) for item in path.rglob("*") if item.is_file()}


def _base_case(case_name: str, *, model_config: dict | None = None) -> Path:
    case_dir = ROOT / "work_tmp" / case_name
    if case_dir.exists():
        shutil.rmtree(case_dir)
    (case_dir / "data" / "raw").mkdir(parents=True)
    (case_dir / "data" / "raw" / "sample.csv").write_text("ok\n1\n", encoding="utf-8")
    (case_dir / "case_config.json").write_text(json.dumps({"case_id": case_name}), encoding="utf-8")
    if model_config is not None:
        (case_dir / "model_config.json").write_text(json.dumps(model_config), encoding="utf-8")
    return case_dir


class F21EvidencePatchTests(unittest.TestCase):
    def test_success_fixture_generates_evidence_map_and_paper_human_review(self) -> None:
        case_dir = _base_case(
            "f2_1_success_evidence_fixture",
            model_config={
                "model_type": "forecasting.moving_average",
                "series": [10, 12, 14, 16, 18, 20, 22, 24],
                "window": 3,
                "horizon": 1,
                "test_size": 2,
            },
        )
        (case_dir / "paper" / "draft").mkdir(parents=True)
        (case_dir / "paper" / "draft" / "main.tex").write_text(
            "The forecast value is 23.5 and the error rate is 4.2%.",
            encoding="utf-8",
        )
        result = run_case("run", case_dir, 30)
        self.assertEqual(result["status"], "ok")
        run_dir = ROOT / result["run_dir"]
        evidence_path = run_dir / "evidence_map.json"
        self.assertTrue(evidence_path.exists())
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertGreater(evidence["item_count"], 0)
        first = evidence["items"][0]
        self.assertTrue(first["sha256"])
        self.assertEqual(first["run_id"], result["run_id"])
        self.assertTrue(first["artifact_path"])
        self.assertTrue((ROOT / "manifests" / "evidence_map.json").exists())

        paper_candidates = json.loads((run_dir / "paper_number_candidates.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(paper_candidates["candidate_count"], 2)
        self.assertTrue(all(item["classification"] == "HUMAN_REVIEW" and item["fatal"] is False for item in paper_candidates["items"]))
        unified = json.loads((run_dir / "unified_validation_report.json").read_text(encoding="utf-8"))
        self.assertEqual(unified["counts"]["hard_fatal"], 0)
        self.assertGreater(unified["counts"]["human_review"], 0)
        validation_report = json.loads((run_dir / "validation_report.json").read_text(encoding="utf-8"))
        self.assertIn("f2_1_unified_categories", validation_report)

    def test_nan_inf_triggers_hard_fatal(self) -> None:
        run_dir = ROOT / "work_tmp" / "f2_1_nan_inf_run"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        (run_dir / "output").mkdir(parents=True)
        payload = {
            "run_id": "f2_1_nan_inf_run",
            "success": True,
            "status": "ok",
            "metrics": {"RMSE": float("nan")},
            "primary_results": {"value": 1},
            "risk_items": [],
            "fatal_count": 0,
        }
        (run_dir / "output" / "result_summary.json").write_text(json.dumps(payload, allow_nan=True), encoding="utf-8")
        report = write_unified_validation_report(run_dir, run_dir / "output" / "result_summary.json")
        self.assertEqual(report["validation_status"], "FAIL")
        codes = {item["code"] for item in report["categories"]["HARD_FATAL"]}
        self.assertIn("nan_inf_detected", codes)

    def test_fatal_run_keeps_previous_output_and_work_tmp_evidence_only(self) -> None:
        case_dir = _base_case(
            "f2_1_fatal_no_promotion_fixture",
            model_config={
                "model_type": "optimization.linear_program",
                "c": [1],
                "A_ub": [[-1], [1]],
                "b_ub": [-1, 0],
                "bounds": [[None, None]],
            },
        )
        (case_dir / "output").mkdir()
        (case_dir / "output" / "result_summary.json").write_text(
            json.dumps(
                {
                    "schema_version": "x5_f0_result_summary_v1",
                    "generated_at": "2026-01-01T00:00:00+00:00",
                    "run_id": "previous_good",
                    "case_id": "f2_1_fatal_no_promotion_fixture",
                    "data_health_status": "ok",
                    "final_submit_created": False,
                }
            ),
            encoding="utf-8",
        )
        result = run_case("run", case_dir, 30)
        self.assertEqual(result["status"], "fatal")
        promoted = json.loads((case_dir / "output" / "result_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(promoted["run_id"], "previous_good")
        run_dir = ROOT / result["run_dir"]
        self.assertTrue((run_dir / "evidence_map.json").exists())
        self.assertFalse((case_dir / "output" / "evidence_map.json").exists())

    def test_candidate_and_emergency_do_not_create_final_submit(self) -> None:
        case_dir = _base_case("f2_1_candidate_fixture")
        (case_dir / "output").mkdir()
        (case_dir / "output" / "result_summary.json").write_text(
            json.dumps(
                {
                    "schema_version": "x5_f0_result_summary_v1",
                    "generated_at": "2026-01-01T00:00:00+00:00",
                    "run_id": "candidate_source",
                    "case_id": "f2_1_candidate_fixture",
                    "data_health_status": "ok",
                    "final_submit_created": False,
                }
            ),
            encoding="utf-8",
        )
        package_result = run_case("package", case_dir, 30)
        emergency_result = run_case("emergency-export", case_dir, 30)
        self.assertEqual(package_result["final_submit_created"], False)
        self.assertEqual(emergency_result["final_submit_created"], False)
        self.assertFalse((ROOT / "final_submit").exists())

    def test_no_model_config_f0_old_path_still_passes_and_safety_boundaries(self) -> None:
        archive_before = _snapshot_files(ROOT / "archive")
        cases_before = _snapshot_files(ROOT / "cases")
        case_dir = _base_case("f2_1_no_model_config_fixture")
        result = run_case("run", case_dir, 30)
        self.assertEqual(result["status"], "ok")
        self.assertFalse((ROOT / "final_submit").exists())
        self.assertEqual(archive_before, _snapshot_files(ROOT / "archive"))
        self.assertEqual(cases_before, _snapshot_files(ROOT / "cases"))


if __name__ == "__main__":
    unittest.main()
