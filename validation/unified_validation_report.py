"""F2.1 unified validation report patch.

This augments the existing F2 validation_report.json with HARD_FATAL,
WARNING, and HUMAN_REVIEW buckets. It does not replace model-aware validation
or turn semantic checks into machine fatal checks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from x5_surgical_utils import rel_path, utc_now, write_json


HUMAN_REVIEW_BASELINE = [
    "paper.tex handwritten numbers require manual reconciliation",
    "unit conversion requires manual review",
    "paper explanation requires manual review",
    "citation semantics require manual review",
    "model choice rationale requires manual review",
]


def _load_json(path: Path) -> tuple[Any, str | None]:
    if not path.exists():
        return None, "missing"
    try:
        return json.loads(path.read_text(encoding="utf-8-sig")), None
    except json.JSONDecodeError as exc:
        return None, str(exc)


def _hard_item(code: str, message: str, path: Path | None = None) -> dict[str, Any]:
    return {
        "category": "HARD_FATAL",
        "code": code,
        "message": message,
        "artifact_path": rel_path(path) if path else None,
        "fatal": True,
    }


def _warning_item(code: str, message: str, path: Path | None = None) -> dict[str, Any]:
    return {
        "category": "WARNING",
        "code": code,
        "message": message,
        "artifact_path": rel_path(path) if path else None,
        "fatal": False,
    }


def _human_item(code: str, message: str, path: Path | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    item = {
        "category": "HUMAN_REVIEW",
        "code": code,
        "message": message,
        "artifact_path": rel_path(path) if path else None,
        "fatal": False,
    }
    if extra:
        item.update(extra)
    return item


def _result_summary_checks(result_summary_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    hard: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    payload, error = _load_json(result_summary_path)
    if error == "missing":
        hard.append(_hard_item("result_summary_missing", "result_summary.json is missing", result_summary_path))
        return hard, warnings, None
    if error:
        hard.append(_hard_item("result_summary_parse_failed", f"result_summary.json parse failed: {error}", result_summary_path))
        return hard, warnings, None
    if not isinstance(payload, dict):
        hard.append(_hard_item("result_summary_top_level_invalid", "result_summary top level is not an object", result_summary_path))
        return hard, warnings, None
    if not payload:
        hard.append(_hard_item("result_summary_empty_object", "result_summary top level is empty", result_summary_path))
        return hard, warnings, payload
    required = ["run_id", "success", "status", "metrics", "primary_results", "risk_items", "fatal_count"]
    missing = [field for field in required if field not in payload]
    if missing:
        hard.append(_hard_item("required_fields_missing", f"required fields missing: {', '.join(missing)}", result_summary_path))
    text = json.dumps(payload, ensure_ascii=False, allow_nan=True)
    if "NaN" in text or "Infinity" in text or "-Infinity" in text:
        hard.append(_hard_item("nan_inf_detected", "result_summary contains NaN/Inf", result_summary_path))
    status_text = f"{payload.get('status')} {payload.get('failure_reason')} {payload.get('risk_items')}".lower()
    if any(token in status_text for token in ["infeasible", "divergent", "invalid status", "solver_error", "unbounded"]):
        hard.append(_hard_item("invalid_or_infeasible_status", "result_summary contains infeasible/divergent/invalid status", result_summary_path))
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        warnings.append(_warning_item("metrics_missing_or_empty", "metrics are missing or empty", result_summary_path))
    if not payload.get("units"):
        warnings.append(_warning_item("units_missing", "unit fields are missing; manual unit review required", result_summary_path))
    return hard, warnings, payload


def _merge_existing_validation(run_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    existing_path = run_dir / "validation_report.json"
    existing, error = _load_json(existing_path)
    hard: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    human: list[dict[str, Any]] = []
    if error or not isinstance(existing, dict):
        return hard, warnings, human
    for item in existing.get("hard_assertions", []):
        if item.get("status") == "FAIL":
            hard.append(_hard_item(str(item.get("name")), str(item.get("message")), existing_path))
    for item in existing.get("soft_warnings", []):
        warnings.append(_warning_item(str(item.get("name")), str(item.get("message")), existing_path))
    for item in existing.get("human_review_items", []):
        human.append(_human_item(str(item.get("name")), str(item.get("message")), existing_path))
    return hard, warnings, human


def _paper_number_human_items(path: Path) -> list[dict[str, Any]]:
    payload, error = _load_json(path)
    if error or not isinstance(payload, dict):
        return []
    items: list[dict[str, Any]] = []
    for candidate in payload.get("items", []):
        if not isinstance(candidate, dict):
            continue
        items.append(
            _human_item(
                "paper_number_candidate",
                f"TeX number candidate {candidate.get('value')} requires manual review",
                path,
                {
                    "source_path": candidate.get("source_path"),
                    "line": candidate.get("line"),
                    "value": candidate.get("value"),
                    "context": candidate.get("context"),
                },
            )
        )
    return items


def write_unified_validation_report(run_dir: Path, result_summary_path: Path) -> dict[str, Any]:
    hard, warnings, payload_source = _result_summary_checks(result_summary_path)
    existing_hard, existing_warnings, existing_human = _merge_existing_validation(run_dir)
    hard.extend(existing_hard)
    warnings.extend(existing_warnings)
    human = existing_human
    human.extend(_paper_number_human_items(run_dir / "paper_number_candidates.json"))
    if not human:
        human.extend(_human_item(f"human_review_{idx + 1}", message) for idx, message in enumerate(HUMAN_REVIEW_BASELINE))
    payload = {
        "schema_version": "x5_f2_1_unified_validation_report_v1",
        "created_at": utc_now(),
        "run_id": run_dir.name,
        "schema_adapted": bool(isinstance(payload_source, dict) and payload_source.get("schema_adapted")),
        "legacy_source": payload_source.get("legacy_source") if isinstance(payload_source, dict) else None,
        "validation_status": "FAIL" if hard else ("PASS_WITH_WARNING" if warnings else "PASS"),
        "categories": {
            "HARD_FATAL": hard,
            "WARNING": warnings,
            "HUMAN_REVIEW": human,
        },
        "counts": {
            "hard_fatal": len(hard),
            "warning": len(warnings),
            "human_review": len(human),
        },
        "forbidden_automation": [
            "No PDF OCR.",
            "No automatic semantic judgment.",
            "No citation readiness claim.",
            "HUMAN_REVIEW items are never fatal unless a separate machine-checkable error exists.",
        ],
    }
    write_json(run_dir / "unified_validation_report.json", payload)
    md = [
        "# X5-F2.1 Unified Validation Report",
        "",
        f"- run_id: `{payload['run_id']}`",
        f"- validation_status: `{payload['validation_status']}`",
        f"- hard_fatal: `{payload['counts']['hard_fatal']}`",
        f"- warning: `{payload['counts']['warning']}`",
        f"- human_review: `{payload['counts']['human_review']}`",
        "",
        "## HARD_FATAL",
        "",
    ]
    md.extend(f"- `{item['code']}`: {item['message']}" for item in hard)
    if not hard:
        md.append("None")
    md.extend(["", "## WARNING", ""])
    md.extend(f"- `{item['code']}`: {item['message']}" for item in warnings)
    if not warnings:
        md.append("None")
    md.extend(["", "## HUMAN_REVIEW", ""])
    md.extend(f"- `{item['code']}`: {item['message']}" for item in human)
    (run_dir / "unified_validation_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    existing_path = run_dir / "validation_report.json"
    existing, error = _load_json(existing_path)
    if isinstance(existing, dict):
        existing["f2_1_unified_categories"] = payload["categories"]
        existing["f2_1_unified_counts"] = payload["counts"]
        write_json(existing_path, existing)
    return payload
