"""Model-aware validation for math_core result_summary.json files."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from math_core.registry import get_model_spec

from .report_writer import validation_report_markdown
from .risk_policy import (
    HUMAN_REVIEW_ITEMS,
    SUPPORTED_MODEL_FAMILIES,
    hard_assertion,
    info,
    utc_now,
    validation_status,
    warning,
)


REQUIRED_FIELDS = ["success", "metrics", "primary_results", "risk_items", "fatal_count", "model_family", "model_id"]


def _bad_numbers(value: Any, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        findings.append(path)
    elif isinstance(value, dict):
        for key, child in value.items():
            findings.extend(_bad_numbers(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            findings.extend(_bad_numbers(child, f"{path}[{idx}]"))
    return findings


def _nested_get(payload: dict[str, Any], key: str) -> Any:
    primary = payload.get("primary_results")
    metrics = payload.get("metrics")
    if isinstance(primary, dict) and key in primary:
        return primary.get(key)
    if isinstance(metrics, dict) and key in metrics:
        return metrics.get(key)
    return None


def _risk_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload.get("risk_items", []), ensure_ascii=False).lower()


def _risk_fatal_count(payload: dict[str, Any]) -> int:
    risks = payload.get("risk_items")
    if not isinstance(risks, list):
        return 0
    return sum(1 for item in risks if isinstance(item, dict) and bool(item.get("fatal")))


def _has_key(container: Any, key: str) -> bool:
    return isinstance(container, dict) and key in container


def _base_report(
    *,
    run_id: str | None,
    model_id: str | None,
    model_family: str | None,
    hard_assertions: list[dict[str, Any]],
    soft_warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    fatal_count = sum(1 for item in hard_assertions if item.get("status") == "FAIL")
    warning_count = len(soft_warnings)
    return {
        "schema_version": "x5_f2_model_aware_validation_v1",
        "run_id": run_id,
        "model_id": model_id,
        "model_family": model_family,
        "validation_status": validation_status(fatal_count, warning_count),
        "hard_assertions": hard_assertions,
        "soft_warnings": soft_warnings,
        "human_review_items": [
            info(f"human_review_{idx + 1}", message) for idx, message in enumerate(HUMAN_REVIEW_ITEMS)
        ],
        "fatal_count": fatal_count,
        "warning_count": warning_count,
        "created_at": utc_now(),
    }


def _validate_common(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    hard: list[dict[str, Any]] = []
    soft: list[dict[str, Any]] = []
    success = payload.get("success")
    risk_items = payload.get("risk_items")
    model_family = payload.get("model_family")
    model_id = payload.get("model_id")

    hard.append(hard_assertion("success_field_bool", isinstance(success, bool), "success field must exist and be boolean"))
    missing = [field for field in REQUIRED_FIELDS if field not in payload]
    hard.append(
        hard_assertion(
            "required_contract_fields_present",
            not missing,
            "required fields present" if not missing else f"missing fields: {', '.join(missing)}",
        )
    )
    fatal_count = payload.get("fatal_count")
    actual_fatal = _risk_fatal_count(payload)
    hard.append(
        hard_assertion(
            "fatal_count_matches_risk_items",
            isinstance(risk_items, list) and fatal_count == actual_fatal,
            f"fatal_count={fatal_count}, risk_items fatal count={actual_fatal}",
        )
    )
    bad_paths = _bad_numbers(payload)
    hard.append(
        hard_assertion(
            "no_nan_inf",
            not bad_paths,
            "all numeric fields are finite" if not bad_paths else f"NaN/Inf at: {', '.join(bad_paths[:10])}",
        )
    )
    hard.append(
        hard_assertion(
            "failure_reason_when_failed",
            success is not False or bool(str(payload.get("failure_reason") or "").strip()),
            "success=false requires failure_reason",
        )
    )
    hard.append(
        hard_assertion(
            "success_true_requires_zero_fatal",
            success is not True or fatal_count == 0,
            "success=true requires fatal_count=0",
        )
    )
    hard.append(
        hard_assertion(
            "model_family_supported",
            model_family in SUPPORTED_MODEL_FAMILIES,
            f"model_family={model_family}",
        )
    )
    hard.append(
        hard_assertion(
            "model_id_registered",
            isinstance(model_id, str) and get_model_spec(model_id) is not None,
            f"model_id={model_id}",
        )
    )
    primary_results = payload.get("primary_results")
    hard.append(
        hard_assertion(
            "primary_results_non_empty_when_success",
            success is not True or (isinstance(primary_results, dict) and bool(primary_results)),
            "primary_results must be non-empty when success=true",
        )
    )
    if payload.get("limitations"):
        soft.append(warning("limitations_present", "model limitations are present and must be preserved"))
    return hard, soft


def _validate_optimization(payload: dict[str, Any], threshold: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    hard: list[dict[str, Any]] = []
    soft: list[dict[str, Any]] = []
    objective = _nested_get(payload, "objective_value")
    violation = _nested_get(payload, "constraint_violation")
    status = str(payload.get("status") or "").lower()
    failure_reason = str(payload.get("failure_reason") or "").lower()
    risk_text = _risk_text(payload)

    hard.append(hard_assertion("optimization_objective_value_present", objective is not None, "objective_value must exist"))
    hard.append(hard_assertion("optimization_constraint_violation_present", violation is not None, "constraint_violation must exist"))
    if violation is not None:
        hard.append(
            hard_assertion(
                "optimization_constraint_violation_threshold",
                isinstance(violation, (int, float)) and violation <= threshold,
                f"constraint_violation={violation}, threshold={threshold}",
            )
        )
    fatal_solver = any(token in f"{status} {failure_reason} {risk_text}" for token in ["infeasible", "unbounded", "solver_error"])
    hard.append(
        hard_assertion(
            "optimization_solver_status_not_fatal",
            not fatal_solver,
            "solver status must not be infeasible, unbounded, or solver_error",
        )
    )
    no_global_claim = "global_optimal" not in f"{status} {risk_text}"
    hard.append(
        hard_assertion(
            "optimization_no_global_optimal_claim",
            no_global_claim,
            "solver success may be treated as solver_success, not as global_optimal",
        )
    )
    if objective is not None and _nested_get(payload, "x") is None:
        soft.append(warning("x_missing_but_objective_present", "x vector is missing while objective_value exists"))
    return hard, soft


def _validate_statistics(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    hard: list[dict[str, Any]] = []
    soft: list[dict[str, Any]] = []
    r2 = _nested_get(payload, "R2")
    rmse = _nested_get(payload, "RMSE")
    residual_std = _nested_get(payload, "residual_std")
    condition_number = _nested_get(payload, "condition_number")
    risk_text = _risk_text(payload)
    hard.append(hard_assertion("statistics_r2_present", r2 is not None, "R2 must exist"))
    hard.append(hard_assertion("statistics_rmse_present", rmse is not None, "RMSE must exist"))
    if rmse is not None:
        hard.append(hard_assertion("statistics_rmse_nonnegative", isinstance(rmse, (int, float)) and rmse >= 0, f"RMSE={rmse}"))
    if "sample count" in risk_text or "insufficient_samples" in risk_text or "样本" in risk_text:
        hard.append(hard_assertion("statistics_sample_count_recheck", False, "sample count risk detected in math_core output"))
    if isinstance(r2, (int, float)) and not (0.0 <= r2 <= 1.0):
        soft.append(warning("r2_out_of_common_range", f"R2={r2} is outside common [0, 1] range"))
    elif isinstance(r2, (int, float)) and r2 < 0.2:
        soft.append(warning("r2_low", f"R2={r2} is low"))
    if isinstance(condition_number, (int, float)) and condition_number > 1e8:
        soft.append(warning("condition_number_high", f"condition_number={condition_number} is high"))
    if residual_std is None:
        soft.append(warning("residual_std_missing", "residual_std is missing"))
    elif isinstance(residual_std, (int, float)) and residual_std > 1e6:
        soft.append(warning("residual_std_high", f"residual_std={residual_std} is high"))
    return hard, soft


def _validate_forecasting(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    hard: list[dict[str, Any]] = []
    soft: list[dict[str, Any]] = []
    risk_text = _risk_text(payload)
    limitations_text = " ".join(str(item).lower() for item in payload.get("limitations", []))
    for metric in ["MAE", "RMSE", "MAPE"]:
        value = _nested_get(payload, metric)
        hard.append(hard_assertion(f"forecasting_{metric.lower()}_present", metric in (payload.get("metrics") or {}), f"{metric} must exist"))
        if isinstance(value, (int, float)):
            hard.append(hard_assertion(f"forecasting_{metric.lower()}_nonnegative", value >= 0, f"{metric}={value}"))
    if "horizon" in risk_text and ("invalid" in risk_text or "positive" in risk_text):
        hard.append(hard_assertion("forecasting_horizon_valid", False, "invalid horizon risk detected"))
    mape = _nested_get(payload, "MAPE")
    if isinstance(mape, (int, float)) and mape > 1.0:
        soft.append(warning("mape_high", f"MAPE={mape} is high"))
    if "baseline" not in limitations_text:
        soft.append(warning("baseline_model_limited", "forecasting limitations should explicitly state baseline capability"))
    return hard, soft


def _validate_graph(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    hard: list[dict[str, Any]] = []
    soft: list[dict[str, Any]] = []
    distance = _nested_get(payload, "shortest_distance")
    path = _nested_get(payload, "path")
    risk_text = _risk_text(payload)
    hard.append(hard_assertion("graph_shortest_distance_present", distance is not None, "shortest_distance must exist"))
    hard.append(hard_assertion("graph_path_present", path is not None, "path must exist"))
    if isinstance(distance, (int, float)):
        hard.append(hard_assertion("graph_shortest_distance_nonnegative", distance >= 0, f"shortest_distance={distance}"))
    if path is not None:
        hard.append(hard_assertion("graph_path_non_empty", isinstance(path, list) and bool(path), "path must be non-empty"))
    if "negative edge" in risk_text or "negative_edge" in risk_text:
        hard.append(hard_assertion("graph_negative_edge_not_present", False, "negative edge risk detected"))
    if "disconnected" in risk_text:
        hard.append(hard_assertion("graph_connected", False, "disconnected risk detected"))
    return hard, soft


def validate_result_summary(payload: dict[str, Any], *, constraint_violation_threshold: float = 1e-7) -> dict[str, Any]:
    hard, soft = _validate_common(payload)
    family = payload.get("model_family")
    if family == "optimization":
        family_hard, family_soft = _validate_optimization(payload, constraint_violation_threshold)
    elif family == "statistics":
        family_hard, family_soft = _validate_statistics(payload)
    elif family == "forecasting":
        family_hard, family_soft = _validate_forecasting(payload)
    elif family == "graph":
        family_hard, family_soft = _validate_graph(payload)
    elif family == "competition_q3":
        family_hard, family_soft = [], []
    else:
        family_hard, family_soft = [], [warning("model_family_support_limited", f"unsupported model_family={family}")]
    hard.extend(family_hard)
    soft.extend(family_soft)
    return _base_report(
        run_id=payload.get("run_id"),
        model_id=payload.get("model_id"),
        model_family=payload.get("model_family"),
        hard_assertions=hard,
        soft_warnings=soft,
    )


def validate_result_summary_file(
    path: Path,
    *,
    output_dir: Path | None = None,
    constraint_violation_threshold: float = 1e-7,
) -> dict[str, Any]:
    hard: list[dict[str, Any]] = []
    soft: list[dict[str, Any]] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("top-level JSON is not an object")
        hard.append(hard_assertion("result_summary_json_parseable", True, "result_summary.json parsed"))
        report = validate_result_summary(payload, constraint_violation_threshold=constraint_violation_threshold)
        report["hard_assertions"].insert(0, hard[0])
    except Exception as exc:
        hard.append(hard_assertion("result_summary_json_parseable", False, f"JSON parse failed: {exc}", path=str(path)))
        report = _base_report(
            run_id=None,
            model_id=None,
            model_family=None,
            hard_assertions=hard,
            soft_warnings=soft,
        )
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "validation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "validation_report.md").write_text(validation_report_markdown(report), encoding="utf-8")
    return report
