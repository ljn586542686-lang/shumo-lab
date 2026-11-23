"""Manual review gate audit for X5-F4.1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from x5_surgical_utils import rel_path, utc_now, write_json


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def audit_manual_review(qg_report_path: Path, manual_review_path: Path, output_dir: Path) -> dict[str, Any]:
    qg_report = _load_json(qg_report_path)
    manual = _load_json(manual_review_path)
    manual_items = manual.get("items") if isinstance(manual.get("items"), list) else []
    manual_rule_ids = {item.get("rule_id") for item in manual_items if isinstance(item, dict)}
    required_manual = []
    skipped = []
    missing_manual = []
    for item in qg_report.get("results", []):
        if not isinstance(item, dict):
            continue
        if item.get("manual_review_required"):
            required_manual.append(item)
            if item.get("rule_id") not in manual_rule_ids:
                missing_manual.append(item)
        if item.get("status") == "SKIPPED":
            skipped.append(item)
    unreviewed_items = [
        {
            "rule_id": item.get("rule_id"),
            "status": item.get("status"),
            "message": item.get("message"),
            "category": "UNREVIEWED_SKIPPED" if item.get("status") == "SKIPPED" else "MISSING_MANUAL_REVIEW_ITEM",
        }
        for item in [*skipped, *missing_manual]
    ]
    payload = {
        "schema_version": "x5_f4_1_manual_review_audit_v1",
        "created_at": utc_now(),
        "qg_report_path": rel_path(qg_report_path),
        "manual_review_path": rel_path(manual_review_path),
        "manual_review_required_count": len(required_manual),
        "manual_review_todo_count": len(manual_items),
        "missing_manual_review_count": len(missing_manual),
        "skipped_count": len(skipped),
        "unreviewed_items": unreviewed_items,
        "manual_review_required_is_not_auto_pass": True,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "manual_review_audit.json", payload)
    lines = [
        "# Manual Review Gate Audit",
        "",
        f"- manual_review_required_count: `{payload['manual_review_required_count']}`",
        f"- manual_review_todo_count: `{payload['manual_review_todo_count']}`",
        f"- missing_manual_review_count: `{payload['missing_manual_review_count']}`",
        f"- skipped_count: `{payload['skipped_count']}`",
        "",
        "## Unreviewed Items",
        "",
    ]
    if unreviewed_items:
        lines.extend(f"- `{item['rule_id']}` {item['category']}: {item['message']}" for item in unreviewed_items)
    else:
        lines.append("None")
    (output_dir / "manual_review_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload
