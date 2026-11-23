"""Minimal moving-average forecast baseline."""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

from ..base import make_result_summary
from ..validators import build_risk_item, check_nan_inf, check_numeric_array


MODEL_SPEC = {
    "model_id": "forecasting.moving_average",
    "model_family": "forecasting",
    "inputs_schema": {
        "series": "optional numeric array",
        "input_path": "optional CSV path relative to case_dir",
        "value_column": "CSV value column, defaults to first column",
        "window": "positive integer",
        "horizon": "positive integer",
        "test_size": "positive integer",
    },
    "outputs_schema": {
        "forecast": "numeric array",
        "MAE": "number",
        "RMSE": "number",
        "MAPE": "number or null",
        "model_label": "baseline_forecast",
    },
    "supported": True,
    "limitations": [
        "Baseline moving average only.",
        "No ARIMA, deep learning, seasonality, or causal forecast claim.",
        "Deterministic train/test split; no random search.",
    ],
    "failure_conditions": [
        "series_too_short",
        "input_nan_or_inf",
        "invalid_horizon",
        "output_nan_or_inf",
    ],
}


def _read_csv_series(case_dir: Path, config: dict[str, Any]) -> tuple[list[float], list[dict[str, Any]]]:
    input_path = config.get("input_path")
    if not input_path:
        return [], [
            build_risk_item(
                severity="fatal",
                category="input_validation",
                message="input_path is required when series is absent",
            )
        ]
    path = case_dir / str(input_path)
    if not path.exists():
        return [], [
            build_risk_item(
                severity="fatal",
                category="input_validation",
                message=f"forecast input file missing: {input_path}",
                path=str(input_path),
            )
        ]
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return [], [
                build_risk_item(
                    severity="fatal",
                    category="input_validation",
                    message="forecast CSV has no header",
                    path=str(input_path),
                )
            ]
        column = config.get("value_column") or reader.fieldnames[0]
        series: list[float] = []
        risks: list[dict[str, Any]] = []
        for row_idx, row in enumerate(reader, start=2):
            try:
                series.append(float(row[str(column)]))
            except (TypeError, ValueError, KeyError) as exc:
                risks.append(
                    build_risk_item(
                        severity="fatal",
                        category="input_validation",
                        message=f"invalid forecast CSV row {row_idx}: {exc}",
                        path=str(input_path),
                    )
                )
        return series, risks


def _rolling_predictions(series: list[float], train_end: int, test_size: int, window: int) -> list[float]:
    history = series[:train_end]
    preds: list[float] = []
    for idx in range(test_size):
        pred = sum(history[-window:]) / window
        preds.append(pred)
        history.append(series[train_end + idx])
    return preds


def run(config: dict[str, Any], *, run_id: str, case_dir: Path | None = None) -> dict[str, Any]:
    case_id = case_dir.name if case_dir else None
    if "series" in config:
        series, risks = check_numeric_array(config.get("series"), field="series")
    elif case_dir:
        series, risks = _read_csv_series(case_dir, config)
    else:
        series, risks = [], [
            build_risk_item(
                severity="fatal",
                category="input_validation",
                message="series or case_dir/input_path is required",
            )
        ]

    try:
        window = int(config.get("window", 3))
        horizon = int(config.get("horizon", 1))
        test_size = int(config.get("test_size", horizon))
    except (TypeError, ValueError):
        window = 0
        horizon = 0
        test_size = 0
        risks.append(build_risk_item(severity="fatal", category="input_validation", message="window, horizon and test_size must be integers"))

    if window <= 0:
        risks.append(build_risk_item(severity="fatal", category="input_validation", message="window must be positive"))
    if horizon <= 0:
        risks.append(build_risk_item(severity="fatal", category="input_validation", message="horizon must be positive", field="horizon"))
    if test_size <= 0:
        risks.append(build_risk_item(severity="fatal", category="input_validation", message="test_size must be positive"))
    if check_nan_inf(series):
        risks.append(build_risk_item(severity="fatal", category="input_validation", message="forecast series contains NaN/Inf"))
    if len(series) < window + test_size:
        risks.append(
            build_risk_item(
                severity="fatal",
                category="input_validation",
                message="series length is insufficient for window and test_size",
            )
        )

    if risks:
        return make_result_summary(
            run_id=run_id,
            case_id=case_id,
            model_id=MODEL_SPEC["model_id"],
            model_family=MODEL_SPEC["model_family"],
            success=False,
            status="input_invalid",
            metrics={},
            primary_results={"model_label": "baseline_forecast"},
            risk_items=risks,
            limitations=MODEL_SPEC["limitations"],
            failure_reason="input_invalid",
        )

    train_end = len(series) - test_size
    predictions = _rolling_predictions(series, train_end, test_size, window)
    actual = series[train_end:]
    errors = [pred - obs for pred, obs in zip(predictions, actual)]
    mae = sum(abs(item) for item in errors) / len(errors)
    rmse = math.sqrt(sum(item * item for item in errors) / len(errors))
    if any(abs(obs) < 1e-12 for obs in actual):
        mape = None
        risks.append(
            build_risk_item(
                severity="warning",
                category="metric",
                message="MAPE is undefined for zero actual values",
            )
        )
    else:
        mape = sum(abs(err / obs) for err, obs in zip(errors, actual)) / len(errors)

    forecast_history = series[:]
    forecast: list[float] = []
    for _ in range(horizon):
        next_value = sum(forecast_history[-window:]) / window
        forecast.append(next_value)
        forecast_history.append(next_value)

    if check_nan_inf({"forecast": forecast, "MAE": mae, "RMSE": rmse, "MAPE": mape}):
        risks.append(build_risk_item(severity="fatal", category="output_validation", message="moving_average output contains NaN/Inf"))

    return make_result_summary(
        run_id=run_id,
        case_id=case_id,
        model_id=MODEL_SPEC["model_id"],
        model_family=MODEL_SPEC["model_family"],
        success=not any(item.get("fatal") for item in risks),
        status="baseline_forecast",
        metrics={"MAE": mae, "RMSE": rmse, "MAPE": mape},
        primary_results={
            "model_label": "baseline_forecast",
            "forecast": forecast,
            "test_predictions": predictions,
            "test_actual": actual,
        },
        risk_items=risks,
        limitations=MODEL_SPEC["limitations"],
    )
