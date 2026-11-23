"""Deterministic evidence contracts for concise competition-paper writing.

The contract records what the writer is allowed to state.  It does not infer a
model, repair a derivation, or decide whether a physical assumption is true.
Instead it requires the authoring record to connect each model, assumption,
optimisation comparison and conclusion to declared evidence.
"""

from __future__ import annotations

from typing import Any


class PaperEvidenceContractError(ValueError):
    pass


_FORBIDDEN_UNQUALIFIED_CLAIMS = ("全局最优", "唯一方案", "必然可行", "工程保证", "无条件保证")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PaperEvidenceContractError(f"PAPER_EVIDENCE_REQUIRED_TEXT: {field}")
    return value.strip()


def _items(value: Any, field: str, *, default: list[Any] | None = None) -> list[Any]:
    if value is None and default is not None:
        return default
    if not isinstance(value, list):
        raise PaperEvidenceContractError(f"PAPER_EVIDENCE_REQUIRED_LIST: {field}")
    return value


def _failure(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def _report(failures: list[dict[str, str]], warnings: list[dict[str, str]] | None = None, **extra: Any) -> dict[str, Any]:
    warnings = warnings or []
    return {
        "status": "FAIL" if failures else ("WARNING" if warnings else "PASS"),
        "hard_failures": failures,
        "warnings": warnings,
        **extra,
    }


def _known_ids(entries: list[Any], key: str, label: str) -> set[str]:
    values: set[str] = set()
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            raise PaperEvidenceContractError(f"{label}_INVALID: {index}")
        value = _text(entry.get(key), f"{label}[{index}].{key}")
        if value in values:
            raise PaperEvidenceContractError(f"{label}_DUPLICATED: {value}")
        values.add(value)
    return values


def _references(values: list[Any], known: set[str], code: str, owner: str, failures: list[dict[str, str]]) -> None:
    if not values:
        failures.append(_failure(code, f"{owner}: reference list is empty"))
    for value in values:
        identifier = _text(value, f"{owner}.reference")
        if identifier not in known:
            failures.append(_failure(code, f"{owner}: {identifier}"))


def validate_paper_evidence_contract(spec: dict[str, Any], *, result_card_ids: set[str] | None = None) -> dict[str, Any]:
    """Validate explicit logical-closure records without claiming semantic proof."""

    if not isinstance(spec, dict):
        raise PaperEvidenceContractError("PAPER_EVIDENCE_CONTRACT_INVALID")
    result_card_ids = result_card_ids or set()
    models = _items(spec.get("model_blocks"), "model_blocks", default=[])
    assumptions = _items(spec.get("assumption_ledger"), "assumption_ledger", default=[])
    optimisations = _items(spec.get("optimization_comparisons"), "optimization_comparisons", default=[])
    claims = _items(spec.get("claim_evidence_cards"), "claim_evidence_cards", default=[])
    failures: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    model_ids = _known_ids(models, "model_id", "MODEL_BLOCK")
    assumption_ids = _known_ids(assumptions, "assumption_id", "ASSUMPTION")
    claim_ids = _known_ids(claims, "claim_id", "CLAIM")
    checked_tasks: list[str] = []

    for index, model in enumerate(models, 1):
        if not isinstance(model, dict):
            continue
        owner = f"model_blocks[{index}]"
        task_id = _text(model.get("task_id"), f"{owner}.task_id")
        checked_tasks.append(task_id)
        for field in ("paper_location", "model_goal", "method_selection_basis", "physical_meaning", "boundary_marker"):
            try:
                _text(model.get(field), f"{owner}.{field}")
            except PaperEvidenceContractError:
                failures.append(_failure("MODEL_LOGICAL_CLOSURE_FIELD_MISSING", f"{model.get('model_id')}.{field}"))
        variables = _items(model.get("variables"), f"{owner}.variables", default=[])
        steps = _items(model.get("derivation_steps"), f"{owner}.derivation_steps", default=[])
        if not variables:
            failures.append(_failure("MODEL_VARIABLE_DEFINITION_MISSING", str(model.get("model_id"))))
        if not steps:
            failures.append(_failure("MODEL_DERIVATION_MISSING", str(model.get("model_id"))))
        for variable_index, variable in enumerate(variables, 1):
            if not isinstance(variable, dict):
                failures.append(_failure("MODEL_VARIABLE_DEFINITION_MISSING", f"{model.get('model_id')}[{variable_index}]"))
                continue
            for field in ("symbol", "definition"):
                try:
                    _text(variable.get(field), f"{owner}.variables[{variable_index}].{field}")
                except PaperEvidenceContractError:
                    failures.append(_failure("MODEL_VARIABLE_DEFINITION_MISSING", f"{model.get('model_id')}.{field}"))
        for step_index, step in enumerate(steps, 1):
            if not isinstance(step, dict):
                failures.append(_failure("MODEL_DERIVATION_MISSING", f"{model.get('model_id')}[{step_index}]"))
                continue
            for field in ("formula_tex", "explanation"):
                try:
                    _text(step.get(field), f"{owner}.derivation_steps[{step_index}].{field}")
                except PaperEvidenceContractError:
                    failures.append(_failure("MODEL_DERIVATION_MISSING", f"{model.get('model_id')}.{field}"))
        refs = _items(model.get("result_card_ids"), f"{owner}.result_card_ids", default=[])
        _references(refs, result_card_ids, "MODEL_RESULT_REFERENCE_INVALID", str(model.get("model_id")), failures)
        limitation_refs = _items(model.get("assumption_ids"), f"{owner}.assumption_ids", default=[])
        _references(limitation_refs, assumption_ids, "MODEL_ASSUMPTION_REFERENCE_INVALID", str(model.get("model_id")), failures)

    for index, assumption in enumerate(assumptions, 1):
        if not isinstance(assumption, dict):
            continue
        owner = f"assumption_ledger[{index}]"
        for field in ("statement", "basis", "simplification", "valid_domain", "limitation_id"):
            try:
                _text(assumption.get(field), f"{owner}.{field}")
            except PaperEvidenceContractError:
                failures.append(_failure("ASSUMPTION_BASIS_OR_BOUNDARY_MISSING", f"{assumption.get('assumption_id')}.{field}"))
        refs = _items(assumption.get("affected_result_card_ids"), f"{owner}.affected_result_card_ids", default=[])
        _references(refs, result_card_ids, "ASSUMPTION_RESULT_IMPACT_INVALID", str(assumption.get("assumption_id")), failures)

    required_optimization_tasks = {str(item) for item in _items(spec.get("optimization_task_ids"), "optimization_task_ids", default=[])}
    comparison_tasks: set[str] = set()
    for index, comparison in enumerate(optimisations, 1):
        if not isinstance(comparison, dict):
            raise PaperEvidenceContractError(f"OPTIMIZATION_COMPARISON_INVALID: {index}")
        owner = f"optimization_comparisons[{index}]"
        task_id = _text(comparison.get("task_id"), f"{owner}.task_id")
        comparison_tasks.add(task_id)
        for field in ("paper_location", "metric", "unit", "direction", "baseline_value", "optimized_value", "comparison_marker"):
            try:
                _text(comparison.get(field), f"{owner}.{field}")
            except PaperEvidenceContractError:
                failures.append(_failure("OPTIMIZATION_COMPARISON_FIELD_MISSING", f"{task_id}.{field}"))
        if comparison.get("direction") not in {"minimize", "maximize", "target"}:
            failures.append(_failure("OPTIMIZATION_DIRECTION_INVALID", task_id))
        baseline_id = comparison.get("baseline_result_card_id")
        optimized_id = comparison.get("optimized_result_card_id")
        _references([baseline_id] if baseline_id else [], result_card_ids, "OPTIMIZATION_RESULT_REFERENCE_INVALID", f"{task_id}.baseline", failures)
        _references([optimized_id] if optimized_id else [], result_card_ids, "OPTIMIZATION_RESULT_REFERENCE_INVALID", f"{task_id}.optimized", failures)
        baseline_scope = comparison.get("baseline_scenario")
        optimized_scope = comparison.get("optimized_scenario")
        if not isinstance(baseline_scope, dict) or baseline_scope != optimized_scope:
            failures.append(_failure("OPTIMIZATION_SCENARIO_NOT_COMPARABLE", task_id))
    for task_id in sorted(required_optimization_tasks - comparison_tasks):
        failures.append(_failure("OPTIMIZATION_COMPARISON_REQUIRED", task_id))

    for index, claim in enumerate(claims, 1):
        if not isinstance(claim, dict):
            continue
        owner = f"claim_evidence_cards[{index}]"
        claim_text = _text(claim.get("claim"), f"{owner}.claim")
        for forbidden in _FORBIDDEN_UNQUALIFIED_CLAIMS:
            if forbidden in claim_text:
                failures.append(_failure("UNQUALIFIED_CLAIM_FORBIDDEN", f"{claim.get('claim_id')}: {forbidden}"))
        for field in ("paper_location", "claim_marker"):
            try:
                _text(claim.get(field), f"{owner}.{field}")
            except PaperEvidenceContractError:
                failures.append(_failure("CLAIM_EVIDENCE_FIELD_MISSING", f"{claim.get('claim_id')}.{field}"))
        _references(_items(claim.get("result_card_ids"), f"{owner}.result_card_ids", default=[]), result_card_ids, "CLAIM_RESULT_REFERENCE_INVALID", str(claim.get("claim_id")), failures)
        _references(_items(claim.get("model_ids"), f"{owner}.model_ids", default=[]), model_ids, "CLAIM_MODEL_REFERENCE_INVALID", str(claim.get("claim_id")), failures)
        if not _items(claim.get("assumption_ids"), f"{owner}.assumption_ids", default=[]):
            warnings.append(_failure("CLAIM_LIMITATION_NOT_DECLARED", str(claim.get("claim_id"))))

    return _report(
        failures,
        warnings,
        contract_version=str(spec.get("version") or "paper_evidence_contract_v1"),
        model_ids=sorted(model_ids),
        assumption_ids=sorted(assumption_ids),
        claim_ids=sorted(claim_ids),
        tasks_checked=sorted(set(checked_tasks)),
        semantic_proof_claimed=False,
    )
