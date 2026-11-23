"""附件1到 Q1 ``PhysicalInputs`` 的正式、只读适配器。

本模块只按工作表中的物理位置读取 144 行数据并完成 kW 到 kWh 的单位换算；
不解析时间标签，不排序、不插值、不修正数据，也不调用求解器。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time
from math import isfinite
from pathlib import Path

from openpyxl import load_workbook

from model_contract import PhysicalInputs


ATTACHMENT1_SHEET = "Sheet1"
ATTACHMENT1_SHA256 = "66b87134f5ecccd68184d3539bb1293ef039f9e0fdd955a589b9bfa7f227c377"
ATTACHMENT1_HEADERS = ("时间", "电价", "小区负载", "光伏发电预测功率")
FORMAL_POSITION_COUNT = 144
POWER_TO_INTERVAL_ENERGY_HOURS = 1.0 / 6.0


def sha256_file(path: Path) -> str:
    """Return the lowercase SHA256 digest of one file."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite_number(value: object, field: str, position: int) -> float:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{field}_NOT_NUMERIC_AT_POSITION_{position}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}_NOT_NUMERIC_AT_POSITION_{position}") from exc
    if not isfinite(number):
        raise ValueError(f"{field}_NONFINITE_AT_POSITION_{position}")
    return number


def _audit_value(value: object) -> object:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


@dataclass(frozen=True)
class FormalQ1Input:
    """Physical input plus immutable evidence about the position-based parse."""

    physical_inputs: PhysicalInputs
    source_sha256: str
    sheet_name: str
    data_positions: int
    first_position_raw: tuple[object, ...]
    last_position_raw: tuple[object, ...]


def load_formal_q1_inputs(
    attachment_path: str | Path,
    *,
    expected_sha256: str = ATTACHMENT1_SHA256,
) -> FormalQ1Input:
    """Load official Attachment1 without interpreting its time-label column."""

    source = Path(attachment_path).resolve()
    if not source.is_file():
        raise FileNotFoundError("ATTACHMENT1_NOT_FOUND")
    actual_sha256 = sha256_file(source)
    if actual_sha256 != expected_sha256:
        raise ValueError("ATTACHMENT1_SHA256_MISMATCH")

    workbook = load_workbook(source, read_only=True, data_only=False)
    try:
        if ATTACHMENT1_SHEET not in workbook.sheetnames:
            raise ValueError("ATTACHMENT1_SHEET_MISSING")
        sheet = workbook[ATTACHMENT1_SHEET]
        headers = tuple(sheet.cell(row=1, column=column).value for column in range(1, 5))
        if headers != ATTACHMENT1_HEADERS:
            raise ValueError("ATTACHMENT1_HEADER_MISMATCH")
        if sheet.max_row != FORMAL_POSITION_COUNT + 1:
            raise ValueError("ATTACHMENT1_POSITION_COUNT_MISMATCH")

        rows = tuple(
            tuple(row)
            for row in sheet.iter_rows(
                min_row=2,
                max_row=FORMAL_POSITION_COUNT + 1,
                min_col=1,
                max_col=4,
                values_only=True,
            )
        )
        if len(rows) != FORMAL_POSITION_COUNT:
            raise ValueError("ATTACHMENT1_POSITION_COUNT_MISMATCH")

        price = tuple(_finite_number(row[1], "PRICE", k) for k, row in enumerate(rows, 1))
        load_kwh = tuple(
            _finite_number(row[2], "LOAD_POWER", k) * POWER_TO_INTERVAL_ENERGY_HOURS
            for k, row in enumerate(rows, 1)
        )
        pv_kwh = tuple(
            _finite_number(row[3], "PV_POWER", k) * POWER_TO_INTERVAL_ENERGY_HOURS
            for k, row in enumerate(rows, 1)
        )
        inputs = PhysicalInputs.from_sequences(load_kwh, pv_kwh, price)
        inputs.validate(FORMAL_POSITION_COUNT)
        return FormalQ1Input(
            physical_inputs=inputs,
            source_sha256=actual_sha256,
            sheet_name=ATTACHMENT1_SHEET,
            data_positions=len(rows),
            first_position_raw=tuple(_audit_value(value) for value in rows[0]),
            last_position_raw=tuple(_audit_value(value) for value in rows[-1]),
        )
    finally:
        workbook.close()
