"""Minimal data health checks for the X5-F0 surgical runner.

The checker reads only data/raw under the selected case and writes reports to
the current run sandbox. It does not clean data, infer meaning, or modify case
outputs.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

from x5_surgical_utils import add_risk, markdown_table, rel_path, utc_now, write_json


READABLE_EXTENSIONS = {".csv", ".tsv", ".xlsx", ".xls"}
MAX_WARN_SIZE_BYTES = 200 * 1024 * 1024


def _import_pandas():
    try:
        import pandas as pd  # type: ignore

        return pd
    except Exception:
        return None


def _looks_garbled(values: list[Any]) -> bool:
    sample = " ".join(str(v) for v in values if v is not None)[:5000]
    if not sample:
        return False
    suspicious = sample.count("�") + sample.count("ï¿½") + sample.count("����")
    control = sum(1 for ch in sample if ord(ch) < 32 and ch not in "\r\n\t")
    return suspicious > 0 or control / max(len(sample), 1) > 0.02


def _duplicate_columns(columns: list[Any]) -> list[str]:
    names = [str(c) for c in columns]
    seen: set[str] = set()
    duplicates: list[str] = []
    for name in names:
        normalized = re.sub(r"\.\d+$", "", name)
        if normalized in seen and normalized not in duplicates:
            duplicates.append(normalized)
        seen.add(normalized)
    return duplicates


def _frame_health(file_path: Path, frame: Any, sheet_name: str | None = None) -> dict[str, Any]:
    rows = int(getattr(frame, "shape", [0, 0])[0])
    cols = int(getattr(frame, "shape", [0, 0])[1])
    all_empty_columns: list[str] = []
    duplicate_columns: list[str] = []
    nan_ratio = 0.0
    garbled = False
    if rows > 0 and cols > 0:
        try:
            null_cells = int(frame.isna().sum().sum())
            nan_ratio = null_cells / max(rows * cols, 1)
            all_empty_columns = [str(col) for col in frame.columns if bool(frame[col].isna().all())]
            duplicate_columns = _duplicate_columns(list(frame.columns))
            head_values = list(frame.columns) + frame.head(10).astype(str).values.flatten().tolist()
            garbled = _looks_garbled(head_values)
        except Exception:
            pass
    status = "pass"
    issues: list[str] = []
    if rows == 0 or cols == 0:
        status = "fail"
        issues.append("empty_table")
    if all_empty_columns:
        status = "warning" if status == "pass" else status
        issues.append("all_empty_columns")
    if duplicate_columns:
        status = "warning" if status == "pass" else status
        issues.append("duplicate_columns")
    if nan_ratio > 0.9:
        status = "fail"
        issues.append("nan_ratio_over_90_percent")
    elif nan_ratio > 0.5:
        status = "warning" if status == "pass" else status
        issues.append("nan_ratio_over_50_percent")
    if garbled:
        status = "warning" if status == "pass" else status
        issues.append("possible_garbled_text")
    return {
        "path": rel_path(file_path),
        "sheet": sheet_name,
        "rows": rows,
        "columns": cols,
        "nan_ratio": round(nan_ratio, 6),
        "all_empty_columns": all_empty_columns,
        "duplicate_columns": duplicate_columns,
        "possible_garbled_text": garbled,
        "issues": issues,
        "status": status,
    }


def _csv_fallback_health(path: Path, sep: str) -> list[dict[str, Any]]:
    last_exc: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with path.open("r", encoding=encoding, errors="replace", newline="") as f:
                rows = list(csv.reader(f, delimiter=sep))
            if not rows:
                return [
                    {
                        "path": rel_path(path),
                        "status": "fail",
                        "issues": ["empty_table"],
                        "rows": 0,
                        "columns": 0,
                        "nan_ratio": 0.0,
                    }
                ]
            header = rows[0]
            body = rows[1:]
            col_count = len(header)
            total_cells = max(len(body) * max(col_count, 1), 1)
            empty_cells = 0
            empty_by_col = [True for _ in range(col_count)]
            for row in body:
                for idx in range(col_count):
                    value = row[idx] if idx < len(row) else ""
                    if value == "":
                        empty_cells += 1
                    else:
                        empty_by_col[idx] = False
            all_empty_columns = [str(header[idx]) for idx, empty in enumerate(empty_by_col) if empty and body]
            duplicate_columns = _duplicate_columns(header)
            nan_ratio = empty_cells / total_cells if body else 0.0
            issues: list[str] = []
            status = "pass"
            if not body or col_count == 0:
                status = "fail"
                issues.append("empty_table")
            if all_empty_columns:
                status = "warning" if status == "pass" else status
                issues.append("all_empty_columns")
            if duplicate_columns:
                status = "warning" if status == "pass" else status
                issues.append("duplicate_columns")
            if nan_ratio > 0.9:
                status = "fail"
                issues.append("nan_ratio_over_90_percent")
            elif nan_ratio > 0.5:
                status = "warning" if status == "pass" else status
                issues.append("nan_ratio_over_50_percent")
            if _looks_garbled(header + [cell for row in body[:10] for cell in row]):
                status = "warning" if status == "pass" else status
                issues.append("possible_garbled_text")
            return [
                {
                    "path": rel_path(path),
                    "sheet": None,
                    "rows": len(body),
                    "columns": col_count,
                    "nan_ratio": round(nan_ratio, 6),
                    "all_empty_columns": all_empty_columns,
                    "duplicate_columns": duplicate_columns,
                    "possible_garbled_text": "possible_garbled_text" in issues,
                    "issues": issues,
                    "status": status,
                }
            ]
        except Exception as exc:
            last_exc = exc
    return [
        {
            "path": rel_path(path),
            "status": "fail",
            "issues": ["unreadable_table"],
            "error": str(last_exc) if last_exc else "csv fallback read failed",
            "size_bytes": path.stat().st_size,
        }
    ]


def _excel_fallback_health(path: Path) -> list[dict[str, Any]]:
    try:
        from openpyxl import load_workbook  # type: ignore
    except Exception:
        return [
            {
                "path": rel_path(path),
                "status": "warning",
                "issues": ["excel_reader_not_available"],
                "size_bytes": path.stat().st_size,
            }
        ]
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
        reports: list[dict[str, Any]] = []
        for sheet in wb.worksheets:
            rows = list(sheet.iter_rows(values_only=True))
            if not rows:
                reports.append(
                    {
                        "path": rel_path(path),
                        "sheet": sheet.title,
                        "status": "fail",
                        "issues": ["empty_table"],
                        "rows": 0,
                        "columns": 0,
                        "nan_ratio": 0.0,
                    }
                )
                continue
            header = [str(v) if v is not None else "" for v in rows[0]]
            body = rows[1:]
            col_count = len(header)
            total_cells = max(len(body) * max(col_count, 1), 1)
            empty_cells = 0
            empty_by_col = [True for _ in range(col_count)]
            for row in body:
                for idx in range(col_count):
                    value = row[idx] if idx < len(row) else None
                    if value is None or value == "":
                        empty_cells += 1
                    else:
                        empty_by_col[idx] = False
            nan_ratio = empty_cells / total_cells if body else 0.0
            all_empty_columns = [header[idx] for idx, empty in enumerate(empty_by_col) if empty and body]
            duplicate_columns = _duplicate_columns(header)
            issues: list[str] = []
            status = "pass"
            if not body or col_count == 0:
                status = "fail"
                issues.append("empty_table")
            if all_empty_columns:
                status = "warning" if status == "pass" else status
                issues.append("all_empty_columns")
            if duplicate_columns:
                status = "warning" if status == "pass" else status
                issues.append("duplicate_columns")
            if nan_ratio > 0.9:
                status = "fail"
                issues.append("nan_ratio_over_90_percent")
            elif nan_ratio > 0.5:
                status = "warning" if status == "pass" else status
                issues.append("nan_ratio_over_50_percent")
            reports.append(
                {
                    "path": rel_path(path),
                    "sheet": sheet.title,
                    "rows": len(body),
                    "columns": col_count,
                    "nan_ratio": round(nan_ratio, 6),
                    "all_empty_columns": all_empty_columns,
                    "duplicate_columns": duplicate_columns,
                    "possible_garbled_text": False,
                    "issues": issues,
                    "status": status,
                }
            )
        return reports
    except Exception as exc:
        return [{"path": rel_path(path), "status": "fail", "issues": ["unreadable_table"], "error": str(exc)}]


def _read_file_health(path: Path, pd: Any) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix not in READABLE_EXTENSIONS:
        return [
            {
                "path": rel_path(path),
                "status": "info",
                "issues": ["not_tabular_extension"],
                "size_bytes": path.stat().st_size,
            }
        ]
    if pd is None:
        if suffix in {".csv", ".tsv"}:
            return _csv_fallback_health(path, "\t" if suffix == ".tsv" else ",")
        return _excel_fallback_health(path)
    try:
        if suffix in {".csv", ".tsv"}:
            sep = "\t" if suffix == ".tsv" else None
            last_exc: Exception | None = None
            for encoding in ("utf-8-sig", "utf-8", "gb18030"):
                try:
                    frame = pd.read_csv(path, sep=sep, engine="python", encoding=encoding)
                    return [_frame_health(path, frame)]
                except Exception as exc:
                    last_exc = exc
            raise last_exc or RuntimeError("csv read failed")
        sheets = pd.read_excel(path, sheet_name=None)
        if not sheets:
            return [
                {
                    "path": rel_path(path),
                    "status": "fail",
                    "issues": ["empty_workbook"],
                    "size_bytes": path.stat().st_size,
                }
            ]
        return [_frame_health(path, frame, str(sheet)) for sheet, frame in sheets.items()]
    except Exception as exc:
        return [
            {
                "path": rel_path(path),
                "status": "fail",
                "issues": ["unreadable_table"],
                "error": str(exc),
                "size_bytes": path.stat().st_size,
            }
        ]


def _raw_data_required_from_case_config(case_dir: Path) -> bool:
    """Return False only for an explicit JSON boolean ``false`` setting."""

    try:
        payload = json.loads((case_dir / "case_config.json").read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return True
    if not isinstance(payload, dict):
        return True
    return payload.get("raw_data_required") is not False


def run_data_health_check(case_dir: Path, run_dir: Path) -> dict[str, Any]:
    raw_dir = case_dir / "data" / "raw"
    raw_data_required = _raw_data_required_from_case_config(case_dir)
    risks: list[dict[str, Any]] = []
    files: list[Path] = []
    file_reports: list[dict[str, Any]] = []
    if not raw_dir.exists():
        if raw_data_required:
            add_risk(
                risks,
                severity="ERROR",
                category="data_health",
                message="data/raw does not exist",
                fatal=True,
                path=rel_path(raw_dir),
            )
    else:
        files = sorted(path for path in raw_dir.rglob("*") if path.is_file())
        if raw_data_required and not files:
            add_risk(
                risks,
                severity="ERROR",
                category="data_health",
                message="data/raw exists but has no files",
                fatal=True,
                path=rel_path(raw_dir),
            )
    pd = _import_pandas()
    for path in files:
        size = path.stat().st_size
        if size == 0:
            file_reports.append({"path": rel_path(path), "status": "fail", "issues": ["zero_byte_file"], "size_bytes": 0})
            continue
        if size > MAX_WARN_SIZE_BYTES:
            file_reports.append(
                {
                    "path": rel_path(path),
                    "status": "warning",
                    "issues": ["file_size_over_200mb"],
                    "size_bytes": size,
                }
            )
        file_reports.extend(_read_file_health(path, pd))

    for item in file_reports:
        status = item.get("status")
        issues = ", ".join(item.get("issues", []))
        if status == "fail":
            add_risk(
                risks,
                severity="ERROR",
                category="data_health",
                message=f"fatal data health issue: {issues}",
                fatal=True,
                path=item.get("path"),
            )
        elif status == "warning":
            add_risk(
                risks,
                severity="WARNING",
                category="data_health",
                message=f"data health warning: {issues}",
                path=item.get("path"),
            )

    status = "fail" if any(item.get("fatal") for item in risks) else ("warning" if risks else "pass")
    payload = {
        "schema_version": "x5_f0_data_health_v1",
        "generated_at": utc_now(),
        "case_dir": rel_path(case_dir),
        "raw_dir": rel_path(raw_dir),
        "raw_data_required": raw_data_required,
        "status": status,
        "file_count": len(files),
        "files": file_reports,
        "risks": risks,
    }
    write_json(run_dir / "data_health_report.json", payload)
    rows = [
        {
            "path": item.get("path"),
            "sheet": item.get("sheet") or "",
            "status": item.get("status"),
            "rows": item.get("rows", ""),
            "columns": item.get("columns", ""),
            "nan_ratio": item.get("nan_ratio", ""),
            "issues": item.get("issues", []),
        }
        for item in file_reports
    ]
    md = f"""# X5-F0 Data Health Report

Status: `{status}`

- generated_at: `{payload['generated_at']}`
- case_dir: `{payload['case_dir']}`
- raw_dir: `{payload['raw_dir']}`
- raw_data_required: `{str(payload['raw_data_required']).lower()}`
- file_count: `{payload['file_count']}`

## File Checks

{markdown_table(rows, [('path', 'Path'), ('sheet', 'Sheet'), ('status', 'Status'), ('rows', 'Rows'), ('columns', 'Columns'), ('nan_ratio', 'NaN Ratio'), ('issues', 'Issues')])}
"""
    (run_dir / "data_health_report.md").write_text(md, encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="X5-F0 data health check")
    parser.add_argument("--case-dir", required=True)
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    payload = run_data_health_check(Path(args.case_dir), Path(args.run_dir))
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("status") != "fail" else 2


if __name__ == "__main__":
    raise SystemExit(main())
