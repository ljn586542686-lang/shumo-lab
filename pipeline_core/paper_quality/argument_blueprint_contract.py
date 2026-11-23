"""Deterministic task-specific argument bindings for formal papers.

The contract does not score prose quality or try to detect AI writing.  It
only proves that every formal task declaration is tied to real task content,
model/equation records, assumptions, upstream results and the accepted answer.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


CONTRACT_VERSION = "argument_blueprint_contract_v1"
TRACE_VERSION = "argument_blueprint_trace_report_v1"
REQUIRED_POLICY_VERSION = "controlled_writing_layer_v5"
SHA256_RE = re.compile(r"[0-9a-f]{64}")


class ArgumentBlueprintContractError(ValueError):
    """Raised when the declaration itself is malformed."""


def _failure(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ArgumentBlueprintContractError(f"ARGUMENT_BLUEPRINT_REQUIRED_TEXT: {field}")
    return value.strip()


def _items(value: Any, field: str, *, default: list[Any] | None = None) -> list[Any]:
    if value is None and default is not None:
        return default
    if not isinstance(value, list):
        raise ArgumentBlueprintContractError(f"ARGUMENT_BLUEPRINT_REQUIRED_LIST: {field}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_question(content_root: Path, content: dict[str, str], key: str) -> dict[str, Any]:
    relative = content.get(key)
    if not isinstance(relative, str) or not relative.strip():
        raise ArgumentBlueprintContractError(f"ARGUMENT_BLUEPRINT_TASK_CONTENT_MISSING: {key}")
    path = (content_root / relative).resolve()
    try:
        path.relative_to(content_root.resolve())
    except ValueError as exc:
        raise ArgumentBlueprintContractError(f"ARGUMENT_BLUEPRINT_CONTENT_ESCAPES_ROOT: {key}") from exc
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArgumentBlueprintContractError(f"ARGUMENT_BLUEPRINT_TASK_CONTENT_INVALID: {key}") from exc
    if not isinstance(payload, dict):
        raise ArgumentBlueprintContractError(f"ARGUMENT_BLUEPRINT_TASK_CONTENT_INVALID: {key}")
    return payload


def _load_text(content_root: Path, content: dict[str, str], key: str) -> str:
    relative = content.get(key)
    if not isinstance(relative, str) or not relative.strip():
        return ""
    path = (content_root / relative).resolve()
    try:
        path.relative_to(content_root.resolve())
        return path.read_text(encoding="utf-8")
    except (ValueError, OSError, UnicodeError):
        return ""


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
        for variable in block.get("variables", []):
            if isinstance(variable, dict):
                parts.extend((str(variable.get("symbol") or ""), str(variable.get("definition") or "")))
        for step in block.get("steps", []):
            if isinstance(step, dict):
                parts.extend((str(step.get("formula_tex") or ""), str(step.get("explanation") or "")))
    return "\n".join(part for part in parts if part.strip())


def _equation_labels(payload: dict[str, Any]) -> set[str]:
    return {
        str(step.get("equation_label") or "").strip()
        for block in payload.get("derivation_blocks", []) if isinstance(block, dict)
        for step in block.get("steps", []) if isinstance(step, dict)
        if str(step.get("equation_label") or "").strip()
    }


def _variable_definitions(payload: dict[str, Any]) -> dict[str, set[str]]:
    definitions: dict[str, set[str]] = {}
    for block in payload.get("derivation_blocks", []):
        if not isinstance(block, dict):
            continue
        for variable in block.get("variables", []):
            if not isinstance(variable, dict):
                continue
            symbol = str(variable.get("symbol") or "").strip()
            definition = str(variable.get("definition") or "").strip()
            if symbol and definition:
                definitions.setdefault(symbol, set()).add(definition)
    return definitions


def _identifier_set(values: Any, field: str) -> set[str]:
    return {_text(value, f"{field}.item") for value in _items(values, field, default=[])}


def _visible(marker: str, text: str, code: str, owner: str, failures: list[dict[str, str]]) -> None:
    if marker not in text:
        failures.append(_failure(code, owner))


def validate_argument_blueprint_contract(
    spec: dict[str, Any],
    *,
    task_contract: dict[str, Any],
    result_acceptance_contract: dict[str, Any],
    paper_evidence_contract: dict[str, Any],
    content_root: Path,
    content: dict[str, str],
) -> dict[str, Any]:
    """Validate bindings and visibility without judging whether prose is good."""

    if not isinstance(spec, dict):
        raise ArgumentBlueprintContractError("ARGUMENT_BLUEPRINT_CONTRACT_REQUIRED")
    if spec.get("version") != CONTRACT_VERSION:
        raise ArgumentBlueprintContractError("ARGUMENT_BLUEPRINT_VERSION_INVALID")
    repair = spec.get("repair_policy")
    if not isinstance(repair, dict):
        raise ArgumentBlueprintContractError("ARGUMENT_BLUEPRINT_REPAIR_POLICY_REQUIRED")
    retries = repair.get("max_automatic_retries")
    if not isinstance(retries, int) or isinstance(retries, bool) or not 0 <= retries <= 2:
        raise ArgumentBlueprintContractError("ARGUMENT_BLUEPRINT_RETRY_LIMIT_INVALID")
    if repair.get("on_exhaustion") != "HUMAN_REVIEW_REQUIRED":
        raise ArgumentBlueprintContractError("ARGUMENT_BLUEPRINT_HUMAN_EXIT_REQUIRED")
    if repair.get("surface_rewrite_forbidden") is not True:
        raise ArgumentBlueprintContractError("ARGUMENT_BLUEPRINT_SURFACE_REWRITE_MUST_BE_FORBIDDEN")

    tasks = task_contract.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ArgumentBlueprintContractError("ARGUMENT_BLUEPRINT_TASK_CONTRACT_INVALID")
    task_map = {str(task.get("task_id") or "").strip(): task for task in tasks if isinstance(task, dict)}
    task_order = list(task_map)
    answers = {
        str(card.get("task_id") or "").strip(): card
        for card in result_acceptance_contract.get("answer_cards", []) if isinstance(card, dict)
    }
    result_ids = {
        str(card.get("result_id") or "").strip()
        for card in result_acceptance_contract.get("result_cards", []) if isinstance(card, dict)
    }
    models = {
        str(model.get("model_id") or "").strip(): model
        for model in paper_evidence_contract.get("model_blocks", []) if isinstance(model, dict)
    }
    assumptions = {
        str(item.get("assumption_id") or "").strip(): item
        for item in paper_evidence_contract.get("assumption_ledger", []) if isinstance(item, dict)
    }
    raw_blueprints = _items(spec.get("tasks"), "argument_blueprint.tasks")
    blueprint_map: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    task_reports: list[dict[str, Any]] = []

    for index, raw in enumerate(raw_blueprints, 1):
        if not isinstance(raw, dict):
            raise ArgumentBlueprintContractError(f"ARGUMENT_BLUEPRINT_TASK_INVALID: {index}")
        task_id = _text(raw.get("task_id"), f"tasks[{index}].task_id")
        if task_id in blueprint_map:
            failures.append(_failure("ARGUMENT_BLUEPRINT_TASK_DUPLICATED", task_id))
        blueprint_map[task_id] = raw
    for task_id in sorted(set(task_map) - set(blueprint_map)):
        failures.append(_failure("ARGUMENT_BLUEPRINT_TASK_MISSING", task_id))
    for task_id in sorted(set(blueprint_map) - set(task_map)):
        failures.append(_failure("ARGUMENT_BLUEPRINT_TASK_UNKNOWN", task_id))

    global_symbols: dict[str, set[str]] = {}
    answer_result_ids: dict[str, set[str]] = {
        task_id: _identifier_set(card.get("result_card_ids", []), f"answer.{task_id}.result_card_ids")
        for task_id, card in answers.items()
    }
    for task_id in task_order:
        raw = blueprint_map.get(task_id)
        task = task_map[task_id]
        if raw is None:
            continue
        question = _load_question(content_root, content, str(task.get("content_key") or ""))
        task_text = _load_text(content_root, content, str(task.get("analysis_content_key") or "")) + "\n" + _question_text(question)
        equation_labels = _equation_labels(question)
        for symbol, definitions in _variable_definitions(question).items():
            global_symbols.setdefault(symbol, set()).update(definitions)
        mode = str(raw.get("complexity_mode") or "").strip().upper()
        if mode not in {"FULL", "SIMPLE"}:
            failures.append(_failure("ARGUMENT_BLUEPRINT_COMPLEXITY_MODE_REQUIRED", task_id))
        if mode == "SIMPLE" and not str(raw.get("simple_mode_reason") or "").strip():
            failures.append(_failure("ARGUMENT_BLUEPRINT_SIMPLE_MODE_REASON_REQUIRED", task_id))

        object_records = _items(raw.get("research_objects"), f"{task_id}.research_objects", default=[])
        minimum_objects = 1 if mode == "SIMPLE" else 2
        if len(object_records) < minimum_objects:
            failures.append(_failure("ARGUMENT_BLUEPRINT_RESEARCH_OBJECTS_INSUFFICIENT", task_id))
        object_ids: set[str] = set()
        for object_index, item in enumerate(object_records, 1):
            if not isinstance(item, dict):
                raise ArgumentBlueprintContractError(f"ARGUMENT_BLUEPRINT_OBJECT_INVALID: {task_id}.{object_index}")
            object_id = _text(item.get("object_id"), f"{task_id}.research_objects[{object_index}].object_id")
            if object_id in object_ids:
                failures.append(_failure("ARGUMENT_BLUEPRINT_OBJECT_DUPLICATED", f"{task_id}.{object_id}"))
            object_ids.add(object_id)
            _text(item.get("public_name"), f"{task_id}.{object_id}.public_name")
            markers = _items(item.get("public_markers"), f"{task_id}.{object_id}.public_markers", default=[])
            if not markers:
                failures.append(_failure("ARGUMENT_BLUEPRINT_OBJECT_BINDING_REQUIRED", f"{task_id}.{object_id}"))
            for marker in markers:
                marker_text = _text(marker, f"{task_id}.{object_id}.marker")
                _visible(marker_text, task_text, "ARGUMENT_BLUEPRINT_OBJECT_NOT_VISIBLE", f"{task_id}.{object_id}", failures)

        model_ids = _identifier_set(raw.get("model_ids"), f"{task_id}.model_ids")
        if not model_ids:
            failures.append(_failure("ARGUMENT_BLUEPRINT_MODEL_BINDING_REQUIRED", task_id))
        for model_id in model_ids:
            model = models.get(model_id)
            if model is None or str(model.get("task_id") or "").strip() != task_id:
                failures.append(_failure("ARGUMENT_BLUEPRINT_MODEL_BINDING_INVALID", f"{task_id}.{model_id}"))

        relations = _items(raw.get("key_relations"), f"{task_id}.key_relations", default=[])
        if mode != "SIMPLE" and not relations:
            failures.append(_failure("ARGUMENT_BLUEPRINT_KEY_RELATION_REQUIRED", task_id))
        relation_ids: set[str] = set()
        for relation_index, relation in enumerate(relations, 1):
            if not isinstance(relation, dict):
                raise ArgumentBlueprintContractError(f"ARGUMENT_BLUEPRINT_RELATION_INVALID: {task_id}.{relation_index}")
            relation_id = _text(relation.get("relation_id"), f"{task_id}.key_relations[{relation_index}].relation_id")
            relation_ids.add(relation_id)
            linked_objects = _identifier_set(relation.get("object_ids"), f"{task_id}.{relation_id}.object_ids")
            if len(linked_objects) < 2 or not linked_objects.issubset(object_ids):
                failures.append(_failure("ARGUMENT_BLUEPRINT_RELATION_OBJECT_BINDING_INVALID", f"{task_id}.{relation_id}"))
            linked_equations = _identifier_set(relation.get("equation_labels"), f"{task_id}.{relation_id}.equation_labels")
            if linked_equations and not linked_equations.issubset(equation_labels):
                failures.append(_failure("ARGUMENT_BLUEPRINT_RELATION_EQUATION_BINDING_INVALID", f"{task_id}.{relation_id}"))
            marker = _text(relation.get("public_marker"), f"{task_id}.{relation_id}.public_marker")
            _visible(marker, task_text, "ARGUMENT_BLUEPRINT_RELATION_NOT_VISIBLE", f"{task_id}.{relation_id}", failures)

        meanings = _items(raw.get("equation_meanings"), f"{task_id}.equation_meanings", default=[])
        meaning_labels: set[str] = set()
        for meaning_index, meaning in enumerate(meanings, 1):
            if not isinstance(meaning, dict):
                raise ArgumentBlueprintContractError(f"ARGUMENT_BLUEPRINT_EQUATION_MEANING_INVALID: {task_id}.{meaning_index}")
            label = _text(meaning.get("equation_label"), f"{task_id}.equation_meanings[{meaning_index}].equation_label")
            meaning_labels.add(label)
            linked_objects = _identifier_set(meaning.get("object_ids"), f"{task_id}.{label}.object_ids")
            if not linked_objects or not linked_objects.issubset(object_ids):
                failures.append(_failure("ARGUMENT_BLUEPRINT_EQUATION_OBJECT_BINDING_INVALID", f"{task_id}.{label}"))
            marker = _text(meaning.get("meaning_marker"), f"{task_id}.{label}.meaning_marker")
            _visible(marker, task_text, "ARGUMENT_BLUEPRINT_EQUATION_MEANING_NOT_VISIBLE", f"{task_id}.{label}", failures)
        if equation_labels != meaning_labels:
            failures.append(_failure("ARGUMENT_BLUEPRINT_EQUATION_MAPPING_INCOMPLETE", f"{task_id}; expected={sorted(equation_labels)}; actual={sorted(meaning_labels)}"))
        if not equation_labels and not str(raw.get("equation_not_applicable_reason") or "").strip():
            failures.append(_failure("ARGUMENT_BLUEPRINT_EQUATION_NOT_APPLICABLE_REASON_REQUIRED", task_id))

        relevant_assumptions = {
            str(assumption_id)
            for model_id in model_ids for assumption_id in models.get(model_id, {}).get("assumption_ids", [])
        }
        assumption_status = str(raw.get("assumption_status") or "").strip().upper()
        assumption_links = _items(raw.get("assumption_links"), f"{task_id}.assumption_links", default=[])
        linked_assumptions: set[str] = set()
        for link_index, link in enumerate(assumption_links, 1):
            if not isinstance(link, dict):
                raise ArgumentBlueprintContractError(f"ARGUMENT_BLUEPRINT_ASSUMPTION_LINK_INVALID: {task_id}.{link_index}")
            assumption_id = _text(link.get("assumption_id"), f"{task_id}.assumption_links[{link_index}].assumption_id")
            linked_assumptions.add(assumption_id)
            if assumption_id not in assumptions:
                failures.append(_failure("ARGUMENT_BLUEPRINT_ASSUMPTION_UNKNOWN", f"{task_id}.{assumption_id}"))
            target_equations = _identifier_set(link.get("equation_labels"), f"{task_id}.{assumption_id}.equation_labels")
            target_relations = _identifier_set(link.get("relation_ids"), f"{task_id}.{assumption_id}.relation_ids")
            if not (target_equations or target_relations):
                failures.append(_failure("ARGUMENT_BLUEPRINT_ASSUMPTION_TARGET_REQUIRED", f"{task_id}.{assumption_id}"))
            if not target_equations.issubset(equation_labels) or not target_relations.issubset(relation_ids):
                failures.append(_failure("ARGUMENT_BLUEPRINT_ASSUMPTION_TARGET_INVALID", f"{task_id}.{assumption_id}"))
            marker = _text(link.get("public_marker"), f"{task_id}.{assumption_id}.public_marker")
            _visible(marker, task_text, "ARGUMENT_BLUEPRINT_ASSUMPTION_NOT_VISIBLE", f"{task_id}.{assumption_id}", failures)
        if relevant_assumptions:
            if assumption_status != "LINKED" or linked_assumptions != relevant_assumptions:
                failures.append(_failure("ARGUMENT_BLUEPRINT_ASSUMPTION_COVERAGE_INCOMPLETE", task_id))
        elif assumption_status != "NONE_DECLARED" or assumption_links:
            failures.append(_failure("ARGUMENT_BLUEPRINT_ASSUMPTION_NONE_DECLARATION_INVALID", task_id))

        answer = answers.get(task_id)
        result_answer = raw.get("result_answer")
        if answer is None or not isinstance(result_answer, dict):
            failures.append(_failure("ARGUMENT_BLUEPRINT_RESULT_ANSWER_REQUIRED", task_id))
        else:
            requirement = _text(result_answer.get("question_requirement_quote"), f"{task_id}.result_answer.question_requirement_quote")
            if requirement != str(answer.get("question_requirement") or "").strip():
                failures.append(_failure("ARGUMENT_BLUEPRINT_QUESTION_REQUIREMENT_MISMATCH", task_id))
            marker = _text(result_answer.get("answer_marker"), f"{task_id}.result_answer.answer_marker")
            if marker != str(answer.get("answer_marker") or "").strip():
                failures.append(_failure("ARGUMENT_BLUEPRINT_ANSWER_MARKER_MISMATCH", task_id))
            _visible(marker, task_text, "ARGUMENT_BLUEPRINT_ANSWER_NOT_VISIBLE", task_id, failures)
            declared_results = _identifier_set(result_answer.get("result_card_ids"), f"{task_id}.result_answer.result_card_ids")
            declared_sources = _identifier_set(result_answer.get("source_bindings"), f"{task_id}.result_answer.source_bindings")
            if declared_results != answer_result_ids.get(task_id, set()) or declared_sources != _identifier_set(answer.get("source_bindings", []), f"answer.{task_id}.source_bindings"):
                failures.append(_failure("ARGUMENT_BLUEPRINT_RESULT_BINDING_MISMATCH", task_id))
            if not declared_results.issubset(result_ids):
                failures.append(_failure("ARGUMENT_BLUEPRINT_RESULT_CARD_UNKNOWN", task_id))

        conflict = raw.get("task_conflict")
        if mode != "SIMPLE":
            if not isinstance(conflict, dict):
                failures.append(_failure("ARGUMENT_BLUEPRINT_TASK_CONFLICT_REQUIRED", task_id))
            else:
                quote = _text(conflict.get("question_requirement_quote"), f"{task_id}.task_conflict.question_requirement_quote")
                if answer is None or quote != str(answer.get("question_requirement") or "").strip():
                    failures.append(_failure("ARGUMENT_BLUEPRINT_TASK_CONFLICT_REQUIREMENT_MISMATCH", task_id))
                marker = _text(conflict.get("public_marker"), f"{task_id}.task_conflict.public_marker")
                _visible(marker, task_text, "ARGUMENT_BLUEPRINT_TASK_CONFLICT_NOT_VISIBLE", task_id, failures)

        upstream = raw.get("upstream_dependency")
        if not isinstance(upstream, dict) or str(upstream.get("status") or "").strip().upper() not in {"NONE", "DECLARED"}:
            failures.append(_failure("ARGUMENT_BLUEPRINT_UPSTREAM_STATUS_REQUIRED", task_id))
        else:
            status = str(upstream.get("status")).strip().upper()
            records = _items(upstream.get("records"), f"{task_id}.upstream_dependency.records", default=[])
            if status == "NONE" and records:
                failures.append(_failure("ARGUMENT_BLUEPRINT_UPSTREAM_NONE_WITH_RECORDS", task_id))
            if status == "DECLARED" and not records:
                failures.append(_failure("ARGUMENT_BLUEPRINT_UPSTREAM_RECORD_REQUIRED", task_id))
            for record_index, record in enumerate(records, 1):
                if not isinstance(record, dict):
                    raise ArgumentBlueprintContractError(f"ARGUMENT_BLUEPRINT_UPSTREAM_RECORD_INVALID: {task_id}.{record_index}")
                source_task = _text(record.get("from_task_id"), f"{task_id}.upstream[{record_index}].from_task_id")
                if source_task not in task_order or task_order.index(source_task) >= task_order.index(task_id):
                    failures.append(_failure("ARGUMENT_BLUEPRINT_UPSTREAM_TASK_INVALID", f"{task_id}.{source_task}"))
                linked_results = _identifier_set(record.get("result_card_ids"), f"{task_id}.upstream[{record_index}].result_card_ids")
                if not linked_results or not linked_results.issubset(answer_result_ids.get(source_task, set())):
                    failures.append(_failure("ARGUMENT_BLUEPRINT_UPSTREAM_RESULT_INVALID", f"{task_id}.{source_task}"))
                marker = _text(record.get("public_marker"), f"{task_id}.upstream[{record_index}].public_marker")
                _visible(marker, task_text, "ARGUMENT_BLUEPRINT_UPSTREAM_NOT_VISIBLE", f"{task_id}.{source_task}", failures)

        failure_modes = _items(raw.get("failure_modes"), f"{task_id}.failure_modes", default=[])
        if mode != "SIMPLE" and not failure_modes:
            failures.append(_failure("ARGUMENT_BLUEPRINT_FAILURE_MODE_REQUIRED", task_id))
        if mode == "SIMPLE" and not failure_modes and not str(raw.get("failure_mode_not_applicable_reason") or "").strip():
            failures.append(_failure("ARGUMENT_BLUEPRINT_FAILURE_MODE_REASON_REQUIRED", task_id))
        for failure_index, failure_mode in enumerate(failure_modes, 1):
            if not isinstance(failure_mode, dict):
                raise ArgumentBlueprintContractError(f"ARGUMENT_BLUEPRINT_FAILURE_MODE_INVALID: {task_id}.{failure_index}")
            failure_id = _text(failure_mode.get("failure_id"), f"{task_id}.failure_modes[{failure_index}].failure_id")
            assumption_refs = _identifier_set(failure_mode.get("assumption_ids"), f"{task_id}.{failure_id}.assumption_ids")
            equation_refs = _identifier_set(failure_mode.get("equation_labels"), f"{task_id}.{failure_id}.equation_labels")
            relation_refs = _identifier_set(failure_mode.get("relation_ids"), f"{task_id}.{failure_id}.relation_ids")
            if not (assumption_refs or equation_refs or relation_refs):
                failures.append(_failure("ARGUMENT_BLUEPRINT_FAILURE_MODE_BINDING_REQUIRED", f"{task_id}.{failure_id}"))
            if not assumption_refs.issubset(relevant_assumptions) or not equation_refs.issubset(equation_labels) or not relation_refs.issubset(relation_ids):
                failures.append(_failure("ARGUMENT_BLUEPRINT_FAILURE_MODE_BINDING_INVALID", f"{task_id}.{failure_id}"))
            marker = _text(failure_mode.get("public_marker"), f"{task_id}.{failure_id}.public_marker")
            _visible(marker, task_text, "ARGUMENT_BLUEPRINT_FAILURE_MODE_NOT_VISIBLE", f"{task_id}.{failure_id}", failures)

        reasoning = raw.get("reasoning_path")
        if not isinstance(reasoning, dict) or str(reasoning.get("mode") or "").strip().upper() not in {"DECLARED", "MIXED", "CUSTOM"}:
            failures.append(_failure("ARGUMENT_BLUEPRINT_REASONING_PATH_REQUIRED", task_id))
        else:
            steps = _items(reasoning.get("steps"), f"{task_id}.reasoning_path.steps", default=[])
            if not steps:
                failures.append(_failure("ARGUMENT_BLUEPRINT_REASONING_STEPS_REQUIRED", task_id))
            positions: list[int] = []
            for step_index, step in enumerate(steps, 1):
                if not isinstance(step, dict):
                    raise ArgumentBlueprintContractError(f"ARGUMENT_BLUEPRINT_REASONING_STEP_INVALID: {task_id}.{step_index}")
                _text(step.get("step_id"), f"{task_id}.reasoning_path.steps[{step_index}].step_id")
                marker = _text(step.get("public_marker"), f"{task_id}.reasoning_path.steps[{step_index}].public_marker")
                if marker not in task_text:
                    failures.append(_failure("ARGUMENT_BLUEPRINT_REASONING_STEP_NOT_VISIBLE", f"{task_id}.{step_index}"))
                else:
                    positions.append(task_text.index(marker))
                links = step.get("links")
                if not isinstance(links, dict):
                    failures.append(_failure("ARGUMENT_BLUEPRINT_REASONING_STEP_LINKS_REQUIRED", f"{task_id}.{step_index}"))
                    continue
                linked_objects = _identifier_set(links.get("object_ids"), f"{task_id}.step[{step_index}].object_ids")
                linked_relations = _identifier_set(links.get("relation_ids"), f"{task_id}.step[{step_index}].relation_ids")
                linked_equations = _identifier_set(links.get("equation_labels"), f"{task_id}.step[{step_index}].equation_labels")
                linked_results = _identifier_set(links.get("result_card_ids"), f"{task_id}.step[{step_index}].result_card_ids")
                linked_models = _identifier_set(links.get("model_ids"), f"{task_id}.step[{step_index}].model_ids")
                if not any((linked_objects, linked_relations, linked_equations, linked_results, linked_models)):
                    failures.append(_failure("ARGUMENT_BLUEPRINT_REASONING_STEP_UNBOUND", f"{task_id}.{step_index}"))
                if (
                    not linked_objects.issubset(object_ids)
                    or not linked_relations.issubset(relation_ids)
                    or not linked_equations.issubset(equation_labels)
                    or not linked_results.issubset(result_ids)
                    or not linked_models.issubset(model_ids)
                ):
                    failures.append(_failure("ARGUMENT_BLUEPRINT_REASONING_STEP_BINDING_INVALID", f"{task_id}.{step_index}"))
            if positions != sorted(positions):
                failures.append(_failure("ARGUMENT_BLUEPRINT_REASONING_ORDER_MISMATCH", task_id))

        task_reports.append(
            {
                "task_id": task_id,
                "complexity_mode": mode,
                "object_ids": sorted(object_ids),
                "model_ids": sorted(model_ids),
                "equation_labels": sorted(equation_labels),
                "relation_ids": sorted(relation_ids),
                "assumption_ids": sorted(relevant_assumptions),
                "answer_result_ids": sorted(answer_result_ids.get(task_id, set())),
            }
        )

    symbol_review = spec.get("symbol_consistency_review")
    declared_symbols = set()
    if not isinstance(symbol_review, dict) or symbol_review.get("status") != "DECLARED_CONSISTENT":
        failures.append(_failure("ARGUMENT_BLUEPRINT_SYMBOL_REVIEW_REQUIRED", "symbol_consistency_review"))
    else:
        declared_symbols = _identifier_set(symbol_review.get("symbols"), "symbol_consistency_review.symbols")
        if declared_symbols != set(global_symbols):
            failures.append(_failure("ARGUMENT_BLUEPRINT_SYMBOL_COVERAGE_INCOMPLETE", f"expected={sorted(global_symbols)}; actual={sorted(declared_symbols)}"))
    for symbol, definitions in global_symbols.items():
        if len(definitions) > 1:
            failures.append(_failure("ARGUMENT_BLUEPRINT_SYMBOL_DEFINITION_CONFLICT", f"{symbol}: {sorted(definitions)}"))

    abstract = _load_text(content_root, content, "abstract")
    abstract_bindings = _items(spec.get("abstract_task_bindings"), "abstract_task_bindings", default=[])
    abstract_map: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(abstract_bindings, 1):
        if not isinstance(record, dict):
            raise ArgumentBlueprintContractError(f"ARGUMENT_BLUEPRINT_ABSTRACT_BINDING_INVALID: {index}")
        task_id = _text(record.get("task_id"), f"abstract_task_bindings[{index}].task_id")
        abstract_map[task_id] = record
    if set(abstract_map) != set(task_map):
        failures.append(_failure("ARGUMENT_BLUEPRINT_ABSTRACT_TASK_COVERAGE_INCOMPLETE", f"expected={sorted(task_map)}; actual={sorted(abstract_map)}"))
    for task_id, record in abstract_map.items():
        for field in ("problem_marker", "method_marker", "result_marker"):
            marker = _text(record.get(field), f"abstract.{task_id}.{field}")
            _visible(marker, abstract, "ARGUMENT_BLUEPRINT_ABSTRACT_MARKER_NOT_VISIBLE", f"{task_id}.{field}", failures)
        linked_results = _identifier_set(record.get("result_card_ids"), f"abstract.{task_id}.result_card_ids")
        if linked_results != answer_result_ids.get(task_id, set()):
            failures.append(_failure("ARGUMENT_BLUEPRINT_ABSTRACT_RESULT_BINDING_MISMATCH", task_id))
    synthesis_marker = _text(spec.get("abstract_synthesis_marker"), "abstract_synthesis_marker")
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", abstract) if part.strip()]
    if not paragraphs or synthesis_marker not in paragraphs[-1]:
        failures.append(_failure("ARGUMENT_BLUEPRINT_ABSTRACT_SYNTHESIS_NOT_VISIBLE", synthesis_marker))

    return {
        "status": "FAIL" if failures else "PASS",
        "contract_version": CONTRACT_VERSION,
        "hard_failures": failures,
        "warnings": [],
        "task_count": len(task_map),
        "task_ids": task_order,
        "task_reports": task_reports,
        "repair_policy": {
            "max_automatic_retries": retries,
            "on_exhaustion": "HUMAN_REVIEW_REQUIRED",
            "surface_rewrite_forbidden": True,
        },
        "semantic_quality_proof_claimed": False,
        "automatic_naturalness_judgment_claimed": False,
    }


def validate_argument_blueprint_trace_report(payload: dict[str, Any], paper_root: str | Path) -> dict[str, Any]:
    """Revalidate a renderer trace against the immutable manifest and paper."""

    failures: list[str] = []
    root = Path(paper_root).resolve()
    if not isinstance(payload, dict):
        return {"status": "FAIL", "reason": "ARGUMENT_BLUEPRINT_TRACE_INVALID", "failures": ["ARGUMENT_BLUEPRINT_TRACE_INVALID"]}
    if payload.get("schema_version") != TRACE_VERSION:
        failures.append("ARGUMENT_BLUEPRINT_TRACE_VERSION_INVALID")
    if payload.get("generated_by") != "paper_renderer":
        failures.append("ARGUMENT_BLUEPRINT_TRACE_PRODUCER_INVALID")
    if payload.get("status") != "PASS":
        failures.append("ARGUMENT_BLUEPRINT_TRACE_NOT_PASSED")
    if payload.get("policy_version") != REQUIRED_POLICY_VERSION:
        failures.append("ARGUMENT_BLUEPRINT_TRACE_POLICY_INVALID")
    if payload.get("manifest_mode") != "CURRENT_FORMAL":
        failures.append("ARGUMENT_BLUEPRINT_TRACE_MANIFEST_MODE_INVALID")
    blueprint = payload.get("argument_blueprint")
    if not isinstance(blueprint, dict) or blueprint.get("status") != "PASS" or blueprint.get("contract_version") != CONTRACT_VERSION:
        failures.append("ARGUMENT_BLUEPRINT_TRACE_CONTRACT_NOT_PASSED")
    main = root / "main.tex"
    if not main.is_file() or payload.get("main_tex_sha256") != _sha256(main):
        failures.append("ARGUMENT_BLUEPRINT_TRACE_MAIN_TEX_HASH_MISMATCH")
    manifest_binding = payload.get("source_manifest")
    if not isinstance(manifest_binding, dict):
        failures.append("ARGUMENT_BLUEPRINT_TRACE_MANIFEST_BINDING_REQUIRED")
    else:
        relative = str(manifest_binding.get("path") or "")
        source = (root.parent / relative).resolve()
        try:
            source.relative_to(root.parent)
        except ValueError:
            failures.append("ARGUMENT_BLUEPRINT_TRACE_MANIFEST_ESCAPES_CASE")
        else:
            if not source.is_file() or manifest_binding.get("sha256") != _sha256(source):
                failures.append("ARGUMENT_BLUEPRINT_TRACE_MANIFEST_HASH_MISMATCH")
    return {
        "status": "FAIL" if failures else "PASS",
        "reason": failures[0] if failures else "ARGUMENT_BLUEPRINT_TRACE_VALID",
        "failures": failures,
        "task_count": blueprint.get("task_count", 0) if isinstance(blueprint, dict) else 0,
    }
