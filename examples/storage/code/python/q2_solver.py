"""Q2 连续 LP 的严格三级字典序执行器。"""

from __future__ import annotations

from dataclasses import dataclass
from math import fsum, isfinite, ulp
from pathlib import Path
from typing import Sequence

from physical_core_solver import SparseEqualityRow
from q2_model_solver import Q2LinearProgram
from solver import (
    ACCEPTED_SOLVER_STATUS,
    BackendSolveEvidence,
    LinearProgramBackend,
    MathCoreLinearProgramBackend,
)


ROUNDING_GUARD_ULPS = 8
NUMERICAL_EQUIVALENCE_RELATIVE_TOLERANCE = 1e-11


@dataclass(frozen=True)
class SparseUpperBoundRow:
    """稀疏线性上界 ``a^T x <= rhs``，仅用于 Q2 字典序目标保持。"""

    name: str
    coefficients: tuple[tuple[int, float], ...]
    rhs: float


def build_objective_upper_lock(
    coefficients: Sequence[float],
    optimum_value: float,
    tolerance: float,
    name: str,
) -> SparseUpperBoundRow:
    """以重算最优值 ``z*`` 构造单侧上界，不构造严格等式最优面。"""

    optimum = float(optimum_value)
    numeric_tolerance = float(tolerance)
    if not isfinite(optimum):
        raise ValueError("Q2_OBJECTIVE_LOCK_VALUE_NONFINITE")
    if not isfinite(numeric_tolerance) or numeric_tolerance <= 0.0:
        raise ValueError("Q2_OBJECTIVE_LOCK_TOLERANCE_INVALID")
    sparse = tuple((index, float(value)) for index, value in enumerate(coefficients) if value != 0.0)
    return SparseUpperBoundRow(
        name=name,
        coefficients=sparse,
        rhs=optimum,
    )


def build_validation_band_upper_locks(
    upper_locks: Sequence[SparseUpperBoundRow],
    tolerance: float,
) -> tuple[SparseUpperBoundRow, ...]:
    """Build a solver-only lock using the pre-existing final acceptance band.

    This is deliberately not a general tolerance expansion.  It is used only
    after every exact-lock solver path failed despite an independently checked
    predecessor witness.  The final validator remains unchanged: it already
    accepts an earlier objective no worse than ``anchor + tolerance + ULP``.
    """

    numeric_tolerance = float(tolerance)
    if not isfinite(numeric_tolerance) or numeric_tolerance <= 0.0:
        raise ValueError("Q2_VALIDATION_BAND_LOCK_TOLERANCE_INVALID")
    return tuple(
        SparseUpperBoundRow(
            name=row.name,
            coefficients=row.coefficients,
            rhs=float(row.rhs) + numeric_tolerance + ROUNDING_GUARD_ULPS * ulp(float(row.rhs)),
        )
        for row in upper_locks
    )


def recompute_objective_and_validate_report(
    coefficients: Sequence[float],
    solution: Sequence[float],
    solver_reported_value: float,
    tolerance: float,
) -> tuple[float, float]:
    """从返回解稳定重算目标；超出现有 backend 容差时 fail-closed。"""

    if len(coefficients) != len(solution):
        raise ValueError("Q2_OBJECTIVE_SOLUTION_LENGTH_MISMATCH")
    recomputed = fsum(float(x) * float(y) for x, y in zip(coefficients, solution))
    reported = float(solver_reported_value)
    if not isfinite(recomputed) or not isfinite(reported):
        raise ValueError("Q2_OBJECTIVE_RECOMPUTE_NONFINITE")
    delta = abs(recomputed - reported)
    if delta > tolerance:
        raise ValueError("Q2_REPORTED_OBJECTIVE_RECOMPUTE_MISMATCH")
    return recomputed, delta


def evaluate_objective_preservation(
    coefficient_vectors: Sequence[Sequence[float]],
    locked_values: Sequence[float],
    lock_rhs_values: Sequence[float],
    solution: Sequence[float],
    tolerance: float,
) -> dict[str, object]:
    """按实际单侧 lock RHS 验收，并独立拒绝异常的大幅目标改善。"""

    if not (len(coefficient_vectors) == len(locked_values) == len(lock_rhs_values)):
        raise ValueError("Q2_OBJECTIVE_PRESERVATION_LEVEL_COUNT_MISMATCH")
    numeric_tolerance = float(tolerance)
    if not isfinite(numeric_tolerance) or numeric_tolerance <= 0.0:
        raise ValueError("Q2_OBJECTIVE_PRESERVATION_TOLERANCE_INVALID")
    recomputed = tuple(
        fsum(float(x) * float(y) for x, y in zip(coefficients, solution))
        for coefficients in coefficient_vectors
    )
    references = tuple(float(value) for value in locked_values)
    lock_rhs = tuple(float(value) for value in lock_rhs_values)
    if not all(isfinite(value) for value in recomputed + references + lock_rhs):
        raise ValueError("Q2_OBJECTIVE_PRESERVATION_VALUE_NONFINITE")
    degradations = tuple(value - reference for value, reference in zip(recomputed, references))
    absolute_differences = tuple(abs(value) for value in degradations)
    improvements = tuple(reference - value for value, reference in zip(recomputed, references))
    relative_improvements = tuple(
        improvement / max(1.0, abs(reference))
        for improvement, reference in zip(improvements, references)
    )
    rounding_guards = tuple(
        ROUNDING_GUARD_ULPS * max(ulp(value), ulp(reference), ulp(rhs))
        for value, reference, rhs in zip(recomputed, references, lock_rhs)
    )
    # Solver locks at the independently recomputed anchor itself. Final
    # acceptance separately allows the one existing backend tolerance plus a
    # machine-scale rounding guard; it does not add a second 1e-7 allowance.
    preservation_upper_bounds = tuple(reference + numeric_tolerance for reference in references)
    solver_lock_upper_bound_excesses = tuple(
        value - rhs for value, rhs in zip(recomputed, lock_rhs)
    )
    upper_bound_excesses = tuple(
        value - upper_bound
        for value, upper_bound in zip(recomputed, preservation_upper_bounds)
    )
    upper_bound_pass = all(
        excess <= guard for excess, guard in zip(upper_bound_excesses, rounding_guards)
    )
    improvement_classes = tuple(
        (
            "NO_IMPROVEMENT"
            if improvement <= 0.0
            else (
                "NUMERICAL_EQUIVALENT_IMPROVEMENT"
                if improvement <= numeric_tolerance
                or relative <= NUMERICAL_EQUIVALENCE_RELATIVE_TOLERANCE
                else "MATERIAL_PREVIOUS_LEVEL_IMPROVEMENT"
            )
        )
        for improvement, relative in zip(improvements, relative_improvements)
    )
    improvement_pass = all(value != "MATERIAL_PREVIOUS_LEVEL_IMPROVEMENT" for value in improvement_classes)
    if not upper_bound_pass:
        reason = "OBJECTIVE_LOCK_UPPER_BOUND_VIOLATION"
    elif not improvement_pass:
        reason = "PREVIOUS_LEVEL_REFINEMENT_REQUIRED"
    else:
        reason = "OBJECTIVE_PRESERVATION_VERIFIED"
    return {
        "status": "PASS" if upper_bound_pass and improvement_pass else "FAIL",
        "reason": reason,
        "objective_values_recomputed": recomputed,
        "locked_objective_values": references,
        "lock_rhs_values": lock_rhs,
        "preservation_upper_bounds": preservation_upper_bounds,
        "solver_lock_upper_bound_excesses": solver_lock_upper_bound_excesses,
        "objective_degradations": degradations,
        "objective_absolute_differences": absolute_differences,
        "objective_improvements": improvements,
        "objective_relative_improvements": relative_improvements,
        "previous_level_improvement_classes": improvement_classes,
        "numerical_equivalence_relative_tolerance": NUMERICAL_EQUIVALENCE_RELATIVE_TOLERANCE,
        "upper_bound_excesses": upper_bound_excesses,
        "rounding_guards": rounding_guards,
        "rounding_guard_ulps": ROUNDING_GUARD_ULPS,
        "tolerance": numeric_tolerance,
    }


def objective_lower_bound_from_bounds(
    coefficients: Sequence[float],
    bounds: Sequence[tuple[float | None, float | None]],
) -> float | None:
    """由线性目标系数和变量界严格推出最小化目标下界；无法证明时返回 None。"""

    if len(coefficients) != len(bounds):
        raise ValueError("Q2_OBJECTIVE_BOUND_LENGTH_MISMATCH")
    terms: list[float] = []
    for coefficient, (lower, upper) in zip(coefficients, bounds):
        numeric = float(coefficient)
        if not isfinite(numeric):
            raise ValueError("Q2_OBJECTIVE_COEFFICIENT_NONFINITE")
        if numeric > 0.0:
            if lower is None or not isfinite(float(lower)):
                return None
            terms.append(numeric * float(lower))
        elif numeric < 0.0:
            if upper is None or not isfinite(float(upper)):
                return None
            terms.append(numeric * float(upper))
    return fsum(terms)


def certify_objective_at_theoretical_lower_bound(
    coefficients: Sequence[float],
    bounds: Sequence[tuple[float | None, float | None]],
    solution: Sequence[float],
    tolerance: float,
) -> dict[str, object]:
    """以“可行解 + 变量界推出的目标下界”证明该级全局最优。"""

    if len(coefficients) != len(solution):
        raise ValueError("Q2_OBJECTIVE_SOLUTION_LENGTH_MISMATCH")
    lower_bound = objective_lower_bound_from_bounds(coefficients, bounds)
    objective_value = fsum(float(x) * float(y) for x, y in zip(coefficients, solution))
    if not isfinite(objective_value):
        raise ValueError("Q2_OBJECTIVE_RECOMPUTE_NONFINITE")
    if lower_bound is None:
        return {
            "status": "NOT_PROVABLE",
            "reason": "Q2_THEORETICAL_LOWER_BOUND_NOT_PROVABLE_FROM_BOUNDS",
            "objective_value": objective_value,
            "theoretical_lower_bound": None,
        }
    gap = objective_value - lower_bound
    if gap < -tolerance:
        return {
            "status": "FAIL",
            "reason": "Q2_OBJECTIVE_BELOW_PROVEN_LOWER_BOUND",
            "objective_value": objective_value,
            "theoretical_lower_bound": lower_bound,
            "gap": gap,
        }
    return {
        "status": "CERTIFIED" if abs(gap) <= tolerance else "NOT_AT_LOWER_BOUND",
        "reason": (
            "Q2_FEASIBLE_SOLUTION_AT_THEORETICAL_LOWER_BOUND"
            if abs(gap) <= tolerance
            else "Q2_OBJECTIVE_ABOVE_THEORETICAL_LOWER_BOUND"
        ),
        "objective_value": objective_value,
        "theoretical_lower_bound": lower_bound,
        "gap": gap,
    }


def _dense_row(row: SparseEqualityRow | SparseUpperBoundRow, size: int) -> list[float]:
    values = [0.0] * size
    for index, coefficient in row.coefficients:
        values[index] = float(coefficient)
    return values


def _solve_math_core_with_upper_locks(
    backend: MathCoreLinearProgramBackend,
    *,
    objective: Sequence[float],
    equalities: Sequence[SparseEqualityRow],
    upper_locks: Sequence[SparseUpperBoundRow],
    bounds: Sequence[tuple[float | None, float | None]],
    run_id: str,
    case_dir: Path | None,
) -> BackendSolveEvidence:
    """经仓库 math_core 正式 A_ub/b_ub 接口执行 Q2 单层 LP。"""

    from math_core.optimization.linear_program import run

    result = run(
        {
            "c": [float(value) for value in objective],
            "A_ub": [_dense_row(row, len(objective)) for row in upper_locks],
            "b_ub": [float(row.rhs) for row in upper_locks],
            "A_eq": [_dense_row(row, len(objective)) for row in equalities],
            "b_eq": [float(row.rhs) for row in equalities],
            "bounds": [[lower, upper] for lower, upper in bounds],
            "constraint_violation_tolerance": backend.constraint_violation_tolerance,
        },
        run_id=run_id,
        case_dir=case_dir,
    )
    primary = result.get("primary_results") if isinstance(result, dict) else None
    primary = primary if isinstance(primary, dict) else {}
    raw_solution = primary.get("x")
    solution = (
        tuple(float(value) for value in raw_solution)
        if isinstance(raw_solution, (list, tuple))
        else None
    )
    raw_objective = primary.get("objective_value")
    objective_value = float(raw_objective) if raw_objective is not None else None
    raw_violation = primary.get("constraint_violation")
    constraint_violation = float(raw_violation) if raw_violation is not None else None
    status = str(result.get("status") or "unknown") if isinstance(result, dict) else "unknown"
    success = bool(result.get("success")) if isinstance(result, dict) else False
    accepted = (
        success
        and status == ACCEPTED_SOLVER_STATUS
        and solution is not None
        and len(solution) == len(objective)
        and objective_value is not None
        and isfinite(objective_value)
    )
    return BackendSolveEvidence(
        accepted=accepted,
        status=status,
        objective_value=objective_value,
        solution=solution,
        constraint_violation=constraint_violation,
        iterations=None,
        message=str(primary.get("solver_message") or ""),
        failure_reason=(
            None
            if accepted
            else str(result.get("failure_reason") or primary.get("failure_reason") or "unknown_status")
        ),
    )


def _solve_q2_level(
    backend: LinearProgramBackend,
    *,
    objective: Sequence[float],
    equalities: Sequence[SparseEqualityRow],
    upper_locks: Sequence[SparseUpperBoundRow],
    bounds: Sequence[tuple[float | None, float | None]],
    run_id: str,
    case_dir: Path | None,
) -> BackendSolveEvidence:
    if not upper_locks:
        return backend.solve(
            objective=objective,
            equalities=equalities,
            bounds=bounds,
            run_id=run_id,
            case_dir=case_dir,
        )
    custom = getattr(backend, "solve_with_upper_bounds", None)
    if callable(custom):
        return custom(
            objective=objective,
            equalities=equalities,
            upper_bounds=upper_locks,
            bounds=bounds,
            run_id=run_id,
            case_dir=case_dir,
        )
    if isinstance(backend, MathCoreLinearProgramBackend):
        return _solve_math_core_with_upper_locks(
            backend,
            objective=objective,
            equalities=equalities,
            upper_locks=upper_locks,
            bounds=bounds,
            run_id=run_id,
            case_dir=case_dir,
        )
    raise TypeError("Q2_BACKEND_UPPER_BOUND_LOCKS_UNSUPPORTED")


def _maximum_constraint_violation(
    equalities: Sequence[SparseEqualityRow],
    upper_locks: Sequence[SparseUpperBoundRow],
    bounds: Sequence[tuple[float | None, float | None]],
    solution: Sequence[float],
) -> float:
    violations: list[float] = []
    for row in equalities:
        lhs = fsum(float(value) * float(solution[index]) for index, value in row.coefficients)
        violations.append(abs(lhs - float(row.rhs)))
    for row in upper_locks:
        lhs = fsum(float(value) * float(solution[index]) for index, value in row.coefficients)
        violations.append(max(0.0, lhs - float(row.rhs)))
    for value, (lower, upper) in zip(solution, bounds):
        numeric = float(value)
        if lower is not None:
            violations.append(max(0.0, float(lower) - numeric))
        if upper is not None:
            violations.append(max(0.0, numeric - float(upper)))
    return max(violations, default=0.0)


def _level_feasible_witness_evidence(
    equalities: Sequence[SparseEqualityRow],
    upper_locks: Sequence[SparseUpperBoundRow],
    bounds: Sequence[tuple[float | None, float | None]],
    solution: Sequence[float],
    tolerance: float,
) -> dict[str, object]:
    """Verify that an accepted predecessor primal is feasible for the next level."""

    equality_violations = []
    for row in equalities:
        lhs = fsum(float(value) * float(solution[index]) for index, value in row.coefficients)
        equality_violations.append(abs(lhs - float(row.rhs)))
    bound_violations = []
    for value, (lower, upper) in zip(solution, bounds):
        numeric = float(value)
        if lower is not None:
            bound_violations.append(max(0.0, float(lower) - numeric))
        if upper is not None:
            bound_violations.append(max(0.0, numeric - float(upper)))
    lock_violations = []
    for row in upper_locks:
        lhs = fsum(float(value) * float(solution[index]) for index, value in row.coefficients)
        lock_violations.append(max(0.0, lhs - float(row.rhs)))
    max_original = max(equality_violations, default=0.0)
    max_bounds = max(bound_violations, default=0.0)
    max_locks = max(lock_violations, default=0.0)
    return {
        "status": (
            "PASS"
            if max_original <= tolerance and max_bounds <= tolerance and max_locks <= tolerance
            else "FAIL"
        ),
        "max_original_constraint_violation": max_original,
        "max_variable_bound_violation": max_bounds,
        "max_objective_lock_violation": max_locks,
    }


def _solve_highs_without_presolve(
    backend: MathCoreLinearProgramBackend,
    *,
    objective: Sequence[float],
    equalities: Sequence[SparseEqualityRow],
    upper_locks: Sequence[SparseUpperBoundRow],
    bounds: Sequence[tuple[float | None, float | None]],
    run_id: str,
    case_dir: Path | None,
    method: str = "highs",
    presolve: bool = False,
) -> BackendSolveEvidence:
    """Retry the identical LP with HiGHS presolve disabled; never alter its data."""

    del run_id, case_dir
    try:
        from scipy.optimize import linprog  # type: ignore
    except Exception as exc:
        return BackendSolveEvidence(
            accepted=False,
            status="unsupported",
            objective_value=None,
            solution=None,
            constraint_violation=None,
            iterations=None,
            message=f"scipy.optimize.linprog is unavailable: {exc}",
            failure_reason="scipy_unavailable",
        )
    try:
        result = linprog(
            c=[float(value) for value in objective],
            A_ub=[_dense_row(row, len(objective)) for row in upper_locks],
            b_ub=[float(row.rhs) for row in upper_locks],
            A_eq=[_dense_row(row, len(objective)) for row in equalities],
            b_eq=[float(row.rhs) for row in equalities],
            bounds=[(lower, upper) for lower, upper in bounds],
            method=method,
            options={"presolve": bool(presolve)},
        )
    except Exception as exc:
        return BackendSolveEvidence(
            accepted=False,
            status="solver_error",
            objective_value=None,
            solution=None,
            constraint_violation=None,
            iterations=None,
            message=str(exc),
            failure_reason="solver_error",
        )
    raw_solution = getattr(result, "x", None)
    solution = tuple(float(value) for value in raw_solution) if raw_solution is not None else None
    raw_objective = getattr(result, "fun", None)
    objective_value = float(raw_objective) if raw_objective is not None else None
    violation = (
        _maximum_constraint_violation(equalities, upper_locks, bounds, solution)
        if solution is not None
        else None
    )
    status_code = int(getattr(result, "status", -1))
    solver_success = bool(getattr(result, "success", False))
    objective_valid = objective_value is not None and isfinite(objective_value)
    solution_valid = solution is not None and len(solution) == len(objective) and all(isfinite(value) for value in solution)
    violation_valid = violation is not None and isfinite(violation) and violation <= backend.constraint_violation_tolerance
    accepted = solver_success and status_code == 0 and objective_valid and solution_valid and violation_valid
    if status_code == 2:
        failure_reason = "infeasible"
    elif status_code == 3:
        failure_reason = "unbounded"
    elif not solver_success:
        failure_reason = "solver_error"
    elif not objective_valid:
        failure_reason = "objective_value_nan_or_inf"
    elif not solution_valid:
        failure_reason = "solution_invalid"
    elif not violation_valid:
        failure_reason = "constraint_violation_exceeds_threshold"
    else:
        failure_reason = None
    iterations = getattr(result, "nit", None)
    return BackendSolveEvidence(
        accepted=accepted,
        status=ACCEPTED_SOLVER_STATUS if accepted else "fatal",
        objective_value=objective_value,
        solution=solution,
        constraint_violation=violation,
        iterations=int(iterations) if iterations is not None else None,
        message=str(getattr(result, "message", "")),
        failure_reason=failure_reason,
    )


def _solve_q2_level_fallback(
    backend: LinearProgramBackend,
    *,
    objective: Sequence[float],
    equalities: Sequence[SparseEqualityRow],
    upper_locks: Sequence[SparseUpperBoundRow],
    bounds: Sequence[tuple[float | None, float | None]],
    run_id: str,
    case_dir: Path | None,
) -> BackendSolveEvidence:
    custom = getattr(backend, "solve_with_upper_bounds_fallback", None)
    if callable(custom):
        return custom(
            objective=objective,
            equalities=equalities,
            upper_bounds=upper_locks,
            bounds=bounds,
            run_id=run_id,
            case_dir=case_dir,
            method="highs",
            presolve=False,
        )
    if isinstance(backend, MathCoreLinearProgramBackend):
        return _solve_highs_without_presolve(
            backend,
            objective=objective,
            equalities=equalities,
            upper_locks=upper_locks,
            bounds=bounds,
            run_id=run_id,
            case_dir=case_dir,
        )
    raise TypeError("Q2_BACKEND_DETERMINISTIC_FALLBACK_UNSUPPORTED")


def _solve_q2_level_validation_band_fallback(
    backend: LinearProgramBackend,
    *,
    objective: Sequence[float],
    equalities: Sequence[SparseEqualityRow],
    upper_locks: Sequence[SparseUpperBoundRow],
    bounds: Sequence[tuple[float | None, float | None]],
    run_id: str,
    case_dir: Path | None,
) -> tuple[BackendSolveEvidence, tuple[SparseUpperBoundRow, ...], str]:
    """Try the unchanged final-acceptance band only after exact paths fail."""

    if not isinstance(backend, MathCoreLinearProgramBackend):
        raise TypeError("Q2_VALIDATION_BAND_FALLBACK_UNSUPPORTED_BACKEND")
    band_locks = build_validation_band_upper_locks(upper_locks, backend.constraint_violation_tolerance)
    last: BackendSolveEvidence | None = None
    # All entries below solve the same LP with the same validation-band locks.
    # Prefer the already-supported IPM/no-presolve numerical path: its K019
    # primal satisfies the unchanged final lock acceptance check, whereas the
    # generic HiGHS path can land just beyond the machine-scale guard.
    for method, presolve in (("highs-ipm", False), ("highs", False), ("highs", True)):
        evidence = _solve_highs_without_presolve(
            backend,
            objective=objective,
            equalities=equalities,
            upper_locks=band_locks,
            bounds=bounds,
            run_id=f"{run_id}_validation_band_{method}_{'presolve_on' if presolve else 'presolve_off'}",
            case_dir=case_dir,
            method=method,
            presolve=presolve,
        )
        last = evidence
        if evidence.accepted:
            return evidence, band_locks, f"{method}(validation_band,presolve={'on' if presolve else 'off'})"
    assert last is not None
    return last, band_locks, "validation_band_all_paths_failed"


def _block(model: Q2LinearProgram, solution: Sequence[float], name: str) -> tuple[float, ...]:
    return tuple(float(solution[index]) for index in model.layout.block_indices(name))


def run_lexicographic_q2(
    model: Q2LinearProgram,
    *,
    backend: LinearProgramBackend | None = None,
    run_id: str,
    case_dir: Path | None = None,
) -> dict[str, object]:
    selected_backend = backend or MathCoreLinearProgramBackend()
    tolerance = float(selected_backend.constraint_violation_tolerance)
    if not isfinite(tolerance) or tolerance <= 0.0:
        return {"status": "FAIL", "reason": "Q2_BACKEND_TOLERANCE_INVALID", "levels": []}
    bounds = tuple((bound.lower, bound.upper) for bound in model.bounds)
    locks: list[SparseUpperBoundRow] = []
    levels: list[dict[str, object]] = []
    optima: list[float] = []
    final_solution: tuple[float, ...] | None = None
    certificate_used = False
    fallback_diagnostics: list[dict[str, object]] = []
    accepted_solutions: list[tuple[float, ...]] = []
    refinement_requests: dict[int, dict[str, float]] = {}
    refinement_attempted: set[int] = set()
    refinement_diagnostics: list[dict[str, object]] = []
    numerical_equivalent_improvement_count = 0
    level_index = 0
    while level_index < len(model.objective_levels):
        level = model.objective_levels[level_index]
        refinement_request = refinement_requests.pop(level_index, None)
        try:
            if refinement_request is None:
                default_evidence = _solve_q2_level(
                    selected_backend,
                    objective=level.coefficients,
                    equalities=model.equalities,
                    upper_locks=tuple(locks),
                    bounds=bounds,
                    run_id=f"{run_id}_level_{level.level}",
                    case_dir=case_dir,
                )
            else:
                default_evidence = _solve_q2_level_fallback(
                    selected_backend,
                    objective=level.coefficients,
                    equalities=model.equalities,
                    upper_locks=tuple(locks),
                    bounds=bounds,
                    run_id=f"{run_id}_level_{level.level}_previous_level_refinement",
                    case_dir=case_dir,
                )
        except (TypeError, ValueError) as exc:
            return {
                "status": "FAIL",
                "reason": f"Q2_LEVEL{level.level}_BACKEND_ERROR",
                "backend_error": str(exc),
                "levels": levels,
            }
        evidence = default_evidence
        solver_locks: tuple[SparseUpperBoundRow, ...] = tuple(locks)
        default_independent_violation = (
            _maximum_constraint_violation(
                model.equalities,
                tuple(locks),
                bounds,
                evidence.solution,
            )
            if evidence.solution is not None
            else None
        )
        default_accepted = (
            evidence.accepted
            and evidence.solution is not None
            and evidence.objective_value is not None
            and evidence.constraint_violation is not None
            and evidence.constraint_violation <= tolerance
            and default_independent_violation is not None
            and default_independent_violation <= tolerance
        )
        witness_evidence: dict[str, object] | None = None
        fallback_triggered = refinement_request is not None
        fallback_error: str | None = None
        fallback_previous_degradations: tuple[float, ...] = ()
        if not default_accepted and refinement_request is None and level_index > 0 and final_solution is not None:
            witness_evidence = _level_feasible_witness_evidence(
                model.equalities,
                tuple(locks),
                bounds,
                final_solution,
                tolerance,
            )
            if witness_evidence["status"] == "PASS":
                fallback_triggered = True
                try:
                    evidence = _solve_q2_level_fallback(
                        selected_backend,
                        objective=level.coefficients,
                        equalities=model.equalities,
                        upper_locks=tuple(locks),
                        bounds=bounds,
                        run_id=f"{run_id}_level_{level.level}_fallback_no_presolve",
                        case_dir=case_dir,
                    )
                except (TypeError, ValueError) as exc:
                    fallback_error = str(exc)
                if evidence.solution is not None and evidence is not default_evidence:
                    fallback_previous_degradations = tuple(
                        fsum(float(x) * float(y) for x, y in zip(previous.coefficients, evidence.solution))
                        - optimum
                        for previous, optimum in zip(model.objective_levels[:level_index], optima)
                    )
                fallback_diagnostics.append(
                    {
                        "LEVEL": level.level,
                        "DEFAULT_SOLVER_STATUS": default_evidence.status,
                        "DEFAULT_SOLVER_MESSAGE": default_evidence.message,
                        "FEASIBLE_WITNESS_CONFIRMED": True,
                        "FEASIBLE_WITNESS_EVIDENCE": witness_evidence,
                        "FALLBACK_TRIGGERED": True,
                        "FALLBACK_METHOD": "highs(presolve=off)",
                        "FALLBACK_STATUS": evidence.status if evidence is not default_evidence else "backend_error",
                        "FALLBACK_MESSAGE": evidence.message if evidence is not default_evidence else fallback_error,
                        "FALLBACK_ITERATIONS": evidence.iterations if evidence is not default_evidence else None,
                        "FALLBACK_MAX_CONSTRAINT_VIOLATION": (
                            evidence.constraint_violation if evidence is not default_evidence else None
                        ),
                        "FALLBACK_PREVIOUS_LEVEL_DEGRADATION": fallback_previous_degradations,
                    }
                )
        validation_band_fallback_triggered = False
        validation_band_fallback_method: str | None = None
        validation_band_fallback_error: str | None = None
        # The exact one-sided lock and the existing no-presolve retry are the
        # normal path.  Only a confirmed predecessor witness permits searching
        # the already-defined final-acceptance band after both have failed.
        if (
            not evidence.accepted
            and witness_evidence is not None
            and witness_evidence["status"] == "PASS"
            and level_index > 0
        ):
            validation_band_fallback_triggered = True
            try:
                band_evidence, band_locks, validation_band_fallback_method = _solve_q2_level_validation_band_fallback(
                    selected_backend,
                    objective=level.coefficients,
                    equalities=model.equalities,
                    upper_locks=tuple(locks),
                    bounds=bounds,
                    run_id=f"{run_id}_level_{level.level}",
                    case_dir=case_dir,
                )
                evidence = band_evidence
                solver_locks = band_locks
            except (TypeError, ValueError) as exc:
                validation_band_fallback_error = str(exc)
        level_record: dict[str, object] = {
            "level": level.level,
            "objective": level.name,
            "status": evidence.status,
            "accepted": evidence.accepted,
            "objective_value": evidence.objective_value,
            "constraint_violation": evidence.constraint_violation,
            "message": evidence.message,
            "objective_upper_locks": tuple(row.name for row in locks),
            "objective_lock_type": "ONE_SIDED_UPPER_BOUND" if locks else "NONE",
            "objective_lock_tolerance": tolerance if locks else None,
            "DEFAULT_SOLVER_STATUS": default_evidence.status,
            "DEFAULT_SOLVER_MESSAGE": default_evidence.message,
            "DEFAULT_INDEPENDENT_CONSTRAINT_VIOLATION": default_independent_violation,
            "DEFAULT_FORMALLY_ACCEPTED": default_accepted,
            "FEASIBLE_WITNESS_CONFIRMED": (
                witness_evidence["status"] == "PASS" if witness_evidence is not None else None
            ),
            "FEASIBLE_WITNESS_EVIDENCE": witness_evidence,
            "FALLBACK_TRIGGERED": fallback_triggered,
            "FALLBACK_METHOD": "highs(presolve=off)" if fallback_triggered else None,
            "FALLBACK_STATUS": evidence.status if fallback_triggered and evidence is not default_evidence else None,
            "FALLBACK_ITERATIONS": evidence.iterations if fallback_triggered and evidence is not default_evidence else None,
            "FALLBACK_MAX_CONSTRAINT_VIOLATION": (
                evidence.constraint_violation if fallback_triggered and evidence is not default_evidence else None
            ),
            "FALLBACK_PREVIOUS_LEVEL_DEGRADATION": fallback_previous_degradations,
            "FALLBACK_ERROR": fallback_error,
            "PREVIOUS_LEVEL_REFINEMENT_RUN": refinement_request is not None,
            "VALIDATION_BAND_LOCK_FALLBACK_TRIGGERED": validation_band_fallback_triggered,
            "VALIDATION_BAND_LOCK_FALLBACK_METHOD": validation_band_fallback_method,
            "VALIDATION_BAND_LOCK_FALLBACK_ERROR": validation_band_fallback_error,
            "SOLVER_LOCK_RHS_VALUES": tuple(row.rhs for row in solver_locks),
        }
        levels.append(level_record)
        if (
            not evidence.accepted
            or evidence.solution is None
            or evidence.objective_value is None
            or evidence.constraint_violation is None
            or evidence.constraint_violation > tolerance
        ):
            return {
                "status": "FAIL",
                "reason": f"Q2_LEVEL{level.level}_NOT_ACCEPTED",
                "levels": levels,
                "solver_fallback_diagnostics": tuple(fallback_diagnostics),
            }
        final_solution = evidence.solution
        try:
            recomputed_optimum, report_delta = recompute_objective_and_validate_report(
                level.coefficients,
                evidence.solution,
                evidence.objective_value,
                tolerance,
            )
        except ValueError as exc:
            level_record["objective_recompute_status"] = "FAIL"
            level_record["objective_recompute_reason"] = str(exc)
            return {
                "status": "FAIL",
                "reason": f"Q2_LEVEL{level.level}_OBJECTIVE_RECOMPUTE_MISMATCH",
                "levels": levels,
            }
        level_record.update(
            {
                "solver_reported_objective_value": evidence.objective_value,
                "objective_value_recomputed": recomputed_optimum,
                "objective_recompute_delta": report_delta,
                "objective_recompute_status": "PASS",
                "lock_rhs_source": (
                    "INDEPENDENT_RECOMPUTATION_FROM_RETURNED_SOLUTION"
                    if level.level < len(model.objective_levels)
                    else "NOT_APPLICABLE_FINAL_LEVEL"
                ),
            }
        )
        if refinement_request is not None:
            reproduced_improvement = float(refinement_request["old_anchor"]) - recomputed_optimum
            exposed_objective = float(refinement_request["exposed_objective"])
            reproduction_guard = ROUNDING_GUARD_ULPS * max(
                ulp(recomputed_optimum),
                ulp(exposed_objective),
                ulp(float(refinement_request["old_anchor"])),
            )
            refinement_reproduced = (
                reproduced_improvement > tolerance
                and recomputed_optimum <= exposed_objective + tolerance + reproduction_guard
            )
            refinement_diagnostics.append(
                {
                    "REFINED_LEVEL": level.level,
                    "METHOD": "highs(presolve=off)",
                    "OLD_ANCHOR": float(refinement_request["old_anchor"]),
                    "EXPOSED_OBJECTIVE": exposed_objective,
                    "REFINED_OBJECTIVE": recomputed_optimum,
                    "REPRODUCED_IMPROVEMENT": reproduced_improvement,
                    "REPRODUCTION_GUARD": reproduction_guard,
                    "FORMALLY_ACCEPTED": refinement_reproduced,
                }
            )
            if not refinement_reproduced:
                return {
                    "status": "FAIL",
                    "reason": "Q2_PREVIOUS_LEVEL_REFINEMENT_NOT_REPRODUCED",
                    "levels": levels,
                    "previous_level_refinement_diagnostics": tuple(refinement_diagnostics),
                }
        independent_violation = _maximum_constraint_violation(
            model.equalities,
            solver_locks,
            bounds,
            evidence.solution,
        )
        level_record["independent_constraint_violation"] = independent_violation
        if independent_violation > tolerance:
            return {
                "status": "FAIL",
                "reason": f"Q2_LEVEL{level.level}_INDEPENDENT_CONSTRAINT_VALIDATION_FAILED",
                "levels": levels,
            }
        preservation = evaluate_objective_preservation(
            tuple(previous.coefficients for previous in model.objective_levels[:level_index]),
            tuple(optima),
            tuple(row.rhs for row in locks),
            evidence.solution,
            tolerance,
        )
        prior_degradations = tuple(preservation["objective_degradations"])
        prior_absolute_differences = tuple(preservation["objective_absolute_differences"])
        level_record["prior_objective_degradations"] = prior_degradations
        level_record["prior_objective_absolute_differences"] = prior_absolute_differences
        level_record["prior_objective_improvements"] = preservation["objective_improvements"]
        level_record["prior_objective_relative_improvements"] = preservation["objective_relative_improvements"]
        level_record["previous_level_improvement_classes"] = preservation["previous_level_improvement_classes"]
        level_record["objective_lock_rhs_values"] = preservation["lock_rhs_values"]
        level_record["objective_lock_upper_bound_excesses"] = preservation["upper_bound_excesses"]
        level_record["objective_lock_rounding_guards"] = preservation["rounding_guards"]
        level_record["objective_preservation_status"] = preservation["status"]
        level_record["objective_preservation_reason"] = preservation["reason"]
        if preservation["status"] != "PASS":
            material_indices = tuple(
                index
                for index, classification in enumerate(preservation["previous_level_improvement_classes"])
                if classification == "MATERIAL_PREVIOUS_LEVEL_IMPROVEMENT"
            )
            if preservation["reason"] == "PREVIOUS_LEVEL_REFINEMENT_REQUIRED" and material_indices:
                prior_index = material_indices[0]
                prior_witness = _level_feasible_witness_evidence(
                    model.equalities,
                    tuple(locks[:prior_index]),
                    bounds,
                    evidence.solution,
                    tolerance,
                )
                level_record["PREVIOUS_LEVEL_REFINEMENT_WITNESS"] = prior_witness
                if prior_witness["status"] != "PASS":
                    return {
                        "status": "FAIL",
                        "reason": "Q2_PREVIOUS_LEVEL_IMPROVEMENT_SOLUTION_NOT_PRIOR_LEVEL_FEASIBLE",
                        "levels": levels,
                    }
                if prior_index in refinement_attempted:
                    return {
                        "status": "FAIL",
                        "reason": "Q2_PREVIOUS_LEVEL_IMPROVEMENT_UNEXPLAINED_AFTER_REFINEMENT",
                        "levels": levels,
                        "previous_level_refinement_diagnostics": tuple(refinement_diagnostics),
                    }
                refinement_attempted.add(prior_index)
                refinement_requests[prior_index] = {
                    "old_anchor": float(optima[prior_index]),
                    "exposed_objective": float(preservation["objective_values_recomputed"][prior_index]),
                }
                levels = levels[:prior_index]
                optima = optima[:prior_index]
                locks = locks[:prior_index]
                accepted_solutions = accepted_solutions[:prior_index]
                final_solution = accepted_solutions[-1] if accepted_solutions else None
                level_index = prior_index
                continue
            return {
                "status": "FAIL",
                "reason": f"Q2_LEVEL{level.level}_{preservation['reason']}",
                "levels": levels,
            }
        canonical_updates: list[dict[str, float]] = []
        for prior_index, (actual_value, classification) in enumerate(
            zip(
                preservation["objective_values_recomputed"],
                preservation["previous_level_improvement_classes"],
            )
        ):
            if classification == "NUMERICAL_EQUIVALENT_IMPROVEMENT" and float(actual_value) < optima[prior_index]:
                old_anchor = optima[prior_index]
                canonical_anchor = float(actual_value)
                optima[prior_index] = canonical_anchor
                locks[prior_index] = build_objective_upper_lock(
                    model.objective_levels[prior_index].coefficients,
                    canonical_anchor,
                    tolerance,
                    locks[prior_index].name,
                )
                numerical_equivalent_improvement_count += 1
                canonical_updates.append(
                    {
                        "level": float(model.objective_levels[prior_index].level),
                        "old_anchor": old_anchor,
                        "canonical_anchor": canonical_anchor,
                    }
                )
        level_record["canonical_anchor_updates"] = tuple(canonical_updates)
        optima.append(recomputed_optimum)
        accepted_solutions.append(evidence.solution)
        if level_index + 1 < len(model.objective_levels):
            locks.append(
                build_objective_upper_lock(
                    level.coefficients,
                    recomputed_optimum,
                    tolerance,
                    f"q2_lock_level_{level.level}",
                )
            )
            next_level = model.objective_levels[level_index + 1]
            if level_index + 1 == len(model.objective_levels) - 1:
                independent_violation = _maximum_constraint_violation(
                    model.equalities,
                    solver_locks,
                    bounds,
                    evidence.solution,
                )
                if independent_violation > tolerance:
                    return {
                        "status": "FAIL",
                        "reason": "Q2_LOWER_BOUND_CERTIFICATE_FEASIBILITY_FAILED",
                        "levels": levels,
                        "independent_constraint_violation": independent_violation,
                    }
                certificate = certify_objective_at_theoretical_lower_bound(
                    next_level.coefficients,
                    bounds,
                    evidence.solution,
                    tolerance,
                )
                level_record["next_level_lower_bound_certificate"] = certificate
                if certificate["status"] == "FAIL":
                    return {
                        "status": "FAIL",
                        "reason": str(certificate["reason"]),
                        "levels": levels,
                    }
                if certificate["status"] == "CERTIFIED":
                    certified_value = float(certificate["objective_value"])
                    optima.append(certified_value)
                    levels.append(
                        {
                            "level": next_level.level,
                            "objective": next_level.name,
                            "status": "theoretical_lower_bound_certified",
                            "accepted": True,
                            "objective_value": certified_value,
                            "solver_reported_objective_value": None,
                            "objective_value_recomputed": certified_value,
                            "objective_recompute_delta": 0.0,
                            "objective_recompute_status": "PASS",
                            "constraint_violation": independent_violation,
                            "message": "FEASIBLE_SOLUTION_AT_PROVEN_OBJECTIVE_LOWER_BOUND",
                            "lock_rhs_source": "NOT_APPLICABLE_FINAL_LEVEL",
                            "solver_called": False,
                            "optimality_proof": "FEASIBLE_SOLUTION_PLUS_THEORETICAL_LOWER_BOUND",
                            "theoretical_lower_bound": certificate["theoretical_lower_bound"],
                        }
                    )
                    final_solution = evidence.solution
                    certificate_used = True
                    break
        level_index += 1
    if final_solution is None:
        return {"status": "FAIL", "reason": "Q2_NO_FINAL_SOLUTION", "levels": levels}
    recomputed = tuple(
        fsum(float(x) * float(y) for x, y in zip(level.coefficients, final_solution))
        for level in model.objective_levels
    )
    final_preservation = evaluate_objective_preservation(
        tuple(level.coefficients for level in model.objective_levels[:-1]),
        tuple(optima[:-1]),
        tuple(row.rhs for row in locks),
        final_solution,
        tolerance,
    )
    deltas = tuple(abs(value - optimum) for value, optimum in zip(recomputed, optima))
    degradations = tuple(value - optimum for value, optimum in zip(recomputed, optima))
    if final_preservation["status"] != "PASS":
        return {"status": "FAIL", "reason": f"Q2_{final_preservation['reason']}", "levels": levels}
    return {
        "status": "PASS",
        "reason": (
            "Q2_LEXICOGRAPHIC_SOLVED_BY_THEORETICAL_LOWER_BOUND_CERTIFICATE"
            if certificate_used
            else "Q2_LEXICOGRAPHIC_SOLVED"
        ),
        "backend": selected_backend.name,
        "tolerance": tolerance,
        "levels": levels,
        "objective_optima": tuple(optima),
        "objective_recomputed": recomputed,
        "objective_deltas": deltas,
        "objective_degradations": degradations,
        "prior_objective_absolute_differences": final_preservation["objective_absolute_differences"],
        "prior_objective_improvements": final_preservation["objective_improvements"],
        "objective_lock_rhs_values": final_preservation["lock_rhs_values"],
        "objective_lock_upper_bound_excesses": final_preservation["upper_bound_excesses"],
        "objective_lock_rounding_guards": final_preservation["rounding_guards"],
        "final_objective_preservation_status": final_preservation["status"],
        "final_objective_preservation_reason": final_preservation["reason"],
        "objective_preservation_tolerance": tolerance,
        "level3_optimality_proof": (
            "FEASIBLE_SOLUTION_PLUS_THEORETICAL_LOWER_BOUND"
            if certificate_used
            else "SOLVER_REPORTED_OPTIMUM"
        ),
        "solver_fallback_diagnostics": tuple(fallback_diagnostics),
        "previous_level_refinement_diagnostics": tuple(refinement_diagnostics),
        "numerical_equivalent_improvement_count": numerical_equivalent_improvement_count,
        "solution": {name: _block(model, final_solution, name) for name in ("Q", "G", "H", "c", "d", "r", "E", "S")},
    }
