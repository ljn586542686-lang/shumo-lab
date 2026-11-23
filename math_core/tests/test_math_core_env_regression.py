from __future__ import annotations

import json
import math
import shutil
import unittest

from math_core.registry import run_model
from pipeline_core.tests.runner_workspace import RunnerWorkspaceTestCase


def _contains_nan_inf(value: object) -> bool:
    if isinstance(value, float):
        return math.isnan(value) or math.isinf(value)
    if isinstance(value, dict):
        return any(_contains_nan_inf(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_nan_inf(child) for child in value)
    return False


class MathCoreEnvRegressionTests(RunnerWorkspaceTestCase):
    def test_numpy_and_scipy_available(self) -> None:
        import numpy
        import scipy
        from scipy.optimize import linprog

        self.assertTrue(numpy.__version__)
        self.assertTrue(scipy.__version__)
        self.assertTrue(callable(linprog))

    def test_lp_normal_fixture_real_solver(self) -> None:
        result = run_model(
            {
                "model_type": "optimization.linear_program",
                "c": [1, 1],
                "A_ub": [[-1, -2]],
                "b_ub": [-2],
                "bounds": [[0, None], [0, None]],
                "constraint_violation_tolerance": 1e-8,
            },
            run_id="env_lp_normal",
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "solver_reported_optimum")
        self.assertEqual(result["fatal_count"], 0)
        self.assertLessEqual(result["primary_results"]["constraint_violation"], 1e-8)
        self.assertIsInstance(result["primary_results"]["x"], list)
        self.assertAlmostEqual(result["primary_results"]["objective_value"], 1.0, places=7)
        self.assertFalse(_contains_nan_inf(result))

    def test_lp_infeasible_fixture_real_solver_fatal(self) -> None:
        result = run_model(
            {
                "model_type": "optimization.linear_program",
                "c": [1],
                "A_ub": [[-1], [1]],
                "b_ub": [-1, 0],
                "bounds": [[None, None]],
            },
            run_id="env_lp_infeasible",
        )
        self.assertFalse(result["success"])
        self.assertGreater(result["fatal_count"], 0)
        self.assertEqual(result["failure_reason"], "infeasible")
        self.assertFalse(_contains_nan_inf(result))

    def test_regression_uses_numpy_path(self) -> None:
        result = run_model(
            {
                "model_type": "statistics.linear_regression",
                "X": [[1, 2], [2, 1], [3, 4], [4, 3], [5, 5]],
                "y": [6, 7, 14, 15, 20],
            },
            run_id="env_regression_numpy",
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["metrics"]["solver"], "numpy_lstsq")
        self.assertFalse(_contains_nan_inf(result))

    def test_forecasting_and_graph_json_clean(self) -> None:
        forecast = run_model(
            {
                "model_type": "forecasting.moving_average",
                "series": [10, 12, 14, 16, 18, 20, 22, 24],
                "window": 3,
                "horizon": 2,
                "test_size": 2,
            },
            run_id="env_forecast",
        )
        graph = run_model(
            {
                "model_type": "graph.shortest_path",
                "edges": [["A", "B", 1], ["B", "C", 2], ["A", "C", 5]],
                "source": "A",
                "target": "C",
            },
            run_id="env_graph",
        )
        self.assertTrue(forecast["success"])
        self.assertTrue(graph["success"])
        self.assertFalse(_contains_nan_inf(forecast))
        self.assertFalse(_contains_nan_inf(graph))

    def test_math_core_fatal_still_no_promotion(self) -> None:
        case_dir = self.root / "work_tmp" / "math_core_env_failure_fixture"
        if case_dir.exists():
            shutil.rmtree(case_dir)
        (case_dir / "data" / "raw").mkdir(parents=True)
        (case_dir / "data" / "raw" / "sample.csv").write_text("ok\n1\n", encoding="utf-8")
        (case_dir / "case_config.json").write_text(json.dumps({"case_id": "math_core_env_failure_fixture"}), encoding="utf-8")
        (case_dir / "output").mkdir()
        (case_dir / "output" / "result_summary.json").write_text(
            json.dumps(
                {
                    "schema_version": "x5_f0_result_summary_v1",
                    "generated_at": "2026-01-01T00:00:00+00:00",
                    "run_id": "previous_good",
                    "case_id": "math_core_env_failure_fixture",
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
        result = self.run_case("run", case_dir, 30)
        self.assertEqual(result["status"], "fatal")
        promoted = json.loads((case_dir / "output" / "result_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(promoted["run_id"], "previous_good")
        run_dir = self.root / result["run_dir"]
        run_summary = json.loads((run_dir / "output" / "result_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(run_summary["failure_reason"], "infeasible")
        self.assertFalse(_contains_nan_inf(run_summary))
        self.assertFalse((self.root / "final_submit").exists())


if __name__ == "__main__":
    unittest.main()
