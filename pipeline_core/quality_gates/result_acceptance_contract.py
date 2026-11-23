"""Small deterministic contracts for results shown in a competition paper.

The checks deliberately validate declared bindings rather than infer physics:
every displayed result names its source, scenario parameters and unit; every
task names a concrete output; every limitation names its downstream impact.
"""

from __future__ import annotations

from typing import Any


class ResultAcceptanceContractError(ValueError):
    pass


DEFAULT_SCENARIO_FIELDS = (
    "result_source",
    "e",
    "duration",
    "scenario_id",
    "player_combination",
    "direction_angle",
    "mirror",
)
ANSWER_TYPES = {"quantitative", "qualitative", "strategy", "classification", "decision"}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResultAcceptanceContractError(f"RESULT_ACCEPTANCE_REQUIRED_TEXT: {field}")
    return value.strip()


def _list(value: Any, field: str, *, default: list[Any] | None = None) -> list[Any]:
    if value is None and default is not None:
        return default
    if not isinstance(value, list):
        raise ResultAcceptanceContractError(f"RESULT_ACCEPTANCE_REQUIRED_LIST: {field}")
    return value


def _failure(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def _report(hard_failures: list[dict[str, str]], **extra: Any) -> dict[str, Any]:
    return {
        "status": "FAIL" if hard_failures else "PASS",
        "hard_failures": hard_failures,
        **extra,
    }


def validate_result_cards(spec: dict[str, Any], *, require_approval: bool = False) -> dict[str, Any]:
    """Validate a case-declared card for every numerical result it presents."""

    cards = _list(spec.get("result_cards", []), "result_cards", default=[])
    required_defaults = _list(
        spec.get("default_scenario_fields", list(DEFAULT_SCENARIO_FIELDS)),
        "default_scenario_fields",
    )
    failures: list[dict[str, str]] = []
    result_ids: set[str] = set()
    for index, raw in enumerate(cards, 1):
        if not isinstance(raw, dict):
            raise ResultAcceptanceContractError(f"RESULT_CARD_INVALID: {index}")
        result_id = _text(raw.get("result_id"), f"result_cards[{index}].result_id")
        if result_id in result_ids:
            failures.append(_failure("RESULT_CARD_DUPLICATED", result_id))
        result_ids.add(result_id)
        for field in ("display_value", "unit", "source_binding", "paper_location"):
            try:
                _text(raw.get(field), f"result_cards[{index}].{field}")
            except ResultAcceptanceContractError:
                failures.append(_failure("RESULT_CARD_FIELD_MISSING", f"{result_id}.{field}"))
        if require_approval and raw.get("approved") is not True:
            failures.append(_failure("RESULT_CARD_NOT_APPROVED", result_id))
        scenario = raw.get("scenario")
        if not isinstance(scenario, dict):
            failures.append(_failure("RESULT_SCENARIO_MISSING", result_id))
            continue
        applicable = _list(raw.get("applicable_scenario_fields", required_defaults), f"result_cards[{index}].applicable_scenario_fields")
        if not applicable:
            failures.append(_failure("RESULT_SCENARIO_FIELDS_EMPTY", result_id))
        for field in applicable:
            name = _text(field, f"result_cards[{index}].scenario_field")
            value = scenario.get(name)
            if value is None or (isinstance(value, str) and not value.strip()):
                failures.append(_failure("RESULT_SCENARIO_FIELD_MISSING", f"{result_id}.{name}"))
        decimals = raw.get("display_precision_decimals")
        verified = raw.get("validated_precision_decimals")
        if decimals is not None or verified is not None:
            if not isinstance(decimals, int) or not isinstance(verified, int) or decimals < 0 or verified < 0:
                failures.append(_failure("RESULT_PRECISION_INVALID", result_id))
            elif decimals > verified:
                failures.append(_failure("RESULT_PRECISION_UNSUPPORTED", result_id))
    return _report(failures, result_card_ids=sorted(result_ids), result_card_count=len(cards))


def validate_answer_cards(
    spec: dict[str, Any],
    result_cards: dict[str, dict[str, Any]] | set[str],
    *,
    task_contract: dict[str, Any] | None = None,
    strict_task_closure: bool = False,
) -> dict[str, Any]:
    """Require a public answer and bind quantitative answers to approved results."""

    result_card_map = result_cards if isinstance(result_cards, dict) else {result_id: {} for result_id in result_cards}
    cards = _list(spec.get("answer_cards", []), "answer_cards", default=[])
    failures: list[dict[str, str]] = []
    task_ids: set[str] = set()
    expected_task_ids: set[str] | None = None
    expected_task_locations: dict[str, str] = {}
    if task_contract is not None:
        tasks = task_contract.get("tasks")
        if not isinstance(tasks, list):
            raise ResultAcceptanceContractError("ANSWER_TASK_CONTRACT_INVALID")
        expected_task_ids = {
            _text(task.get("task_id"), "task_contract.tasks.task_id")
            for task in tasks
            if isinstance(task, dict)
        }
        if len(expected_task_ids) != len(tasks):
            raise ResultAcceptanceContractError("ANSWER_TASK_CONTRACT_INVALID")
        expected_task_locations = {
            _text(task.get("task_id"), "task_contract.tasks.task_id"):
            _text(task.get("content_key"), "task_contract.tasks.content_key")
            for task in tasks
            if isinstance(task, dict)
        }
    for index, raw in enumerate(cards, 1):
        if not isinstance(raw, dict):
            raise ResultAcceptanceContractError(f"ANSWER_CARD_INVALID: {index}")
        task_id = _text(raw.get("task_id"), f"answer_cards[{index}].task_id")
        if task_id in task_ids:
            failures.append(_failure("ANSWER_CARD_DUPLICATED", task_id))
        task_ids.add(task_id)
        required_fields = ["question_requirement", "paper_output", "representative_value"]
        if strict_task_closure:
            required_fields.extend(("paper_location", "answer_marker", "answer_type"))
        for field in required_fields:
            try:
                _text(raw.get(field), f"answer_cards[{index}].{field}")
            except ResultAcceptanceContractError:
                failures.append(_failure("ANSWER_OUTPUT_MISSING", f"{task_id}.{field}"))
        references = _list(raw.get("result_card_ids", []), f"answer_cards[{index}].result_card_ids", default=[])
        source_bindings = _list(raw.get("source_bindings", []), f"answer_cards[{index}].source_bindings", default=[])
        if strict_task_closure:
            if task_id in expected_task_locations and raw.get("paper_location") != expected_task_locations[task_id]:
                failures.append(_failure("ANSWER_TASK_CONTENT_LOCATION_MISMATCH", task_id))
            answer_type = str(raw.get("answer_type") or "")
            if answer_type not in ANSWER_TYPES:
                failures.append(_failure("ANSWER_TYPE_INVALID", task_id))
            for source in source_bindings:
                try:
                    _text(source, f"answer_cards[{index}].source_binding")
                except ResultAcceptanceContractError:
                    failures.append(_failure("ANSWER_SOURCE_BINDING_INVALID", task_id))
            if answer_type == "quantitative" and not references:
                failures.append(_failure("ANSWER_RESULT_CARD_MISSING", task_id))
            if answer_type != "quantitative" and not references and not source_bindings:
                failures.append(_failure("ANSWER_SOURCE_BINDING_MISSING", task_id))
            for result_id in references:
                if result_id not in result_card_map:
                    failures.append(_failure("ANSWER_RESULT_CARD_UNKNOWN", f"{task_id}.{result_id}"))
                elif answer_type == "quantitative" and result_card_map[result_id].get("approved") is not True:
                    failures.append(_failure("ANSWER_RESULT_CARD_NOT_APPROVED", f"{task_id}.{result_id}"))
        else:
            if not references:
                failures.append(_failure("ANSWER_RESULT_CARD_MISSING", task_id))
            for result_id in references:
                if result_id not in result_card_map:
                    failures.append(_failure("ANSWER_RESULT_CARD_UNKNOWN", f"{task_id}.{result_id}"))
    if expected_task_ids is not None:
        for task_id in sorted(expected_task_ids - task_ids):
            failures.append(_failure("ANSWER_CARD_MISSING_FOR_TASK", task_id))
        for task_id in sorted(task_ids - expected_task_ids):
            failures.append(_failure("ANSWER_CARD_UNKNOWN_TASK", task_id))
    return _report(
        failures,
        task_ids=sorted(task_ids),
        expected_task_ids=sorted(expected_task_ids) if expected_task_ids is not None else None,
        strict_task_closure=strict_task_closure,
        answer_card_count=len(cards),
    )


def validate_limitation_impacts(spec: dict[str, Any]) -> dict[str, Any]:
    impacts = _list(spec.get("limitation_impacts", []), "limitation_impacts", default=[])
    failures: list[dict[str, str]] = []
    ids: set[str] = set()
    for index, raw in enumerate(impacts, 1):
        if not isinstance(raw, dict):
            raise ResultAcceptanceContractError(f"LIMITATION_IMPACT_INVALID: {index}")
        limitation_id = _text(raw.get("limitation_id"), f"limitation_impacts[{index}].limitation_id")
        if limitation_id in ids:
            failures.append(_failure("LIMITATION_IMPACT_DUPLICATED", limitation_id))
        ids.add(limitation_id)
        for field in ("assumption_or_boundary", "affected_quantities", "conditional_results"):
            try:
                _text(raw.get(field), f"limitation_impacts[{index}].{field}")
            except ResultAcceptanceContractError:
                failures.append(_failure("LIMITATION_IMPACT_MISSING", f"{limitation_id}.{field}"))
    return _report(failures, limitation_ids=sorted(ids), limitation_impact_count=len(impacts))


def validate_visibility_links(
    spec: dict[str, Any],
    result_card_ids: set[str],
    *,
    expected_task_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Validate declared input, visual and method-to-output links."""

    failures: list[dict[str, str]] = []
    inputs = _list(spec.get("input_conditions", []), "input_conditions", default=[])
    visuals = _list(spec.get("visual_bindings", []), "visual_bindings", default=[])
    methods = _list(spec.get("method_output_links", []), "method_output_links", default=[])
    input_tasks: set[str] = set()
    method_tasks: set[str] = set()
    for index, raw in enumerate(inputs, 1):
        if not isinstance(raw, dict):
            raise ResultAcceptanceContractError(f"INPUT_CONDITION_INVALID: {index}")
        for field in ("condition_id", "paper_location", "marker"):
            try:
                _text(raw.get(field), f"input_conditions[{index}].{field}")
            except ResultAcceptanceContractError:
                failures.append(_failure("INPUT_CONDITION_FIELD_MISSING", f"input[{index}].{field}"))
        if expected_task_ids is not None:
            task_id = str(raw.get("task_id") or "").strip()
            if not task_id:
                failures.append(_failure("INPUT_CONDITION_TASK_ID_MISSING", f"input[{index}]"))
            else:
                input_tasks.add(task_id)
    for index, raw in enumerate(visuals, 1):
        if not isinstance(raw, dict):
            raise ResultAcceptanceContractError(f"VISUAL_BINDING_INVALID: {index}")
        for field in ("visual_id", "kind", "paper_location", "caption_marker"):
            try:
                _text(raw.get(field), f"visual_bindings[{index}].{field}")
            except ResultAcceptanceContractError:
                failures.append(_failure("VISUAL_BINDING_FIELD_MISSING", f"visual[{index}].{field}"))
        if raw.get("kind") not in {"table", "figure"}:
            failures.append(_failure("VISUAL_BINDING_KIND_INVALID", f"visual[{index}]"))
        linked = _list(raw.get("result_card_ids", []), f"visual_bindings[{index}].result_card_ids", default=[])
        if not linked:
            failures.append(_failure("VISUAL_RESULT_CARD_MISSING", f"visual[{index}]"))
        for result_id in linked:
            if result_id not in result_card_ids:
                failures.append(_failure("VISUAL_RESULT_CARD_UNKNOWN", f"visual[{index}].{result_id}"))
    for index, raw in enumerate(methods, 1):
        if not isinstance(raw, dict):
            raise ResultAcceptanceContractError(f"METHOD_OUTPUT_LINK_INVALID: {index}")
        for field in ("method_id", "paper_location", "method_marker", "output_marker"):
            try:
                _text(raw.get(field), f"method_output_links[{index}].{field}")
            except ResultAcceptanceContractError:
                failures.append(_failure("METHOD_OUTPUT_FIELD_MISSING", f"method[{index}].{field}"))
        if expected_task_ids is not None:
            task_id = str(raw.get("task_id") or "").strip()
            if not task_id:
                failures.append(_failure("METHOD_OUTPUT_TASK_ID_MISSING", f"method[{index}]"))
            else:
                method_tasks.add(task_id)
        linked = _list(raw.get("result_card_ids", []), f"method_output_links[{index}].result_card_ids", default=[])
        if not linked:
            failures.append(_failure("METHOD_RESULT_CARD_MISSING", f"method[{index}]"))
        for result_id in linked:
            if result_id not in result_card_ids:
                failures.append(_failure("METHOD_RESULT_CARD_UNKNOWN", f"method[{index}].{result_id}"))
    if expected_task_ids is not None:
        for task_id in sorted(expected_task_ids - input_tasks):
            failures.append(_failure("INPUT_CONDITION_MISSING_FOR_TASK", task_id))
        for task_id in sorted(expected_task_ids - method_tasks):
            failures.append(_failure("METHOD_OUTPUT_LINK_MISSING_FOR_TASK", task_id))
    return _report(
        failures,
        input_condition_count=len(inputs),
        visual_binding_count=len(visuals),
        method_output_link_count=len(methods),
        input_task_ids=sorted(input_tasks),
        method_task_ids=sorted(method_tasks),
    )


def validate_result_acceptance_contract(
    spec: dict[str, Any], *, task_contract: dict[str, Any] | None = None, strict_visibility: bool = False
) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise ResultAcceptanceContractError("RESULT_ACCEPTANCE_CONTRACT_INVALID")
    strict_task_closure = task_contract is not None
    results = validate_result_cards(spec, require_approval=strict_task_closure)
    result_cards = {
        str(raw["result_id"]): raw
        for raw in spec.get("result_cards", [])
        if isinstance(raw, dict) and raw.get("result_id")
    }
    answers = validate_answer_cards(
        spec,
        result_cards,
        task_contract=task_contract,
        strict_task_closure=strict_task_closure,
    )
    limitations = validate_limitation_impacts(spec)
    expected_task_ids = None
    if strict_task_closure and strict_visibility:
        expected_task_ids = {str(task["task_id"]) for task in task_contract["tasks"] if isinstance(task, dict)}
    visibility = validate_visibility_links(
        spec, set(results["result_card_ids"]), expected_task_ids=expected_task_ids
    )
    failures = [*results["hard_failures"], *answers["hard_failures"], *limitations["hard_failures"], *visibility["hard_failures"]]
    return _report(
        failures,
        result_cards=results,
        answer_cards=answers,
        limitation_impacts=limitations,
        visibility_links=visibility,
        contract_version=str(spec.get("version") or "result_acceptance_contract_v1"),
    )
