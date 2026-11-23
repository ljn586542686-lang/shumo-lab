from __future__ import annotations

import importlib.util
import json
import shutil
import unittest

from math_core.registry import run_model
from pipeline_core.tests.runner_workspace import RunnerWorkspaceTestCase


class MathCoreSmokeTests(RunnerWorkspaceTestCase):
    def test_lp_feasible_or_unsupported_without_scipy(self) -> None:
        result = run_model(
            {
                "model_type": "optimization.linear_program",
                "c": [1, 1],
                "A_ub": [[-1, -1]],
                "b_ub": [-1],
                "bounds": [[0, None], [0, None]],
            },
            run_id="unit_lp",
        )
        if importlib.util.find_spec("scipy"):
            self.assertTrue(result["success"])
            self.assertEqual(result["status"], "solver_reported_optimum")
            self.assertIsNotNone(result["primary_results"]["objective_value"])
        else:
            self.assertFalse(result["success"])
            self.assertEqual(result["status"], "unsupported")
            self.assertGreater(result["fatal_count"], 0)

    def test_regression_normal(self) -> None:
        result = run_model(
            {
                "model_type": "statistics.linear_regression",
                "X": [[1, 2], [2, 1], [3, 4], [4, 3], [5, 5]],
                "y": [6, 7, 14, 15, 20],
            },
            run_id="unit_regression",
        )
        self.assertTrue(result["success"])
        self.assertIn("beta", result["primary_results"])
        self.assertIn("R2", result["metrics"])

    def test_moving_average_normal(self) -> None:
        result = run_model(
            {
                "model_type": "forecasting.moving_average",
                "series": [10, 12, 14, 16, 18, 20, 22, 24],
                "window": 3,
                "horizon": 2,
                "test_size": 2,
            },
            run_id="unit_forecast",
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["primary_results"]["model_label"], "baseline_forecast")
        self.assertEqual(len(result["primary_results"]["forecast"]), 2)

    def test_shortest_path_normal(self) -> None:
        result = run_model(
            {
                "model_type": "graph.shortest_path",
                "edges": [["A", "B", 1], ["B", "C", 2], ["A", "C", 5]],
                "source": "A",
                "target": "C",
            },
            run_id="unit_graph",
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["primary_results"]["shortest_distance"], 3.0)
        self.assertEqual(result["primary_results"]["path"], ["A", "B", "C"])

    def test_runner_model_config_promotes_success(self) -> None:
        case_dir = self.root / "work_tmp" / "math_core_runner_success_fixture"
        if case_dir.exists():
            shutil.rmtree(case_dir)
        (case_dir / "data" / "raw").mkdir(parents=True)
        (case_dir / "data" / "processed").mkdir(parents=True)
        (case_dir / "data" / "raw" / "sample.csv").write_text("ok\n1\n", encoding="utf-8")
        (case_dir / "case_config.json").write_text(json.dumps({"case_id": "math_core_runner_success_fixture"}), encoding="utf-8")
        (case_dir / "data" / "processed" / "regression.csv").write_text(
            "x1,x2,y\n1,2,6\n2,1,7\n3,4,14\n4,3,15\n5,5,20\n",
            encoding="utf-8",
        )
        (case_dir / "model_config.json").write_text(
            json.dumps(
                {
                    "model_type": "statistics.linear_regression",
                    "input_path": "data/processed/regression.csv",
                    "target": "y",
                    "features": ["x1", "x2"],
                }
            ),
            encoding="utf-8",
        )
        result = self.run_case("run", case_dir, 30)
        self.assertEqual(result["status"], "ok")
        promoted = json.loads((case_dir / "output" / "result_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(promoted["model_id"], "statistics.linear_regression")
        self.assertFalse((self.root / "final_submit").exists())


if __name__ == "__main__":
    unittest.main()
