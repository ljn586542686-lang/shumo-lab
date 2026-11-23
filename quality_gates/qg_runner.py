"""Runner for X5-F2.4 paper semantic quality gates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from x5_surgical_utils import ROOT

from .core_rules import run_core_rules
from .report_writer import build_manual_review_todo, build_qg_report, write_qg_outputs


def _read_config(case_dir: Path) -> dict[str, Any]:
    config_path = case_dir / "quality_gates.json"
    if not config_path.exists():
        return {}
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def run_quality_gates(
    *,
    case_dir: Path,
    run_dir: Path,
    run_id: str,
    root: Path = ROOT,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    config = _read_config(case_dir)
    results = run_core_rules(config, root=root, case_dir=case_dir, run_dir=run_dir)
    report = build_qg_report(results, run_id=run_id, case_dir=case_dir)
    manual_todo = build_manual_review_todo(results, run_id=run_id)
    write_qg_outputs(report, manual_todo, output_dir or (run_dir / "output" / "quality"))
    return report
