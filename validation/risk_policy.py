"""Risk policy for X5-F2 model-aware validation.

Only machine-checkable errors are fatal. Semantic paper checks remain human
review items and must not be represented as automatic judgments.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


SUPPORTED_MODEL_FAMILIES = {"optimization", "statistics", "forecasting", "graph", "competition_q3"}

HARD_ASSERTION_NAMES = [
    "result_summary_json_parseable",
    "success_field_bool",
    "required_contract_fields_present",
    "fatal_count_matches_risk_items",
    "no_nan_inf",
    "failure_reason_when_failed",
    "success_true_requires_zero_fatal",
    "model_family_supported",
    "model_id_registered",
    "primary_results_non_empty_when_success",
]

SOFT_WARNING_NAMES = [
    "r2_low",
    "condition_number_high",
    "mape_high",
    "x_missing_but_objective_present",
    "residual_std_high",
    "limitations_present",
    "baseline_model_limited",
    "metric_not_comparable",
    "model_family_support_limited",
]

HUMAN_REVIEW_ITEMS = [
    "paper handwritten numbers must be manually reconciled with result_summary",
    "unit conversion must be manually reviewed",
    "model assumptions must be manually reviewed",
    "literature support must be manually verified",
    "heuristic stability must be manually reviewed when applicable",
    "figure and table explanations must be manually reviewed",
    "conclusion overreach must be manually reviewed",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def hard_assertion(name: str, passed: bool, message: str, *, path: str | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "status": "PASS" if passed else "FAIL",
        "severity": "info" if passed else "fatal",
        "message": message,
        "path": path,
    }


def warning(name: str, message: str, *, path: str | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "severity": "warning",
        "message": message,
        "path": path,
    }


def info(name: str, message: str, *, path: str | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "severity": "info",
        "message": message,
        "path": path,
    }


def validation_status(fatal_count: int, warning_count: int) -> str:
    if fatal_count > 0:
        return "FAIL"
    if warning_count > 0:
        return "PASS_WITH_WARNING"
    return "PASS"
