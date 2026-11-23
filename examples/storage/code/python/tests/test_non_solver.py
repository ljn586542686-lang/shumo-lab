"""不调用 solver 的 Q1 结构与科学验证测试。"""

from __future__ import annotations

import ast
import math
import sys
import unittest
from pathlib import Path


CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from model_contract import (  # noqa: E402
    APPROVED_MODEL_FAMILY,
    APPROVED_ROUTE_ID,
    PhysicalInputs,
    StorageParameters,
    TimeGrid,
    VariableLayout,
)
from physical_core_solver import build_balance_rows, build_soc_rows, build_variable_bounds  # noqa: E402
from q1_model_solver import (  # noqa: E402
    Q1_INITIAL_ENERGY_KWH,
    Q1_TERMINAL_ENERGY_KWH,
    build_exact_objective_lock,
    build_q1_model,
)
from scientific_validation import (  # noqa: E402
    balance_residuals,
    recompute_primary_objective,
    recompute_soc_path,
    simultaneous_charge_discharge_events,
    validate_q1_trajectory,
)
from solver import build_solver_neutral_payload  # noqa: E402
from synthetic_fixtures import arbitrage_margin, build_synthetic_suite  # noqa: E402


def build_flat_q1():
    n = 144
    return build_q1_model((1_000.0,) * n, (0.0,) * n, (1.0,) * n)


class TestModelContract(unittest.TestCase):
    def test_approved_route_and_family(self):
        self.assertEqual(APPROVED_ROUTE_ID, "A：因果确定性滚动线性优化")
        self.assertEqual(APPROVED_MODEL_FAMILY, "continuous_linear_programming")

    def test_144_position_based_intervals(self):
        grid = TimeGrid()
        self.assertEqual(len(grid.indices), 144)
        self.assertEqual(grid.indices[0], 1)
        self.assertEqual(grid.indices[-1], 144)
        self.assertEqual(grid.intervals_minutes[0], (0, 10))
        self.assertEqual(grid.intervals_minutes[-1], (1430, 1440))

    def test_delta_t_and_power_conversion(self):
        grid = TimeGrid()
        storage = StorageParameters.working_e_a()
        self.assertAlmostEqual(grid.delta_t_hours, 1.0 / 6.0)
        self.assertAlmostEqual(storage.interval_energy_limit_kwh(grid), 5_000.0 / 6.0)

    def test_efficiency_configuration_switch(self):
        e_a = StorageParameters.working_e_a()
        e_b = StorageParameters.sensitivity_e_b()
        self.assertEqual((e_a.eta_charge, e_a.eta_discharge), (0.9, 0.9))
        self.assertAlmostEqual(e_b.eta_charge, math.sqrt(0.9))
        self.assertAlmostEqual(e_b.eta_discharge, math.sqrt(0.9))


class TestQ1MatrixStructure(unittest.TestCase):
    def test_q1_boundary_builder(self):
        model = build_flat_q1()
        self.assertEqual(model.initial_energy_kwh, Q1_INITIAL_ENERGY_KWH)
        self.assertEqual(model.terminal_energy_kwh, Q1_TERMINAL_ENERGY_KWH)
        terminal = model.bounds[model.layout.energy(143)]
        self.assertEqual((terminal.lower, terminal.upper), (6_000.0, 6_000.0))

    def test_curtailment_upper_bound_is_pv(self):
        inputs = PhysicalInputs.from_sequences((100.0,), (37.5,), (1.0,))
        grid = TimeGrid(n_intervals=1)
        layout = VariableLayout(1)
        bounds = build_variable_bounds(inputs, grid, StorageParameters.working_e_a(), layout)
        self.assertEqual(bounds[layout.r(0)].lower, 0.0)
        self.assertEqual(bounds[layout.r(0)].upper, 37.5)

    def test_negative_grid_export_is_not_in_domain(self):
        model = build_flat_q1()
        for index in model.layout.block_indices("x"):
            self.assertEqual(model.bounds[index].lower, 0.0)
            self.assertIsNone(model.bounds[index].upper)

    def test_balance_matrix_signs(self):
        inputs = PhysicalInputs.from_sequences((11.0,), (4.0,), (2.0,))
        layout = VariableLayout(1)
        row = build_balance_rows(inputs, layout)[0]
        self.assertEqual(row.coefficient(layout.x(0)), 1.0)
        self.assertEqual(row.coefficient(layout.c(0)), -1.0)
        self.assertEqual(row.coefficient(layout.d(0)), 1.0)
        self.assertEqual(row.coefficient(layout.r(0)), -1.0)
        self.assertEqual(row.rhs, 7.0)

    def test_soc_matrix_signs(self):
        grid = TimeGrid(n_intervals=2)
        storage = StorageParameters.working_e_a()
        layout = VariableLayout(2)
        first, second = build_soc_rows(grid, storage, layout, 6_000.0)
        self.assertAlmostEqual(first.coefficient(layout.c(0)), -0.9)
        self.assertAlmostEqual(first.coefficient(layout.d(0)), 1.0 / 0.9)
        self.assertEqual(first.coefficient(layout.energy(0)), 1.0)
        self.assertEqual(first.rhs, 6_000.0)
        self.assertEqual(second.coefficient(layout.energy(0)), -1.0)
        self.assertEqual(second.coefficient(layout.energy(1)), 1.0)
        self.assertEqual(second.rhs, 0.0)

    def test_primary_objective_only_prices_x(self):
        model = build_flat_q1()
        objective = model.primary_objective
        self.assertTrue(all(objective[index] == 1.0 for index in model.layout.block_indices("x")))
        for block in ("c", "d", "r", "E"):
            self.assertTrue(all(objective[index] == 0.0 for index in model.layout.block_indices(block)))

    def test_lexicographic_levels_are_separate(self):
        model = build_flat_q1()
        self.assertEqual([level.level for level in model.objective_levels], [1, 2, 3])
        self.assertNotIn("epsilon", " ".join(level.rule for level in model.objective_levels).lower())
        lock = build_exact_objective_lock(model.primary_objective, 123.0, "lock_level_1")
        self.assertEqual(lock.rhs, 123.0)
        self.assertEqual(len(lock.coefficients), 144)

    def test_solver_neutral_payload_declares_repository_backend_without_calling_it(self):
        payload = build_solver_neutral_payload(build_flat_q1())
        self.assertEqual(payload["solver_backend"], "math_core.optimization.linear_program")
        self.assertEqual(payload["solver_execution_status"], "READY_FOR_AUTHORIZED_SOLVER_EXECUTION")
        self.assertEqual(payload["variable_domain"], "CONTINUOUS")


class TestIndependentScientificChecks(unittest.TestCase):
    def test_soc_hand_calculation(self):
        fixture = build_synthetic_suite()["TEST_D_SOC_HAND_CALC"]
        storage = StorageParameters.working_e_a()
        actual = recompute_soc_path(
            fixture.initial_energy_kwh,
            fixture.prescribed_charge_kwh,
            fixture.prescribed_discharge_kwh,
            storage,
        )
        expected = (
            6_000.0 + 0.9 * 100.0,
            6_000.0 + 0.9 * 100.0 - 45.0 / 0.9,
            6_000.0 + 0.9 * 100.0 - 45.0 / 0.9 + 0.9 * 20.0,
        )
        self.assertEqual(len(actual), 3)
        for observed, target in zip(actual, expected):
            self.assertAlmostEqual(observed, target)

    def test_balance_residual_direction(self):
        inputs = PhysicalInputs.from_sequences((8.0,), (3.0,), (2.0,))
        residual = balance_residuals((6.0,), (2.0,), (1.0,), (0.0,), inputs)
        self.assertEqual(residual, (0.0,))

    def test_independent_objective_recomputation(self):
        self.assertEqual(recompute_primary_objective((2.0, 3.0), (5.0, 7.0)), 31.0)

    def test_simultaneous_charge_discharge_diagnostic(self):
        inputs = PhysicalInputs.from_sequences((10.0,), (12.0,), (1.5,))
        events = simultaneous_charge_discharge_events((2.0,), (1.0,), (6_000.0,), inputs)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["k"], 1)
        self.assertTrue(events[0]["pv_surplus_status"])
        self.assertEqual(events[0]["classification"], "TO_BE_VERIFIED")

    def test_full_validator_accepts_hand_constructed_feasible_path(self):
        inputs = PhysicalInputs.from_sequences((100.0,) * 3, (0.0,) * 3, (1.0,) * 3)
        report = validate_q1_trajectory(
            grid_purchase_kwh=(100.0,) * 3,
            charge_kwh=(0.0,) * 3,
            discharge_kwh=(0.0,) * 3,
            curtailed_pv_kwh=(0.0,) * 3,
            energy_path_kwh=(6_000.0,) * 3,
            inputs=inputs,
            storage=StorageParameters.working_e_a(),
            grid=TimeGrid(n_intervals=3),
        )
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["primary_objective_recomputed"], 300.0)

    def test_pv_surplus_can_be_curtailed_without_export_or_soc_overflow(self):
        fixture = build_synthetic_suite()["TEST_C_PV_SURPLUS_FULL_BATTERY"]
        n = len(fixture.inputs.load_kwh)
        curtailment = tuple(
            max(v_k - l_k, 0.0)
            for l_k, v_k in zip(fixture.inputs.load_kwh, fixture.inputs.pv_available_kwh)
        )
        grid_purchase = tuple(
            max(l_k - v_k, 0.0)
            for l_k, v_k in zip(fixture.inputs.load_kwh, fixture.inputs.pv_available_kwh)
        )
        report = validate_q1_trajectory(
            grid_purchase_kwh=grid_purchase,
            charge_kwh=(0.0,) * n,
            discharge_kwh=(0.0,) * n,
            curtailed_pv_kwh=curtailment,
            energy_path_kwh=(fixture.initial_energy_kwh,) * n,
            inputs=fixture.inputs,
            storage=StorageParameters.working_e_a(),
            initial_energy_kwh=fixture.initial_energy_kwh,
            terminal_energy_kwh=fixture.terminal_energy_kwh,
        )
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(fixture.initial_energy_kwh, 6_000.0)
        self.assertEqual(fixture.terminal_energy_kwh, 6_000.0)
        self.assertTrue(
            any(
                v_k > l_k
                for l_k, v_k in zip(fixture.inputs.load_kwh, fixture.inputs.pv_available_kwh)
            )
        )
        self.assertGreater(sum(curtailment), 0.0)

    def test_arbitrage_threshold_direction(self):
        storage = StorageParameters.working_e_a()
        threshold = 1.0 / (storage.eta_charge * storage.eta_discharge)
        self.assertLess(arbitrage_margin(1.0, 0.99 * threshold, storage), 0.0)
        self.assertGreater(arbitrage_margin(1.0, 1.01 * threshold, storage), 0.0)

    def test_all_synthetic_fixtures_present(self):
        suite = build_synthetic_suite()
        expected = {
            "TEST_A_FLAT_PRICE_NO_PV",
            "TEST_B_PRICE_ARBITRAGE_B1",
            "TEST_B_PRICE_ARBITRAGE_B2",
            "TEST_C_PV_SURPLUS_FULL_BATTERY",
            "TEST_D_SOC_HAND_CALC",
            "TEST_E_Q1_TERMINAL_SOC",
            "TEST_F_POWER_LIMIT",
            "TEST_G_NO_NEGATIVE_VARIABLES",
            "TEST_H_BALANCE",
            "TEST_I_SIMULTANEOUS_CD",
            "TEST_J_OBJECTIVE_RECOMPUTE",
        }
        self.assertEqual(set(suite), expected)
        self.assertFalse(suite["TEST_D_SOC_HAND_CALC"].solver_required)

    def test_case_code_uses_only_repository_lp_backend(self):
        banned_modules = {"scipy", "cvxpy", "pulp", "gurobipy", "ortools", "pyomo"}
        banned_calls = {"linprog", "milp", "minimize", "solve", "optimize"}
        # This test covers the Q1 production path. Q2 also has a SciPy fallback.
        for name in ("model_contract", "physical_core_solver", "q1_model_solver", "solver", "scientific_validation"):
            path = CODE_DIR / (name + ".py")
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertFalse(any(alias.name.split(".")[0] in banned_modules for alias in node.names))
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotIn((node.module or "").split(".")[0], banned_modules)
                elif isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        self.assertNotIn(node.func.id, banned_calls)
                    elif isinstance(node.func, ast.Attribute):
                        repository_backend_callers = {"solver.py", "q2_solver.py", "diagnose_q2_jan2.py"}
                        if path.name not in repository_backend_callers:
                            self.assertNotIn(node.func.attr, banned_calls)


if __name__ == "__main__":
    unittest.main()
