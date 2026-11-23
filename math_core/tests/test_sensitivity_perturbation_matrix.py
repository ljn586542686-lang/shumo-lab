"""Tests for math_core.sensitivity.perturbation_matrix (X5-F5.2).

Test groups (original 9 + new 4 = 13 total):
1.  basic_success           — baseline metric + 2 problem params → results + ranking
2.  normalized_delta        — formula: abs(delta) / max(abs(baseline), epsilon)
3.  baseline_near_zero      — epsilon prevents inf / ZeroDivisionError
4.  grid_record             — perturbation_scale, absolute_or_relative, sampling_method, random_seed
5.  solver_hyperparams      — method/maxiter/tol rejected, not silently executed
6.  single_model_lock       — multi_model_results → failure / manual_review_required
7.  ranking_tie             — tie_tolerance marks same ranking_group
8.  output_files            — sensitivity_report.json and .csv generated
9.  forbidden_paths         — no final_submit/, no cases/ or archive/ modification
10. failure_contract        — failed perturbation runs excluded from ranking aggregation
11. run_counts              — run_counts.total/success/failed/excluded_from_ranking
12. reproducibility_fields  — sampling_method, random_seed, tie_tolerance, raw_nd_values, normalized_delta
13. nested_hyperparam_guard — solver_hyperparameters.method, model.method, maxiter all rejected
14. disclaimer_content      — ranking_disclaimer mentions local/non-causal/non-deterministic
"""
from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path


# ---------------------------------------------------------------------------
# Shared helper: simple deterministic model runner
# ---------------------------------------------------------------------------

def _make_sum_of_squares_runner(targets: list[float]):
    """Return a runner that computes sum((x_i - t_i)^2) for given targets."""
    def runner(params: dict) -> float:
        x = params.get("x", [0.0])
        t = targets
        return sum((xi - ti) ** 2 for xi, ti in zip(x, t))
    return runner


def _make_scalar_runner():
    """Runner: returns float(params.get('a', 0.0))."""
    return lambda p: float(p.get("a", 0.0))


def _failing_runner(params: dict) -> float:
    """Runner that always raises RuntimeError."""
    raise RuntimeError("deliberate_failure")


# ---------------------------------------------------------------------------
# Group 1: basic_success
# ---------------------------------------------------------------------------

class SensitivityBasicSuccessTests(unittest.TestCase):
    """Test group 1: basic success with two problem parameters."""

    def _run(self, config: dict) -> dict:
        from math_core.sensitivity.perturbation_matrix import run_perturbation_matrix
        return run_perturbation_matrix(config, run_id="test_sens_basic")

    def test_two_params_produces_results_and_ranking(self) -> None:
        runner = _make_sum_of_squares_runner([1.0, 2.0])
        result = self._run({
            "model_runner": runner,
            "baseline_params": {"x": [1.0, 2.0]},
            "metric_name": "objective_value",
            "parameter_grid": [
                {
                    "parameter_name": "x",
                    "baseline_value": [1.0, 2.0],
                    "perturbation_type": "grid_value",
                    "values": [[2.0, 2.0], [3.0, 2.0]],
                    "absolute_or_relative": "absolute",
                    "perturbation_scale": 1.0,
                },
            ],
        })
        self.assertTrue(result["success"], msg=result.get("failure_reason"))
        self.assertIn("parameter_ranking", result)
        self.assertIn("perturbation_results", result)
        self.assertGreater(len(result["perturbation_results"]), 0)
        self.assertGreater(len(result["parameter_ranking"]), 0)

    def test_report_contains_required_top_level_keys(self) -> None:
        runner = _make_sum_of_squares_runner([0.0])
        result = self._run({
            "model_runner": runner,
            "baseline_params": {"x": [0.0]},
            "metric_name": "objective_value",
            "parameter_grid": [
                {
                    "parameter_name": "x",
                    "baseline_value": [0.0],
                    "perturbation_type": "grid_value",
                    "values": [[1.0], [2.0]],
                    "absolute_or_relative": "absolute",
                    "perturbation_scale": 1.0,
                },
            ],
        })
        for key in ("schema_version", "baseline_metric", "parameter_ranking",
                    "perturbation_results", "limitations", "manual_review_required",
                    "ranking_disclaimer", "evidence_tier", "metadata", "run_counts",
                    "parameter_grid"):
            self.assertIn(key, result, msg=f"missing key: {key}")

    def test_metadata_flags_are_false(self) -> None:
        runner = _make_sum_of_squares_runner([0.0])
        result = self._run({
            "model_runner": runner,
            "baseline_params": {"x": [0.0]},
            "metric_name": "objective_value",
            "parameter_grid": [
                {
                    "parameter_name": "x",
                    "baseline_value": [0.0],
                    "perturbation_type": "grid_value",
                    "values": [[1.0]],
                    "absolute_or_relative": "absolute",
                    "perturbation_scale": 1.0,
                },
            ],
        })
        meta = result["metadata"]
        self.assertFalse(meta["model_tournament_performed"])
        self.assertFalse(meta["automl_performed"])
        self.assertFalse(meta["academic_validity_judged"])


# ---------------------------------------------------------------------------
# Group 2: normalized_delta
# ---------------------------------------------------------------------------

class SensitivityNormalizedDeltaTests(unittest.TestCase):
    """Test group 2: normalized_delta formula correctness."""

    def test_normalized_delta_formula(self) -> None:
        from math_core.sensitivity.perturbation_matrix import _normalized_delta
        epsilon = 1e-12
        nd = _normalized_delta(3.0, 2.0, epsilon)
        self.assertAlmostEqual(nd, 0.5, places=10)

    def test_normalized_delta_negative_baseline(self) -> None:
        from math_core.sensitivity.perturbation_matrix import _normalized_delta
        epsilon = 1e-12
        nd = _normalized_delta(-2.0, -4.0, epsilon)
        self.assertAlmostEqual(nd, 0.5, places=10)

    def test_normalized_delta_zero_delta(self) -> None:
        from math_core.sensitivity.perturbation_matrix import _normalized_delta
        nd = _normalized_delta(5.0, 5.0, 1e-12)
        self.assertEqual(nd, 0.0)


# ---------------------------------------------------------------------------
# Group 3: baseline_near_zero
# ---------------------------------------------------------------------------

class SensitivityBaselineNearZeroTests(unittest.TestCase):
    """Test group 3: epsilon prevents inf/ZeroDivisionError when baseline ≈ 0."""

    def test_baseline_zero_uses_epsilon(self) -> None:
        from math_core.sensitivity.perturbation_matrix import run_perturbation_matrix
        runner = lambda p: p.get("offset", 0.0)
        result = run_perturbation_matrix(
            {
                "model_runner": runner,
                "baseline_params": {"offset": 0.0},
                "metric_name": "offset",
                "epsilon": 1e-12,
                "parameter_grid": [
                    {
                        "parameter_name": "offset",
                        "baseline_value": 0.0,
                        "perturbation_type": "grid_value",
                        "values": [1e-6],
                        "absolute_or_relative": "absolute",
                        "perturbation_scale": 1e-6,
                    },
                ],
            },
            run_id="test_sens_zero",
        )
        self.assertTrue(result["success"])
        row = result["perturbation_results"][0]
        self.assertIsNotNone(row["normalized_delta"])
        self.assertFalse(math.isinf(row["normalized_delta"]))
        self.assertFalse(math.isnan(row["normalized_delta"]))

    def test_no_division_error_raised(self) -> None:
        from math_core.sensitivity.perturbation_matrix import run_perturbation_matrix
        runner = lambda p: 0.0
        try:
            run_perturbation_matrix(
                {
                    "model_runner": runner,
                    "baseline_params": {},
                    "metric_name": "const",
                    "parameter_grid": [
                        {
                            "parameter_name": "dummy",
                            "baseline_value": 0.0,
                            "perturbation_type": "grid_value",
                            "values": [1.0],
                            "absolute_or_relative": "absolute",
                            "perturbation_scale": 1.0,
                        },
                    ],
                },
                run_id="test_sens_zerodiv",
            )
        except (ZeroDivisionError, FloatingPointError) as exc:
            self.fail(f"run_perturbation_matrix raised {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Group 4: grid_record
# ---------------------------------------------------------------------------

class SensitivityGridRecordTests(unittest.TestCase):
    """Test group 4: perturbation_scale, absolute_or_relative, sampling_method, random_seed recorded."""

    def test_grid_record_fields_present(self) -> None:
        from math_core.sensitivity.perturbation_matrix import run_perturbation_matrix
        runner = lambda p: float(p.get("a", 0.0)) ** 2
        result = run_perturbation_matrix(
            {
                "model_runner": runner,
                "baseline_params": {"a": 2.0},
                "metric_name": "a_squared",
                "sampling_method": "OAT",
                "random_seed": None,
                "parameter_grid": [
                    {
                        "parameter_name": "a",
                        "baseline_value": 2.0,
                        "perturbation_type": "absolute_offset",
                        "values": [1.0, 3.0, 4.0],
                        "absolute_or_relative": "absolute",
                        "perturbation_scale": 0.5,
                    },
                ],
            },
            run_id="test_sens_grid",
        )
        self.assertTrue(result["success"])
        for row in result["perturbation_results"]:
            self.assertIn("perturbation_scale", row)
            self.assertIn("absolute_or_relative", row)
            self.assertIn("sampling_method", row)
            self.assertIn("random_seed", row)
            self.assertEqual(row["sampling_method"], "OAT")
            self.assertIsNone(row["random_seed"])

    def test_random_seed_recorded_when_sampling_random(self) -> None:
        from math_core.sensitivity.perturbation_matrix import run_perturbation_matrix
        runner = lambda p: float(p.get("a", 0.0))
        result = run_perturbation_matrix(
            {
                "model_runner": runner,
                "baseline_params": {"a": 1.0},
                "metric_name": "a",
                "sampling_method": "random",
                "random_seed": 42,
                "parameter_grid": [
                    {
                        "parameter_name": "a",
                        "baseline_value": 1.0,
                        "perturbation_type": "grid_value",
                        "values": [2.0, 3.0, 4.0],
                        "absolute_or_relative": "absolute",
                        "perturbation_scale": 1.0,
                    },
                ],
            },
            run_id="test_sens_rand",
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["random_seed"], 42)
        for row in result["perturbation_results"]:
            self.assertEqual(row["random_seed"], 42)


# ---------------------------------------------------------------------------
# Group 5: solver_hyperparams (plain names)
# ---------------------------------------------------------------------------

class SensitivityHyperparamGuardTests(unittest.TestCase):
    """Test group 5: solver_hyperparameters are rejected, not silently executed."""

    def _run(self, config: dict) -> dict:
        from math_core.sensitivity.perturbation_matrix import run_perturbation_matrix
        return run_perturbation_matrix(config, run_id="test_sens_hyperparam")

    def _base_config(self, param_name: str) -> dict:
        runner = lambda p: float(p.get("x", 0.0))
        return {
            "model_runner": runner,
            "baseline_params": {"x": 1.0},
            "metric_name": "x",
            "parameter_grid": [
                {
                    "parameter_name": param_name,
                    "baseline_value": "SLSQP",
                    "perturbation_type": "grid_value",
                    "values": ["L-BFGS-B", "COBYLA"],
                    "absolute_or_relative": "absolute",
                    "perturbation_scale": None,
                },
            ],
        }

    def test_method_rejected(self) -> None:
        result = self._run(self._base_config("method"))
        rejected_names = [r["parameter_name"] for r in result["rejected_params"]]
        self.assertIn("method", rejected_names)

    def test_maxiter_rejected(self) -> None:
        result = self._run(self._base_config("maxiter"))
        rejected_names = [r["parameter_name"] for r in result["rejected_params"]]
        self.assertIn("maxiter", rejected_names)

    def test_tol_rejected(self) -> None:
        result = self._run(self._base_config("tol"))
        rejected_names = [r["parameter_name"] for r in result["rejected_params"]]
        self.assertIn("tol", rejected_names)

    def test_rejected_param_in_manual_review(self) -> None:
        result = self._run(self._base_config("solver"))
        self.assertTrue(
            any("hyperparameter" in msg for msg in result["manual_review_required"]),
            msg="manual_review_required must mention hyperparameter rejection",
        )

    def test_hyperparameter_not_in_perturbation_results(self) -> None:
        result = self._run(self._base_config("maxiter"))
        perturbed_names = {r["parameter_name"] for r in result["perturbation_results"]}
        self.assertNotIn("maxiter", perturbed_names,
                         msg="maxiter must NOT appear in perturbation_results")


# ---------------------------------------------------------------------------
# Group 6: single_model_lock
# ---------------------------------------------------------------------------

class SensitivitySingleModelLockTests(unittest.TestCase):
    """Test group 6: multi_model_results triggers failure, not tournament."""

    def test_multi_model_results_rejected(self) -> None:
        from math_core.sensitivity.perturbation_matrix import run_perturbation_matrix
        runner = lambda p: 0.0
        result = run_perturbation_matrix(
            {
                "model_runner": runner,
                "baseline_params": {},
                "metric_name": "obj",
                "multi_model_results": [{"model_id": "A"}, {"model_id": "B"}],
                "parameter_grid": [
                    {
                        "parameter_name": "x",
                        "baseline_value": 0.0,
                        "perturbation_type": "grid_value",
                        "values": [1.0],
                        "absolute_or_relative": "absolute",
                        "perturbation_scale": 1.0,
                    },
                ],
            },
            run_id="test_sens_multimodel",
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["failure_reason"], "multi_model_tournament_rejected")
        self.assertTrue(len(result["manual_review_required"]) > 0)

    def test_no_best_model_key_in_output(self) -> None:
        from math_core.sensitivity.perturbation_matrix import run_perturbation_matrix
        runner = lambda p: float(p.get("x", 0.0))
        result = run_perturbation_matrix(
            {
                "model_runner": runner,
                "baseline_params": {"x": 1.0},
                "metric_name": "x",
                "parameter_grid": [
                    {
                        "parameter_name": "x",
                        "baseline_value": 1.0,
                        "perturbation_type": "grid_value",
                        "values": [2.0],
                        "absolute_or_relative": "absolute",
                        "perturbation_scale": 1.0,
                    },
                ],
            },
            run_id="test_sens_nobestmodel",
        )
        for forbidden_key in ("best_model", "winner", "best_by_metric"):
            self.assertNotIn(forbidden_key, result,
                             msg=f"'{forbidden_key}' must not appear in sensitivity output")


# ---------------------------------------------------------------------------
# Group 7: ranking_tie
# ---------------------------------------------------------------------------

class SensitivityRankingTieTests(unittest.TestCase):
    """Test group 7: tie_tolerance causes same ranking_group assignment."""

    def test_tie_same_ranking_group(self) -> None:
        from math_core.sensitivity.perturbation_matrix import run_perturbation_matrix
        runner = lambda p: float(p.get("a", 1.0)) * 2.0 + float(p.get("b", 1.0)) * 2.0
        baseline = {"a": 1.0, "b": 1.0}
        result = run_perturbation_matrix(
            {
                "model_runner": runner,
                "baseline_params": baseline,
                "metric_name": "sum",
                "tie_tolerance": 0.1,
                "parameter_grid": [
                    {
                        "parameter_name": "a",
                        "baseline_value": 1.0,
                        "perturbation_type": "grid_value",
                        "values": [2.0],
                        "absolute_or_relative": "absolute",
                        "perturbation_scale": 1.0,
                    },
                    {
                        "parameter_name": "b",
                        "baseline_value": 1.0,
                        "perturbation_type": "grid_value",
                        "values": [2.0],
                        "absolute_or_relative": "absolute",
                        "perturbation_scale": 1.0,
                    },
                ],
            },
            run_id="test_sens_tie",
        )
        self.assertTrue(result["success"])
        ranking = result["parameter_ranking"]
        self.assertEqual(len(ranking), 2)
        groups = {r["ranking_group"] for r in ranking}
        self.assertEqual(groups, {1}, msg="Both params should be in ranking_group 1 (tied)")

    def test_ranking_preserves_raw_values(self) -> None:
        from math_core.sensitivity.perturbation_matrix import run_perturbation_matrix
        runner = lambda p: float(p.get("a", 0.0))
        result = run_perturbation_matrix(
            {
                "model_runner": runner,
                "baseline_params": {"a": 1.0},
                "metric_name": "a",
                "parameter_grid": [
                    {
                        "parameter_name": "a",
                        "baseline_value": 1.0,
                        "perturbation_type": "grid_value",
                        "values": [2.0, 3.0],
                        "absolute_or_relative": "absolute",
                        "perturbation_scale": 1.0,
                    },
                ],
            },
            run_id="test_sens_rawvals",
        )
        self.assertTrue(result["success"])
        ranking = result["parameter_ranking"]
        self.assertGreater(len(ranking), 0)
        self.assertIn("raw_normalized_delta_values", ranking[0])
        self.assertIsInstance(ranking[0]["raw_normalized_delta_values"], list)
        self.assertGreater(len(ranking[0]["raw_normalized_delta_values"]), 0)


# ---------------------------------------------------------------------------
# Group 8: output_files
# ---------------------------------------------------------------------------

class SensitivityOutputFileTests(unittest.TestCase):
    """Test group 8: sensitivity_report.json and .csv are generated correctly."""

    def test_write_json_and_csv(self) -> None:
        from math_core.sensitivity.perturbation_matrix import (
            run_perturbation_matrix,
            write_sensitivity_report,
        )
        runner = lambda p: float(p.get("x", 0.0)) ** 2
        report = run_perturbation_matrix(
            {
                "model_runner": runner,
                "baseline_params": {"x": 3.0},
                "metric_name": "x_squared",
                "parameter_grid": [
                    {
                        "parameter_name": "x",
                        "baseline_value": 3.0,
                        "perturbation_type": "grid_value",
                        "values": [1.0, 5.0],
                        "absolute_or_relative": "absolute",
                        "perturbation_scale": 2.0,
                    },
                ],
            },
            run_id="test_sens_files",
        )
        self.assertTrue(report["success"])

        with tempfile.TemporaryDirectory() as tmpdir:
            json_path, csv_path = write_sensitivity_report(report, tmpdir)
            self.assertTrue(json_path.exists(), "sensitivity_report.json not created")
            self.assertTrue(csv_path.exists(), "sensitivity_report.csv not created")

            loaded = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["schema_version"], "x5_f5_2_sensitivity_v1")
            self.assertIn("ranking_disclaimer", loaded)
            self.assertIn("parameter_ranking", loaded)
            self.assertIn("perturbation_results", loaded)

            csv_text = csv_path.read_text(encoding="utf-8")
            self.assertIn("parameter_name", csv_text)
            self.assertIn("normalized_delta", csv_text)

    def test_json_contains_ranking_disclaimer(self) -> None:
        from math_core.sensitivity.perturbation_matrix import (
            run_perturbation_matrix,
            write_sensitivity_report,
            RANKING_DISCLAIMER,
        )
        runner = lambda p: 0.0
        report = run_perturbation_matrix(
            {
                "model_runner": runner,
                "baseline_params": {"x": 1.0},
                "metric_name": "const",
                "parameter_grid": [
                    {
                        "parameter_name": "x",
                        "baseline_value": 1.0,
                        "perturbation_type": "grid_value",
                        "values": [2.0],
                        "absolute_or_relative": "absolute",
                        "perturbation_scale": 1.0,
                    },
                ],
            },
            run_id="test_sens_disclaimer",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path, _ = write_sensitivity_report(report, tmpdir)
            loaded = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["ranking_disclaimer"], RANKING_DISCLAIMER)

    def test_json_top_level_parameter_grid(self) -> None:
        from math_core.sensitivity.perturbation_matrix import (
            run_perturbation_matrix,
            write_sensitivity_report,
        )
        grid_spec = {
            "parameter_name": "x",
            "baseline_value": 3.0,
            "perturbation_type": "grid_value",
            "values": [1.0, 5.0],
            "absolute_or_relative": "absolute",
            "perturbation_scale": 2.0,
        }
        runner = lambda p: float(p.get("x", 0.0)) ** 2
        report = run_perturbation_matrix(
            {
                "model_runner": runner,
                "baseline_params": {"x": 3.0},
                "metric_name": "x_squared",
                "parameter_grid": [grid_spec],
            },
            run_id="test_sens_pg_toplevel",
        )
        # In-memory: parameter_grid at top level
        self.assertIn("parameter_grid", report,
                      "report dict must contain top-level 'parameter_grid'")
        self.assertIsInstance(report["parameter_grid"], list)
        self.assertEqual(len(report["parameter_grid"]), 1)
        self.assertEqual(report["parameter_grid"][0]["parameter_name"], "x")

        # On disk: parameter_grid present in JSON
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path, _ = write_sensitivity_report(report, tmpdir)
            loaded = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertIn("parameter_grid", loaded,
                          "sensitivity_report.json must contain top-level 'parameter_grid'")
            self.assertIsInstance(loaded["parameter_grid"], list)
            self.assertGreater(len(loaded["parameter_grid"]), 0)


# ---------------------------------------------------------------------------
# Group 9: forbidden_paths
# ---------------------------------------------------------------------------

class SensitivityForbiddenPathTests(unittest.TestCase):
    """Test group 9: no forbidden paths are created or modified."""

    def test_no_final_submit_created(self) -> None:
        from math_core.sensitivity.perturbation_matrix import (
            run_perturbation_matrix,
            write_sensitivity_report,
        )
        runner = lambda p: 0.0
        report = run_perturbation_matrix(
            {
                "model_runner": runner,
                "baseline_params": {},
                "metric_name": "const",
                "parameter_grid": [
                    {
                        "parameter_name": "dummy",
                        "baseline_value": 0.0,
                        "perturbation_type": "grid_value",
                        "values": [1.0],
                        "absolute_or_relative": "absolute",
                        "perturbation_scale": 1.0,
                    },
                ],
            },
            run_id="test_sens_forbidden",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            write_sensitivity_report(report, tmpdir)

        repo_root = Path(__file__).parents[2]
        final_submit = repo_root / "final_submit"
        self.assertFalse(final_submit.exists(),
                         "final_submit/ must not be created by F5.2")

    def test_output_goes_to_work_tmp_not_root(self) -> None:
        from math_core.sensitivity.perturbation_matrix import (
            run_perturbation_matrix,
            write_sensitivity_report,
        )
        runner = lambda p: 1.0
        report = run_perturbation_matrix(
            {
                "model_runner": runner,
                "baseline_params": {},
                "metric_name": "const",
                "parameter_grid": [
                    {
                        "parameter_name": "dummy",
                        "baseline_value": 0.0,
                        "perturbation_type": "grid_value",
                        "values": [1.0],
                        "absolute_or_relative": "absolute",
                        "perturbation_scale": 1.0,
                    },
                ],
            },
            run_id="test_sens_pathloc",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path, csv_path = write_sensitivity_report(report, tmpdir)
            repo_root = Path(__file__).parents[2]
            self.assertFalse((repo_root / "sensitivity_report.json").exists())
            self.assertFalse((repo_root / "sensitivity_report.csv").exists())
            self.assertTrue(json_path.parent == Path(tmpdir))


# ---------------------------------------------------------------------------
# Group 10: failure_contract
# ---------------------------------------------------------------------------

class SensitivityFailureContractTests(unittest.TestCase):
    """Test group 10: failed perturbation runs excluded from ranking aggregation.

    Covers: failed_run, excluded_from_ranking, failure_contract_status,
    failure_reason per perturbation result.
    """

    def _make_config_with_failing_runner(self):
        """Config where the runner fails for all perturbed values."""
        return {
            "model_runner": _failing_runner,
            # baseline must succeed for the test, so patch baseline
            "baseline_params": {"a": 0.0},
            "metric_name": "obj",
            "parameter_grid": [
                {
                    "parameter_name": "a",
                    "baseline_value": 0.0,
                    "perturbation_type": "grid_value",
                    "values": [1.0, 2.0],
                    "absolute_or_relative": "absolute",
                    "perturbation_scale": 1.0,
                },
            ],
        }

    def _make_config_mixed(self):
        """Runner: succeeds on a=0.0 (baseline check), fails on a=999.0, succeeds on a=1.0."""
        def runner(p):
            v = float(p.get("a", 0.0))
            if v == 999.0:
                raise RuntimeError("deliberate fail")
            return v * v
        return {
            "model_runner": runner,
            "baseline_params": {"a": 2.0},
            "metric_name": "a_sq",
            "parameter_grid": [
                {
                    "parameter_name": "a",
                    "baseline_value": 2.0,
                    "perturbation_type": "grid_value",
                    "values": [1.0, 999.0, 3.0],
                    "absolute_or_relative": "absolute",
                    "perturbation_scale": 1.0,
                },
            ],
        }

    def test_failed_runner_row_retained_in_perturbation_results(self) -> None:
        from math_core.sensitivity.perturbation_matrix import run_perturbation_matrix

        def baseline_ok_then_fail(p):
            if p.get("a") == 0.0:
                return 4.0       # baseline succeeds
            raise RuntimeError("perturbed fail")

        config = {
            "model_runner": baseline_ok_then_fail,
            "baseline_params": {"a": 0.0},
            "metric_name": "obj",
            "parameter_grid": [
                {
                    "parameter_name": "a",
                    "baseline_value": 0.0,
                    "perturbation_type": "grid_value",
                    "values": [1.0],
                    "absolute_or_relative": "absolute",
                    "perturbation_scale": 1.0,
                },
            ],
        }
        result = run_perturbation_matrix(config, run_id="test_fc_retained")
        rows = result["perturbation_results"]
        self.assertEqual(len(rows), 1, "Failed run must still appear in perturbation_results")
        row = rows[0]
        self.assertTrue(row["failed_run"])
        self.assertTrue(row["excluded_from_ranking"])
        self.assertIsNotNone(row["failure_reason"])
        self.assertIn("excluded", row["failure_contract_status"])

    def test_failed_run_not_in_ranking_aggregation(self) -> None:
        from math_core.sensitivity.perturbation_matrix import run_perturbation_matrix

        def runner(p):
            v = float(p.get("a", 0.0))
            if v == 999.0:
                raise RuntimeError("deliberate fail")
            return v * v

        config = {
            "model_runner": runner,
            "baseline_params": {"a": 2.0},
            "metric_name": "a_sq",
            "parameter_grid": [
                {
                    "parameter_name": "a",
                    "baseline_value": 2.0,
                    "perturbation_type": "grid_value",
                    "values": [1.0, 999.0, 3.0],  # 999 will fail
                    "absolute_or_relative": "absolute",
                    "perturbation_scale": 1.0,
                },
            ],
        }
        result = run_perturbation_matrix(config, run_id="test_fc_excluded")
        # 2 success runs (a=1 and a=3), 1 failed run (a=999)
        # ranking should only use 2 success points
        ranking = result["parameter_ranking"]
        self.assertEqual(len(ranking), 1)
        entry = ranking[0]
        self.assertEqual(entry["perturbation_count"], 2,
                         "perturbation_count must count only successful runs")
        self.assertEqual(len(entry["raw_normalized_delta_values"]), 2,
                         "raw_normalized_delta_values must not include the failed run")

    def test_success_field_on_perturbation_result_rows(self) -> None:
        from math_core.sensitivity.perturbation_matrix import run_perturbation_matrix

        def runner(p):
            v = float(p.get("a", 0.0))
            if v == 5.0:
                raise RuntimeError("fail at 5")
            return v

        config = {
            "model_runner": runner,
            "baseline_params": {"a": 1.0},
            "metric_name": "a",
            "parameter_grid": [
                {
                    "parameter_name": "a",
                    "baseline_value": 1.0,
                    "perturbation_type": "grid_value",
                    "values": [2.0, 5.0],
                    "absolute_or_relative": "absolute",
                    "perturbation_scale": 1.0,
                },
            ],
        }
        result = run_perturbation_matrix(config, run_id="test_fc_success_field")
        rows = result["perturbation_results"]
        self.assertEqual(len(rows), 2)
        success_row = next(r for r in rows if r["perturbation_values"] == [2.0])
        failed_row = next(r for r in rows if r["perturbation_values"] == [5.0])
        self.assertTrue(success_row["success"])
        self.assertFalse(success_row["failed_run"])
        self.assertFalse(success_row["excluded_from_ranking"])
        self.assertFalse(failed_row["success"])
        self.assertTrue(failed_row["failed_run"])
        self.assertTrue(failed_row["excluded_from_ranking"])


# ---------------------------------------------------------------------------
# Group 11: run_counts
# ---------------------------------------------------------------------------

class SensitivityRunCountsTests(unittest.TestCase):
    """Test group 11: run_counts.total/success/failed/excluded_from_ranking."""

    def test_run_counts_all_success(self) -> None:
        from math_core.sensitivity.perturbation_matrix import run_perturbation_matrix
        runner = lambda p: float(p.get("a", 0.0))
        config = {
            "model_runner": runner,
            "baseline_params": {"a": 1.0},
            "metric_name": "a",
            "parameter_grid": [
                {
                    "parameter_name": "a",
                    "baseline_value": 1.0,
                    "perturbation_type": "grid_value",
                    "values": [2.0, 3.0, 4.0],
                    "absolute_or_relative": "absolute",
                    "perturbation_scale": 1.0,
                },
            ],
        }
        result = run_perturbation_matrix(config, run_id="test_rc_all_success")
        rc = result["run_counts"]
        self.assertEqual(rc["total"], 3)
        self.assertEqual(rc["success"], 3)
        self.assertEqual(rc["failed"], 0)
        self.assertEqual(rc["excluded_from_ranking"], 0)

    def test_run_counts_with_failures(self) -> None:
        from math_core.sensitivity.perturbation_matrix import run_perturbation_matrix

        def runner(p):
            v = float(p.get("a", 0.0))
            if v in (2.0, 3.0):
                raise RuntimeError("forced fail")
            return v

        config = {
            "model_runner": runner,
            "baseline_params": {"a": 1.0},
            "metric_name": "a",
            "parameter_grid": [
                {
                    "parameter_name": "a",
                    "baseline_value": 1.0,
                    "perturbation_type": "grid_value",
                    "values": [1.5, 2.0, 3.0, 4.0],  # 2 fail, 2 succeed
                    "absolute_or_relative": "absolute",
                    "perturbation_scale": 1.0,
                },
            ],
        }
        result = run_perturbation_matrix(config, run_id="test_rc_with_failures")
        rc = result["run_counts"]
        self.assertEqual(rc["total"], 4)
        self.assertEqual(rc["success"], 2)
        self.assertEqual(rc["failed"], 2)
        self.assertEqual(rc["excluded_from_ranking"], 2)

    def test_run_counts_in_failure_report(self) -> None:
        from math_core.sensitivity.perturbation_matrix import run_perturbation_matrix
        result = run_perturbation_matrix(
            {
                "model_runner": lambda p: 0.0,
                "baseline_params": {},
                "metric_name": "obj",
                "multi_model_results": [{"model_id": "A"}],
                "parameter_grid": [
                    {
                        "parameter_name": "x",
                        "baseline_value": 1.0,
                        "perturbation_type": "grid_value",
                        "values": [2.0],
                        "absolute_or_relative": "absolute",
                        "perturbation_scale": 1.0,
                    },
                ],
            },
            run_id="test_rc_failure_report",
        )
        self.assertFalse(result["success"])
        self.assertIn("run_counts", result)
        rc = result["run_counts"]
        self.assertEqual(rc["total"], 0)
        self.assertEqual(rc["success"], 0)
        self.assertEqual(rc["failed"], 0)
        self.assertEqual(rc["excluded_from_ranking"], 0)


# ---------------------------------------------------------------------------
# Group 12: high_failure_rate
# ---------------------------------------------------------------------------

class SensitivityHighFailureRateTests(unittest.TestCase):
    """Test group 12: high_failure_rate flag and ranking_reliability when >50% of a parameter's runs fail."""

    def test_high_failure_rate_flagged(self) -> None:
        from math_core.sensitivity.perturbation_matrix import run_perturbation_matrix

        def runner(p):
            v = float(p.get("a", 0.0))
            # 3 out of 4 perturbed values will fail → 75% failure rate
            if v in (10.0, 20.0, 30.0):
                raise RuntimeError("deliberate high failure")
            return v

        config = {
            "model_runner": runner,
            "baseline_params": {"a": 1.0},
            "metric_name": "a",
            "parameter_grid": [
                {
                    "parameter_name": "a",
                    "baseline_value": 1.0,
                    "perturbation_type": "grid_value",
                    "values": [2.0, 10.0, 20.0, 30.0],  # 1 success, 3 fail
                    "absolute_or_relative": "absolute",
                    "perturbation_scale": 1.0,
                },
            ],
        }
        result = run_perturbation_matrix(config, run_id="test_hfr_flagged")
        ranking = result["parameter_ranking"]
        self.assertEqual(len(ranking), 1)
        entry = ranking[0]
        self.assertTrue(entry["high_failure_rate"],
                        "high_failure_rate must be True when >50% runs fail")
        self.assertEqual(entry["ranking_reliability"], "limited",
                         "ranking_reliability must be 'limited' for high failure rate")

    def test_low_failure_rate_not_flagged(self) -> None:
        from math_core.sensitivity.perturbation_matrix import run_perturbation_matrix

        def runner(p):
            v = float(p.get("a", 0.0))
            if v == 99.0:
                raise RuntimeError("one fail")
            return v

        config = {
            "model_runner": runner,
            "baseline_params": {"a": 1.0},
            "metric_name": "a",
            "parameter_grid": [
                {
                    "parameter_name": "a",
                    "baseline_value": 1.0,
                    "perturbation_type": "grid_value",
                    "values": [2.0, 3.0, 4.0, 99.0],  # 3 success, 1 fail → 25%
                    "absolute_or_relative": "absolute",
                    "perturbation_scale": 1.0,
                },
            ],
        }
        result = run_perturbation_matrix(config, run_id="test_hfr_not_flagged")
        ranking = result["parameter_ranking"]
        self.assertEqual(len(ranking), 1)
        entry = ranking[0]
        self.assertFalse(entry["high_failure_rate"],
                         "high_failure_rate must be False when ≤50% runs fail")
        self.assertEqual(entry["ranking_reliability"], "normal")


# ---------------------------------------------------------------------------
# Group 13: reproducibility_fields
# ---------------------------------------------------------------------------

class SensitivityReproducibilityFieldsTests(unittest.TestCase):
    """Test group 13: sampling_method, random_seed, tie_tolerance, raw_nd_values, normalized_delta all present."""

    def test_top_level_reproducibility_fields(self) -> None:
        from math_core.sensitivity.perturbation_matrix import run_perturbation_matrix
        runner = lambda p: float(p.get("a", 0.0))
        result = run_perturbation_matrix(
            {
                "model_runner": runner,
                "baseline_params": {"a": 2.0},
                "metric_name": "a",
                "sampling_method": "OAT",
                "random_seed": None,
                "tie_tolerance": 1e-5,
                "parameter_grid": [
                    {
                        "parameter_name": "a",
                        "baseline_value": 2.0,
                        "perturbation_type": "grid_value",
                        "values": [3.0, 4.0],
                        "absolute_or_relative": "absolute",
                        "perturbation_scale": 1.0,
                    },
                ],
            },
            run_id="test_repro_fields",
        )
        self.assertIn("sampling_method", result)
        self.assertIn("random_seed", result)
        self.assertIn("tie_tolerance", result)
        self.assertEqual(result["sampling_method"], "OAT")
        self.assertIsNone(result["random_seed"])
        self.assertAlmostEqual(result["tie_tolerance"], 1e-5)

    def test_raw_nd_values_in_ranking(self) -> None:
        from math_core.sensitivity.perturbation_matrix import run_perturbation_matrix
        runner = lambda p: float(p.get("a", 0.0)) ** 2
        result = run_perturbation_matrix(
            {
                "model_runner": runner,
                "baseline_params": {"a": 2.0},
                "metric_name": "a_sq",
                "parameter_grid": [
                    {
                        "parameter_name": "a",
                        "baseline_value": 2.0,
                        "perturbation_type": "grid_value",
                        "values": [3.0, 4.0],
                        "absolute_or_relative": "absolute",
                        "perturbation_scale": 1.0,
                    },
                ],
            },
            run_id="test_repro_rawvals",
        )
        ranking = result["parameter_ranking"]
        self.assertGreater(len(ranking), 0)
        entry = ranking[0]
        self.assertIn("raw_normalized_delta_values", entry)
        raw = entry["raw_normalized_delta_values"]
        self.assertIsInstance(raw, list)
        self.assertEqual(len(raw), 2)
        for v in raw:
            self.assertIsInstance(v, float)
            self.assertGreaterEqual(v, 0.0)

    def test_normalized_delta_per_result_row(self) -> None:
        from math_core.sensitivity.perturbation_matrix import run_perturbation_matrix
        runner = lambda p: float(p.get("a", 0.0))
        result = run_perturbation_matrix(
            {
                "model_runner": runner,
                "baseline_params": {"a": 2.0},
                "metric_name": "a",
                "parameter_grid": [
                    {
                        "parameter_name": "a",
                        "baseline_value": 2.0,
                        "perturbation_type": "grid_value",
                        "values": [4.0],
                        "absolute_or_relative": "absolute",
                        "perturbation_scale": 2.0,
                    },
                ],
            },
            run_id="test_repro_nd_row",
        )
        rows = result["perturbation_results"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertIn("normalized_delta", row)
        self.assertIsNotNone(row["normalized_delta"])
        # a: baseline=2 perturbed=4 → nd = |4-2| / max(|2|, 1e-12) = 1.0
        self.assertAlmostEqual(row["normalized_delta"], 1.0, places=10)

    def test_json_disk_contains_reproducibility_fields(self) -> None:
        from math_core.sensitivity.perturbation_matrix import (
            run_perturbation_matrix,
            write_sensitivity_report,
        )
        runner = lambda p: float(p.get("a", 0.0))
        report = run_perturbation_matrix(
            {
                "model_runner": runner,
                "baseline_params": {"a": 1.0},
                "metric_name": "a",
                "sampling_method": "OAT",
                "random_seed": 7,
                "parameter_grid": [
                    {
                        "parameter_name": "a",
                        "baseline_value": 1.0,
                        "perturbation_type": "grid_value",
                        "values": [2.0],
                        "absolute_or_relative": "absolute",
                        "perturbation_scale": 1.0,
                    },
                ],
            },
            run_id="test_repro_disk",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path, _ = write_sensitivity_report(report, tmpdir)
            loaded = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertIn("sampling_method", loaded)
            self.assertIn("random_seed", loaded)
            self.assertIn("tie_tolerance", loaded)
            self.assertIn("raw_normalized_delta_values",
                          loaded["parameter_ranking"][0])
            self.assertIn("normalized_delta",
                          loaded["perturbation_results"][0])


# ---------------------------------------------------------------------------
# Group 14: nested_hyperparam_guard
# ---------------------------------------------------------------------------

class SensitivityNestedHyperparamGuardTests(unittest.TestCase):
    """Test group 14: nested/compound hyperparameter names rejected.

    Covers: solver_hyperparameters.method, model.method, maxiter (plain).
    """

    def _run_with_param(self, param_name: str) -> dict:
        from math_core.sensitivity.perturbation_matrix import run_perturbation_matrix
        runner = lambda p: float(p.get("x", 0.0))
        return run_perturbation_matrix(
            {
                "model_runner": runner,
                "baseline_params": {"x": 1.0},
                "metric_name": "x",
                "parameter_grid": [
                    {
                        "parameter_name": param_name,
                        "baseline_value": "SLSQP",
                        "perturbation_type": "grid_value",
                        "values": ["L-BFGS-B"],
                        "absolute_or_relative": "absolute",
                        "perturbation_scale": None,
                    },
                ],
            },
            run_id="test_nested_guard",
        )

    def test_solver_hyperparameters_dot_method_rejected(self) -> None:
        result = self._run_with_param("solver_hyperparameters.method")
        rejected_names = [r["parameter_name"] for r in result["rejected_params"]]
        self.assertIn("solver_hyperparameters.method", rejected_names,
                      "solver_hyperparameters.method must be rejected")

    def test_model_dot_method_rejected(self) -> None:
        result = self._run_with_param("model.method")
        rejected_names = [r["parameter_name"] for r in result["rejected_params"]]
        self.assertIn("model.method", rejected_names,
                      "model.method must be rejected (second segment 'method' is forbidden)")

    def test_maxiter_plain_rejected(self) -> None:
        result = self._run_with_param("maxiter")
        rejected_names = [r["parameter_name"] for r in result["rejected_params"]]
        self.assertIn("maxiter", rejected_names)

    def test_nested_rejected_not_in_perturbation_results(self) -> None:
        for param in ("solver_hyperparameters.method", "model.method", "maxiter"):
            result = self._run_with_param(param)
            perturbed_names = {r["parameter_name"] for r in result["perturbation_results"]}
            self.assertNotIn(param, perturbed_names,
                             msg=f"'{param}' must NOT appear in perturbation_results")

    def test_solver_params_prefix_rejected(self) -> None:
        result = self._run_with_param("solver_params.tol")
        rejected_names = [r["parameter_name"] for r in result["rejected_params"]]
        self.assertIn("solver_params.tol", rejected_names,
                      "solver_params.tol must be rejected (forbidden compound prefix)")


# ---------------------------------------------------------------------------
# Group 15: disclaimer_content
# ---------------------------------------------------------------------------

class SensitivityDisclaimerContentTests(unittest.TestCase):
    """Test group 15: ranking_disclaimer must mention local, non-causal, non-deterministic."""

    def _get_disclaimer(self) -> str:
        from math_core.sensitivity.perturbation_matrix import RANKING_DISCLAIMER
        return RANKING_DISCLAIMER

    def test_disclaimer_mentions_local(self) -> None:
        d = self._get_disclaimer().lower()
        self.assertIn("local", d,
                      "ranking_disclaimer must mention 'local' perturbation scope")

    def test_disclaimer_mentions_causal(self) -> None:
        d = self._get_disclaimer().lower()
        self.assertIn("causal", d,
                      "ranking_disclaimer must clarify it does not imply causal strength")

    def test_disclaimer_mentions_deterministic(self) -> None:
        d = self._get_disclaimer().lower()
        self.assertIn("deterministic", d,
                      "ranking_disclaimer must state results are not deterministic conclusions")

    def test_disclaimer_mentions_academic_validity(self) -> None:
        d = self._get_disclaimer().lower()
        self.assertIn("academic", d,
                      "ranking_disclaimer must mention academic validity is not established")

    def test_disclaimer_present_in_report_dict(self) -> None:
        from math_core.sensitivity.perturbation_matrix import run_perturbation_matrix, RANKING_DISCLAIMER
        runner = lambda p: float(p.get("a", 0.0))
        result = run_perturbation_matrix(
            {
                "model_runner": runner,
                "baseline_params": {"a": 1.0},
                "metric_name": "a",
                "parameter_grid": [
                    {
                        "parameter_name": "a",
                        "baseline_value": 1.0,
                        "perturbation_type": "grid_value",
                        "values": [2.0],
                        "absolute_or_relative": "absolute",
                        "perturbation_scale": 1.0,
                    },
                ],
            },
            run_id="test_disclaimer_in_report",
        )
        self.assertEqual(result["ranking_disclaimer"], RANKING_DISCLAIMER)


if __name__ == "__main__":
    unittest.main()
