"""Legacy result_summary adapter for X5-F4.

The adapter writes an adapted copy for validation. It never modifies the
legacy source file and it records every inferred or unavailable field in
warnings.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from x5_surgical_utils import rel_path, sha256_path, utc_now, write_json


ADAPTER_SCHEMA = "x5_f4_legacy_result_summary_adapter_v1"
ADAPTED_SCHEMA = "x5_f4_adapted_result_summary_v1"

MINIMUM_CONTRACT_FIELDS = [
    "schema_version",
    "case_id",
    "run_id",
    "created_at",
    "model_outputs",
    "metrics",
    "artifacts",
    "data_sources",
    "validation_status",
    "warnings",
    "fatal_items",
]

UNIFIED_VALIDATION_COMPAT_FIELDS = [
    "run_id",
    "success",
    "status",
    "metrics",
    "primary_results",
    "risk_items",
    "fatal_count",
]


def _load_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, "missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return None, str(exc)
    if not isinstance(payload, dict):
        return None, "top level is not an object"
    return payload, None


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _numeric_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    top_supplier = payload.get("top_supplier")
    if isinstance(top_supplier, dict):
        for key in [
            "baseline_importance_score",
            "fulfillment_rate",
            "supply_cv",
            "supply_stability_score",
            "rank",
        ]:
            if isinstance(top_supplier.get(key), (int, float)):
                metrics[key] = top_supplier[key]
    for key in ["supplier_count", "week_count", "top50_count"]:
        if isinstance(payload.get(key), (int, float)):
            metrics[key] = payload[key]
    raw_metrics = payload.get("metrics")
    if isinstance(raw_metrics, dict):
        metrics.update(raw_metrics)
    return metrics


def _primary_results(payload: dict[str, Any]) -> dict[str, Any]:
    fields = [
        "top_supplier",
        "top50_material_distribution",
        "validation_flags",
        "limitations",
        "model_scope",
        "case_status",
    ]
    return {field: payload[field] for field in fields if field in payload}


def _artifacts(source_path: Path) -> list[str]:
    output_dir = source_path.parent
    artifacts: list[str] = []
    if output_dir.exists():
        for rel in ["result_summary.json", "result_summary.txt", "result.csv"]:
            if (output_dir / rel).exists():
                artifacts.append(rel_path(output_dir / rel))
        for subdir in ["tables", "figures", "validation"]:
            base = output_dir / subdir
            if base.exists():
                artifacts.extend(rel_path(path) for path in sorted(base.rglob("*")) if path.is_file())
    return artifacts


def _data_sources(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("source_file") or payload.get("data_sources") or payload.get("source_data")
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item).strip()]
    return [str(raw)]


def detect_legacy_schema(source_path: Path) -> dict[str, Any]:
    payload, error = _load_json(source_path)
    if error:
        return {
            "legacy_schema_detected": False,
            "parse_error": error,
            "missing_minimum_fields": MINIMUM_CONTRACT_FIELDS,
            "missing_unified_validation_fields": UNIFIED_VALIDATION_COMPAT_FIELDS,
        }
    assert payload is not None
    missing_minimum = [field for field in MINIMUM_CONTRACT_FIELDS if field not in payload]
    missing_unified = [field for field in UNIFIED_VALIDATION_COMPAT_FIELDS if field not in payload]
    return {
        "legacy_schema_detected": bool(missing_minimum or missing_unified),
        "parse_error": None,
        "missing_minimum_fields": missing_minimum,
        "missing_unified_validation_fields": missing_unified,
    }


def adapt_result_summary(
    source_path: Path,
    output_dir: Path,
    *,
    synthetic_run_id: str | None = None,
) -> dict[str, Any]:
    """Write adapted_result_summary.json and adapter reports.

    Returns the adapter report payload. The source file is read-only.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    source_path = source_path.resolve()
    payload, error = _load_json(source_path)
    warnings: list[str] = []
    fatal_items: list[dict[str, Any]] = []
    synthetic = synthetic_run_id or f"adapted_{source_path.stem}"
    if error:
        fatal_items.append({"code": "source_unreadable", "message": error, "source_path": rel_path(source_path)})
        adapted = {
            "schema_version": ADAPTED_SCHEMA,
            "case_id": None,
            "run_id": synthetic,
            "created_at": utc_now(),
            "model_outputs": None,
            "metrics": None,
            "artifacts": [],
            "data_sources": [],
            "validation_status": "FAIL",
            "warnings": warnings,
            "fatal_items": fatal_items,
            "legacy_source": {
                "path": rel_path(source_path),
                "sha256": sha256_path(source_path) if source_path.exists() else None,
                "run_id": None,
                "synthetic_run_id": synthetic,
            },
            "schema_adapted": True,
            "success": False,
            "status": "fatal",
            "primary_results": None,
            "risk_items": fatal_items,
            "fatal_count": len(fatal_items),
        }
    else:
        assert payload is not None
        source_run_id = payload.get("run_id")
        case_id = _first_non_empty(payload.get("case_id"), payload.get("case"), payload.get("case_name"))
        created_at = _first_non_empty(payload.get("created_at"), payload.get("generated_at"), payload.get("timestamp"))
        if source_run_id is None:
            warnings.append("run_id missing in legacy source; synthetic_run_id was used.")
        if created_at is None:
            created_at = utc_now()
            warnings.append("created_at/generated_at missing in legacy source; adapter creation time was used.")
        if case_id is None:
            warnings.append("case_id missing and cannot be inferred.")
        metrics = _numeric_metrics(payload)
        if not metrics:
            warnings.append("metrics could not be inferred from legacy source.")
        primary_results = _primary_results(payload)
        if not primary_results:
            warnings.append("primary_results/model_outputs could not be inferred from legacy source.")
        data_sources = _data_sources(payload)
        if not data_sources:
            warnings.append("data_sources could not be inferred from legacy source.")
        validation_passed = payload.get("validation_passed")
        if isinstance(validation_passed, bool):
            validation_status = "PASS" if validation_passed else "FAIL"
            success = validation_passed
        else:
            validation_status = "UNKNOWN"
            success = None
            warnings.append("validation_passed missing; validation_status set to UNKNOWN.")
        if case_id is None or not primary_results:
            fatal_items.append(
                {
                    "code": "minimum_contract_unresolved",
                    "message": "case_id or model_outputs could not be inferred from legacy result_summary.",
                    "source_path": rel_path(source_path),
                }
            )
        adapted = {
            "schema_version": ADAPTED_SCHEMA,
            "case_id": case_id,
            "run_id": source_run_id or synthetic,
            "created_at": created_at,
            "model_outputs": primary_results or None,
            "metrics": metrics or None,
            "artifacts": _artifacts(source_path),
            "data_sources": data_sources,
            "validation_status": validation_status,
            "warnings": warnings,
            "fatal_items": fatal_items,
            "legacy_source": {
                "path": rel_path(source_path),
                "sha256": sha256_path(source_path),
                "schema_version": payload.get("schema_version"),
                "run_id": source_run_id,
                "synthetic_run_id": None if source_run_id else synthetic,
            },
            "schema_adapted": True,
            "success": success,
            "status": "ok" if success is True and not fatal_items else ("fatal" if fatal_items else "unknown"),
            "primary_results": primary_results or None,
            "risk_items": fatal_items,
            "fatal_count": len(fatal_items),
        }

    missing_after = [field for field in MINIMUM_CONTRACT_FIELDS if field not in adapted]
    if missing_after:
        fatal_items.append({"code": "adapter_contract_bug", "message": f"adapter missed fields: {', '.join(missing_after)}"})
        adapted["fatal_items"] = fatal_items
        adapted["risk_items"] = fatal_items
        adapted["fatal_count"] = len(fatal_items)
        adapted["status"] = "fatal"
        adapted["validation_status"] = "FAIL"

    adapted_path = output_dir / "adapted_result_summary.json"
    write_json(adapted_path, adapted)
    report = {
        "schema_version": ADAPTER_SCHEMA,
        "created_at": utc_now(),
        "source_path": rel_path(source_path),
        "adapted_path": rel_path(adapted_path),
        "schema_adapted": True,
        "legacy_schema_detected": detect_legacy_schema(source_path).get("legacy_schema_detected"),
        "missing_minimum_fields_before": detect_legacy_schema(source_path).get("missing_minimum_fields"),
        "missing_unified_validation_fields_before": detect_legacy_schema(source_path).get("missing_unified_validation_fields"),
        "warnings": warnings,
        "fatal_items": fatal_items,
        "validation_continue_allowed": len(fatal_items) == 0,
        "limitations": [
            "The adapter writes a copy and never modifies the legacy source.",
            "Unavailable fields are null or explicitly warned; no silent data fabrication.",
            "If minimum inference fails, fatal_items remains non-empty.",
        ],
    }
    write_json(output_dir / "result_summary_adapter_report.json", report)
    md = [
        "# X5-F4 Legacy Result Summary Adapter Report",
        "",
        f"- source_path: `{report['source_path']}`",
        f"- adapted_path: `{report['adapted_path']}`",
        f"- schema_adapted: `{str(report['schema_adapted']).lower()}`",
        f"- legacy_schema_detected: `{str(report['legacy_schema_detected']).lower()}`",
        f"- validation_continue_allowed: `{str(report['validation_continue_allowed']).lower()}`",
        "",
        "## Warnings",
        "",
    ]
    md.extend(f"- {item}" for item in warnings)
    if not warnings:
        md.append("None")
    md.extend(["", "## Fatal Items", ""])
    md.extend(f"- `{item.get('code')}`: {item.get('message')}" for item in fatal_items)
    if not fatal_items:
        md.append("None")
    (output_dir / "result_summary_adapter_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return report
