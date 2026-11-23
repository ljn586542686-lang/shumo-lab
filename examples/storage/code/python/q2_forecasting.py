"""Q2 简单候选的严格因果 January walk-forward 选择。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from math import isfinite, sqrt
from statistics import median
from typing import Mapping, Sequence

from q2_data_contract import POSITIONS_PER_DAY, Q2InformationSet


LOAD_CANDIDATES = (
    "L1_previous_day_same_position",
    "L2_previous_week_same_position",
    "L3_rolling_3day_mean_same_position",
    "L4_rolling_7day_mean_same_position",
    "L5_recent_same_weekday_mean",
)
PV_CANDIDATES = (
    "P1_previous_day_same_position",
    "P2_rolling_3day_mean",
    "P3_rolling_7day_mean",
    "P4_rolling_7day_median",
)
JAN_EVALUATION_START = date(2025, 1, 8)
JAN_EVALUATION_END = date(2025, 1, 31)
JANUARY_BOOTSTRAP_START = date(2025, 1, 1)
JANUARY_BOOTSTRAP_END = date(2025, 1, 31)
FORMAL_FORECAST_START_DATE = date(2025, 2, 1)
MAE_CLOSE_RELATIVE = 0.01


@dataclass(frozen=True)
class ForecastMetrics:
    candidate: str
    evaluation_days: int
    value_count: int
    mae_kw: float
    rmse_kw: float
    daily_energy_bias_kwh: float
    wape: float
    maximum_source_day: str
    future_leakage_count: int


@dataclass(frozen=True)
class ForecastSelection:
    series: str
    selected_candidate: str
    rule: str
    metrics: tuple[ForecastMetrics, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "series": self.series,
            "selected_candidate": self.selected_candidate,
            "rule": self.rule,
            "metrics": [asdict(item) for item in self.metrics],
        }


def _mean(vectors: Sequence[Sequence[float]]) -> tuple[float, ...]:
    if not vectors:
        raise ValueError("Q2_FORECAST_HISTORY_EMPTY")
    return tuple(sum(float(vector[k]) for vector in vectors) / len(vectors) for k in range(POSITIONS_PER_DAY))


def _candidate_sources(candidate: str, day: date, history_days: Sequence[date]) -> tuple[date, ...]:
    prior = tuple(sorted(item for item in history_days if item < day))
    if candidate.startswith(("L1_", "P1_")):
        return prior[-1:] if prior else ()
    if candidate.startswith("L2_"):
        target = date.fromordinal(day.toordinal() - 7)
        return (target,) if target in prior else ()
    if candidate.startswith(("L3_", "P2_")):
        return prior[-3:] if len(prior) >= 3 else ()
    if candidate.startswith(("L4_", "P3_", "P4_")):
        return prior[-7:] if len(prior) >= 7 else ()
    if candidate.startswith("L5_"):
        same_weekday = tuple(item for item in prior if item.weekday() == day.weekday())
        return same_weekday
    raise ValueError(f"Q2_FORECAST_CANDIDATE_UNKNOWN:{candidate}")


def forecast_candidate(
    candidate: str,
    day: date,
    history: Mapping[date, Sequence[float]],
) -> tuple[tuple[float, ...], tuple[date, ...]]:
    sources = _candidate_sources(candidate, day, tuple(history))
    if not sources:
        raise ValueError(f"Q2_FORECAST_HISTORY_INSUFFICIENT:{candidate}:{day.isoformat()}")
    if any(source >= day for source in sources):
        raise ValueError("Q2_FORECAST_FUTURE_LEAKAGE")
    vectors = tuple(history[source] for source in sources)
    if candidate.startswith("P4_"):
        predicted = tuple(median(float(vector[k]) for vector in vectors) for k in range(POSITIONS_PER_DAY))
    elif candidate.startswith(("L1_", "L2_", "P1_")):
        predicted = tuple(float(value) for value in vectors[-1])
    else:
        predicted = _mean(vectors)
    if len(predicted) != POSITIONS_PER_DAY or not all(isfinite(value) for value in predicted):
        raise ValueError("Q2_FORECAST_VECTOR_INVALID")
    if candidate.startswith("P"):
        predicted = tuple(max(0.0, value) for value in predicted)
    return predicted, sources


def _metrics(candidate: str, actual: Mapping[date, Sequence[float]]) -> ForecastMetrics:
    errors: list[float] = []
    daily_bias: list[float] = []
    actual_abs_sum = 0.0
    maximum_source: date | None = None
    leakage = 0
    days = tuple(
        day for day in sorted(actual)
        if JAN_EVALUATION_START <= day <= JAN_EVALUATION_END
    )
    for day in days:
        history = {source: actual[source] for source in actual if source < day}
        prediction, sources = forecast_candidate(candidate, day, history)
        leakage += sum(source >= day for source in sources)
        maximum_source = max((maximum_source, *sources)) if maximum_source is not None else max(sources)
        day_errors = tuple(prediction[k] - float(actual[day][k]) for k in range(POSITIONS_PER_DAY))
        errors.extend(day_errors)
        daily_bias.append(sum(day_errors) / 6.0)
        actual_abs_sum += sum(abs(float(value)) for value in actual[day])
    if not errors or actual_abs_sum <= 0.0:
        raise ValueError("Q2_FORECAST_METRIC_DENOMINATOR_INVALID")
    return ForecastMetrics(
        candidate=candidate,
        evaluation_days=len(days),
        value_count=len(errors),
        mae_kw=sum(abs(value) for value in errors) / len(errors),
        rmse_kw=sqrt(sum(value * value for value in errors) / len(errors)),
        daily_energy_bias_kwh=sum(daily_bias) / len(daily_bias),
        wape=sum(abs(value) for value in errors) / actual_abs_sum,
        maximum_source_day=maximum_source.isoformat() if maximum_source else "",
        future_leakage_count=leakage,
    )


def select_forecast_model(series: str, actual: Mapping[date, Sequence[float]]) -> ForecastSelection:
    candidates = LOAD_CANDIDATES if series == "load" else PV_CANDIDATES if series == "pv" else ()
    if not candidates:
        raise ValueError("Q2_FORECAST_SERIES_UNKNOWN")
    metrics = tuple(_metrics(candidate, actual) for candidate in candidates)
    minimum_mae = min(item.mae_kw for item in metrics)
    close = tuple(item for item in metrics if item.mae_kw <= minimum_mae * (1.0 + MAE_CLOSE_RELATIVE))
    selected = min(close, key=lambda item: (item.rmse_kw, abs(item.daily_energy_bias_kwh), candidates.index(item.candidate)))
    return ForecastSelection(
        series=series,
        selected_candidate=selected.candidate,
        rule="min MAE; within 1% compare RMSE, then abs daily_energy_bias, then declared order",
        metrics=metrics,
    )


class SelectedCausalPredictionProvider:
    """Use selected forecast models only after January selection is complete."""

    def __init__(self, load_candidate: str, pv_candidate: str) -> None:
        if load_candidate not in LOAD_CANDIDATES or pv_candidate not in PV_CANDIDATES:
            raise ValueError("Q2_SELECTED_FORECAST_CANDIDATE_INVALID")
        self.load_candidate = load_candidate
        self.pv_candidate = pv_candidate
        self.future_leakage_count = 0
        self.bootstrap_events: list[dict[str, str]] = []

    def _history(self, information_set: Q2InformationSet, field: str) -> dict[date, tuple[float, ...]]:
        information_set.validate()
        return {
            record.day: (record.load_kw if field == "load" else record.pv_kw)
            for record in information_set.observed_history
        }

    def _forecast(self, candidate: str, day: date, information_set: Q2InformationSet, field: str) -> tuple[float, ...]:
        if day < FORMAL_FORECAST_START_DATE:
            raise ValueError("Q2_SELECTED_MODEL_NOT_AVAILABLE_BEFORE_2025_02_01")
        history = self._history(information_set, field)
        try:
            prediction, sources = forecast_candidate(candidate, day, history)
        except ValueError as exc:
            if not str(exc).startswith("Q2_FORECAST_HISTORY_INSUFFICIENT"):
                raise
            if history:
                fallback = "L1_previous_day_same_position" if field == "load" else "P1_previous_day_same_position"
                prediction, sources = forecast_candidate(fallback, day, history)
                policy = "PREVIOUS_DAY_PERSISTENCE"
            else:
                prediction, sources = (0.0,) * POSITIONS_PER_DAY, ()
                policy = "ZERO_PLAN_NO_HISTORY"
            self.bootstrap_events.append({"day": day.isoformat(), "series": field, "policy": policy})
        leakage = sum(source >= day for source in sources)
        self.future_leakage_count += leakage
        if leakage:
            raise ValueError("Q2_FORECAST_FUTURE_LEAKAGE")
        return tuple(max(0.0, value) if field == "pv" else value for value in prediction)

    def forecast_load(self, day: date, information_set: Q2InformationSet) -> Sequence[float]:
        return self._forecast(self.load_candidate, day, information_set, "load")

    def forecast_pv(self, day: date, information_set: Q2InformationSet) -> Sequence[float]:
        return self._forecast(self.pv_candidate, day, information_set, "pv")


class JanuaryBootstrapPredictionProvider:
    """January-only technical bootstrap independent of final model selection."""

    load_method = "PREVIOUS_DAY_SAME_POSITION"
    pv_method = "PREVIOUS_DAY_SAME_POSITION"
    formal_prediction_method = False
    model_selection_future_leakage_guard = "PASS"

    def __init__(self) -> None:
        self.future_leakage_count = 0
        self.model_selection_future_leakage_count = 0
        self.bootstrap_events: list[dict[str, str]] = []

    @staticmethod
    def _history(information_set: Q2InformationSet, field: str) -> dict[date, tuple[float, ...]]:
        information_set.validate()
        return {
            record.day: (record.load_kw if field == "load" else record.pv_kw)
            for record in information_set.observed_history
        }

    def _forecast(self, day: date, information_set: Q2InformationSet, field: str) -> tuple[float, ...]:
        if not JANUARY_BOOTSTRAP_START <= day <= JANUARY_BOOTSTRAP_END:
            raise ValueError("Q2_JANUARY_BOOTSTRAP_DAY_OUT_OF_RANGE")
        history = self._history(information_set, field)
        if day == JANUARY_BOOTSTRAP_START:
            prediction = (0.0,) * POSITIONS_PER_DAY
            sources: tuple[date, ...] = ()
            policy = "ZERO_PLAN_NO_HISTORY"
        else:
            previous_day = date.fromordinal(day.toordinal() - 1)
            if previous_day not in history:
                raise ValueError(f"Q2_JANUARY_PREVIOUS_DAY_HISTORY_MISSING:{previous_day.isoformat()}")
            prediction = tuple(float(value) for value in history[previous_day])
            sources = (previous_day,)
            policy = "PREVIOUS_DAY_SAME_POSITION"
        leakage = sum(source >= day for source in sources)
        self.future_leakage_count += leakage
        if leakage:
            raise ValueError("Q2_FORECAST_FUTURE_LEAKAGE")
        self.bootstrap_events.append(
            {
                "day": day.isoformat(),
                "series": field,
                "policy": policy,
                "source_day": sources[0].isoformat() if sources else "NONE",
                "selected_model_dependency": "NO",
                "formal_prediction_method": "NO",
            }
        )
        return tuple(max(0.0, value) if field == "pv" else value for value in prediction)

    def forecast_load(self, day: date, information_set: Q2InformationSet) -> Sequence[float]:
        return self._forecast(day, information_set, "load")

    def forecast_pv(self, day: date, information_set: Q2InformationSet) -> Sequence[float]:
        return self._forecast(day, information_set, "pv")
