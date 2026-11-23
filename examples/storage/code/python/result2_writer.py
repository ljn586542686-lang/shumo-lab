"""Official Q2 result workbook writer and independent read-back validator."""

from __future__ import annotations

import json
import subprocess
from datetime import date, datetime
from hashlib import sha256
from math import fsum
from pathlib import Path
from typing import Sequence

from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel

from q2_data_contract import POSITIONS_PER_DAY, RESULT2_DATE_RANGE, RESULT2_DAYS, RESULT2_SHA256


EXPECTED_SHEETS = ("计划购电量", "充放电量", "紧急购电量")
BLOCK_LABELS = ("0:00-4:00", "4:00-8:00", "8:00-12:00", "12:00-16:00", "16:00-20:00", "20:00-24:00")
POSITIONS_PER_BLOCK = 24
NUMERICAL_TOLERANCE = 1e-7


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        converted = from_excel(value)
        return converted.date() if isinstance(converted, datetime) else converted
    raise ValueError("RESULT2_OUTPUT_DATE_INVALID")


def validate_ledger_for_result2(ledger: dict[str, object]) -> None:
    if ledger.get("status") != "PASS":
        raise ValueError("RESULT2_LEDGER_NOT_FORMALLY_ACCEPTED")
    scope = ledger.get("scope")
    daily = ledger.get("daily_results")
    if not isinstance(scope, dict) or scope.get("days") != RESULT2_DAYS:
        raise ValueError("RESULT2_LEDGER_SCOPE_INVALID")
    if not isinstance(daily, list) or len(daily) != RESULT2_DAYS:
        raise ValueError("RESULT2_LEDGER_DAY_COUNT_MISMATCH")
    dates = tuple(date.fromisoformat(str(item["day"])) for item in daily)
    if (dates[0], dates[-1]) != RESULT2_DATE_RANGE or len(set(dates)) != RESULT2_DAYS:
        raise ValueError("RESULT2_LEDGER_DATE_RANGE_MISMATCH")
    for item in daily:
        trajectories = item.get("trajectories")
        if not isinstance(trajectories, dict):
            raise ValueError("RESULT2_LEDGER_TRAJECTORIES_MISSING")
        for field in ("Q", "G", "H", "c", "d"):
            values = trajectories.get(field)
            if not isinstance(values, list) or len(values) != POSITIONS_PER_DAY:
                raise ValueError(f"RESULT2_LEDGER_POSITION_COUNT_MISMATCH:{field}")
        energy = trajectories.get("E")
        if not isinstance(energy, list) or len(energy) != POSITIONS_PER_DAY + 1:
            raise ValueError("RESULT2_LEDGER_SOC_PATH_COUNT_MISMATCH")


def write_result2(*, template_path: Path, ledger_path: Path, output_path: Path, javascript_writer: Path) -> dict[str, object]:
    if sha256_file(template_path).lower() != RESULT2_SHA256.lower():
        raise ValueError("RESULT2_TEMPLATE_SHA256_MISMATCH")
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    validate_ledger_for_result2(ledger)
    completed = subprocess.run(
        [
            "node",
            str(javascript_writer),
            "--template",
            str(template_path),
            "--ledger",
            str(ledger_path),
            "--output",
            str(output_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise RuntimeError(f"RESULT2_ARTIFACT_WRITER_FAILED:{completed.stderr.strip() or completed.stdout.strip()}")
    validation = validate_result2_output(output_path, ledger)
    if validation["status"] != "PASS":
        raise RuntimeError(f"RESULT2_INDEPENDENT_VALIDATION_FAILED:{validation['failures']}")
    return {**validation, "sha256": sha256_file(output_path), "writer_stdout": completed.stdout.strip()}


def _close(left: float, right: float, tolerance: float = NUMERICAL_TOLERANCE) -> bool:
    return abs(float(left) - float(right)) <= tolerance


def validate_result2_output(path: Path, ledger: dict[str, object]) -> dict[str, object]:
    validate_ledger_for_result2(ledger)
    daily: list[dict[str, object]] = ledger["daily_results"]
    failures: list[str] = []
    workbook = load_workbook(path, read_only=True, data_only=False)
    try:
        if tuple(workbook.sheetnames) != EXPECTED_SHEETS:
            failures.append("SHEET_NAMES")
        plan = workbook["计划购电量"]
        plan_rows = list(
            plan.iter_rows(min_row=1, max_row=RESULT2_DAYS + 1, min_col=1, max_col=147, values_only=True)
        )
        position_labels = tuple(plan_rows[0][1:145])
        if len(position_labels) != POSITIONS_PER_DAY or len(set(position_labels)) != POSITIONS_PER_DAY:
            failures.append("PLAN_POSITION_LABELS")
        plan_dates = tuple(_as_date(row[0]) for row in plan_rows[1:])
        if (plan_dates[0], plan_dates[-1], len(set(plan_dates))) != (*RESULT2_DATE_RANGE, RESULT2_DAYS):
            failures.append("PLAN_DATES")
        for row, day in zip(plan_rows[1:], daily):
            expected_q = tuple(float(value) for value in day["trajectories"]["Q"])
            actual_q = tuple(float(value) for value in row[1:145])
            if any(not _close(left, right) for left, right in zip(actual_q, expected_q)):
                failures.append(f"PLAN_VALUES:{day['day']}")
                break
            if not _close(float(row[145]), fsum(expected_q)):
                failures.append(f"PLAN_TOTAL:{day['day']}")
                break
            if not _close(float(row[146]), float(day["costs"]["planned_purchase_cost"])):
                failures.append(f"PLAN_COST:{day['day']}")
                break

        storage = workbook["充放电量"]
        storage_rows = list(
            storage.iter_rows(min_row=1, max_row=RESULT2_DAYS * 6 + 1, min_col=1, max_col=6, values_only=True)
        )
        for day_index, day in enumerate(daily):
            block_rows = storage_rows[1 + day_index * 6 : 1 + (day_index + 1) * 6]
            if _as_date(block_rows[0][0]) != date.fromisoformat(str(day["day"])):
                failures.append(f"STORAGE_DATE:{day['day']}")
                break
            for block, label in enumerate(BLOCK_LABELS):
                row = block_rows[block]
                start = block * POSITIONS_PER_BLOCK
                end = start + POSITIONS_PER_BLOCK
                if row[1] != label:
                    failures.append(f"STORAGE_BLOCK_LABEL:{day['day']}:{block}")
                    break
                expected_c = fsum(float(value) for value in day["trajectories"]["c"][start:end])
                expected_d = fsum(float(value) for value in day["trajectories"]["d"][start:end])
                if not _close(float(row[2]), expected_c):
                    failures.append(f"CHARGE_MAPPING:{day['day']}:{block}")
                    break
                if not _close(float(row[3]), expected_d):
                    failures.append(f"DISCHARGE_MAPPING:{day['day']}:{block}")
                    break
            if failures:
                break
            energy = day["trajectories"]["E"]
            if block_rows[0][4] != "0:00" or not _close(float(block_rows[0][5]), float(energy[0])):
                failures.append(f"SOC_START_MAPPING:{day['day']}")
                break
            if block_rows[1][4] != "24:00" or not _close(float(block_rows[1][5]), float(energy[-1])):
                failures.append(f"SOC_END_MAPPING:{day['day']}")
                break

        expected_emergency: list[tuple[str, object, float]] = []
        for day in daily:
            first_for_day = True
            for position, amount in enumerate(day["trajectories"]["H"]):
                if float(amount) > NUMERICAL_TOLERANCE:
                    expected_emergency.append((day["day"] if first_for_day else "", position_labels[position], float(amount)))
                    first_for_day = False
        emergency = workbook["紧急购电量"]
        actual_emergency: list[tuple[str, object, float]] = []
        for day_value, label, amount in emergency.iter_rows(min_row=2, min_col=1, max_col=3, values_only=True):
            if amount is None and label is None:
                continue
            actual_emergency.append(("" if day_value is None else _as_date(day_value).isoformat(), label, float(amount)))
        if len(actual_emergency) != len(expected_emergency):
            failures.append("EMERGENCY_ROW_COUNT")
        elif any(a[0] != e[0] or a[1] != e[1] or not _close(a[2], e[2]) for a, e in zip(actual_emergency, expected_emergency)):
            failures.append("EMERGENCY_MAPPING")
    finally:
        workbook.close()
    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "first_date": RESULT2_DATE_RANGE[0].isoformat(),
        "last_date": RESULT2_DATE_RANGE[1].isoformat(),
        "days": RESULT2_DAYS,
        "positions_per_day": POSITIONS_PER_DAY,
    }
