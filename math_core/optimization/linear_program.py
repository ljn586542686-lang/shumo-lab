"""Minimal linear programming wrapper.

Capability boundary: min c^T x with optional A_ub, b_ub, A_eq, b_eq and
bounds. SciPy is optional; without it this module returns unsupported fatal.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from ..base import make_result_summary
from ..validators import build_risk_item, check_numeric_array


MODEL_SPEC = {
    "model_id": "optimization.linear_program",
    "model_family": "optimization",
    "inputs_schema": {
        "c": "numeric array",
        "A_ub": "optional numeric matrix",
        "b_ub": "optional numeric array",
        "A_eq": "optional numeric matrix",
        "b_eq": "optional numeric array",
        "bounds": "optional list of [lower, upper]",
    },
    "outputs_schema": {
        "status": "string",
        "objective_value": "number or null",
        "x": "numeric array or null",
        "constraint_violation": "number or null",
        "solver_message": "string",
        "success": "boolean",
        "failure_reason": "string when failed",
    },
    "supported": True,
    "limitations": [
        "Only continuous linear programming is covered.",
        "SciPy linprog is required for solving.",
        "Result is a solver-reported optimum when SciPy succeeds; no independent global optimality proof is generated.",
    ],
    "failure_conditions": [
        "scipy_unavailable",
        "infeasible",
        "unbounded",
        "solver_error",
        "objective_value_nan_or_inf",
        "constraint_violation_exceeds_threshold",
    ],
}


def _matrix(value: Any, field: str) -> tuple[list[list[float]] | None, list[dict[str, Any]]]:
    if value is None:
        return None, []
    if not isinstance(value, list):
        return None, [
            build_risk_item(
                severity="fatal",
                category="input_validation",
                message=f"{field} must be a matrix",
                field=field,
            )
        ]
    rows: list[list[float]] = []
    risks: list[dict[str, Any]] = []
    for idx, row in enumerate(value):
        nums, row_risks = check_numeric_array(row, field=f"{field}[{idx}]")
        risks.extend(row_risks)
        rows.append(nums)
    return rows, risks


def _bounds(value: Any, n: int) -> tuple[list[tuple[float | None, float | None]] | None, list[dict[str, Any]]]:
    if value is None:
        return None, []
    if not isinstance(value, list) or len(value) != n:
        return None, [
            build_risk_item(
                severity="fatal",
                category="input_validation",
                message="bounds must be a list with the same length as c",
                field="bounds",
            )
        ]
    bounds: list[tuple[float | None, float | None]] = []
    risks: list[dict[str, Any]] = []
    for idx, item in enumerate(value):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            risks.append(
                build_risk_item(
                    severity="fatal",
                    category="input_validation",
                    message=f"bounds[{idx}] must be [lower, upper]",
                    field=f"bounds[{idx}]",
                )
            )
            continue
        pair: list[float | None] = []
        for jdx, raw in enumerate(item):
            if raw is None:
                pair.append(None)
                continue
            try:
                number = float(raw)
            except (TypeError, ValueError):
                risks.append(
                    build_risk_item(
                        severity="fatal",
                        category="input_validation",
                        message=f"bounds[{idx}][{jdx}] is not numeric",
                        field=f"bounds[{idx}][{jdx}]",
                    )
                )
                number = 0.0
            if math.isnan(number) or math.isinf(number):
                risks.append(
                    build_risk_item(
                        severity="fatal",
                        category="input_validation",
                        message=f"bounds[{idx}][{jdx}] is NaN/Inf",
                        field=f"bounds[{idx}][{jdx}]",
                    )
                )
            pair.append(number)
        if len(pair) == 2:
            bounds.append((pair[0], pair[1]))
    return bounds, risks


def _max_constraint_violation(
    x: list[float],
    A_ub: list[list[float]] | None,
    b_ub: list[float] | None,
    A_eq: list[list[float]] | None,
    b_eq: list[float] | None,
) -> float:
    violation = 0.0
    if A_ub is not None and b_ub is not None:
        for row, rhs in zip(A_ub, b_ub):
            violation = max(violation, sum(a * xi for a, xi in zip(row, x)) - rhs)
    if A_eq is not None and b_eq is not None:
        for row, rhs in zip(A_eq, b_eq):
            violation = max(violation, abs(sum(a * xi for a, xi in zip(row, x)) - rhs))
    return max(0.0, violation)


def run(config: dict[str, Any], *, run_id: str, case_dir: Path | None = None) -> dict[str, Any]:
    case_id = case_dir.name if case_dir else None
    c, risks = check_numeric_array(config.get("c"), field="c")
    A_ub, matrix_risks = _matrix(config.get("A_ub"), "A_ub")
    risks.extend(matrix_risks)
    b_ub, b_ub_risks = check_numeric_array(config.get("b_ub"), field="b_ub", allow_empty=True) if config.get("b_ub") is not None else (None, [])
    risks.extend(b_ub_risks)
    A_eq, eq_risks = _matrix(config.get("A_eq"), "A_eq")
    risks.extend(eq_risks)
    b_eq, b_eq_risks = check_numeric_array(config.get("b_eq"), field="b_eq", allow_empty=True) if config.get("b_eq") is not None else (None, [])
    risks.extend(b_eq_risks)
    bounds, bounds_risks = _bounds(config.get("bounds"), len(c))
    risks.extend(bounds_risks)
    tolerance = float(config.get("constraint_violation_tolerance", 1e-7))

    if risks:
        return make_result_summary(
            run_id=run_id,
            case_id=case_id,
            model_id=MODEL_SPEC["model_id"],
            model_family=MODEL_SPEC["model_family"],
            success=False,
            status="input_invalid",
            metrics={},
            primary_results={
                "objective_value": None,
                "x": None,
                "constraint_violation": None,
                "solver_message": "input validation failed",
            },
            risk_items=risks,
            limitations=MODEL_SPEC["limitations"],
            failure_reason="input_invalid",
        )

    try:
        from scipy.optimize import linprog  # type: ignore
    except Exception as exc:
        risk = build_risk_item(
            severity="fatal",
            category="solver",
            message=f"scipy.optimize.linprog is unavailable: {exc}",
            recommendation="Install scipy before using optimization.linear_program, or select another model.",
        )
        return make_result_summary(
            run_id=run_id,
            case_id=case_id,
            model_id=MODEL_SPEC["model_id"],
            model_family=MODEL_SPEC["model_family"],
            success=False,
            status="unsupported",
            metrics={},
            primary_results={
                "objective_value": None,
                "x": None,
                "constraint_violation": None,
                "solver_message": "unsupported: scipy unavailable",
            },
            risk_items=[risk],
            limitations=MODEL_SPEC["limitations"],
            failure_reason="scipy_unavailable",
        )

    try:
        result = linprog(c=c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method="highs")
    except Exception as exc:
        return make_result_summary(
            run_id=run_id,
            case_id=case_id,
            model_id=MODEL_SPEC["model_id"],
            model_family=MODEL_SPEC["model_family"],
            success=False,
            status="solver_error",
            metrics={},
            primary_results={
                "objective_value": None,
                "x": None,
                "constraint_violation": None,
                "solver_message": str(exc),
            },
            risk_items=[
                build_risk_item(
                    severity="fatal",
                    category="solver",
                    message=f"linprog solver_error: {exc}",
                )
            ],
            limitations=MODEL_SPEC["limitations"],
            failure_reason="solver_error",
        )

    status = str(getattr(result, "message", ""))
    if int(getattr(result, "status", -1)) == 2:
        risks.append(build_risk_item(severity="fatal", category="solver", message="linear program infeasible"))
        failure_reason = "infeasible"
    elif int(getattr(result, "status", -1)) == 3:
        risks.append(build_risk_item(severity="fatal", category="solver", message="linear program unbounded"))
        failure_reason = "unbounded"
    elif not bool(getattr(result, "success", False)):
        risks.append(build_risk_item(severity="fatal", category="solver", message=f"linprog failed: {status}"))
        failure_reason = "solver_error"
    else:
        failure_reason = None

    x = [float(item) for item in getattr(result, "x", [])] if getattr(result, "x", None) is not None else None
    objective = float(getattr(result, "fun")) if getattr(result, "fun", None) is not None else None
    violation = _max_constraint_violation(x, A_ub, b_ub, A_eq, b_eq) if x is not None else None
    if objective is not None and (math.isnan(objective) or math.isinf(objective)):
        risks.append(build_risk_item(severity="fatal", category="solver", message="objective_value is NaN/Inf"))
        objective = None
        failure_reason = failure_reason or "objective_value_nan_or_inf"
    if violation is not None and violation > tolerance:
        risks.append(
            build_risk_item(
                severity="fatal",
                category="solver",
                message=f"constraint_violation {violation} exceeds tolerance {tolerance}",
            )
        )
        failure_reason = failure_reason or "constraint_violation_exceeds_threshold"

    success = bool(getattr(result, "success", False)) and not any(item.get("fatal") for item in risks)
    return make_result_summary(
        run_id=run_id,
        case_id=case_id,
        model_id=MODEL_SPEC["model_id"],
        model_family=MODEL_SPEC["model_family"],
        success=success,
        status="solver_reported_optimum" if success else "fatal",
        metrics={"objective_value": objective, "constraint_violation": violation},
        primary_results={
            "objective_value": objective,
            "x": x,
            "constraint_violation": violation,
            "solver_message": status,
            "success": success,
            "failure_reason": failure_reason,
        },
        risk_items=risks,
        limitations=MODEL_SPEC["limitations"],
        failure_reason=failure_reason,
    )
