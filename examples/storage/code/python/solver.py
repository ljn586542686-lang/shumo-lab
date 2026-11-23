"""C 题连续 LP backend adapter 与严格字典序编排。

功能：把 ``Q1LinearProgram`` 交给仓库现有连续 LP backend，按三级目标依次
求解，并在每一级后用等式锁定既有最优值。
输入：已经由 ``q1_model_solver`` 构造的 Q1 模型，以及符合本模块协议的 backend。
输出：逐级 solver evidence、最终变量轨迹和独立科学验证结果。
使用：只能由受授权的 ``run_pipeline.py`` 调用；导入本模块不会运行求解器。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Protocol, Sequence

from physical_core_solver import SparseEqualityRow
from q1_model_solver import Q1LinearProgram, build_exact_objective_lock
from scientific_validation import validate_q1_trajectory


SOLVER_EXECUTION_STATUS = "READY_FOR_AUTHORIZED_SOLVER_EXECUTION"
SOLVER_BACKEND = "math_core.optimization.linear_program"
# 与仓库现有 math_core.optimization.linear_program.run 的默认约束违反阈值一致。
REPOSITORY_CONSTRAINT_VIOLATION_TOLERANCE = 1e-7
ACCEPTED_SOLVER_STATUS = "solver_reported_optimum"


@dataclass(frozen=True)
class BackendSolveEvidence:
    """一次 LP backend 调用的规范化证据。"""

    accepted: bool
    status: str
    objective_value: float | None
    solution: tuple[float, ...] | None
    constraint_violation: float | None
    iterations: int | None
    message: str
    failure_reason: str | None = None


class LinearProgramBackend(Protocol):
    """字典序编排器消费的最小 backend 协议。"""

    name: str
    constraint_violation_tolerance: float

    def solve(
        self,
        *,
        objective: Sequence[float],
        equalities: Sequence[SparseEqualityRow],
        bounds: Sequence[tuple[float | None, float | None]],
        run_id: str,
        case_dir: Path | None,
    ) -> BackendSolveEvidence:
        ...


def _dense_row(row: SparseEqualityRow, size: int) -> list[float]:
    values = [0.0] * size
    for index, coefficient in row.coefficients:
        values[index] = float(coefficient)
    return values


class MathCoreLinearProgramBackend:
    """复用仓库 ``math_core`` 连续 LP wrapper；本类不新增求解依赖。"""

    name = SOLVER_BACKEND
    constraint_violation_tolerance = REPOSITORY_CONSTRAINT_VIOLATION_TOLERANCE

    def solve(
        self,
        *,
        objective: Sequence[float],
        equalities: Sequence[SparseEqualityRow],
        bounds: Sequence[tuple[float | None, float | None]],
        run_id: str,
        case_dir: Path | None,
    ) -> BackendSolveEvidence:
        # math_core 内部仅在本方法真正被调用时才导入 scipy.optimize.linprog。
        from math_core.optimization.linear_program import run

        result = run(
            {
                "c": [float(value) for value in objective],
                "A_eq": [_dense_row(row, len(objective)) for row in equalities],
                "b_eq": [float(row.rhs) for row in equalities],
                "bounds": [[lower, upper] for lower, upper in bounds],
                "constraint_violation_tolerance": self.constraint_violation_tolerance,
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
                None if accepted else str(result.get("failure_reason") or primary.get("failure_reason") or "unknown_status")
            ),
        )


def build_solver_neutral_payload(model: Q1LinearProgram) -> dict[str, object]:
    """序列化系数；不导入或调用 backend。"""

    return {
        "model_family": "continuous_linear_programming",
        "variable_domain": model.variable_domain,
        "variable_count": model.layout.size,
        "objective_levels": [
            {
                "level": level.level,
                "name": level.name,
                "coefficients": level.coefficients,
                "rule": level.rule,
            }
            for level in model.objective_levels
        ],
        "equalities": [
            {"name": row.name, "coefficients": row.coefficients, "rhs": row.rhs}
            for row in model.equalities
        ],
        "bounds": [(bound.lower, bound.upper) for bound in model.bounds],
        "solver_backend": SOLVER_BACKEND,
        "solver_execution_status": SOLVER_EXECUTION_STATUS,
    }


def _dot(coefficients: Sequence[float], solution: Sequence[float]) -> float:
    return sum(float(a) * float(b) for a, b in zip(coefficients, solution))


def _variable_block(model: Q1LinearProgram, solution: Sequence[float], name: str) -> tuple[float, ...]:
    return tuple(float(solution[index]) for index in model.layout.block_indices(name))


def _failure(
    *,
    reason: str,
    backend: LinearProgramBackend,
    levels: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "status": "FAIL",
        "reason": reason,
        "backend": backend.name,
        "numerical_tolerance": backend.constraint_violation_tolerance,
        "numerical_tolerance_role": "FEASIBILITY_ONLY_NOT_ECONOMIC_WEIGHT",
        "levels": levels,
        "final_solution": None,
        "scientific_validation": None,
    }


def run_lexicographic_q1(
    model: Q1LinearProgram,
    *,
    backend: LinearProgramBackend | None = None,
    run_id: str = "q1_lexicographic",
    case_dir: Path | None = None,
) -> dict[str, object]:
    """严格按 LEVEL 1→2→3 求解；任何一级失败都立即停止。"""

    selected_backend = backend or MathCoreLinearProgramBackend()
    if (
        not isfinite(float(selected_backend.constraint_violation_tolerance))
        or selected_backend.constraint_violation_tolerance <= 0.0
    ):
        return _failure(reason="BACKEND_NUMERICAL_TOLERANCE_INVALID", backend=selected_backend, levels=[])
    bounds = tuple((bound.lower, bound.upper) for bound in model.bounds)
    locks: list[SparseEqualityRow] = []
    level_records: list[dict[str, object]] = []
    optimum_values: list[float] = []
    final_solution: tuple[float, ...] | None = None

    for level in model.objective_levels:
        active_locks = tuple(locks)
        try:
            evidence = selected_backend.solve(
                objective=level.coefficients,
                equalities=model.equalities + active_locks,
                bounds=bounds,
                run_id=f"{run_id}_level_{level.level}",
                case_dir=case_dir,
            )
        except Exception as exc:
            level_records.append(
                {
                    "level": level.level,
                    "objective": level.name,
                    "solver_status": "backend_error",
                    "accepted": False,
                    "objective_value": None,
                    "iterations": None,
                    "message": str(exc),
                    "failure_reason": "backend_error",
                    "locking_constraints": tuple(row.name for row in active_locks),
                }
            )
            return _failure(
                reason=f"LEVEL{level.level}_BACKEND_ERROR",
                backend=selected_backend,
                levels=level_records,
            )
        level_records.append(
            {
                "level": level.level,
                "objective": level.name,
                "solver_status": evidence.status,
                "accepted": evidence.accepted,
                "objective_value": evidence.objective_value,
                "iterations": evidence.iterations,
                "message": evidence.message,
                "failure_reason": evidence.failure_reason,
                "locking_constraints": tuple(row.name for row in active_locks),
            }
        )
        if (
            not evidence.accepted
            or evidence.status != ACCEPTED_SOLVER_STATUS
            or evidence.solution is None
            or evidence.objective_value is None
            or not isfinite(evidence.objective_value)
            or len(evidence.solution) != model.layout.size
            or not all(isfinite(value) for value in evidence.solution)
            or evidence.constraint_violation is None
            or not isfinite(evidence.constraint_violation)
            or evidence.constraint_violation > selected_backend.constraint_violation_tolerance
        ):
            return _failure(
                reason=f"LEVEL{level.level}_SOLVER_STATUS_NOT_ACCEPTED",
                backend=selected_backend,
                levels=level_records,
            )
        optimum_values.append(evidence.objective_value)
        final_solution = evidence.solution
        if level.level < len(model.objective_levels):
            locks.append(
                build_exact_objective_lock(
                    level.coefficients,
                    evidence.objective_value,
                    f"lock_level_{level.level}",
                )
            )

    if final_solution is None or len(optimum_values) != 3:
        return _failure(reason="LEXICOGRAPHIC_LEVEL_COUNT_INVALID", backend=selected_backend, levels=level_records)

    tolerance = selected_backend.constraint_violation_tolerance
    final_primary = _dot(model.objective_levels[0].coefficients, final_solution)
    final_throughput = _dot(model.objective_levels[1].coefficients, final_solution)
    primary_delta = abs(final_primary - optimum_values[0])
    throughput_delta = abs(final_throughput - optimum_values[1])
    if primary_delta > tolerance:
        return _failure(reason="FINAL_PRIMARY_OBJECTIVE_NOT_PRESERVED", backend=selected_backend, levels=level_records)
    if throughput_delta > tolerance:
        return _failure(reason="FINAL_LEVEL2_OBJECTIVE_NOT_PRESERVED", backend=selected_backend, levels=level_records)

    x = _variable_block(model, final_solution, "x")
    c = _variable_block(model, final_solution, "c")
    d = _variable_block(model, final_solution, "d")
    r = _variable_block(model, final_solution, "r")
    energy = _variable_block(model, final_solution, "E")
    try:
        validation = validate_q1_trajectory(
            grid_purchase_kwh=x,
            charge_kwh=c,
            discharge_kwh=d,
            curtailed_pv_kwh=r,
            energy_path_kwh=energy,
            inputs=model.inputs,
            storage=model.storage,
            grid=model.grid,
            initial_energy_kwh=model.initial_energy_kwh,
            terminal_energy_kwh=model.terminal_energy_kwh,
            tolerance=tolerance,
        )
    except (TypeError, ValueError) as exc:
        failure = _failure(reason="FINAL_SCIENTIFIC_VALIDATION_ERROR", backend=selected_backend, levels=level_records)
        failure["validation_error"] = str(exc)
        return failure
    if validation.get("status") != "PASS":
        return _failure(reason="FINAL_SCIENTIFIC_VALIDATION_FAILED", backend=selected_backend, levels=level_records)

    return {
        "status": "PASS",
        "reason": "LEXICOGRAPHIC_Q1_SOLVED_AND_VALIDATED",
        "backend": selected_backend.name,
        "numerical_tolerance": tolerance,
        "numerical_tolerance_role": "FEASIBILITY_ONLY_NOT_ECONOMIC_WEIGHT",
        "levels": level_records,
        "objective_optima": tuple(optimum_values),
        "final_primary_objective_recomputed": final_primary,
        "final_primary_objective_delta": primary_delta,
        "final_level2_objective_recomputed": final_throughput,
        "final_level2_objective_delta": throughput_delta,
        "final_solution": {
            "x_kwh": x,
            "c_kwh": c,
            "d_kwh": d,
            "r_kwh": r,
            "E_kwh": energy,
        },
        "scientific_validation": validation,
    }
