"""Fail-closed visual reasoning chains for formal competition papers.

The contract is deliberately declarative.  It does not guess whether a paper
"looks good" and it does not invent a chart.  Instead, every declared task must
bind a model/equation, an intermediate result, a final result, a formal figure,
and two exact pieces of public discussion.  This closes the common black-box
gap between a formula and the final number.
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any


CONTRACT_VERSION = "visual_reasoning_contract_v1"
DISPLAY_PROFILE = "STRONG_EVIDENCE_STORY"
ALLOWED_REASONING_ROLES = {
    "MODEL_ENTRY_EXPLANATION",
    "GEOMETRY_INTERSECTION",
    "ENUMERATION_PROJECTION",
    "STATE_TRANSITION",
    "CONVERGENCE",
    "RESIDUAL_VECTOR",
    "RANK_SPECTRUM",
    "COMPARISON",
    "MECHANISM_EXPLANATION",
    "OTHER_EXPLANATORY",
}
ROLE_FAMILIES = {
    "MODEL_STRUCTURE": {"MODEL_ENTRY_EXPLANATION", "GEOMETRY_INTERSECTION", "MECHANISM_EXPLANATION", "RANK_SPECTRUM"},
    "INTERMEDIATE_PROCESS": {"ENUMERATION_PROJECTION", "STATE_TRANSITION", "CONVERGENCE"},
    "RESULT_DIAGNOSTIC": {"RESIDUAL_VECTOR", "COMPARISON", "RANK_SPECTRUM"},
}
MODEL_ENTRY_FORMS = {
    "GEOMETRIC_CONFIGURATION",
    "CASE_CLASSIFICATION",
    "STATE_CONFIGURATION",
    "DATA_RELATION",
    "MECHANISM_DIAGRAM",
    "NETWORK_TOPOLOGY",
    "TEMPORAL_DYNAMICS",
    "OPTIMIZATION_LANDSCAPE",
    "FEASIBLE_REGION",
    "RESOURCE_SCHEDULE",
    "UNCERTAINTY_STRUCTURE",
    "INDICATOR_RELATION",
}

# A model-entry figure is routed by what the question needs to explain, not by
# the case identity.  This keeps the same contract useful for geometry,
# prediction, optimisation, scheduling, network and uncertainty problems.
VISUAL_FAMILY_RULES = {
    "SPATIAL_GEOMETRY": {
        "forms": {"GEOMETRIC_CONFIGURATION", "CASE_CLASSIFICATION"},
        "figure_types": {"geometric_schematic", "scatter", "contour"},
    },
    "NETWORK_STRUCTURE": {
        "forms": {"NETWORK_TOPOLOGY", "MECHANISM_DIAGRAM"},
        "figure_types": {"geometric_schematic"},
    },
    "DYNAMIC_PROCESS": {
        "forms": {"STATE_CONFIGURATION", "MECHANISM_DIAGRAM", "TEMPORAL_DYNAMICS"},
        "figure_types": {"geometric_schematic", "trajectory_2d", "trajectory_3d", "multi_line"},
    },
    "DATA_PATTERN": {
        "forms": {"DATA_RELATION", "CASE_CLASSIFICATION"},
        "figure_types": {"scatter", "heatmap", "contour", "filled_contour"},
    },
    "OPTIMIZATION_DECISION": {
        "forms": {"OPTIMIZATION_LANDSCAPE", "FEASIBLE_REGION", "MECHANISM_DIAGRAM"},
        "figure_types": {"contour", "filled_contour", "heatmap", "surface_3d", "geometric_schematic"},
    },
    "SCHEDULING_RESOURCE": {
        "forms": {"RESOURCE_SCHEDULE", "STATE_CONFIGURATION"},
        "figure_types": {"interval_timeline", "heatmap"},
    },
    "STATISTICAL_UNCERTAINTY": {
        "forms": {"UNCERTAINTY_STRUCTURE", "DATA_RELATION"},
        "figure_types": {"error_bar", "uncertainty_band", "scatter"},
    },
    "EVALUATION_COMPARISON": {
        "forms": {"INDICATOR_RELATION", "DATA_RELATION"},
        "figure_types": {"grouped_bar", "heatmap"},
    },
}
FORM_FIGURE_TYPES = {
    "GEOMETRIC_CONFIGURATION": {"geometric_schematic"},
    "CASE_CLASSIFICATION": {"geometric_schematic", "scatter"},
    "STATE_CONFIGURATION": {"geometric_schematic", "trajectory_2d", "trajectory_3d", "interval_timeline"},
    "DATA_RELATION": {"scatter", "heatmap", "contour", "filled_contour"},
    "MECHANISM_DIAGRAM": {"geometric_schematic", "trajectory_2d", "trajectory_3d", "multi_line"},
    "NETWORK_TOPOLOGY": {"geometric_schematic"},
    "TEMPORAL_DYNAMICS": {"trajectory_2d", "trajectory_3d", "multi_line"},
    "OPTIMIZATION_LANDSCAPE": {"contour", "filled_contour", "heatmap", "surface_3d"},
    "FEASIBLE_REGION": {"contour", "filled_contour", "heatmap", "geometric_schematic"},
    "RESOURCE_SCHEDULE": {"interval_timeline", "heatmap"},
    "UNCERTAINTY_STRUCTURE": {"error_bar", "uncertainty_band", "scatter"},
    "INDICATOR_RELATION": {"grouped_bar", "heatmap"},
}


class VisualReasoningContractError(ValueError):
    """Raised when a formal visual reasoning declaration is malformed."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _failure(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VisualReasoningContractError(f"VISUAL_REASONING_REQUIRED_TEXT: {field}")
    return value.strip()


def _list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise VisualReasoningContractError(f"VISUAL_REASONING_REQUIRED_LIST: {field}")
    return value


def _question_payload(content_root: Path, content: dict[str, str], key: str) -> dict[str, Any]:
    relative = content.get(key)
    if not isinstance(relative, str) or not relative.strip():
        raise VisualReasoningContractError(f"VISUAL_REASONING_TASK_CONTENT_MISSING: {key}")
    path = (content_root / relative).resolve()
    try:
        path.relative_to(content_root.resolve())
    except ValueError as exc:
        raise VisualReasoningContractError(f"VISUAL_REASONING_CONTENT_ESCAPES_ROOT: {key}") from exc
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VisualReasoningContractError(f"VISUAL_REASONING_TASK_CONTENT_INVALID: {key}") from exc
    if not isinstance(payload, dict):
        raise VisualReasoningContractError(f"VISUAL_REASONING_TASK_CONTENT_INVALID: {key}")
    return payload


def _question_text(payload: dict[str, Any]) -> str:
    parts = [str(payload.get("intro") or ""), str(payload.get("closing_optional") or "")]
    for section in payload.get("sections", []):
        if isinstance(section, dict):
            parts.extend((str(section.get("title") or ""), str(section.get("content") or "")))
    for block in payload.get("derivation_blocks", []):
        if not isinstance(block, dict):
            continue
        parts.extend(
            (
                str(block.get("title") or ""),
                str(block.get("method_selection_basis") or ""),
                str(block.get("physical_explanation") or ""),
                str(block.get("assumptions_and_boundary") or ""),
            )
        )
        for step in block.get("steps", []):
            if isinstance(step, dict):
                parts.extend((str(step.get("formula_tex") or ""), str(step.get("explanation") or "")))
    return "\n".join(part for part in parts if part.strip())


def _question_equation_labels(payload: dict[str, Any]) -> set[str]:
    labels: set[str] = set()
    for block in payload.get("derivation_blocks", []):
        if not isinstance(block, dict):
            continue
        for step in block.get("steps", []):
            if isinstance(step, dict) and isinstance(step.get("equation_label"), str) and step["equation_label"].strip():
                labels.add(step["equation_label"].strip())
    return labels


def _model_entry_semantic_ids(spec: dict[str, Any]) -> set[str]:
    """Return semantic identifiers that are materially present in figure data."""

    figure_type = str(spec.get("figure_type") or "").strip()
    identifiers: set[str] = set()
    for field in ("x_label", "y_label", "z_label", "colorbar_label"):
        value = str(spec.get(field) or "").strip()
        if value:
            identifiers.add(value)
    if figure_type == "geometric_schematic":
        identifiers.update(
            str(item.get("object_id") or "").strip()
            for panel in spec.get("panels", []) if isinstance(panel, dict)
            for item in panel.get("objects", []) if isinstance(item, dict)
            if str(item.get("object_id") or "").strip()
        )
    elif figure_type == "interval_timeline":
        identifiers.update(
            str(lane.get("lane_id") or "").strip()
            for lane in spec.get("lanes", []) if isinstance(lane, dict)
            if str(lane.get("lane_id") or "").strip()
        )
    if figure_type in {"line", "multi_line", "scatter", "grouped_bar"}:
        identifiers.update(
            str(series.get("name") or "").strip()
            for series in spec.get("series", []) if isinstance(series, dict)
            if str(series.get("name") or "").strip()
        )
    if figure_type in {"bar", "grouped_bar", "error_bar"}:
        identifiers.update(str(item).strip() for item in spec.get("categories", []) if str(item).strip())
    series_name = str(spec.get("series_name") or "").strip()
    if series_name:
        identifiers.add(series_name)
    return identifiers


def validate_visual_reasoning_contract(
    spec: dict[str, Any],
    *,
    task_contract: dict[str, Any],
    figure_placement: dict[str, Any],
    result_acceptance_contract: dict[str, Any],
    paper_evidence_contract: dict[str, Any],
    content_root: Path,
    content: dict[str, str],
) -> dict[str, Any]:
    """Validate one visible model-to-intermediate-to-result chain per task."""

    if not isinstance(spec, dict):
        raise VisualReasoningContractError("VISUAL_REASONING_CONTRACT_REQUIRED")
    if spec.get("version") != CONTRACT_VERSION:
        raise VisualReasoningContractError("VISUAL_REASONING_CONTRACT_VERSION_INVALID")
    if spec.get("display_profile") != DISPLAY_PROFILE:
        raise VisualReasoningContractError("VISUAL_REASONING_STRONG_DISPLAY_PROFILE_REQUIRED")

    tasks = task_contract.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise VisualReasoningContractError("VISUAL_REASONING_TASK_CONTRACT_INVALID")
    task_map = {str(task.get("task_id")): task for task in tasks if isinstance(task, dict)}
    if len(task_map) != len(tasks):
        raise VisualReasoningContractError("VISUAL_REASONING_TASK_CONTRACT_INVALID")

    figures = figure_placement.get("figures") if isinstance(figure_placement, dict) else None
    if not isinstance(figures, list):
        raise VisualReasoningContractError("VISUAL_REASONING_FIGURE_PLAN_REQUIRED")
    figure_map = {
        str(figure.get("figure_id")): figure
        for figure in figures
        if isinstance(figure, dict) and isinstance(figure.get("figure_id"), str)
    }
    formal_figures = [
        figure for figure in figures
        if isinstance(figure, dict) and figure.get("formal", True) is True
    ]
    # Every task gets a problem-specific relation figure before its first
    # derivation; at least one genuinely hard task also gets a process or
    # diagnostic figure.  The extra figure is tied to explanatory value, not
    # to a decorative page quota.
    minimum_figure_count = len(tasks) + 1
    result_cards = result_acceptance_contract.get("result_cards") if isinstance(result_acceptance_contract, dict) else None
    if not isinstance(result_cards, list):
        raise VisualReasoningContractError("VISUAL_REASONING_RESULT_CONTRACT_REQUIRED")
    result_ids = {
        str(card.get("result_id"))
        for card in result_cards
        if isinstance(card, dict) and isinstance(card.get("result_id"), str) and card["result_id"].strip()
    }
    model_blocks = paper_evidence_contract.get("model_blocks") if isinstance(paper_evidence_contract, dict) else None
    if not isinstance(model_blocks, list):
        raise VisualReasoningContractError("VISUAL_REASONING_MODEL_CONTRACT_REQUIRED")
    model_tasks = {
        str(block.get("model_id")): str(block.get("task_id"))
        for block in model_blocks
        if isinstance(block, dict) and isinstance(block.get("model_id"), str)
    }

    raw_chains = _list(spec.get("task_chains"), "visual_reasoning_contract.task_chains")
    failures: list[dict[str, str]] = []
    seen_tasks: set[str] = set()
    seen_chains: set[str] = set()
    normalized: list[dict[str, Any]] = []
    referenced_visual_ids: set[str] = set()
    all_roles: set[str] = set()
    public_takeaways: set[str] = set()

    for index, raw in enumerate(raw_chains, 1):
        if not isinstance(raw, dict):
            raise VisualReasoningContractError(f"VISUAL_REASONING_CHAIN_INVALID: {index}")
        chain_id = _text(raw.get("chain_id"), f"task_chains[{index}].chain_id")
        task_id = _text(raw.get("task_id"), f"task_chains[{index}].task_id")
        if chain_id in seen_chains:
            failures.append(_failure("VISUAL_REASONING_CHAIN_DUPLICATED", chain_id))
        seen_chains.add(chain_id)
        if task_id in seen_tasks:
            failures.append(_failure("VISUAL_REASONING_TASK_DUPLICATED", task_id))
        seen_tasks.add(task_id)
        task = task_map.get(task_id)
        if task is None:
            failures.append(_failure("VISUAL_REASONING_UNKNOWN_TASK", task_id))
            continue

        model_ids = [_text(item, f"{chain_id}.model_ids") for item in _list(raw.get("model_ids"), f"{chain_id}.model_ids")]
        equation_labels = [_text(item, f"{chain_id}.equation_labels") for item in _list(raw.get("equation_labels", []), f"{chain_id}.equation_labels")]
        intermediate_ids = [_text(item, f"{chain_id}.intermediate_result_ids") for item in _list(raw.get("intermediate_result_ids"), f"{chain_id}.intermediate_result_ids")]
        final_ids = [_text(item, f"{chain_id}.final_result_ids") for item in _list(raw.get("final_result_ids"), f"{chain_id}.final_result_ids")]
        visual_ids = [_text(item, f"{chain_id}.visual_ids") for item in _list(raw.get("visual_ids"), f"{chain_id}.visual_ids")]
        referenced_visual_ids.update(visual_ids)
        model_marker = _text(raw.get("model_to_intermediate_marker"), f"{chain_id}.model_to_intermediate_marker")
        result_marker = _text(raw.get("intermediate_to_result_marker"), f"{chain_id}.intermediate_to_result_marker")

        if not model_ids:
            failures.append(_failure("VISUAL_REASONING_MODEL_LINK_REQUIRED", task_id))
        if not equation_labels and not str(raw.get("equation_not_applicable_reason") or "").strip():
            failures.append(_failure("VISUAL_REASONING_EQUATION_LINK_OR_REASON_REQUIRED", task_id))
        if not intermediate_ids:
            failures.append(_failure("VISUAL_REASONING_INTERMEDIATE_EVIDENCE_REQUIRED", task_id))
        if not final_ids:
            failures.append(_failure("VISUAL_REASONING_FINAL_RESULT_REQUIRED", task_id))
        if not visual_ids:
            failures.append(_failure("VISUAL_REASONING_EXPLANATORY_FIGURE_REQUIRED", task_id))
        if set(intermediate_ids) & set(final_ids):
            failures.append(_failure("VISUAL_REASONING_INTERMEDIATE_FINAL_MUST_DIFFER", task_id))

        for model_id in model_ids:
            if model_id not in model_tasks:
                failures.append(_failure("VISUAL_REASONING_MODEL_UNKNOWN", f"{task_id}.{model_id}"))
            elif model_tasks[model_id] != task_id:
                failures.append(_failure("VISUAL_REASONING_MODEL_TASK_MISMATCH", f"{task_id}.{model_id}"))
        for result_id in [*intermediate_ids, *final_ids]:
            if result_id not in result_ids:
                failures.append(_failure("VISUAL_REASONING_RESULT_UNKNOWN", f"{task_id}.{result_id}"))

        question_key = str(task.get("content_key") or "")
        question = _question_payload(content_root, content, question_key)
        question_text = _question_text(question)
        section_titles = {
            str(section.get("title")).strip()
            for section in question.get("sections", [])
            if isinstance(section, dict) and str(section.get("title") or "").strip()
        }
        available_labels = _question_equation_labels(question)
        for label in equation_labels:
            if label not in available_labels:
                failures.append(_failure("VISUAL_REASONING_EQUATION_UNKNOWN", f"{task_id}.{label}"))
        if model_marker not in question_text:
            failures.append(_failure("VISUAL_REASONING_MODEL_TO_INTERMEDIATE_NOT_VISIBLE", task_id))
        if result_marker not in question_text:
            failures.append(_failure("VISUAL_REASONING_INTERMEDIATE_TO_RESULT_NOT_VISIBLE", task_id))

        roles: list[str] = []
        model_entry_count = 0
        for visual_id in visual_ids:
            figure = figure_map.get(visual_id)
            if figure is None:
                failures.append(_failure("VISUAL_REASONING_FIGURE_UNKNOWN", f"{task_id}.{visual_id}"))
                continue
            if figure.get("task_id") != task_id:
                failures.append(_failure("VISUAL_REASONING_FIGURE_TASK_MISMATCH", f"{task_id}.{visual_id}"))
            if figure.get("reasoning_chain_id") != chain_id:
                failures.append(_failure("VISUAL_REASONING_FIGURE_CHAIN_MISMATCH", f"{task_id}.{visual_id}"))
            role = str(figure.get("reasoning_role") or "").strip().upper()
            if role not in ALLOWED_REASONING_ROLES:
                failures.append(_failure("VISUAL_REASONING_ROLE_INVALID", f"{task_id}.{visual_id}"))
            roles.append(role)
            all_roles.add(role)
            if role == "MODEL_ENTRY_EXPLANATION":
                model_entry_count += 1
                if figure.get("placement_slot") != "TASK_MODEL_ENTRY":
                    failures.append(_failure("VISUAL_REASONING_MODEL_ENTRY_PLACEMENT_REQUIRED", f"{task_id}.{visual_id}"))
                form = str(figure.get("explanatory_form") or "").strip().upper()
                if form not in MODEL_ENTRY_FORMS:
                    failures.append(_failure("VISUAL_REASONING_MODEL_ENTRY_FORM_INVALID", f"{task_id}.{visual_id}"))
                visual_family = str(figure.get("task_visual_family") or "").strip().upper()
                family_rule = VISUAL_FAMILY_RULES.get(visual_family)
                if family_rule is None:
                    failures.append(_failure("VISUAL_REASONING_MODEL_ENTRY_VISUAL_FAMILY_REQUIRED", f"{task_id}.{visual_id}"))
                elif form not in family_rule["forms"]:
                    failures.append(_failure("VISUAL_REASONING_MODEL_ENTRY_FORM_FAMILY_MISMATCH", f"{task_id}.{visual_id}"))
                object_bindings = figure.get("object_bindings")
                normalized_object_bindings = {
                    str(item).strip() for item in object_bindings or [] if str(item).strip()
                } if isinstance(object_bindings, list) else set()
                if len(normalized_object_bindings) < 2:
                    failures.append(_failure("VISUAL_REASONING_MODEL_ENTRY_OBJECT_BINDINGS_REQUIRED", f"{task_id}.{visual_id}"))
                inline_spec = figure.get("matlab_figure_spec")
                if not isinstance(inline_spec, dict):
                    failures.append(_failure("VISUAL_REASONING_MODEL_ENTRY_FIGURE_SPEC_REQUIRED", f"{task_id}.{visual_id}"))
                else:
                    figure_type = str(inline_spec.get("figure_type") or "").strip()
                    if family_rule is not None and figure_type not in family_rule["figure_types"]:
                        failures.append(_failure("VISUAL_REASONING_MODEL_ENTRY_FIGURE_FAMILY_MISMATCH", f"{task_id}.{visual_id}"))
                    if form in FORM_FIGURE_TYPES and figure_type not in FORM_FIGURE_TYPES[form]:
                        failures.append(_failure("VISUAL_REASONING_MODEL_ENTRY_FORM_FIGURE_MISMATCH", f"{task_id}.{visual_id}"))
                    semantic_ids = _model_entry_semantic_ids(inline_spec)
                    if not normalized_object_bindings.issubset(semantic_ids):
                        failures.append(_failure("VISUAL_REASONING_MODEL_ENTRY_OBJECT_SPEC_MISMATCH", f"{task_id}.{visual_id}"))
                entry_lead = str(figure.get("entry_lead") or "").strip()
                choice_reason = str(figure.get("model_choice_reason") or "").strip()
                relation_statement = str(figure.get("relation_statement") or "").strip()
                if not entry_lead or entry_lead not in str(question.get("intro") or ""):
                    failures.append(_failure("VISUAL_REASONING_MODEL_ENTRY_LEAD_NOT_VISIBLE", f"{task_id}.{visual_id}"))
                if not choice_reason or choice_reason not in str(question.get("intro") or ""):
                    failures.append(_failure("VISUAL_REASONING_MODEL_CHOICE_REASON_NOT_VISIBLE", f"{task_id}.{visual_id}"))
                if not relation_statement or relation_statement not in question_text:
                    failures.append(_failure("VISUAL_REASONING_MODEL_ENTRY_RELATION_NOT_VISIBLE", f"{task_id}.{visual_id}"))
            if str(figure.get("first_discussion") or "").strip() not in section_titles:
                failures.append(_failure("VISUAL_REASONING_FIGURE_NOT_DISCLOSED_IN_TASK", f"{task_id}.{visual_id}"))
            reader_question = str(figure.get("reader_question") or "").strip()
            public_takeaway = str(figure.get("public_takeaway") or "").strip()
            if not reader_question:
                failures.append(_failure("VISUAL_REASONING_READER_QUESTION_REQUIRED", f"{task_id}.{visual_id}"))
            if not public_takeaway:
                failures.append(_failure("VISUAL_REASONING_PUBLIC_TAKEAWAY_REQUIRED", f"{task_id}.{visual_id}"))
            elif public_takeaway not in question_text:
                failures.append(_failure("VISUAL_REASONING_PUBLIC_TAKEAWAY_NOT_VISIBLE", f"{task_id}.{visual_id}"))
            elif public_takeaway in public_takeaways:
                failures.append(_failure("VISUAL_REASONING_PUBLIC_TAKEAWAY_DUPLICATED", public_takeaway))
            public_takeaways.add(public_takeaway)
            logic_links = figure.get("logic_links")
            if not isinstance(logic_links, dict):
                failures.append(_failure("VISUAL_REASONING_FIGURE_LOGIC_LINKS_REQUIRED", f"{task_id}.{visual_id}"))
                continue
            linked_models = {str(item) for item in logic_links.get("model_ids", []) if str(item).strip()}
            linked_equations = {str(item) for item in logic_links.get("equation_labels", []) if str(item).strip()}
            linked_intermediate = {str(item) for item in logic_links.get("intermediate_result_ids", []) if str(item).strip()}
            linked_final = {str(item) for item in logic_links.get("final_result_ids", []) if str(item).strip()}
            layers = sum(bool(group) for group in ((linked_models | linked_equations), linked_intermediate, linked_final))
            if layers < 2:
                failures.append(_failure("VISUAL_REASONING_FIGURE_MUST_LINK_TWO_LAYERS", f"{task_id}.{visual_id}"))
            if not linked_models.issubset(set(model_ids)):
                failures.append(_failure("VISUAL_REASONING_FIGURE_MODEL_LINK_MISMATCH", f"{task_id}.{visual_id}"))
            if not linked_equations.issubset(set(equation_labels)):
                failures.append(_failure("VISUAL_REASONING_FIGURE_EQUATION_LINK_MISMATCH", f"{task_id}.{visual_id}"))
            if not linked_intermediate.issubset(set(intermediate_ids)):
                failures.append(_failure("VISUAL_REASONING_FIGURE_INTERMEDIATE_LINK_MISMATCH", f"{task_id}.{visual_id}"))
            if not linked_final.issubset(set(final_ids)):
                failures.append(_failure("VISUAL_REASONING_FIGURE_FINAL_LINK_MISMATCH", f"{task_id}.{visual_id}"))
            if role == "MODEL_ENTRY_EXPLANATION" and (not linked_models or not linked_equations):
                failures.append(_failure("VISUAL_REASONING_MODEL_ENTRY_MUST_BIND_MODEL_AND_EQUATION", f"{task_id}.{visual_id}"))

        if model_entry_count != 1:
            failures.append(_failure("VISUAL_REASONING_ONE_MODEL_ENTRY_FIGURE_PER_TASK_REQUIRED", f"{task_id}; actual={model_entry_count}"))

        normalized.append(
            {
                "chain_id": chain_id,
                "task_id": task_id,
                "model_ids": model_ids,
                "equation_labels": equation_labels,
                "intermediate_result_ids": intermediate_ids,
                "final_result_ids": final_ids,
                "visual_ids": visual_ids,
                "reasoning_roles": roles,
                "public_markers": [model_marker, result_marker],
            }
        )

    for task_id in sorted(set(task_map) - seen_tasks):
        failures.append(_failure("VISUAL_REASONING_CHAIN_MISSING_FOR_TASK", task_id))
    for task_id in sorted(seen_tasks - set(task_map)):
        failures.append(_failure("VISUAL_REASONING_CHAIN_UNKNOWN_TASK", task_id))
    formal_figure_ids = {
        str(figure.get("figure_id")) for figure in formal_figures if figure.get("figure_id")
    }
    if len(formal_figures) < minimum_figure_count:
        failures.append(
            _failure(
                "VISUAL_REASONING_STRONG_FIGURE_COUNT_NOT_MET",
                f"required={minimum_figure_count}; actual={len(formal_figures)}",
            )
        )
    if not any(len(chain.get("visual_ids", [])) >= 2 for chain in normalized):
        failures.append(_failure("VISUAL_REASONING_FOCUS_TASK_SUPPORT_VISUAL_REQUIRED", "no task has an additional process or diagnostic figure"))
    if formal_figure_ids != referenced_visual_ids:
        failures.append(
            _failure(
                "VISUAL_REASONING_UNREFERENCED_OR_MISSING_FORMAL_FIGURE",
                f"formal={sorted(formal_figure_ids)}; referenced={sorted(referenced_visual_ids)}",
            )
        )
    for family, family_roles in ROLE_FAMILIES.items():
        if not (all_roles & family_roles):
            failures.append(_failure("VISUAL_REASONING_ROLE_FAMILY_MISSING", family))

    return {
        "status": "FAIL" if failures else "PASS",
        "contract_version": CONTRACT_VERSION,
        "display_profile": DISPLAY_PROFILE,
        "hard_failures": failures,
        "warnings": [],
        "task_count": len(task_map),
        "chain_count": len(normalized),
        "minimum_formal_figure_count": minimum_figure_count,
        "formal_figure_count": len(formal_figures),
        "role_families_covered": sorted(
            family for family, roles in ROLE_FAMILIES.items() if all_roles & roles
        ),
        "task_chains": normalized,
    }


def validate_visual_reasoning_trace_report(
    payload: dict[str, Any], paper_root: Path
) -> dict[str, Any]:
    """Revalidate the renderer trace against immutable paper assets."""

    failures: list[str] = []
    if not isinstance(payload, dict):
        return {"status": "FAIL", "reason": "VISUAL_REASONING_TRACE_INVALID"}
    if payload.get("schema_version") != "visual_reasoning_trace_report_v1":
        failures.append("VISUAL_REASONING_TRACE_VERSION_INVALID")
    if payload.get("generated_by") != "paper_renderer":
        failures.append("VISUAL_REASONING_TRACE_PRODUCER_INVALID")
    if payload.get("status") != "PASS":
        failures.append("VISUAL_REASONING_TRACE_NOT_PASSED")
    if payload.get("policy_version") not in {
        "controlled_writing_layer_v4",
        "controlled_writing_layer_v5",
    }:
        failures.append("VISUAL_REASONING_TRACE_POLICY_INVALID")
    if payload.get("manifest_mode") != "CURRENT_FORMAL":
        failures.append("VISUAL_REASONING_TRACE_MODE_INVALID")

    visual = payload.get("visual_reasoning")
    if not isinstance(visual, dict) or visual.get("status") != "PASS":
        failures.append("VISUAL_REASONING_CONTRACT_NOT_PASSED")
        visual = {}
    if visual.get("display_profile") != DISPLAY_PROFILE:
        failures.append("VISUAL_REASONING_STRONG_DISPLAY_PROFILE_MISSING")
    task_count = visual.get("task_count")
    chain_count = visual.get("chain_count")
    chains = visual.get("task_chains")
    if (
        not isinstance(task_count, int)
        or task_count <= 0
        or not isinstance(chain_count, int)
        or chain_count != task_count
        or not isinstance(chains, list)
        or len(chains) != task_count
    ):
        failures.append("VISUAL_REASONING_TASK_COVERAGE_INVALID")
    minimum_figure_count = visual.get("minimum_formal_figure_count")
    formal_figure_count = visual.get("formal_figure_count")
    if (
        not isinstance(minimum_figure_count, int)
        or not isinstance(task_count, int)
        or minimum_figure_count != task_count + 1
        or not isinstance(formal_figure_count, int)
        or formal_figure_count < minimum_figure_count
    ):
        failures.append("VISUAL_REASONING_STRONG_FIGURE_COUNT_INVALID")
    if set(visual.get("role_families_covered", [])) != set(ROLE_FAMILIES):
        failures.append("VISUAL_REASONING_ROLE_FAMILY_COVERAGE_INVALID")

    declared_visual_ids: set[str] = set()
    if isinstance(chains, list):
        for chain in chains:
            ids = chain.get("visual_ids") if isinstance(chain, dict) else None
            if not isinstance(ids, list) or not ids:
                failures.append("VISUAL_REASONING_CHAIN_VISUAL_MISSING")
                continue
            declared_visual_ids.update(str(item) for item in ids)

    main_tex = paper_root / "main.tex"
    expected_main_hash = payload.get("main_tex_sha256")
    if (
        not main_tex.is_file()
        or not isinstance(expected_main_hash, str)
        or _sha256(main_tex) != expected_main_hash.lower()
    ):
        failures.append("VISUAL_REASONING_MAIN_TEX_HASH_MISMATCH")

    assets = payload.get("figure_assets")
    bound_visual_ids: set[str] = set()
    if not isinstance(assets, list) or not assets:
        failures.append("VISUAL_REASONING_ASSET_BINDINGS_REQUIRED")
    else:
        for asset in assets:
            if not isinstance(asset, dict):
                failures.append("VISUAL_REASONING_ASSET_BINDING_INVALID")
                continue
            figure_id = str(asset.get("figure_id") or "")
            relative = asset.get("path")
            expected_hash = asset.get("sha256")
            if not figure_id or not isinstance(relative, str) or not isinstance(expected_hash, str):
                failures.append("VISUAL_REASONING_ASSET_BINDING_INVALID")
                continue
            path = (paper_root / relative).resolve()
            try:
                path.relative_to(paper_root.resolve())
            except ValueError:
                failures.append("VISUAL_REASONING_ASSET_ESCAPES_PAPER")
                continue
            if not path.is_file() or _sha256(path) != expected_hash.lower():
                failures.append(f"VISUAL_REASONING_ASSET_HASH_MISMATCH:{figure_id}")
                continue
            bound_visual_ids.add(figure_id)
    if declared_visual_ids - bound_visual_ids:
        failures.append("VISUAL_REASONING_DECLARED_FIGURE_NOT_BOUND")

    return {
        "status": "FAIL" if failures else "PASS",
        "reason": failures[0] if failures else "PASS",
        "failures": failures,
        "task_count": task_count,
        "chain_count": chain_count,
        "declared_visual_ids": sorted(declared_visual_ids),
        "bound_visual_ids": sorted(bound_visual_ids),
    }
