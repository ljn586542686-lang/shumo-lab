"""Q1 synthetic fixtures；本模块只生成数据，不调用求解器。

功能：生成 TEST_A 至 TEST_J 的小型或 144 时段结构化输入。
输入：可选储能效率配置。
输出：``SyntheticFixture`` 映射及显式的 solver 延后标记。
使用：纯结构/手算测试可直接消费；最优解性质测试延后到 SOLVER_EXECUTION。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from model_contract import PhysicalInputs, StorageParameters


DEFERRED_TO_SOLVER_EXECUTION_STAGE = "DEFERRED_TO_SOLVER_EXECUTION_STAGE"


@dataclass(frozen=True)
class SyntheticFixture:
    name: str
    inputs: PhysicalInputs
    initial_energy_kwh: float = 6_000.0
    terminal_energy_kwh: float | None = 6_000.0
    prescribed_charge_kwh: tuple[float, ...] = field(default_factory=tuple)
    prescribed_discharge_kwh: tuple[float, ...] = field(default_factory=tuple)
    solver_required: bool = True
    purpose: str = ""


def _inputs(
    load: tuple[float, ...],
    pv: tuple[float, ...],
    price: tuple[float, ...],
) -> PhysicalInputs:
    return PhysicalInputs.from_sequences(load, pv, price)


def _constant_inputs(
    *,
    n: int = 144,
    load: float = 1_000.0,
    pv: float = 0.0,
    price: float = 1.0,
) -> PhysicalInputs:
    return _inputs((load,) * n, (pv,) * n, (price,) * n)


def build_synthetic_suite(
    storage: StorageParameters | None = None,
) -> dict[str, SyntheticFixture]:
    selected_storage = storage or StorageParameters.working_e_a()
    low_price = 1.0
    loss_threshold_high_price = low_price / (
        selected_storage.eta_charge * selected_storage.eta_discharge
    )
    half = 72
    b1_price = (low_price,) * half + (0.99 * loss_threshold_high_price,) * half
    b2_price = (low_price,) * half + (1.01 * loss_threshold_high_price,) * half

    pv_surplus = [0.0] * 144
    for k in range(8):
        pv_surplus[k] = 2_000.0
    near_full_load = [100.0] * 144

    soc_charge = (100.0, 0.0, 20.0)
    soc_discharge = (0.0, 45.0, 0.0)
    soc_inputs = _constant_inputs(n=3, load=100.0, pv=0.0, price=1.0)

    base = _constant_inputs()
    return {
        "TEST_A_FLAT_PRICE_NO_PV": SyntheticFixture(
            "TEST_A_FLAT_PRICE_NO_PV",
            base,
            purpose="flat price, no PV; verify no free economic gain and no meaningless cycling",
        ),
        "TEST_B_PRICE_ARBITRAGE_B1": SyntheticFixture(
            "TEST_B_PRICE_ARBITRAGE_B1",
            _inputs((1_000.0,) * 144, (0.0,) * 144, b1_price),
            purpose="price spread just below the round-trip-loss threshold",
        ),
        "TEST_B_PRICE_ARBITRAGE_B2": SyntheticFixture(
            "TEST_B_PRICE_ARBITRAGE_B2",
            _inputs((1_000.0,) * 144, (0.0,) * 144, b2_price),
            purpose="price spread just above the round-trip-loss threshold",
        ),
        "TEST_C_PV_SURPLUS_FULL_BATTERY": SyntheticFixture(
            "TEST_C_PV_SURPLUS_FULL_BATTERY",
            _inputs(tuple(near_full_load), tuple(pv_surplus), (1.0,) * 144),
            purpose="require explicit PV curtailment near the SOC upper bound",
        ),
        "TEST_D_SOC_HAND_CALC": SyntheticFixture(
            "TEST_D_SOC_HAND_CALC",
            soc_inputs,
            prescribed_charge_kwh=soc_charge,
            prescribed_discharge_kwh=soc_discharge,
            solver_required=False,
            terminal_energy_kwh=None,
            purpose="independent hand calculation of the SOC recursion",
        ),
        "TEST_E_Q1_TERMINAL_SOC": SyntheticFixture(
            "TEST_E_Q1_TERMINAL_SOC", base, purpose="verify E_0=E_144=6000"
        ),
        "TEST_F_POWER_LIMIT": SyntheticFixture(
            "TEST_F_POWER_LIMIT", base, purpose="verify c,d <= max_power*Delta_t"
        ),
        "TEST_G_NO_NEGATIVE_VARIABLES": SyntheticFixture(
            "TEST_G_NO_NEGATIVE_VARIABLES", base, purpose="verify x,c,d,r nonnegativity"
        ),
        "TEST_H_BALANCE": SyntheticFixture(
            "TEST_H_BALANCE", base, purpose="independently recompute interval balance"
        ),
        "TEST_I_SIMULTANEOUS_CD": SyntheticFixture(
            "TEST_I_SIMULTANEOUS_CD", base, purpose="scan c_k>tol and d_k>tol"
        ),
        "TEST_J_OBJECTIVE_RECOMPUTE": SyntheticFixture(
            "TEST_J_OBJECTIVE_RECOMPUTE", base, purpose="independently recompute sum(p_k*x_k)"
        ),
    }


def arbitrage_margin(
    low_price: float,
    high_price: float,
    storage: StorageParameters,
) -> float:
    """Positive means one charged bus-kWh can save more high-price cost than it costs."""

    return high_price * storage.eta_charge * storage.eta_discharge - low_price


def solver_dependent_test_names() -> tuple[str, ...]:
    return tuple(
        name
        for name, fixture in build_synthetic_suite().items()
        if fixture.solver_required
    )
