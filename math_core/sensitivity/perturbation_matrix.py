"""Perturbation matrix runner for X5-F5.2 sensitivity / robustness evidence.

Design constraints (anti-AutoML / anti-tournament guardrails):
  - Only problem_parameters may be perturbed; solver_hyperparameters are rejected.
  - Only one model result may be analysed per run; multi-model input is rejected.
  - No best_model / winner / best_by_metric output is produced.
  - ranking reflects local sensitivity within the given grid only.
  - failed perturbation runs are excluded from ranking aggregation.

Public API
----------
run_perturbation_matrix(config, *, run_id) -> dict
    Execute the perturbation grid and return a full sensitivity report dict.

write_sensitivity_report(report, output_dir) -> tuple[Path, Path]
    Write report to sensitivity_report.json and sensitivity_report.csv.
"""

from __future__ import annotations

import csv
import json
import math
import random
import statistics
from pathlib import Path
from typing import Any

from ..validators import build_risk_item, utc_now

SCHEMA_VERSION = "x5_f5_2_sensitivity_v1"
DEFAULT_EPSILON = 1e-12
DEFAULT_TIE_TOLERANCE = 1e-6

RANKING_DISCLAIMER = (
    "Ranking reflects local sensitivity evidence within the given perturbation grid "
    "only. It does not imply global causal strength or global optimality. "
    "It does not represent a deterministic conclusion about parameter importance. "
    "It does not automatically establish academic validity of any model or result. "
    "Results must not be wrapped into deterministic model conclusions without further "
    "independent validation."
)

# ---------------------------------------------------------------------------
# Solver hyperparameter guard (anti-AutoML / anti-tournament)
# ---------------------------------------------------------------------------

_FORBIDDEN_HYPERPARAMETER_KEYS: frozenset[str] = frozenset({
    "method", "maxiter", "max_iter", "tol", "ftol", "gtol", "xtol",
    "solver", "optimizer", "optimizer_method",
    "options", "callback", "jac", "hess", "hessp",
    "bounds",           # solver bound, not problem param
    "constraints",      # solver constraint objects, not problem param
})

# Forbidden compound-name prefixes: reject "solver_hyperparameters.X" and "model.method" etc.
_FORBIDDEN_COMPOUND_PREFIXES: frozenset[str] = frozenset({
    "solver_hyperparameters",
    "solver_params",
})

# Forbidden compound-name second segments: reject "X.method", "X.maxiter" etc.
# (applies when first segment is NOT a problem-param namespace)
_FORBIDDEN_COMPOUND_SECOND: frozenset[str] = frozenset({
    "method", "maxiter", "max_iter", "tol", "ftol", "gtol", "xtol",
    "solver", "optimizer",
})


def _is_solver_hyperparameter(name: str) -> bool:
    """Return True if name looks like a solver hyperparameter (not a problem param).

    Handles:
      - plain names:  "method", "maxiter", "tol", "solver"
      - compound:     "solver_hyperparameters.method", "solver_params.tol"
      - model.method: "model.method"  (second segment is a solver key)
    """
    stripped = name.strip().lower()
    parts = stripped.split(".", 1)
    base = parts[0]

    # Plain-name match
    if base in _FORBIDDEN_HYPERPARAMETER_KEYS:
        return True

    # Forbidden compound prefix (e.g. "solver_hyperparameters.*")
    if base in _FORBIDDEN_COMPOUND_PREFIXES:
        return True

    # Compound name where the second segment is a forbidden key
    # e.g. "model.method", "config.tol", "options.maxiter"
    if len(parts) == 2 and parts[1] in _FORBIDDEN_COMPOUND_SECOND:
        return True

    return False


# ---------------------------------------------------------------------------
# Normalized delta
# ---------------------------------------------------------------------------

def _normalized_delta(metric_perturbed: float, metric_baseline: float, epsilon: float) -> float:
    return abs(metric_perturbed - metric_baseline) / max(abs(metric_baseline), epsilon)


# ---------------------------------------------------------------------------
# Grid expansion
# ---------------------------------------------------------------------------

def _expand_grid(
    param_spec: dict[str, Any],
    *,
    sampling_method: str,
    random_seed: int | None,
) -> list[Any]:
    """Return list of perturbation values for a single parameter spec."""
    if "values" in param_spec:
        values = list(param_spec["values"])
    elif "range" in param_spec:
        start, stop, n = param_spec["range"]
        if n < 2:
            values = [start]
        else:
            step = (stop - start) / (n - 1)
            values = [start + i * step for i in range(n)]
    else:
        return []

    if sampling_method == "random" and random_seed is not None:
        rng = random.Random(random_seed)
        rng.shuffle(values)

    return values


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------

def run_perturbation_matrix(
    config: dict[str, Any],
    *,
    run_id: str,
) -> dict[str, Any]:
    """Run the perturbation matrix and return the full sensitivity report dict.

    Required config keys:
        model_runner        callable(problem_params) -> float  (metric evaluator)
        baseline_params     dict of problem parameter name -> baseline value
        parameter_grid      list of param_spec dicts (see below)
        metric_name         str

    Optional config keys:
        epsilon             float  (default 1e-12)
        tie_tolerance       float  (default 1e-6)
        sampling_method     'OAT' | 'grid' | 'random'  (default 'OAT')
        random_seed         int | None
        multi_model_results list — FORBIDDEN; triggers manual_review_required
        solver_hyperparameters dict — any key overlap with parameter_grid triggers rejection

    Each param_spec in parameter_grid:
        parameter_name      str
        baseline_value      number
        perturbation_type   'absolute_offset' | 'relative_scale' | 'grid_value'
        values OR range     list of values OR [start, stop, n_steps]
        absolute_or_relative 'absolute' | 'relative'
        perturbation_scale  float (informational)
    """
    risks: list[dict[str, Any]] = []
    manual_review_required: list[str] = []

    # --- multi-model tournament guard ---
    if config.get("multi_model_results"):
        manual_review_required.append(
            "multi_model_results provided: cross-model comparison is not performed "
            "in F5.2 (reserved for F5.3 model tournament). Manual review required."
        )
        return _failure_report(
            run_id=run_id,
            failure_reason="multi_model_tournament_rejected",
            message="multi_model_results is not supported in F5.2 perturbation matrix.",
            manual_review_required=manual_review_required,
        )

    # --- required fields ---
    model_runner = config.get("model_runner")
    if not callable(model_runner):
        return _failure_report(
            run_id=run_id,
            failure_reason="missing_model_runner",
            message="model_runner must be a callable(problem_params) -> float.",
        )

    baseline_params: dict[str, Any] = dict(config.get("baseline_params") or {})
    parameter_grid: list[dict[str, Any]] = list(config.get("parameter_grid") or [])
    metric_name: str = str(config.get("metric_name") or "objective_value")

    if not parameter_grid:
        return _failure_report(
            run_id=run_id,
            failure_reason="empty_parameter_grid",
            message="parameter_grid must contain at least one entry.",
        )

    epsilon: float = float(config.get("epsilon", DEFAULT_EPSILON))
    tie_tolerance: float = float(config.get("tie_tolerance", DEFAULT_TIE_TOLERANCE))
    sampling_method: str = str(config.get("sampling_method") or "OAT")
    random_seed: int | None = config.get("random_seed")

    # --- baseline metric ---
    try:
        baseline_metric: float = float(model_runner(baseline_params))
        if math.isnan(baseline_metric) or math.isinf(baseline_metric):
            return _failure_report(
                run_id=run_id,
                failure_reason="baseline_metric_nan_or_inf",
                message=f"baseline model_runner returned NaN/Inf for metric '{metric_name}'.",
            )
    except Exception as exc:
        return _failure_report(
            run_id=run_id,
            failure_reason="baseline_runner_error",
            message=f"model_runner raised on baseline params: {exc}",
        )

    # --- perturbation loop ---
    perturbation_results: list[dict[str, Any]] = []
    rejected_params: list[dict[str, Any]] = []

    for spec in parameter_grid:
        param_name: str = str(spec.get("parameter_name") or "")
        if not param_name:
            risks.append(build_risk_item(
                severity="warning", category="parameter_grid",
                message="param_spec missing 'parameter_name', skipped.",
            ))
            continue

        # solver_hyperparameter guard (handles plain names AND compound names)
        if _is_solver_hyperparameter(param_name):
            rejected_params.append({
                "parameter_name": param_name,
                "reason": (
                    f"'{param_name}' is a solver hyperparameter or matches a forbidden "
                    "compound key pattern; not perturbed in F5.2"
                ),
                "action": "rejected",
            })
            manual_review_required.append(
                f"parameter '{param_name}' is a solver hyperparameter and was rejected. "
                "Solver hyperparameter sensitivity is not performed in F5.2."
            )
            continue

        baseline_value = spec.get("baseline_value", baseline_params.get(param_name))
        perturbation_type: str = str(spec.get("perturbation_type") or "grid_value")
        absolute_or_relative: str = str(spec.get("absolute_or_relative") or "absolute")
        perturbation_scale: float | None = spec.get("perturbation_scale")

        values = _expand_grid(spec, sampling_method=sampling_method, random_seed=random_seed)
        if not values:
            risks.append(build_risk_item(
                severity="warning", category="parameter_grid",
                message=f"parameter '{param_name}' has no values to perturb, skipped.",
            ))
            continue

        for pval in values:
            # Build perturbed params
            perturbed_params = dict(baseline_params)
            # Support dot-notation for nested keys (one level deep)
            parts = param_name.split(".", 1)
            if (
                len(parts) == 2
                and parts[0] in perturbed_params
                and isinstance(perturbed_params[parts[0]], dict)
            ):
                nested = dict(perturbed_params[parts[0]])
                nested[parts[1]] = pval
                perturbed_params[parts[0]] = nested
            else:
                perturbed_params[param_name] = pval

            # Run perturbed model — failure contract: track success per run
            perturbed_metric: float | None = None
            delta: float | None = None
            nd: float | None = None
            note: str | None = None
            run_success: bool = False
            run_failure_reason: str | None = None

            try:
                raw = model_runner(perturbed_params)
                pm = float(raw)
                if math.isnan(pm) or math.isinf(pm):
                    note = "NaN_or_Inf"
                    run_failure_reason = "perturbed_metric_nan_or_inf"
                else:
                    perturbed_metric = pm
                    delta = perturbed_metric - baseline_metric
                    nd = _normalized_delta(perturbed_metric, baseline_metric, epsilon)
                    run_success = True
            except Exception as exc:
                note = f"runner_error: {exc}"
                run_failure_reason = "runner_exception"

            failed_run: bool = not run_success
            excluded_from_ranking: bool = failed_run

            row: dict[str, Any] = {
                "parameter_name": param_name,
                "baseline_value": baseline_value,
                "perturbation_type": perturbation_type,
                "perturbation_values": [pval],
                "absolute_or_relative": absolute_or_relative,
                "perturbation_scale": perturbation_scale,
                "sampling_method": sampling_method,
                "random_seed": random_seed,
                "metric_name": metric_name,
                "baseline_metric": baseline_metric,
                "perturbed_metric": perturbed_metric,
                "delta": delta,
                "normalized_delta": nd,
                # failure contract fields
                "success": run_success,
                "failure_reason": run_failure_reason,
                "failed_run": failed_run,
                "excluded_from_ranking": excluded_from_ranking,
                "failure_contract_status": (
                    "pass" if run_success else f"excluded: {run_failure_reason}"
                ),
            }
            if note:
                row["note"] = note
            perturbation_results.append(row)

    # --- per-parameter aggregation: only success rows enter ranking ---
    param_agg: dict[str, list[float]] = {}
    param_total: dict[str, int] = {}
    param_failed: dict[str, int] = {}

    for row in perturbation_results:
        pname = row["parameter_name"]
        param_total[pname] = param_total.get(pname, 0) + 1
        if row["failed_run"]:
            param_failed[pname] = param_failed.get(pname, 0) + 1
        else:
            nd_val = row.get("normalized_delta")
            if nd_val is not None:
                param_agg.setdefault(pname, []).append(nd_val)

    ranking_raw: list[dict[str, Any]] = []
    for pname, nd_values in param_agg.items():
        n = len(nd_values)
        mean_nd = statistics.mean(nd_values) if n >= 1 else 0.0
        max_nd = max(nd_values) if n >= 1 else 0.0
        std_nd = statistics.stdev(nd_values) if n >= 2 else 0.0

        total = param_total.get(pname, n)
        failed = param_failed.get(pname, 0)
        failure_rate = failed / total if total > 0 else 0.0
        high_failure_rate = failure_rate > 0.5
        ranking_reliability = "limited" if high_failure_rate else "normal"

        ranking_raw.append({
            "parameter_name": pname,
            "mean_normalized_delta": mean_nd,
            "max_normalized_delta": max_nd,
            "std_normalized_delta": std_nd,
            "perturbation_count": n,
            "raw_normalized_delta_values": nd_values,
            "ranking_group": None,          # filled below
            "total_runs": total,
            "failed_runs": failed,
            "failure_rate": round(failure_rate, 6),
            "high_failure_rate": high_failure_rate,
            "ranking_reliability": ranking_reliability,
        })

    # Sort by mean_nd desc, then max_nd desc
    ranking_raw.sort(key=lambda r: (-r["mean_normalized_delta"], -r["max_normalized_delta"]))

    # Assign ranking groups (tie detection)
    if ranking_raw:
        group = 1
        ranking_raw[0]["ranking_group"] = group
        for i in range(1, len(ranking_raw)):
            prev = ranking_raw[i - 1]["mean_normalized_delta"]
            curr = ranking_raw[i]["mean_normalized_delta"]
            if abs(prev - curr) > tie_tolerance:
                group += 1
            ranking_raw[i]["ranking_group"] = group

    # --- run_counts summary ---
    total_runs = len(perturbation_results)
    failed_runs = sum(1 for r in perturbation_results if r["failed_run"])
    success_runs = total_runs - failed_runs
    excluded_runs = sum(1 for r in perturbation_results if r["excluded_from_ranking"])

    run_counts: dict[str, int] = {
        "total": total_runs,
        "success": success_runs,
        "failed": failed_runs,
        "excluded_from_ranking": excluded_runs,
    }

    # --- build report ---
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "run_id": run_id,
        "metric_name": metric_name,
        "baseline_metric": baseline_metric,
        "epsilon": epsilon,
        "tie_tolerance": tie_tolerance,
        "sampling_method": sampling_method,
        "random_seed": random_seed,
        "ranking_disclaimer": RANKING_DISCLAIMER,
        "parameter_grid": parameter_grid,
        "run_counts": run_counts,
        "metadata": {
            "schema_version": SCHEMA_VERSION,
            "f5_stage": "F5.2",
            "model_tournament_performed": False,
            "automl_performed": False,
            "academic_validity_judged": False,
        },
        "parameter_ranking": ranking_raw,
        "perturbation_results": perturbation_results,
        "rejected_params": rejected_params,
        "risk_items": risks,
        "manual_review_required": manual_review_required,
        "limitations": [
            "Ranking reflects local sensitivity within the given perturbation grid only.",
            "Does not imply global causal strength or academic validity.",
            "Failed perturbation runs are excluded from ranking; see run_counts and excluded_from_ranking fields.",
            "Current run uses E0 synthetic fixture. Real-case (E1+) anchor not yet validated.",
            "solver_hyperparameters (method, maxiter, tol, etc.) are intentionally excluded from perturbation.",
            "Multi-model comparison is not performed here; reserved for F5.3 model tournament.",
        ],
        "evidence_tier": "E0",
        "success": True,
    }
    return report


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def write_sensitivity_report(
    report: dict[str, Any],
    output_dir: str | Path,
) -> tuple[Path, Path]:
    """Write sensitivity_report.json and sensitivity_report.csv to output_dir.

    Returns (json_path, csv_path).
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    json_path = out / "sensitivity_report.json"
    csv_path = out / "sensitivity_report.csv"

    # Write JSON
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    # Write CSV — one row per perturbation result
    fieldnames = [
        "parameter_name", "baseline_value", "perturbation_type",
        "perturbation_values", "absolute_or_relative", "perturbation_scale",
        "sampling_method", "random_seed", "metric_name", "baseline_metric",
        "perturbed_metric", "delta", "normalized_delta",
        "success", "failure_reason", "failed_run", "excluded_from_ranking",
        "failure_contract_status", "note",
    ]
    rows = report.get("perturbation_results", [])
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        # Write disclaimer as a comment row
        f.write(f"# ranking_disclaimer: {report.get('ranking_disclaimer', '')}\n")
        for row in rows:
            flat_row = dict(row)
            if isinstance(flat_row.get("perturbation_values"), list):
                flat_row["perturbation_values"] = str(flat_row["perturbation_values"])
            writer.writerow(flat_row)

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
        "parameter_ranking": [],
        "perturbation_results": [],
        "rejected_params": [],
        "run_counts": {"total": 0, "success": 0, "failed": 0, "excluded_from_ranking": 0},
        "manual_review_required": manual_review_required or [],
        "risk_items": [
            build_risk_item(
                severity="fatal", category="perturbation_matrix",
                message=message,
            )
        ],
        "limitations": [],
        "evidence_tier": "E0",
        "metadata": {
            "schema_version": SCHEMA_VERSION,
            "f5_stage": "F5.2",
            "model_tournament_performed": False,
            "automl_performed": False,
            "academic_validity_judged": False,
        },
    }
