"""C 题公共物理调度内核（solver-neutral）。

功能：构造逐时段供需平衡、SOC 递推和变量上下界。
输入：统一区间电量、时间网格、储能参数和初始储电量。
输出：稀疏等式行与变量 bounds；不导入、不调用优化求解器。
使用：由 Q1 构造器及后续 Q2-Q4 物理层复用；文件名遵守 model authority 的 ``*_solver.py`` 绑定规则。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from model_contract import PhysicalInputs, StorageParameters, TimeGrid, VariableLayout


@dataclass(frozen=True)
class SparseEqualityRow:
    name: str
    coefficients: tuple[tuple[int, float], ...]
    rhs: float

    def coefficient(self, index: int) -> float:
        return dict(self.coefficients).get(index, 0.0)


@dataclass(frozen=True)
class VariableBound:
    lower: float | None
    upper: float | None


def _row(name: str, values: dict[int, float], rhs: float) -> SparseEqualityRow:
    return SparseEqualityRow(
        name=name,
        coefficients=tuple(sorted((index, float(value)) for index, value in values.items() if value != 0.0)),
        rhs=float(rhs),
    )


def build_balance_rows(
    inputs: PhysicalInputs,
    layout: VariableLayout,
) -> tuple[SparseEqualityRow, ...]:
    """Build x - c + d - r = L - V for every interval."""

    inputs.validate(layout.n_intervals)
    rows: list[SparseEqualityRow] = []
    for k in range(layout.n_intervals):
        rows.append(
            _row(
                name=f"balance_k{k + 1}",
                values={
                    layout.x(k): 1.0,
                    layout.c(k): -1.0,
                    layout.d(k): 1.0,
                    layout.r(k): -1.0,
                },
                rhs=inputs.load_kwh[k] - inputs.pv_available_kwh[k],
            )
        )
    return tuple(rows)


def build_soc_rows(
    grid: TimeGrid,
    storage: StorageParameters,
    layout: VariableLayout,
    initial_energy_kwh: float,
) -> tuple[SparseEqualityRow, ...]:
    """Build E_k - E_(k-1) - eta_c c_k + d_k/eta_d = 0."""

    if layout.n_intervals != grid.n_intervals:
        raise ValueError("TIME_LAYOUT_MISMATCH")
    if not isfinite(initial_energy_kwh):
        raise ValueError("INITIAL_ENERGY_NONFINITE")
    if not storage.operational_min_kwh <= initial_energy_kwh <= storage.operational_max_kwh:
        raise ValueError("INITIAL_ENERGY_OUT_OF_BOUNDS")
    rows: list[SparseEqualityRow] = []
    for k in range(grid.n_intervals):
        coefficients = {
            layout.c(k): -storage.eta_charge,
            layout.d(k): 1.0 / storage.eta_discharge,
            layout.energy(k): 1.0,
        }
        rhs = initial_energy_kwh if k == 0 else 0.0
        if k > 0:
            coefficients[layout.energy(k - 1)] = -1.0
        rows.append(_row(f"soc_k{k + 1}", coefficients, rhs))
    return tuple(rows)


def build_variable_bounds(
    inputs: PhysicalInputs,
    grid: TimeGrid,
    storage: StorageParameters,
    layout: VariableLayout,
    terminal_energy_kwh: float | None = None,
) -> tuple[VariableBound, ...]:
    """Bind nonnegativity, curtailment, power-to-energy, and SOC limits."""

    inputs.validate(grid.n_intervals)
    if layout.n_intervals != grid.n_intervals:
        raise ValueError("TIME_LAYOUT_MISMATCH")
    if terminal_energy_kwh is not None and not (
        storage.operational_min_kwh <= terminal_energy_kwh <= storage.operational_max_kwh
    ):
        raise ValueError("TERMINAL_ENERGY_OUT_OF_BOUNDS")
    power_energy_limit = storage.interval_energy_limit_kwh(grid)
    bounds: list[VariableBound] = [VariableBound(None, None) for _ in range(layout.size)]
    for k in range(grid.n_intervals):
        bounds[layout.x(k)] = VariableBound(0.0, None)
        bounds[layout.c(k)] = VariableBound(0.0, power_energy_limit)
        bounds[layout.d(k)] = VariableBound(0.0, power_energy_limit)
        bounds[layout.r(k)] = VariableBound(0.0, inputs.pv_available_kwh[k])
        bounds[layout.energy(k)] = VariableBound(
            storage.operational_min_kwh,
            storage.operational_max_kwh,
        )
    if terminal_energy_kwh is not None:
        bounds[layout.energy(grid.n_intervals - 1)] = VariableBound(
            terminal_energy_kwh,
            terminal_energy_kwh,
        )
    return tuple(bounds)


def build_physical_equalities(
    inputs: PhysicalInputs,
    grid: TimeGrid,
    storage: StorageParameters,
    layout: VariableLayout,
    initial_energy_kwh: float,
) -> tuple[SparseEqualityRow, ...]:
    return build_balance_rows(inputs, layout) + build_soc_rows(
        grid,
        storage,
        layout,
        initial_energy_kwh,
    )
