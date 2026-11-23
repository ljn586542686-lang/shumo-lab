"""Q2 双分支最小骨架；只构造合同对象，不调用求解器。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from math import isfinite
from typing import Sequence

from model_contract import PhysicalInputs, StorageParameters, TimeGrid, VariableLayout
from physical_core_solver import (
    SparseEqualityRow,
    VariableBound,
    build_physical_equalities,
    build_variable_bounds,
)
from q2_data_contract import POSITIONS_PER_DAY, PredictionProvider, Q2InformationSet, kw_to_kwh


EMERGENCY_MULTIPLIER = 5.0
PREDICTION_MODEL_STATUS = "JANUARY_WALK_FORWARD_SELECTION"
PLANNED_PURCHASE_PHYSICAL_REALIZATION_STATUS = "LOCKED_HUMAN_DECISION"
TERMINAL_HORIZON_STATUS = "END_OF_YEAR_SOC_6000"
DAILY_SOC_RESET_PRESENT = False


class StorageExecutionMode(str, Enum):
    FIXED_STORAGE_SCHEDULE = "FIXED_STORAGE_SCHEDULE"
    CAUSAL_STORAGE_RECOURSE = "CAUSAL_STORAGE_RECOURSE"


class PlannedPurchasePhysicalMode(str, Enum):
    COMMITTED_AND_PHYSICALLY_DELIVERED = "COMMITTED_AND_PHYSICALLY_DELIVERED"
    FINANCIAL_COMMITMENT_WITH_ACTUAL_USAGE_SEPARATED = (
        "FINANCIAL_COMMITMENT_WITH_ACTUAL_USAGE_SEPARATED"
    )


@dataclass(frozen=True)
class Q2Configuration:
    storage_execution_mode: StorageExecutionMode | None
    planned_purchase_physical_mode: PlannedPurchasePhysicalMode | None
    terminal_horizon_mode: str | None
    prediction_provider: PredictionProvider | None
    terminal_energy_kwh: float | None = None

    def validate(self) -> None:
        if not isinstance(self.storage_execution_mode, StorageExecutionMode):
            raise ValueError("Q2_STORAGE_EXECUTION_MODE_REQUIRED")
        if not isinstance(self.planned_purchase_physical_mode, PlannedPurchasePhysicalMode):
            raise ValueError("Q2_PLANNED_PURCHASE_PHYSICAL_MODE_REQUIRED")
        if not self.terminal_horizon_mode or self.terminal_horizon_mode.strip().upper() == "PENDING":
            raise ValueError("Q2_TERMINAL_HORIZON_MODE_REQUIRED")
        if self.prediction_provider is None:
            raise ValueError("Q2_PREDICTION_PROVIDER_REQUIRED")
        if self.terminal_energy_kwh is not None and not isfinite(self.terminal_energy_kwh):
            raise ValueError("Q2_TERMINAL_ENERGY_NONFINITE")


@dataclass(frozen=True)
class Q2SettlementLayer:
    planned_coefficients: tuple[float, ...]
    emergency_coefficients: tuple[float, ...]


@dataclass(frozen=True)
class Q2DaySkeleton:
    day: date
    information_set: Q2InformationSet
    physical_inputs: PhysicalInputs
    physical_equalities: tuple[SparseEqualityRow, ...]
    physical_bounds: tuple[VariableBound, ...]
    settlement: Q2SettlementLayer
    storage_execution_mode: StorageExecutionMode
    planned_purchase_physical_mode: PlannedPurchasePhysicalMode
    terminal_horizon_mode: str
    physical_realization_relation: str
    artificial_storage_dissipation_forbidden: bool


def build_settlement_layer(price_per_kwh: Sequence[float]) -> Q2SettlementLayer:
    prices = tuple(float(value) for value in price_per_kwh)
    if len(prices) != POSITIONS_PER_DAY or not all(isfinite(value) and value >= 0.0 for value in prices):
        raise ValueError("Q2_PRICE_VECTOR_INVALID")
    return Q2SettlementLayer(prices, tuple(EMERGENCY_MULTIPLIER * value for value in prices))


def evaluate_base_settlement(
    price_per_kwh: Sequence[float],
    planned_purchase_kwh: Sequence[float],
    emergency_purchase_kwh: Sequence[float],
) -> float:
    settlement = build_settlement_layer(price_per_kwh)
    planned = tuple(float(value) for value in planned_purchase_kwh)
    emergency = tuple(float(value) for value in emergency_purchase_kwh)
    if len(planned) != POSITIONS_PER_DAY or len(emergency) != POSITIONS_PER_DAY:
        raise ValueError("Q2_SETTLEMENT_POSITION_COUNT_MISMATCH")
    if not all(isfinite(value) and value >= 0.0 for value in planned + emergency):
        raise ValueError("Q2_SETTLEMENT_QUANTITY_INVALID")
    return sum(p * q for p, q in zip(settlement.planned_coefficients, planned)) + sum(
        p * h for p, h in zip(settlement.emergency_coefficients, emergency)
    )


def next_day_initial_soc(previous_day_final_soc_kwh: float) -> float:
    value = float(previous_day_final_soc_kwh)
    if not isfinite(value):
        raise ValueError("Q2_PREVIOUS_FINAL_SOC_NONFINITE")
    return value


def _physical_relation(mode: PlannedPurchasePhysicalMode) -> str:
    if mode is PlannedPurchasePhysicalMode.COMMITTED_AND_PHYSICALLY_DELIVERED:
        return "x_k = Q_k + H_k"
    return "x_k = U_k + H_k; Q_k is a financial commitment and U_k is separately tracked actual usage"


def build_q2_day_skeleton(
    day: date,
    information_set: Q2InformationSet,
    price_per_kwh: Sequence[float],
    configuration: Q2Configuration,
    storage: StorageParameters | None = None,
) -> Q2DaySkeleton:
    configuration.validate()
    information_set.validate()
    if information_set.decision_day != day:
        raise ValueError("Q2_INFORMATION_DAY_MISMATCH")
    provider = configuration.prediction_provider
    assert provider is not None
    load_kwh = kw_to_kwh(provider.forecast_load(day, information_set))
    pv_kwh = kw_to_kwh(provider.forecast_pv(day, information_set))
    prices = tuple(float(value) for value in price_per_kwh)
    inputs = PhysicalInputs.from_sequences(load_kwh, pv_kwh, prices)
    grid = TimeGrid()
    storage_parameters = storage or StorageParameters.working_e_a()
    layout = VariableLayout(grid.n_intervals)
    equalities = build_physical_equalities(
        inputs,
        grid,
        storage_parameters,
        layout,
        information_set.current_soc_kwh,
    )
    bounds = build_variable_bounds(
        inputs,
        grid,
        storage_parameters,
        layout,
        terminal_energy_kwh=configuration.terminal_energy_kwh,
    )
    storage_mode = configuration.storage_execution_mode
    physical_mode = configuration.planned_purchase_physical_mode
    assert storage_mode is not None and physical_mode is not None
    return Q2DaySkeleton(
        day=day,
        information_set=information_set,
        physical_inputs=inputs,
        physical_equalities=equalities,
        physical_bounds=bounds,
        settlement=build_settlement_layer(prices),
        storage_execution_mode=storage_mode,
        planned_purchase_physical_mode=physical_mode,
        terminal_horizon_mode=configuration.terminal_horizon_mode or "",
        physical_realization_relation=_physical_relation(physical_mode),
        artificial_storage_dissipation_forbidden=True,
    )
