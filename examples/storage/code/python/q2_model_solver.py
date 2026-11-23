"""Q2 连续 LP 构造器；复用 Q1 物理内核，不调用求解器。"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Sequence

from model_contract import PhysicalInputs, StorageParameters, TimeGrid
from physical_core_solver import (
    SparseEqualityRow,
    VariableBound,
    build_balance_rows,
    build_soc_rows,
    build_variable_bounds,
)
from q1_model_solver import ObjectiveLevel


EMERGENCY_MULTIPLIER = 5.0
VARIABLE_BLOCKS = ("Q", "G", "H", "c", "d", "r", "E", "S")


@dataclass(frozen=True)
class Q2VariableLayout:
    """Q, G, H, c, d, r, E and unused-plan slack S blocks."""

    n_intervals: int

    def __post_init__(self) -> None:
        if self.n_intervals <= 0:
            raise ValueError("Q2_VARIABLE_LAYOUT_SIZE_INVALID")

    @property
    def size(self) -> int:
        return len(VARIABLE_BLOCKS) * self.n_intervals

    def _index(self, block: int, k: int) -> int:
        if not 0 <= k < self.n_intervals:
            raise IndexError("Q2_TIME_INDEX_OUT_OF_RANGE")
        return block * self.n_intervals + k

    def q(self, k: int) -> int: return self._index(0, k)
    def g(self, k: int) -> int: return self._index(1, k)
    def h(self, k: int) -> int: return self._index(2, k)
    def c(self, k: int) -> int: return self._index(3, k)
    def d(self, k: int) -> int: return self._index(4, k)
    def r(self, k: int) -> int: return self._index(5, k)
    def energy(self, k: int) -> int: return self._index(6, k)
    def slack(self, k: int) -> int: return self._index(7, k)

    def x(self, k: int) -> int:
        """Q1 physical-core compatibility: physical grid x maps to ordinary-use G."""
        return self.g(k)

    def block_indices(self, name: str) -> tuple[int, ...]:
        accessors = {
            "Q": self.q,
            "G": self.g,
            "H": self.h,
            "c": self.c,
            "d": self.d,
            "r": self.r,
            "E": self.energy,
            "S": self.slack,
        }
        try:
            accessor = accessors[name]
        except KeyError as exc:
            raise KeyError(f"Q2_UNKNOWN_VARIABLE_BLOCK:{name}") from exc
        return tuple(accessor(k) for k in range(self.n_intervals))


@dataclass(frozen=True)
class Q2LinearProgram:
    grid: TimeGrid
    storage: StorageParameters
    inputs: PhysicalInputs
    layout: Q2VariableLayout
    equalities: tuple[SparseEqualityRow, ...]
    bounds: tuple[VariableBound, ...]
    objective_levels: tuple[ObjectiveLevel, ...]
    initial_energy_kwh: float
    terminal_energy_kwh: float | None
    planned_purchase_fixed_kwh: tuple[float, ...] | None
    variable_domain: str = "CONTINUOUS"


def _objective(layout: Q2VariableLayout, coefficients_by_block: dict[str, Sequence[float]]) -> tuple[float, ...]:
    values = [0.0] * layout.size
    for block, coefficients in coefficients_by_block.items():
        if len(coefficients) != layout.n_intervals:
            raise ValueError("Q2_OBJECTIVE_LENGTH_MISMATCH")
        for index, coefficient in zip(layout.block_indices(block), coefficients):
            numeric = float(coefficient)
            if not isfinite(numeric):
                raise ValueError("Q2_OBJECTIVE_NONFINITE")
            values[index] = numeric
    return tuple(values)


def build_q2_objectives(inputs: PhysicalInputs, layout: Q2VariableLayout) -> tuple[ObjectiveLevel, ...]:
    prices = inputs.price_per_kwh
    ones = (1.0,) * layout.n_intervals
    primary = _objective(
        layout,
        {"Q": prices, "H": tuple(EMERGENCY_MULTIPLIER * value for value in prices)},
    )
    throughput = _objective(layout, {"c": ones, "d": ones})
    curtailment = _objective(layout, {"r": ones})
    return (
        ObjectiveLevel(1, "q2_total_purchase_cost", primary, "min sum(p_k*Q_k + 5*p_k*H_k)"),
        ObjectiveLevel(2, "storage_throughput", throughput, "lock level 1 exactly; min sum(c_k+d_k)"),
        ObjectiveLevel(3, "pv_curtailment", curtailment, "lock levels 1-2 exactly; min sum(r_k)"),
    )


def _add_emergency_to_balance(
    base_rows: Sequence[SparseEqualityRow],
    layout: Q2VariableLayout,
) -> tuple[SparseEqualityRow, ...]:
    rows: list[SparseEqualityRow] = []
    for k, row in enumerate(base_rows):
        coefficients = dict(row.coefficients)
        coefficients[layout.h(k)] = 1.0
        rows.append(
            SparseEqualityRow(
                name=f"q2_{row.name}",
                coefficients=tuple(sorted(coefficients.items())),
                rhs=row.rhs,
            )
        )
    return tuple(rows)


def _realization_rows(layout: Q2VariableLayout) -> tuple[SparseEqualityRow, ...]:
    return tuple(
        SparseEqualityRow(
            name=f"planned_realization_k{k + 1}",
            coefficients=((layout.q(k), -1.0), (layout.g(k), 1.0), (layout.slack(k), 1.0)),
            rhs=0.0,
        )
        for k in range(layout.n_intervals)
    )


def build_q2_model(
    load_kwh: Sequence[float],
    pv_available_kwh: Sequence[float],
    price_per_kwh: Sequence[float],
    *,
    initial_energy_kwh: float,
    planned_purchase_fixed_kwh: Sequence[float] | None = None,
    terminal_energy_kwh: float | None = None,
    storage: StorageParameters | None = None,
) -> Q2LinearProgram:
    inputs = PhysicalInputs.from_sequences(load_kwh, pv_available_kwh, price_per_kwh)
    n = len(inputs.load_kwh)
    grid = TimeGrid(n_intervals=n)
    selected_storage = storage or StorageParameters.working_e_a()
    layout = Q2VariableLayout(n)
    base_balance = build_balance_rows(inputs, layout)
    soc_rows = build_soc_rows(grid, selected_storage, layout, initial_energy_kwh)
    equalities = _add_emergency_to_balance(base_balance, layout) + soc_rows + _realization_rows(layout)
    bounds = list(build_variable_bounds(inputs, grid, selected_storage, layout, terminal_energy_kwh))
    fixed = None if planned_purchase_fixed_kwh is None else tuple(float(value) for value in planned_purchase_fixed_kwh)
    if fixed is not None and len(fixed) != n:
        raise ValueError("Q2_FIXED_PLAN_LENGTH_MISMATCH")
    for k in range(n):
        if fixed is None:
            bounds[layout.q(k)] = VariableBound(0.0, None)
        else:
            if not isfinite(fixed[k]) or fixed[k] < 0.0:
                raise ValueError("Q2_FIXED_PLAN_INVALID")
            bounds[layout.q(k)] = VariableBound(fixed[k], fixed[k])
        bounds[layout.h(k)] = VariableBound(0.0, None)
        bounds[layout.slack(k)] = VariableBound(0.0, None)
    return Q2LinearProgram(
        grid=grid,
        storage=selected_storage,
        inputs=inputs,
        layout=layout,
        equalities=equalities,
        bounds=tuple(bounds),
        objective_levels=build_q2_objectives(inputs, layout),
        initial_energy_kwh=float(initial_energy_kwh),
        terminal_energy_kwh=terminal_energy_kwh,
        planned_purchase_fixed_kwh=fixed,
    )
