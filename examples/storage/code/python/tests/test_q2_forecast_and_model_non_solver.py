"""Q2 forecast、LP 结构与锁定语义的非求解测试。"""

from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path


PYTHON_DIR = Path(__file__).resolve().parents[1]
CASE_ROOT = PYTHON_DIR.parents[1]
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from formal_q2_provider import load_formal_q2_data  # noqa: E402
from q2_data_contract import POSITIONS_PER_DAY, build_information_set  # noqa: E402
from q2_forecasting import (  # noqa: E402
    FORMAL_FORECAST_START_DATE,
    JanuaryBootstrapPredictionProvider,
    SelectedCausalPredictionProvider,
    select_forecast_model,
)
from q2_model_solver import EMERGENCY_MULTIPLIER, build_q2_model  # noqa: E402
from soc import canonicalize_soc_for_propagation  # noqa: E402


class Q2ForecastAndModelTests(unittest.TestCase):


    def test_selected_provider_bootstrap_is_causal(self):
        provider = JanuaryBootstrapPredictionProvider()
        empty = build_information_set(date(2025, 1, 1), 6000.0, {})
        self.assertEqual(sum(provider.forecast_load(date(2025, 1, 1), empty)), 0.0)
        self.assertEqual(provider.future_leakage_count, 0)





    def test_q2_model_has_three_physical_and_realization_rows_per_position(self):
        n = 4
        model = build_q2_model((1.0,) * n, (0.0,) * n, (1.0,) * n, initial_energy_kwh=6000.0)
        self.assertEqual(model.layout.size, 8 * n)
        self.assertEqual(len(model.equalities), 3 * n)
        self.assertEqual(len(model.bounds), 8 * n)

    def test_financial_commitment_realization_is_g_plus_unused_slack(self):
        model = build_q2_model((1.0,), (0.0,), (1.0,), initial_energy_kwh=6000.0)
        row = next(item for item in model.equalities if item.name == "planned_realization_k1")
        coefficients = dict(row.coefficients)
        self.assertEqual(coefficients[model.layout.q(0)], -1.0)
        self.assertEqual(coefficients[model.layout.g(0)], 1.0)
        self.assertEqual(coefficients[model.layout.slack(0)], 1.0)

    def test_emergency_objective_coefficient_is_exactly_five_times_price(self):
        model = build_q2_model((1.0,), (0.0,), (2.0,), initial_energy_kwh=6000.0)
        primary = model.objective_levels[0].coefficients
        self.assertEqual(EMERGENCY_MULTIPLIER, 5.0)
        self.assertEqual(primary[model.layout.q(0)], 2.0)
        self.assertEqual(primary[model.layout.h(0)], 10.0)

    def test_daily_terminal_reset_is_absent(self):
        free = build_q2_model((1.0,), (0.0,), (1.0,), initial_energy_kwh=6000.0)
        fixed = build_q2_model((1.0,), (0.0,), (1.0,), initial_energy_kwh=6000.0, terminal_energy_kwh=6000.0)
        self.assertEqual((free.bounds[free.layout.energy(0)].lower, free.bounds[free.layout.energy(0)].upper), (1200.0, 10800.0))
        self.assertEqual((fixed.bounds[fixed.layout.energy(0)].lower, fixed.bounds[fixed.layout.energy(0)].upper), (6000.0, 6000.0))

    def test_soc_just_below_lower_bound_is_canonicalized_with_evidence(self):
        canonical, event = canonicalize_soc_for_propagation(
            1199.99999995,
            lower_bound_kwh=1200.0,
            upper_bound_kwh=10800.0,
            tolerance=1e-7,
            day=date(2025, 1, 1),
            interval_one_based=39,
            source_level="LEVEL2_THEORETICAL_LOWER_BOUND_CERTIFICATE",
        )
        self.assertEqual(canonical, 1200.0)
        self.assertEqual(event["RAW_SOC"], 1199.99999995)
        self.assertAlmostEqual(event["CORRECTION"], 5e-8)
        self.assertEqual(event["BOUND"], "LOWER")

    def test_soc_just_above_upper_bound_is_canonicalized(self):
        canonical, event = canonicalize_soc_for_propagation(
            10800.00000005,
            lower_bound_kwh=1200.0,
            upper_bound_kwh=10800.0,
            tolerance=1e-7,
            day=date(2025, 1, 1),
            interval_one_based=1,
            source_level="LEVEL3_SOLVER",
        )
        self.assertEqual(canonical, 10800.0)
        self.assertEqual(event["BOUND"], "UPPER")

    def test_exact_soc_bounds_are_unchanged_without_event(self):
        for value in (1200.0, 10800.0):
            canonical, event = canonicalize_soc_for_propagation(
                value,
                lower_bound_kwh=1200.0,
                upper_bound_kwh=10800.0,
                tolerance=1e-7,
                day=date(2025, 1, 1),
                interval_one_based=1,
                source_level="LEVEL3_SOLVER",
            )
            self.assertEqual(canonical, value)
            self.assertIsNone(event)

    def test_true_lower_soc_violation_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "Q2_TRUE_SOC_PROPAGATION_VIOLATION"):
            canonicalize_soc_for_propagation(
                1199.999,
                lower_bound_kwh=1200.0,
                upper_bound_kwh=10800.0,
                tolerance=1e-7,
                day=date(2025, 1, 1),
                interval_one_based=1,
                source_level="LEVEL3_SOLVER",
            )

    def test_true_upper_soc_violation_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "Q2_TRUE_SOC_PROPAGATION_VIOLATION"):
            canonicalize_soc_for_propagation(
                10800.001,
                lower_bound_kwh=1200.0,
                upper_bound_kwh=10800.0,
                tolerance=1e-7,
                day=date(2025, 1, 1),
                interval_one_based=1,
                source_level="LEVEL3_SOLVER",
            )

    def test_canonical_soc_can_construct_next_model(self):
        canonical, _ = canonicalize_soc_for_propagation(
            1199.99999995,
            lower_bound_kwh=1200.0,
            upper_bound_kwh=10800.0,
            tolerance=1e-7,
            day=date(2025, 1, 1),
            interval_one_based=39,
            source_level="LEVEL2_THEORETICAL_LOWER_BOUND_CERTIFICATE",
        )
        model = build_q2_model((1.0,), (0.0,), (1.0,), initial_energy_kwh=canonical)
        self.assertEqual(model.initial_energy_kwh, 1200.0)

    def test_soc_canonicalization_is_not_unconditional_clip(self):
        source = (PYTHON_DIR / "soc.py").read_text(encoding="utf-8").replace(" ", "")
        self.assertNotIn("min(max(", source)


if __name__ == "__main__":
    unittest.main()
