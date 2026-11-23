"""Deterministically render competition-paper TeX from writer content.

The writer supplies content fragments and structured question content only.
This module owns the complete top-level section tree, while the authoritative
TeX template owns typography and page behavior.  It does not run models,
change results, or infer claims.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from pipeline_core.paper_quality.writing_policy import (
    FORMAL_POLICY_VERSIONS,
    WRITING_POLICY_VERSION,
    WritingPolicyError,
    validate_writing_policy,
)
from pipeline_core.paper_quality.argument_blueprint_contract import TRACE_VERSION
from pipeline_core.paper_quality.matlab_figure_pipeline import (
    FigurePaperBindingError,
    validate_figure_paper_binding,
)
from pipeline_core.paper_quality.writer_schema import WriterSchemaError, resolve_task_contract


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = ROOT / "pipeline_core" / "freeze" / "formal_contest_freeze" / "final_paper_template.tex"
SCHEMA_VERSION = "deterministic_competition_paper_renderer_v3"
TRACK_C_SCHEMA_VERSION = "track_c_paper_expression_runtime_v2"
TRACK_C_RECEIPT_VERSION = "track_c_receipt_v2"
WRITER_SCHEMA_VERSION = "WRITER_SCHEMA_V2"
TEMPLATE_CONTRACT_VERSION = "competition_paper_template_contract_v1"
LEGACY_WRITER_KEYS = {
    "model_building",
    "solution_process",
    "results_analysis",
    "question_summary",
}

REQUIRED_BASE_KEYS = (
    "title",
    "authors",
    "abstract",
    "keywords",
    "problem_restatement",
    "model_assumptions",
    "notation",
    "model_evaluation",
    "references",
    "appendix",
)
QUESTION_KEY_RE = re.compile(r"^question_content_[a-z][a-z0-9_]*$")
CONTENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
EQUATION_LABEL_RE = re.compile(r"^eq:[A-Za-z0-9][A-Za-z0-9:._-]*$")
INPUT_RE = re.compile(r"\\(?:input|include)\s*\{([^}]+)\}")
FIGURE_ASSET_EXTENSIONS = {".png", ".pdf"}
FIGURE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
FORBIDDEN_CONTENT_PATTERNS = (
    (re.compile(r"\\section\*?\s*\{"), "WRITER_TOP_LEVEL_SECTION_FORBIDDEN"),
    (re.compile(r"\\tableofcontents\b"), "TOC_FORBIDDEN"),
    (re.compile(r"\\(?:documentclass|usepackage|geometry|ctexset)\b"), "WRITER_TEMPLATE_COMMAND_FORBIDDEN"),
    (re.compile(r"\\(?:zihao|fontsize|large|Large|small|footnotesize|scriptsize)\b"), "WRITER_TYPOGRAPHY_COMMAND_FORBIDDEN"),
    (re.compile(r"\\(?:newcommand|renewcommand|providecommand|def|let)\b"), "WRITER_MACRO_COMMAND_FORBIDDEN"),
    (re.compile(r"\\(?:clearpage|newpage|pagebreak|FloatBarrier|vspace)\b"), "WRITER_PAGE_CONTROL_FORBIDDEN"),
    (re.compile(r"\\begin\{(?:enumerate|thebibliography)\}|\\end\{(?:enumerate|thebibliography)\}"), "WRITER_STRUCTURE_ENVIRONMENT_FORBIDDEN"),
    (re.compile(r"(?i)\b(?:preview|debug|format-only|pipeline|renderer|test)\b"), "WRITER_INTERNAL_TEXT_FORBIDDEN"),
)


class PaperRendererError(ValueError):
    """Raised when writer content cannot enter the fixed paper template."""


def validate_authoritative_template_contract(template: str) -> dict[str, Any]:
    """Fail closed when the single paper template loses frozen layout rules."""

    required_tokens = {
        "xelatex_engine": "% !TeX program = xelatex",
        "portable_fontset": "fontset=none",
        "fixed_geometry": r"\usepackage[left=2.7cm,right=2.7cm,top=2.54cm,bottom=2.54cm,footskip=1.0cm]{geometry}",
        "first_paragraph_indent": r"\usepackage{indentfirst}",
        "two_character_indent": r"\setlength{\parindent}{2em}",
        "uniform_superscript_citation": r"\renewcommand{\cite}[1]{\textsuperscript{\PaperOriginalCite{#1}}}",
        "times_file_binding": "C:/Windows/Fonts/times.ttf",
        "simsun_file_binding": "C:/Windows/Fonts/simsun.ttc",
        "portable_latin_fallback": r"\setmainfont{TeX Gyre Termes}",
        "portable_cjk_fallback": "FandolSong-Regular",
        "fixed_figure_placement": r"\begin{figure}[htbp]",
        "centered_primary_heading": r"{\filcenter\zihao{4}\heiti}",
        "semantic_table_header_font": r"\newcommand{\PaperTableHead}[1]{{\kaishu #1}}",
        "single_structure_marker": "% PAPER_RENDERER:STRUCTURE_BODY",
    }
    # The current case has an explicitly authorized layout correction: body
    # single spacing and an abstract using the full body width.  Keep the
    # former tokens accepted so older cases remain compatible.
    layout_alternatives = {
        "body_spacing": (r"\setstretch{1.5}", r"\setstretch{1}"),
        "abstract_keyword_width": (
            r"\begin{center}\begin{minipage}{0.90\textwidth}",
            r"\noindent\begin{minipage}{\textwidth}",
        ),
    }
    missing = [name for name, token in required_tokens.items() if token not in template]
    missing.extend(
        name for name, tokens in layout_alternatives.items()
        if not any(token in template for token in tokens)
    )
    forbidden = [
        name for name, token in {
            "windows_alias_fontset": "fontset=windows",
            "uncontrolled_contents": r"\tableofcontents",
        }.items() if token in template
    ]
    if template.count(required_tokens["single_structure_marker"]) != 1:
        missing.append("single_structure_marker_count")
    if missing or forbidden:
        detail = f"missing={sorted(set(missing))}; forbidden={sorted(forbidden)}"
        raise PaperRendererError(f"AUTHORITATIVE_TEMPLATE_CONTRACT_VIOLATION: {detail}")
    return {
        "status": "PASS",
        "contract_version": TEMPLATE_CONTRACT_VERSION,
        "checks": sorted([*required_tokens, *layout_alternatives]),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_content_map(root: Path, content: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for key in sorted(content):
        relative = content[key]
        path = (root / relative).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError as exc:
            raise PaperRendererError(f"TRACK_C_OUTPUT_ESCAPES_ROOT: {key}") from exc
        if not path.is_file():
            raise PaperRendererError(f"TRACK_C_OUTPUT_MISSING: {key}")
        digest.update(key.encode("utf-8"))
        digest.update(b"\0")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _resolve_inside(root: Path, raw: str, label: str) -> Path:
    path = (root / raw).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise PaperRendererError(f"{label} escapes content_root: {raw}") from exc
    if not path.is_file():
        raise PaperRendererError(f"{label} is missing: {raw}")
    return path

def _escape_figure_caption(text: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "#": r"\#",
        "$": r"\$",
        "%": r"\%",
        "&": r"\&",
        "_": r"\_",
        "^": r"\^{}",
        "~": r"\~{}",
    }
    return "".join(replacements.get(char, char) for char in text)


def _validate_figure_assets(payload: dict[str, Any], content_root: Path) -> list[dict[str, Any]]:
    track_c_pass = payload.get("track_c_pass")
    if not isinstance(track_c_pass, dict):
        return []
    raw_assets = track_c_pass.get("figure_assets", [])
    if raw_assets is None:
        return []
    if not isinstance(raw_assets, list):
        raise PaperRendererError("FIGURE_HANDOFF_INVALID")
    if raw_assets and track_c_pass.get("schema_version") != TRACK_C_SCHEMA_VERSION:
        raise PaperRendererError("FIGURE_HANDOFF_VERSION_INVALID")
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_assets, 1):
        if not isinstance(raw, dict):
            raise PaperRendererError(f"FIGURE_HANDOFF_INVALID: {index}")
        figure_id = raw.get("figure_id")
        if not isinstance(figure_id, str) or FIGURE_ID_RE.fullmatch(figure_id) is None:
            raise PaperRendererError(f"FIGURE_METADATA_INVALID: {index}")
        if figure_id in seen_ids:
            raise PaperRendererError(f"FIGURE_DUPLICATED: {figure_id}")
        seen_ids.add(figure_id)
        asset_path = raw.get("asset_path")
        asset_format = str(raw.get("asset_format") or "").lower().strip()
        source_binding = raw.get("source_binding")
        result_binding = raw.get("result_binding")
        asset_binding = raw.get("asset_binding")
        visual_qa = raw.get("visual_qa")
        caption = raw.get("caption")
        if not isinstance(caption, str) or not caption.strip():
            raise PaperRendererError(f"FIGURE_CAPTION_REQUIRED: {figure_id}")
        if not isinstance(asset_path, str) or not asset_path.strip():
            raise PaperRendererError("FIGURE_ASSET_MISSING")
        if raw.get("generation_status") != "GENERATED" or raw.get("execution_status") != "SUCCEEDED":
            raise PaperRendererError("FIGURE_GENERATION_FAILED")
        if not isinstance(source_binding, dict) or not source_binding:
            raise PaperRendererError(f"FIGURE_SOURCE_BINDING_MISSING: {figure_id}")
        if not isinstance(result_binding, dict):
            raise PaperRendererError(f"FIGURE_RESULT_BINDING_MISSING: {figure_id}")
        if (
            not isinstance(result_binding.get("result_id"), str)
            or not result_binding["result_id"].strip()
            or not isinstance(result_binding.get("result_type"), str)
            or not result_binding["result_type"].strip()
            or not isinstance(result_binding.get("result_sha256"), str)
            or SHA256_RE.fullmatch(result_binding["result_sha256"]) is None
            or result_binding.get("approved") is not True
            or result_binding.get("source_contract") != "result_acceptance_contract"
            or not isinstance(result_binding.get("result_acceptance_contract_sha256"), str)
            or SHA256_RE.fullmatch(result_binding["result_acceptance_contract_sha256"]) is None
            or not isinstance(result_binding.get("gate3_evidence_sha256"), str)
            or SHA256_RE.fullmatch(result_binding["gate3_evidence_sha256"]) is None
        ):
            if result_binding.get("approved") is not True:
                raise PaperRendererError(f"FIGURE_RESULT_NOT_APPROVED: {figure_id}")
            raise PaperRendererError(f"FIGURE_RESULT_BINDING_INVALID: {figure_id}")
        if (
            source_binding.get("source_id") != result_binding["result_id"]
            or source_binding.get("source_type") != result_binding["result_type"]
            or str(source_binding.get("sha256", "")).lower() != result_binding["result_sha256"].lower()
            or source_binding.get("approved") is not True
        ):
            raise PaperRendererError(f"FIGURE_RESULT_BINDING_MISMATCH: {figure_id}")
        if not isinstance(asset_binding, dict):
            raise PaperRendererError(f"FIGURE_ASSET_BINDING_MISSING: {figure_id}")
        if not isinstance(visual_qa, dict) or visual_qa.get("status") != "PASS":
            raise PaperRendererError(f"FIGURE_VISUAL_QA_FAILED: {figure_id}")
        try:
            paper_binding = validate_figure_paper_binding(raw)
        except FigurePaperBindingError as exc:
            raise PaperRendererError(str(exc)) from exc
        if (
            not isinstance(asset_binding.get("asset_sha256"), str)
            or SHA256_RE.fullmatch(asset_binding["asset_sha256"]) is None
            or not isinstance(asset_binding.get("generated_at"), str)
            or not asset_binding["generated_at"].strip()
            or not isinstance(asset_binding.get("generator_version"), str)
            or not asset_binding["generator_version"].strip()
            or not isinstance(asset_binding.get("spec_sha256"), str)
            or SHA256_RE.fullmatch(asset_binding["spec_sha256"]) is None
            or not isinstance(asset_binding.get("script_sha256"), str)
            or SHA256_RE.fullmatch(asset_binding["script_sha256"]) is None
        ):
            raise PaperRendererError(f"FIGURE_ASSET_BINDING_INVALID: {figure_id}")
        if not isinstance(caption, str) or not caption.strip() or any(ord(char) < 32 for char in caption):
            raise PaperRendererError(f"FIGURE_CAPTION_INVALID: {figure_id}")
        caption_tex = _escape_figure_caption(caption.strip())
        resolved = _resolve_inside(content_root, asset_path, f"figure asset {figure_id}")
        if resolved.suffix.lower() not in FIGURE_ASSET_EXTENSIONS:
            raise PaperRendererError(f"FIGURE_FORMAT_INVALID: {figure_id}")
        if asset_format != resolved.suffix.lower().lstrip(".") or resolved.stat().st_size <= 0:
            raise PaperRendererError(f"FIGURE_ASSET_INVALID: {figure_id}")
        if _sha256(resolved) != asset_binding["asset_sha256"].lower():
            raise PaperRendererError(f"FIGURE_ASSET_HASH_MISMATCH: {figure_id}")
        normalized.append(
            {
                "figure_id": figure_id,
                "asset_path": Path(asset_path).as_posix(),
                "asset_format": asset_format,
                "generation_status": "GENERATED",
                "execution_status": "SUCCEEDED",
                "source_binding": source_binding,
                "result_binding": {
                    "result_id": result_binding["result_id"],
                    "result_type": result_binding["result_type"],
                    "result_sha256": result_binding["result_sha256"].lower(),
                    "approved": True,
                    "source_contract": result_binding["source_contract"],
                    "result_acceptance_contract_sha256": result_binding["result_acceptance_contract_sha256"].lower(),
                    "gate3_evidence_sha256": result_binding["gate3_evidence_sha256"].lower(),
                },
                "asset_binding": {
                    "asset_sha256": asset_binding["asset_sha256"].lower(),
                    "generated_at": asset_binding["generated_at"],
                    "generator_version": asset_binding["generator_version"],
                    "spec_sha256": asset_binding["spec_sha256"].lower(),
                    "script_sha256": asset_binding["script_sha256"].lower(),
                },
                "visual_qa": visual_qa,
                **paper_binding,
                "task_id": raw.get("task_id"),
                "reasoning_role": raw.get("reasoning_role"),
                "placement_slot": raw.get("placement_slot", "SECTION_AFTER_DISCUSSION"),
                "caption": caption_tex,
            }
        )
    return normalized


def _validate_text(text: str, label: str) -> str:
    if not isinstance(text, str):
        raise PaperRendererError(f"content must be a string: {label}")
    for pattern, code in FORBIDDEN_CONTENT_PATTERNS:
        if pattern.search(text):
            raise PaperRendererError(f"{code}: {label}")
    for target in INPUT_RE.findall(text):
        normalized_target = target.replace("\\", "/").lstrip("./")
        if not normalized_target.startswith(("figures/", "output/figures/")):
            raise PaperRendererError(f"WRITER_INPUT_PATH_FORBIDDEN: {label}: {target}")
    return text


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PaperRendererError(f"invalid structured content: {label}") from exc
    if not isinstance(payload, dict):
        raise PaperRendererError(f"structured content must be an object: {label}")
    return payload


def _validate_question_content(payload: dict[str, Any], label: str) -> dict[str, Any]:
    intro = payload.get("intro", "")
    closing = payload.get("closing_optional", "")
    if not isinstance(intro, str) or not isinstance(closing, str):
        raise PaperRendererError(f"question intro/closing must be strings: {label}")
    sections = payload.get("sections", [])
    if not isinstance(sections, list):
        raise PaperRendererError(f"question sections must be a list: {label}")
    normalized_sections: list[dict[str, str]] = []
    for index, item in enumerate(sections, start=1):
        if not isinstance(item, dict):
            raise PaperRendererError(f"question section must be an object: {label}[{index}]")
        title = item.get("title")
        content = item.get("content")
        if not isinstance(title, str) or not title.strip():
            raise PaperRendererError(f"EMPTY_HEADING_FORBIDDEN: {label}[{index}]")
        if not isinstance(content, str) or not content.strip():
            raise PaperRendererError(f"EMPTY_HEADING_FORBIDDEN: {label}[{index}]")
        if "\\" in title:
            raise PaperRendererError(f"WRITER_HEADING_COMMAND_FORBIDDEN: {label}[{index}]")
        _validate_text(content, f"{label}[{index}].content")
        normalized_sections.append({"title": title.strip(), "content": content})
    _validate_text(intro, f"{label}.intro")
    _validate_text(closing, f"{label}.closing_optional")
    if not intro.strip() and not normalized_sections and not closing.strip():
        raise PaperRendererError(f"EMPTY_QUESTION_CONTENT: {label}")
    derivation_blocks = payload.get("derivation_blocks", [])
    if not isinstance(derivation_blocks, list):
        raise PaperRendererError(f"DERIVATION_BLOCKS_INVALID: {label}")
    normalized_blocks: list[dict[str, Any]] = []
    for block_index, block in enumerate(derivation_blocks, 1):
        if not isinstance(block, dict):
            raise PaperRendererError(f"DERIVATION_BLOCK_INVALID: {label}[{block_index}]")
        title = block.get("title")
        goal = block.get("model_goal")
        selection_basis = block.get("method_selection_basis", "")
        physical = block.get("physical_explanation")
        boundary = block.get("assumptions_and_boundary")
        if not all(isinstance(item, str) and item.strip() for item in (title, goal, physical, boundary)):
            raise PaperRendererError(f"DERIVATION_BLOCK_FIELD_MISSING: {label}[{block_index}]")
        variables = block.get("variables")
        steps = block.get("steps")
        if not isinstance(variables, list) or not variables or not isinstance(steps, list) or not steps:
            raise PaperRendererError(f"DERIVATION_BLOCK_VARIABLES_OR_STEPS_MISSING: {label}[{block_index}]")
        normalized_variables: list[dict[str, str]] = []
        for variable_index, variable in enumerate(variables, 1):
            if not isinstance(variable, dict):
                raise PaperRendererError(f"DERIVATION_VARIABLE_INVALID: {label}[{block_index}][{variable_index}]")
            symbol = variable.get("symbol")
            definition = variable.get("definition")
            if not isinstance(symbol, str) or not symbol.strip() or not isinstance(definition, str) or not definition.strip():
                raise PaperRendererError(f"DERIVATION_VARIABLE_FIELD_MISSING: {label}[{block_index}][{variable_index}]")
            normalized_variables.append({"symbol": symbol.strip(), "definition": _validate_text(definition, f"{label}.derivation.variable")})
        normalized_steps: list[dict[str, str]] = []
        for step_index, step in enumerate(steps, 1):
            if not isinstance(step, dict):
                raise PaperRendererError(f"DERIVATION_STEP_INVALID: {label}[{block_index}][{step_index}]")
            formula = step.get("formula_tex")
            explanation = step.get("explanation")
            if not isinstance(formula, str) or not formula.strip() or not isinstance(explanation, str) or not explanation.strip():
                raise PaperRendererError(f"DERIVATION_STEP_FIELD_MISSING: {label}[{block_index}][{step_index}]")
            equation_label = step.get("equation_label")
            if equation_label is not None:
                if not isinstance(equation_label, str) or not EQUATION_LABEL_RE.fullmatch(equation_label.strip()):
                    raise PaperRendererError(f"EQUATION_LABEL_INVALID: {label}[{block_index}][{step_index}]")
                equation_label = equation_label.strip()
            else:
                equation_label = ""
            normalized_steps.append({"formula_tex": _validate_text(formula, f"{label}.derivation.formula"), "explanation": _validate_text(explanation, f"{label}.derivation.explanation"), "equation_label": equation_label})
        normalized_blocks.append({
            "title": title.strip(), "model_goal": _validate_text(goal, f"{label}.derivation.goal"),
            "method_selection_basis": _validate_text(selection_basis, f"{label}.derivation.method_selection_basis") if isinstance(selection_basis, str) and selection_basis.strip() else "",
            "variables": normalized_variables, "steps": normalized_steps,
            "physical_explanation": _validate_text(physical, f"{label}.derivation.physical"),
            "assumptions_and_boundary": _validate_text(boundary, f"{label}.derivation.boundary"),
        })
    return {"intro": intro, "sections": normalized_sections, "closing_optional": closing, "derivation_blocks": normalized_blocks}


def _validate_equation_labels(structured: dict[str, Any]) -> None:
    seen: dict[str, str] = {}
    for content_key, question in structured.items():
        if not isinstance(question, dict):
            continue
        for block_index, block in enumerate(question.get("derivation_blocks", []), 1):
            for step_index, step in enumerate(block.get("steps", []), 1):
                equation_label = step.get("equation_label")
                if not equation_label:
                    continue
                location = f"{content_key}.derivation_blocks[{block_index}].steps[{step_index}]"
                if equation_label in seen:
                    raise PaperRendererError(f"DUPLICATE_EQUATION_LABEL: {equation_label}: {seen[equation_label]} and {location}")
                seen[equation_label] = location


def _validate_model_evaluation(payload: dict[str, Any], label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in ("strengths", "weaknesses", "improvements"):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise PaperRendererError(f"EMPTY_EVALUATION_SECTION_FORBIDDEN: {label}.{key}")
        result[key] = _validate_text(value, f"{label}.{key}")
    return result


def _read_manifest(
    path: Path, *, formal: bool = False
) -> tuple[dict[str, Any], Path, dict[str, str], dict[str, Any], dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PaperRendererError(f"invalid content manifest: {path}") from exc
    if not isinstance(payload, dict):
        raise PaperRendererError("content manifest must be a JSON object")
    try:
        task_contract = resolve_task_contract(payload)
    except WriterSchemaError as exc:
        raise PaperRendererError(str(exc)) from exc
    tasks = task_contract["tasks"]
    root_raw = payload.get("content_root", ".")
    if not isinstance(root_raw, str):
        raise PaperRendererError("content_root must be a string")
    content_root = (path.parent / root_raw).resolve()
    if not content_root.is_dir():
        raise PaperRendererError(f"content_root is missing: {content_root}")
    raw_content = payload.get("content")
    if not isinstance(raw_content, dict):
        raise PaperRendererError("content must be an object mapping keys to relative files")
    if formal:
        _validate_formal_schema(payload, {str(key): str(value) for key, value in raw_content.items()}, tasks)

    required = set(REQUIRED_BASE_KEYS)
    for task in tasks:
        required.add(task["content_key"])
        if task_contract["migration_mode"] == "EXPLICIT_TASK_CONTRACT":
            required.add(task["analysis_content_key"])
    missing = sorted(key for key in required if key not in raw_content)
    if missing:
        raise PaperRendererError("missing content keys: " + ", ".join(missing))

    resolved: dict[str, str] = {}
    structured: dict[str, Any] = {}
    structured_task_keys = {task["content_key"] for task in tasks}
    for key, raw in raw_content.items():
        if not isinstance(key, str) or not CONTENT_NAME_RE.fullmatch(key):
            raise PaperRendererError(f"invalid content key: {key!r}")
        if not isinstance(raw, str):
            raise PaperRendererError(f"content path must be a string: {key}")
        source = _resolve_inside(content_root, raw, key)
        if key == "model_evaluation":
            structured[key] = _validate_model_evaluation(_read_json(source, key), key)
        elif key in structured_task_keys:
            structured[key] = _validate_question_content(_read_json(source, key), key)
        else:
            _validate_text(source.read_text(encoding="utf-8"), key)
        resolved[key] = raw
    _validate_equation_labels(structured)
    return payload, content_root, resolved, structured, task_contract


def _validate_formal_schema(payload: dict[str, Any], content: dict[str, str], tasks: list[dict[str, str]]) -> None:
    legacy = sorted(
        key for key in content if any(key == legacy_key or key.startswith(legacy_key + "_") for legacy_key in LEGACY_WRITER_KEYS)
    )
    v2_present = set(REQUIRED_BASE_KEYS).issubset(content) and all(task["content_key"] in content for task in tasks)
    if legacy and v2_present:
        raise PaperRendererError("MIXED_WRITER_SCHEMA_FORBIDDEN")
    if legacy:
        raise PaperRendererError("LEGACY_WRITER_SCHEMA_NOT_ALLOWED_FOR_FORMAL_BUILD")
    if payload.get("writer_schema_version") != WRITER_SCHEMA_VERSION:
        raise PaperRendererError("WRITER_SCHEMA_V2_REQUIRED")
    if not v2_present:
        raise PaperRendererError("WRITER_SCHEMA_V2_REQUIRED")
    if payload.get("manifest_mode") == "CURRENT_FORMAL":
        policy = payload.get("writing_policy")
        if not isinstance(policy, dict) or policy.get("version") not in FORMAL_POLICY_VERSIONS:
            raise PaperRendererError("FORMAL_WRITING_POLICY_VERSION_REQUIRED")


def _read_formal_receipt(
    *,
    payload: dict[str, Any],
    content_root: Path,
    manifest_path: Path,
    receipt_path: Path,
    expected_case_id: str,
) -> dict[str, Any]:
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PaperRendererError("TRACK_C_RECEIPT_INVALID") from exc
    if not isinstance(receipt, dict):
        raise PaperRendererError("TRACK_C_RECEIPT_INVALID")
    required = {
        "receipt_version",
        "case_id",
        "writer_schema_version",
        "track_c_input_sha256",
        "track_c_output_sha256",
        "track_c_rules_version",
        "track_c_status",
        "created_by_actual_track_c_runtime",
    }
    if not required.issubset(receipt):
        raise PaperRendererError("TRACK_C_RECEIPT_INVALID")
    if receipt.get("case_id") != expected_case_id:
        raise PaperRendererError("TRACK_C_RECEIPT_INVALID")
    if receipt.get("writer_schema_version") != WRITER_SCHEMA_VERSION:
        raise PaperRendererError("TRACK_C_RECEIPT_INVALID")
    if (
        receipt.get("schema_version") != TRACK_C_SCHEMA_VERSION
        or receipt.get("receipt_version") != TRACK_C_RECEIPT_VERSION
    ):
        raise PaperRendererError("TRACK_C_RECEIPT_INVALID")
    if receipt.get("track_c_status") != "PASS" or receipt.get("created_by_actual_track_c_runtime") is not True:
        raise PaperRendererError("TRACK_C_RECEIPT_INVALID")
    if not str(receipt.get("track_c_rules_version", "")).strip():
        raise PaperRendererError("TRACK_C_RECEIPT_INVALID")
    output_artifact = receipt.get("output_artifact")
    renderer_input = receipt.get("renderer_input")
    if not isinstance(output_artifact, str) or not isinstance(renderer_input, str):
        raise PaperRendererError("TRACK_C_RECEIPT_INVALID")
    if Path(output_artifact).resolve() != manifest_path or Path(renderer_input).resolve() != manifest_path:
        raise PaperRendererError("TRACK_C_RECEIPT_INVALID")
    input_artifact = receipt.get("input_artifact")
    if not isinstance(input_artifact, str) or not Path(input_artifact).is_file():
        raise PaperRendererError("TRACK_C_RECEIPT_INVALID")
    expected_input_artifact = Path(input_artifact).resolve()
    allowed_input_parents = {manifest_path.parent.resolve()}
    # Track C keeps its output manifest under paper/track_c while the
    # original writer manifest remains in paper/.  This is a path-layout
    # compatibility rule only; the hash and filename checks remain strict.
    if manifest_path.parent.name == "track_c":
        allowed_input_parents.add(manifest_path.parent.parent.resolve())
    if (
        expected_input_artifact.parent not in allowed_input_parents
        or expected_input_artifact.name not in {"writer_manifest.json", "writer_manifest_with_figures.json"}
    ):
        raise PaperRendererError("TRACK_C_RECEIPT_INVALID")
    if receipt.get("track_c_input_sha256") != _sha256(expected_input_artifact):
        raise PaperRendererError("TRACK_C_RECEIPT_INVALID")
    if receipt.get("track_c_output_manifest_sha256") != _sha256(manifest_path):
        raise PaperRendererError("TRACK_C_RECEIPT_INVALID")
    if receipt.get("track_c_output_sha256") != _sha256_content_map(content_root, payload["content"]):
        raise PaperRendererError("TRACK_C_RECEIPT_INVALID")
    track_c_pass = payload.get("track_c_pass")
    if not isinstance(track_c_pass, dict) or track_c_pass.get("status") != "PASS":
        raise PaperRendererError("TRACK_C_RECEIPT_INVALID")
    return receipt


def _structure_body(
    tasks: list[dict[str, str]],
    structured: dict[str, Any],
    content: dict[str, str],
    figure_assets: list[dict[str, Any]],
) -> str:
    section_counts: dict[str, int] = {}
    for task in tasks:
        for section in structured[task["content_key"]]["sections"]:
            title = section["title"]
            section_counts[title] = section_counts.get(title, 0) + 1
    figures_by_section: dict[str, list[dict[str, Any]]] = {}
    opening_figures_by_task: dict[str, list[dict[str, Any]]] = {}
    for figure in figure_assets:
        if figure.get("placement_slot") == "TASK_MODEL_ENTRY":
            task_id = str(figure.get("task_id") or "").strip()
            if not task_id:
                raise PaperRendererError(f"FIGURE_TASK_ID_REQUIRED: {figure['figure_id']}")
            opening_figures_by_task.setdefault(task_id, []).append(figure)
            continue
        section = figure["first_discussion"]
        if section_counts.get(section) != 1:
            raise PaperRendererError(f"FIGURE_FIRST_DISCUSSION_SECTION_NOT_UNIQUE: {figure['figure_id']}")
        figures_by_section.setdefault(section, []).append(figure)

    def append_bound_figures(lines: list[str], section: str) -> None:
        for figure in figures_by_section.get(section, []):
            lines.append(
                rf"\PaperFigure{{writer_content/{figure['asset_path']}}}{{{figure['caption']}}}{{fig:{figure['figure_id']}}}"
            )

    def append_opening_figures(lines: list[str], task_id: str) -> None:
        for figure in opening_figures_by_task.get(task_id, []):
            lines.append(
                rf"\PaperFigure{{writer_content/{figure['asset_path']}}}{{{figure['caption']}}}{{fig:{figure['figure_id']}}}"
            )

    lines = [
        r"\section{问题重述}",
        r"\PaperContent{problem_restatement}",
        r"",
        r"\Needspace{10\baselineskip}",
        r"\section{问题分析}",
    ]
    for task in tasks:
        if task["analysis_content_key"] in content:
            lines.extend([rf"\subsection{{{task['label']}的分析}}", rf"\PaperContent{{{task['analysis_content_key']}}}"])
    lines.extend(
        [
            r"",
            r"\section{模型假设}",
            r"\begin{PaperAssumptions}",
            r"\PaperContent{model_assumptions}",
            r"\end{PaperAssumptions}",
            r"",
            r"\Needspace{14\baselineskip}",
            r"\section{符号说明}",
            r"\PaperContent{notation}",
            r"",
            r"\section{模型的建立与求解}",
        ]
    )
    for task in tasks:
        key = task["content_key"]
        question = structured[key]
        lines.append(rf"\subsection{{{task['label']}}}")
        if question["intro"].strip():
            lines.append(rf"\PaperContent{{{key}_intro}}")
        append_opening_figures(lines, task["task_id"])
        for block_index, block in enumerate(question["derivation_blocks"], 1):
            prefix = f"{key}_derivation_{block_index}"
            lines.append(rf"\subsubsection{{{block['title']}}}")
            if block["method_selection_basis"]:
                lines.append(rf"\PaperContent{{{prefix}_method_selection_basis}}")
            for step_index, _step in enumerate(block["steps"], 1):
                step = block["steps"][step_index - 1]
                # Writer explanations motivate a key relation before the
                # displayed equation. Variable records stay in the schema and
                # evidence layer instead of being dumped into public prose.
                lines.append(rf"\PaperContent{{{prefix}_step_{step_index}_explanation}}")
                formula_lines = [rf"\PaperContent{{{prefix}_step_{step_index}_formula}}"]
                if step["equation_label"]:
                    lines.extend([r"\begin{equation}", *formula_lines, rf"\label{{{step['equation_label']}}}", r"\end{equation}"])
                else:
                    lines.extend([r"\[", *formula_lines, r"\]"])
            lines.append(rf"\PaperContent{{{prefix}_physical}}")
        for section_index, section in enumerate(question["sections"], start=1):
            lines.extend(
                [
                    rf"\subsubsection{{{section['title']}}}",
                    rf"\PaperContent{{{key}_section_{section_index}}}",
                ]
            )
            append_bound_figures(lines, section["title"])
        for block_index, _block in enumerate(question["derivation_blocks"], 1):
            prefix = f"{key}_derivation_{block_index}"
            lines.append(rf"\PaperContent{{{prefix}_boundary}}")
        if question["closing_optional"].strip():
            lines.append(rf"\PaperContent{{{key}_closing}}")
    lines.extend(
        [
            r"",
            r"\section{模型的评价、改进与推广}",
            r"\subsection{模型优点}",
            r"\PaperContent{model_evaluation_strengths}",
            r"\subsection{模型缺点}",
            r"\PaperContent{model_evaluation_weaknesses}",
            r"\subsection{模型的改进}",
            r"\PaperContent{model_evaluation_improvements}",
        ]
    )
    if "ai_usage_statement" in content:
        lines.extend(
            [
                r"",
                r"\section{AI 工具使用说明}",
                r"\PaperContent{ai_usage_statement}",
            ]
        )
    lines.extend(
        [
            r"",
            r"\section{参考文献}",
            r"\begin{PaperReferences}{9}",
            r"\PaperContent{references}",
            r"\end{PaperReferences}",
            r"",
            r"\FloatBarrier",
            r"\clearpage",
            r"\appendix",
            r"\section*{附录}",
            r"\setcounter{subsection}{0}",
            r"\renewcommand{\thesubsection}{A.\arabic{subsection}}",
            r"\begin{PaperAppendix}",
            r"\PaperContent{appendix}",
            r"\end{PaperAppendix}",
        ]
    )
    return "\n".join(lines)


def _render_main(
    template: str,
    tasks: list[dict[str, str]],
    structured: dict[str, Any],
    content: dict[str, str],
    figure_assets: list[dict[str, Any]],
) -> str:
    marker = "% PAPER_RENDERER:STRUCTURE_BODY"
    if template.count(marker) != 1:
        raise PaperRendererError("authoritative template must contain exactly one structure marker")
    return template.replace(marker, _structure_body(tasks, structured, content, figure_assets))


def _write_structured_fragments(writer_dir: Path, structured: dict[str, Any], tasks: list[dict[str, str]]) -> None:
    for key, question in structured.items():
        if key == "model_evaluation":
            for field, text in question.items():
                (writer_dir / f"model_evaluation_{field}.tex").write_text(text.strip() + "\n", encoding="utf-8")
            continue
        if key not in {task["content_key"] for task in tasks}:
            continue
        stem = key
        intro = question["intro"]
        closing = question["closing_optional"]
        if intro.strip():
            (writer_dir / f"{stem}_intro.tex").write_text(intro.strip() + "\n", encoding="utf-8")
        if closing.strip():
            (writer_dir / f"{stem}_closing.tex").write_text(closing.strip() + "\n", encoding="utf-8")
        for section_index, section in enumerate(question["sections"], start=1):
            section_stem = f"{stem}_section_{section_index}"
            (writer_dir / f"{section_stem}.tex").write_text(section["content"].strip() + "\n", encoding="utf-8")
        for block_index, block in enumerate(question["derivation_blocks"], 1):
            prefix = f"{stem}_derivation_{block_index}"
            (writer_dir / f"{prefix}_goal.tex").write_text(block["model_goal"].strip() + "\n", encoding="utf-8")
            if block["method_selection_basis"]:
                (writer_dir / f"{prefix}_method_selection_basis.tex").write_text(block["method_selection_basis"].strip() + "\n", encoding="utf-8")
            for variable_index, variable in enumerate(block["variables"], 1):
                (writer_dir / f"{prefix}_variable_{variable_index}_symbol.tex").write_text(variable["symbol"].strip() + "\n", encoding="utf-8")
                (writer_dir / f"{prefix}_variable_{variable_index}_definition.tex").write_text(variable["definition"].strip() + "\n", encoding="utf-8")
            for step_index, step in enumerate(block["steps"], 1):
                (writer_dir / f"{prefix}_step_{step_index}_formula.tex").write_text(step["formula_tex"].strip() + "\n", encoding="utf-8")
                (writer_dir / f"{prefix}_step_{step_index}_explanation.tex").write_text(step["explanation"].strip() + "\n", encoding="utf-8")
            (writer_dir / f"{prefix}_physical.tex").write_text(block["physical_explanation"].strip() + "\n", encoding="utf-8")
            (writer_dir / f"{prefix}_boundary.tex").write_text(block["assumptions_and_boundary"].strip() + "\n", encoding="utf-8")


def render_paper(
    manifest_path: str | Path,
    output_dir: str | Path,
    *,
    formal: bool = False,
    receipt_path: str | Path | None = None,
    expected_case_id: str | None = None,
) -> dict[str, Any]:
    """Render one paper directory and return a deterministic render record."""

    manifest_path = Path(manifest_path).resolve()
    output_dir = Path(output_dir).resolve()
    payload, content_root, content, structured, task_contract = _read_manifest(manifest_path, formal=formal)
    try:
        writing_policy = validate_writing_policy(payload, content_root, content)
    except WritingPolicyError as exc:
        raise PaperRendererError(str(exc)) from exc
    if not TEMPLATE_PATH.is_file():
        raise PaperRendererError(f"authoritative template is missing: {TEMPLATE_PATH}")
    template_text = TEMPLATE_PATH.read_text(encoding="utf-8")
    template_contract = validate_authoritative_template_contract(template_text)
    receipt: dict[str, Any] | None = None
    if formal:
        if not receipt_path or not expected_case_id:
            raise PaperRendererError("TRACK_C_RECEIPT_INVALID")
        receipt = _read_formal_receipt(
            payload=payload,
            content_root=content_root,
            manifest_path=manifest_path,
            receipt_path=Path(receipt_path).resolve(),
            expected_case_id=expected_case_id,
        )
    figure_assets = _validate_figure_assets(payload, content_root)

    output_dir.mkdir(parents=True, exist_ok=True)
    writer_content_dir = "writer_content"
    default_writer_dir = (output_dir / writer_content_dir).resolve()
    source_paths = [
        _resolve_inside(content_root, relative, f"content source {key}")
        for key, relative in content.items()
    ]
    source_paths.extend(
        _resolve_inside(content_root, figure["asset_path"], f"figure asset {figure['figure_id']}")
        for figure in figure_assets
    )
    source_tree_overlap = any(
        path == default_writer_dir or default_writer_dir in path.parents
        for path in source_paths
    )
    existing_writer_json = any(default_writer_dir.glob("*.json"))
    if content_root == default_writer_dir or source_tree_overlap or existing_writer_json:
        # Never overwrite a manifest's source tree with generated TeX.  This
        # is especially important for structured JSON content, which must
        # remain JSON for a later formal render.  Check every declared source,
        # as well as any existing writer JSON, rather than relying on a
        # task-name convention.
        writer_content_dir = "rendered_writer_content"
    writer_dir = output_dir / writer_content_dir
    writer_dir.mkdir(parents=True, exist_ok=True)
    for key, relative in content.items():
        shutil.copyfile(content_root / relative, writer_dir / Path(relative).name)
    for figure in figure_assets:
        source = _resolve_inside(content_root, figure["asset_path"], f"figure asset {figure['figure_id']}")
        target = writer_dir / figure["asset_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    _write_structured_fragments(writer_dir, structured, task_contract["tasks"])

    main_path = output_dir / "main.tex"
    main_path.write_text(
        _render_main(
            template_text,
            task_contract["tasks"],
            structured,
            content,
            figure_assets,
        ),
        encoding="utf-8",
    )
    visual_reasoning_report = {
        "schema_version": "visual_reasoning_trace_report_v1",
        "generated_by": "paper_renderer",
        "status": writing_policy.get("visual_reasoning", {}).get("status", "NOT_APPLICABLE"),
        "policy_version": writing_policy.get("policy_version"),
        "manifest_mode": writing_policy.get("manifest_mode"),
        "task_contract": task_contract,
        "visual_reasoning": writing_policy.get("visual_reasoning", {}),
        "main_tex_sha256": _sha256(main_path),
        "figure_assets": [
            {
                "figure_id": figure["figure_id"],
                "path": (Path("writer_content") / figure["asset_path"]).as_posix(),
                "sha256": figure["asset_binding"]["asset_sha256"],
            }
            for figure in figure_assets
        ],
    }
    visual_reasoning_report_path = output_dir / "visual_reasoning_trace_report.json"
    visual_reasoning_report_path.write_text(
        json.dumps(visual_reasoning_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    argument_blueprint_report_path: Path | None = None
    argument_blueprint_report = writing_policy.get("argument_blueprint", {})
    if writing_policy.get("policy_version") == WRITING_POLICY_VERSION:
        try:
            source_manifest_relative = manifest_path.relative_to(output_dir.parent).as_posix()
        except ValueError as exc:
            raise PaperRendererError("ARGUMENT_BLUEPRINT_SOURCE_MANIFEST_OUTSIDE_CASE") from exc
        argument_blueprint_trace = {
            "schema_version": TRACE_VERSION,
            "generated_by": "paper_renderer",
            "status": argument_blueprint_report.get("status", "NOT_APPLICABLE"),
            "policy_version": writing_policy.get("policy_version"),
            "manifest_mode": writing_policy.get("manifest_mode"),
            "task_contract": task_contract,
            "argument_blueprint": argument_blueprint_report,
            "main_tex_sha256": _sha256(main_path),
            "source_manifest": {
                "path": source_manifest_relative,
                "sha256": _sha256(manifest_path),
            },
        }
        argument_blueprint_report_path = output_dir / "argument_blueprint_trace_report.json"
        argument_blueprint_report_path.write_text(
            json.dumps(argument_blueprint_trace, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    try:
        manifest_identity = manifest_path.relative_to(ROOT).as_posix()
    except ValueError:
        manifest_identity = str(manifest_path)
    record = {
        "schema_version": SCHEMA_VERSION,
        "template_path": TEMPLATE_PATH.relative_to(ROOT).as_posix(),
        "template_sha256": _sha256(TEMPLATE_PATH),
        "template_contract": template_contract,
        "question_count": len(task_contract["tasks"]),
        "task_contract": task_contract,
        "content_keys": sorted(content),
        "figure_assets": figure_assets,
        "figure_count": len(figure_assets),
        "question_content_contract": "intro_sections_closing_optional",
        "mandatory_question_summary": False,
        "fixed_four_block_schema": False,
        "main_tex": main_path.relative_to(output_dir).as_posix(),
        "writer_content_dir": writer_dir.relative_to(output_dir).as_posix(),
        "input_manifest": manifest_identity,
        "track_c_output_manifest": payload.get("track_c_pass", {}).get("status") == "PASS",
        "track_c_runtime_consumed": receipt is not None,
        "formal_mode": formal,
        "writer_schema_version": payload.get("writer_schema_version"),
        "case_id": payload.get("case_id"),
        "track_c_receipt_path": str(Path(receipt_path).resolve()) if receipt_path else None,
        "track_c_receipt_sha256": _sha256(Path(receipt_path).resolve()) if receipt_path else None,
        "gpt_format_reminder_required": False,
        "template_enforced_structure": True,
        "template_enforced_typography": True,
        "table_of_contents": False,
        "model_changed": False,
        "result_changed": False,
        "claim_changed": False,
        "writing_policy": writing_policy,
        "visual_reasoning_trace_report": visual_reasoning_report_path.relative_to(output_dir).as_posix(),
        "visual_reasoning_trace_report_sha256": _sha256(visual_reasoning_report_path),
        "argument_blueprint_trace_report": (
            argument_blueprint_report_path.relative_to(output_dir).as_posix()
            if argument_blueprint_report_path is not None
            else None
        ),
        "argument_blueprint_trace_report_sha256": (
            _sha256(argument_blueprint_report_path)
            if argument_blueprint_report_path is not None
            else None
        ),
        "appendix_code_policy": writing_policy.get("appendix_code_policy", "PENDING_USER_POLICY"),
    }
    (output_dir / "render_manifest.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a competition paper with the fixed authoritative TeX template")
    parser.add_argument("--content-manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    try:
        record = render_paper(args.content_manifest, args.output_dir)
    except PaperRendererError as exc:
        print(f"BLOCKED / {exc}")
        return 2
    print(json.dumps(record, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
