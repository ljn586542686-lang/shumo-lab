from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SUPPORT_CHAIN_PATH = Path(__file__).with_name("model_route_support_chain_matrix.json")
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]

ROUTE_ALIASES = {
    "statistical_analysis": "statistics_analysis",
    "statistics_analysis": "statistics_analysis",
    "statistics": "statistics_analysis",
    "optimization": "optimization_planning",
    "optimization_planning": "optimization_planning",
    "evaluation": "evaluation_decision",
    "evaluation_decision": "evaluation_decision",
    "graph_network_spatial": "graph_network_spatial_coverage",
    "graph_network_spatial_coverage": "graph_network_spatial_coverage",
    "network_spatial": "graph_network_spatial_coverage",
    "dynamic_scheduling": "dynamic_scheduling",
    "scheduling": "dynamic_scheduling",
    "mechanism_fitting": "mechanism_fitting",
    "mechanism": "mechanism_fitting",
    "fallback": "mechanism_fitting",
    "classification": "classification_clustering",
    "classification_clustering": "classification_clustering",
    "clustering": "classification_clustering",
    "multi_objective": "multi_objective_decision",
    "multi_objective_decision": "multi_objective_decision",
    "mixed": "mixed_problem",
    "mixed_problem": "mixed_problem",
    "stat_opt_mixed": "mixed_problem",
    "prediction": "prediction_timeseries",
    "prediction_timeseries": "prediction_timeseries",
    "time_series": "prediction_timeseries",
}

STRUCTURE_RULES_BY_ROUTE = {
    "optimization_planning": [
        "pipeline_core/paper_quality/rules/excellent_paper_problem_progression_rules.md"
    ],
    "statistics_analysis": [
        "pipeline_core/paper_quality/rules/excellent_paper_statistical_evidence_chain_rules.md"
    ],
    "prediction_timeseries": [
        "pipeline_core/paper_quality/rules/excellent_paper_statistical_evidence_chain_rules.md",
        "pipeline_core/paper_quality/rules/problem_by_problem_narrative_upgrade_rules.md",
    ],
    "mixed_problem": [
        "pipeline_core/paper_quality/rules/excellent_paper_stat_opt_decision_chain_rules.md"
    ],
    "mechanism_fitting": [
        "pipeline_core/paper_quality/rules/excellent_paper_mechanism_fitting_optimization_rules.md"
    ],
}


def load_support_chain(path: Path | None = None) -> dict[str, Any]:
    chain_path = path or SUPPORT_CHAIN_PATH
    if not chain_path.exists():
        return {
            "status": "MISSING",
            "chains": [],
            "source_path": chain_path.as_posix(),
            "load_error": "model_route_support_chain_matrix.json not found",
        }
    data = json.loads(chain_path.read_text(encoding="utf-8"))
    data["source_path"] = chain_path.as_posix()
    return data


def normalize_route(problem_type: str | None = None, model_route: str | None = None) -> str | None:
    for explicit in (model_route, problem_type):
        normalized = ROUTE_ALIASES.get(str(explicit).strip().lower()) if explicit else None
        if normalized:
            return normalized

    candidates = [model_route, problem_type]
    joined = " / ".join(str(item).lower() for item in candidates if item)

    for key, value in ROUTE_ALIASES.items():
        if key in joined:
            return value

    if any(token in joined for token in ["供应商", "时间序列", "预测"]):
        return "prediction_timeseries"
    if any(token in joined for token in ["优化", "规划", "调度", "订购", "转运"]):
        return "optimization_planning"
    if any(token in joined for token in ["统计", "回归", "分类", "聚类", "成分"]):
        return "statistics_analysis"
    if any(token in joined for token in ["图网络", "空间", "覆盖", "设施选址"]):
        return "graph_network_spatial_coverage"
    if any(token in joined for token in ["机理", "拟合", "炉温", "热过程"]):
        return "mechanism_fitting"
    if any(token in joined for token in ["混合", "评分", "信贷"]):
        return "mixed_problem"

    return model_route or problem_type


def _index_chains(chain_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("model_route", "")): item for item in chain_data.get("chains", [])}


def _list_paths(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    paths: list[str] = []
    for item in items:
        if isinstance(item, str):
            paths.append(item)
        elif isinstance(item, dict):
            paths.append(
                str(
                    item.get("file_name")
                    or item.get("path")
                    or item.get("rule")
                    or item.get("asset_id")
                    or item
                )
            )
    return paths


def _any_formal_reference_allowed(chain: dict[str, Any]) -> bool:
    method_items = chain.get("method_paper_placeholder_support", [])
    structure_items = chain.get("structure_sample_support", [])
    for item in method_items + structure_items:
        if isinstance(item, dict) and bool(item.get("formal_reference_allowed", False)):
            return True
    return False


def _all_citations_not_ready(chain: dict[str, Any]) -> bool:
    method_items = chain.get("method_paper_placeholder_support", [])
    return not any(bool(item.get("citation_ready", False)) for item in method_items if isinstance(item, dict))


def _card_id_from_path(card_path: str) -> str:
    return Path(card_path).stem


def _card_title(card_body: str, fallback: str) -> str:
    for line in card_body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title
        if stripped.startswith("知识卡："):
            return stripped
    return fallback


def _load_selected_card_bodies(
    card_paths: list[str],
    repository_root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load only the card bodies already selected by the existing route."""
    root = (repository_root or REPOSITORY_ROOT).resolve()
    retrieved_card_ids = [_card_id_from_path(item) for item in card_paths]
    loaded_card_ids: list[str] = []
    injected_card_ids: list[str] = []
    cards: list[dict[str, str]] = []
    missing_cards: list[dict[str, str]] = []
    unreadable_cards: list[dict[str, str]] = []

    for card_path in card_paths:
        card_id = _card_id_from_path(card_path)
        candidate = (root / card_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            unreadable_cards.append(
                {
                    "card_id": card_id,
                    "path": card_path,
                    "status": "KNOWLEDGE_CARD_BODY_UNREADABLE",
                }
            )
            continue

        if not candidate.is_file():
            missing_cards.append(
                {
                    "card_id": card_id,
                    "path": card_path,
                    "status": "KNOWLEDGE_CARD_BODY_MISSING",
                }
            )
            continue

        try:
            body = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            unreadable_cards.append(
                {
                    "card_id": card_id,
                    "path": card_path,
                    "status": "KNOWLEDGE_CARD_BODY_UNREADABLE",
                }
            )
            continue

        body = body.strip()
        if not body:
            unreadable_cards.append(
                {
                    "card_id": card_id,
                    "path": card_path,
                    "status": "KNOWLEDGE_CARD_BODY_UNREADABLE",
                }
            )
            continue

        loaded_card_ids.append(card_id)
        injected_card_ids.append(card_id)
        cards.append(
            {
                "card_id": card_id,
                "title": _card_title(body, card_id),
                "relevant_content": body,
            }
        )

    if not card_paths:
        status = "NO_RELEVANT_CARD"
    elif unreadable_cards:
        status = "KNOWLEDGE_CARD_BODY_UNREADABLE"
    elif missing_cards:
        status = "KNOWLEDGE_CARD_BODY_MISSING"
    else:
        status = "CONSUMED"

    evidence = {
        "retrieved_card_ids": retrieved_card_ids,
        "loaded_card_ids": loaded_card_ids,
        "injected_card_ids": injected_card_ids,
        "retrieved_equals_loaded": retrieved_card_ids == loaded_card_ids,
        "loaded_equals_injected": loaded_card_ids == injected_card_ids,
        "full_library_bulk_load": False,
        "missing_cards": missing_cards,
        "unreadable_cards": unreadable_cards,
        "status": status,
    }
    model_building_context = {
        "status": status if status != "CONSUMED" else "READY",
        "cards": cards,
    }
    return model_building_context, evidence


def build_support_chain_context(
    problem_type: str | None = None,
    model_route: str | None = None,
    chain_data: dict[str, Any] | None = None,
    repository_root: Path | None = None,
) -> dict[str, Any]:
    data = chain_data or load_support_chain()
    resolved_route = normalize_route(problem_type=problem_type, model_route=model_route)
    chain = _index_chains(data).get(str(resolved_route), {})
    structure_rules = _list_paths(chain.get("excellent_paper_structure_rule_support", []))
    structure_rules.extend(STRUCTURE_RULES_BY_ROUTE.get(str(resolved_route), []))
    structure_rules = list(dict.fromkeys(structure_rules))

    support_chain_found = bool(chain)
    formal_reference_allowed = _any_formal_reference_allowed(chain) if chain else False
    citation_ready = not _all_citations_not_ready(chain) if chain else False
    manual_review_required = bool(chain.get("manual_review_required", True)) if chain else True
    selected_card_paths = _list_paths(chain.get("textbook_or_knowledge_card_support", [])) if chain else []
    model_building_context, consumption_evidence = _load_selected_card_bodies(
        selected_card_paths,
        repository_root=repository_root,
    )

    return {
        "problem_type": problem_type,
        "model_route": model_route,
        "resolved_model_route": resolved_route,
        "support_chain_found": support_chain_found,
        "support_chain_source": data.get("source_path", SUPPORT_CHAIN_PATH.as_posix()),
        "textbook_concept_support": selected_card_paths,
        "model_building_context": model_building_context,
        "consumption_evidence": consumption_evidence,
        "method_citation_support": chain.get("method_paper_placeholder_support", []),
        "structure_sample_support": chain.get("structure_sample_support", []),
        "case_validation_support": chain.get("validation_or_case_support", []),
        "citation_ready": citation_ready,
        "formal_reference_allowed": formal_reference_allowed,
        "manual_review_required": manual_review_required,
        "excellent_paper_structure_rules": structure_rules,
        "method_citation_support_status": "placeholder_only_pending_manual_review",
        "structure_sample_support_status": "structure_sample_only_not_method_citation",
        "formal_references_generated": False,
        "excellent_paper_as_method_citation": False,
    }


def summarize_support_chain_context(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "resolved_model_route": context.get("resolved_model_route"),
        "support_chain_found": bool(context.get("support_chain_found", False)),
        "textbook_support_count": len(context.get("textbook_concept_support", [])),
        "method_placeholder_count": len(context.get("method_citation_support", [])),
        "structure_sample_count": len(context.get("structure_sample_support", [])),
        "case_validation_support_count": len(context.get("case_validation_support", [])),
        "structure_rule_count": len(context.get("excellent_paper_structure_rules", [])),
        "citation_ready": bool(context.get("citation_ready", False)),
        "formal_reference_allowed": bool(context.get("formal_reference_allowed", False)),
        "manual_review_required": bool(context.get("manual_review_required", True)),
    }
