"""Model comparison / tournament evidence runner for X5-F5.3.

Design constraints (scope lock guardrails):
  - candidate_models MUST be supplied explicitly by the caller; never auto-generated.
  - metric direction MUST be explicit ('minimize' or 'maximize'); never inferred.
  - failed models (success=false) are excluded from leader/tie determination but
    retained in comparison_table with excluded_from_leader_selection=true.
  - Forbidden output keys: best_model, winner, overall_winner, auto_selected_model,
    recommended_model, recommendation, final_model, automl_result.
  - best_by_metric != best_model (it is a per-metric local evidence record only).
  - No cross-metric weighted scoring or overall ranking.
  - selection_reason_required and manual_review_required are blocking fields.
  - 2021B data is not used in F5.3 development.

Public API
----------
run_model_tournament(config, *, run_id) -> dict
    Run model comparison and return a full evidence report dict.

write_tournament_report(report, output_dir) -> tuple[Path, Path]
    Write model_comparison_report.json and model_comparison_report.csv.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

from ..validators import build_risk_item, utc_now

SCHEMA_VERSION = "x5_f5_3_comparison_v1"
DEFAULT_TIE_TOLERANCE = 1e-9

# ---------------------------------------------------------------------------
# Forbidden output keys (scope lock §1.5)
# ---------------------------------------------------------------------------
FORBIDDEN_OUTPUT_KEYS: frozenset[str] = frozenset({
    "best_model",
    "winner",
    "overall_winner",
    "auto_selected_model",
    "recommended_model",
    "recommendation",
    "final_model",
    "automl_result",
})

VALID_DIRECTIONS: frozenset[str] = frozenset({"minimize", "maximize"})

DISCLAIMER = (
    "Model comparison provides evidence only. "
    "best_by_metric != best_model. "
    "Metric-level leadership does not equal overall model optimality. "
    "Final model selection requires human judgement combining problem intent, "
    "interpretability, stability, constraint satisfaction, sensitivity results, "
    "and paper narrative. "
    "The system does not auto-select a final model, does not perform AutoML, "
    "and does not automatically establish academic validity."
)


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------

def run_model_tournament(
    config: dict[str, Any],
    *,
    run_id: str,
) -> dict[str, Any]:
    """Run model comparison and return a full evidence report dict.

    Required config keys:
        candidate_models   list of model result dicts (must be provided explicitly)
        metrics            list of {'metric_name': str, 'direction': 'minimize'|'maximize'}

    Optional config keys:
        tie_tolerance      float (default 1e-9)
        selection_reason   str | None
        manual_review_notes list[str] | None
    """
    risks: list[dict[str, Any]] = []
    manual_review_items: list[str] = []

    # --- candidate_models must be explicitly provided ---
    raw_candidates = config.get("candidate_models")
    if raw_candidates is None:
        manual_review_items.append("candidate_models not provided; must be supplied explicitly.")
        return _failure_report(
            run_id=run_id,
            failure_reason="missing_candidate_models",
            message="candidate_models must be provided explicitly; F5.3 does not auto-generate candidates.",
            manual_review_required=manual_review_items,
        )
    if not isinstance(raw_candidates, list) or len(raw_candidates) == 0:
        manual_review_items.append("candidate_models is empty.")
        return _failure_report(
            run_id=run_id,
            failure_reason="empty_candidate_models",
            message="candidate_models must be a non-empty list.",
            manual_review_required=manual_review_items,
        )

    # --- metrics must be explicitly provided with direction ---
    raw_metrics = config.get("metrics")
    if not raw_metrics or not isinstance(raw_metrics, list):
        manual_review_items.append("metrics not provided or empty.")
        return _failure_report(
            run_id=run_id,
            failure_reason="missing_metrics",
            message="metrics must be a non-empty list with explicit direction for each metric.",
            manual_review_required=manual_review_items,
        )

    tie_tolerance: float = float(config.get("tie_tolerance", DEFAULT_TIE_TOLERANCE))
    selection_reason: str | None = config.get("selection_reason") or None
    extra_manual_notes: list[str] = list(config.get("manual_review_notes") or [])
    manual_review_items.extend(extra_manual_notes)

    # --- validate metric definitions ---
    metric_defs: list[dict[str, Any]] = []
    has_direction_missing = False
    for mspec in raw_metrics:
        mname = str(mspec.get("metric_name") or "").strip()
        direction = str(mspec.get("direction") or "").strip().lower()
        if not mname:
            risks.append(build_risk_item(
                severity="warning", category="metric_definition",
                message="A metric entry is missing 'metric_name'; skipped.",
            ))
            continue
        if direction not in VALID_DIRECTIONS:
            has_direction_missing = True
            manual_review_items.append(
                f"metric '{mname}' has missing or invalid direction '{direction}'; "
                "must be 'minimize' or 'maximize'. F5.3 does not auto-infer direction."
            )
            metric_defs.append({"metric_name": mname, "direction": None, "direction_missing": True})
        else:
            metric_defs.append({"metric_name": mname, "direction": direction, "direction_missing": False})

    if has_direction_missing:
        manual_review_items.append(
            "metric_direction_missing=true: comparison cannot be completed for metrics without direction."
        )

    # --- failure contract: classify each candidate ---
    comparison_table: list[dict[str, Any]] = []
    valid_models: list[dict[str, Any]] = []
    failed_models: list[dict[str, Any]] = []

    for cand in raw_candidates:
        model_id = str(cand.get("model_id") or "").strip() or "(unknown)"
        model_name = str(cand.get("model_name") or model_id)
        model_type = str(cand.get("model_type") or "unknown")
        success = bool(cand.get("success", False))
        failure_reason = cand.get("failure_reason") or (None if success else "success=false")
        raw_metrics_vals: dict[str, Any] = dict(cand.get("metrics") or {})

        row: dict[str, Any] = {
            "model_id": model_id,
            "model_name": model_name,
            "model_type": model_type,
            "success": success,
            "status": "valid" if success else "failed",
            "excluded_from_leader_selection": not success,
            "failure_reason": failure_reason,
            "metrics": raw_metrics_vals,
        }
        comparison_table.append(row)
        if success:
            valid_models.append(row)
        else:
            failed_models.append(row)

    total_count = len(comparison_table)
    valid_count = len(valid_models)
    failed_count = len(failed_models)
    excluded_count = failed_count  # excluded_from_comparison == failed here

    model_counts: dict[str, int] = {
        "total": total_count,
        "valid": valid_count,
        "failed": failed_count,
        "excluded_from_comparison": excluded_count,
    }

    insufficient_valid = valid_count < 2
    if insufficient_valid:
        manual_review_items.append(
            f"insufficient_valid_models: only {valid_count} valid model(s) after failure contract check; "
            "at least 2 required for meaningful comparison."
        )

    # --- per-metric leader / tie determination (valid models only) ---
    best_by_metric: dict[str, Any] = {}
    ties: list[dict[str, Any]] = {}
    metric_leaders: dict[str, list[str]] = {}

    if not insufficient_valid and not has_direction_missing:
        for mdef in metric_defs:
            if mdef["direction_missing"]:
                continue
            mname = mdef["metric_name"]
            direction = mdef["direction"]

            # collect valid models that have this metric
            scored: list[tuple[float, str]] = []  # (value, model_id)
            for row in valid_models:
                raw_val = row["metrics"].get(mname)
                if raw_val is None:
                    continue
                try:
                    val = float(raw_val)
                    if math.isnan(val) or math.isinf(val):
                        continue
                    scored.append((val, row["model_id"]))
                except (TypeError, ValueError):
                    continue

            if not scored:
                best_by_metric[mname] = {"leaders": [], "ties": [], "note": "no valid metric values"}
                metric_leaders[mname] = []
                continue

            # find best value
            if direction == "minimize":
                best_val = min(v for v, _ in scored)
            else:
                best_val = max(v for v, _ in scored)

            leaders = []
            tie_group = []
            for val, mid in scored:
                if abs(val - best_val) <= tie_tolerance:
                    leaders.append({"model_id": mid, "metric_value": val})
                else:
                    # check if within tie_tolerance of any leader
                    pass

            # detect ties among all models within tie_tolerance of best
            in_tolerance = [(val, mid) for val, mid in scored if abs(val - best_val) <= tie_tolerance]
            is_tie = len(in_tolerance) > 1

            # delta = abs(val - best_val); consistent for both minimize and maximize
            leader_entries = [
                {"model_id": mid, "metric_value": val, "delta": abs(val - best_val)}
                for val, mid in in_tolerance
            ]
            leader_entry: dict[str, Any] = {
                "metric_name": mname,
                "direction": direction,
                "best_value": best_val,
                "leaders": leader_entries,
                "tied": is_tie,
                "indistinguishable_within_tolerance": is_tie,
                "tie_tolerance_used": tie_tolerance,
                "note": (
                    "best_by_metric reflects local metric leadership only. "
                    "best_by_metric != best_model. "
                    "best_by_metric != recommended_model. "
                    "This is not a final model selection."
                ),
            }
            best_by_metric[mname] = leader_entry
            metric_leaders[mname] = [mid for _, mid in in_tolerance]

            if is_tie:
                ties[mname] = {
                    "metric_name": mname,
                    "tie_group": [
                        {"model_id": mid, "metric_value": val, "delta": abs(val - best_val)}
                        for val, mid in in_tolerance
                    ],
                    "tie_tolerance": tie_tolerance,
                }
    elif insufficient_valid:
        for mdef in metric_defs:
            mname = mdef["metric_name"]
            best_by_metric[mname] = {
                "leaders": [],
                "note": "insufficient_valid_models: comparison not performed.",
            }
            metric_leaders[mname] = []

    # --- selection_reason / blocking fields ---
    selection_reason_required = not bool(selection_reason)
    final_selection_pending = selection_reason_required or bool(manual_review_items)

    if selection_reason_required:
        manual_review_items.append(
            "selection_reason is missing; selection_reason_required=true. "
            "F5.3 output cannot be marked final_selection_ready."
        )

    # --- build report ---
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "run_id": run_id,
        "tie_tolerance": tie_tolerance,
        "metric_definitions": metric_defs,
        "model_counts": model_counts,
        "comparison_table": comparison_table,
        "best_by_metric": best_by_metric,
        "metric_leaders": metric_leaders,
        "ties": ties,
        "candidate_models": [
            {"model_id": r["model_id"], "model_type": r["model_type"], "status": r["status"]}
            for r in comparison_table
        ],
        "selection_reason_required": selection_reason_required,
        "selection_reason": selection_reason,
        "manual_review_required": bool(manual_review_items),
        "manual_review_items": manual_review_items,
        "model_comparison_complete": not insufficient_valid and not has_direction_missing,
        "final_selection_pending_human_review": final_selection_pending,
        "insufficient_valid_models": insufficient_valid,
        "metric_direction_missing": has_direction_missing,
        "risk_items": risks,
        "limitations": [
            "Comparison reflects model performance within the given test fixture only.",
            "best_by_metric != best_model and != recommended_model.",
            "No cross-metric weighted scoring or overall ranking is performed.",
            "Failed models are excluded from leader determination but retained in comparison_table.",
            "Final model selection requires human review combining problem intent, "
            "interpretability, stability, constraint satisfaction, sensitivity results, "
            "and paper narrative.",
            "No 2021B data used in F5.3 development (reserved for F5.4).",
            "Current run uses E0 synthetic fixture (evidence_tier='E0').",
        ],
        "evidence_tier": "E0",
        "disclaimer": DISCLAIMER,
        "metadata": {
            "schema_version": SCHEMA_VERSION,
            "f5_stage": "F5.3",
            "model_tournament_performed": False,
            "automl_performed": False,
            "academic_validity_judged": False,
            "final_model_selected": False,
            "two_thousand_twenty_one_b_used": False,
        },
        "success": True,
    }

    # Paranoia check: verify no forbidden keys slipped in
    for fk in FORBIDDEN_OUTPUT_KEYS:
        assert fk not in report, f"SCOPE VIOLATION: forbidden key '{fk}' found in report"

    return report


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def write_tournament_report(
    report: dict[str, Any],
    output_dir: str | Path,
) -> tuple[Path, Path]:
    """Write model_comparison_report.json and model_comparison_report.csv.

    Returns (json_path, csv_path).
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    json_path = out / "model_comparison_report.json"
    csv_path = out / "model_comparison_report.csv"

    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    # CSV: one row per model × metric
    metric_defs = report.get("metric_definitions", [])
    comparison_table = report.get("comparison_table", [])
    best_by_metric = report.get("best_by_metric", {})

    global_tie_tolerance: float = float(report.get("tie_tolerance", DEFAULT_TIE_TOLERANCE))

    fieldnames = [
        "model_id", "model_name", "model_type", "status",
        "excluded_from_leader_selection", "failure_reason",
        "metric_name", "direction", "metric_value", "delta",
        "is_leader", "tied", "tie_tolerance",
    ]

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        f.write(f"# disclaimer: {report.get('disclaimer', '')}\n")
        for row in comparison_table:
            for mdef in metric_defs:
                mname = mdef["metric_name"]
                direction = mdef.get("direction") or "unknown"
                raw_val = row["metrics"].get(mname)
                metric_value = None
                if raw_val is not None:
                    try:
                        metric_value = float(raw_val)
                    except (TypeError, ValueError):
                        pass

                leaders_entry = best_by_metric.get(mname, {})
                best_val = leaders_entry.get("best_value")
                leader_ids = {ld["model_id"] for ld in leaders_entry.get("leaders", [])}
                is_leader = row["model_id"] in leader_ids and not row["excluded_from_leader_selection"]
                is_tied = leaders_entry.get("tied", False) and is_leader

                # delta: distance from best_value for this metric
                delta = None
                if metric_value is not None and best_val is not None:
                    delta = abs(metric_value - best_val)

                writer.writerow({
                    "model_id": row["model_id"],
                    "model_name": row.get("model_name", row["model_id"]),
                    "model_type": row["model_type"],
                    "status": row["status"],
                    "excluded_from_leader_selection": row["excluded_from_leader_selection"],
                    "failure_reason": row.get("failure_reason") or "",
                    "metric_name": mname,
                    "direction": direction,
                    "metric_value": metric_value,
                    "delta": delta,
                    "is_leader": is_leader,
                    "tied": is_tied,
                    "tie_tolerance": global_tie_tolerance,
                })

    return json_path, csv_path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _failure_report(
    *,
    run_id: str,
    failure_reason: str,
    message: str,
    manual_review_required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "run_id": run_id,
        "success": False,
        "failure_reason": failure_reason,
        "message": message,
        "metric_definitions": [],
        "model_counts": {"total": 0, "valid": 0, "failed": 0, "excluded_from_comparison": 0},
        "comparison_table": [],
        "best_by_metric": {},
        "metric_leaders": {},
        "ties": {},
        "candidate_models": [],
        "selection_reason_required": True,
        "selection_reason": None,
        "manual_review_required": True,
        "manual_review_items": manual_review_required or [],
        "model_comparison_complete": False,
        "final_selection_pending_human_review": True,
        "insufficient_valid_models": True,
        "metric_direction_missing": False,
        "risk_items": [
            build_risk_item(
                severity="fatal", category="model_tournament",
                message=message,
            )
        ],
        "limitations": [],
        "evidence_tier": "E0",
        "disclaimer": DISCLAIMER,
        "metadata": {
            "schema_version": SCHEMA_VERSION,
            "f5_stage": "F5.3",
            "model_tournament_performed": False,
            "automl_performed": False,
            "academic_validity_judged": False,
            "final_model_selected": False,
            "two_thousand_twenty_one_b_used": False,
        },
    }
