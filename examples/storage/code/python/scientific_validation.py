"""C 题物理与科学验证器（纯函数）。

功能：独立重算 SOC、平衡残差、一级目标和约束违反，并扫描同时充放电。
输入：候选轨迹及其原始区间输入。
输出：可审计的字典报告；不读取正式附件、不调用求解器。
使用：本轮用于手算和结构测试，未来用于 solver 输出的独立复核。
"""

from __future__ import annotations

from math import isfinite
from typing import Sequence

from model_contract import PhysicalInputs, StorageParameters, TimeGrid


def _values(values: Sequence[float], name: str) -> tuple[float, ...]:
    converted = tuple(float(value) for value in values)
    if not converted:
        raise ValueError(f"{name}_EMPTY")
    if not all(isfinite(value) for value in converted):
        raise ValueError(f"{name}_NONFINITE")
    return converted


def recompute_soc_path(
    initial_energy_kwh: float,
    charge_kwh: Sequence[float],
    discharge_kwh: Sequence[float],
    storage: StorageParameters,
) -> tuple[float, ...]:
    charge = _values(charge_kwh, "CHARGE")
    discharge = _values(discharge_kwh, "DISCHARGE")
    if len(charge) != len(discharge):
        raise ValueError("CHARGE_DISCHARGE_LENGTH_MISMATCH")
    energy = float(initial_energy_kwh)
    path: list[float] = []
    for c_k, d_k in zip(charge, discharge):
        energy = energy + storage.eta_charge * c_k - d_k / storage.eta_discharge
        path.append(energy)
    return tuple(path)


def balance_residuals(
    grid_purchase_kwh: Sequence[float],
    charge_kwh: Sequence[float],
    discharge_kwh: Sequence[float],
    curtailed_pv_kwh: Sequence[float],
    inputs: PhysicalInputs,
) -> tuple[float, ...]:
    x = _values(grid_purchase_kwh, "GRID_PURCHASE")
    c = _values(charge_kwh, "CHARGE")
    d = _values(discharge_kwh, "DISCHARGE")
    r = _values(curtailed_pv_kwh, "CURTAILMENT")
    n = len(inputs.load_kwh)
    if {len(x), len(c), len(d), len(r), len(inputs.pv_available_kwh), len(inputs.price_per_kwh)} != {n}:
        raise ValueError("VALIDATION_LENGTH_MISMATCH")
    return tuple(
        x[k] + inputs.pv_available_kwh[k] - r[k] + d[k] - inputs.load_kwh[k] - c[k]
        for k in range(n)
    )


def recompute_primary_objective(
    grid_purchase_kwh: Sequence[float],
    price_per_kwh: Sequence[float],
) -> float:
    x = _values(grid_purchase_kwh, "GRID_PURCHASE")
    price = _values(price_per_kwh, "PRICE")
    if len(x) != len(price):
        raise ValueError("OBJECTIVE_LENGTH_MISMATCH")
    return sum(p_k * x_k for p_k, x_k in zip(price, x))


def simultaneous_charge_discharge_events(
    charge_kwh: Sequence[float],
    discharge_kwh: Sequence[float],
    energy_path_kwh: Sequence[float],
    inputs: PhysicalInputs,
    tolerance: float = 1e-8,
) -> tuple[dict[str, object], ...]:
    c = _values(charge_kwh, "CHARGE")
    d = _values(discharge_kwh, "DISCHARGE")
    energy = _values(energy_path_kwh, "ENERGY")
    n = len(c)
    if {len(d), len(energy), len(inputs.load_kwh)} != {n}:
        raise ValueError("SIMULTANEOUS_CD_LENGTH_MISMATCH")
    events: list[dict[str, object]] = []
    for k in range(n):
        if c[k] > tolerance and d[k] > tolerance:
            events.append(
                {
                    "k": k + 1,
                    "c_k": c[k],
                    "d_k": d[k],
                    "p_k": inputs.price_per_kwh[k],
                    "V_k": inputs.pv_available_kwh[k],
                    "L_k": inputs.load_kwh[k],
                    "E_k": energy[k],
                    "pv_surplus_status": inputs.pv_available_kwh[k] > inputs.load_kwh[k],
                    "primary_objective_effect": "REQUIRES_COUNTERFACTUAL_REVIEW",
                    "classification": "TO_BE_VERIFIED",
                }
            )
    return tuple(events)


def validate_q1_trajectory(
    *,
    grid_purchase_kwh: Sequence[float],
    charge_kwh: Sequence[float],
    discharge_kwh: Sequence[float],
    curtailed_pv_kwh: Sequence[float],
    energy_path_kwh: Sequence[float],
    inputs: PhysicalInputs,
    storage: StorageParameters,
    grid: TimeGrid | None = None,
    initial_energy_kwh: float = 6_000.0,
    terminal_energy_kwh: float = 6_000.0,
    tolerance: float = 1e-8,
) -> dict[str, object]:
    selected_grid = grid or TimeGrid(n_intervals=len(inputs.load_kwh))
    inputs.validate(selected_grid.n_intervals)
    x = _values(grid_purchase_kwh, "GRID_PURCHASE")
    c = _values(charge_kwh, "CHARGE")
    d = _values(discharge_kwh, "DISCHARGE")
    r = _values(curtailed_pv_kwh, "CURTAILMENT")
    energy = _values(energy_path_kwh, "ENERGY")
    if {len(x), len(c), len(d), len(r), len(energy)} != {selected_grid.n_intervals}:
        raise ValueError("TRAJECTORY_LENGTH_MISMATCH")

    recomputed_energy = recompute_soc_path(initial_energy_kwh, c, d, storage)
    residuals = balance_residuals(x, c, d, r, inputs)
    power_energy_limit = storage.interval_energy_limit_kwh(selected_grid)
    checks = {
        "grid_purchase_nonnegative": min(x) >= -tolerance,
        "charge_nonnegative": min(c) >= -tolerance,
        "discharge_nonnegative": min(d) >= -tolerance,
        "curtailment_nonnegative": min(r) >= -tolerance,
        "curtailment_within_pv": all(r_k <= v_k + tolerance for r_k, v_k in zip(r, inputs.pv_available_kwh)),
        "charge_power_limit": max(c) <= power_energy_limit + tolerance,
        "discharge_power_limit": max(d) <= power_energy_limit + tolerance,
        "soc_lower_bound": min(energy) >= storage.operational_min_kwh - tolerance,
        "soc_upper_bound": max(energy) <= storage.operational_max_kwh + tolerance,
        "soc_recursion": max(abs(a - b) for a, b in zip(energy, recomputed_energy)) <= tolerance,
        "terminal_soc": abs(energy[-1] - terminal_energy_kwh) <= tolerance,
        "balance": max(abs(value) for value in residuals) <= tolerance,
    }
    events = simultaneous_charge_discharge_events(c, d, energy, inputs, tolerance)
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "max_abs_balance_residual": max(abs(value) for value in residuals),
        "max_abs_soc_recalculation_error": max(abs(a - b) for a, b in zip(energy, recomputed_energy)),
        "primary_objective_recomputed": recompute_primary_objective(x, inputs.price_per_kwh),
        "simultaneous_charge_discharge_status": "DETECTED" if events else "NOT_DETECTED",
        "simultaneous_charge_discharge_events": events,
        "simultaneous_charge_discharge_interpretation": "TO_BE_VERIFIED",
    }

