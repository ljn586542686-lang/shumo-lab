"""Optional, case-agnostic writing-policy checks.

This module distinguishes publication blockers from writing suggestions.  It
never rewrites a model, result, claim, or formula.  A legacy manifest without
``writing_policy`` remains compatible; an explicit policy records warnings in
the Track C receipt and renderer record, while only source/claim/public-text
contract failures can block a formal build.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pipeline_core.quality_gates.result_acceptance_contract import (
    ResultAcceptanceContractError,
    validate_result_acceptance_contract,
)
from pipeline_core.paper_quality.literature_usage_contract import (
    LiteratureUsageContractError,
    validate_literature_usage_contract,
)
from pipeline_core.paper_quality.matlab_figure_pipeline import (
    MatlabFigureSpecError,
    validate_figure_spec,
)
from pipeline_core.paper_quality.paper_evidence_contract import (
    PaperEvidenceContractError,
    validate_paper_evidence_contract,
)
from pipeline_core.paper_quality.argument_blueprint_contract import (
    ArgumentBlueprintContractError,
    validate_argument_blueprint_contract,
)
from pipeline_core.paper_quality.writer_schema import (
    CURRENT_FORMAL_MODE,
    LEGACY_COMPATIBILITY_MODE,
    WriterSchemaError,
    resolve_manifest_mode,
    resolve_task_contract,
)
from pipeline_core.paper_quality.visual_reasoning_contract import (
    VisualReasoningContractError,
    validate_visual_reasoning_contract,
)


WRITING_POLICY_VERSION = "controlled_writing_layer_v5"
SUPPORTED_POLICY_VERSIONS = {
    "controlled_writing_layer_v1",
    "controlled_writing_layer_v2",
    "controlled_writing_layer_v3",
    "controlled_writing_layer_v4",
    WRITING_POLICY_VERSION,
}
FORMAL_POLICY_VERSIONS = {"controlled_writing_layer_v4", WRITING_POLICY_VERSION}
APPENDIX_CODE_MODE = "PENDING_USER_POLICY"
FINAL_APPENDIX_CODE_MODES = {"NOT_REQUIRED", "SOURCE_LIST", "FULL_CODE"}
DEFAULT_PROTECTED_MATH_SYMBOLS = [
    "m", "M", "g", "e", "R", "J", "theta", "alpha", "Delta t",
    "i", "j", "x", "y", "z", "W", "B", "T", "+", "-",
]
_PUBLIC_Q_RE = re.compile(r"\bQ[1-9]\b", re.IGNORECASE)
_INTERNAL_STATE_RE = re.compile(
    r"(?i)\b(?:solver|gate|hash|json|candidate|canonical\s+result|final_submit|"
    r"claim_boundary|result_card|evidence_missing|pass_with_limitations|"
    r"allowed_claim_strength|gate\s+authority|receipt\s+hash|canonical\s+binding|"
    r"fail[-_ ]closed|internal\s+review\s+packet)\b"
)


class WritingPolicyError(ValueError):
    """Raised for malformed policy declarations or hard policy failures."""


def _warning(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def _hard_failure(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def _report(*, warnings: list[dict[str, str]] | None = None, hard_failures: list[dict[str, str]] | None = None, **extra: Any) -> dict[str, Any]:
    warnings = warnings or []
    hard_failures = hard_failures or []
    return {
        "status": "FAIL" if hard_failures else ("WARNING" if warnings else "PASS"),
        "warnings": warnings,
        "hard_failures": hard_failures,
        **extra,
    }


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WritingPolicyError(f"WRITING_POLICY_REQUIRED_TEXT: {label}")
    return value.strip()


def _list(value: Any, label: str, *, default: list[Any] | None = None) -> list[Any]:
    if value is None and default is not None:
        return default
    if not isinstance(value, list):
        raise WritingPolicyError(f"WRITING_POLICY_REQUIRED_LIST: {label}")
    return value


def _load_texts(content_root: Path, content: dict[str, str]) -> dict[str, str]:
    texts: dict[str, str] = {}
    for key, relative in content.items():
        path = (content_root / relative).resolve()
        if not path.is_file():
            raise WritingPolicyError(f"WRITING_POLICY_CONTENT_MISSING: {key}")
        if path.suffix.lower() != ".json":
            texts[key] = path.read_text(encoding="utf-8")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WritingPolicyError(f"WRITING_POLICY_JSON_INVALID: {key}") from exc
        if key.startswith("question_content_"):
            parts = [str(payload.get("intro", "")), str(payload.get("closing_optional", ""))]
            for section in payload.get("sections", []):
                if isinstance(section, dict):
                    parts.extend([str(section.get("title", "")), str(section.get("content", ""))])
            for block in payload.get("derivation_blocks", []):
                if not isinstance(block, dict):
                    continue
                parts.extend(
                    [
                        str(block.get("title", "")),
                        str(block.get("method_selection_basis", "")),
                        str(block.get("physical_explanation", "")),
                        str(block.get("assumptions_and_boundary", "")),
                    ]
                )
                for variable in block.get("variables", []):
                    if isinstance(variable, dict):
                        parts.extend([str(variable.get("symbol", "")), str(variable.get("definition", ""))])
                for step in block.get("steps", []):
                    if isinstance(step, dict):
                        parts.extend([str(step.get("formula_tex", "")), str(step.get("explanation", ""))])
            texts[key] = "\n\n".join(part for part in parts if part.strip())
        elif key == "model_evaluation":
            texts[key] = "\n".join(
                str(payload.get(field, "")) for field in ("strengths", "weaknesses", "improvements")
            )
        else:
            texts[key] = json.dumps(payload, ensure_ascii=False)
    return texts


def _missing_markers(text: str, markers: list[Any], label: str) -> list[str]:
    missing: list[str] = []
    for marker in markers:
        marker_text = _nonempty(marker, f"{label}.marker")
        if marker_text not in text:
            missing.append(marker_text)
    return missing


def validate_abstract_structure(
    text: str,
    spec: dict[str, Any],
    *,
    task_contract: dict[str, Any] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    minimum = int(spec.get("minimum_paragraphs", 0))
    if minimum < 0:
        raise WritingPolicyError("ABSTRACT_MINIMUM_PARAGRAPHS_INVALID")
    warnings: list[dict[str, str]] = []
    hard_failures: list[dict[str, str]] = []
    expected_task_ids = [str(task["task_id"]) for task in (task_contract or {}).get("tasks", [])]
    structural_minimum = len(expected_task_ids) + 2 if strict else 0
    if strict and minimum < structural_minimum:
        hard_failures.append(
            _hard_failure(
                "ABSTRACT_TOTAL_PART_TOTAL_CONTRACT_INCOMPLETE",
                f"minimum_paragraphs must be at least {structural_minimum}",
            )
        )
    if minimum and len(paragraphs) < minimum:
        issue = _warning("ABSTRACT_PARAGRAPH_COUNT_LOW", f"expected at least {minimum}, found {len(paragraphs)}")
        (hard_failures if strict else warnings).append(issue)
    concepts = spec.get("required_concepts", {})
    if not isinstance(concepts, dict):
        raise WritingPolicyError("ABSTRACT_CONCEPT_SPEC_INVALID")
    for concept, markers in concepts.items():
        missing = _missing_markers(text, _list(markers, f"abstract.required_concepts.{concept}"), f"abstract.{concept}")
        if missing:
            issue = _warning("ABSTRACT_CONCEPT_NOT_EVIDENT", f"{concept}: {', '.join(missing)}")
            (hard_failures if strict else warnings).append(issue)

    structure = spec.get("total_part_total_structure")
    if strict and not isinstance(structure, dict):
        hard_failures.append(_hard_failure("ABSTRACT_TOTAL_PART_TOTAL_STRUCTURE_REQUIRED", "missing"))
    elif isinstance(structure, dict):
        opening_markers = _list(structure.get("opening_markers", []), "abstract.total_part_total_structure.opening_markers", default=[])
        closing_markers = _list(structure.get("closing_markers", []), "abstract.total_part_total_structure.closing_markers", default=[])
        task_paragraphs = _list(structure.get("task_paragraphs", []), "abstract.total_part_total_structure.task_paragraphs", default=[])
        if strict and (not opening_markers or not closing_markers):
            hard_failures.append(_hard_failure("ABSTRACT_OPENING_CLOSING_MARKERS_REQUIRED", "opening or closing markers missing"))
        if paragraphs:
            missing_opening = _missing_markers(paragraphs[0], opening_markers, "abstract.opening")
            missing_closing = _missing_markers(paragraphs[-1], closing_markers, "abstract.closing")
            if missing_opening:
                hard_failures.append(_hard_failure("ABSTRACT_OPENING_NOT_EVIDENT", ", ".join(missing_opening)))
            if missing_closing:
                hard_failures.append(_hard_failure("ABSTRACT_CLOSING_NOT_EVIDENT", ", ".join(missing_closing)))
        records: dict[str, dict[str, Any]] = {}
        for index, record in enumerate(task_paragraphs, 1):
            if not isinstance(record, dict):
                raise WritingPolicyError(f"ABSTRACT_TASK_PARAGRAPH_INVALID: {index}")
            task_id = _nonempty(record.get("task_id"), f"abstract.task_paragraphs[{index}].task_id")
            records[task_id] = record
        if strict:
            for task_id in sorted(set(expected_task_ids) - set(records)):
                hard_failures.append(_hard_failure("ABSTRACT_TASK_PARAGRAPH_MISSING", task_id))
            for task_id in sorted(set(records) - set(expected_task_ids)):
                hard_failures.append(_hard_failure("ABSTRACT_TASK_PARAGRAPH_UNKNOWN", task_id))
        used_indices: set[int] = set()
        last_index = 0
        for task_id in expected_task_ids:
            record = records.get(task_id)
            if record is None:
                continue
            markers = _list(record.get("markers", []), f"abstract.task_paragraphs.{task_id}.markers", default=[])
            matching = [
                index for index, paragraph in enumerate(paragraphs[1:-1], 1)
                if markers and not _missing_markers(paragraph, markers, f"abstract.task_paragraphs.{task_id}")
            ]
            if not matching:
                hard_failures.append(_hard_failure("ABSTRACT_TASK_PARAGRAPH_NOT_EVIDENT", task_id))
                continue
            paragraph_index = matching[0]
            if paragraph_index in used_indices or paragraph_index <= last_index:
                hard_failures.append(_hard_failure("ABSTRACT_TASK_PARAGRAPH_ORDER_INVALID", task_id))
            used_indices.add(paragraph_index)
            last_index = paragraph_index
    return _report(
        warnings=warnings,
        hard_failures=hard_failures,
        paragraph_count=len(paragraphs),
        minimum_paragraphs=minimum,
        keyword_count_requirement=int(spec.get("minimum_keywords", 0)),
    )


def validate_problem_restatement(
    text: str,
    spec: dict[str, Any],
    tasks: list[dict[str, str]] | None = None,
    *,
    strict: bool = False,
) -> dict[str, Any]:
    warnings: list[dict[str, str]] = []
    hard_failures: list[dict[str, str]] = []
    background_markers = _list(spec.get("background_markers", []), "problem_restatement.background_markers", default=[])
    missing = _missing_markers(text, background_markers, "problem_restatement.background")
    if missing:
        issue = _warning("PROBLEM_RESTATEMENT_BACKGROUND_NOT_EVIDENT", ", ".join(missing))
        (hard_failures if strict else warnings).append(issue)
    raw_questions = spec.get("questions")
    if raw_questions is None and tasks is not None:
        raw_questions = [{"label": task["label"], "markers": []} for task in tasks]
    questions = _list(raw_questions, "problem_restatement.questions", default=[])
    labels: list[str] = []
    for index, question in enumerate(questions, 1):
        if not isinstance(question, dict):
            raise WritingPolicyError(f"PROBLEM_RESTATEMENT_QUESTION_INVALID: {index}")
        label = _nonempty(question.get("label"), f"problem_restatement.questions[{index}].label")
        labels.append(label)
        if label not in text:
            issue = _warning("PROBLEM_RESTATEMENT_TASK_LABEL_NOT_EVIDENT", label)
            (hard_failures if strict else warnings).append(issue)
        markers = _list(question.get("markers", []), f"problem_restatement.questions[{index}].markers", default=[])
        missing = _missing_markers(text, markers, f"problem_restatement.{label}")
        if missing:
            issue = _warning("PROBLEM_RESTATEMENT_TASK_DETAIL_NOT_EVIDENT", f"{label}: {', '.join(missing)}")
            (hard_failures if strict else warnings).append(issue)
    for forbidden in spec.get("forbidden_method_terms", []):
        if str(forbidden) in text:
            issue = _warning("PROBLEM_RESTATEMENT_METHOD_TERM_PRESENT", str(forbidden))
            (hard_failures if strict else warnings).append(issue)
    if strict and tasks is not None and len(labels) != len(tasks):
        hard_failures.append(_hard_failure("PROBLEM_RESTATEMENT_TASK_COUNT_MISMATCH", f"expected {len(tasks)}, found {len(labels)}"))
    return _report(warnings=warnings, hard_failures=hard_failures, question_labels=labels)


def validate_keywords(text: str, spec: dict[str, Any], *, strict: bool = False) -> dict[str, Any]:
    normalized = text.strip()
    keywords = [item.strip() for item in re.split(r"[；;]", normalized) if item.strip()]
    minimum = int(spec.get("minimum_keywords", 3 if strict else 0))
    maximum = int(spec.get("maximum_keywords", 8))
    if minimum < 0 or maximum < minimum:
        raise WritingPolicyError("KEYWORD_COUNT_CONTRACT_INVALID")
    warnings: list[dict[str, str]] = []
    hard_failures: list[dict[str, str]] = []
    if len(keywords) < minimum or len(keywords) > maximum:
        issue = _warning("KEYWORD_COUNT_OUT_OF_RANGE", f"expected {minimum}-{maximum}, found {len(keywords)}")
        (hard_failures if strict else warnings).append(issue)
    if "\n" in normalized or "\r" in normalized:
        issue = _warning("KEYWORDS_MUST_REMAIN_SINGLE_LINE", "newline found")
        (hard_failures if strict else warnings).append(issue)
    return _report(
        warnings=warnings,
        hard_failures=hard_failures,
        keyword_count=len(keywords),
        minimum_keywords=minimum,
        maximum_keywords=maximum,
    )


def validate_formula_derivation_chains(
    texts: dict[str, str],
    spec: dict[str, Any],
    *,
    task_contract: dict[str, Any] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    chains = _list(spec.get("chains", []), "formula_derivation.chains", default=[])
    warnings: list[dict[str, str]] = []
    hard_failures: list[dict[str, str]] = []
    checked: list[str] = []
    checked_task_ids: set[str] = set()
    for index, chain in enumerate(chains, 1):
        if not isinstance(chain, dict):
            raise WritingPolicyError(f"FORMULA_DERIVATION_CHAIN_INVALID: {index}")
        question = _nonempty(chain.get("question"), f"formula_derivation.chains[{index}].question")
        task_id = str(chain.get("task_id") or "").strip()
        if task_id:
            checked_task_ids.add(task_id)
        steps = _list(chain.get("steps", []), f"formula_derivation.chains[{index}].steps", default=[])
        if len(steps) < 3:
            issue = _warning("FORMULA_DERIVATION_CHAIN_SHORT", question)
            (hard_failures if strict else warnings).append(issue)
        locations = _list(chain.get("content_keys", []), f"formula_derivation.chains[{index}].content_keys", default=[])
        combined = "\n".join(texts.get(str(key), "") for key in locations)
        missing = _missing_markers(combined, _list(chain.get("markers", []), f"formula_derivation.chains[{index}].markers", default=[]), f"formula_derivation.{question}")
        if missing:
            issue = _warning("FORMULA_DERIVATION_EVIDENCE_NOT_EVIDENT", f"{question}: {', '.join(missing)}")
            (hard_failures if strict else warnings).append(issue)
        checked.append(question)
    expected_task_ids: set[str] = set()
    if strict and task_contract is not None:
        expected_task_ids = {str(task["task_id"]) for task in task_contract["tasks"]}
        for task_id in sorted(expected_task_ids - checked_task_ids):
            hard_failures.append(_hard_failure("FORMULA_DERIVATION_CHAIN_MISSING_FOR_TASK", task_id))
        for task_id in sorted(checked_task_ids - expected_task_ids):
            hard_failures.append(_hard_failure("FORMULA_DERIVATION_CHAIN_UNKNOWN_TASK", task_id))
    return _report(
        warnings=warnings,
        hard_failures=hard_failures,
        chains_checked=checked,
        task_ids_checked=sorted(checked_task_ids),
        expected_task_ids=sorted(expected_task_ids),
        semantic_proof_claimed=False,
    )


def _semantic_subscript_pattern(mapping: dict[str, str]) -> re.Pattern[str] | None:
    if not mapping:
        return None
    escaped = "|".join(re.escape(key) for key in mapping)
    return re.compile(
        r"(?:v|V|p|r|F|T|u|h)_\{?\s*(?:\\text\{)?(?:" + escaped + r")\b",
        re.IGNORECASE,
    )


def validate_symbol_policy(text: str, spec: dict[str, Any], *, strict: bool = False) -> dict[str, Any]:
    mapping = spec.get("semantic_subscript_mapping", spec.get("semantic_subscript_whitelist", {}))
    if mapping is None:
        mapping = {}
    if not isinstance(mapping, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in mapping.items()):
        raise WritingPolicyError("SEMANTIC_SUBSCRIPT_MAPPING_INVALID")
    protected = _list(spec.get("protected_math_symbols", DEFAULT_PROTECTED_MATH_SYMBOLS), "symbol_policy.protected_math_symbols")
    pattern = _semantic_subscript_pattern(mapping)
    warnings: list[dict[str, str]] = []
    hard_failures: list[dict[str, str]] = []
    decision = str(spec.get("semantic_subscript_decision") or "").strip().upper()
    if strict and decision not in {"REQUIRED", "NOT_APPLICABLE"}:
        hard_failures.append(_hard_failure("SEMANTIC_SUBSCRIPT_DECISION_REQUIRED", decision or "missing"))
    if strict and decision == "REQUIRED" and not mapping:
        hard_failures.append(_hard_failure("SEMANTIC_SUBSCRIPT_MAPPING_REQUIRED", "mapping is empty"))
    if strict and decision == "REQUIRED":
        for source, target in mapping.items():
            if not re.search(r"[\u4e00-\u9fff]", target):
                hard_failures.append(_hard_failure("SEMANTIC_SUBSCRIPT_CHINESE_TARGET_REQUIRED", source))
    if pattern is not None:
        remaining = sorted(set(match.group(0) for match in pattern.finditer(text)))
        if remaining:
            issue = _warning("ENGLISH_SEMANTIC_SUBSCRIPT_REMAINS", ", ".join(remaining))
            (hard_failures if strict else warnings).append(issue)
    return _report(
        warnings=warnings,
        hard_failures=hard_failures,
        semantic_subscript_mapping=mapping,
        semantic_subscript_decision=decision or "UNDECLARED",
        protected_math_symbols=protected,
        protection_mode="DECLARATIVE_NO_REWRITE",
    )


def validate_paragraph_structure(
    texts: dict[str, str],
    spec: dict[str, Any],
    *,
    task_contract: dict[str, Any] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    records = _list(spec.get("records", []), "paragraph_policy.records", default=[])
    hard_failures: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    checked: set[str] = set()
    for index, record in enumerate(records, 1):
        if not isinstance(record, dict):
            raise WritingPolicyError(f"PARAGRAPH_POLICY_RECORD_INVALID: {index}")
        key = _nonempty(record.get("content_key"), f"paragraph_policy.records[{index}].content_key")
        minimum = int(record.get("minimum_paragraphs", 1))
        if minimum < 1:
            raise WritingPolicyError(f"PARAGRAPH_POLICY_MINIMUM_INVALID: {key}")
        text = texts.get(key, "")
        count = len([part for part in re.split(r"\n\s*\n", text) if part.strip()])
        checked.add(key)
        if count < minimum:
            issue = _warning("PARAGRAPH_STRUCTURE_INSUFFICIENT", f"{key}: expected {minimum}, found {count}")
            (hard_failures if strict else warnings).append(issue)
    expected: set[str] = set()
    if strict and task_contract is not None:
        for task in task_contract["tasks"]:
            expected.add(str(task["analysis_content_key"]))
            expected.add(str(task["content_key"]))
        for key in sorted(expected - checked):
            hard_failures.append(_hard_failure("PARAGRAPH_POLICY_MISSING_FOR_TASK_CONTENT", key))
    return _report(
        warnings=warnings,
        hard_failures=hard_failures,
        content_keys_checked=sorted(checked),
        expected_task_content_keys=sorted(expected),
    )


def validate_task_closure(
    texts: dict[str, str],
    spec: dict[str, Any],
    *,
    task_contract: dict[str, Any],
    strict: bool = False,
) -> dict[str, Any]:
    records = _list(spec.get("records", []), "task_closure_contract.records", default=[])
    expected = {str(task["task_id"]): task for task in task_contract["tasks"]}
    seen: set[str] = set()
    hard_failures: list[dict[str, str]] = []
    required_marker_names = ("model", "derivation", "result", "validation", "conclusion")
    for index, record in enumerate(records, 1):
        if not isinstance(record, dict):
            raise WritingPolicyError(f"TASK_CLOSURE_RECORD_INVALID: {index}")
        task_id = _nonempty(record.get("task_id"), f"task_closure_contract.records[{index}].task_id")
        seen.add(task_id)
        if task_id not in expected:
            hard_failures.append(_hard_failure("TASK_CLOSURE_UNKNOWN_TASK", task_id))
            continue
        analysis_key = str(expected[task_id]["analysis_content_key"])
        content_key = str(expected[task_id]["content_key"])
        combined = texts.get(analysis_key, "") + "\n" + texts.get(content_key, "")
        markers = record.get("markers")
        if not isinstance(markers, dict):
            hard_failures.append(_hard_failure("TASK_CLOSURE_MARKERS_REQUIRED", task_id))
            continue
        for name in required_marker_names:
            marker = str(markers.get(name) or "").strip()
            if not marker or marker not in combined:
                hard_failures.append(_hard_failure("TASK_CLOSURE_COMPONENT_NOT_VISIBLE", f"{task_id}.{name}"))
        visual_decision = str(record.get("visual_decision") or "").strip().upper()
        if visual_decision not in {"REQUIRED", "NOT_REQUIRED_WITH_REASON"}:
            hard_failures.append(_hard_failure("TASK_VISUAL_DECISION_REQUIRED", task_id))
        if visual_decision == "NOT_REQUIRED_WITH_REASON" and not str(record.get("visual_reason") or "").strip():
            hard_failures.append(_hard_failure("TASK_VISUAL_REASON_REQUIRED", task_id))
    if strict:
        for task_id in sorted(set(expected) - seen):
            hard_failures.append(_hard_failure("TASK_CLOSURE_RECORD_MISSING", task_id))
    return _report(hard_failures=hard_failures, task_ids_checked=sorted(seen), expected_task_ids=sorted(expected))


def validate_public_language(text: str, *, strict: bool = True) -> dict[str, Any]:
    issues: list[str] = []
    visible_text = re.sub(r"\\(?:label|ref|eqref)\{[^}]+\}", "", text)
    if _PUBLIC_Q_RE.search(visible_text):
        issues.append("PUBLIC_PROBLEM_LABEL_FORBIDDEN")
    if _INTERNAL_STATE_RE.search(visible_text):
        issues.append("INTERNAL_STATE_TERM_LEAKAGE")
    if strict and issues:
        raise WritingPolicyError(issues[0])
    return _report(
        hard_failures=[_hard_failure(code, "public paper content") for code in issues],
        strict=strict,
    )


def _contains_chinese(value: Any) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", str(value or "")))


def validate_figure_placement(
    spec: dict[str, Any],
    content_root: Path | None = None,
    *,
    strict: bool = False,
) -> dict[str, Any]:
    figures = _list(spec.get("figures", []), "figure_placement.figures", default=[])
    warnings: list[dict[str, str]] = []
    hard_failures: list[dict[str, str]] = []
    formal_backend = spec.get("formal_figure_backend")
    if figures and formal_backend is None:
        raise WritingPolicyError("FORMAL_FIGURE_BACKEND_REQUIRED")
    if formal_backend is not None and formal_backend != "MATLAB":
        raise WritingPolicyError("FORMAL_FIGURE_BACKEND_MUST_BE_MATLAB")
    checked: list[str] = []
    for index, figure in enumerate(figures, 1):
        if not isinstance(figure, dict):
            raise WritingPolicyError(f"FIGURE_PLAN_INVALID: {index}")
        figure_id = _nonempty(figure.get("figure_id"), f"figure_placement.figures[{index}].figure_id")
        checked.append(figure_id)
        for field in ("public_title", "first_discussion"):
            if not isinstance(figure.get(field), str) or not str(figure.get(field)).strip():
                warnings.append(_warning("FIGURE_METADATA_INCOMPLETE", f"{figure_id}.{field}"))
        source_binding = figure.get("source_binding")
        if not isinstance(source_binding, (str, dict)) or not source_binding:
            warnings.append(_warning("FIGURE_METADATA_INCOMPLETE", f"{figure_id}.source_binding"))
        if formal_backend == "MATLAB" and figure.get("formal", True):
            if figure.get("render_backend") != "MATLAB":
                hard_failures.append(_hard_failure("FORMAL_FIGURE_BACKEND_MISMATCH", figure_id))
            script = figure.get("matlab_script_path")
            if not isinstance(script, str) or not script.strip():
                hard_failures.append(_hard_failure("MATLAB_FIGURE_SCRIPT_REQUIRED", figure_id))
            inline_spec = figure.get("matlab_figure_spec")
            if inline_spec is None:
                hard_failures.append(_hard_failure("MATLAB_FIGURE_SPEC_REQUIRED", figure_id))
            else:
                try:
                    normalized = validate_figure_spec(inline_spec)
                except MatlabFigureSpecError as exc:
                    hard_failures.append(_hard_failure("MATLAB_FIGURE_SPEC_INVALID", f"{figure_id}: {exc}"))
                    continue
                if strict:
                    language_policy = str(inline_spec.get("language_policy") or "").strip().upper()
                    if language_policy != "ZH_PRIMARY":
                        hard_failures.append(_hard_failure("FORMAL_FIGURE_LANGUAGE_POLICY_REQUIRED", figure_id))
                    for field in ("title", "x_label", "y_label"):
                        value = normalized.get(field)
                        if value and not _contains_chinese(value):
                            hard_failures.append(_hard_failure("FORMAL_FIGURE_CHINESE_LABEL_REQUIRED", f"{figure_id}.{field}"))
                    visible_names: list[str] = []
                    for item in normalized.get("series", []):
                        if isinstance(item, dict) and item.get("name"):
                            visible_names.append(str(item["name"]))
                    visible_names.extend(str(item) for item in normalized.get("categories", []) if str(item).strip())
                    for panel in normalized.get("panels", []):
                        if not isinstance(panel, dict):
                            continue
                        if panel.get("title"):
                            visible_names.append(str(panel["title"]))
                        visible_names.extend(
                            str(item.get("label"))
                            for item in panel.get("objects", [])
                            if isinstance(item, dict) and str(item.get("label") or "").strip()
                        )
                        visible_names.extend(
                            str(item.get("label"))
                            for item in panel.get("relations", [])
                            if isinstance(item, dict) and str(item.get("label") or "").strip()
                        )
                    for index, value in enumerate(visible_names, 1):
                        if not _contains_chinese(value):
                            hard_failures.append(_hard_failure("FORMAL_FIGURE_CHINESE_SERIES_LABEL_REQUIRED", f"{figure_id}.{index}"))
    return _report(
        warnings=warnings,
        hard_failures=hard_failures,
        figures_checked=checked,
        placement_control="MATLAB_ASSET_BINDING_NO_AUTO_PAGINATION" if formal_backend == "MATLAB" else "METADATA_ONLY_NO_AUTO_PAGINATION",
    )


def _citation_keys(text: str) -> set[str]:
    keys: set[str] = set()
    for raw in re.findall(r"\\cite(?:[tp])?\{([^}]+)\}", text):
        keys.update(item.strip() for item in raw.split(",") if item.strip())
    return keys


def _bibitem_keys(text: str) -> set[str]:
    return {item.strip() for item in re.findall(r"\\bibitem(?:\[[^]]+\])?\{([^}]+)\}", text) if item.strip()}


def validate_references(texts: dict[str, str], spec: dict[str, Any]) -> dict[str, Any]:
    references = texts.get("references", "")
    body = "\n".join(value for key, value in texts.items() if key != "references")
    cited = _citation_keys(body)
    listed = _bibitem_keys(references)
    missing = sorted(listed - cited)
    extra = sorted(cited - listed)
    hard_failures: list[dict[str, str]] = []
    if missing or extra:
        hard_failures.append(_hard_failure("REFERENCE_BIDIRECTIONAL_MISMATCH", f"listed_not_cited={missing}; cited_not_listed={extra}"))
    warnings: list[dict[str, str]] = []
    minimum = int(spec.get("minimum_references", 0))
    if minimum and len(listed) < minimum:
        warnings.append(_warning("REFERENCE_COUNT_LOW", f"expected at least {minimum}, found {len(listed)}"))
    if any(len(line.strip()) < 8 for line in references.splitlines() if "\\bibitem" in line):
        warnings.append(_warning("REFERENCE_METADATA_SPARSE", "one or more bibliography entries are unusually short"))
    return _report(
        warnings=warnings,
        hard_failures=hard_failures,
        listed_keys=sorted(listed),
        cited_keys=sorted(cited),
    )


def validate_limitations(texts: dict[str, str], spec: dict[str, Any]) -> dict[str, Any]:
    limitations = _list(spec.get("limitations", []), "limitation_policy.limitations", default=[])
    hard_failures: list[dict[str, str]] = []
    checked: list[str] = []
    public_ids: list[str] = []
    internal_ids: list[str] = []
    for index, item in enumerate(limitations, 1):
        if not isinstance(item, dict):
            raise WritingPolicyError(f"LIMITATION_ENTRY_INVALID: {index}")
        limitation_id = _nonempty(item.get("limitation_id", f"limitation_{index}"), f"limitations[{index}].limitation_id")
        visibility = item.get("visibility", "public")
        if visibility not in {"public", "internal"}:
            raise WritingPolicyError(f"LIMITATION_VISIBILITY_INVALID: {limitation_id}")
        locations = _list(item.get("content_keys", []), f"limitations[{index}].content_keys", default=[])
        combined = "\n".join(texts.get(str(key), "") for key in locations)
        markers = _list(item.get("markers", []), f"limitations[{index}].markers", default=[])
        checked.append(limitation_id)
        if visibility == "internal":
            internal_ids.append(limitation_id)
            continue
        public_ids.append(limitation_id)
        missing = _missing_markers(combined, markers, f"limitations[{index}]")
        if missing and item.get("required", True):
            hard_failures.append(_hard_failure("LIMITATION_PROPAGATION_MISSING", f"{limitation_id}: {', '.join(missing)}"))
    return _report(
        hard_failures=hard_failures,
        limitation_ids_checked=checked,
        public_limitation_ids=public_ids,
        internal_limitation_ids=internal_ids,
    )


def _merge_reports(*reports: dict[str, Any]) -> dict[str, Any]:
    warnings: list[dict[str, str]] = []
    hard_failures: list[dict[str, str]] = []
    for report in reports:
        warnings.extend(report.get("warnings", []))
        hard_failures.extend(report.get("hard_failures", []))
    return _report(warnings=warnings, hard_failures=hard_failures)


def validate_result_acceptance(
    texts: dict[str, str],
    spec: dict[str, Any],
    *,
    task_contract: dict[str, Any] | None = None,
    limitation_spec: dict[str, Any] | None = None,
    strict_visibility: bool = False,
) -> dict[str, Any]:
    """Bind declared result cards to the exact public writer fragments."""

    try:
        report = validate_result_acceptance_contract(
            spec, task_contract=task_contract, strict_visibility=strict_visibility
        )
    except ResultAcceptanceContractError as exc:
        raise WritingPolicyError(str(exc)) from exc
    failures = list(report["hard_failures"])
    public_limitations = _list(
        (limitation_spec or {}).get("limitations", []),
        "limitation_policy.limitations",
        default=[],
    )
    public_limitation_markers: list[tuple[str, str, str]] = []
    for limitation in public_limitations:
        if not isinstance(limitation, dict) or limitation.get("visibility", "public") != "public":
            continue
        limitation_id = str(limitation.get("limitation_id") or "unknown")
        locations = _list(limitation.get("content_keys", []), f"limitation.{limitation_id}.content_keys", default=[])
        markers = _list(limitation.get("markers", []), f"limitation.{limitation_id}.markers", default=[])
        for location in locations:
            for marker in markers:
                public_limitation_markers.append((str(location), str(marker), limitation_id))
    for card in spec.get("result_cards", []):
        if not isinstance(card, dict):
            continue
        result_id = str(card.get("result_id") or "unknown")
        location = str(card.get("paper_location") or "")
        text = texts.get(location)
        if text is None:
            failures.append(_hard_failure("RESULT_PAPER_LOCATION_UNKNOWN", f"{result_id}.{location}"))
            continue
        for field in ("display_value", "unit"):
            value = str(card.get(field) or "")
            if value and value not in text:
                failures.append(_hard_failure("RESULT_PAPER_BINDING_MISSING", f"{result_id}.{field}"))
    for item in spec.get("input_conditions", []):
        if isinstance(item, dict):
            text = texts.get(str(item.get("paper_location") or ""), "")
            if str(item.get("marker") or "") not in text:
                failures.append(_hard_failure("INPUT_CONDITION_NOT_VISIBLE", str(item.get("condition_id") or "unknown")))
    for item in [*spec.get("visual_bindings", []), *spec.get("method_output_links", [])]:
        if isinstance(item, dict):
            text = texts.get(str(item.get("paper_location") or ""), "")
            markers = [item.get("caption_marker")] if "caption_marker" in item else [item.get("method_marker"), item.get("output_marker")]
            for marker in markers:
                if str(marker or "") not in text:
                    failures.append(_hard_failure("DECLARED_PAPER_LINK_NOT_VISIBLE", str(item.get("visual_id") or item.get("method_id") or "unknown")))
    for answer in spec.get("answer_cards", []):
        if not isinstance(answer, dict):
            continue
        task_id = str(answer.get("task_id") or "unknown")
        location = str(answer.get("paper_location") or "")
        marker = str(answer.get("answer_marker") or "")
        text = texts.get(location)
        if text is None or not marker or marker not in text:
            failures.append(_hard_failure("ANSWER_MARKER_NOT_VISIBLE", task_id))
            continue
        answer_position = text.index(marker)
        for limitation_location, limitation_marker, limitation_id in public_limitation_markers:
            if limitation_location != location or limitation_marker not in text:
                continue
            if text.index(limitation_marker) < answer_position:
                failures.append(_hard_failure("ANSWER_BEFORE_PUBLIC_LIMITATION_REQUIRED", f"{task_id}.{limitation_id}"))
    return _report(warnings=list(report.get("warnings", [])), hard_failures=failures, contract=report)


def validate_paper_evidence(
    texts: dict[str, str],
    spec: dict[str, Any],
    result_contract: dict[str, Any] | None,
    *,
    task_contract: dict[str, Any] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Check declared model/assumption/claim records against public fragments."""

    result_ids = {
        str(item.get("result_id"))
        for item in (result_contract or {}).get("result_cards", [])
        if isinstance(item, dict) and item.get("result_id")
    }
    try:
        report = validate_paper_evidence_contract(spec, result_card_ids=result_ids)
    except PaperEvidenceContractError as exc:
        raise WritingPolicyError(str(exc)) from exc
    failures = list(report["hard_failures"])
    if strict and task_contract is not None:
        expected = {str(task["task_id"]) for task in task_contract["tasks"]}
        actual = set(report.get("tasks_checked", []))
        for task_id in sorted(expected - actual):
            failures.append(_hard_failure("MODEL_EVIDENCE_MISSING_FOR_TASK", task_id))
        claim_tasks = {
            str(claim.get("task_id"))
            for claim in spec.get("claim_evidence_cards", [])
            if isinstance(claim, dict) and claim.get("task_id")
        }
        for task_id in sorted(expected - claim_tasks):
            failures.append(_hard_failure("CLAIM_EVIDENCE_MISSING_FOR_TASK", task_id))
        if not spec.get("assumption_ledger"):
            failures.append(_hard_failure("ASSUMPTION_LEDGER_REQUIRED", "empty"))
    for block in spec.get("model_blocks", []):
        if isinstance(block, dict):
            text = texts.get(str(block.get("paper_location") or ""), "")
            for marker in (block.get("method_selection_basis"), block.get("physical_meaning"), block.get("boundary_marker")):
                if isinstance(marker, str) and marker and marker not in text:
                    failures.append(_hard_failure("MODEL_LOGICAL_CLOSURE_NOT_VISIBLE", str(block.get("model_id") or "unknown")))
    for comparison in spec.get("optimization_comparisons", []):
        if isinstance(comparison, dict):
            text = texts.get(str(comparison.get("paper_location") or ""), "")
            marker = comparison.get("comparison_marker")
            if isinstance(marker, str) and marker and marker not in text:
                failures.append(_hard_failure("OPTIMIZATION_COMPARISON_NOT_VISIBLE", str(comparison.get("task_id") or "unknown")))
    for claim in spec.get("claim_evidence_cards", []):
        if isinstance(claim, dict):
            text = texts.get(str(claim.get("paper_location") or ""), "")
            marker = claim.get("claim_marker")
            if isinstance(marker, str) and marker and marker not in text:
                failures.append(_hard_failure("CLAIM_NOT_VISIBLE", str(claim.get("claim_id") or "unknown")))
    return _report(warnings=list(report.get("warnings", [])), hard_failures=failures, contract=report)


def validate_literature_usage(texts: dict[str, str], spec: dict[str, Any]) -> dict[str, Any]:
    cited = _citation_keys("\n".join(value for key, value in texts.items() if key != "references"))
    try:
        report = validate_literature_usage_contract(spec, cited_keys=cited)
    except LiteratureUsageContractError as exc:
        raise WritingPolicyError(str(exc)) from exc
    failures = list(report["hard_failures"])
    for record in spec.get("records", []):
        if isinstance(record, dict):
            location = str(record.get("paper_location") or "")
            citation = str(record.get("citation_key") or "")
            if citation and citation not in texts.get(location, ""):
                failures.append(_hard_failure("LITERATURE_USAGE_NOT_VISIBLE", str(record.get("record_id") or "unknown")))
    return _report(warnings=list(report.get("warnings", [])), hard_failures=failures, contract=report)


def validate_writing_policy(payload: dict[str, Any], content_root: Path, content: dict[str, str]) -> dict[str, Any]:
    """Validate the declared current policy or an explicitly recognized legacy manifest."""

    try:
        manifest_mode = resolve_manifest_mode(payload, content_keys=set(content))
    except WriterSchemaError as exc:
        raise WritingPolicyError(str(exc)) from exc
    policy = payload.get("writing_policy")
    if policy is None:
        if manifest_mode != LEGACY_COMPATIBILITY_MODE:
            raise WritingPolicyError("CURRENT_FORMAL_POLICY_REQUIRED")
        return {
            "status": "PASS",
            "mode": "LEGACY_COMPATIBILITY_BYPASS",
            "policy_version": WRITING_POLICY_VERSION,
            "explicit_policy": False,
            "migration_mode": "LEGACY_MANIFEST_COMPATIBLE",
            "manifest_mode": manifest_mode,
            "warnings": [],
            "hard_failures": [],
            "appendix_code_policy": APPENDIX_CODE_MODE,
        }
    if not isinstance(policy, dict):
        raise WritingPolicyError("WRITING_POLICY_INVALID")
    if policy.get("version") not in SUPPORTED_POLICY_VERSIONS:
        raise WritingPolicyError("WRITING_POLICY_VERSION_UNSUPPORTED")
    if manifest_mode == CURRENT_FORMAL_MODE and not isinstance(payload.get("task_contract"), dict):
        raise WritingPolicyError("CURRENT_FORMAL_TASK_CONTRACT_REQUIRED")
    try:
        task_contract = resolve_task_contract(payload)
    except WriterSchemaError as exc:
        raise WritingPolicyError(str(exc)) from exc
    texts = _load_texts(content_root, content)
    public_keys = policy.get("public_content_keys")
    if public_keys is None:
        public_keys = list(texts)
    public_keys = _list(public_keys, "public_content_keys")
    public_text = "\n".join(texts.get(str(key), "") for key in public_keys)
    strict = manifest_mode == CURRENT_FORMAL_MODE and policy.get("version") in {
        "controlled_writing_layer_v3",
        "controlled_writing_layer_v4",
        WRITING_POLICY_VERSION,
    }
    visual_reasoning_required = (
        manifest_mode == CURRENT_FORMAL_MODE
        and policy.get("version") in FORMAL_POLICY_VERSIONS
    )
    argument_blueprint_required = (
        manifest_mode == CURRENT_FORMAL_MODE
        and policy.get("version") == WRITING_POLICY_VERSION
    )
    abstract = validate_abstract_structure(
        texts.get("abstract", ""), policy.get("abstract", {}), task_contract=task_contract, strict=strict
    )
    keywords = validate_keywords(
        texts.get("keywords", ""), policy.get("abstract", {}), strict=strict
    )
    restatement = validate_problem_restatement(
        texts.get("problem_restatement", ""),
        policy.get("problem_restatement", {}),
        task_contract["tasks"],
        strict=strict,
    )
    derivation = validate_formula_derivation_chains(
        texts, policy.get("formula_derivation", {}), task_contract=task_contract, strict=strict
    )
    symbols = validate_symbol_policy(public_text, policy.get("symbol_policy", {}), strict=strict)
    paragraphs = validate_paragraph_structure(
        texts, policy.get("paragraph_policy", {}), task_contract=task_contract, strict=strict
    )
    task_closure = validate_task_closure(
        texts, policy.get("task_closure_contract", {}), task_contract=task_contract, strict=strict
    )
    language = validate_public_language(public_text, strict=strict)
    figures = validate_figure_placement(policy.get("figure_placement", {}), content_root, strict=strict)
    references = validate_references(texts, policy.get("reference_policy", {}))
    limitation_spec = policy.get("limitation_policy", {})
    limitations = validate_limitations(texts, limitation_spec)
    result_acceptance_spec = policy.get("result_acceptance_contract")
    if result_acceptance_spec is None and manifest_mode == CURRENT_FORMAL_MODE:
        raise WritingPolicyError("RESULT_ACCEPTANCE_CONTRACT_REQUIRED_FOR_CURRENT_FORMAL")
    result_acceptance = (
        validate_result_acceptance(
            texts,
            result_acceptance_spec,
            task_contract=task_contract,
            limitation_spec=limitation_spec,
            strict_visibility=strict,
        )
        if result_acceptance_spec is not None
        else _report()
    )
    evidence_spec = policy.get("paper_evidence_contract")
    paper_evidence = (
        validate_paper_evidence(
            texts, evidence_spec, result_acceptance_spec, task_contract=task_contract, strict=strict
        )
        if evidence_spec is not None
        else _report()
    )
    literature_spec = policy.get("literature_usage_contract")
    literature_usage = (
        validate_literature_usage(texts, literature_spec)
        if literature_spec is not None
        else _report()
    )
    if visual_reasoning_required:
        try:
            visual_reasoning = validate_visual_reasoning_contract(
                policy.get("visual_reasoning_contract"),
                task_contract=task_contract,
                figure_placement=policy.get("figure_placement", {}),
                result_acceptance_contract=result_acceptance_spec or {},
                paper_evidence_contract=evidence_spec or {},
                content_root=content_root,
                content=content,
            )
        except VisualReasoningContractError as exc:
            raise WritingPolicyError(str(exc)) from exc
    else:
        visual_reasoning = _report()
    if argument_blueprint_required:
        try:
            argument_blueprint = validate_argument_blueprint_contract(
                policy.get("argument_blueprint_contract"),
                task_contract=task_contract,
                result_acceptance_contract=result_acceptance_spec or {},
                paper_evidence_contract=evidence_spec or {},
                content_root=content_root,
                content=content,
            )
        except ArgumentBlueprintContractError as exc:
            raise WritingPolicyError(str(exc)) from exc
    else:
        argument_blueprint = _report()
    appendix_mode = policy.get("appendix_code_mode", APPENDIX_CODE_MODE)
    if strict:
        if appendix_mode not in FINAL_APPENDIX_CODE_MODES:
            raise WritingPolicyError("APPENDIX_CODE_POLICY_DECISION_REQUIRED")
        if appendix_mode == "NOT_REQUIRED" and not str(policy.get("appendix_code_reason") or "").strip():
            raise WritingPolicyError("APPENDIX_CODE_REASON_REQUIRED")
    elif appendix_mode != APPENDIX_CODE_MODE:
        raise WritingPolicyError("APPENDIX_CODE_POLICY_MUST_REMAIN_PENDING_USER_POLICY")
    overall = _merge_reports(
        abstract, keywords, restatement, derivation, symbols, paragraphs, task_closure, language, figures, references, limitations, result_acceptance,
        paper_evidence, literature_usage, visual_reasoning,
        argument_blueprint,
    )
    result = {
        **overall,
        "policy_version": str(policy.get("version")),
        "explicit_policy": True,
        "manifest_mode": manifest_mode,
        "task_contract": task_contract,
        "abstract": abstract,
        "keywords": keywords,
        "problem_restatement": restatement,
        "formula_derivation": derivation,
        "symbol_policy": symbols,
        "paragraph_policy": paragraphs,
        "task_closure_contract": task_closure,
        "public_language": language,
        "figure_placement": figures,
        "references": references,
        "limitations": limitations,
        "result_acceptance": result_acceptance,
        "paper_evidence": paper_evidence,
        "literature_usage": literature_usage,
        "visual_reasoning": visual_reasoning,
        "argument_blueprint": argument_blueprint,
        "appendix_code_policy": appendix_mode,
    }
    if result["hard_failures"]:
        codes = ", ".join(item["code"] for item in result["hard_failures"])
        raise WritingPolicyError(f"WRITING_POLICY_HARD_FAILURE: {codes}")
    return result


def normalize_public_problem_label(label: str) -> str:
    """Compatibility helper; explicit task contracts now supply public labels."""

    match = re.fullmatch(r"Q([1-9])", label, flags=re.IGNORECASE)
    if not match:
        return label
    return "问题" + "一二三四五六七八九"[int(match.group(1)) - 1]
