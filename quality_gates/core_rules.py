"""Core quality gate rules for X5-F2.2/F2.3/F2.4."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


STATUSES = {"PASS", "FAIL", "WARNING", "SKIPPED", "MANUAL_REVIEW_REQUIRED"}


def make_result(
    *,
    rule_id: str,
    severity: str,
    status: str,
    evidence_files: list[str] | None = None,
    message: str,
    repair_action: str,
    manual_review_required: bool = False,
    promotion_allowed: bool = True,
    **extra: Any,
) -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"Unsupported quality gate status: {status}")
    result = {
        "rule_id": rule_id,
        "severity": severity,
        "status": status,
        "evidence_files": evidence_files or [],
        "message": message,
        "repair_action": repair_action,
        "manual_review_required": manual_review_required,
        "promotion_allowed": promotion_allowed,
    }
    result.update(extra)
    return result


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_json_any(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return None


def _list_from_payload(payload: Any, *keys: str) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _read_draft_text(case_dir: Path) -> tuple[str, list[str]]:
    chunks: list[str] = []
    evidence: list[str] = []
    paper_dir = case_dir / "paper" / "draft"
    if paper_dir.exists():
        for pattern in ["*.tex", "*.md", "*.txt"]:
            for path in sorted(paper_dir.rglob(pattern)):
                chunks.append(path.read_text(encoding="utf-8", errors="replace"))
                evidence.append(path.as_posix())
    return "\n".join(chunks), evidence


def _as_input_entry(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        return {"path": raw, "usage": "model"}
    if isinstance(raw, dict):
        entry = dict(raw)
        if "path" not in entry and "name" in entry:
            entry["path"] = entry["name"]
        return entry
    return {"path": str(raw), "usage": "model"}


def _norm(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().lower()


def rule_data_01(config: dict[str, Any], *, root: Path) -> dict[str, Any]:
    declared_raw = config.get("declared_key_inputs")
    consumed_raw = config.get("consumed_inputs") or config.get("model_consumed_inputs") or []
    if not declared_raw:
        return make_result(
            rule_id="QG-DATA-01",
            severity="HARD_BLOCK",
            status="WARNING",
            message="declared_key_inputs is missing; data completeness cannot be machine-confirmed",
            repair_action="Declare key inputs and consumed_inputs in quality_gates.json.",
            manual_review_required=True,
            promotion_allowed=True,
        )
    declared = [_as_input_entry(item) for item in declared_raw]
    consumed = {_norm(item.get("path") if isinstance(item, dict) else item) for item in consumed_raw}
    missing_hard: list[str] = []
    descriptive: list[str] = []
    for item in declared:
        path = _norm(item.get("path"))
        if not path or path in consumed:
            continue
        if item.get("usage") == "descriptive_only" and str(item.get("reason") or "").strip():
            descriptive.append(str(item.get("path")))
        else:
            missing_hard.append(str(item.get("path")))
    evidence = [str(item.get("path")) for item in declared if item.get("path")]
    if missing_hard:
        return make_result(
            rule_id="QG-DATA-01",
            severity="HARD_BLOCK",
            status="FAIL",
            evidence_files=evidence,
            message=f"Declared key inputs were not consumed by model or validation: {', '.join(missing_hard)}",
            repair_action="Consume declared key inputs or mark descriptive_only with an explicit reason.",
            manual_review_required=True,
            promotion_allowed=False,
        )
    if descriptive:
        return make_result(
            rule_id="QG-DATA-01",
            severity="HARD_BLOCK",
            status="WARNING",
            evidence_files=evidence,
            message=f"Some declared inputs are descriptive_only and require manual review: {', '.join(descriptive)}",
            repair_action="Confirm descriptive-only inputs are not required for model computation.",
            manual_review_required=True,
            promotion_allowed=True,
        )
    return make_result(
        rule_id="QG-DATA-01",
        severity="HARD_BLOCK",
        status="PASS",
        evidence_files=evidence,
        message="Declared key inputs are covered by consumed_inputs.",
        repair_action="No action required.",
    )


def _near_boundary(value: Any, lower: Any, upper: Any) -> bool:
    try:
        x = float(value)
        lo = float(lower)
        hi = float(upper)
    except (TypeError, ValueError):
        return False
    span = abs(hi - lo)
    if span <= 0:
        return False
    return min(abs(x - lo), abs(hi - x)) / span <= 0.05


def _prediction_risky(prediction: dict[str, Any]) -> bool:
    risky = False
    try:
        pred = float(prediction.get("pred_value"))
        train_min = prediction.get("train_target_min")
        train_max = prediction.get("train_target_max")
        if train_min is not None and pred < float(train_min):
            risky = True
        if train_max is not None and pred > float(train_max):
            risky = True
        max_observed = prediction.get("max_observed_value")
        if max_observed is not None and float(max_observed) != 0 and pred / float(max_observed) > 1.3:
            risky = True
    except (TypeError, ValueError):
        pass
    if _near_boundary(
        prediction.get("recommended_x"),
        prediction.get("observed_x_min"),
        prediction.get("observed_x_max"),
    ):
        risky = True
    return risky


def _has_extrapolation_disclosure(prediction: dict[str, Any]) -> bool:
    return all(str(prediction.get(field) or "").strip() for field in ["extrapolation_ratio", "uncertainty_or_interval", "reliability_statement"])


def rule_extrap_02(config: dict[str, Any], *, root: Path) -> dict[str, Any]:
    predictions = config.get("predictions") or config.get("forecast_outputs") or []
    if not predictions:
        return make_result(
            rule_id="QG-EXTRAP-02",
            severity="HARD_BLOCK",
            status="SKIPPED",
            message="No prediction metadata was provided; extrapolation gate is not applicable.",
            repair_action="For predictive models, add prediction metadata with observed ranges and risk statements.",
        )
    risky_without_disclosure: list[str] = []
    risky_with_disclosure: list[str] = []
    for idx, prediction in enumerate(predictions):
        if not isinstance(prediction, dict) or not _prediction_risky(prediction):
            continue
        label = str(prediction.get("id") or f"prediction[{idx}]")
        if _has_extrapolation_disclosure(prediction):
            risky_with_disclosure.append(label)
        else:
            risky_without_disclosure.append(label)
    if risky_without_disclosure:
        return make_result(
            rule_id="QG-EXTRAP-02",
            severity="HARD_BLOCK",
            status="FAIL",
            message=f"Prediction extrapolation lacks risk disclosure: {', '.join(risky_without_disclosure)}",
            repair_action="Add extrapolation_ratio, uncertainty_or_interval and reliability_statement before strong extrapolation claims.",
            manual_review_required=True,
            promotion_allowed=False,
        )
    if risky_with_disclosure:
        return make_result(
            rule_id="QG-EXTRAP-02",
            severity="HARD_BLOCK",
            status="MANUAL_REVIEW_REQUIRED",
            message=f"Extrapolation is disclosed but requires manual review: {', '.join(risky_with_disclosure)}",
            repair_action="Review uncertainty interval and reliability statement before paper use.",
            manual_review_required=True,
            promotion_allowed=True,
        )
    return make_result(
        rule_id="QG-EXTRAP-02",
        severity="HARD_BLOCK",
        status="PASS",
        message="No undisclosed high-risk extrapolation detected.",
        repair_action="No action required.",
    )


def _sensitivity_files(case_dir: Path, run_dir: Path) -> list[Path]:
    files: list[Path] = []
    for base in [run_dir / "output", case_dir / "output"]:
        if not base.exists():
            continue
        files.extend(sorted(base.rglob("sensitivity_*.json")))
        files.extend(sorted(base.rglob("sensitivity_*.csv")))
    return files


def _paper_mentions_sensitivity(case_dir: Path) -> bool:
    paper_dir = case_dir / "paper"
    if not paper_dir.exists():
        return False
    for path in paper_dir.rglob("*.tex"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "敏感性" in text or "灵敏度" in text or "sensitivity" in text.lower():
            return True
    return False


def rule_sens_03(config: dict[str, Any], *, root: Path, case_dir: Path, run_dir: Path) -> dict[str, Any]:
    expected = [str(item) for item in (config.get("sensitivity_parameters") or config.get("critical_parameters") or [])]
    files = _sensitivity_files(case_dir, run_dir)
    mentions = _paper_mentions_sensitivity(case_dir)
    evidence = [_rel(path, root) for path in files]
    if not files and not mentions and not expected:
        return make_result(
            rule_id="QG-SENS-03",
            severity="SOFT_BLOCK",
            status="SKIPPED",
            message="No sensitivity requirement or paper sensitivity claim detected.",
            repair_action="Add sensitivity artifacts when the model uses key parameters or the paper discusses sensitivity.",
        )
    if not files:
        return make_result(
            rule_id="QG-SENS-03",
            severity="SOFT_BLOCK",
            status="FAIL",
            message="Sensitivity output is missing while sensitivity is expected or mentioned.",
            repair_action="Generate sensitivity_*.json/csv covering key parameters, or remove unsupported paper claims.",
            manual_review_required=True,
            promotion_allowed=True,
        )
    if expected:
        joined = "\n".join(path.read_text(encoding="utf-8", errors="replace")[:200000] for path in files)
        missing = [name for name in expected if name not in joined]
        if missing:
            return make_result(
                rule_id="QG-SENS-03",
                severity="SOFT_BLOCK",
                status="FAIL",
                evidence_files=evidence,
                message=f"Sensitivity artifacts do not mention key parameters: {', '.join(missing)}",
                repair_action="Cover all key parameters in sensitivity artifacts.",
                manual_review_required=True,
                promotion_allowed=True,
            )
    return make_result(
        rule_id="QG-SENS-03",
        severity="SOFT_BLOCK",
        status="PASS",
        evidence_files=evidence,
        message="Sensitivity artifacts exist and cover declared key parameters at existence level.",
        repair_action="Manual review still needed for sensitivity validity.",
        manual_review_required=True,
        promotion_allowed=True,
    )


def _result_summary_metadata(run_dir: Path, case_dir: Path) -> dict[str, Any]:
    for path in [run_dir / "output" / "result_summary.json", case_dir / "output" / "result_summary.json"]:
        payload = _read_json(path)
        if payload:
            return payload
    return {}


def rule_error_04(config: dict[str, Any], *, root: Path, case_dir: Path, run_dir: Path) -> dict[str, Any]:
    summary = _result_summary_metadata(run_dir, case_dir)
    composite = config.get("composite_target") or summary.get("composite_target") or summary.get("model_metadata", {}).get("composite_target")
    if not composite:
        return make_result(
            rule_id="QG-ERROR-04",
            severity="SOFT_BLOCK",
            status="SKIPPED",
            message="No composite_target metadata found; error propagation gate is not applicable.",
            repair_action="For composite targets such as Y=f(X,S), provide formula and uncertainty metadata.",
        )
    candidate_points = config.get("candidate_points") or summary.get("candidate_points") or []
    required_ok = isinstance(composite, dict) and bool(composite.get("formula")) and bool(composite.get("input_uncertainty")) and (
        bool(composite.get("pred_interval")) or bool(composite.get("error_band"))
    )
    missing_candidate_error = [
        str(item.get("id") or idx)
        for idx, item in enumerate(candidate_points)
        if isinstance(item, dict) and not (item.get("pred_interval") or item.get("error_propagation"))
    ]
    if not required_ok or missing_candidate_error:
        detail = []
        if not required_ok:
            detail.append("composite_target lacks formula/input_uncertainty/pred_interval-or-error_band")
        if missing_candidate_error:
            detail.append(f"candidate points lack pred_interval or error_propagation: {', '.join(missing_candidate_error)}")
        return make_result(
            rule_id="QG-ERROR-04",
            severity="SOFT_BLOCK",
            status="FAIL",
            message="; ".join(detail),
            repair_action="Add formula, input_uncertainty and pred_interval/error_band or candidate error_propagation.",
            manual_review_required=True,
            promotion_allowed=True,
        )
    return make_result(
        rule_id="QG-ERROR-04",
        severity="SOFT_BLOCK",
        status="PASS",
        message="Composite target includes minimal error propagation metadata.",
        repair_action="Manually spot-check formula correctness.",
        manual_review_required=True,
        promotion_allowed=True,
    )


def _load_comparison(config: dict[str, Any], case_dir: Path, run_dir: Path) -> dict[str, Any]:
    if isinstance(config.get("model_comparison"), dict):
        return config["model_comparison"]
    for path in [run_dir / "model_comparison.json", run_dir / "output" / "model_comparison.json", case_dir / "output" / "model_comparison.json"]:
        payload = _read_json(path)
        if payload:
            return payload
    return {}


def _model_metric(model: dict[str, Any]) -> float | None:
    for key in ["metric_value", "RMSE", "MAE", "score", "objective_value", "shortest_distance"]:
        if key in model:
            try:
                return float(model[key])
            except (TypeError, ValueError):
                return None
    metrics = model.get("metrics")
    if isinstance(metrics, dict):
        for key in ["RMSE", "MAE", "score", "objective_value", "shortest_distance"]:
            if key in metrics:
                try:
                    return float(metrics[key])
                except (TypeError, ValueError):
                    return None
    return None


def _selection_reason_present(comparison: dict[str, Any]) -> bool:
    for key in ["quant_selection_reason", "selection_criteria"]:
        value = comparison.get(key)
        if isinstance(value, dict) and value:
            return True
        if isinstance(value, list) and value:
            return True
        if isinstance(value, str) and value.strip():
            return True
    return False


def rule_model_05(config: dict[str, Any], *, root: Path, case_dir: Path, run_dir: Path) -> dict[str, Any]:
    comparison = _load_comparison(config, case_dir, run_dir)
    models = comparison.get("models") or comparison.get("candidates") or []
    if not isinstance(models, list) or len(models) < 3:
        return make_result(
            rule_id="QG-MODEL-05",
            severity="SOFT_BLOCK",
            status="SKIPPED",
            message="Fewer than three comparable models were provided; model-stacking gate is not applicable.",
            repair_action="Provide model_comparison with at least three models when claiming multi-model comparison.",
        )
    values: list[tuple[str, float]] = []
    for idx, model in enumerate(models):
        if not isinstance(model, dict):
            continue
        metric = _model_metric(model)
        if metric is None:
            continue
        values.append((str(model.get("model_id") or model.get("candidate_id") or idx), metric))
    if len(values) < 3:
        return make_result(
            rule_id="QG-MODEL-05",
            severity="SOFT_BLOCK",
            status="SKIPPED",
            message="At least three numeric model metrics are required for convergence check.",
            repair_action="Add comparable numeric metrics or avoid multi-model convergence claims.",
        )
    direction = str(comparison.get("metric_direction") or "minimize").lower()
    metric_numbers = [value for _name, value in values]
    best = min(metric_numbers) if "min" in direction else max(metric_numbers)
    if abs(best) < 1e-12:
        close_range = max(metric_numbers) - min(metric_numbers) < 0.15
    else:
        close_range = abs(max(metric_numbers) - min(metric_numbers)) / abs(best) < 0.15
    best_model = min(values, key=lambda item: item[1])[0] if "min" in direction else max(values, key=lambda item: item[1])[0]
    main_model = str(comparison.get("main_model") or comparison.get("selected_model") or comparison.get("best_candidate") or "")
    main_not_best = bool(main_model and best_model and main_model != best_model)
    if close_range and main_not_best and not _selection_reason_present(comparison):
        return make_result(
            rule_id="QG-MODEL-05",
            severity="SOFT_BLOCK",
            status="FAIL",
            message="Model metrics are very close, selected main model is not metric-best, and no structured selection reason is recorded.",
            repair_action="Add quant_selection_reason or selection_criteria, or remove models without substantive contribution.",
            manual_review_required=True,
            promotion_allowed=True,
        )
    if close_range and main_not_best:
        return make_result(
            rule_id="QG-MODEL-05",
            severity="SOFT_BLOCK",
            status="MANUAL_REVIEW_REQUIRED",
            message="Model metrics are close and selected model is not metric-best; structured reason exists and needs review.",
            repair_action="Review extrapolation stability, interpretability, overfitting gap, and complexity penalty.",
            manual_review_required=True,
            promotion_allowed=True,
        )
    return make_result(
        rule_id="QG-MODEL-05",
        severity="SOFT_BLOCK",
        status="PASS",
        message="No unreasoned model stacking detected.",
        repair_action="No action required.",
    )


def rule_sym_06(config: dict[str, Any], *, root: Path) -> dict[str, Any]:
    symbols = config.get("symbols_defined")
    if not symbols:
        return make_result(
            rule_id="QG-SYM-06",
            severity="SOFT_BLOCK",
            status="SKIPPED",
            message="No symbols_defined table was provided.",
            repair_action="Provide symbols_defined when the paper defines model symbols.",
        )
    symbol_names = [str(item.get("name") if isinstance(item, dict) else item).strip() for item in symbols if str(item).strip()]
    equations = config.get("equations_used") or []
    formula = str(config.get("model_formula") or "")
    usage_text = " ".join(str(item) for item in equations) + " " + formula
    unused = [name for name in symbol_names if name and name not in usage_text]
    model_metadata = config.get("model_metadata") if isinstance(config.get("model_metadata"), dict) else {}
    missing_model_fields = [field for field in ["formula", "coefficients", "hyperparameters"] if not model_metadata.get(field) and not config.get(field)]
    if unused or missing_model_fields:
        parts = []
        if unused:
            parts.append(f"unused symbols: {', '.join(unused)}")
        if missing_model_fields:
            parts.append(f"missing model fields: {', '.join(missing_model_fields)}")
        return make_result(
            rule_id="QG-SYM-06",
            severity="SOFT_BLOCK",
            status="FAIL",
            message="; ".join(parts),
            repair_action="Add model equations, dummy encoding notes, selected hyperparameters, or remove unused symbols.",
            manual_review_required=False,
            promotion_allowed=True,
        )
    return make_result(
        rule_id="QG-SYM-06",
        severity="SOFT_BLOCK",
        status="PASS",
        message="Defined symbols appear in equations/formula and key model fields exist.",
        repair_action="Formula correctness still deserves manual spot-check if used in paper.",
        manual_review_required=False,
        promotion_allowed=True,
    )


def _load_preprocessing_log(config: dict[str, Any], case_dir: Path) -> dict[str, Any]:
    if isinstance(config.get("preprocessing_log"), dict):
        return config["preprocessing_log"]
    for rel in ["preprocessing_log.json", "data/processed/preprocessing_log.json", "output/preprocessing_log.json"]:
        payload = _read_json(case_dir / rel)
        if payload:
            return payload
    return {}


def rule_prep_07(config: dict[str, Any], *, root: Path, case_dir: Path) -> dict[str, Any]:
    log = _load_preprocessing_log(config, case_dir)
    paper_claim = config.get("paper_effective_rows_claim")
    parsed_features = config.get("parsed_text_or_combination_features") or []
    if not log:
        return make_result(
            rule_id="QG-PREP-07",
            severity="WARNING",
            status="WARNING",
            message="preprocessing_log is missing; row accounting cannot be machine-confirmed.",
            repair_action="Record raw_rows, removed_rows, removal_rules, final_rows and derived_feature_rules.",
            manual_review_required=bool(paper_claim or parsed_features),
            promotion_allowed=True,
        )
    required = ["raw_rows", "removed_rows", "removal_rules", "final_rows", "derived_feature_rules"]
    missing = [field for field in required if field not in log]
    warnings: list[str] = []
    if missing:
        warnings.append(f"missing preprocessing fields: {', '.join(missing)}")
    try:
        if int(log.get("raw_rows")) - int(log.get("removed_rows")) != int(log.get("final_rows")):
            warnings.append("raw_rows - removed_rows does not equal final_rows")
    except (TypeError, ValueError):
        warnings.append("row counts are not numeric")
    if paper_claim is not None:
        try:
            if int(paper_claim) != int(log.get("final_rows")):
                warnings.append("paper effective row claim does not match final_rows")
        except (TypeError, ValueError):
            warnings.append("paper effective row claim cannot be compared")
    if parsed_features and not log.get("derived_feature_rules"):
        warnings.append("parsed text/combination features lack derived_feature_rules")
    if warnings:
        return make_result(
            rule_id="QG-PREP-07",
            severity="WARNING",
            status="WARNING",
            message="; ".join(warnings),
            repair_action="Complete preprocessing log and row-accounting evidence.",
            manual_review_required=bool(parsed_features),
            promotion_allowed=True,
        )
    return make_result(
        rule_id="QG-PREP-07",
        severity="WARNING",
        status="PASS",
        message="Preprocessing log contains required row accounting and derived feature fields.",
        repair_action="No action required.",
        promotion_allowed=True,
    )


def _load_figure_manifest(config: dict[str, Any], case_dir: Path, run_dir: Path, root: Path) -> tuple[list[dict[str, Any]], list[str], bool]:
    raw = config.get("figure_manifest") or config.get("figures")
    figures = _list_from_payload(raw, "figures", "items") if raw is not None else []
    evidence: list[str] = []
    manifest_found = bool(figures)
    if not figures:
        for path in [
            run_dir / "output" / "figures" / "figure_manifest.json",
            run_dir / "output" / "figure_manifest.json",
            case_dir / "output" / "figures" / "figure_manifest.json",
            case_dir / "output" / "figure_manifest.json",
        ]:
            payload = _read_json_any(path)
            loaded = _list_from_payload(payload, "figures", "items")
            if loaded:
                figures = loaded
                evidence.append(_rel(path, root))
                manifest_found = True
                break
    normalized: list[dict[str, Any]] = []
    for item in figures:
        if isinstance(item, dict):
            normalized.append(dict(item))
    if normalized:
        for item in normalized:
            path = item.get("path") or item.get("artifact_path")
            if path:
                evidence.append(str(path))
        return normalized, sorted(set(evidence)), manifest_found
    figure_files: list[Path] = []
    for base in [run_dir / "output" / "figures", case_dir / "output" / "figures"]:
        if not base.exists():
            continue
        for suffix in ["*.png", "*.jpg", "*.jpeg", "*.pdf", "*.svg"]:
            figure_files.extend(sorted(base.glob(suffix)))
    for path in figure_files:
        normalized.append({"figure_id": path.stem, "path": _rel(path, root)})
        evidence.append(_rel(path, root))
    return normalized, sorted(set(evidence)), manifest_found


def _has_figure_explanation(figure: dict[str, Any], draft_text: str) -> bool:
    if str(figure.get("interpretation") or figure.get("linked_claim") or figure.get("claim_id") or "").strip():
        return True
    figure_id = str(figure.get("figure_id") or figure.get("id") or "").strip()
    caption = str(figure.get("caption") or "").strip()
    candidates = [item for item in [figure_id, caption[:40]] if item]
    if not candidates:
        return False
    lowered = draft_text.lower()
    explanation_words = ["说明", "表明", "显示", "解释", "支持", "indicates", "shows", "demonstrates", "reveals", "supports"]
    return any(candidate.lower() in lowered for candidate in candidates) and any(word in lowered for word in explanation_words)


def _is_diagnostic_figure(figure: dict[str, Any]) -> bool:
    figure_type = str(figure.get("figure_type") or figure.get("type") or "").lower()
    diagnostic_types = ["residual", "prediction_vs_observed", "error_plot", "diagnostic"]
    return bool(figure.get("diagnostic_figure")) or any(name in figure_type for name in diagnostic_types)


def rule_fig_08(config: dict[str, Any], *, root: Path, case_dir: Path, run_dir: Path) -> dict[str, Any]:
    figures, evidence, manifest_found = _load_figure_manifest(config, case_dir, run_dir, root)
    draft_text, draft_files = _read_draft_text(case_dir)
    evidence = sorted(set(evidence + [_rel(Path(path), root) for path in draft_files]))
    if not figures:
        return make_result(
            rule_id="QG-FIG-08",
            severity="WARNING",
            status="SKIPPED",
            evidence_files=evidence,
            message="No figure manifest or figure files were detected.",
            repair_action="Provide figure_manifest or output/figures artifacts when paper claims rely on figures.",
            manual_review_required=False,
            promotion_allowed=True,
            figure_id=[],
        )
    warnings: list[str] = []
    diagnostic_missing: list[str] = []
    affected: list[str] = []
    if figures and not manifest_found:
        warnings.append("figure files exist without figure_manifest metadata")
    for idx, figure in enumerate(figures):
        figure_id = str(figure.get("figure_id") or figure.get("id") or figure.get("path") or f"figure[{idx}]")
        affected.append(figure_id)
        try:
            series_count = int(figure.get("series_count"))
        except (TypeError, ValueError):
            series_count = 0
        has_readability_aid = any(str(figure.get(field) or "").strip() for field in ["facet", "highlight", "grouping"])
        if series_count > 8 and not has_readability_aid:
            warnings.append(f"{figure_id} has series_count>{8} without facet/highlight/grouping")
        if not _has_figure_explanation(figure, draft_text):
            warnings.append(f"{figure_id} lacks a linked explanation sentence")
        if _is_diagnostic_figure(figure) and not str(figure.get("interpretation") or figure.get("linked_claim") or "").strip():
            diagnostic_missing.append(figure_id)
    if diagnostic_missing:
        return make_result(
            rule_id="QG-FIG-08",
            severity="SOFT_BLOCK",
            status="FAIL",
            evidence_files=evidence,
            message=f"Diagnostic figures require manual review because interpretation/linked_claim is missing: {', '.join(diagnostic_missing)}",
            repair_action="Add interpretation or linked_claim, then manually judge whether the figure exposes an unexplained defect.",
            manual_review_required=True,
            promotion_allowed=True,
            figure_id=diagnostic_missing,
        )
    if warnings:
        return make_result(
            rule_id="QG-FIG-08",
            severity="WARNING",
            status="WARNING",
            evidence_files=evidence,
            message="; ".join(warnings),
            repair_action="Add grouping/highlight/facet metadata and paper explanation sentences for each figure.",
            manual_review_required=True,
            promotion_allowed=True,
            figure_id=affected,
        )
    return make_result(
        rule_id="QG-FIG-08",
        severity="WARNING",
        status="PASS",
        evidence_files=evidence,
        message="Figure metadata and draft references satisfy the lightweight readability/linkage checks.",
        repair_action="Manually confirm figures genuinely support the stated conclusions.",
        manual_review_required=True,
        promotion_allowed=True,
        figure_id=affected,
    )


def _load_citation_matrix(config: dict[str, Any], case_dir: Path, root: Path) -> tuple[list[dict[str, Any]], list[str], bool]:
    raw = config.get("citation_matrix") or config.get("citations")
    entries = _list_from_payload(raw, "citations", "items", "rows") if raw is not None else []
    evidence: list[str] = []
    matrix_found = bool(entries)
    if not entries:
        json_path = case_dir / "literature" / "citation_matrix.json"
        payload = _read_json_any(json_path)
        loaded = _list_from_payload(payload, "citations", "items", "rows")
        if loaded:
            entries = loaded
            evidence.append(_rel(json_path, root))
            matrix_found = True
    normalized = [dict(item) for item in entries if isinstance(item, dict)]
    md_path = case_dir / "literature" / "citation_matrix.md"
    if not normalized and md_path.exists():
        text = md_path.read_text(encoding="utf-8", errors="replace")
        evidence.append(_rel(md_path, root))
        for line in text.splitlines():
            if "|" not in line or "---" in line:
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) >= 4 and cells[0].lower() not in {"citation", "citation_id", "key"}:
                normalized.append(
                    {
                        "citation_id": cells[0],
                        "supported_claim": cells[1],
                        "evidence_anchor": cells[2],
                        "source_position": cells[3],
                    }
                )
        matrix_found = bool(normalized)
    return normalized, evidence, matrix_found


def _draft_citation_keys(text: str) -> set[str]:
    keys: set[str] = set()
    for match in re.finditer(r"\\cite\w*\{([^}]+)\}", text):
        for key in match.group(1).split(","):
            cleaned = key.strip()
            if cleaned:
                keys.add(cleaned)
    return keys


def _citation_key(entry: dict[str, Any]) -> str:
    return str(entry.get("citation_id") or entry.get("key") or entry.get("id") or entry.get("source_key") or "").strip()


def _supported_claims(entry: dict[str, Any]) -> list[str]:
    claims = _string_list(entry.get("supported_claim") or entry.get("claim_id") or entry.get("claim_ids"))
    return [claim for claim in claims if claim.strip()]


def rule_ref_09(config: dict[str, Any], *, root: Path, case_dir: Path) -> dict[str, Any]:
    entries, evidence, matrix_found = _load_citation_matrix(config, case_dir, root)
    draft_text, draft_files = _read_draft_text(case_dir)
    evidence = sorted(set(evidence + [_rel(Path(path), root) for path in draft_files]))
    draft_keys = _draft_citation_keys(draft_text)
    if not matrix_found:
        return make_result(
            rule_id="QG-REF-09",
            severity="SOFT_BLOCK",
            status="FAIL",
            evidence_files=evidence,
            message="citation_matrix is missing; citation-to-claim binding cannot be checked.",
            repair_action="Create citation_matrix with supported_claim and evidence_anchor/source_position for each citation.",
            manual_review_required=True,
            promotion_allowed=True,
        )
    issues: list[str] = []
    review_only: list[str] = []
    matrix_keys = {_citation_key(entry) for entry in entries if _citation_key(entry)}
    missing_from_matrix = sorted(key for key in draft_keys if key not in matrix_keys)
    if missing_from_matrix:
        issues.append(f"draft citations missing from citation_matrix: {', '.join(missing_from_matrix)}")
    for idx, entry in enumerate(entries):
        key = _citation_key(entry) or f"citation[{idx}]"
        claims = _supported_claims(entry)
        has_anchor = bool(str(entry.get("evidence_anchor") or entry.get("source_position") or "").strip())
        background_only = bool(entry.get("background_only"))
        if not claims and not background_only:
            issues.append(f"{key} has no supported_claim")
        elif background_only:
            review_only.append(f"{key} is background_only")
        if claims and not has_anchor:
            issues.append(f"{key} has supported_claim but lacks evidence_anchor/source_position")
        if len(claims) > 1 and not has_anchor:
            issues.append(f"{key} maps to multiple claims without a concrete evidence_anchor")
    if issues:
        return make_result(
            rule_id="QG-REF-09",
            severity="SOFT_BLOCK",
            status="FAIL",
            evidence_files=evidence,
            message="; ".join(issues),
            repair_action="Bind every citation to concrete supported_claim and evidence_anchor/source_position; mark background-only citations explicitly.",
            manual_review_required=True,
            promotion_allowed=True,
        )
    if review_only:
        return make_result(
            rule_id="QG-REF-09",
            severity="SOFT_BLOCK",
            status="MANUAL_REVIEW_REQUIRED",
            evidence_files=evidence,
            message="; ".join(review_only),
            repair_action="Manually confirm background-only citations are not used as claim support.",
            manual_review_required=True,
            promotion_allowed=True,
        )
    return make_result(
        rule_id="QG-REF-09",
        severity="SOFT_BLOCK",
        status="PASS",
        evidence_files=evidence,
        message="Citation entries are bound to supported_claim and evidence_anchor/source_position at metadata level.",
        repair_action="Manually verify the cited sources actually support the claims.",
        manual_review_required=True,
        promotion_allowed=True,
    )


def _extract_abstract(config: dict[str, Any], case_dir: Path, root: Path) -> tuple[str, dict[str, str], list[str]]:
    sections_raw = config.get("abstract_sections")
    sections: dict[str, str] = {}
    if isinstance(sections_raw, dict):
        sections = {str(key): str(value) for key, value in sections_raw.items()}
        return "\n".join(sections.values()), sections, []
    abstract_raw = config.get("abstract")
    if isinstance(abstract_raw, dict):
        sections = {str(key): str(value) for key, value in abstract_raw.items()}
        return "\n".join(sections.values()), sections, []
    if isinstance(abstract_raw, str) and abstract_raw.strip():
        return abstract_raw, {}, []
    evidence: list[str] = []
    abstract_md = case_dir / "paper" / "draft" / "abstract.md"
    if abstract_md.exists():
        evidence.append(_rel(abstract_md, root))
        return abstract_md.read_text(encoding="utf-8", errors="replace"), {}, evidence
    paper_dir = case_dir / "paper" / "draft"
    if paper_dir.exists():
        for path in sorted(paper_dir.rglob("*.tex")):
            text = path.read_text(encoding="utf-8", errors="replace")
            match = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", text, re.DOTALL)
            if match:
                evidence.append(_rel(path, root))
                return match.group(1), {}, evidence
    return "", {}, evidence


def _abstract_has_element(text: str, sections: dict[str, str], key: str) -> bool:
    if str(sections.get(key) or "").strip():
        return True
    lowered = text.lower()
    keyword_map = {
        "problem": ["problem", "objective", "问题", "目标", "研究对象"],
        "method": ["method", "model", "采用", "方法", "模型", "算法"],
        "result": ["result", "rmse", "r2", "mae", "auc", "accuracy", "结果", "得到", "预测"],
        "conclusion": ["conclusion", "recommend", "结论", "建议", "表明"],
    }
    return any(word in lowered for word in keyword_map[key])


def _abstract_result_text(text: str, sections: dict[str, str]) -> str:
    for key in ["result", "results"]:
        if str(sections.get(key) or "").strip():
            return str(sections[key])
    match = re.search(r"(result[s]?|结果)[:：](.*?)(conclusion|结论|建议|$)", text, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(2)
    return text


def rule_abst_10(config: dict[str, Any], *, root: Path, case_dir: Path) -> dict[str, Any]:
    """Check abstract structure and abstract-to-body/result support only.

    QG-ABST-10 does not perform independent recomputation, second-method
    validation, rerun/diff checks, or equivalent-optimum verification. Those
    concerns belong to XVAL-10 and EQ-OPT respectively.
    """

    text, sections, evidence = _extract_abstract(config, case_dir, root)
    if not text.strip():
        return make_result(
            rule_id="QG-ABST-10",
            severity="WARNING",
            status="WARNING",
            evidence_files=evidence,
            message="abstract is missing; four-element structure cannot be checked.",
            repair_action="Add an abstract with problem, method, result and conclusion elements.",
            manual_review_required=True,
            promotion_allowed=True,
        )
    missing = [key for key in ["problem", "method", "result", "conclusion"] if not _abstract_has_element(text, sections, key)]
    result_text = _abstract_result_text(text, sections)
    metric_pattern = re.compile(r"\b(rmse|r2|mae|auc|accuracy)\b|均方根|准确率", re.IGNORECASE)
    has_metric = bool(metric_pattern.search(result_text))
    domain_terms = _string_list(config.get("domain_terms"))
    conclusion_claim = str(config.get("conclusion_claim") or sections.get("conclusion_claim") or "").strip()
    has_domain_substance = bool(conclusion_claim) or any(term.lower() in result_text.lower() for term in domain_terms)
    if has_metric and not has_domain_substance:
        detail = []
        if missing:
            detail.append(f"missing abstract elements: {', '.join(missing)}")
        detail.append("result section contains metric words but lacks domain entity or conclusion_claim")
        return make_result(
            rule_id="QG-ABST-10",
            severity="SOFT_BLOCK",
            status="FAIL",
            evidence_files=evidence,
            message="; ".join(detail),
            repair_action="Add a supported domain-specific result conclusion and manually confirm it is consistent with the canonical result summary or paper body.",
            manual_review_required=True,
            promotion_allowed=True,
        )
    if missing:
        return make_result(
            rule_id="QG-ABST-10",
            severity="WARNING",
            status="WARNING",
            evidence_files=evidence,
            message=f"abstract is missing four-element structure fields: {', '.join(missing)}",
            repair_action="Revise abstract to include problem, method, result and conclusion elements.",
            manual_review_required=True,
            promotion_allowed=True,
        )
    return make_result(
        rule_id="QG-ABST-10",
        severity="WARNING",
        status="PASS",
        evidence_files=evidence,
        message="Abstract contains the lightweight problem/method/result/conclusion structure and a domain result signal.",
        repair_action="Manually confirm abstract claims are supported and consistent with the canonical result summary or paper body.",
        manual_review_required=True,
        promotion_allowed=True,
    )


def run_core_rules(config: dict[str, Any], *, root: Path, case_dir: Path, run_dir: Path) -> list[dict[str, Any]]:
    return [
        rule_data_01(config, root=root),
        rule_extrap_02(config, root=root),
        rule_sens_03(config, root=root, case_dir=case_dir, run_dir=run_dir),
        rule_error_04(config, root=root, case_dir=case_dir, run_dir=run_dir),
        rule_model_05(config, root=root, case_dir=case_dir, run_dir=run_dir),
        rule_sym_06(config, root=root),
        rule_prep_07(config, root=root, case_dir=case_dir),
        rule_fig_08(config, root=root, case_dir=case_dir, run_dir=run_dir),
        rule_ref_09(config, root=root, case_dir=case_dir),
        rule_abst_10(config, root=root, case_dir=case_dir),
    ]
