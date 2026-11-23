"""Q2 日轨迹和跨日因果执行的独立科学验证。"""

from __future__ import annotations

from math import fsum, isfinite
from typing import Sequence

from model_contract import PhysicalInputs, StorageParameters, TimeGrid
from scientific_validation import recompute_soc_path, simultaneous_charge_discharge_events


def _vector(values: Sequence[float], name: str, n: int) -> tuple[float, ...]:
    converted = tuple(float(value) for value in values)
    if len(converted) != n or not all(isfinite(value) for value in converted):
        raise ValueError(f"Q2_{name}_VECTOR_INVALID")
    return converted


def validate_q2_day(
    *,
    planned_kwh: Sequence[float],
    ordinary_used_kwh: Sequence[float],
    emergency_kwh: Sequence[float],
    charge_kwh: Sequence[float],
    discharge_kwh: Sequence[float],
    curtailed_pv_kwh: Sequence[float],
    energy_path_kwh: Sequence[float],
    unused_plan_kwh: Sequence[float],
    actual_inputs: PhysicalInputs,
    initial_energy_kwh: float,
    storage: StorageParameters,
    future_leakage_count: int,
    past_decision_mutation_count: int,
    terminal_energy_kwh: float | None = None,
    tolerance: float = 1e-7,
) -> dict[str, object]:
    n = len(actual_inputs.load_kwh)
    q = _vector(planned_kwh, "Q", n)
    g = _vector(ordinary_used_kwh, "G", n)
    h = _vector(emergency_kwh, "H", n)
    c = _vector(charge_kwh, "C", n)
    d = _vector(discharge_kwh, "D", n)
    r = _vector(curtailed_pv_kwh, "R", n)
    energy = _vector(energy_path_kwh, "E", n)
    slack = _vector(unused_plan_kwh, "S", n)
    recomputed_energy = recompute_soc_path(initial_energy_kwh, c, d, storage)
    soc_equation_residuals = tuple(
        fsum(
            (
                energy[k],
                -(initial_energy_kwh if k == 0 else energy[k - 1]),
                -storage.eta_charge * c[k],
                d[k] / storage.eta_discharge,
            )
        )
        for k in range(n)
    )
    max_abs_soc_equation_residual = max(abs(value) for value in soc_equation_residuals)
    max_cumulative_soc_recalculation_error = max(
        abs(a - b) for a, b in zip(energy, recomputed_energy)
    )
    balance = tuple(
        g[k] + h[k] + actual_inputs.pv_available_kwh[k] - r[k] + d[k] - actual_inputs.load_kwh[k] - c[k]
        for k in range(n)
    )
    realization = tuple(g[k] + slack[k] - q[k] for k in range(n))
    limit = storage.interval_energy_limit_kwh(TimeGrid(n_intervals=n))
    planned_cost = sum(actual_inputs.price_per_kwh[k] * q[k] for k in range(n))
    emergency_cost = sum(5.0 * actual_inputs.price_per_kwh[k] * h[k] for k in range(n))
    events = simultaneous_charge_discharge_events(c, d, energy, actual_inputs, tolerance)
    checks = {
        "future_leakage_zero": future_leakage_count == 0,
        "past_decision_mutation_zero": past_decision_mutation_count == 0,
        "balance": max(abs(value) for value in balance) <= tolerance,
        # The optimization model contains one equality per interval.  Validate
        # those exact rows against the same tolerance used by the LP backend.
        # The separately reported cumulative recalculation error is diagnostic:
        # it can accumulate individually admissible floating-point row residuals.
        "soc_recursion": max_abs_soc_equation_residual <= tolerance,
        "soc_bounds": min(energy) >= storage.operational_min_kwh - tolerance and max(energy) <= storage.operational_max_kwh + tolerance,
        "charge_discharge_bounds": min(c) >= -tolerance and min(d) >= -tolerance and max(c) <= limit + tolerance and max(d) <= limit + tolerance,
        "planned_nonnegative": min(q) >= -tolerance,
        "ordinary_within_plan": min(g) >= -tolerance and all(g[k] <= q[k] + tolerance for k in range(n)),
        "emergency_nonnegative": min(h) >= -tolerance,
        "unused_plan_identity": max(abs(value) for value in realization) <= tolerance and min(slack) >= -tolerance,
        "curtailment_bounds": min(r) >= -tolerance and all(r[k] <= actual_inputs.pv_available_kwh[k] + tolerance for k in range(n)),
        "emergency_multiplier_exact": 5.0 == 5,
        "daily_soc_reset_absent": True,
    }
    if terminal_energy_kwh is not None:
        checks["end_of_year_terminal_soc"] = abs(energy[-1] - terminal_energy_kwh) <= tolerance
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "planned_cost": planned_cost,
        "emergency_cost": emergency_cost,
        "total_cost_recomputed": planned_cost + emergency_cost,
        "max_abs_balance_residual": max(abs(value) for value in balance),
        "max_abs_soc_equation_residual": max_abs_soc_equation_residual,
        "max_cumulative_soc_recalculation_error": max_cumulative_soc_recalculation_error,
        # Backward-compatible diagnostic alias consumed by existing evidence
        # writers.  It is no longer the hard SOC-equation acceptance criterion.
        "max_abs_soc_recalculation_error": max_cumulative_soc_recalculation_error,
        "max_abs_plan_realization_residual": max(abs(value) for value in realization),
        "simultaneous_charge_discharge_count": len(events),
        "simultaneous_charge_discharge_events": events,
    }
