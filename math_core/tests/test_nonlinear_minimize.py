"""Tests for math_core.optimization.nonlinear_minimize (X5-F5.1).

Test groups:
1. success_case      — minimize sum_of_squares, solution ≈ [1, 2], obj ≈ 0
2. bounds_case       — bounds respected, solution within declared bounds
3. constraint_case   — linear inequality constraint, violation ≈ 0
4. failure_case      — missing initial_guess → success=False, failure_reason set
5. registry_case     — nonlinear_minimize reachable via math_core.registry
6. negative_case     — infeasible constraints, maxiter truncation,
                       mock-based override test (Fix 5: no longer skipped)
7. contract_fix_case — Fix 1/3/4: constraint_violation=0.0 when no constraints,
                       illegal coefficients, illegal tolerance
"""
from __future__ import annotations

import importlib.util
import unittest

SCIPY_AVAILABLE = importlib.util.find_spec("scipy") is not None


@unittest.skipUnless(SCIPY_AVAILABLE, "scipy not installed")
class NonlinearMinimizeSuccessTests(unittest.TestCase):
    """Test group 1: basic success with sum_of_squares objective."""

    def _run(self, config: dict) -> dict:
        from math_core.optimization.nonlinear_minimize import run
        return run(config, run_id="test_nlm_success")

    def test_sum_of_squares_converges(self) -> None:
        result = self._run({
            "objective_type": "sum_of_squares",
            "objective_params": {"targets": [1.0, 2.0]},
            "initial_guess": [10.0, 10.0],
        })
        self.assertTrue(result["success"], msg=result.get("failure_reason"))
        sol = result["primary_results"]["solution"]
        self.assertIsNotNone(sol)
        self.assertAlmostEqual(sol[0], 1.0, places=3)
        self.assertAlmostEqual(sol[1], 2.0, places=3)
        self.assertAlmostEqual(result["primary_results"]["objective_value"], 0.0, places=4)
        self.assertEqual(result["primary_results"]["solver_status"], "converged")
        self.assertIsNone(result["primary_results"]["failure_reason"])

    def test_result_summary_fields_present(self) -> None:
        result = self._run({
            "objective_type": "sum_of_squares",
            "objective_params": {"targets": [0.0]},
            "initial_guess": [5.0],
        })
        pr = result["primary_results"]
        for field in ("model_type", "solver", "status", "success", "objective_value",
                      "solution", "solver_status", "message", "constraint_violation",
                      "failure_reason", "diagnostics"):
            self.assertIn(field, pr, msg=f"missing field: {field}")

    def test_no_constraints_violation_is_zero(self) -> None:
        # Fix 1: constraint_violation must be 0.0 (not None) when no constraints given.
        result = self._run({
            "objective_type": "sum_of_squares",
            "objective_params": {"targets": [1.0, 2.0]},
            "initial_guess": [1.0, 2.0],
        })
        self.assertTrue(result["success"], msg=result.get("failure_reason"))
        cv = result["primary_results"]["constraint_violation"]
        self.assertIsNotNone(cv, "constraint_violation must not be None")
        self.assertIsInstance(cv, float)
        self.assertEqual(cv, 0.0)


@unittest.skipUnless(SCIPY_AVAILABLE, "scipy not installed")
class NonlinearMinimizeBoundsTests(unittest.TestCase):
    """Test group 2: bounds are respected."""

    def _run(self, config: dict) -> dict:
        from math_core.optimization.nonlinear_minimize import run
        return run(config, run_id="test_nlm_bounds")

    def test_solution_within_bounds(self) -> None:
        # Minimize (x-5)^2 but bound x in [0, 2] → solution should be ≈ 2
        result = self._run({
            "objective_type": "sum_of_squares",
            "objective_params": {"targets": [5.0]},
            "initial_guess": [0.5],
            "bounds": [[0.0, 2.0]],
        })
        self.assertTrue(result["success"], msg=result.get("failure_reason"))
        sol = result["primary_results"]["solution"]
        self.assertIsNotNone(sol)
        self.assertGreaterEqual(sol[0], 0.0 - 1e-6)
        self.assertLessEqual(sol[0], 2.0 + 1e-6)

    def test_invalid_bounds_length_fails(self) -> None:
        result = self._run({
            "objective_type": "sum_of_squares",
            "objective_params": {"targets": [1.0, 2.0]},
            "initial_guess": [0.0, 0.0],
            "bounds": [[0.0, 1.0]],  # length 1 but n=2 → fatal
        })
        self.assertFalse(result["success"])
        self.assertGreater(result["fatal_count"], 0)


@unittest.skipUnless(SCIPY_AVAILABLE, "scipy not installed")
class NonlinearMinimizeConstraintTests(unittest.TestCase):
    """Test group 3: linear inequality constraint."""

    def _run(self, config: dict) -> dict:
        from math_core.optimization.nonlinear_minimize import run
        return run(config, run_id="test_nlm_constraint")

    def test_ineq_constraint_satisfied(self) -> None:
        # Minimize (x-0)^2 + (y-0)^2 subject to x + y >= 1
        # Unconstrained min = [0, 0]; constrained should push toward x+y=1
        result = self._run({
            "objective_type": "sum_of_squares",
            "objective_params": {"targets": [0.0, 0.0]},
            "initial_guess": [2.0, 2.0],
            "constraints": [{"type": "ineq", "coefficients": [1.0, 1.0], "rhs": 1.0}],
        })
        self.assertTrue(result["success"], msg=result.get("failure_reason"))
        sol = result["primary_results"]["solution"]
        self.assertIsNotNone(sol)
        self.assertGreaterEqual(sol[0] + sol[1], 1.0 - 1e-4)
        cv = result["primary_results"]["constraint_violation"]
        self.assertIsNotNone(cv, "constraint_violation must not be None")
        self.assertIsInstance(cv, float)
        self.assertLessEqual(cv, 1e-4)

    def test_invalid_constraint_type_fails(self) -> None:
        result = self._run({
            "objective_type": "sum_of_squares",
            "objective_params": {"targets": [0.0]},
            "initial_guess": [1.0],
            "constraints": [{"type": "bad_type", "coefficients": [1.0], "rhs": 0.0}],
        })
        self.assertFalse(result["success"])
        self.assertGreater(result["fatal_count"], 0)


class NonlinearMinimizeFailureTests(unittest.TestCase):
    """Test group 4: failure cases — no scipy import needed for input errors."""

    def _run(self, config: dict) -> dict:
        from math_core.optimization.nonlinear_minimize import run
        return run(config, run_id="test_nlm_failure")

    def test_missing_initial_guess_fails(self) -> None:
        result = self._run({
            "objective_type": "sum_of_squares",
            "objective_params": {"targets": [1.0]},
            # no initial_guess
        })
        self.assertFalse(result["success"])
        self.assertGreater(result["fatal_count"], 0)
        self.assertIn("failure_reason", result)
        self.assertEqual(result["failure_reason"], "missing_initial_guess")

    def test_unknown_objective_type_fails(self) -> None:
        result = self._run({
            "objective_type": "nonexistent_objective",
            "objective_params": {},
            "initial_guess": [0.0],
        })
        self.assertFalse(result["success"])
        self.assertGreater(result["fatal_count"], 0)
        self.assertEqual(result["failure_reason"], "unknown_objective_type")

    @unittest.skipUnless(SCIPY_AVAILABLE, "scipy not installed")
    def test_nan_objective_fails(self) -> None:
        # Minimize quadratic with NaN coefficient → NaN objective → failure
        # We force NaN by passing NaN in initial_guess (invalid input)
        result = self._run({
            "objective_type": "sum_of_squares",
            "objective_params": {"targets": [float("nan")]},
            "initial_guess": [0.0],
        })
        # NaN target produces NaN objective_value → must fail
        self.assertFalse(result["success"])
        self.assertGreater(result["fatal_count"], 0)


class NonlinearMinimizeRegistryTests(unittest.TestCase):
    """Test group 5: registry discovery."""

    def test_nonlinear_minimize_registered(self) -> None:
        from math_core.registry import list_models, get_model_spec
        model_ids = [spec["model_id"] for spec in list_models()]
        self.assertIn("optimization.nonlinear_minimize", model_ids)

    def test_get_model_spec_returns_spec(self) -> None:
        from math_core.registry import get_model_spec
        spec = get_model_spec("optimization.nonlinear_minimize")
        self.assertIsNotNone(spec)
        self.assertEqual(spec["model_family"], "optimization")
        self.assertIn("inputs_schema", spec)
        self.assertIn("outputs_schema", spec)

    @unittest.skipUnless(SCIPY_AVAILABLE, "scipy not installed")
    def test_run_model_via_registry(self) -> None:
        from math_core.registry import run_model
        result = run_model(
            {
                "model_type": "optimization.nonlinear_minimize",
                "objective_type": "sum_of_squares",
                "objective_params": {"targets": [3.0, 4.0]},
                "initial_guess": [0.0, 0.0],
            },
            run_id="registry_nlm_test",
        )
        self.assertTrue(result["success"], msg=result.get("failure_reason"))
        sol = result["primary_results"]["solution"]
        self.assertAlmostEqual(sol[0], 3.0, places=2)
        self.assertAlmostEqual(sol[1], 4.0, places=2)

    def test_unregistered_model_type_fails(self) -> None:
        from math_core.registry import run_model
        result = run_model(
            {"model_type": "optimization.nonlinear_minimize_v999"},
            run_id="registry_miss_test",
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["failure_reason"], "unregistered_model_type")


@unittest.skipUnless(SCIPY_AVAILABLE, "scipy not installed")
class NonlinearMinimizeNegativeTests(unittest.TestCase):
    """Test group 6: negative / failure-contract tests.

    (a) infeasible constraints  — contradictory bounds force failure
    (b) maxiter truncation      — solver truncated before convergence → not success
    (c) mock-based override     — directly calls _apply_failure_contract with a
                                  simulated violation to confirm the flip logic,
                                  without depending on natural SLSQP false-positive
    """

    def _run(self, config: dict) -> dict:
        from math_core.optimization.nonlinear_minimize import run
        return run(config, run_id="test_nlm_negative")

    def test_infeasible_constraint_fails(self) -> None:
        # x >= 2  AND  x <= 0 are contradictory — no feasible point exists.
        result = self._run({
            "objective_type": "sum_of_squares",
            "objective_params": {"targets": [1.0]},
            "initial_guess": [1.0],
            "constraints": [
                {"type": "ineq", "coefficients": [1.0], "rhs": 2.0},   # x >= 2
                {"type": "ineq", "coefficients": [-1.0], "rhs": 0.0},  # x <= 0
            ],
        })
        self.assertFalse(result["success"],
                         msg="Infeasible constraints must yield success=False")
        self.assertGreater(result["fatal_count"], 0)

    def test_maxiter_failure_not_success(self) -> None:
        # maxiter=1 on a multi-variable problem prevents convergence.
        result = self._run({
            "objective_type": "sum_of_squares",
            "objective_params": {"targets": [10.0, 20.0, 30.0]},
            "initial_guess": [0.0, 0.0, 0.0],
            "options": {"maxiter": 1},
        })
        self.assertFalse(result["success"],
                         msg="Truncated solver (maxiter=1) must yield success=False")
        self.assertGreater(result["fatal_count"], 0)

    def test_constraint_violation_overrides_scipy_success_via_helper(self) -> None:
        # Fix 5: test the override branch directly using _apply_failure_contract,
        # bypassing the real solver entirely.  This avoids depending on SLSQP
        # producing a natural false-positive (which is machine-precision-dependent).
        #
        # Scenario: suppose scipy said success=True (so risks=[]) but the solution
        # [0.5] violates constraint "x >= 1" (ineq, coeff=[1], rhs=1).
        # violation = 1 - 0.5 = 0.5, which exceeds CONSTRAINT_TOLERANCE (1e-6).
        # _apply_failure_contract must append a fatal risk and return success=False.
        from math_core.optimization.nonlinear_minimize import (
            _apply_failure_contract,
            CONSTRAINT_TOLERANCE,
        )
        raw_constraints = [{"type": "ineq", "coefficients": [1.0], "rhs": 1.0}]
        solution = [0.5]  # violates x >= 1
        violation, updated_risks, failure_reason, success = _apply_failure_contract(
            solution=solution,
            raw_constraints=raw_constraints,
            risks=[],               # scipy "said" success — no prior risks
            effective_tolerance=CONSTRAINT_TOLERANCE,
            failure_reason=None,
        )
        self.assertFalse(success,
                         "helper must flip success=False when violation > tolerance")
        self.assertGreater(violation, CONSTRAINT_TOLERANCE)
        self.assertTrue(any(r.get("fatal") for r in updated_risks),
                        "helper must append fatal risk for the violation")
        self.assertEqual(failure_reason, "constraint_violation_exceeds_threshold")


class NonlinearMinimizeContractFixTests(unittest.TestCase):
    """Test group 7: Codex review fixes — Fix 1 (violation=0.0), Fix 3 (illegal
    coefficients), Fix 4 (illegal tolerance).  No scipy required for Fix 3/4.
    """

    def _run(self, config: dict) -> dict:
        from math_core.optimization.nonlinear_minimize import run
        return run(config, run_id="test_nlm_contract_fix")

    # --- Fix 3: illegal coefficients must not raise, must return failure ---

    def test_invalid_string_coefficients_do_not_raise(self) -> None:
        # coefficients=["abc"] — non-numeric string elements.
        # Must return a math_core failure result (input_invalid), not raise TypeError.
        try:
            result = self._run({
                "objective_type": "sum_of_squares",
                "objective_params": {"targets": [0.0]},
                "initial_guess": [1.0],
                "constraints": [
                    {"type": "ineq", "coefficients": ["abc"], "rhs": 0.0}
                ],
            })
        except Exception as exc:
            self.fail(f"run() raised an exception instead of returning failure: {exc}")
        self.assertFalse(result["success"],
                         "Non-numeric string coefficients must yield success=False")
        self.assertIn("failure_reason", result)
        self.assertIsNotNone(result["failure_reason"])

    def test_none_coefficients_do_not_raise(self) -> None:
        # coefficients=None — missing entirely.
        try:
            result = self._run({
                "objective_type": "sum_of_squares",
                "objective_params": {"targets": [0.0]},
                "initial_guess": [1.0],
                "constraints": [
                    {"type": "ineq", "coefficients": None, "rhs": 0.0}
                ],
            })
        except Exception as exc:
            self.fail(f"run() raised an exception instead of returning failure: {exc}")
        self.assertFalse(result["success"])
        self.assertIsNotNone(result["failure_reason"])

    # --- Fix 4: illegal tolerance must not raise, must return failure ---

    def test_string_tolerance_returns_failure(self) -> None:
        # constraint_violation_tolerance="bad" — must not raise ValueError.
        try:
            result = self._run({
                "objective_type": "sum_of_squares",
                "objective_params": {"targets": [0.0]},
                "initial_guess": [1.0],
                "constraint_violation_tolerance": "bad",
            })
        except Exception as exc:
            self.fail(f"run() raised an exception instead of returning failure: {exc}")
        self.assertFalse(result["success"],
                         "Invalid tolerance must yield success=False")
        self.assertIn("failure_reason", result)
        self.assertEqual(result["failure_reason"], "invalid_tolerance")

    def test_negative_tolerance_returns_failure(self) -> None:
        # Negative tolerance is semantically invalid.
        try:
            result = self._run({
                "objective_type": "sum_of_squares",
                "objective_params": {"targets": [0.0]},
                "initial_guess": [1.0],
                "constraint_violation_tolerance": -1.0,
            })
        except Exception as exc:
            self.fail(f"run() raised an exception instead of returning failure: {exc}")
        self.assertFalse(result["success"])
        self.assertEqual(result["failure_reason"], "invalid_tolerance")

    def test_nan_tolerance_returns_failure(self) -> None:
        # NaN tolerance is semantically invalid.
        try:
            result = self._run({
                "objective_type": "sum_of_squares",
                "objective_params": {"targets": [0.0]},
                "initial_guess": [1.0],
                "constraint_violation_tolerance": float("nan"),
            })
        except Exception as exc:
            self.fail(f"run() raised an exception instead of returning failure: {exc}")
        self.assertFalse(result["success"])
        self.assertEqual(result["failure_reason"], "invalid_tolerance")

    # --- Fix 1 (no-scipy path): empty-primary also has constraint_violation=0.0 ---

    def test_failure_primary_constraint_violation_is_float(self) -> None:
        # Even failure paths must output constraint_violation as float.
        result = self._run({
            "objective_type": "sum_of_squares",
            "objective_params": {"targets": [1.0]},
            # no initial_guess → early failure path
        })
        self.assertFalse(result["success"])
        cv = result["primary_results"]["constraint_violation"]
        self.assertIsNotNone(cv, "constraint_violation must be float even on failure")
        self.assertIsInstance(cv, float)


if __name__ == "__main__":
    unittest.main()
