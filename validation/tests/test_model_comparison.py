from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from validation.comparison import compare_result_summaries, write_model_comparison


class ModelComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def _write_summary(self, name: str, payload: dict) -> Path:
        base = self.workspace / "comparison_inputs"
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def test_same_family_forecasting_sorted_by_rmse(self) -> None:
        first = self._write_summary(
            "forecast_a",
            {
                "run_id": "forecast_a",
                "model_id": "forecasting.moving_average",
                "model_family": "forecasting",
                "success": True,
                "status": "baseline_forecast",
                "fatal_count": 0,
                "metrics": {"RMSE": 2.0, "MAE": 1.0, "MAPE": 0.1},
                "primary_results": {"forecast": [1]},
            },
        )
        second = self._write_summary(
            "forecast_b",
            {
                "run_id": "forecast_b",
                "model_id": "forecasting.moving_average",
                "model_family": "forecasting",
                "success": True,
                "status": "baseline_forecast",
                "fatal_count": 0,
                "metrics": {"RMSE": 1.0, "MAE": 0.5, "MAPE": 0.1},
                "primary_results": {"forecast": [1]},
            },
        )
        report = compare_result_summaries([first, second])
        self.assertEqual(report["based_on_metric"], "RMSE")
        self.assertEqual(report["best_candidate"], "forecast_b")
        self.assertEqual(report["candidates"][0]["candidate_id"], "forecast_b")

    def test_mixed_family_not_ranked(self) -> None:
        forecast = self._write_summary(
            "mixed_forecast",
            {
                "run_id": "mixed_forecast",
                "model_id": "forecasting.moving_average",
                "model_family": "forecasting",
                "success": True,
                "status": "baseline_forecast",
                "fatal_count": 0,
                "metrics": {"RMSE": 1.0},
                "primary_results": {"forecast": [1]},
            },
        )
        graph = self._write_summary(
            "mixed_graph",
            {
                "run_id": "mixed_graph",
                "model_id": "graph.shortest_path",
                "model_family": "graph",
                "success": True,
                "status": "ok",
                "fatal_count": 0,
                "metrics": {"shortest_distance": 3.0},
                "primary_results": {"shortest_distance": 3.0, "path": ["A", "B"]},
            },
        )
        report = compare_result_summaries([forecast, graph])
        self.assertEqual(report["family_mode"], "mixed_family")
        self.assertIsNone(report["best_candidate"])
        self.assertTrue(any("metric_not_comparable" in item for item in report["notes"]))

    def test_write_model_comparison_outputs_files(self) -> None:
        path = self._write_summary(
            "comparison_write",
            {
                "run_id": "comparison_write",
                "model_id": "graph.shortest_path",
                "model_family": "graph",
                "success": True,
                "status": "ok",
                "fatal_count": 0,
                "metrics": {"shortest_distance": 3.0},
                "primary_results": {"shortest_distance": 3.0, "path": ["A", "B"]},
            },
        )
        out_dir = self.workspace / "comparison_output"
        report = write_model_comparison([path], out_dir)
        self.assertEqual(report["best_candidate"], "comparison_write")
        self.assertTrue((out_dir / "model_comparison.json").exists())
        self.assertTrue((out_dir / "model_comparison.md").exists())


if __name__ == "__main__":
    unittest.main()
