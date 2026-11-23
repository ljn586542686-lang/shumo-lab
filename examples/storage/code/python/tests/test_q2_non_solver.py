"""Q2 数据、因果边界和双分支骨架的非求解测试。"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path


PYTHON_DIR = Path(__file__).resolve().parents[1]
CASE_ROOT = PYTHON_DIR.parents[1]
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from q2_data_contract import (  # noqa: E402
    ATTACHMENT2_DATE_RANGE,
    ATTACHMENT2_DAYS,
    ATTACHMENT2_RELATIVE_PATH,
    POSITIONS_PER_DAY,
    RESULT2_DATE_RANGE,
    RESULT2_DAYS,
    RESULT2_RELATIVE_PATH,
    TIME_MAPPING,
    audit_attachment2,
    audit_result2,
    build_information_set,
    kw_to_kwh,
    validate_formal_hashes,
)
from q2_model_skeleton import (  # noqa: E402
    EMERGENCY_MULTIPLIER,
    PlannedPurchasePhysicalMode,
    Q2Configuration,
    StorageExecutionMode,
    build_q2_day_skeleton,
    build_settlement_layer,
    evaluate_base_settlement,
    next_day_initial_soc,
)


ZERO = (0.0,) * POSITIONS_PER_DAY
ONE = (1.0,) * POSITIONS_PER_DAY


class DeterministicProvider:
    def forecast_load(self, day, information_set):
        return (600.0,) * POSITIONS_PER_DAY

    def forecast_pv(self, day, information_set):
        return (100.0,) * POSITIONS_PER_DAY


def valid_config(**overrides):
    values = {
        "storage_execution_mode": StorageExecutionMode.FIXED_STORAGE_SCHEDULE,
        "planned_purchase_physical_mode": PlannedPurchasePhysicalMode.COMMITTED_AND_PHYSICALLY_DELIVERED,
        "terminal_horizon_mode": "EXPLICIT_TEST_POLICY",
        "prediction_provider": DeterministicProvider(),
    }
    values.update(overrides)
    return Q2Configuration(**values)




class Q2CausalityAndUnitsTests(unittest.TestCase):
    def test_kw_to_kwh_uses_one_sixth_hour(self):
        converted = kw_to_kwh((5000.0,) * POSITIONS_PER_DAY)
        self.assertAlmostEqual(converted[0], 5000.0 / 6.0)

    def test_future_actual_leakage_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Q2_FUTURE_ACTUAL_LEAKAGE"):
            build_information_set(date(2025, 2, 1), 6000.0, {date(2025, 2, 1): (ZERO, ZERO)})

    def test_january_is_valid_history_for_february_first(self):
        info = build_information_set(date(2025, 2, 1), 6000.0, {date(2025, 1, 31): (ZERO, ZERO)})
        self.assertEqual(info.observed_history[0].day, date(2025, 1, 31))

    def test_cross_day_initial_soc_equals_previous_final(self):
        self.assertEqual(next_day_initial_soc(4321.5), 4321.5)

    def test_daily_soc_reset_is_not_implicit(self):
        self.assertEqual(next_day_initial_soc(7000.0), 7000.0)
        self.assertNotEqual(next_day_initial_soc(7000.0), 6000.0)


class Q2SkeletonTests(unittest.TestCase):
    def test_emergency_multiplier_is_five(self):
        layer = build_settlement_layer(ONE)
        self.assertEqual(EMERGENCY_MULTIPLIER, 5.0)
        self.assertEqual(layer.emergency_coefficients[0], 5.0)
        self.assertEqual(evaluate_base_settlement(ONE, ONE, ONE), POSITIONS_PER_DAY * 6.0)

    def test_storage_execution_mode_is_required(self):
        with self.assertRaisesRegex(ValueError, "Q2_STORAGE_EXECUTION_MODE_REQUIRED"):
            valid_config(storage_execution_mode=None).validate()

    def test_planned_purchase_physical_mode_is_required(self):
        with self.assertRaisesRegex(ValueError, "Q2_PLANNED_PURCHASE_PHYSICAL_MODE_REQUIRED"):
            valid_config(planned_purchase_physical_mode=None).validate()

    def test_terminal_horizon_mode_is_required(self):
        with self.assertRaisesRegex(ValueError, "Q2_TERMINAL_HORIZON_MODE_REQUIRED"):
            valid_config(terminal_horizon_mode="PENDING").validate()

    def test_prediction_provider_is_required(self):
        with self.assertRaisesRegex(ValueError, "Q2_PREDICTION_PROVIDER_REQUIRED"):
            valid_config(prediction_provider=None).validate()

    def test_both_semantic_branches_reuse_q1_physical_core(self):
        info = build_information_set(date(2025, 2, 1), 6000.0, {date(2025, 1, 31): (ZERO, ZERO)})
        for mode in PlannedPurchasePhysicalMode:
            skeleton = build_q2_day_skeleton(
                date(2025, 2, 1),
                info,
                ONE,
                valid_config(planned_purchase_physical_mode=mode),
            )
            self.assertEqual(len(skeleton.physical_equalities), 2 * POSITIONS_PER_DAY)
            self.assertEqual(len(skeleton.physical_bounds), 5 * POSITIONS_PER_DAY)
            self.assertTrue(skeleton.artificial_storage_dissipation_forbidden)


if __name__ == "__main__":
    unittest.main()
