"""Rolling residual correction with observations strictly before the forecast day."""
from __future__ import annotations
import math
from datetime import date,timedelta
from typing import Mapping,Sequence
from q2_data_contract import POSITIONS_PER_DAY,Q2InformationSet
from q2_forecasting import forecast_candidate

BASE_LOAD_CANDIDATE = "L5_recent_same_weekday_mean"

PV_CANDIDATE = "P3_rolling_7day_mean"

def base_load_forecast(day: date, history: Mapping[date, Sequence[float]]) -> tuple[float, ...]:
    return forecast_candidate(BASE_LOAD_CANDIDATE, day, history)[0]

def residual_source_days(day: date, history: Mapping[date, Sequence[float]], window: int) -> tuple[date, ...]:
    eligible: list[date] = []
    for source in sorted(item for item in history if item < day):
        source_history = {prior: history[prior] for prior in history if prior < source}
        try:
            base_load_forecast(source, source_history)
        except ValueError as exc:
            if str(exc).startswith("Q2_FORECAST_HISTORY_INSUFFICIENT"):
                continue
            raise
        eligible.append(source)
    return tuple(eligible[-window:])

def corrected_load_forecast(
    day: date,
    history: Mapping[date, Sequence[float]],
    window: int,
) -> tuple[tuple[float, ...], tuple[date, ...]]:
    baseline = base_load_forecast(day, history)
    sources = residual_source_days(day, history, window)
    if not sources:
        return baseline, ()
    residuals = []
    for source in sources:
        source_history = {prior: history[prior] for prior in history if prior < source}
        source_baseline = base_load_forecast(source, source_history)
        residuals.append(
            tuple(float(history[source][k]) - source_baseline[k] for k in range(POSITIONS_PER_DAY))
        )
    corrected = tuple(
        baseline[k] + math.fsum(residual[k] for residual in residuals) / len(residuals)
        for k in range(POSITIONS_PER_DAY)
    )
    if min(corrected) < -1e-9:
        raise RuntimeError(f"Q2_CORRECTED_LOAD_FORECAST_NEGATIVE:{day.isoformat()}")
    return tuple(max(0.0, value) for value in corrected), sources

class CorrectedPredictionProvider:
    def __init__(self, window: int) -> None:
        self.window = window
        self.future_leakage_count = 0
        self.bootstrap_events: list[dict[str, str]] = []

    @staticmethod
    def _history(information_set: Q2InformationSet, field: str) -> dict[date, tuple[float, ...]]:
        information_set.validate()
        return {
            record.day: (record.load_kw if field == "load" else record.pv_kw)
            for record in information_set.observed_history
        }

    def forecast_load(self, day: date, information_set: Q2InformationSet) -> Sequence[float]:
        history = self._history(information_set, "load")
        predicted, sources = corrected_load_forecast(day, history, self.window)
        leakage = sum(source >= day for source in sources)
        self.future_leakage_count += leakage
        if leakage:
            raise RuntimeError("Q2_CORRECTED_FORECAST_FUTURE_LEAKAGE")
        return predicted

    def forecast_pv(self, day: date, information_set: Q2InformationSet) -> Sequence[float]:
        history = self._history(information_set, "pv")
        predicted, sources = forecast_candidate(PV_CANDIDATE, day, history)
        leakage = sum(source >= day for source in sources)
        self.future_leakage_count += leakage
        if leakage:
            raise RuntimeError("Q2_PV_FORECAST_FUTURE_LEAKAGE")
        return predicted
