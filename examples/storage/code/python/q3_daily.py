"""Reusable contest implementation. The caller supplies authorized local case inputs."""

from __future__ import annotations

import argparse

import json

import os

import sys

from datetime import date, datetime, time, timedelta

from hashlib import sha256

from math import fsum

from pathlib import Path

from typing import Any, Sequence

from openpyxl import load_workbook

from formal_q2_provider import load_formal_q2_data

from model_contract import PhysicalInputs, StorageParameters

from q2_data_contract import POSITIONS_PER_DAY, build_information_set, kw_to_kwh

from q2_model_solver import build_q2_model

from q2_scientific_validation import validate_q2_day

from q2_solver import run_lexicographic_q2

from q2_daily import _clean_nonnegative, canonicalize_soc_for_propagation

from q3_model_solver import (Q3_FORMAL_DAYS, Q3_FORMAL_END, Q3_FORMAL_START, Q3_TERMINAL_SOC_KWH, RELEASE_COMPLETED_INTERVALS, RELEASE_HOURS, actual_soc_for_update, apply_future_plan_update, audit_information_access, build_pv_point_forecast_curve, recompute_total_q3_cost, release_pv_anchor_from_completed_actual, run_q3_adjustment_model)

CASE_ROOT = Path(__file__).resolve().parents[2]

def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return date.fromisoformat(text)
        except ValueError:
            parts = text.split("-")
            if len(parts) == 3:
                return date(int(parts[0]), int(parts[1]), int(parts[2]))
            raise
    raise ValueError("Q3_ATTACHMENT3_DATE_INVALID")

def _release_hour(value: object) -> int:
    if isinstance(value, time):
        return int(value.hour)
    if isinstance(value, datetime):
        return int(value.hour)
    text = str(value).strip()
    hour = int(text.split(":", 1)[0])
    if hour not in RELEASE_HOURS:
        raise ValueError("Q3_ATTACHMENT3_RELEASE_HOUR_INVALID")
    return hour

def load_attachment3_forecasts(path: Path) -> dict[tuple[date, int], tuple[float, ...]]:
    """Read the official 4x365 release rows without changing their semantics."""
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook[workbook.sheetnames[0]]
        current_day: date | None = None
        rows: dict[tuple[date, int], tuple[float, ...]] = {}
        for values in sheet.iter_rows(min_row=2, values_only=True):
            if values[0] is not None and str(values[0]).strip() != "":
                current_day = _as_date(values[0])
            if current_day is None:
                raise ValueError("Q3_ATTACHMENT3_DATE_FORWARD_FILL_MISSING")
            hour = _release_hour(values[1])
            forecast = tuple(float(value) for value in values[2:26])
            if len(forecast) != 24 or min(forecast) < 0:
                raise ValueError("Q3_ATTACHMENT3_FORECAST_VECTOR_INVALID")
            key = (current_day, hour)
            if key in rows:
                raise ValueError("Q3_ATTACHMENT3_DUPLICATE_RELEASE")
            rows[key] = forecast
        expected = {(Q3_FORMAL_START + timedelta(days=i), hour) for i in range(Q3_FORMAL_DAYS) for hour in RELEASE_HOURS}
        if not expected.issubset(rows):
            raise ValueError("Q3_ATTACHMENT3_FORMAL_RELEASE_MISSING")
        return rows
    finally:
        workbook.close()

def _history(data, day: date):
    return {known: (data.load_kw_by_day[known], data.pv_kw_by_day[known]) for known in data.dates if known < day}

def _solution_validation(result: dict[str, Any], inputs: PhysicalInputs, initial: float, terminal: float | None) -> dict[str, Any]:
    solution = result.get("solution")
    if solution is None:
        raise RuntimeError(f"Q3_NO_ACCEPTED_PRIMAL:{result.get('reason', 'SOLUTION_MISSING')}")
    return validate_q2_day(
        planned_kwh=solution["Q"], ordinary_used_kwh=solution["G"], emergency_kwh=solution["H"],
        charge_kwh=solution["c"], discharge_kwh=solution["d"], curtailed_pv_kwh=solution["r"],
        energy_path_kwh=solution["E"], unused_plan_kwh=solution["S"], actual_inputs=inputs,
        initial_energy_kwh=initial, storage=StorageParameters.working_e_a(),
        future_leakage_count=0, past_decision_mutation_count=0, terminal_energy_kwh=terminal,
    )

def _accepted(result: dict[str, Any], validation: dict[str, Any] | None, label: str) -> None:
    if result.get("status") != "PASS" or result.get("solution") is None:
        raise RuntimeError(f"Q3_{label}_SOLVER_FAILED:{result.get('reason')}")
    if validation is not None and validation.get("status") != "PASS":
        raise RuntimeError(f"Q3_{label}_FINAL_SOLUTION_VALIDATION_FAILED")

def _accumulate_solver_audit(statistics: dict[str, int], result: dict[str, Any]) -> None:
    """Record existing solver-path outcomes without altering solver behavior."""

    for level in result.get("levels", ()):
        fallback = bool(level.get("FALLBACK_TRIGGERED"))
        validation_band = bool(level.get("VALIDATION_BAND_LOCK_FALLBACK_TRIGGERED"))
        statistics["solver_call_count"] += 1
        statistics["standard_path_success_count"] += int(not fallback and not validation_band)
        statistics["existing_fallback_trigger_count"] += int(fallback)
        statistics["existing_fallback_success_count"] += int(
            fallback and level.get("FALLBACK_STATUS") == "OPTIMAL"
        )
        statistics["validation_band_fallback_trigger_count"] += int(validation_band)
        statistics["validation_band_fallback_success_count"] += int(
            validation_band and level.get("STATUS") == "OPTIMAL"
        )

def _canonical_daily_result(record: dict[str, Any]) -> dict[str, Any]:
    """Expose the complete persisted Q3 day result without changing decisions."""

    snapshots = record["effective_plan_after_updates"]
    trajectories = record["trajectories"]
    solver_statistics = record["solver_statistics"]
    return {
        **record,
        "schema_version": "q3_final_daily_result_v1",
        "date": record["day"],
        "soc_start": record["soc_start_kwh"],
        "soc_end": record["soc_end_kwh"],
        "plan_q0": record["initial_plan"],
        "effective_plan_after_06": snapshots["06"],
        "effective_plan_after_12": snapshots["12"],
        "effective_plan_after_18": snapshots["18"],
        "charge": trajectories["c"],
        "discharge": trajectories["d"],
        "emergency": trajectories["H"],
        "soc_path": [record["soc_start_kwh"], *trajectories["E"]],
        "initial_plan_cost": record["costs"]["initial_plan_cost"],
        "adjustment_cost": record["costs"]["net_adjustment_cost"],
        "emergency_cost": record["costs"]["emergency_cost"],
        "total_cost": record["costs"]["total_q3_cost"],
        "day_scientific_validation": record["validation"],
        "solver_fallback_counts": {
            key: int(solver_statistics[key])
            for key in (
                "solver_call_count",
                "standard_path_success_count",
                "existing_fallback_trigger_count",
                "existing_fallback_success_count",
                "validation_band_fallback_trigger_count",
                "validation_band_fallback_success_count",
            )
            if key in solver_statistics
        },
    }

def _validate_daily_ledger_record(record: dict[str, Any]) -> None:
    """Fail closed on an incomplete persisted daily result."""

    required = (
        "date", "soc_start", "soc_end", "plan_q0",
        "effective_plan_after_06", "effective_plan_after_12", "effective_plan_after_18",
        "charge", "discharge", "emergency", "soc_path",
        "initial_plan_cost", "adjustment_cost", "emergency_cost", "total_cost",
        "day_scientific_validation", "solver_fallback_counts",
    )
    missing = [name for name in required if name not in record]
    if missing:
        raise RuntimeError(f"Q3_FINAL_DAILY_LEDGER_FIELDS_MISSING:{','.join(missing)}")
    if any(len(record[name]) != POSITIONS_PER_DAY for name in (
        "plan_q0", "effective_plan_after_06", "effective_plan_after_12",
        "effective_plan_after_18", "charge", "discharge", "emergency",
    )) or len(record["soc_path"]) != POSITIONS_PER_DAY + 1:
        raise RuntimeError("Q3_FINAL_DAILY_LEDGER_DIMENSION_INVALID")
    if record["day_scientific_validation"].get("status") != "PASS":
        raise RuntimeError("Q3_FINAL_DAILY_LEDGER_VALIDATION_NOT_PASS")

def execute_day(*, day: date, initial_soc: float, previous_final_soc: float | None, data, predictor, releases) -> tuple[dict[str, Any], float]:
    information_set = build_information_set(day, initial_soc, _history(data, day))
    leakage_before = predictor.future_leakage_count
    frozen_load = kw_to_kwh(predictor.forecast_load(day, information_set))
    actual_load = kw_to_kwh(data.load_kw_by_day[day])
    actual_pv_kw = data.pv_kw_by_day[day]
    actual_pv = kw_to_kwh(actual_pv_kw)
    price = data.price_per_kwh
    terminal = Q3_TERMINAL_SOC_KWH if day == Q3_FORMAL_END else None
    executed = {name: [] for name in ("Q", "G", "H", "c", "d", "r", "E", "S")}
    initial_plan: tuple[float, ...] | None = None
    effective_plan: tuple[float, ...] | None = None
    updates: list[tuple[int, tuple[float, ...]]] = []
    effective_plan_after_updates: dict[str, list[float]] = {}
    release_stats = {str(hour): {"update_count": 0, "adjust_up_kwh": 0.0, "adjust_down_kwh": 0.0, "abs_plan_change_kwh": 0.0, "net_adjustment_cost": 0.0} for hour in (6, 12, 18)}
    current_soc = float(initial_soc)
    boundary_events: list[dict[str, Any]] = []
    solver_stats = {
        "solver_call_count": 0,
        "standard_path_success_count": 0,
        "existing_fallback_trigger_count": 0,
        "existing_fallback_success_count": 0,
        "validation_band_fallback_trigger_count": 0,
        "validation_band_fallback_success_count": 0,
        "normal_level3_solver_calls": 0,
        "theoretical_lower_bound_certificate_acceptances": 0,
    }
    past_mutations = 0
    unreleased_forecast = 0
    future_actual = 0
    release_curve = None
    release_start = 0
    for k in range(POSITIONS_PER_DAY):
        hour = k // 6
        if hour in RELEASE_HOURS and k == RELEASE_COMPLETED_INTERVALS[hour]:
            anchor, anchor_index = release_pv_anchor_from_completed_actual(hour, actual_pv_kw[:k])
            access = audit_information_access(release_hour=hour, accessed_actual_position_indices=(() if anchor_index is None else tuple(range(k))), accessed_forecast_release_hours=(hour,))
            future_actual += int(access["future_actual_data_leakage_count"])
            unreleased_forecast += int(access["unreleased_forecast_leakage_count"])
            release_curve = build_pv_point_forecast_curve(release_hour=hour, current_anchor_kw=anchor, forecast_1h_to_24h_kw=releases[(day, hour)])
            release_start = k
            pv_suffix = release_curve.same_day_control_energy_kwh
            load_suffix = frozen_load[k:]
            if hour == 0:
                model = build_q2_model(load_suffix, pv_suffix, price[k:], initial_energy_kwh=current_soc, terminal_energy_kwh=terminal)
                result = run_lexicographic_q2(model, run_id=f"q3_plan_{day.isoformat()}_r00", case_dir=CASE_ROOT)
                label = f"INITIAL_PLAN_{day.isoformat()}_R00"
                _accepted(result, None, label)
                validation = _solution_validation(result, PhysicalInputs.from_sequences(load_suffix, pv_suffix, price[k:]), current_soc, terminal)
                _accepted(result, validation, label)
                # Solver values within the established nonnegativity tolerance
                # are canonicalized before they become a fixed financial plan.
                initial_plan = tuple(_clean_nonnegative(float(value)) for value in result["solution"]["Q"])
                effective_plan = initial_plan
            else:
                assert effective_plan is not None
                model = __import__("q3_model_solver").build_q3_adjustment_model(load_suffix, pv_suffix, price[k:], initial_energy_kwh=actual_soc_for_update(current_soc), previous_effective_plan_kwh=effective_plan[k:], terminal_energy_kwh=terminal)
                result = run_q3_adjustment_model(model, run_id=f"q3_update_{day.isoformat()}_r{hour:02d}", case_dir=CASE_ROOT)
                label = f"ADJUSTMENT_{day.isoformat()}_R{hour:02d}"
                _accepted(result, None, label)
                validation = _solution_validation(result, PhysicalInputs.from_sequences(load_suffix, pv_suffix, price[k:]), current_soc, terminal)
                _accepted(result, validation, label)
                proposed = tuple(_clean_nonnegative(float(value)) for value in result["solution"]["Q"])
                before = effective_plan
                effective_plan = apply_future_plan_update(before, proposed, release_hour=hour)
                updates.append((hour, proposed))
                effective_plan_after_updates[f"{hour:02d}"] = list(effective_plan)
                adjustment = result["q3_adjustment"]
                release_stats[str(hour)] = {"update_count": 1, "adjust_up_kwh": fsum(adjustment["delta_plus_kwh"]), "adjust_down_kwh": fsum(adjustment["delta_minus_kwh"]), "abs_plan_change_kwh": fsum(abs(a-b) for a,b in zip(proposed, before[k:])), "net_adjustment_cost": float(adjustment["adjustment_cost_delta"])}
            solver_stats["normal_level3_solver_calls"] += int(result.get("level3_optimality_proof") == "SOLVER_REPORTED_OPTIMUM")
            solver_stats["theoretical_lower_bound_certificate_acceptances"] += int(result.get("level3_optimality_proof") == "FEASIBLE_SOLUTION_PLUS_THEORETICAL_LOWER_BOUND")
            _accumulate_solver_audit(solver_stats, result)
        assert effective_plan is not None and release_curve is not None
        prefix = {name: tuple(values) for name, values in executed.items()}
        relative = k - release_start
        pv_forecast = release_curve.same_day_control_energy_kwh
        remaining_load = (actual_load[k],) + frozen_load[k + 1:]
        remaining_pv = (actual_pv[k],) + pv_forecast[relative + 1:]
        recourse = run_lexicographic_q2(build_q2_model(remaining_load, remaining_pv, price[k:], initial_energy_kwh=current_soc, planned_purchase_fixed_kwh=effective_plan[k:], terminal_energy_kwh=terminal), run_id=f"q3_recourse_{day.isoformat()}_k{k+1:03d}", case_dir=CASE_ROOT)
        label = f"CAUSAL_RECOURSE_{day.isoformat()}_K{k + 1:03d}"
        _accepted(recourse, None, label)
        validation = _solution_validation(recourse, PhysicalInputs.from_sequences(remaining_load, remaining_pv, price[k:]), current_soc, terminal)
        _accepted(recourse, validation, label)
        solution = recourse["solution"]
        source = "LEVEL2_THEORETICAL_LOWER_BOUND_CERTIFICATE" if recourse.get("level3_optimality_proof") == "FEASIBLE_SOLUTION_PLUS_THEORETICAL_LOWER_BOUND" else "LEVEL3_SOLVER"
        canonical, event = canonicalize_soc_for_propagation(float(solution["E"][0]), lower_bound_kwh=1200.0, upper_bound_kwh=10800.0, tolerance=1e-7, day=day, interval_one_based=k + 1, source_level=source)
        if event:
            boundary_events.append(event)
        for name in executed:
            executed[name].append(canonical if name == "E" else _clean_nonnegative(float(solution[name][0])))
        current_soc = canonical
        for name, old in prefix.items():
            past_mutations += int(tuple(executed[name][:-1]) != old)
        solver_stats["normal_level3_solver_calls"] += int(recourse.get("level3_optimality_proof") == "SOLVER_REPORTED_OPTIMUM")
        solver_stats["theoretical_lower_bound_certificate_acceptances"] += int(recourse.get("level3_optimality_proof") == "FEASIBLE_SOLUTION_PLUS_THEORETICAL_LOWER_BOUND")
        _accumulate_solver_audit(solver_stats, recourse)
    assert initial_plan is not None
    costs = recompute_total_q3_cost(initial_plan_kwh=initial_plan, price_per_kwh=price, updates=updates, emergency_purchase_kwh=executed["H"])
    actual_validation = validate_q2_day(planned_kwh=tuple(executed["Q"]), ordinary_used_kwh=tuple(executed["G"]), emergency_kwh=tuple(executed["H"]), charge_kwh=tuple(executed["c"]), discharge_kwh=tuple(executed["d"]), curtailed_pv_kwh=tuple(executed["r"]), energy_path_kwh=tuple(executed["E"]), unused_plan_kwh=tuple(executed["S"]), actual_inputs=PhysicalInputs.from_sequences(actual_load, actual_pv, price), initial_energy_kwh=initial_soc, storage=StorageParameters.working_e_a(), future_leakage_count=future_actual + (predictor.future_leakage_count - leakage_before), past_decision_mutation_count=past_mutations, terminal_energy_kwh=terminal)
    if actual_validation["status"] != "PASS" or (previous_final_soc is not None and abs(initial_soc - previous_final_soc) > 1e-7):
        raise RuntimeError(f"Q3_SCIENTIFIC_VALIDATION_FAILED:{day.isoformat()}")
    if set(effective_plan_after_updates) != {"06", "12", "18"}:
        raise RuntimeError("Q3_EFFECTIVE_PLAN_RELEASE_SNAPSHOT_MISSING")
    return {"day": day.isoformat(), "status": "PASS", "initial_plan": list(initial_plan), "final_effective_plan": list(effective_plan), "effective_plan_after_updates": effective_plan_after_updates, "updates": [{"release_hour": h, "plan_suffix": list(p)} for h,p in updates], "trajectories": {name: list(values) for name, values in executed.items()}, "costs": {"initial_plan_cost": costs["initial_plan_cost"], "net_adjustment_cost": costs["adjustment_cost_total"], "emergency_cost": costs["emergency_cost"], "total_q3_cost": costs["total_cost"]}, "release_statistics": release_stats, "validation": actual_validation, "soc_start_kwh": initial_soc, "soc_end_kwh": current_soc, "boundary_events": boundary_events, "solver_statistics": solver_stats, "unreleased_forecast_leakage_count": unreleased_forecast, "next_day_commitment_before_midnight_count": 0}, current_soc
