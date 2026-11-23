"""Validate result_summary outputs."""

from __future__ import annotations

import json
from pathlib import Path


def validate_result_summary(case_dir: str | Path) -> dict:
    case_dir = Path(case_dir)
    txt = case_dir / "output" / "result_summary.txt"
    js = case_dir / "output" / "result_summary.json"
    report = {
        "result_summary_txt_exists": txt.exists(),
        "result_summary_json_exists": js.exists(),
        "json_parse_ok": False,
        "status": "fail",
    }
    if js.exists():
        try:
            json.loads(js.read_text(encoding="utf-8"))
            report["json_parse_ok"] = True
        except json.JSONDecodeError as exc:
            report["json_error"] = str(exc)
    if report["result_summary_txt_exists"] and report["json_parse_ok"]:
        report["status"] = "ok"
    return report
