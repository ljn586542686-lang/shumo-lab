from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel


DAYS = 334
POSITIONS = 144
TOL = 1e-7
BLOCK_LABELS = ("0:00-4:00", "4:00-8:00", "8:00-12:00", "12:00-16:00", "16:00-20:00", "20:00-24:00")
SHEETS = ("计划购电量", "调整购电量", "充放电量", "紧急购电量")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        converted = from_excel(value)
        return converted.date() if isinstance(converted, datetime) else converted
    raise ValueError(f"INVALID_DATE:{value!r}")


def close(left: object, right: object) -> bool:
    return abs(float(left) - float(right)) <= TOL


def main(template_path: Path, ledger_path: Path, output_path: Path, report_path: Path) -> int:
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    daily = ledger["daily_results"]
    failures: list[str] = []
    if ledger.get("status") != "PASS" or len(daily) != DAYS:
        failures.append("LEDGER_SCOPE")
    dates = [date.fromisoformat(day["date"]) for day in daily]
    expected_dates = [date(2025, 2, 1).fromordinal(date(2025, 2, 1).toordinal() + index) for index in range(DAYS)]
    if dates != expected_dates:
        failures.append("LEDGER_DATES")
    if not all(day.get("day_scientific_validation", {}).get("status") == "PASS" for day in daily):
        failures.append("DAILY_SCIENTIFIC_VALIDATION")

    summary = ledger["summary"]
    cost_recompute = math.fsum(float(day["total_cost"]) for day in daily)
    emergency_recompute = math.fsum(math.fsum(float(value) for value in day["emergency"]) for day in daily)
    if not close(cost_recompute, summary["total_q3_cost"]):
        failures.append("TOTAL_COST_RECOMPUTE")
    if not close(emergency_recompute, summary["emergency_kwh"]):
        failures.append("EMERGENCY_KWH_RECOMPUTE")

    template = load_workbook(template_path, read_only=False, data_only=False)
    workbook = load_workbook(output_path, read_only=False, data_only=False)
    try:
        if tuple(workbook.sheetnames) != SHEETS or tuple(template.sheetnames) != SHEETS:
            failures.append("SHEET_NAMES")
        for name in SHEETS:
            if [cell.value for cell in template[name][1]] != [cell.value for cell in workbook[name][1]]:
                failures.append(f"HEADER:{name}")
        plan = workbook["计划购电量"]
        adjusted = workbook["调整购电量"]
        plan_template = template["计划购电量"]
        adjusted_template = template["调整购电量"]
        position_labels = [plan.cell(1, col).value for col in range(2, 146)]
        if len(position_labels) != POSITIONS or len(set(position_labels)) != POSITIONS:
            failures.append("POSITION_LABELS")

        for index, day in enumerate(daily, start=2):
            for sheet, field, expected_cost, source in (
                (plan, "plan_q0", float(day["initial_plan_cost"]), plan_template),
                (adjusted, "final_effective_plan", float(day["initial_plan_cost"]) + float(day["adjustment_cost"]), adjusted_template),
            ):
                try:
                    if as_date(sheet.cell(index, 1).value) != date.fromisoformat(day["date"]):
                        failures.append(f"DATE:{sheet.title}:{day['date']}")
                except Exception:
                    failures.append(f"DATE:{sheet.title}:{day['date']}")
                if sheet.cell(index, 1).number_format != source.cell(index, 1).number_format:
                    failures.append(f"DATE_FORMAT:{sheet.title}:{day['date']}")
                values = [sheet.cell(index, col).value for col in range(2, 146)]
                if any(value is None or not close(value, expected) for value, expected in zip(values, day[field])):
                    failures.append(f"VALUES:{sheet.title}:{day['date']}")
                if not close(sheet.cell(index, 146).value, math.fsum(float(v) for v in day[field])):
                    failures.append(f"TOTAL:{sheet.title}:{day['date']}")
                if not close(sheet.cell(index, 147).value, expected_cost):
                    failures.append(f"COST:{sheet.title}:{day['date']}")
            if failures:
                break

        storage = workbook["充放电量"]
        storage_template = template["充放电量"]
        for day_index, day in enumerate(daily):
            first_row = 2 + day_index * 6
            try:
                if as_date(storage.cell(first_row, 1).value) != date.fromisoformat(day["date"]):
                    failures.append(f"STORAGE_DATE:{day['date']}")
            except Exception:
                failures.append(f"STORAGE_DATE:{day['date']}")
            if storage.cell(first_row, 1).number_format != storage_template.cell(2, 1).number_format:
                failures.append(f"STORAGE_DATE_FORMAT:{day['date']}")
            for block, label in enumerate(BLOCK_LABELS):
                row = first_row + block
                if storage.cell(row, 2).value != label:
                    failures.append(f"STORAGE_LABEL:{day['date']}:{block}")
                start = block * 24
                end = start + 24
                if not close(storage.cell(row, 3).value, math.fsum(float(v) for v in day["charge"][start:end])):
                    failures.append(f"CHARGE:{day['date']}:{block}")
                if not close(storage.cell(row, 4).value, math.fsum(float(v) for v in day["discharge"][start:end])):
                    failures.append(f"DISCHARGE:{day['date']}:{block}")
            if storage.cell(first_row, 5).number_format != storage_template.cell(2, 5).number_format:
                failures.append(f"STORAGE_START_TIME_FORMAT:{day['date']}")
            if storage.cell(first_row + 1, 5).value != storage_template.cell(3, 5).value or storage.cell(first_row + 1, 5).number_format != storage_template.cell(3, 5).number_format:
                failures.append(f"STORAGE_END_TIME_FORMAT:{day['date']}")
            if not close(storage.cell(first_row, 6).value, day["soc_path"][0]) or not close(storage.cell(first_row + 1, 6).value, day["soc_path"][-1]):
                failures.append(f"STORAGE_SOC:{day['date']}")
            if failures:
                break

        expected_emergency: list[tuple[str, object, float]] = []
        for day in daily:
            first = True
            for position, amount in enumerate(day["emergency"]):
                if float(amount) > TOL:
                    expected_emergency.append((day["date"] if first else "", position_labels[position], float(amount)))
                    first = False
        emergency = workbook["紧急购电量"]
        emergency_template = template["紧急购电量"]
        actual_emergency: list[tuple[str, object, float]] = []
        for row in range(2, emergency.max_row + 1):
            date_value, label, amount = (emergency.cell(row, col).value for col in range(1, 4))
            if date_value is None and label is None and amount is None:
                continue
            try:
                date_text = "" if date_value is None else as_date(date_value).isoformat()
            except Exception:
                failures.append(f"EMERGENCY_DATE:{row}")
                date_text = "INVALID"
            actual_emergency.append((date_text, label, float(amount)))
            if date_value is not None and emergency.cell(row, 1).number_format != emergency_template.cell(2, 1).number_format:
                failures.append(f"EMERGENCY_DATE_FORMAT:{row}")
        if len(actual_emergency) != len(expected_emergency):
            failures.append("EMERGENCY_ROW_COUNT")
        elif any(a[0] != e[0] or a[1] != e[1] or not close(a[2], e[2]) for a, e in zip(actual_emergency, expected_emergency)):
            failures.append("EMERGENCY_MAPPING")
    finally:
        template.close()
        workbook.close()

    result = {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "scientific_validation": "PASS" if ledger.get("status") == "PASS" and len(daily) == DAYS and all(day.get("day_scientific_validation", {}).get("status") == "PASS" for day in daily) and close(cost_recompute, summary["total_q3_cost"]) and close(emergency_recompute, summary["emergency_kwh"]) else "FAIL",
        "template_mapping": "PASS" if not failures else "FAIL",
        "format_validation": "PASS" if not failures else "FAIL",
        "total_cost_recomputed": cost_recompute,
        "emergency_kwh_recomputed": emergency_recompute,
        "result3_sha256": sha256(output_path),
        "template_sha256": sha256(template_path),
    }
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


