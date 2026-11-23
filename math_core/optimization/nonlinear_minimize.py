"""Minimal nonlinear optimization wrapper for X5-F5.1.

Capability boundary: minimize f(x) subject to optional bounds and constraints,
using scipy.optimize.minimize. Only safe, declarative objective functions
expressed as polynomial-style dicts or Python lambda strings are NOT accepted;
instead, the objective must be one of the built-in named functions registered
below, or a sum-of-squares form specified via config. This avoids arbitrary
code execution while remaining testable and auditable.

For contest use, the caller specifies:
    objective_type: one of the registered built-in types
    objective_params: parameters for that type
    initial_guess: list of floats
    bounds: optional list of [lower, upper] per variable (None = unbounded)
    constraints: optional list of {type: 'ineq'|'eq', coefficients, rhs}
    method: optional string (default 'SLSQP')
    options: optional dict passed to scipy.optimize.minimize

Constraints are expressed declaratively as linear forms:
    {type: 'ineq', coefficients: [a0, a1, ...], rhs: b}
    meaning  sum(a_i * x_i) - b >= 0  for 'ineq'
    and      sum(a_i * x_i) - b == 0  for 'eq'

This avoids arbitrary code execution and keeps the interface auditable.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from ..base import make_result_summary
from ..validators import build_risk_item, check_numeric_array

# math_core decides success — not a bare scipy pass-through.
# scipy may report success=True while a constraint is still violated;
# we enforce our own threshold unconditionally.
CONSTRAINT_TOLERANCE: float = 1e-6

MODEL_SPEC = {
    "model_id": "optimization.nonlinear_minimize",
    "model_family": "optimization",
    "inputs_schema": {
        "objective_type": "string — registered built-in objective name",
        "objective_params": "dict — parameters for the chosen objective",
        "initial_guess": "numeric array — starting point for the solver",
        "bounds": "optional list of [lower, upper] per variable",
        "constraints": "optional list of {type, coefficients, rhs}",
        "method": "optional string (default SLSQP)",
        "options": "optional dict passed to scipy.optimize.minimize",
    },
    "outputs_schema": {
        "model_type": "string",
        "solver": "string",
        "status": "string",
        "success": "boolean",
        "objective_value": "number or null",
        "solution": "numeric array or null",
        "solver_status": "string",
        "message": "string",
        "constraint_violation": "number or null",
        "failure_reason": "string when failed",
        "diagnostics": "dict",
    },
    "supported": True,
    "limitations": [
        "Only built-in declarative objective types are supported; arbitrary callables are not accepted.",
        "Constraints must be expressed as linear forms; nonlinear constraints require a future extension.",
        "Convergence depends on scipy and the chosen method; local optima are not globally guaranteed.",
        "NaN/Inf in objective or solution causes failure; solver is not restarted automatically.",
        "This is a math_core depth patch, not AutoML and not a model recommendation system.",
    ],
    "failure_conditions": [
        "scipy_unavailable",
        "missing_initial_guess",
        "input_invalid",
        "objective_nan_or_inf",
        "solver_failed",
        "constraint_violation_exceeds_threshold",
        "unknown_objective_type",
    ],
}

# ---------------------------------------------------------------------------
# Built-in objective registry
# ---------------------------------------------------------------------------

def _obj_sum_of_squares(x: list[float], params: dict[str, Any]) -> float:
    """Minimize sum((x_i - target_i)^2). params: {targets: [t0, t1, ...]}"""
    targets = params.get("targets", [0.0] * len(x))
    return sum((xi - ti) ** 2 for xi, ti in zip(x, targets))


def _obj_quadratic(x: list[float], params: dict[str, Any]) -> float:
    """Minimize sum(a_i * x_i^2 + b_i * x_i + c_i).
    params: {a: [...], b: [...], c: [...]}  (all optional, default 1/0/0)
    """
    n = len(x)
    a = params.get("a", [1.0] * n)
    b = params.get("b", [0.0] * n)
    c = params.get("c", [0.0] * n)
    return sum(ai * xi ** 2 + bi * xi + ci for ai, bi, ci, xi in zip(a, b, c, x))


_OBJECTIVE_REGISTRY: dict[str, Any] = {
    "sum_of_squares": _obj_sum_of_squares,
    "quadratic": _obj_quadratic,
}


def _build_scipy_objective(objective_type: str, params: dict[str, Any]):
    fn = _OBJECTIVE_REGISTRY.get(objective_type)
    if fn is None:
        return None, build_risk_item(
            severity="fatal",
            category="input_validation",
            message=f"unknown objective_type: {objective_type!r}. Registered: {sorted(_OBJECTIVE_REGISTRY)}",
            field="objective_type",
        )
    def _wrapped(x_arr):
        return fn(list(x_arr), params)
    return _wrapped, None


# ---------------------------------------------------------------------------
# Bounds parsing (same style as linear_program)
# ---------------------------------------------------------------------------

def _parse_bounds(value: Any, n: int) -> tuple[list[tuple[float | None, float | None]] | None, list[dict[str, Any]]]:
    if value is None:
        return None, []
    if not isinstance(value, list) or len(value) != n:
        return None, [build_risk_item(
            severity="fatal",
            category="input_validation",
            message="bounds must be a list with the same length as initial_guess",
            field="bounds",
        )]
    result: list[tuple[float | None, float | None]] = []
    risks: list[dict[str, Any]] = []
    for idx, item in enumerate(value):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            risks.append(build_risk_item(
                severity="fatal",
                category="input_validation",
                message=f"bounds[{idx}] must be [lower, upper]",
                field=f"bounds[{idx}]",
            ))
            continue
        pair: list[float | None] = []
        for jdx, raw in enumerate(item):
            if raw is None:
                pair.append(None)
                continue
            try:
                v = float(raw)
            except (TypeError, ValueError):
                risks.append(build_risk_item(
                    severity="fatal",
                    category="input_validation",
                    message=f"bounds[{idx}][{jdx}] is not numeric",
                    field=f"bounds[{idx}][{jdx}]",
                ))
                v = 0.0
            if math.isnan(v) or math.isinf(v):
                risks.append(build_risk_item(
                    severity="fatal",
                    category="input_validation",
                    message=f"bounds[{idx}][{jdx}] is NaN/Inf",
                    field=f"bounds[{idx}][{jdx}]",
                ))
            pair.append(v)
        if len(pair) == 2:
            result.append((pair[0], pair[1]))
    return result, risks


# ---------------------------------------------------------------------------
# Constraints parsing (linear declarative form)
# ---------------------------------------------------------------------------

def _parse_constraints(value: Any, n: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse list of {type, coefficients, rhs} into scipy constraint dicts."""
    if not value:
        return [], []
    if not isinstance(value, list):
        return [], [build_risk_item(
            severity="fatal",
            category="input_validation",
            message="constraints must be a list",
            field="constraints",
        )]
    scipy_constraints: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    for idx, item in enumerate(value):
        if not isinstance(item, dict):
            risks.append(build_risk_item(
                severity="fatal",
                category="input_validation",
                message=f"constraints[{idx}] must be a dict",
                field=f"constraints[{idx}]",
            ))
            continue
        ctype = str(item.get("type", "ineq")).strip().lower()
        if ctype not in ("ineq", "eq"):
            risks.append(build_risk_item(
                severity="fatal",
                category="input_validation",
                message=f"constraints[{idx}].type must be 'ineq' or 'eq'",
                field=f"constraints[{idx}].type",
            ))
            continue
        coeffs_raw = item.get("coefficients")
        coeffs, coeff_risks = check_numeric_array(coeffs_raw, field=f"constraints[{idx}].coefficients")
        risks.extend(coeff_risks)
        if coeff_risks:
            continue
        if len(coeffs) != n:
            risks.append(build_risk_item(
                severity="fatal",
                category="input_validation",
                message=f"constraints[{idx}].coefficients length {len(coeffs)} != initial_guess length {n}",
                field=f"constraints[{idx}].coefficients",
            ))
            continue
        try:
            rhs = float(item.get("rhs", 0.0))
        except (TypeError, ValueError):
            risks.append(build_risk_item(
                severity="fatal",
                category="input_validation",
                message=f"constraints[{idx}].rhs is not numeric",
                field=f"constraints[{idx}].rhs",
            ))
            continue
        # Closure captures coeffs/rhs by value via default args
        def _make_fun(c=coeffs, r=rhs):
            def fun(x_arr):
                return sum(ci * xi for ci, xi in zip(c, x_arr)) - r
            return fun
        scipy_constraints.append({"type": ctype, "fun": _make_fun()})
    return scipy_constraints, risks


# ---------------------------------------------------------------------------
# Constraint violation measurement
# ---------------------------------------------------------------------------

def _max_constraint_violation(
    x: list[float],
    raw_constraints: list[dict[str, Any]] | None,
) -> float:
    if not raw_constraints:
        return 0.0
    violation = 0.0
    for item in raw_constraints:
        if not isinstance(item, dict):
            continue
        ctype = str(item.get("type", "ineq")).strip().lower()
        coeffs_raw = item.get("coefficients", [])
        try:
            rhs = float(item.get("rhs", 0.0))
            # Convert each coefficient to float defensively; skip on failure.
            coeffs = [float(c) for c in (coeffs_raw or [])]
            val = sum(ci * xi for ci, xi in zip(coeffs, x)) - rhs
        except (TypeError, ValueError):
            continue  # malformed constraint — skip; parse phase already flagged it
        if ctype == "ineq":
            # ineq means fun >= 0; violation if val < 0
            violation = max(violation, -val)
        else:
            violation = max(violation, abs(val))
    return max(0.0, violation)


# ---------------------------------------------------------------------------
# Failure-contract helper (testable without running scipy)
# ---------------------------------------------------------------------------

def _apply_failure_contract(
    *,
    solution: list[float] | None,
    raw_constraints: list[dict[str, Any]] | None,
    risks: list[dict[str, Any]],
    effective_tolerance: float,
    failure_reason: str | None,
) -> tuple[float, list[dict[str, Any]], str | None, bool]:
    """Compute constraint_violation, append fatal risk if needed, decide success.

    Returns (violation_float, updated_risks, updated_failure_reason, success).

    violation_float is always a float (0.0 when no constraints or no solution),
    which satisfies the model-aware validator's non-null requirement.
    """
    # Always produce a float, never None — 0.0 means "no violation measured".
    violation: float = 0.0

    if solution is not None and raw_constraints:
        violation = _max_constraint_violation(solution, raw_constraints)
        if violation > effective_tolerance:
            risks = list(risks)  # copy so caller's list is not mutated
            risks.append(build_risk_item(
                severity="fatal", category="solver",
                message=(
                    f"constraint_violation {violation:.6g} exceeds "
                    f"effective_tolerance {effective_tolerance}"
                ),
            ))
            failure_reason = failure_reason or "constraint_violation_exceeds_threshold"

    # math_core determines success independently — never a bare scipy pass-through.
    # Even if scipy reports success=True, a constraint violation above
    # effective_tolerance forces success=False here.
    has_fatal = any(r.get("fatal") for r in risks)
    success = not has_fatal and violation <= effective_tolerance

    return violation, risks, failure_reason, success


# ---------------------------------------------------------------------------
# Main run function
# ---------------------------------------------------------------------------

def run(config: dict[str, Any], *, run_id: str, case_dir: Path | None = None) -> dict[str, Any]:
    case_id = case_dir.name if case_dir else None
    solver_key = "nonlinear_minimize"

    # --- validate initial_guess ---
    x0, ig_risks = check_numeric_array(config.get("initial_guess"), field="initial_guess")
    risks: list[dict[str, Any]] = list(ig_risks)
    if ig_risks:
        return make_result_summary(
            run_id=run_id, case_id=case_id,
            model_id=MODEL_SPEC["model_id"], model_family=MODEL_SPEC["model_family"],
            success=False, status="input_invalid", metrics={},
            primary_results=_empty_primary(solver_key, "input validation failed: missing initial_guess"),
            risk_items=risks, limitations=MODEL_SPEC["limitations"],
            failure_reason="missing_initial_guess",
        )

    n = len(x0)

    # --- validate objective ---
    objective_type = str(config.get("objective_type") or "").strip()
    objective_params = config.get("objective_params") or {}
    if not isinstance(objective_params, dict):
        objective_params = {}
    scipy_obj, obj_risk = _build_scipy_objective(objective_type, objective_params)
    if obj_risk:
        risks.append(obj_risk)
        return make_result_summary(
            run_id=run_id, case_id=case_id,
            model_id=MODEL_SPEC["model_id"], model_family=MODEL_SPEC["model_family"],
            success=False, status="input_invalid", metrics={},
            primary_results=_empty_primary(solver_key, f"unknown objective_type: {objective_type!r}"),
            risk_items=risks, limitations=MODEL_SPEC["limitations"],
            failure_reason="unknown_objective_type",
        )

    # --- validate and resolve effective tolerance (Fix 2 & 4) ---
    raw_tol = config.get("constraint_violation_tolerance")
    if raw_tol is None:
        effective_tolerance: float = CONSTRAINT_TOLERANCE
    else:
        try:
            effective_tolerance = float(raw_tol)
            if math.isnan(effective_tolerance) or math.isinf(effective_tolerance) or effective_tolerance < 0:
                raise ValueError(f"tolerance must be a finite non-negative float, got {raw_tol!r}")
        except (TypeError, ValueError) as exc:
            risk = build_risk_item(
                severity="fatal", category="input_validation",
                message=f"invalid constraint_violation_tolerance: {exc}",
                field="constraint_violation_tolerance",
            )
            return make_result_summary(
                run_id=run_id, case_id=case_id,
                model_id=MODEL_SPEC["model_id"], model_family=MODEL_SPEC["model_family"],
                success=False, status="input_invalid", metrics={},
                primary_results=_empty_primary(solver_key, f"invalid tolerance: {raw_tol!r}"),
                risk_items=[risk], limitations=MODEL_SPEC["limitations"],
                failure_reason="invalid_tolerance",
            )

    # --- parse bounds ---
    bounds, bounds_risks = _parse_bounds(config.get("bounds"), n)
    risks.extend(bounds_risks)

    # --- parse constraints ---
    raw_constraints = config.get("constraints")
    scipy_constraints, constr_risks = _parse_constraints(raw_constraints, n)
    risks.extend(constr_risks)

    if any(r.get("fatal") for r in risks):
        return make_result_summary(
            run_id=run_id, case_id=case_id,
            model_id=MODEL_SPEC["model_id"], model_family=MODEL_SPEC["model_family"],
            success=False, status="input_invalid", metrics={},
            primary_results=_empty_primary(solver_key, "input validation failed"),
            risk_items=risks, limitations=MODEL_SPEC["limitations"],
            failure_reason="input_invalid",
        )

    method = str(config.get("method") or "SLSQP").strip()
    options = config.get("options")
    if not isinstance(options, dict):
        options = {}

    # --- import scipy ---
    try:
        from scipy.optimize import minimize as scipy_minimize  # type: ignore
    except Exception as exc:
        risk = build_risk_item(
            severity="fatal", category="solver",
            message=f"scipy.optimize.minimize is unavailable: {exc}",
            recommendation="Install scipy before using optimization.nonlinear_minimize.",
        )
        return make_result_summary(
            run_id=run_id, case_id=case_id,
            model_id=MODEL_SPEC["model_id"], model_family=MODEL_SPEC["model_family"],
            success=False, status="unsupported", metrics={},
            primary_results=_empty_primary(solver_key, "unsupported: scipy unavailable"),
            risk_items=[risk], limitations=MODEL_SPEC["limitations"],
            failure_reason="scipy_unavailable",
        )

    # --- run solver ---
    try:
        import numpy as np  # type: ignore
        x0_arr = np.array(x0, dtype=float)
        scipy_bounds = bounds if bounds is not None else None
        result = scipy_minimize(
            scipy_obj,
            x0_arr,
            method=method,
            bounds=scipy_bounds,
            constraints=scipy_constraints,
            options=options if options else None,
        )
    except Exception as exc:
        risk = build_risk_item(
            severity="fatal", category="solver",
            message=f"scipy.optimize.minimize raised: {exc}",
        )
        return make_result_summary(
            run_id=run_id, case_id=case_id,
            model_id=MODEL_SPEC["model_id"], model_family=MODEL_SPEC["model_family"],
            success=False, status="solver_error", metrics={},
            primary_results=_empty_primary(solver_key, str(exc)),
            risk_items=[risk], limitations=MODEL_SPEC["limitations"],
            failure_reason="solver_failed",
        )

    solver_success: bool = bool(getattr(result, "success", False))
    solver_message: str = str(getattr(result, "message", ""))
    solver_status: str = "converged" if solver_success else "failed"
    x_arr = getattr(result, "x", None)
    fun_val = getattr(result, "fun", None)

    solution: list[float] | None = None
    objective_value: float | None = None
    failure_reason: str | None = None

    if x_arr is not None:
        solution = [float(v) for v in x_arr]
    if fun_val is not None:
        try:
            objective_value = float(fun_val)
        except (TypeError, ValueError):
            objective_value = None

    # --- NaN/Inf checks ---
    if objective_value is not None and (math.isnan(objective_value) or math.isinf(objective_value)):
        risks.append(build_risk_item(
            severity="fatal", category="solver",
            message="objective_value is NaN/Inf",
        ))
        objective_value = None
        failure_reason = "objective_nan_or_inf"

    if solution is not None:
        for idx, v in enumerate(solution):
            if math.isnan(v) or math.isinf(v):
                risks.append(build_risk_item(
                    severity="fatal", category="solver",
                    message=f"solution[{idx}] is NaN/Inf",
                    field=f"solution[{idx}]",
                ))
                solution = None
                failure_reason = failure_reason or "objective_nan_or_inf"
                break

    if not solver_success:
        risks.append(build_risk_item(
            severity="fatal", category="solver",
            message=f"scipy.optimize.minimize did not converge: {solver_message}",
        ))
        failure_reason = failure_reason or "solver_failed"

    # --- failure contract (Fix 1 & 2): violation is always float, tolerance unified ---
    violation, risks, failure_reason, success = _apply_failure_contract(
        solution=solution,
        raw_constraints=raw_constraints,
        risks=risks,
        effective_tolerance=effective_tolerance,
        failure_reason=failure_reason,
    )

    primary = {
        "model_type": MODEL_SPEC["model_id"],
        "solver": solver_key,
        "status": "solver_reported_optimum" if success else "fatal",
        "success": success,
        "objective_value": objective_value,
        "solution": solution,
        "solver_status": solver_status,
        "message": solver_message,
        "constraint_violation": violation,  # always float, never None
        "failure_reason": failure_reason,
        "diagnostics": {
            "method": method,
            "n_vars": n,
            "objective_type": objective_type,
            "n_constraints": len(scipy_constraints),
            "bounds_provided": bounds is not None,
            "scipy_nit": int(getattr(result, "nit", 0)),
            "scipy_nfev": int(getattr(result, "nfev", 0)),
        },
    }

    return make_result_summary(
        run_id=run_id, case_id=case_id,
        model_id=MODEL_SPEC["model_id"], model_family=MODEL_SPEC["model_family"],
        success=success,
        status="solver_reported_optimum" if success else ("solver_error" if not solver_success else "fatal"),
        metrics={"objective_value": objective_value, "constraint_violation": violation},
        primary_results=primary,
        risk_items=risks,
        limitations=MODEL_SPEC["limitations"],
        failure_reason=failure_reason,
    )


def _empty_primary(solver_key: str, message: str) -> dict[str, Any]:
    return {
        "model_type": MODEL_SPEC["model_id"],
        "solver": solver_key,
        "status": "fatal",
        "success": False,
        "objective_value": None,
        "solution": None,
        "solver_status": "not_run",
        "message": message,
        "constraint_violation": 0.0,  # always float, never None
        "failure_reason": message,
        "diagnostics": {},
    }
