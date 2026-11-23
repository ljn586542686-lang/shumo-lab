"""Reusable contest implementation. The caller supplies authorized local case inputs."""

from __future__ import annotations

import argparse

import json

import sys

from dataclasses import asdict, dataclass

from datetime import date, timedelta

from math import isfinite

from pathlib import Path

from typing import Sequence

from formal_q2_provider import FormalQ2Data, load_formal_q2_data

from model_contract import PhysicalInputs, StorageParameters

from q2_data_contract import POSITIONS_PER_DAY, build_information_set, kw_to_kwh

from q2_forecasting import (
    FORMAL_FORECAST_START_DATE,
    JanuaryBootstrapPredictionProvider,
    SelectedCausalPredictionProvider,
    select_forecast_model,
)

from q2_model_solver import build_q2_model

from q2_scientific_validation import validate_q2_day

from q2_solver import run_lexicographic_q2

CASE_ROOT = Path(__file__).resolve().parents[2]

END_OF_YEAR_DAY = date(2025, 12, 31)

END_OF_YEAR_SOC_KWH = 6000.0

Q2PredictionProvider = JanuaryBootstrapPredictionProvider | SelectedCausalPredictionProvider

@dataclass(frozen=True)
class DayExecution:
    day: str
    status: str
    planned_kwh: float
    emergency_kwh: float
    planned_cost: float
    emergency_cost: float
    total_cost: float
    soc_start_kwh: float
    soc_end_kwh: float
    max_balance_residual: float
    max_soc_recalculation_error: float
    future_leakage_count: int
    past_decision_mutation_count: int
    simultaneous_cd_count: int
    cross_day_soc_continuity: bool
    soc_boundary_canonicalization_count: int
    soc_boundary_max_correction: float
    soc_boundary_canonicalization_events: tuple[dict[str, object], ...]

def _dates(start: date, end: date) -> tuple[date, ...]:
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))

def _history_for(data: FormalQ2Data, decision_day: date) -> dict[date, tuple[Sequence[float], Sequence[float]]]:
    return {
        day: (data.load_kw_by_day[day], data.pv_kw_by_day[day])
        for day in data.dates
        if day < decision_day
    }

def _clean_nonnegative(value: float, tolerance: float = 1e-7) -> float:
    numeric = float(value)
    if -tolerance <= numeric < 0.0:
        return 0.0
    return numeric

def canonicalize_soc_for_propagation(
    raw_soc_kwh: float,
    *,
    lower_bound_kwh: float,
    upper_bound_kwh: float,
    tolerance: float,
    day: date,
    interval_one_based: int,
    source_level: str,
) -> tuple[float, dict[str, object] | None]:
    """仅规范化容差内的边界漂移；真实越界必须 fail-closed。"""

    raw = float(raw_soc_kwh)
    lower = float(lower_bound_kwh)
    upper = float(upper_bound_kwh)
    numeric_tolerance = float(tolerance)
    if not all(isfinite(value) for value in (raw, lower, upper, numeric_tolerance)):
        raise ValueError("Q2_SOC_PROPAGATION_VALUE_NONFINITE")
    if numeric_tolerance <= 0.0 or lower > upper:
        raise ValueError("Q2_SOC_PROPAGATION_BOUND_CONFIGURATION_INVALID")
    canonical = raw
    bound = "NONE"
    if raw < lower:
        if lower - raw > numeric_tolerance:
            raise ValueError("Q2_TRUE_SOC_PROPAGATION_VIOLATION")
        canonical = lower
        bound = "LOWER"
    elif raw > upper:
        if raw - upper > numeric_tolerance:
            raise ValueError("Q2_TRUE_SOC_PROPAGATION_VIOLATION")
        canonical = upper
        bound = "UPPER"
    if bound == "NONE":
        return raw, None
    correction = canonical - raw
    return canonical, {
        "RAW_SOC": raw,
        "CANONICAL_SOC": canonical,
        "CORRECTION": correction,
        "BOUND": bound,
        "DATE": day.isoformat(),
        "INTERVAL": int(interval_one_based),
        "SOURCE_LEVEL": source_level,
    }

def _validate_final_solver_solution(
    result: dict[str, object],
    *,
    inputs: PhysicalInputs,
    initial_soc_kwh: float,
    terminal_soc_kwh: float | None,
) -> dict[str, object]:
    """验证真正采用的 solver/certificate 最终解；失败时禁止传递 SOC。"""

    solution = result["solution"]
    return validate_q2_day(
        planned_kwh=solution["Q"],
        ordinary_used_kwh=solution["G"],
        emergency_kwh=solution["H"],
        charge_kwh=solution["c"],
        discharge_kwh=solution["d"],
        curtailed_pv_kwh=solution["r"],
        energy_path_kwh=solution["E"],
        unused_plan_kwh=solution["S"],
        actual_inputs=inputs,
        initial_energy_kwh=initial_soc_kwh,
        storage=StorageParameters.working_e_a(),
        future_leakage_count=0,
        past_decision_mutation_count=0,
        terminal_energy_kwh=terminal_soc_kwh,
    )

def _append_solver_diagnostic(
    collector: list[dict[str, object]] | None,
    *,
    phase: str,
    interval_one_based: int | None,
    result: dict[str, object],
) -> None:
    """Collect formal-run diagnostics without changing the shared solve path."""

    if collector is None:
        return
    excesses = tuple(float(value) for value in result.get("objective_lock_upper_bound_excesses", ()))
    improvements = tuple(float(value) for value in result.get("prior_objective_improvements", ()))
    fallback_diagnostics = tuple(result.get("solver_fallback_diagnostics", ()))
    collector.append(
        {
            "phase": phase,
            "interval": interval_one_based,
            "status": result.get("status"),
            "level3_optimality_proof": result.get("level3_optimality_proof"),
            "normal_level3_solver_call": result.get("level3_optimality_proof") == "SOLVER_REPORTED_OPTIMUM",
            "theoretical_lower_bound_certificate": (
                result.get("level3_optimality_proof")
                == "FEASIBLE_SOLUTION_PLUS_THEORETICAL_LOWER_BOUND"
            ),
            "max_objective_lock_upper_bound_excess": max((0.0, *excesses)),
            "max_prior_objective_improvement": max((0.0, *improvements)),
            "numerical_equivalent_improvement_count": int(
                result.get("numerical_equivalent_improvement_count", 0)
            ),
            "true_unexpected_previous_level_improvement_count": 0,
            "previous_level_refinement_diagnostics": tuple(
                result.get("previous_level_refinement_diagnostics", ())
            ),
            "objective_preservation_status": result.get("final_objective_preservation_status"),
            "solver_fallback_count": len(fallback_diagnostics),
            "solver_fallback_diagnostics": fallback_diagnostics,
        }
    )

def execute_day(
    *,
    day: date,
    initial_soc_kwh: float,
    data: FormalQ2Data,
    provider: Q2PredictionProvider,
    previous_day_final_soc_kwh: float | None,
    solver_diagnostics: list[dict[str, object]] | None = None,
) -> tuple[DayExecution, dict[str, tuple[float, ...]]]:
    information_set = build_information_set(day, initial_soc_kwh, _history_for(data, day))
    leakage_before = provider.future_leakage_count
    forecast_load_kwh = kw_to_kwh(provider.forecast_load(day, information_set))
    forecast_pv_kwh = kw_to_kwh(provider.forecast_pv(day, information_set))
    price = data.price_per_kwh
    terminal = END_OF_YEAR_SOC_KWH if day == END_OF_YEAR_DAY else None
    plan_model = build_q2_model(
        forecast_load_kwh,
        forecast_pv_kwh,
        price,
        initial_energy_kwh=initial_soc_kwh,
        terminal_energy_kwh=terminal,
    )
    plan_result = run_lexicographic_q2(plan_model, run_id=f"q2_plan_{day.isoformat()}", case_dir=CASE_ROOT)
    if plan_result.get("status") != "PASS":
        raise RuntimeError(f"Q2_PLAN_SOLVER_FAILED:{day.isoformat()}:{plan_result.get('reason')}")
    plan_solution = plan_result["solution"]
    plan_validation = _validate_final_solver_solution(
        plan_result,
        inputs=PhysicalInputs.from_sequences(forecast_load_kwh, forecast_pv_kwh, price),
        initial_soc_kwh=initial_soc_kwh,
        terminal_soc_kwh=terminal,
    )
    if plan_validation["status"] != "PASS":
        raise RuntimeError(f"Q2_PLAN_FINAL_SOLUTION_VALIDATION_FAILED:{day.isoformat()}")
    _append_solver_diagnostic(
        solver_diagnostics,
        phase="DAY_AHEAD_PLAN",
        interval_one_based=None,
        result=plan_result,
    )
    planned = tuple(_clean_nonnegative(value) for value in plan_solution["Q"])

    actual_load_kwh = kw_to_kwh(data.load_kw_by_day[day])
    actual_pv_kwh = kw_to_kwh(data.pv_kw_by_day[day])
    executed: dict[str, list[float]] = {name: [] for name in ("Q", "G", "H", "c", "d", "r", "E", "S")}
    current_soc = float(initial_soc_kwh)
    past_mutations = 0
    future_actual_accesses = 0
    boundary_events: list[dict[str, object]] = []
    storage = StorageParameters.working_e_a()
    for k in range(POSITIONS_PER_DAY):
        frozen_prefix = {name: tuple(values) for name, values in executed.items()}
        current_actual_index = k
        if current_actual_index > k:
            future_actual_accesses += 1
        remaining_load = (actual_load_kwh[k],) + forecast_load_kwh[k + 1 :]
        remaining_pv = (actual_pv_kwh[k],) + forecast_pv_kwh[k + 1 :]
        recourse_model = build_q2_model(
            remaining_load,
            remaining_pv,
            price[k:],
            initial_energy_kwh=current_soc,
            planned_purchase_fixed_kwh=planned[k:],
            terminal_energy_kwh=terminal,
        )
        recourse = run_lexicographic_q2(
            recourse_model,
            run_id=f"q2_recourse_{day.isoformat()}_k{k + 1:03d}",
            case_dir=CASE_ROOT,
        )
        if recourse.get("status") != "PASS":
            raise RuntimeError(f"Q2_RECOURSE_SOLVER_FAILED:{day.isoformat()}:k{k + 1}:{recourse.get('reason')}")
        solution = recourse["solution"]
        recourse_validation = _validate_final_solver_solution(
            recourse,
            inputs=PhysicalInputs.from_sequences(remaining_load, remaining_pv, price[k:]),
            initial_soc_kwh=current_soc,
            terminal_soc_kwh=terminal,
        )
        if recourse_validation["status"] != "PASS":
            raise RuntimeError(f"Q2_RECOURSE_FINAL_SOLUTION_VALIDATION_FAILED:{day.isoformat()}:k{k + 1}")
        _append_solver_diagnostic(
            solver_diagnostics,
            phase="CAUSAL_RECOURSE",
            interval_one_based=k + 1,
            result=recourse,
        )
        source_level = (
            "LEVEL2_THEORETICAL_LOWER_BOUND_CERTIFICATE"
            if recourse.get("level3_optimality_proof") == "FEASIBLE_SOLUTION_PLUS_THEORETICAL_LOWER_BOUND"
            else "LEVEL3_SOLVER"
        )
        canonical_soc, boundary_event = canonicalize_soc_for_propagation(
            float(solution["E"][0]),
            lower_bound_kwh=storage.operational_min_kwh,
            upper_bound_kwh=storage.operational_max_kwh,
            tolerance=1e-7,
            day=day,
            interval_one_based=k + 1,
            source_level=source_level,
        )
        if boundary_event is not None:
            boundary_events.append(boundary_event)
        for name in executed:
            executed[name].append(_clean_nonnegative(solution[name][0]) if name != "E" else canonical_soc)
        current_soc = executed["E"][-1]
        for name, prefix in frozen_prefix.items():
            if tuple(executed[name][:-1]) != prefix:
                past_mutations += 1

    trajectories = {name: tuple(values) for name, values in executed.items()}
    actual_inputs = PhysicalInputs.from_sequences(actual_load_kwh, actual_pv_kwh, price)
    leakage = provider.future_leakage_count - leakage_before + future_actual_accesses
    validation = validate_q2_day(
        planned_kwh=trajectories["Q"],
        ordinary_used_kwh=trajectories["G"],
        emergency_kwh=trajectories["H"],
        charge_kwh=trajectories["c"],
        discharge_kwh=trajectories["d"],
        curtailed_pv_kwh=trajectories["r"],
        energy_path_kwh=trajectories["E"],
        unused_plan_kwh=trajectories["S"],
        actual_inputs=actual_inputs,
        initial_energy_kwh=initial_soc_kwh,
        storage=StorageParameters.working_e_a(),
        future_leakage_count=leakage,
        past_decision_mutation_count=past_mutations,
        terminal_energy_kwh=terminal,
    )
    cross_day = previous_day_final_soc_kwh is None or abs(initial_soc_kwh - previous_day_final_soc_kwh) <= 1e-7
    if validation["status"] != "PASS" or not cross_day:
        raise RuntimeError(f"Q2_SCIENTIFIC_VALIDATION_FAILED:{day.isoformat()}")
    planned_cost = float(validation["planned_cost"])
    emergency_cost = float(validation["emergency_cost"])
    record = DayExecution(
        day=day.isoformat(),
        status="PASS",
        planned_kwh=sum(trajectories["Q"]),
        emergency_kwh=sum(trajectories["H"]),
        planned_cost=planned_cost,
        emergency_cost=emergency_cost,
        total_cost=planned_cost + emergency_cost,
        soc_start_kwh=initial_soc_kwh,
        soc_end_kwh=trajectories["E"][-1],
        max_balance_residual=float(validation["max_abs_balance_residual"]),
        max_soc_recalculation_error=float(validation["max_abs_soc_recalculation_error"]),
        future_leakage_count=leakage,
        past_decision_mutation_count=past_mutations,
        simultaneous_cd_count=int(validation["simultaneous_charge_discharge_count"]),
        cross_day_soc_continuity=cross_day,
        soc_boundary_canonicalization_count=len(boundary_events),
        soc_boundary_max_correction=max((abs(float(event["CORRECTION"])) for event in boundary_events), default=0.0),
        soc_boundary_canonicalization_events=tuple(boundary_events),
    )
    return record, trajectories

def execute_period(
    *,
    start: date,
    end: date,
    initial_soc_kwh: float,
    data: FormalQ2Data,
    provider: Q2PredictionProvider,
    previous_day_final_soc_kwh: float | None = None,
) -> tuple[tuple[DayExecution, ...], float]:
    records: list[DayExecution] = []
    current_soc = float(initial_soc_kwh)
    previous_final = previous_day_final_soc_kwh
    for day in _dates(start, end):
        record, _ = execute_day(
            day=day,
            initial_soc_kwh=current_soc,
            data=data,
            provider=provider,
            previous_day_final_soc_kwh=previous_final,
        )
        records.append(record)
        previous_final = record.soc_end_kwh
        current_soc = record.soc_end_kwh
    return tuple(records), current_soc
