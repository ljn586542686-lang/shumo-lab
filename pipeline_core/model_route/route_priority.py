from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline_core.literature.support_chain.support_chain_loader import normalize_route
from pipeline_core.model_route.route_direction_review import validate_route_direction_review


ROOT = Path(__file__).resolve().parents[2]

P0_KEYS = ("manual_route_lock", "human_confirmed_route", "confirmed_model_route", "locked_model_route")
P1_KEYS = ("model_route", "target_route", "final_model_route", "intended_problem_family")

CONFIRMED_CASE_ROUTES = {
    "case_09_real_optimization_test": "optimization_planning",
    "case_10_real_prediction_timeseries_test": "prediction_timeseries",
    "case_11_real_evaluation_decision_test": "graph_network_spatial_coverage",
    "active_competition_2017B_photo_pricing": "mixed_problem",
}

ACTIVE_COMPETITION_ROUTES = {
    "active_competition_2017B_photo_pricing": "mixed_problem",
}


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def read_case_config(case_dir: Path) -> dict[str, Any]:
    return load_json(case_dir / "case_config.json")


def _normalize(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower()
    if "evaluation_decision_network" in lowered or "coverage_assignment" in lowered or "graph_network" in lowered:
        return "graph_network_spatial_coverage"
    if "prediction_timeseries" in lowered or "dynamic_prediction" in lowered:
        return "prediction_timeseries"
    if "optimization_planning" in lowered:
        return "optimization_planning"
    if "mixed_statistics_optimization" in lowered or "stat_opt_mixed" in lowered:
        return "mixed_problem"
    return normalize_route(problem_type=text, model_route=text)


def _first_route(metadata: dict[str, Any], keys: tuple[str, ...]) -> tuple[str | None, str | None]:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            route = _normalize(value)
            if route:
                return route, key
    return None, None


def _metadata_text(metadata: dict[str, Any]) -> str:
    values: list[str] = []
    for key in (
        "case_id",
        "case_name",
        "case_role",
        "problem_type",
        "problem_title",
        "problem_name",
        "completed_stage",
        "current_status",
        "case_status",
        "intended_problem_family",
        "notes",
    ):
        value = metadata.get(key)
        if value:
            values.append(str(value))
    for key in ("secondary_problem_families", "audit_limitations"):
        value = metadata.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
    return " / ".join(values)


def _historical_route(metadata: dict[str, Any]) -> tuple[str | None, str | None]:
    case_id = str(metadata.get("case_id", "")).strip()
    if case_id in CONFIRMED_CASE_ROUTES:
        return CONFIRMED_CASE_ROUTES[case_id], "confirmed_case_route_map"

    text = _metadata_text(metadata).lower()
    if "prediction_timeseries" in text or "time_series" in text:
        return "prediction_timeseries", "historical_case_metadata"
    if "optimization_planning" in text:
        return "optimization_planning", "historical_case_metadata"
    if "graph_network_spatial_coverage" in text:
        return "graph_network_spatial_coverage", "historical_case_metadata"
    if "evaluation_decision_network" in text or "coverage_assignment" in text or "graph_network" in text:
        return "graph_network_spatial_coverage", "historical_case_metadata"
    if "mixed_statistics_optimization" in text or "stat_opt_mixed" in text or "mixed_problem" in text:
        return "mixed_problem", "historical_case_metadata"
    if "mechanism_fitting" in text:
        return "mechanism_fitting", "historical_case_metadata"
    return None, None


def _active_competition_route(metadata: dict[str, Any], case_dir: Path | None) -> tuple[str | None, str | None]:
    case_id = str(metadata.get("case_id", "")).strip()
    if case_id in ACTIVE_COMPETITION_ROUTES:
        return ACTIVE_COMPETITION_ROUTES[case_id], "active_competition_config"
    if case_dir:
        rel = case_dir.as_posix().replace("\\", "/")
        if "cases/active_competition/current_case" in rel:
            route = _normalize(metadata.get("model_route") or metadata.get("target_route"))
            if route:
                return route, "active_competition_config"
            problem_type = str(metadata.get("problem_type", "")).lower()
            if "mixed" in problem_type and ("optimization" in problem_type or "pricing" in problem_type):
                return "mixed_problem", "active_competition_config"
    return None, None


def resolve_model_route_from_metadata(
    metadata: dict[str, Any],
    router_suggestion: str | None = None,
    case_dir: Path | None = None,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []

    route, source_key = _first_route(metadata, P0_KEYS)
    if route:
        candidates.append({"priority": "P0", "source": source_key, "route": route})

    route, source_key = _first_route(metadata, P1_KEYS)
    if route:
        candidates.append({"priority": "P1", "source": source_key, "route": route})

    route, source_key = _active_competition_route(metadata, case_dir)
    if route:
        candidates.append({"priority": "P2", "source": source_key, "route": route})

    route, source_key = _historical_route(metadata)
    if route:
        candidates.append({"priority": "P3", "source": source_key, "route": route})

    normalized_router_suggestion = _normalize(router_suggestion)
    if normalized_router_suggestion:
        candidates.append(
            {"priority": "P4", "source": "problem_type_router_auto_suggestion", "route": normalized_router_suggestion}
        )

    candidates.append({"priority": "P5", "source": "fallback_route", "route": "mechanism_fitting"})
    selected = candidates[0]
    final_route = str(selected["route"])

    conflict_detected = bool(normalized_router_suggestion and normalized_router_suggestion != final_route)
    conflict_note = ""
    if conflict_detected:
        conflict_note = (
            "router_suggestion is recorded as an auxiliary signal and does not override "
            f"confirmed route `{final_route}` from {selected['priority']} / {selected['source']}."
        )

    route_review = (
        validate_route_direction_review(case_dir, metadata)
        if case_dir is not None
        else {
            "route_review_required": False, "route_review_status": "MANUAL_REVIEW_REQUIRED",
            "route_review_artifact_path": None, "selected_route_id": None,
            "selected_model_family": None, "blockers": [], "warnings": [],
            "provider_role_id": None, "reviewer_role_id": None,
            "rejected_alternatives_count": 0, "post_hoc_suspected": False,
            "route_conflict": False, "effective_submission_tier": "training",
        }
    )
    review_required = route_review["route_review_required"] is True
    review_route = route_review.get("selected_route_id")
    if review_required and route_review["route_review_status"] != "PASS":
        return {
            "case_id": metadata.get("case_id") or (case_dir.name if case_dir else "UNKNOWN_CASE"),
            "router_suggestion": normalized_router_suggestion,
            "final_model_route": None,
            "route_source": "ROUTE_REVIEW_BLOCKED",
            "route_priority": None,
            "route_conflict_detected": False,
            "route_conflict_note": "selection/formal route review did not pass; implicit route fallback is disabled.",
            "route_candidates": candidates,
            "priority_rule": ["route_direction_review required before P0-P5 route resolution"],
            **route_review,
        }

    override_candidates = [item for item in candidates if item["priority"] in {"P0", "P1"}]
    if route_review["route_review_status"] == "PASS" and isinstance(review_route, str) and review_route.strip():
        normalized_review_route = _normalize(review_route) or review_route.strip()
        conflicting = [
            item for item in override_candidates
            if (_normalize(item["route"]) or str(item["route"]).strip()) != normalized_review_route
        ]
        if review_required and conflicting:
            route_review["route_conflict"] = True
            route_review["blockers"] = ["ROUTE_REVIEW_ROUTE_CONFLICT"]
            route_review["route_review_status"] = "BLOCKED"
            return {
                "case_id": metadata.get("case_id") or (case_dir.name if case_dir else "UNKNOWN_CASE"),
                "router_suggestion": normalized_router_suggestion,
                "final_model_route": None,
                "route_source": "ROUTE_REVIEW_CONFLICT",
                "route_priority": None,
                "route_conflict_detected": True,
                "route_conflict_note": "P0/P1 route override conflicts with route_direction_review selected_route_id.",
                "route_candidates": candidates,
                "priority_rule": ["route_direction_review conflicts with P0/P1 override"],
                **route_review,
            }
        final_route = normalized_review_route
        selected = {"priority": "P0_ROUTE_REVIEW", "source": "route_direction_review", "route": final_route}
        candidates.insert(0, selected)

    return {
        "case_id": metadata.get("case_id") or (case_dir.name if case_dir else "UNKNOWN_CASE"),
        "router_suggestion": normalized_router_suggestion,
        "final_model_route": final_route,
        "route_source": f"{selected['priority']} / {selected['source']}",
        "route_priority": selected["priority"],
        "route_conflict_detected": conflict_detected,
        "route_conflict_note": conflict_note,
        "route_candidates": candidates,
        "priority_rule": [
            "route_direction_review selected_route_id when valid and present",
            "P0 manual_route_lock / human_confirmed_route",
            "P1 case_config.model_route / case_config.target_route",
            "P2 active_competition confirmed current_case route",
            "P3 historical_case_metadata verified route",
            "P4 problem_type_router automatic suggestion",
            "P5 fallback route",
        ],
        **route_review,
    }


def resolve_model_route(case_dir: Path, router_suggestion: str | None = None) -> dict[str, Any]:
    return resolve_model_route_from_metadata(
        read_case_config(case_dir),
        router_suggestion=router_suggestion,
        case_dir=case_dir,
    )
