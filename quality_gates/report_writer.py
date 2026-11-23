"""Report writer for X5-F2.4 quality gates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from x5_surgical_utils import markdown_table, utc_now, write_json


def build_qg_report(results: list[dict[str, Any]], *, run_id: str, case_dir: Path) -> dict[str, Any]:
    hard_block_count = sum(1 for item in results if item.get("severity") == "HARD_BLOCK" and item.get("status") == "FAIL")
    soft_block_count = sum(1 for item in results if item.get("severity") == "SOFT_BLOCK" and item.get("status") == "FAIL")
    warning_count = sum(1 for item in results if item.get("status") in {"WARNING", "MANUAL_REVIEW_REQUIRED"})
    manual_items = [item for item in results if item.get("manual_review_required")]
    return {
        "schema_version": "x5_f2_4_paper_semantic_quality_gates_v1",
        "created_at": utc_now(),
        "run_id": run_id,
        "case_dir": str(case_dir),
        "promotion_allowed": hard_block_count == 0,
        "hard_block_count": hard_block_count,
        "soft_block_count": soft_block_count,
        "warning_count": warning_count,
        "manual_review_count": len(manual_items),
        "results": results,
    }


def build_manual_review_todo(results: list[dict[str, Any]], *, run_id: str) -> dict[str, Any]:
    items = []
    for item in results:
        if not item.get("manual_review_required"):
            continue
        row = {
            "rule_id": item.get("rule_id"),
            "severity": item.get("severity"),
            "status": item.get("status"),
            "message": item.get("message"),
            "repair_action": item.get("repair_action"),
            "evidence_files": item.get("evidence_files", []),
        }
        for extra_key in ["figure_id"]:
            if extra_key in item:
                row[extra_key] = item.get(extra_key)
        items.append(row)
    return {
        "schema_version": "x5_f2_4_manual_review_todo_v1",
        "created_at": utc_now(),
        "run_id": run_id,
        "item_count": len(items),
        "items": items,
        "note": "Manual review items are not auto-approved.",
    }


def write_qg_outputs(report: dict[str, Any], manual_todo: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "qg_report.json", report)
    write_json(output_dir / "manual_review_todo.json", manual_todo)
    rows = [
        {
            "rule_id": item.get("rule_id"),
            "severity": item.get("severity"),
            "status": item.get("status"),
            "promotion_allowed": item.get("promotion_allowed"),
            "manual_review_required": item.get("manual_review_required"),
            "message": item.get("message"),
        }
        for item in report.get("results", [])
    ]
    md = f"""# X5-F2.4 Paper Semantic Quality Gates

- run_id: `{report.get('run_id')}`
- promotion_allowed: `{str(report.get('promotion_allowed')).lower()}`
- hard_block_count: `{report.get('hard_block_count')}`
- soft_block_count: `{report.get('soft_block_count')}`
- warning_count: `{report.get('warning_count')}`
- manual_review_count: `{report.get('manual_review_count')}`

{markdown_table(rows, [('rule_id', 'Rule'), ('severity', 'Severity'), ('status', 'Status'), ('promotion_allowed', 'Promotion Allowed'), ('manual_review_required', 'Manual Review'), ('message', 'Message')])}
"""
    (output_dir / "qg_report.md").write_text(md, encoding="utf-8")
