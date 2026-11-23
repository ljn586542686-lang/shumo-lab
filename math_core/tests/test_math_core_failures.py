from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from math_core.registry import run_model
from run_case import run_case
from x5_surgical_utils import ROOT


def _snapshot_files(path: Path) -> dict[str, tuple[int, int]]:
    if not path.exists():
        return {}
    return {str(item.relative_to(path)): (item.stat().st_size, item.stat().st_mtime_ns) for item in path.rglob("*") if item.is_file()}


class MathCoreFailureTests(unittest.TestCase):
    def test_lp_infeasible_or_unsupported(self) -> None:
        result = run_model(
            {
                "model_type": "optimization.linear_program",
                "c": [1],
                "A_ub": [[1], [-1]],
                "b_ub": [0, -1],
                "bounds": [[0, None]],
            },
            run_id="unit_lp_infeasible",
        )
        self.assertFalse(result["success"])
        self.assertGreater(result["fatal_count"], 0)
        self.assertIn(result["failure_reason"], {"infeasible", "scipy_unavailable"})

    def test_regression_nan_fatal(self) -> None:
        result = run_model(
            {
                "model_type": "statistics.linear_regression",
                "X": [[1, 2], [2, float("nan")], [3, 4]],
                "y": [1, 2, 3],
            },
            run_id="unit_regression_nan",
        )
        self.assertFalse(result["success"])
        self.assertGreater(result["fatal_count"], 0)

    def test_moving_average_series_too_short(self) -> None:
        result = run_model(
            {
                "model_type": "forecasting.moving_average",
                "series": [1, 2],
                "window": 3,
                "horizon": 1,
                "test_size": 1,
            },
            run_id="unit_forecast_short",
        )
        self.assertFalse(result["success"])
        self.assertGreater(result["fatal_count"], 0)

    def test_shortest_path_negative_edge_fatal(self) -> None:
        result = run_model(
            {
                "model_type": "graph.shortest_path",
                "edges": [["A", "B", -1]],
                "source": "A",
                "target": "B",
            },
            run_id="unit_graph_negative",
        )
        self.assertFalse(result["success"])
        self.assertGreater(result["fatal_count"], 0)

    def test_unregistered_model_type_fatal(self) -> None:
        result = run_model({"model_type": "unknown.model"}, run_id="unit_unknown")
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "unregistered_model_type")
        self.assertGreater(result["fatal_count"], 0)

    def test_math_core_fatal_no_promotion_and_safety(self) -> None:
        archive_before = _snapshot_files(ROOT / "archive")
        cases_before = _snapshot_files(ROOT / "cases")
        case_dir = ROOT / "work_tmp" / "math_core_runner_failure_fixture"
        if case_dir.exists():
            shutil.rmtree(case_dir)
        (case_dir / "data" / "raw").mkdir(parents=True)
        (case_dir / "data" / "processed").mkdir(parents=True)
        (case_dir / "data" / "raw" / "sample.csv").write_text("ok\n1\n", encoding="utf-8")
        (case_dir / "case_config.json").write_text(json.dumps({"case_id": "math_core_runner_failure_fixture"}), encoding="utf-8")
        (case_dir / "output").mkdir()
        (case_dir / "output" / "result_summary.json").write_text(
            json.dumps(
                {
                    "schema_version": "x5_f0_result_summary_v1",
                    "generated_at": "2026-01-01T00:00:00+00:00",
                    "run_id": "previous_good",
                    "case_id": "math_core_runner_failure_fixture",
                    "data_health_status": "ok",
                    "final_submit_created": False,
                }
            ),
            encoding="utf-8",
        )
        (case_dir / "model_config.json").write_text(
            json.dumps(
                {
                    "model_type": "forecasting.moving_average",
                    "series": [1, 2],
                    "window": 3,
                    "horizon": 1,
                    "test_size": 1,
                }
            ),
            encoding="utf-8",
        )
        result = run_case("run", case_dir, 30)
        self.assertEqual(result["status"], "fatal")
        promoted = json.loads((case_dir / "output" / "result_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(promoted["run_id"], "previous_good")
        run_dir = ROOT / result["run_dir"]
        self.assertTrue((run_dir / "output" / "result_summary.json").exists())
        risk_summary = json.loads((run_dir / "risk_summary.json").read_text(encoding="utf-8"))
        self.assertGreater(risk_summary["fatal_count"], 0)
        self.assertFalse((ROOT / "final_submit").exists())
        self.assertEqual(archive_before, _snapshot_files(ROOT / "archive"))
        self.assertEqual(cases_before, _snapshot_files(ROOT / "cases"))


if __name__ == "__main__":
    unittest.main()
