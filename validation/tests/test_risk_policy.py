from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from run_case import run_case
from validation.risk_policy import HARD_ASSERTION_NAMES, HUMAN_REVIEW_ITEMS, SOFT_WARNING_NAMES, validation_status
from x5_surgical_utils import ROOT


def _snapshot_files(path: Path) -> dict[str, tuple[int, int]]:
    if not path.exists():
        return {}
    return {str(item.relative_to(path)): (item.stat().st_size, item.stat().st_mtime_ns) for item in path.rglob("*") if item.is_file()}


class RiskPolicyAndRunnerTests(unittest.TestCase):
    def test_policy_status(self) -> None:
        self.assertEqual(validation_status(0, 0), "PASS")
        self.assertEqual(validation_status(0, 1), "PASS_WITH_WARNING")
        self.assertEqual(validation_status(1, 0), "FAIL")
        self.assertIn("no_nan_inf", HARD_ASSERTION_NAMES)
        self.assertIn("metric_not_comparable", SOFT_WARNING_NAMES)
        self.assertTrue(HUMAN_REVIEW_ITEMS)

    def test_validation_warning_does_not_block_promotion(self) -> None:
        case_dir = ROOT / "work_tmp" / "f2_warning_promotion_fixture"
        if case_dir.exists():
            shutil.rmtree(case_dir)
        (case_dir / "data" / "raw").mkdir(parents=True)
        (case_dir / "data" / "raw" / "sample.csv").write_text("ok\n1\n", encoding="utf-8")
        (case_dir / "case_config.json").write_text(json.dumps({"case_id": "f2_warning_promotion_fixture"}), encoding="utf-8")
        (case_dir / "model_config.json").write_text(
            json.dumps(
                {
                    "model_type": "forecasting.moving_average",
                    "series": [10, 12, 14, 16, 18, 20, 22, 24],
                    "window": 3,
                    "horizon": 1,
                    "test_size": 2,
                }
            ),
            encoding="utf-8",
        )
        result = run_case("run", case_dir, 30)
        self.assertEqual(result["status"], "ok")
        self.assertTrue((case_dir / "output" / "result_summary.json").exists())
        run_dir = ROOT / result["run_dir"]
        report = json.loads((run_dir / "validation_report.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(report["warning_count"], 1)
        self.assertEqual(report["fatal_count"], 0)

    def test_validation_fatal_blocks_promotion_and_safety(self) -> None:
        archive_before = _snapshot_files(ROOT / "archive")
        cases_before = _snapshot_files(ROOT / "cases")
        case_dir = ROOT / "work_tmp" / "f2_fatal_no_promotion_fixture"
        if case_dir.exists():
            shutil.rmtree(case_dir)
        (case_dir / "data" / "raw").mkdir(parents=True)
        (case_dir / "data" / "raw" / "sample.csv").write_text("ok\n1\n", encoding="utf-8")
        (case_dir / "case_config.json").write_text(json.dumps({"case_id": "f2_fatal_no_promotion_fixture"}), encoding="utf-8")
        (case_dir / "output").mkdir()
        (case_dir / "output" / "result_summary.json").write_text(
            json.dumps(
                {
                    "schema_version": "x5_f0_result_summary_v1",
                    "generated_at": "2026-01-01T00:00:00+00:00",
                    "run_id": "previous_good",
                    "case_id": "f2_fatal_no_promotion_fixture",
                    "data_health_status": "ok",
                    "final_submit_created": False,
                }
            ),
            encoding="utf-8",
        )
        (case_dir / "model_config.json").write_text(
            json.dumps(
                {
                    "model_type": "optimization.linear_program",
                    "c": [1],
                    "A_ub": [[-1], [1]],
                    "b_ub": [-1, 0],
                    "bounds": [[None, None]],
                }
            ),
            encoding="utf-8",
        )
        result = run_case("run", case_dir, 30)
        self.assertEqual(result["status"], "fatal")
        promoted = json.loads((case_dir / "output" / "result_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(promoted["run_id"], "previous_good")
        run_dir = ROOT / result["run_dir"]
        self.assertTrue((run_dir / "validation_report.json").exists())
        self.assertFalse((ROOT / "final_submit").exists())
        self.assertEqual(archive_before, _snapshot_files(ROOT / "archive"))
        self.assertEqual(cases_before, _snapshot_files(ROOT / "cases"))

    def test_no_model_config_f0_old_path_still_passes(self) -> None:
        # 该 F0 兼容性测试原先依赖仓库外的历史 work_tmp 快照；冻结清理后
        # 快照不再保证存在，因此在测试内建立最小、可重复的旧路径夹具。
        (ROOT / "work_tmp").mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="fixture_success_", dir=ROOT / "work_tmp") as directory:
            case_dir = Path(directory)
            (case_dir / "data" / "raw").mkdir(parents=True)
            (case_dir / "data" / "raw" / "sample.csv").write_text("ok\n1\n", encoding="utf-8")
            (case_dir / "case_config.json").write_text(
                json.dumps({"case_id": "fixture_success"}), encoding="utf-8"
            )
            result = run_case("run", case_dir, 30)
            self.assertEqual(result["status"], "ok")
        self.assertFalse((ROOT / "final_submit").exists())


if __name__ == "__main__":
    unittest.main()
