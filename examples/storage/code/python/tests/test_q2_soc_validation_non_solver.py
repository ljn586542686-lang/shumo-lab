"""Q2 SOC scientific-validation consistency tests; no solver is invoked."""

from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path


PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from model_contract import PhysicalInputs, StorageParameters  # noqa: E402
from q2_scientific_validation import validate_q2_day  # noqa: E402


def validate_energy_path(energy_path):
    n = len(energy_path)
    zeros = (0.0,) * n
    inputs = PhysicalInputs.from_sequences(zeros, zeros, (1.0,) * n)
    return validate_q2_day(
        planned_kwh=zeros,
        ordinary_used_kwh=zeros,
        emergency_kwh=zeros,
        charge_kwh=zeros,
        discharge_kwh=zeros,
        curtailed_pv_kwh=zeros,
        energy_path_kwh=energy_path,
        unused_plan_kwh=zeros,
        actual_inputs=inputs,
        initial_energy_kwh=6000.0,
        storage=StorageParameters.working_e_a(),
        future_leakage_count=0,
        past_decision_mutation_count=0,
    )


class Q2SocValidationConsistencyTests(unittest.TestCase):
    def test_row_residuals_pass_while_cumulative_diagnostic_can_exceed_tolerance(self):
        result = validate_energy_path((6000.00000005, 6000.00000010, 6000.00000015))
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["checks"]["soc_recursion"])
        self.assertLessEqual(result["max_abs_soc_equation_residual"], 1e-7)
        self.assertGreater(result["max_cumulative_soc_recalculation_error"], 1e-7)
        self.assertEqual(
            result["max_abs_soc_recalculation_error"],
            result["max_cumulative_soc_recalculation_error"],
        )

    def test_single_soc_equation_residual_above_tolerance_fails_closed(self):
        result = validate_energy_path((6000.0000002, 6000.0000002, 6000.0000002))
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["checks"]["soc_recursion"])
        self.assertGreater(result["max_abs_soc_equation_residual"], 1e-7)

    def test_true_soc_bound_violation_above_tolerance_fails_closed(self):
        n = 1
        zeros = (0.0,) * n
        inputs = PhysicalInputs.from_sequences(zeros, zeros, (1.0,) * n)
        result = validate_q2_day(
            planned_kwh=zeros,
            ordinary_used_kwh=zeros,
            emergency_kwh=zeros,
            charge_kwh=zeros,
            discharge_kwh=zeros,
            curtailed_pv_kwh=zeros,
            energy_path_kwh=(1199.9999998,),
            unused_plan_kwh=zeros,
            actual_inputs=inputs,
            initial_energy_kwh=1200.0,
            storage=StorageParameters.working_e_a(),
            future_leakage_count=0,
            past_decision_mutation_count=0,
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["checks"]["soc_bounds"])

    def test_validator_default_tolerance_remains_one_e_minus_seven(self):
        default = inspect.signature(validate_q2_day).parameters["tolerance"].default
        self.assertEqual(default, 1e-7)


if __name__ == "__main__":
    unittest.main()
