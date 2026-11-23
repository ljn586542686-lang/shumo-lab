"""Q1 求解结果到官方 ``result1.xlsx`` 模板的按位置写入适配器。

写入前复制官方模板；原模板只读。时间标签和格式不参与解析，且不被改写。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from math import isfinite
from pathlib import Path
from typing import Mapping, Sequence

from openpyxl import load_workbook

from formal_q1_provider import sha256_file


RESULT1_SHA256 = "28360e0974e7d6065394a8aba4e14a86773ae0036cc7d3ea1b211b515b03d688"
PURCHASE_SHEET = "计划购电量"
STORAGE_SHEET = "充放电量"
FORMAL_POSITION_COUNT = 144
STORAGE_BLOCK_COUNT = 6
POSITIONS_PER_STORAGE_BLOCK = 24


def _finite_sequence(values: object, field: str) -> tuple[float, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ValueError(f"{field}_SEQUENCE_REQUIRED")
    converted = tuple(float(value) for value in values)
    if len(converted) != FORMAL_POSITION_COUNT:
        raise ValueError(f"{field}_POSITION_COUNT_MISMATCH")
    if not all(isfinite(value) for value in converted):
        raise ValueError(f"{field}_NONFINITE")
    return converted


def _validated_solution(result: Mapping[str, object]) -> tuple[tuple[float, ...], ...]:
    if result.get("status") != "PASS":
        raise ValueError("Q1_RESULT_NOT_PASS")
    validation = result.get("scientific_validation")
    if not isinstance(validation, Mapping) or validation.get("status") != "PASS":
        raise ValueError("Q1_SCIENTIFIC_VALIDATION_NOT_PASS")
    solution = result.get("final_solution")
    if not isinstance(solution, Mapping):
        raise ValueError("Q1_FINAL_SOLUTION_MISSING")
    return (
        _finite_sequence(solution.get("x_kwh"), "GRID_PURCHASE"),
        _finite_sequence(solution.get("c_kwh"), "CHARGE"),
        _finite_sequence(solution.get("d_kwh"), "DISCHARGE"),
        _finite_sequence(solution.get("E_kwh"), "ENERGY"),
    )


def _validate_template_structure(workbook: object) -> None:
    if PURCHASE_SHEET not in workbook.sheetnames or STORAGE_SHEET not in workbook.sheetnames:
        raise ValueError("RESULT1_REQUIRED_SHEET_MISSING")
    purchase = workbook[PURCHASE_SHEET]
    storage = workbook[STORAGE_SHEET]
    if (purchase["A1"].value, purchase["B1"].value) != ("时间段", "购电量"):
        raise ValueError("RESULT1_PURCHASE_HEADER_MISMATCH")
    if tuple(storage.cell(row=1, column=column).value for column in range(1, 6)) != (
        "时间段",
        "充电量",
        "放电量",
        "时刻",
        "储电量",
    ):
        raise ValueError("RESULT1_STORAGE_HEADER_MISMATCH")
    if purchase.max_row != FORMAL_POSITION_COUNT + 1:
        raise ValueError("RESULT1_PURCHASE_POSITION_COUNT_MISMATCH")
    if storage.max_row != STORAGE_BLOCK_COUNT + 1:
        raise ValueError("RESULT1_STORAGE_BLOCK_COUNT_MISMATCH")


def write_result1_from_q1(
    template_path: str | Path,
    output_path: str | Path,
    result: Mapping[str, object],
    *,
    initial_energy_kwh: float = 6_000.0,
    expected_template_sha256: str = RESULT1_SHA256,
) -> dict[str, object]:
    """Copy the official template and write only the Q1 numeric output cells."""

    template = Path(template_path).resolve()
    destination = Path(output_path).resolve()
    if not template.is_file():
        raise FileNotFoundError("RESULT1_TEMPLATE_NOT_FOUND")
    actual_sha256 = sha256_file(template)
    if actual_sha256 != expected_template_sha256:
        raise ValueError("RESULT1_TEMPLATE_SHA256_MISMATCH")
    if destination == template:
        raise ValueError("RESULT1_TEMPLATE_OVERWRITE_FORBIDDEN")
    if destination.exists():
        raise FileExistsError("RESULT1_OUTPUT_ALREADY_EXISTS")

    x_kwh, c_kwh, d_kwh, energy_kwh = _validated_solution(result)
    initial_energy = float(initial_energy_kwh)
    if not isfinite(initial_energy):
        raise ValueError("INITIAL_ENERGY_NONFINITE")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_handle = tempfile.NamedTemporaryFile(
        prefix=f".{destination.stem}_",
        suffix=destination.suffix,
        dir=destination.parent,
        delete=False,
    )
    temp_path = Path(temp_handle.name)
    temp_handle.close()
    workbook = None
    try:
        shutil.copy2(template, temp_path)
        workbook = load_workbook(temp_path, data_only=False)
        _validate_template_structure(workbook)
        purchase = workbook[PURCHASE_SHEET]
        storage = workbook[STORAGE_SHEET]

        for k, value in enumerate(x_kwh, start=2):
            purchase.cell(row=k, column=2).value = value
        for block in range(STORAGE_BLOCK_COUNT):
            start = block * POSITIONS_PER_STORAGE_BLOCK
            stop = start + POSITIONS_PER_STORAGE_BLOCK
            storage.cell(row=block + 2, column=2).value = sum(c_kwh[start:stop])
            storage.cell(row=block + 2, column=3).value = sum(d_kwh[start:stop])
        storage["E2"] = initial_energy
        storage["E3"] = energy_kwh[-1]

        workbook.save(temp_path)
        workbook.close()
        workbook = None
        os.replace(temp_path, destination)
    except Exception:
        if workbook is not None:
            workbook.close()
        temp_path.unlink(missing_ok=True)
        raise

    return {
        "status": "PASS",
        "reason": "RESULT1_Q1_FIELDS_WRITTEN_POSITIONALLY",
        "template_sha256": actual_sha256,
        "output_path": str(destination),
        "purchase_sheet": PURCHASE_SHEET,
        "purchase_field": "B2:B145",
        "purchase_positions": FORMAL_POSITION_COUNT,
        "storage_sheet": STORAGE_SHEET,
        "storage_fields": "B2:C7,E2:E3",
    }
