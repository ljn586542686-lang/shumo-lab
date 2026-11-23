"""Q2 字典序目标锁的非求解回归测试。"""

from __future__ import annotations

import hashlib
import sys
import unittest
from dataclasses import replace
from math import inf, nextafter, ulp
from pathlib import Path
from unittest.mock import patch


PYTHON_DIR = Path(__file__).resolve().parents[1]
CASE_ROOT = PYTHON_DIR.parents[1]
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from physical_core_solver import VariableBound  # noqa: E402
from q2_model_solver import build_q2_model  # noqa: E402
from q2_solver import (  # noqa: E402
    NUMERICAL_EQUIVALENCE_RELATIVE_TOLERANCE,
    ROUNDING_GUARD_ULPS,
    build_objective_upper_lock,
    build_validation_band_upper_locks,
    certify_objective_at_theoretical_lower_bound,
    evaluate_objective_preservation,
    objective_lower_bound_from_bounds,
    recompute_objective_and_validate_report,
    run_lexicographic_q2,
)
from solver import BackendSolveEvidence  # noqa: E402


TOLERANCE = 1e-7


class FakeBackend:
    name = "non_solver_fake_backend"
    constraint_violation_tolerance = TOLERANCE

    def __init__(self, reported_level1_delta: float = 0.0, solutions=None):
        self.reported_level1_delta = reported_level1_delta
        self.solutions = solutions
        self.calls: list[dict[str, object]] = []

    def _solve(self, *, objective, equalities, upper_bounds, bounds, run_id, case_dir):
        del bounds, case_dir
        level = len(self.calls) + 1
        default = (1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 6000.0, 0.0)
        solution = default if self.solutions is None else self.solutions[min(level - 1, len(self.solutions) - 1)]
        recomputed = sum(float(a) * float(b) for a, b in zip(objective, solution))
        reported = recomputed + (self.reported_level1_delta if level == 1 else 0.0)
        self.calls.append(
            {
                "run_id": run_id,
                "equalities": tuple(equalities),
                "upper_bounds": tuple(upper_bounds),
                "objective": tuple(objective),
            }
        )
        return BackendSolveEvidence(
            accepted=True,
            status="solver_reported_optimum",
            objective_value=reported,
            solution=solution,
            constraint_violation=0.0,
            iterations=0,
            message="NON_SOLVER_FIXTURE",
        )

    def solve(self, *, objective, equalities, bounds, run_id, case_dir):
        return self._solve(
            objective=objective,
            equalities=equalities,
            upper_bounds=(),
            bounds=bounds,
            run_id=run_id,
            case_dir=case_dir,
        )

    def solve_with_upper_bounds(self, *, objective, equalities, upper_bounds, bounds, run_id, case_dir):
        return self._solve(
            objective=objective,
            equalities=equalities,
            upper_bounds=upper_bounds,
            bounds=bounds,
            run_id=run_id,
            case_dir=case_dir,
        )


class DeterministicFallbackBackend(FakeBackend):
    def __init__(self, *, fail_level: int, fallback_solution=None, default_invalid_solution=None):
        super().__init__()
        self.fail_level = fail_level
        self.fallback_solution = fallback_solution
        self.default_invalid_solution = default_invalid_solution
        self.default_failure_delivered = False
        self.fallback_calls: list[dict[str, object]] = []

    def solve_with_upper_bounds(self, *, objective, equalities, upper_bounds, bounds, run_id, case_dir):
        level = len(self.calls) + 1
        if level == self.fail_level and not self.default_failure_delivered:
            self.default_failure_delivered = True
            self.calls.append(
                {
                    "run_id": run_id,
                    "equalities": tuple(equalities),
                    "upper_bounds": tuple(upper_bounds),
                    "bounds": tuple(bounds),
                    "objective": tuple(objective),
                }
            )
            if self.default_invalid_solution is not None:
                solution = self.default_invalid_solution
                return BackendSolveEvidence(
                    accepted=True,
                    status="solver_reported_optimum",
                    objective_value=sum(float(a) * float(b) for a, b in zip(objective, solution)),
                    solution=solution,
                    constraint_violation=0.0,
                    iterations=5,
                    message="OPTIMAL_WITH_POSTVALIDATION_FAILURE_FIXTURE",
                )
            return BackendSolveEvidence(
                accepted=False,
                status="fatal",
                objective_value=None,
                solution=None,
                constraint_violation=None,
                iterations=None,
                message="FALSE_INFEASIBLE_FIXTURE",
                failure_reason="infeasible",
            )
        return super().solve_with_upper_bounds(
            objective=objective,
            equalities=equalities,
            upper_bounds=upper_bounds,
            bounds=bounds,
            run_id=run_id,
            case_dir=case_dir,
        )

    def solve_with_upper_bounds_fallback(
        self,
        *,
        objective,
        equalities,
        upper_bounds,
        bounds,
        run_id,
        case_dir,
        method,
        presolve,
    ):
        del case_dir
        solution = self.fallback_solution or (1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 6000.0, 0.0)
        self.fallback_calls.append(
            {
                "run_id": run_id,
                "equalities": tuple(equalities),
                "upper_bounds": tuple(upper_bounds),
                "bounds": tuple(bounds),
                "objective": tuple(objective),
                "method": method,
                "presolve": presolve,
            }
        )
        return BackendSolveEvidence(
            accepted=True,
            status="solver_reported_optimum",
            objective_value=sum(float(a) * float(b) for a, b in zip(objective, solution)),
            solution=solution,
            constraint_violation=0.0,
            iterations=17,
            message="FALLBACK_OPTIMAL_FIXTURE",
        )


def one_interval_model(pv_available=0.0):
    return build_q2_model(
        (1.0,),
        (pv_available,),
        (2.0,),
        initial_energy_kwh=6000.0,
    )


class Q2ObjectiveLockTests(unittest.TestCase):
    def test_small_report_delta_returns_recomputed_value(self):
        value, delta = recompute_objective_and_validate_report((2.0,), (1.0,), 2.0 + 1e-8, TOLERANCE)
        self.assertEqual(value, 2.0)
        self.assertAlmostEqual(delta, 1e-8)

    def test_delta_at_or_below_tolerance_can_continue(self):
        value, _ = recompute_objective_and_validate_report((2.0,), (1.0,), 2.0 + TOLERANCE, TOLERANCE)
        self.assertEqual(value, 2.0)

    def test_delta_above_tolerance_stops(self):
        with self.assertRaisesRegex(ValueError, "Q2_REPORTED_OBJECTIVE_RECOMPUTE_MISMATCH"):
            recompute_objective_and_validate_report((2.0,), (1.0,), 2.0 + 2 * TOLERANCE, TOLERANCE)

    def test_next_level_lock_uses_recomputed_value(self):
        backend = FakeBackend(reported_level1_delta=1e-8)
        result = run_lexicographic_q2(one_interval_model(), backend=backend, run_id="non_solver")
        self.assertEqual(result["status"], "PASS")
        level1_lock = backend.calls[1]["upper_bounds"][-1]
        self.assertEqual(level1_lock.name, "q2_lock_level_1")
        self.assertEqual(level1_lock.rhs, 2.0)
        self.assertNotEqual(level1_lock.rhs, 2.0 + 1e-8)

    def test_lock_is_one_sided_upper_bound_not_exact_equality(self):
        backend = FakeBackend()
        model = one_interval_model()
        result = run_lexicographic_q2(model, backend=backend, run_id="non_solver")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(backend.calls[1]["equalities"], model.equalities)
        self.assertEqual(len(backend.calls[1]["upper_bounds"]), 1)
        self.assertEqual(backend.calls[1]["upper_bounds"][0].rhs, 2.0)

    def test_default_optimal_does_not_trigger_fallback(self):
        backend = DeterministicFallbackBackend(fail_level=99)
        result = run_lexicographic_q2(one_interval_model(), backend=backend, run_id="non_solver")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(backend.fallback_calls, [])
        self.assertEqual(result["solver_fallback_diagnostics"], ())

    def test_false_infeasible_with_witness_triggers_no_presolve_fallback(self):
        backend = DeterministicFallbackBackend(fail_level=2)
        result = run_lexicographic_q2(one_interval_model(), backend=backend, run_id="non_solver")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(backend.fallback_calls), 1)
        self.assertEqual(backend.fallback_calls[0]["method"], "highs")
        self.assertFalse(backend.fallback_calls[0]["presolve"])
        diagnostic = result["solver_fallback_diagnostics"][0]
        self.assertTrue(diagnostic["FEASIBLE_WITNESS_CONFIRMED"])
        self.assertEqual(diagnostic["FALLBACK_STATUS"], "solver_reported_optimum")
        self.assertEqual(diagnostic["FALLBACK_ITERATIONS"], 17)

    def test_optimal_primal_failing_independent_bounds_triggers_fallback(self):
        invalid_default = (1.0 - 2.0 * TOLERANCE, 1.0, 0.0, 0.0, 0.0, 0.0, 6000.0, -2.0 * TOLERANCE)
        backend = DeterministicFallbackBackend(
            fail_level=2,
            default_invalid_solution=invalid_default,
        )
        result = run_lexicographic_q2(one_interval_model(), backend=backend, run_id="non_solver")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(backend.fallback_calls), 1)
        self.assertEqual(result["levels"][1]["DEFAULT_SOLVER_STATUS"], "solver_reported_optimum")
        self.assertFalse(result["levels"][1]["DEFAULT_FORMALLY_ACCEPTED"])
        self.assertGreater(result["levels"][1]["DEFAULT_INDEPENDENT_CONSTRAINT_VIOLATION"], TOLERANCE)
        self.assertTrue(result["levels"][1]["FALLBACK_TRIGGERED"])
        self.assertEqual(result["levels"][1]["FALLBACK_STATUS"], "solver_reported_optimum")

    def test_fallback_receives_identical_lp_data(self):
        backend = DeterministicFallbackBackend(fail_level=2)
        result = run_lexicographic_q2(one_interval_model(), backend=backend, run_id="non_solver")
        self.assertEqual(result["status"], "PASS")
        default_level2 = backend.calls[1]
        fallback_level2 = backend.fallback_calls[0]
        for field in ("objective", "equalities", "upper_bounds", "bounds"):
            self.assertEqual(fallback_level2[field], default_level2[field])

    def test_failed_default_without_feasible_witness_does_not_fallback(self):
        backend = DeterministicFallbackBackend(fail_level=2)
        invalid_witness = {
            "status": "FAIL",
            "max_original_constraint_violation": 1.0,
            "max_variable_bound_violation": 0.0,
            "max_objective_lock_violation": 0.0,
        }
        with patch("q2_solver._level_feasible_witness_evidence", return_value=invalid_witness):
            result = run_lexicographic_q2(one_interval_model(), backend=backend, run_id="non_solver")
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["reason"], "Q2_LEVEL2_NOT_ACCEPTED")
        self.assertEqual(backend.fallback_calls, [])
        self.assertFalse(result["levels"][1]["FEASIBLE_WITNESS_CONFIRMED"])

    def test_fallback_solution_violating_previous_lock_fails_closed(self):
        delta_q = TOLERANCE
        invalid = (1.0 + delta_q, 1.0, 0.0, 0.0, 0.0, 0.0, 6000.0, delta_q)
        backend = DeterministicFallbackBackend(fail_level=2, fallback_solution=invalid)
        result = run_lexicographic_q2(one_interval_model(), backend=backend, run_id="non_solver")
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["reason"], "Q2_LEVEL2_INDEPENDENT_CONSTRAINT_VALIDATION_FAILED")

    def test_level3_uses_same_deterministic_fallback_logic(self):
        solution = (1.0, 1.0, 0.0, 0.0, 0.0, 1.0, 6000.0, 0.0)
        backend = DeterministicFallbackBackend(fail_level=3, fallback_solution=solution)
        backend.solutions = (solution, solution, solution)
        result = run_lexicographic_q2(one_interval_model(pv_available=1.0), backend=backend, run_id="non_solver")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(backend.fallback_calls), 1)
        self.assertIn("level_3_fallback_no_presolve", backend.fallback_calls[0]["run_id"])
        self.assertEqual(result["level3_optimality_proof"], "SOLVER_REPORTED_OPTIMUM")

    def test_upper_lock_builder_uses_recomputed_anchor_without_tolerance(self):
        lock = build_objective_upper_lock((2.0,), 2.0, TOLERANCE, "level1")
        self.assertEqual(lock.coefficients, ((0, 2.0),))
        self.assertEqual(lock.rhs, 2.0)



    def test_level2_degradation_within_tolerance_passes(self):
        delta_q = 0.25 * TOLERANCE
        level2_solution = (1.0 + delta_q, 1.0, 0.0, 0.0, 0.0, 0.0, 6000.0, delta_q)
        backend = FakeBackend(solutions=((1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 6000.0, 0.0), level2_solution))
        result = run_lexicographic_q2(one_interval_model(), backend=backend, run_id="non_solver")
        self.assertEqual(result["status"], "PASS")
        self.assertLessEqual(result["levels"][1]["prior_objective_degradations"][0], TOLERANCE)

    def test_level2_degradation_above_tolerance_fails_closed(self):
        delta_q = 0.75 * TOLERANCE
        level2_solution = (1.0 + delta_q, 1.0, 0.0, 0.0, 0.0, 0.0, 6000.0, delta_q)
        result = evaluate_objective_preservation(
            ((2.0,),), (2.0,), (2.0,), (1.0 + delta_q,), TOLERANCE
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["reason"], "OBJECTIVE_LOCK_UPPER_BOUND_VIOLATION")

    def test_final_preservation_accepts_improvement_within_tolerance(self):
        result = evaluate_objective_preservation(
            ((1.0,),),
            (1.0,),
            (1.0,),
            (1.0 - 0.5 * TOLERANCE,),
            TOLERANCE,
        )
        self.assertEqual(result["status"], "PASS")
        self.assertAlmostEqual(result["objective_improvements"][0], 0.5 * TOLERANCE)

    def test_final_preservation_rejects_abnormal_objective_improvement(self):
        result = evaluate_objective_preservation(
            ((1.0,),),
            (1.0,),
            (1.0,),
            (1.0 - 2.0 * TOLERANCE,),
            TOLERANCE,
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["reason"], "PREVIOUS_LEVEL_REFINEMENT_REQUIRED")
        self.assertEqual(
            result["previous_level_improvement_classes"],
            ("MATERIAL_PREVIOUS_LEVEL_IMPROVEMENT",),
        )
        self.assertLess(result["objective_degradations"][0], -TOLERANCE)
        self.assertGreater(result["objective_absolute_differences"][0], TOLERANCE)


    def test_material_improvement_requests_previous_level_refinement(self):
        result = evaluate_objective_preservation(
            ((1.0,),), (100.0,), (100.0,), (99.0,), TOLERANCE
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["reason"], "PREVIOUS_LEVEL_REFINEMENT_REQUIRED")

    def test_numerically_equivalent_improvement_only_lowers_canonical_anchor(self):
        model = one_interval_model(pv_available=1.0)
        size = model.layout.size
        zero = (0.0,) * size
        level2 = list(zero)
        level2[model.layout.r(0)] = 1e8
        level3 = list(zero)
        level3[model.layout.g(0)] = 1.0
        objectives = (
            replace(model.objective_levels[0], coefficients=zero),
            replace(model.objective_levels[1], coefficients=tuple(level2)),
            replace(model.objective_levels[2], coefficients=tuple(level3)),
        )
        model = replace(model, objective_levels=objectives)
        first = (1.0, 1.0, 0.0, 0.0, 0.0, 1.0, 6000.0, 0.0)
        slightly_better_r = 1.0 - 2e-15
        final = (
            slightly_better_r,
            slightly_better_r,
            0.0,
            0.0,
            0.0,
            slightly_better_r,
            6000.0,
            0.0,
        )
        backend = FakeBackend(solutions=(first, first, final))
        result = run_lexicographic_q2(model, backend=backend, run_id="non_solver")
        self.assertEqual(result["status"], "PASS")
        self.assertLess(result["objective_optima"][1], 1e8)
        update = result["levels"][2]["canonical_anchor_updates"][0]
        self.assertEqual(update["old_anchor"], 1e8)
        self.assertEqual(update["canonical_anchor"], result["objective_optima"][1])

    def test_material_feasible_improvement_triggers_prior_level_refinement(self):
        model = one_interval_model(pv_available=1.0)
        size = model.layout.size
        zero = (0.0,) * size
        level2 = list(zero)
        level2[model.layout.r(0)] = 1.0
        level3 = list(zero)
        level3[model.layout.g(0)] = 1.0
        model = replace(
            model,
            objective_levels=(
                replace(model.objective_levels[0], coefficients=zero),
                replace(model.objective_levels[1], coefficients=tuple(level2)),
                replace(model.objective_levels[2], coefficients=tuple(level3)),
            ),
        )
        old = (1.0, 1.0, 0.0, 0.0, 0.0, 1.0, 6000.0, 0.0)
        better = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 6000.0, 0.0)
        backend = DeterministicFallbackBackend(fail_level=99, fallback_solution=better)
        backend.solutions = (old, old, better, better)
        result = run_lexicographic_q2(model, backend=backend, run_id="non_solver")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(backend.fallback_calls), 1)
        self.assertIn("previous_level_refinement", backend.fallback_calls[0]["run_id"])
        self.assertTrue(result["previous_level_refinement_diagnostics"][0]["FORMALLY_ACCEPTED"])
        self.assertEqual(result["objective_optima"][1], 0.0)

    def test_improving_but_prior_level_infeasible_solution_fails_without_refinement(self):
        model = one_interval_model(pv_available=1.0)
        size = model.layout.size
        zero = (0.0,) * size
        level2 = list(zero)
        level2[model.layout.r(0)] = 1.0
        level3 = list(zero)
        level3[model.layout.g(0)] = 1.0
        model = replace(
            model,
            objective_levels=(
                replace(model.objective_levels[0], coefficients=zero),
                replace(model.objective_levels[1], coefficients=tuple(level2)),
                replace(model.objective_levels[2], coefficients=tuple(level3)),
            ),
        )
        old = (1.0, 1.0, 0.0, 0.0, 0.0, 1.0, 6000.0, 0.0)
        invalid_better = (0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 6000.0, 0.0)
        backend = FakeBackend(solutions=(old, old, invalid_better))
        result = run_lexicographic_q2(model, backend=backend, run_id="non_solver")
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["reason"], "Q2_LEVEL3_INDEPENDENT_CONSTRAINT_VALIDATION_FAILED")






    def test_level3_checks_both_prior_objective_degradations(self):
        solution = (1.0, 1.0, 0.0, 0.0, 0.0, 1.0, 6000.0, 0.0)
        backend = FakeBackend(solutions=(solution, solution, solution))
        result = run_lexicographic_q2(one_interval_model(pv_available=1.0), backend=backend, run_id="non_solver")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["levels"][2]["prior_objective_degradations"], (0.0, 0.0))
        self.assertEqual(result["levels"][2]["prior_objective_absolute_differences"], (0.0, 0.0))
        self.assertEqual(result["levels"][2]["objective_preservation_status"], "PASS")

    def test_level3_zero_lower_bound_is_certified_without_solver_call(self):
        backend = FakeBackend()
        result = run_lexicographic_q2(one_interval_model(), backend=backend, run_id="non_solver")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(backend.calls), 2)
        self.assertEqual(result["levels"][2]["status"], "theoretical_lower_bound_certified")
        self.assertFalse(result["levels"][2]["solver_called"])
        self.assertEqual(result["objective_optima"][2], 0.0)

    def test_level3_above_lower_bound_still_calls_solver(self):
        solution = (1.0, 1.0, 0.0, 0.0, 0.0, 1.0, 6000.0, 0.0)
        backend = FakeBackend(solutions=(solution, solution, solution))
        result = run_lexicographic_q2(one_interval_model(pv_available=1.0), backend=backend, run_id="non_solver")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(backend.calls), 3)
        self.assertEqual(result["level3_optimality_proof"], "SOLVER_REPORTED_OPTIMUM")

    def test_unprovable_lower_bound_cannot_certify_and_calls_solver(self):
        model = one_interval_model()
        bounds = list(model.bounds)
        bounds[model.layout.r(0)] = VariableBound(None, 0.0)
        unbounded_model = replace(model, bounds=tuple(bounds))
        backend = FakeBackend()
        result = run_lexicographic_q2(unbounded_model, backend=backend, run_id="non_solver")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(backend.calls), 3)
        self.assertEqual(
            result["levels"][1]["next_level_lower_bound_certificate"]["status"],
            "NOT_PROVABLE",
        )

    def test_lower_bound_certificate_uses_common_tolerance(self):
        result = certify_objective_at_theoretical_lower_bound(
            (1.0,), ((0.0, None),), (-0.5 * TOLERANCE,), TOLERANCE
        )
        self.assertEqual(result["status"], "CERTIFIED")
        self.assertAlmostEqual(result["theoretical_lower_bound"], 0.0)

    def test_lower_bound_is_strictly_derived_from_coefficients_and_bounds(self):
        self.assertEqual(
            objective_lower_bound_from_bounds((1.0, 2.0), ((0.0, None), (3.0, 5.0))),
            6.0,
        )
        self.assertIsNone(objective_lower_bound_from_bounds((1.0,), ((None, None),)))

    def test_negative_coefficient_uses_finite_upper_bound(self):
        self.assertEqual(objective_lower_bound_from_bounds((-2.0,), ((None, 5.0),)), -10.0)

    def test_negative_coefficient_without_finite_upper_bound_is_not_provable(self):
        self.assertIsNone(objective_lower_bound_from_bounds((-2.0,), ((0.0, None),)))

    def test_q2_curtailment_objective_has_strict_zero_lower_bound(self):
        model = one_interval_model(pv_available=1.0)
        bounds = tuple((bound.lower, bound.upper) for bound in model.bounds)
        level3 = model.objective_levels[2]
        self.assertEqual(objective_lower_bound_from_bounds(level3.coefficients, bounds), 0.0)

    def test_above_tolerance_fails_before_next_level(self):
        backend = FakeBackend(reported_level1_delta=2 * TOLERANCE)
        result = run_lexicographic_q2(one_interval_model(), backend=backend, run_id="non_solver")
        self.assertEqual(result["reason"], "Q2_LEVEL1_OBJECTIVE_RECOMPUTE_MISMATCH")
        self.assertEqual(len(backend.calls), 1)

    def test_three_level_order_and_objectives_are_unchanged(self):
        model = one_interval_model()
        self.assertEqual([item.level for item in model.objective_levels], [1, 2, 3])
        self.assertEqual(
            [item.name for item in model.objective_levels],
            ["q2_total_purchase_cost", "storage_throughput", "pv_curtailment"],
        )
        self.assertEqual(model.objective_levels[0].coefficients[model.layout.q(0)], 2.0)
        self.assertEqual(model.objective_levels[0].coefficients[model.layout.h(0)], 10.0)

    def test_q1_frozen_files_and_q2_formulation_are_unchanged(self):
        expected = {
            "model_contract.py": "effdd95caa111fdfffb06ccab5576e9df18c4b0cc7caebad5a94f759bbae14d2",
            "physical_core_solver.py": "7f9a36d6f3303c16ff5365fa08f4ee953162ccf7dcf96b0144e89c29852a1c3a",
            "q1_model_solver.py": "ac3a90d2bf9eda1ced02f4812c3d6655eb18a02c743a98645f860aba5bba81dd",
            "solver.py": "ea3c95a49055efb52f3bf232c1ff6f485330de9488b4f153ea3fc003a42029df",
            "q2_model_solver.py": "3e39fdf100654d9df1ee6e1f4f6189018b532b9865c433eb48d9f8df69088625",
        }
        for filename, digest in expected.items():
            with self.subTest(filename=filename):
                actual = hashlib.sha256((PYTHON_DIR / filename).read_bytes()).hexdigest()
                self.assertEqual(actual, digest)

    def test_no_weighted_sum_or_epsilon_is_introduced(self):
        source = (PYTHON_DIR / "q2_solver.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("epsilon", source)
        self.assertNotIn("weighted_sum", source)


if __name__ == "__main__":
    unittest.main()
