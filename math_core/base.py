"""Shared result contracts for X5-F1 minimal math core."""

from __future__ import annotations

from typing import Any

from .validators import build_risk_item, sanitize_for_json, utc_now


RESULT_SCHEMA_VERSION = "x5_f1_math_core_result_summary_v1"
CANONICAL_RESULT_SCHEMA_ID = "x5_f0_canonical_result_schema_v1"


def make_result_summary(
    *,
    run_id: str,
    model_id: str,
    model_family: str,
    success: bool,
    status: str,
    metrics: dict[str, Any] | None = None,
    primary_results: dict[str, Any] | None = None,
    risk_items: list[dict[str, Any]] | None = None,
    limitations: list[str] | None = None,
    failure_reason: str | None = None,
    case_id: str | None = None,
) -> dict[str, Any]:
    risks = list(risk_items or [])
    safe_metrics, metric_risks = sanitize_for_json(metrics or {}, "$.metrics")
    safe_results, result_risks = sanitize_for_json(primary_results or {}, "$.primary_results")
    risks.extend(metric_risks)
    risks.extend(result_risks)

    fatal_count = sum(1 for item in risks if item.get("fatal"))
    if fatal_count:
        success = False
        if status in {"ok", "success", "solver_reported_optimum"}:
            status = "fatal"
        if not failure_reason:
            first_fatal = next((item for item in risks if item.get("fatal")), {})
            failure_reason = str(first_fatal.get("message") or "fatal risk detected")

    if not success and not failure_reason:
        failure_reason = "model run did not succeed"
        risks.append(
            build_risk_item(
                severity="fatal",
                category="result_contract",
                message=failure_reason,
            )
        )
        fatal_count += 1

    if not success and not risks:
        risks.append(
            build_risk_item(
                severity="fatal",
                category="result_contract",
                message="success=false requires at least one risk item",
            )
        )
        fatal_count += 1

    warning_count = sum(1 for item in risks if item.get("severity") == "warning")
    created_at = utc_now()
    payload = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "result_schema_id": CANONICAL_RESULT_SCHEMA_ID,
        "producer_schema_version": RESULT_SCHEMA_VERSION,
        "generated_at": created_at,
        "created_at": created_at,
        "run_id": run_id,
        "case_id": case_id or "math_core_case",
        "model_id": model_id,
        "model_family": model_family,
        "success": bool(success and fatal_count == 0),
        "status": status,
        "metrics": safe_metrics,
        "primary_results": safe_results,
        "risk_items": risks,
        "fatal_count": fatal_count,
        "warning_count": warning_count,
        "limitations": limitations or [],
        "final_submit_created": False,
    }
    if failure_reason:
        payload["failure_reason"] = failure_reason
    return payload
