"""C 题公共模型合同。

功能：集中保存已锁定的时间、储能参数、输入校验和变量索引。
输入：调用方传入的负荷、光伏与电价区间序列。
输出：不可变配置对象和统一的变量位置映射。
使用：由 ``physical_core_solver``、``q1_model_solver`` 和验证代码导入；本模块不求解。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Iterable, Sequence


APPROVED_ROUTE_ID = "A：因果确定性滚动线性优化"
APPROVED_MODEL_FAMILY = "continuous_linear_programming"
SOURCE_EVIDENCE_POLICY = "LOCKED"
TIME_INDEX_STATUS = "LOCKED"
TIME_MAPPING = "POSITION_BASED"
NON_ANTICIPATIVITY = "LOCKED"
PAST_EXECUTED_DECISIONS_FROZEN = "LOCKED"
NO_IMPLICIT_EXPORT = "STRONG_WORKING_ASSUMPTION"


def _finite_tuple(values: Iterable[float], name: str) -> tuple[float, ...]:
    converted = tuple(float(value) for value in values)
    if not converted:
        raise ValueError(f"{name}_EMPTY")
    if not all(isfinite(value) for value in converted):
        raise ValueError(f"{name}_NONFINITE")
    return converted


@dataclass(frozen=True)
class TimeGrid:
    """Position-based interval grid; no result time-string parsing is allowed."""

    n_intervals: int = 144
    delta_t_hours: float = 1.0 / 6.0
    interval_minutes: int = 10

    def __post_init__(self) -> None:
        if self.n_intervals <= 0:
            raise ValueError("TIME_INTERVAL_COUNT_INVALID")
        if self.delta_t_hours <= 0 or not isfinite(self.delta_t_hours):
            raise ValueError("DELTA_T_INVALID")
        if self.interval_minutes <= 0:
            raise ValueError("INTERVAL_MINUTES_INVALID")
        if abs(self.delta_t_hours * 60.0 - self.interval_minutes) > 1e-12:
            raise ValueError("TIME_GRID_UNIT_MISMATCH")

    @property
    def indices(self) -> tuple[int, ...]:
        return tuple(range(1, self.n_intervals + 1))

    @property
    def intervals_minutes(self) -> tuple[tuple[int, int], ...]:
        return tuple(
            ((k - 1) * self.interval_minutes, k * self.interval_minutes)
            for k in self.indices
        )


@dataclass(frozen=True)
class StorageParameters:
    """Storage parameters with switchable efficiency interpretation."""

    nominal_capacity_kwh: float = 12_000.0
    operational_min_kwh: float = 1_200.0
    operational_max_kwh: float = 10_800.0
    max_charge_discharge_power_kw: float = 5_000.0
    eta_charge: float = 0.9
    eta_discharge: float = 0.9

    def __post_init__(self) -> None:
        values = (
            self.nominal_capacity_kwh,
            self.operational_min_kwh,
            self.operational_max_kwh,
            self.max_charge_discharge_power_kw,
            self.eta_charge,
            self.eta_discharge,
        )
        if not all(isfinite(value) for value in values):
            raise ValueError("STORAGE_PARAMETER_NONFINITE")
        if not (
            0.0 <= self.operational_min_kwh
            <= self.operational_max_kwh
            <= self.nominal_capacity_kwh
        ):
            raise ValueError("STORAGE_ENERGY_BOUND_INVALID")
        if self.max_charge_discharge_power_kw <= 0.0:
            raise ValueError("STORAGE_POWER_LIMIT_INVALID")
        if not (0.0 < self.eta_charge <= 1.0 and 0.0 < self.eta_discharge <= 1.0):
            raise ValueError("STORAGE_EFFICIENCY_INVALID")

    @classmethod
    def working_e_a(cls) -> "StorageParameters":
        return cls(eta_charge=0.9, eta_discharge=0.9)

    @classmethod
    def sensitivity_e_b(cls) -> "StorageParameters":
        eta = sqrt(0.9)
        return cls(eta_charge=eta, eta_discharge=eta)

    def interval_energy_limit_kwh(self, grid: TimeGrid) -> float:
        return self.max_charge_discharge_power_kw * grid.delta_t_hours


@dataclass(frozen=True)
class PhysicalInputs:
    """Interval-energy inputs in kWh and price in currency/kWh."""

    load_kwh: tuple[float, ...]
    pv_available_kwh: tuple[float, ...]
    price_per_kwh: tuple[float, ...]

    @classmethod
    def from_sequences(
        cls,
        load_kwh: Sequence[float],
        pv_available_kwh: Sequence[float],
        price_per_kwh: Sequence[float],
    ) -> "PhysicalInputs":
        inputs = cls(
            load_kwh=_finite_tuple(load_kwh, "LOAD"),
            pv_available_kwh=_finite_tuple(pv_available_kwh, "PV"),
            price_per_kwh=_finite_tuple(price_per_kwh, "PRICE"),
        )
        inputs.validate()
        return inputs

    def validate(self, expected_length: int | None = None) -> None:
        lengths = {len(self.load_kwh), len(self.pv_available_kwh), len(self.price_per_kwh)}
        if len(lengths) != 1:
            raise ValueError("INPUT_LENGTH_MISMATCH")
        if expected_length is not None and lengths != {expected_length}:
            raise ValueError("INPUT_INTERVAL_COUNT_MISMATCH")
        if any(value < 0.0 for value in self.load_kwh):
            raise ValueError("LOAD_NEGATIVE")
        if any(value < 0.0 for value in self.pv_available_kwh):
            raise ValueError("PV_NEGATIVE")


@dataclass(frozen=True)
class VariableLayout:
    """Five n-length blocks: x, c, d, r, and end-of-interval E."""

    n_intervals: int

    def __post_init__(self) -> None:
        if self.n_intervals <= 0:
            raise ValueError("VARIABLE_LAYOUT_SIZE_INVALID")

    @property
    def size(self) -> int:
        return 5 * self.n_intervals

    def _index(self, block: int, k_zero_based: int) -> int:
        if not 0 <= k_zero_based < self.n_intervals:
            raise IndexError("TIME_INDEX_OUT_OF_RANGE")
        return block * self.n_intervals + k_zero_based

    def x(self, k_zero_based: int) -> int:
        return self._index(0, k_zero_based)

    def c(self, k_zero_based: int) -> int:
        return self._index(1, k_zero_based)

    def d(self, k_zero_based: int) -> int:
        return self._index(2, k_zero_based)

    def r(self, k_zero_based: int) -> int:
        return self._index(3, k_zero_based)

    def energy(self, k_zero_based: int) -> int:
        return self._index(4, k_zero_based)

    def block_indices(self, name: str) -> tuple[int, ...]:
        accessors = {
            "x": self.x,
            "c": self.c,
            "d": self.d,
            "r": self.r,
            "E": self.energy,
        }
        try:
            accessor = accessors[name]
        except KeyError as exc:
            raise KeyError(f"UNKNOWN_VARIABLE_BLOCK:{name}") from exc
        return tuple(accessor(k) for k in range(self.n_intervals))
