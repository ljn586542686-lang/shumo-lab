"""Q1 最小连续 LP 的纯构造器。

功能：生成变量、目标、物理等式、bounds 与严格字典序目标接口。
输入：144 个区间的 L、V、p，以及统一储能配置。
输出：solver-neutral ``Q1LinearProgram``；不会调用任何求解器。
使用：未来 SOLVER_EXECUTION 阶段由受授权 adapter 消费该结构；本文件自身不求解。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Sequence

from model_contract import PhysicalInputs, StorageParameters, TimeGrid, VariableLayout
from physical_core_solver import (
    SparseEqualityRow,
    VariableBound,
    build_physical_equalities,
    build_variable_bounds,
)


Q1_INITIAL_ENERGY_KWH = 6_000.0
Q1_TERMINAL_ENERGY_KWH = 6_000.0


@dataclass(frozen=True)
class ObjectiveLevel:
    level: int
    name: str
    coefficients: tuple[float, ...]
    rule: str


@dataclass(frozen=True)
class Q1LinearProgram:
    grid: TimeGrid
    storage: StorageParameters
    inputs: PhysicalInputs
    layout: VariableLayout
    equalities: tuple[SparseEqualityRow, ...]
    bounds: tuple[VariableBound, ...]
    objective_levels: tuple[ObjectiveLevel, ...]
    initial_energy_kwh: float
    terminal_energy_kwh: float
    variable_domain: str = "CONTINUOUS"
    solver_executed: bool = False

    @property
    def primary_objective(self) -> tuple[float, ...]:
        return self.objective_levels[0].coefficients


def _objective_vector(layout: VariableLayout, block: str, values: Sequence[float]) -> tuple[float, ...]:
    if len(values) != layout.n_intervals:
        raise ValueError("OBJECTIVE_VALUE_LENGTH_MISMATCH")
    coefficients = [0.0] * layout.size
    for index, value in zip(layout.block_indices(block), values):
        numeric = float(value)
        if not isfinite(numeric):
            raise ValueError("OBJECTIVE_COEFFICIENT_NONFINITE")
        coefficients[index] = numeric
    return tuple(coefficients)


def build_lexicographic_objectives(
    inputs: PhysicalInputs,
    layout: VariableLayout,
) -> tuple[ObjectiveLevel, ...]:
    """Return separate levels; they must never be collapsed by epsilon weights."""

    ones = (1.0,) * layout.n_intervals
    primary = _objective_vector(layout, "x", inputs.price_per_kwh)
    throughput_c = _objective_vector(layout, "c", ones)
    throughput_d = _objective_vector(layout, "d", ones)
    throughput = tuple(a + b for a, b in zip(throughput_c, throughput_d))
    curtailment = _objective_vector(layout, "r", ones)
    return (
        ObjectiveLevel(1, "economic_grid_purchase_cost", primary, "min sum(p_k*x_k)"),
        ObjectiveLevel(2, "storage_throughput", throughput, "lock level 1 exactly; min sum(c_k+d_k)"),
        ObjectiveLevel(3, "pv_curtailment", curtailment, "lock levels 1-2 exactly; min sum(r_k)"),
    )


def build_exact_objective_lock(
    coefficients: Sequence[float],
    optimum_value: float,
    name: str,
) -> SparseEqualityRow:
    """Represent an exact previous-level objective lock without epsilon weighting."""

    if not isfinite(float(optimum_value)):
        raise ValueError("OBJECTIVE_LOCK_VALUE_NONFINITE")
    sparse = tuple((index, float(value)) for index, value in enumerate(coefficients) if value != 0.0)
    return SparseEqualityRow(name=name, coefficients=sparse, rhs=float(optimum_value))


def build_q1_model(
    load_kwh: Sequence[float],
    pv_available_kwh: Sequence[float],
    price_per_kwh: Sequence[float],
    storage: StorageParameters | None = None,
) -> Q1LinearProgram:
    """Construct the locked 144-interval Q1 LP without solving it."""

    grid = TimeGrid()
    selected_storage = storage or StorageParameters.working_e_a()
    inputs = PhysicalInputs.from_sequences(load_kwh, pv_available_kwh, price_per_kwh)
    inputs.validate(grid.n_intervals)
    layout = VariableLayout(grid.n_intervals)
    equalities = build_physical_equalities(
        inputs,
        grid,
        selected_storage,
        layout,
        Q1_INITIAL_ENERGY_KWH,
    )
    bounds = build_variable_bounds(
        inputs,
        grid,
        selected_storage,
        layout,
        terminal_energy_kwh=Q1_TERMINAL_ENERGY_KWH,
    )
    return Q1LinearProgram(
        grid=grid,
        storage=selected_storage,
        inputs=inputs,
        layout=layout,
        equalities=equalities,
        bounds=bounds,
        objective_levels=build_lexicographic_objectives(inputs, layout),
        initial_energy_kwh=Q1_INITIAL_ENERGY_KWH,
        terminal_energy_kwh=Q1_TERMINAL_ENERGY_KWH,
    )
