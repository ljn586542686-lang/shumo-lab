"""Minimal linear regression baseline."""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

from ..base import make_result_summary
from ..validators import build_risk_item, check_nan_inf


MODEL_SPEC = {
    "model_id": "statistics.linear_regression",
    "model_family": "statistics",
    "inputs_schema": {
        "input_path": "optional CSV path relative to case_dir",
        "target": "target column name",
        "features": "feature column names",
        "X": "optional numeric matrix",
        "y": "optional numeric array",
    },
    "outputs_schema": {
        "beta": "coefficient array",
        "intercept": "number",
        "R2": "number",
        "RMSE": "number",
        "residual_mean": "number",
        "residual_std": "number",
        "condition_number": "number",
    },
    "supported": True,
    "limitations": [
        "Ordinary least squares only.",
        "No automatic feature engineering or causal claim.",
        "Uses numpy when available; standard-library normal-equation fallback is only a minimal baseline.",
    ],
    "failure_conditions": [
        "input_nan_or_inf",
        "insufficient_samples",
        "matrix_unsolvable",
        "output_nan_or_inf",
    ],
}


def _read_csv(case_dir: Path, config: dict[str, Any]) -> tuple[list[list[float]], list[float], list[dict[str, Any]]]:
    risks: list[dict[str, Any]] = []
    input_path = config.get("input_path")
    target = config.get("target")
    features = config.get("features")
    if not input_path or not target or not features:
        return [], [], [
            build_risk_item(
                severity="fatal",
                category="input_validation",
                message="input_path, target and features are required for CSV regression input",
            )
        ]
    path = case_dir / str(input_path)
    if not path.exists():
        return [], [], [
            build_risk_item(
                severity="fatal",
                category="input_validation",
                message=f"regression input file missing: {input_path}",
                path=str(input_path),
            )
        ]
    X: list[list[float]] = []
    y: list[float] = []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return [], [], [
                build_risk_item(
                    severity="fatal",
                    category="input_validation",
                    message="regression CSV has no header",
                    path=str(input_path),
                )
            ]
        for row_idx, row in enumerate(reader, start=2):
            try:
                X.append([float(row[name]) for name in features])
                y.append(float(row[target]))
            except (TypeError, ValueError, KeyError) as exc:
                risks.append(
                    build_risk_item(
                        severity="fatal",
                        category="input_validation",
                        message=f"invalid regression CSV row {row_idx}: {exc}",
                        path=str(input_path),
                    )
                )
    return X, y, risks


def _inline_xy(config: dict[str, Any]) -> tuple[list[list[float]], list[float], list[dict[str, Any]]]:
    risks: list[dict[str, Any]] = []
    raw_X = config.get("X")
    raw_y = config.get("y")
    if not isinstance(raw_X, list) or not isinstance(raw_y, list):
        return [], [], [
            build_risk_item(
                severity="fatal",
                category="input_validation",
                message="X and y must be provided when input_path is absent",
            )
        ]
    X: list[list[float]] = []
    y: list[float] = []
    for idx, row in enumerate(raw_X):
        if not isinstance(row, list):
            risks.append(build_risk_item(severity="fatal", category="input_validation", message=f"X[{idx}] must be a list"))
            continue
        try:
            X.append([float(item) for item in row])
        except (TypeError, ValueError) as exc:
            risks.append(build_risk_item(severity="fatal", category="input_validation", message=f"X[{idx}] invalid: {exc}"))
    for idx, item in enumerate(raw_y):
        try:
            y.append(float(item))
        except (TypeError, ValueError) as exc:
            risks.append(build_risk_item(severity="fatal", category="input_validation", message=f"y[{idx}] invalid: {exc}"))
    return X, y, risks


def _transpose(matrix: list[list[float]]) -> list[list[float]]:
    return [list(col) for col in zip(*matrix)]


def _matmul(left: list[list[float]], right: list[list[float]]) -> list[list[float]]:
    return [[sum(a * b for a, b in zip(row, col)) for col in zip(*right)] for row in left]


def _matvec(matrix: list[list[float]], vector: list[float]) -> list[float]:
    return [sum(a * b for a, b in zip(row, vector)) for row in matrix]


def _solve_linear_system(A: list[list[float]], b: list[float]) -> list[float]:
    n = len(A)
    aug = [row[:] + [b[idx]] for idx, row in enumerate(A)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-12:
            raise ValueError("matrix is singular or ill-conditioned")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        pivot_value = aug[col][col]
        for j in range(col, n + 1):
            aug[col][j] /= pivot_value
        for row in range(n):
            if row == col:
                continue
            factor = aug[row][col]
            for j in range(col, n + 1):
                aug[row][j] -= factor * aug[col][j]
    return [aug[i][n] for i in range(n)]


def _condition_number(matrix: list[list[float]]) -> float:
    try:
        import numpy as np  # type: ignore

        return float(np.linalg.cond(np.array(matrix, dtype=float)))
    except Exception:
        diag = [abs(matrix[i][i]) for i in range(min(len(matrix), len(matrix[0])))]
        positive = [item for item in diag if item > 0]
        if not positive:
            return float("inf")
        return max(positive) / min(positive)


def _least_squares(X: list[list[float]], y: list[float]) -> tuple[list[float], float, str]:
    X_design = [[1.0] + row for row in X]
    try:
        import numpy as np  # type: ignore

        beta_all, *_ = np.linalg.lstsq(np.array(X_design, dtype=float), np.array(y, dtype=float), rcond=None)
        return [float(item) for item in beta_all[1:]], float(beta_all[0]), "numpy_lstsq"
    except Exception:
        Xt = _transpose(X_design)
        XtX = _matmul(Xt, X_design)
        Xty = _matvec(Xt, y)
        beta_all = _solve_linear_system(XtX, Xty)
        return beta_all[1:], beta_all[0], "normal_equation_fallback"


def run(config: dict[str, Any], *, run_id: str, case_dir: Path | None = None) -> dict[str, Any]:
    if case_dir and config.get("input_path"):
        X, y, risks = _read_csv(case_dir, config)
    else:
        X, y, risks = _inline_xy(config)
    case_id = case_dir.name if case_dir else None

    if check_nan_inf({"X": X, "y": y}):
        risks.append(
            build_risk_item(
                severity="fatal",
                category="input_validation",
                message="regression input contains NaN/Inf",
                recommendation="Clean or impute inputs before running linear_regression.",
            )
        )
    n_samples = len(X)
    n_features = len(X[0]) if X else 0
    if n_samples != len(y):
        risks.append(build_risk_item(severity="fatal", category="input_validation", message="X and y length mismatch"))
    if n_samples <= n_features:
        risks.append(
            build_risk_item(
                severity="fatal",
                category="input_validation",
                message="sample count must be greater than feature count",
            )
        )
    if X and any(len(row) != n_features for row in X):
        risks.append(build_risk_item(severity="fatal", category="input_validation", message="feature row lengths are inconsistent"))

    if risks:
        return make_result_summary(
            run_id=run_id,
            case_id=case_id,
            model_id=MODEL_SPEC["model_id"],
            model_family=MODEL_SPEC["model_family"],
            success=False,
            status="input_invalid",
            metrics={},
            primary_results={},
            risk_items=risks,
            limitations=MODEL_SPEC["limitations"],
            failure_reason="input_invalid",
        )

    try:
        beta, intercept, solver = _least_squares(X, y)
    except Exception as exc:
        return make_result_summary(
            run_id=run_id,
            case_id=case_id,
            model_id=MODEL_SPEC["model_id"],
            model_family=MODEL_SPEC["model_family"],
            success=False,
            status="matrix_unsolvable",
            metrics={},
            primary_results={},
            risk_items=[
                build_risk_item(
                    severity="fatal",
                    category="solver",
                    message=f"linear regression matrix unsolvable: {exc}",
                )
            ],
            limitations=MODEL_SPEC["limitations"],
            failure_reason="matrix_unsolvable",
        )

    preds = [intercept + sum(coef * value for coef, value in zip(beta, row)) for row in X]
    residuals = [actual - pred for actual, pred in zip(y, preds)]
    rss = sum(item * item for item in residuals)
    mean_y = sum(y) / len(y)
    tss = sum((item - mean_y) ** 2 for item in y)
    r2 = 1.0 - rss / tss if tss > 0 else 1.0
    rmse = math.sqrt(rss / len(y))
    residual_mean = sum(residuals) / len(residuals)
    residual_std = math.sqrt(sum((item - residual_mean) ** 2 for item in residuals) / len(residuals))
    condition_number = _condition_number(_matmul(_transpose([[1.0] + row for row in X]), [[1.0] + row for row in X]))

    if condition_number > float(config.get("condition_number_warning_threshold", 1e8)):
        risks.append(build_risk_item(severity="warning", category="diagnostic", message="condition_number is large"))
    if r2 < float(config.get("r2_warning_threshold", 0.1)):
        risks.append(build_risk_item(severity="warning", category="diagnostic", message="R2 is low"))
    if residual_std > float(config.get("residual_std_warning_threshold", 1e6)):
        risks.append(build_risk_item(severity="warning", category="diagnostic", message="residual_std is large"))
    if check_nan_inf({"beta": beta, "intercept": intercept, "R2": r2, "RMSE": rmse}):
        risks.append(build_risk_item(severity="fatal", category="output_validation", message="linear regression output contains NaN/Inf"))

    metrics = {
        "R2": r2,
        "RMSE": rmse,
        "residual_mean": residual_mean,
        "residual_std": residual_std,
        "condition_number": condition_number,
        "solver": solver,
    }
    return make_result_summary(
        run_id=run_id,
        case_id=case_id,
        model_id=MODEL_SPEC["model_id"],
        model_family=MODEL_SPEC["model_family"],
        success=not any(item.get("fatal") for item in risks),
        status="ok" if not any(item.get("fatal") for item in risks) else "fatal",
        metrics=metrics,
        primary_results={"beta": beta, "intercept": intercept},
        risk_items=risks,
        limitations=MODEL_SPEC["limitations"],
    )
