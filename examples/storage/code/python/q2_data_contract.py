"""Q2 数据与因果信息合同；本模块不构造或调用求解器。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from math import isfinite
from pathlib import Path
from typing import Mapping, Protocol, Sequence


ATTACHMENT2_RELATIVE_PATH = Path("data/raw/附件2.xlsx")
RESULT2_RELATIVE_PATH = Path("data/raw/result2.xlsx")
ATTACHMENT2_SHA256 = "2e95fd446bfafa0d8c59577b5c2e2ea8b3f1def20dde54a3062556f4da9b4c72"
RESULT2_SHA256 = "1c26494cfc6d754e0bd9bff7e13e1126a73d2d2da6c5336eb251d89b9a1a1a47"
ATTACHMENT2_SHEETS = ("小区负载", "光伏发电实际功率")
RESULT2_SHEETS = ("计划购电量", "充放电量", "紧急购电量")
ATTACHMENT2_DATE_RANGE = (date(2025, 1, 1), date(2025, 12, 31))
RESULT2_DATE_RANGE = (date(2025, 2, 1), date(2025, 12, 31))
ATTACHMENT2_DAYS = 365
RESULT2_DAYS = 334
POSITIONS_PER_DAY = 144
DELTA_T_HOURS = 1.0 / 6.0
TIME_MAPPING = "POSITION_BASED"


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_formal_hashes(case_root: Path) -> dict[str, bool]:
    return {
        "attachment2": sha256_file(case_root / ATTACHMENT2_RELATIVE_PATH) == ATTACHMENT2_SHA256,
        "result2": sha256_file(case_root / RESULT2_RELATIVE_PATH) == RESULT2_SHA256,
    }


def kw_to_kwh(power_kw: Sequence[float]) -> tuple[float, ...]:
    values = tuple(float(value) for value in power_kw)
    if len(values) != POSITIONS_PER_DAY:
        raise ValueError("Q2_POSITION_COUNT_MISMATCH")
    if not all(isfinite(value) and value >= 0.0 for value in values):
        raise ValueError("Q2_POWER_VALUE_INVALID")
    return tuple(value * DELTA_T_HOURS for value in values)


@dataclass(frozen=True)
class DailyActualPower:
    day: date
    load_kw: tuple[float, ...]
    pv_kw: tuple[float, ...]

    def validate(self) -> None:
        kw_to_kwh(self.load_kw)
        kw_to_kwh(self.pv_kw)


@dataclass(frozen=True)
class Q2InformationSet:
    decision_day: date
    current_soc_kwh: float
    observed_history: tuple[DailyActualPower, ...]

    def validate(self) -> None:
        if not isfinite(self.current_soc_kwh):
            raise ValueError("Q2_CURRENT_SOC_NONFINITE")
        seen: set[date] = set()
        for observation in self.observed_history:
            observation.validate()
            if observation.day >= self.decision_day:
                raise ValueError("Q2_FUTURE_ACTUAL_LEAKAGE")
            if observation.day in seen:
                raise ValueError("Q2_HISTORY_DAY_DUPLICATED")
            seen.add(observation.day)


class PredictionProvider(Protocol):
    def forecast_load(self, day: date, information_set: Q2InformationSet) -> Sequence[float]: ...

    def forecast_pv(self, day: date, information_set: Q2InformationSet) -> Sequence[float]: ...


def build_information_set(
    decision_day: date,
    current_soc_kwh: float,
    observed_history: Mapping[date, tuple[Sequence[float], Sequence[float]]],
) -> Q2InformationSet:
    observations = tuple(
        DailyActualPower(day=day, load_kw=tuple(load), pv_kw=tuple(pv))
        for day, (load, pv) in sorted(observed_history.items())
    )
    information_set = Q2InformationSet(decision_day, float(current_soc_kwh), observations)
    information_set.validate()
    return information_set


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise ValueError("Q2_DATE_CELL_INVALID")


def audit_attachment2(path: Path) -> dict[str, object]:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=False)
    try:
        if tuple(workbook.sheetnames) != ATTACHMENT2_SHEETS:
            raise ValueError("ATTACHMENT2_SHEET_MISMATCH")
        summaries: dict[str, object] = {}
        for name in ATTACHMENT2_SHEETS:
            sheet = workbook[name]
            dates = tuple(_as_date(sheet.cell(row=row, column=1).value) for row in range(2, 367))
            if sheet.max_row != 366 or sheet.max_column != 145:
                raise ValueError("ATTACHMENT2_DIMENSION_MISMATCH")
            if len(set(dates)) != ATTACHMENT2_DAYS or (min(dates), max(dates)) != ATTACHMENT2_DATE_RANGE:
                raise ValueError("ATTACHMENT2_DATE_RANGE_MISMATCH")
            missing = nonnumeric = nonfinite = negative = 0
            for row in sheet.iter_rows(min_row=2, max_row=366, min_col=2, max_col=145, values_only=True):
                for value in row:
                    if value is None:
                        missing += 1
                    elif not isinstance(value, (int, float)):
                        nonnumeric += 1
                    elif not isfinite(float(value)):
                        nonfinite += 1
                    elif float(value) < 0.0:
                        negative += 1
            summaries[name] = {
                "days": len(dates),
                "positions_per_day": sheet.max_column - 1,
                "missing": missing,
                "nonnumeric": nonnumeric,
                "nonfinite": nonfinite,
                "negative": negative,
            }
        return {"sheets": tuple(workbook.sheetnames), "summaries": summaries}
    finally:
        workbook.close()


def audit_result2(path: Path) -> dict[str, object]:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=False)
    try:
        if tuple(workbook.sheetnames) != RESULT2_SHEETS:
            raise ValueError("RESULT2_SHEET_MISMATCH")
        plan = workbook["计划购电量"]
        dates = tuple(_as_date(plan.cell(row=row, column=1).value) for row in range(2, 336))
        if len(set(dates)) != RESULT2_DAYS or (min(dates), max(dates)) != RESULT2_DATE_RANGE:
            raise ValueError("RESULT2_DATE_RANGE_MISMATCH")
        headers = tuple(plan.cell(row=1, column=column).value for column in range(2, 146))
        if len(headers) != POSITIONS_PER_DAY or len(set(headers)) != POSITIONS_PER_DAY:
            raise ValueError("RESULT2_POSITION_HEADER_MISMATCH")
        return {
            "sheets": tuple(workbook.sheetnames),
            "date_range": RESULT2_DATE_RANGE,
            "days": len(dates),
            "positions_per_day": len(headers),
            "first_position_label": headers[0],
            "last_position_label": headers[-1],
            "fields": {
                "计划购电量": ("日期", "144个位置的计划购电量", "全天购电量", "全天购电费"),
                "充放电量": ("日期", "时间段", "充电量", "放电量", "时刻", "储电量"),
                "紧急购电量": ("日期", "购电时间段", "购电量"),
            },
        }
    finally:
        workbook.close()
