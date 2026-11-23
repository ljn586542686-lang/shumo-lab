"""Reusable contest implementation. The caller supplies authorized local case inputs."""

from __future__ import annotations

import argparse

import json

import sys

import time

from datetime import date, timedelta

from hashlib import sha256

from math import fsum, sqrt

from pathlib import Path

from statistics import correlation

from typing import Mapping, Sequence

from pipeline_core.python_tools.model_authority import verify_model_authority

from formal_q2_provider import FormalQ2Data, load_formal_q2_data

from q2_data_contract import POSITIONS_PER_DAY

from q2_forecasting import SelectedCausalPredictionProvider, forecast_candidate

from result2_writer import write_result2



NUMERICAL_TOLERANCE = 1e-7

def _forecast_metrics(predicted_kw: Sequence[float], actual_kw: Sequence[float]) -> dict[str, float]:
    errors = tuple(float(predicted) - float(actual) for predicted, actual in zip(predicted_kw, actual_kw))
    return {
        "mae_kw": fsum(abs(value) for value in errors) / len(errors),
        "rmse_kw": sqrt(fsum(value * value for value in errors) / len(errors)),
        "daily_energy_bias_kwh": fsum(errors) / 6.0,
        "absolute_error_sum_kw": fsum(abs(value) for value in errors),
        "squared_error_sum_kw2": fsum(value * value for value in errors),
        "count": len(errors),
    }

def _daily_forecast_evidence(
    *,
    day: date,
    data: FormalQ2Data,
    load_candidate: str,
    pv_candidate: str,
) -> dict[str, object]:
    load_history = {source: data.load_kw_by_day[source] for source in data.dates if source < day}
    pv_history = {source: data.pv_kw_by_day[source] for source in data.dates if source < day}
    load_forecast, load_sources = forecast_candidate(load_candidate, day, load_history)
    pv_forecast, pv_sources = forecast_candidate(pv_candidate, day, pv_history)
    if any(source >= day for source in (*load_sources, *pv_sources)):
        raise RuntimeError("Q2_FORMAL_FORECAST_FUTURE_LEAKAGE")
    actual_load = data.load_kw_by_day[day]
    actual_pv = data.pv_kw_by_day[day]
    net_errors = tuple(
        (float(load_forecast[k]) - float(pv_forecast[k]))
        - (float(actual_load[k]) - float(actual_pv[k]))
        for k in range(POSITIONS_PER_DAY)
    )
    return {
        "load": _forecast_metrics(load_forecast, actual_load),
        "pv": _forecast_metrics(pv_forecast, actual_pv),
        "net_absolute_energy_error_kwh": fsum(abs(value) for value in net_errors) / 6.0,
        "load_source_days": [item.isoformat() for item in load_sources],
        "pv_source_days": [item.isoformat() for item in pv_sources],
        "future_actual_leakage_count": 0,
    }

def _safe_correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) < 2 or max(left) == min(left) or max(right) == min(right):
        return None
    return float(correlation(left, right))

def _aggregate_forecast(daily: Sequence[dict[str, object]], series: str) -> dict[str, float]:
    items = [item["forecast_error"][series] for item in daily]
    count = sum(int(item["count"]) for item in items)
    return {
        "mae_kw": fsum(float(item["absolute_error_sum_kw"]) for item in items) / count,
        "rmse_kw": sqrt(fsum(float(item["squared_error_sum_kw2"]) for item in items) / count),
        "daily_energy_bias_kwh": fsum(float(item["daily_energy_bias_kwh"]) for item in items) / len(items),
    }

def build_annual_summary(daily: Sequence[dict[str, object]], runtime_seconds: float) -> dict[str, object]:
    planned = fsum(float(item["totals"]["planned_purchase_kwh"]) for item in daily)
    ordinary = fsum(float(item["totals"]["ordinary_actual_purchase_kwh"]) for item in daily)
    emergency = fsum(float(item["totals"]["emergency_purchase_kwh"]) for item in daily)
    planned_cost = fsum(float(item["costs"]["planned_purchase_cost"]) for item in daily)
    emergency_cost = fsum(float(item["costs"]["emergency_purchase_cost"]) for item in daily)
    total_cost = fsum(float(item["costs"]["total_cost"]) for item in daily)
    return {
        "full_q2_days": len(daily),
        "total_planned_purchase_kwh": planned,
        "total_ordinary_actual_purchase_kwh": ordinary,
        "total_emergency_purchase_kwh": emergency,
        "total_planned_purchase_cost": planned_cost,
        "total_emergency_purchase_cost": emergency_cost,
        "total_cost": total_cost,
        "total_charge_kwh": fsum(float(item["totals"]["charge_kwh"]) for item in daily),
        "total_discharge_kwh": fsum(float(item["totals"]["discharge_kwh"]) for item in daily),
        "emergency_purchase_days": sum(float(item["totals"]["emergency_purchase_kwh"]) > NUMERICAL_TOLERANCE for item in daily),
        "emergency_purchase_intervals": sum(int(item["validation"]["emergency_purchase_interval_count"]) for item in daily),
        "average_daily_emergency_purchase_kwh": emergency / len(daily),
        "feb1_initial_soc_kwh": float(daily[0]["soc"]["start_kwh"]),
        "dec31_final_soc_kwh": float(daily[-1]["soc"]["end_kwh"]),
        "annual_soc_min_kwh": min(float(item["soc"]["minimum_kwh"]) for item in daily),
        "annual_soc_max_kwh": max(float(item["soc"]["maximum_kwh"]) for item in daily),
        "max_abs_balance_residual": max(float(item["validation"]["max_abs_balance_residual"]) for item in daily),
        "max_abs_soc_recalculation_error": max(float(item["validation"]["max_abs_soc_recalculation_error"]) for item in daily),
        "max_cross_day_soc_continuity_error": max(float(item["validation"]["cross_day_soc_continuity_error"]) for item in daily),
        "max_cost_recompute_delta": max(float(item["validation"]["cost_recompute_delta"]) for item in daily),
        "max_actual_objective_lock_violation": max(float(item["validation"]["max_actual_objective_lock_violation"]) for item in daily),
        "future_actual_leakage_count": sum(int(item["validation"]["future_actual_leakage_count"]) for item in daily),
        "model_selection_future_leakage_count": 0,
        "past_decision_mutation_count": sum(int(item["validation"]["past_decision_mutation_count"]) for item in daily),
        "daily_soc_reset_count": sum(int(item["validation"]["daily_soc_reset_count"]) for item in daily),
        "simultaneous_charge_discharge_count": sum(int(item["validation"]["simultaneous_charge_discharge_count"]) for item in daily),
        "normal_level3_solver_calls": sum(int(item["solver_diagnostics"]["normal_level3_solver_calls"]) for item in daily),
        "theoretical_lower_bound_certificate_acceptances": sum(int(item["solver_diagnostics"]["theoretical_lower_bound_certificate_acceptances"]) for item in daily),
        "soc_boundary_canonicalization_count": sum(int(item["validation"]["soc_boundary_canonicalization_count"]) for item in daily),
        "soc_boundary_max_correction": max(float(item["validation"]["soc_boundary_max_correction"]) for item in daily),
        "unexpected_prior_objective_improvement_count": sum(int(item["validation"]["unexpected_prior_objective_improvement_count"]) for item in daily),
        "total_runtime_seconds": runtime_seconds,
        "emergency_to_planned_purchase_ratio": emergency / planned if planned else None,
        "emergency_cost_share": emergency_cost / total_cost if total_cost else None,
    }
