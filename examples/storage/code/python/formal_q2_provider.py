"""附件2与附件1电价的正式只读 Q2 provider；保持原列位置顺序。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from pathlib import Path

from openpyxl import load_workbook

from formal_q1_provider import load_formal_q1_inputs
from q2_data_contract import (
    ATTACHMENT2_DATE_RANGE,
    ATTACHMENT2_DAYS,
    ATTACHMENT2_SHA256,
    ATTACHMENT2_SHEETS,
    POSITIONS_PER_DAY,
    sha256_file,
)


@dataclass(frozen=True)
class FormalQ2Data:
    dates: tuple[date, ...]
    load_kw_by_day: dict[date, tuple[float, ...]]
    pv_kw_by_day: dict[date, tuple[float, ...]]
    price_per_kwh: tuple[float, ...]
    attachment2_sha256: str
    attachment1_sha256: str
    time_mapping: str = "POSITION_BASED"


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise ValueError("Q2_DATE_CELL_INVALID")


def _finite_nonnegative(value: object, field: str, row: int, column: int) -> float:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{field}_NOT_NUMERIC_AT_{row}_{column}")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}_NOT_NUMERIC_AT_{row}_{column}") from exc
    if not isfinite(numeric):
        raise ValueError(f"{field}_NONFINITE_AT_{row}_{column}")
    if numeric < 0.0:
        raise ValueError(f"{field}_NEGATIVE_AT_{row}_{column}")
    return numeric


def _load_series(sheet, field: str) -> tuple[tuple[date, ...], dict[date, tuple[float, ...]]]:
    if sheet.max_row != ATTACHMENT2_DAYS + 1 or sheet.max_column != POSITIONS_PER_DAY + 1:
        raise ValueError("ATTACHMENT2_DIMENSION_MISMATCH")
    dates: list[date] = []
    values: dict[date, tuple[float, ...]] = {}
    for row_index, row in enumerate(
        sheet.iter_rows(min_row=2, max_row=ATTACHMENT2_DAYS + 1, min_col=1, max_col=POSITIONS_PER_DAY + 1, values_only=True),
        start=2,
    ):
        day = _as_date(row[0])
        if day in values:
            raise ValueError("ATTACHMENT2_DUPLICATE_DATE")
        daily = tuple(
            _finite_nonnegative(value, field, row_index, column_index)
            for column_index, value in enumerate(row[1:], start=2)
        )
        dates.append(day)
        values[day] = daily
    return tuple(dates), values


def load_formal_q2_data(attachment2_path: str | Path, attachment1_path: str | Path) -> FormalQ2Data:
    attachment2 = Path(attachment2_path).resolve()
    if sha256_file(attachment2) != ATTACHMENT2_SHA256:
        raise ValueError("ATTACHMENT2_SHA256_MISMATCH")
    workbook = load_workbook(attachment2, read_only=True, data_only=False)
    try:
        if tuple(workbook.sheetnames) != ATTACHMENT2_SHEETS:
            raise ValueError("ATTACHMENT2_SHEET_MISMATCH")
        load_dates, load = _load_series(workbook[ATTACHMENT2_SHEETS[0]], "LOAD")
        pv_dates, pv = _load_series(workbook[ATTACHMENT2_SHEETS[1]], "PV")
        if load_dates != pv_dates:
            raise ValueError("ATTACHMENT2_SERIES_DATE_MISMATCH")
        if len(load_dates) != ATTACHMENT2_DAYS or (min(load_dates), max(load_dates)) != ATTACHMENT2_DATE_RANGE:
            raise ValueError("ATTACHMENT2_DATE_RANGE_MISMATCH")
    finally:
        workbook.close()
    q1_input = load_formal_q1_inputs(attachment1_path)
    return FormalQ2Data(
        dates=load_dates,
        load_kw_by_day=load,
        pv_kw_by_day=pv,
        price_per_kwh=q1_input.physical_inputs.price_per_kwh,
        attachment2_sha256=ATTACHMENT2_SHA256,
        attachment1_sha256=q1_input.source_sha256,
    )
