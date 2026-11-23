"""Q3 information, settlement, and rolling-plan layer over the frozen Q2 core.

This module does not execute the 334-day formal run.  It binds Q3 to the
accepted Q2 predictor, physical LP, lexicographic solver, and independent
validator while adding only the Q3 release-time and adjustment semantics.
"""

from __future__ import annotations

import importlib.util

import json

from dataclasses import dataclass

from datetime import date

from hashlib import sha256

from math import fsum, isfinite

from pathlib import Path

from types import ModuleType

from typing import Mapping, Sequence

from model_contract import PhysicalInputs, StorageParameters

from physical_core_solver import SparseEqualityRow, VariableBound

from q1_model_solver import ObjectiveLevel

from q2_model_solver import Q2LinearProgram, build_q2_model

from q2_scientific_validation import validate_q2_day

from q2_solver import run_lexicographic_q2

from soc import canonicalize_soc_for_propagation

POSITIONS_PER_DAY = 144

INTERVALS_PER_HOUR = 6

DELTA_T_HOURS = 1.0 / 6.0

RELEASE_HOURS = (0, 6, 12, 18)

RELEASE_COMPLETED_INTERVALS = {0: 0, 6: 36, 12: 72, 18: 108}

MIDNIGHT_PV_ANCHOR_STATUS = "RESOLVED"

MIDNIGHT_PV_ANCHOR_KW = 0.0

MIDNIGHT_PV_ANCHOR_CLASS = "PHYSICAL_BOUNDARY_CONDITION"

MIDNIGHT_PV_ANCHOR_SOURCE = "PHYSICAL_ZERO_WITH_DATA_CONSISTENCY_CHECK"

EMERGENCY_MULTIPLIER = 5.0

ADJUST_UP_MULTIPLIER = 1.5

ADJUST_DOWN_REFUND_MULTIPLIER = 0.5

Q3_FORMAL_START = date(2025, 2, 1)

Q3_FORMAL_END = date(2025, 12, 31)

Q3_FORMAL_DAYS = 334

Q3_TERMINAL_SOC_KWH = 6000.0

Q3_TERMINAL_SOC_SOURCE = "MODELING_DECISION"

Q3_BUILD_INITIAL_PLAN_MODEL = build_q2_model

Q3_BUILD_REALTIME_RECOURSE_MODEL = build_q2_model

Q3_RUN_LEXICOGRAPHIC = run_lexicographic_q2

Q3_VALIDATE_OPERATION = validate_q2_day

Q3_CANONICALIZE_PROPAGATED_SOC = canonicalize_soc_for_propagation

def _finite_vector(values: Sequence[float], name: str, expected: int | None = None) -> tuple[float, ...]:
    vector = tuple(float(value) for value in values)
    if expected is not None and len(vector) != expected:
        raise ValueError(f"Q3_{name}_LENGTH_MISMATCH")
    if not vector or not all(isfinite(value) for value in vector):
        raise ValueError(f"Q3_{name}_INVALID")
    return vector

def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

@dataclass(frozen=True)
class Q3PVForecastCurve:
    release_hour: int
    hourly_point_power_kw: tuple[float, ...]
    ten_minute_node_power_kw: tuple[float, ...]
    ten_minute_interval_energy_kwh: tuple[float, ...]

    @property
    def forecast_point_offsets_hours(self) -> tuple[int, ...]:
        return tuple(range(1, 25))

    @property
    def completed_intervals(self) -> int:
        return RELEASE_COMPLETED_INTERVALS[self.release_hour]

    @property
    def same_day_control_interval_count(self) -> int:
        return POSITIONS_PER_DAY - self.completed_intervals

    @property
    def same_day_control_energy_kwh(self) -> tuple[float, ...]:
        return self.ten_minute_interval_energy_kwh[: self.same_day_control_interval_count]

    @property
    def next_day_commitment_before_midnight_count(self) -> int:
        return 0

def build_pv_point_forecast_curve(
    *,
    release_hour: int,
    current_anchor_kw: float,
    forecast_1h_to_24h_kw: Sequence[float],
) -> Q3PVForecastCurve:
    """Map release-time point forecasts to 10-minute nodes and trapezoid energy."""

    if release_hour not in RELEASE_HOURS:
        raise ValueError("Q3_RELEASE_HOUR_INVALID")
    anchor = float(current_anchor_kw)
    forecasts = _finite_vector(forecast_1h_to_24h_kw, "PV_FORECAST", 24)
    if not isfinite(anchor) or anchor < 0.0 or min(forecasts) < 0.0:
        raise ValueError("Q3_PV_FORECAST_NEGATIVE_OR_NONFINITE")
    if release_hour == 0 and anchor != MIDNIGHT_PV_ANCHOR_KW:
        raise ValueError("Q3_MIDNIGHT_PV_ANCHOR_MUST_BE_ZERO")

    hourly = (anchor,) + forecasts
    nodes: list[float] = []
    for hour in range(24):
        left = hourly[hour]
        right = hourly[hour + 1]
        for substep in range(INTERVALS_PER_HOUR):
            fraction = substep / INTERVALS_PER_HOUR
            nodes.append(left + fraction * (right - left))
    nodes.append(hourly[-1])
    energies = tuple(
        (nodes[index] + nodes[index + 1]) * 0.5 * DELTA_T_HOURS
        for index in range(POSITIONS_PER_DAY)
    )
    return Q3PVForecastCurve(
        release_hour=release_hour,
        hourly_point_power_kw=hourly,
        ten_minute_node_power_kw=tuple(nodes),
        ten_minute_interval_energy_kwh=energies,
    )

def release_pv_anchor_from_completed_actual(
    release_hour: int,
    completed_actual_pv_kw: Sequence[float] = (),
) -> tuple[float, int | None]:
    """Return the legal release anchor and zero-based Attachment2 position."""

    if release_hour not in RELEASE_HOURS:
        raise ValueError("Q3_RELEASE_HOUR_INVALID")
    completed = RELEASE_COMPLETED_INTERVALS[release_hour]
    values = tuple(float(value) for value in completed_actual_pv_kw)
    if release_hour == 0:
        if values:
            raise ValueError("Q3_MIDNIGHT_FUTURE_ACTUAL_PV_FORBIDDEN")
        return MIDNIGHT_PV_ANCHOR_KW, None
    if len(values) != completed:
        raise ValueError("Q3_COMPLETED_ACTUAL_PV_PREFIX_REQUIRED")
    if not all(isfinite(value) and value >= 0.0 for value in values):
        raise ValueError("Q3_COMPLETED_ACTUAL_PV_INVALID")
    return values[-1], completed - 1

def audit_information_access(
    *,
    release_hour: int,
    accessed_actual_position_indices: Sequence[int],
    accessed_forecast_release_hours: Sequence[int],
) -> dict[str, int | str]:
    """Audit actual-data and forecast-release access at one decision time."""

    if release_hour not in RELEASE_HOURS:
        raise ValueError("Q3_RELEASE_HOUR_INVALID")
    completed = RELEASE_COMPLETED_INTERVALS[release_hour]
    future_actual = sum(int(index) < 0 or int(index) >= completed for index in accessed_actual_position_indices)
    unreleased = sum(int(hour) not in RELEASE_HOURS or int(hour) > release_hour for hour in accessed_forecast_release_hours)
    return {
        "status": "PASS" if future_actual == 0 and unreleased == 0 else "FAIL",
        "future_actual_data_leakage_count": future_actual,
        "unreleased_forecast_leakage_count": unreleased,
    }

def apply_future_plan_update(
    previous_effective_plan_kwh: Sequence[float],
    proposed_remaining_plan_kwh: Sequence[float],
    *,
    release_hour: int,
) -> tuple[float, ...]:
    """Replace only the not-yet-executed suffix; the past prefix is immutable."""

    previous = _finite_vector(previous_effective_plan_kwh, "PREVIOUS_PLAN", POSITIONS_PER_DAY)
    completed = RELEASE_COMPLETED_INTERVALS.get(release_hour)
    if completed is None:
        raise ValueError("Q3_RELEASE_HOUR_INVALID")
    proposed = _finite_vector(
        proposed_remaining_plan_kwh,
        "PROPOSED_REMAINING_PLAN",
        POSITIONS_PER_DAY - completed,
    )
    if min(previous) < 0.0 or min(proposed) < 0.0:
        raise ValueError("Q3_PLAN_NEGATIVE")
    return previous[:completed] + proposed

def count_past_decision_mutations(
    before: Mapping[str, Sequence[float]],
    after: Mapping[str, Sequence[float]],
    *,
    release_hour: int,
    tolerance: float = 0.0,
) -> int:
    """Count changed past values across all registered executed-state vectors."""

    completed = RELEASE_COMPLETED_INTERVALS.get(release_hour)
    if completed is None:
        raise ValueError("Q3_RELEASE_HOUR_INVALID")
    if before.keys() != after.keys():
        raise ValueError("Q3_FROZEN_VECTOR_KEYS_MISMATCH")
    mutations = 0
    for name in before:
        left = tuple(float(value) for value in before[name])
        right = tuple(float(value) for value in after[name])
        if len(left) != len(right) or len(left) < completed:
            raise ValueError(f"Q3_FROZEN_VECTOR_LENGTH_MISMATCH:{name}")
        mutations += sum(abs(left[index] - right[index]) > tolerance for index in range(completed))
    return mutations

def remaining_midnight_load_forecast(
    midnight_load_forecast_kwh: Sequence[float],
    *,
    release_hour: int,
) -> tuple[float, ...]:
    """Return the frozen midnight Q2 load forecast suffix without re-estimation."""

    forecast = _finite_vector(midnight_load_forecast_kwh, "MIDNIGHT_LOAD_FORECAST", POSITIONS_PER_DAY)
    completed = RELEASE_COMPLETED_INTERVALS.get(release_hour)
    if completed is None:
        raise ValueError("Q3_RELEASE_HOUR_INVALID")
    return forecast[completed:]

def actual_soc_for_update(actual_soc_kwh: float) -> float:
    """Use the realized current SOC, not a prior planned SOC trajectory."""

    value = float(actual_soc_kwh)
    if not isfinite(value):
        raise ValueError("Q3_CURRENT_ACTUAL_SOC_INVALID")
    return value

def adjustment_increment(
    previous_kwh: float,
    new_kwh: float,
    price_per_kwh: float,
) -> dict[str, float]:
    previous = float(previous_kwh)
    current = float(new_kwh)
    price = float(price_per_kwh)
    if not all(isfinite(value) for value in (previous, current, price)):
        raise ValueError("Q3_ADJUSTMENT_INPUT_NONFINITE")
    if previous < 0.0 or current < 0.0 or price < 0.0:
        raise ValueError("Q3_ADJUSTMENT_INPUT_NEGATIVE")
    up = max(current - previous, 0.0)
    down = max(previous - current, 0.0)
    return {
        "delta_plus_kwh": up,
        "delta_minus_kwh": down,
        "adjustment_cost_delta": ADJUST_UP_MULTIPLIER * price * up
        - ADJUST_DOWN_REFUND_MULTIPLIER * price * down,
    }

def settle_sequential_effective_plans(
    *,
    initial_plan_kwh: Sequence[float],
    price_per_kwh: Sequence[float],
    updates: Sequence[tuple[int, Sequence[float]]],
) -> dict[str, object]:
    """Settle Q0 -> Q6 -> Q12 -> Q18 against the immediately prior plan."""

    initial = _finite_vector(initial_plan_kwh, "INITIAL_PLAN", POSITIONS_PER_DAY)
    prices = _finite_vector(price_per_kwh, "PRICE", POSITIONS_PER_DAY)
    if min(initial) < 0.0 or min(prices) < 0.0:
        raise ValueError("Q3_SETTLEMENT_INPUT_NEGATIVE")
    effective = initial
    adjustment_costs: list[float] = []
    adjustment_up: list[float] = []
    adjustment_down: list[float] = []
    seen: list[int] = []
    mutation_count = 0
    for release_hour, proposed_remaining in updates:
        if release_hour not in (6, 12, 18) or release_hour in seen or (seen and release_hour <= seen[-1]):
            raise ValueError("Q3_UPDATE_SEQUENCE_INVALID")
        new_effective = apply_future_plan_update(
            effective,
            proposed_remaining,
            release_hour=release_hour,
        )
        completed = RELEASE_COMPLETED_INTERVALS[release_hour]
        mutation_count += sum(effective[index] != new_effective[index] for index in range(completed))
        increments = [
            adjustment_increment(effective[index], new_effective[index], prices[index])
            for index in range(completed, POSITIONS_PER_DAY)
        ]
        adjustment_costs.append(fsum(item["adjustment_cost_delta"] for item in increments))
        adjustment_up.append(fsum(item["delta_plus_kwh"] for item in increments))
        adjustment_down.append(fsum(item["delta_minus_kwh"] for item in increments))
        effective = new_effective
        seen.append(release_hour)
    return {
        "initial_plan_cost": fsum(price * quantity for price, quantity in zip(prices, initial)),
        "adjustment_cost_deltas": tuple(adjustment_costs),
        "adjustment_up_kwh": tuple(adjustment_up),
        "adjustment_down_kwh": tuple(adjustment_down),
        "adjustment_cost_total": fsum(adjustment_costs),
        "final_effective_plan_kwh": effective,
        "past_decision_mutation_count": mutation_count,
    }

def recompute_total_q3_cost(
    *,
    initial_plan_kwh: Sequence[float],
    price_per_kwh: Sequence[float],
    updates: Sequence[tuple[int, Sequence[float]]],
    emergency_purchase_kwh: Sequence[float],
) -> dict[str, float | tuple[float, ...] | int]:
    settlement = settle_sequential_effective_plans(
        initial_plan_kwh=initial_plan_kwh,
        price_per_kwh=price_per_kwh,
        updates=updates,
    )
    prices = _finite_vector(price_per_kwh, "PRICE", POSITIONS_PER_DAY)
    emergency = _finite_vector(emergency_purchase_kwh, "EMERGENCY", POSITIONS_PER_DAY)
    if min(emergency) < 0.0:
        raise ValueError("Q3_EMERGENCY_PURCHASE_NEGATIVE")
    emergency_cost = fsum(
        EMERGENCY_MULTIPLIER * price * quantity
        for price, quantity in zip(prices, emergency)
    )
    initial_cost = float(settlement["initial_plan_cost"])
    adjustment_cost = float(settlement["adjustment_cost_total"])
    return {
        **settlement,
        "emergency_cost": emergency_cost,
        "total_cost": initial_cost + adjustment_cost + emergency_cost,
    }

@dataclass(frozen=True)
class Q3VariableLayout:
    """Q2's eight blocks followed by adjustment-up and adjustment-down."""

    n_intervals: int

    @property
    def size(self) -> int:
        return 10 * self.n_intervals

    def _index(self, block: int, k: int) -> int:
        if not 0 <= k < self.n_intervals:
            raise IndexError("Q3_TIME_INDEX_OUT_OF_RANGE")
        return block * self.n_intervals + k

    def q(self, k: int) -> int: return self._index(0, k)
    def g(self, k: int) -> int: return self._index(1, k)
    def h(self, k: int) -> int: return self._index(2, k)
    def c(self, k: int) -> int: return self._index(3, k)
    def d(self, k: int) -> int: return self._index(4, k)
    def r(self, k: int) -> int: return self._index(5, k)
    def energy(self, k: int) -> int: return self._index(6, k)
    def slack(self, k: int) -> int: return self._index(7, k)
    def adjust_up(self, k: int) -> int: return self._index(8, k)
    def adjust_down(self, k: int) -> int: return self._index(9, k)

    def block_indices(self, name: str) -> tuple[int, ...]:
        accessors = {
            "Q": self.q,
            "G": self.g,
            "H": self.h,
            "c": self.c,
            "d": self.d,
            "r": self.r,
            "E": self.energy,
            "S": self.slack,
            "U": self.adjust_up,
            "D": self.adjust_down,
        }
        try:
            accessor = accessors[name]
        except KeyError as exc:
            raise KeyError(f"Q3_UNKNOWN_VARIABLE_BLOCK:{name}") from exc
        return tuple(accessor(k) for k in range(self.n_intervals))

@dataclass(frozen=True)
class Q3AdjustmentLinearProgram:
    grid: object
    storage: StorageParameters
    inputs: PhysicalInputs
    layout: Q3VariableLayout
    equalities: tuple[SparseEqualityRow, ...]
    bounds: tuple[VariableBound, ...]
    objective_levels: tuple[ObjectiveLevel, ...]
    initial_energy_kwh: float
    terminal_energy_kwh: float | None
    planned_purchase_fixed_kwh: None
    previous_effective_plan_kwh: tuple[float, ...]
    variable_domain: str = "CONTINUOUS"

def _extended_objective(
    base: Sequence[float],
    extra_up: Sequence[float],
    extra_down: Sequence[float],
) -> tuple[float, ...]:
    return tuple(float(value) for value in base) + tuple(float(value) for value in extra_up) + tuple(
        float(value) for value in extra_down
    )

def build_q3_adjustment_model(
    load_kwh: Sequence[float],
    pv_available_kwh: Sequence[float],
    price_per_kwh: Sequence[float],
    *,
    initial_energy_kwh: float,
    previous_effective_plan_kwh: Sequence[float],
    terminal_energy_kwh: float | None = None,
    storage: StorageParameters | None = None,
) -> Q3AdjustmentLinearProgram:
    """Extend the Q2 LP only with Q3 sequential adjustment variables/cost."""

    base: Q2LinearProgram = build_q2_model(
        load_kwh,
        pv_available_kwh,
        price_per_kwh,
        initial_energy_kwh=initial_energy_kwh,
        terminal_energy_kwh=terminal_energy_kwh,
        storage=storage,
    )
    n = base.grid.n_intervals
    previous = _finite_vector(previous_effective_plan_kwh, "PREVIOUS_EFFECTIVE_PLAN", n)
    if min(previous) < 0.0:
        raise ValueError("Q3_PREVIOUS_EFFECTIVE_PLAN_NEGATIVE")
    layout = Q3VariableLayout(n)
    update_rows = tuple(
        SparseEqualityRow(
            name=f"q3_sequential_plan_update_k{k + 1}",
            coefficients=((layout.q(k), 1.0), (layout.adjust_up(k), -1.0), (layout.adjust_down(k), 1.0)),
            rhs=previous[k],
        )
        for k in range(n)
    )
    bounds = tuple(base.bounds) + tuple(VariableBound(0.0, None) for _ in range(2 * n))
    prices = base.inputs.price_per_kwh
    zero = (0.0,) * n
    primary_base = [0.0] * (8 * n)
    for index, price in zip(layout.block_indices("H"), prices):
        primary_base[index] = EMERGENCY_MULTIPLIER * price
    primary = _extended_objective(
        primary_base,
        tuple(ADJUST_UP_MULTIPLIER * price for price in prices),
        tuple(-ADJUST_DOWN_REFUND_MULTIPLIER * price for price in prices),
    )
    level2 = _extended_objective(base.objective_levels[1].coefficients, zero, zero)
    level3 = _extended_objective(base.objective_levels[2].coefficients, zero, zero)
    objectives = (
        ObjectiveLevel(1, "q3_future_incremental_total_cost", primary, "min adjustment increment plus 5*p*H"),
        ObjectiveLevel(2, base.objective_levels[1].name, level2, base.objective_levels[1].rule),
        ObjectiveLevel(3, base.objective_levels[2].name, level3, base.objective_levels[2].rule),
    )
    return Q3AdjustmentLinearProgram(
        grid=base.grid,
        storage=base.storage,
        inputs=base.inputs,
        layout=layout,
        equalities=base.equalities + update_rows,
        bounds=bounds,
        objective_levels=objectives,
        initial_energy_kwh=base.initial_energy_kwh,
        terminal_energy_kwh=base.terminal_energy_kwh,
        planned_purchase_fixed_kwh=None,
        previous_effective_plan_kwh=previous,
    )

def run_q3_adjustment_model(
    model: Q3AdjustmentLinearProgram,
    *,
    backend=None,
    run_id: str,
    case_dir: Path | None = None,
) -> dict[str, object]:
    """Run the shared Q2 lexicographic solver and derive adjustment blocks."""

    result = run_lexicographic_q2(model, backend=backend, run_id=run_id, case_dir=case_dir)
    if result.get("status") != "PASS":
        return result
    solution = result["solution"]
    effective = tuple(float(value) for value in solution["Q"])
    up = tuple(max(effective[k] - model.previous_effective_plan_kwh[k], 0.0) for k in range(model.grid.n_intervals))
    down = tuple(max(model.previous_effective_plan_kwh[k] - effective[k], 0.0) for k in range(model.grid.n_intervals))
    result["q3_adjustment"] = {
        "delta_plus_kwh": up,
        "delta_minus_kwh": down,
        "adjustment_cost_delta": fsum(
            ADJUST_UP_MULTIPLIER * model.inputs.price_per_kwh[k] * up[k]
            - ADJUST_DOWN_REFUND_MULTIPLIER * model.inputs.price_per_kwh[k] * down[k]
            for k in range(model.grid.n_intervals)
        ),
    }
    return result
