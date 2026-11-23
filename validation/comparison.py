"""Minimal model comparison for X5-F2."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .report_writer import comparison_report_markdown
from .risk_policy import utc_now


def _metric(payload: dict[str, Any], key: str) -> Any:
    for container_name in ("metrics", "primary_results"):
        container = payload.get(container_name)
        if isinstance(container, dict) and key in container:
            return container.get(key)
    return None


def _candidate(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": payload.get("run_id") or path.stem,
        "path": str(path),
        "model_id": payload.get("model_id"),
        "model_family": payload.get("model_family"),
        "success": payload.get("success"),
        "status": payload.get("status"),
        "fatal_count": payload.get("fatal_count"),
        "metric_value": None,
    }


def _load(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return None, str(exc)
    return payload if isinstance(payload, dict) else None, None if isinstance(payload, dict) else "top-level JSON is not object"


def _comparison_metric(family: str, payloads: list[dict[str, Any]]) -> tuple[str | None, str]:
    if family == "forecasting":
        for metric in ("RMSE", "MAE"):
            if all(isinstance(_metric(item, metric), (int, float)) for item in payloads):
                return metric, "asc"
        return None, "metric_not_comparable"
    if family == "statistics":
        for metric, direction in (("RMSE", "asc"), ("MAE", "asc"), ("R2", "desc")):
            if all(isinstance(_metric(item, metric), (int, float)) for item in payloads):
                return metric, direction
        return None, "metric_not_comparable"
    if family == "optimization":
        directions = {
            str(_metric(item, "objective_direction") or "minimize").lower()
            for item in payloads
        }
        if len(directions) != 1:
            return None, "metric_not_comparable"
        if all(isinstance(_metric(item, "objective_value"), (int, float)) for item in payloads):
            return "objective_value", "asc" if "min" in next(iter(directions)) else "desc"
        return None, "metric_not_comparable"
    if family == "graph":
        if all(isinstance(_metric(item, "shortest_distance"), (int, float)) for item in payloads):
            return "shortest_distance", "asc"
        return None, "metric_not_comparable"
    return None, "metric_not_comparable"


def compare_result_summaries(paths: list[Path]) -> dict[str, Any]:
    loaded: list[tuple[Path, dict[str, Any]]] = []
    notes: list[str] = []
    for path in paths:
        payload, error = _load(path)
        if payload is None:
            notes.append(f"load_failed: {path}: {error}")
            continue
        loaded.append((path, payload))

    candidates = [_candidate(path, payload) for path, payload in loaded]
    families = {payload.get("model_family") for _path, payload in loaded}
    report: dict[str, Any] = {
        "schema_version": "x5_f2_model_comparison_v1",
        "created_at": utc_now(),
        "comparison_status": "PASS" if loaded else "FAIL",
        "family_mode": "empty",
        "based_on_metric": None,
        "best_candidate": None,
        "candidates": candidates,
        "notes": notes,
    }
    if not loaded:
        report["notes"].append("no valid result_summary inputs")
        return report
    if len(families) != 1:
        report["family_mode"] = "mixed_family"
        report["comparison_status"] = "PASS_WITH_WARNING"
        report["notes"].append("metric_not_comparable: mixed family inputs are listed but not ranked")
        return report

    family = str(next(iter(families)))
    report["family_mode"] = "same_family"
    payloads = [payload for _path, payload in loaded]
    metric, direction = _comparison_metric(family, payloads)
    if metric is None:
        report["comparison_status"] = "PASS_WITH_WARNING"
        report["notes"].append(f"metric_not_comparable: {direction}")
        return report

    for candidate, (_path, payload) in zip(candidates, loaded):
        candidate["metric_value"] = _metric(payload, metric)
    candidates.sort(key=lambda item: item["metric_value"], reverse=direction == "desc")
    report["candidates"] = candidates
    report["based_on_metric"] = metric
    report["best_candidate"] = candidates[0]["candidate_id"] if candidates else None
    report["notes"].append(f"best_candidate is based_on_metric={metric}; this is not a comprehensive optimum claim")
    return report


def write_model_comparison(paths: list[Path], output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report = compare_result_summaries(paths)
    (output_dir / "model_comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "model_comparison.md").write_text(comparison_report_markdown(report), encoding="utf-8")
    return report
