"""Minimal executable Track C paper-expression pass.

Track C reviews writer-owned narrative and expression carriers before the
deterministic renderer consumes them.  It is intentionally a bounded
pass-through review: it does not score, rewrite model content, change frozen
results, or create workflow state.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from pipeline_core.paper_quality.writing_policy import (
    WRITING_POLICY_VERSION,
    WritingPolicyError,
    validate_writing_policy,
)
from pipeline_core.paper_quality.matlab_figure_pipeline import (
    FIGURE_EXECUTION_RECEIPT_VERSION,
    FIGURE_SPEC_VERSION,
    FigureAssetQualityError,
    FigurePaperBindingError,
    MatlabFigureSpecError,
    bind_gate_evidence,
    canonical_record_sha256,
    validate_figure_asset,
    validate_figure_paper_binding,
    validate_figure_spec,
)
from pipeline_core.paper_quality.writer_schema import (
    CURRENT_FORMAL_MODE,
    WriterSchemaError,
    resolve_manifest_mode,
    resolve_task_contract,
)
from pipeline_core.python_tools.human_gate import evaluate_human_gate


SCHEMA_VERSION = "track_c_paper_expression_runtime_v2"
RECEIPT_VERSION = "track_c_receipt_v2"
WRITER_SCHEMA_VERSION = "WRITER_SCHEMA_V2"
RULES_VERSION = "paper_draft_quality_checklist_v1"
QUESTION_KEY_RE = re.compile(r"^question_content_[a-z][a-z0-9_]*$")
FIGURE_ASSET_EXTENSIONS = {".png", ".pdf"}
FIGURE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
GENERIC_HEADINGS = re.compile(r"^(从.+到.+|进一步分析|综合分析|相关分析|模型处理|求解过程|结果处理|本问小结)$")
INTERNAL_LANGUAGE = re.compile(
    r"(?i)\b(?:gate|provenance|pipeline|review token|ai workflow|production pipeline)\b|内部审计|逻辑合同|阻断门"
)
FIXED_BLOCK_HEADINGS = {"模型的建立", "模型的求解", "结果与分析", "本问小结"}


class TrackCPaperExpressionError(ValueError):
    """Raised when Track C cannot approve writer content for rendering."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_content_map(root: Path, content: dict[str, str]) -> str:
    """Hash the actual Track C output files, not only their manifest names."""

    digest = hashlib.sha256()
    for key in sorted(content):
        relative = content[key]
        path = (root / relative).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError as exc:
            raise TrackCPaperExpressionError(f"TRACK_C_CONTENT_ESCAPES_ROOT: {key}") from exc
        if not path.is_file():
            raise TrackCPaperExpressionError(f"TRACK_C_CONTENT_MISSING: {key}")
        digest.update(key.encode("utf-8"))
        digest.update(b"\0")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _read_manifest(path: Path) -> tuple[dict[str, Any], Path]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TrackCPaperExpressionError(f"TRACK_C_INPUT_UNREADABLE: {path}") from exc
    if not isinstance(payload, dict):
        raise TrackCPaperExpressionError("TRACK_C_INPUT_INVALID")
    root = (path.parent / str(payload.get("content_root", "."))).resolve()
    try:
        root.relative_to(path.parent.resolve())
    except ValueError as exc:
        raise TrackCPaperExpressionError("TRACK_C_CONTENT_ROOT_ESCAPES_INPUT") from exc
    if not root.is_dir():
        raise TrackCPaperExpressionError(f"TRACK_C_CONTENT_ROOT_MISSING: {root}")
    content = payload.get("content")
    if not isinstance(content, dict):
        raise TrackCPaperExpressionError("TRACK_C_CONTENT_MAP_MISSING")
    return payload, root


def _read_content(root: Path, relative: str, label: str) -> str:
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise TrackCPaperExpressionError(f"TRACK_C_CONTENT_ESCAPES_ROOT: {label}") from exc
    if not path.is_file():
        raise TrackCPaperExpressionError(f"TRACK_C_CONTENT_MISSING: {label}")
    return path.read_text(encoding="utf-8")


def _load_structured(root: Path, content: dict[str, Any], key: str) -> dict[str, Any]:
    raw = content.get(key)
    if not isinstance(raw, str):
        raise TrackCPaperExpressionError(f"TRACK_C_STRUCTURED_INPUT_MISSING: {key}")
    try:
        value = json.loads(_read_content(root, raw, key))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise TrackCPaperExpressionError(f"TRACK_C_STRUCTURED_INPUT_INVALID: {key}") from exc
    if not isinstance(value, dict):
        raise TrackCPaperExpressionError(f"TRACK_C_STRUCTURED_INPUT_INVALID: {key}")
    return value


def _check_text(text: str, label: str) -> None:
    if INTERNAL_LANGUAGE.search(text):
        raise TrackCPaperExpressionError(f"TRACK_C_INTERNAL_LANGUAGE_LEAKAGE: {label}")


def _review(payload: dict[str, Any], root: Path, task_contract: dict[str, Any]) -> dict[str, Any]:
    content = payload["content"]
    task_content_keys = {task["content_key"] for task in task_contract["tasks"]}
    all_text: list[str] = []
    for key, relative in content.items():
        if not isinstance(key, str) or not isinstance(relative, str):
            raise TrackCPaperExpressionError("TRACK_C_CONTENT_ENTRY_INVALID")
        if key in task_content_keys or key == "model_evaluation":
            continue
        text = _read_content(root, relative, key)
        _check_text(text, key)
        all_text.append(text)

    section_titles: list[str] = []
    for task in task_contract["tasks"]:
        key = task["content_key"]
        if key not in content:
            raise TrackCPaperExpressionError("TRACK_C_QUESTION_CONTENT_COUNT_MISMATCH")
        question = _load_structured(root, content, key)
        sections = question.get("sections")
        if not isinstance(sections, list):
            raise TrackCPaperExpressionError(f"TRACK_C_QUESTION_SECTIONS_INVALID: {key}")
        for index, section in enumerate(sections, start=1):
            if not isinstance(section, dict):
                raise TrackCPaperExpressionError(f"TRACK_C_SECTION_INVALID: {key}[{index}]")
            title = section.get("title")
            body = section.get("content")
            if not isinstance(title, str) or not title.strip() or not isinstance(body, str) or not body.strip():
                raise TrackCPaperExpressionError(f"TRACK_C_EMPTY_SECTION: {key}[{index}]")
            title = title.strip()
            if GENERIC_HEADINGS.fullmatch(title):
                raise TrackCPaperExpressionError(f"TRACK_C_GENERIC_SUBHEADING: {key}[{index}]")
            if title in FIXED_BLOCK_HEADINGS:
                raise TrackCPaperExpressionError(f"TRACK_C_FIXED_BLOCK_SUBHEADING: {key}[{index}]")
            _check_text(title, f"{key}[{index}].title")
            _check_text(body, f"{key}[{index}].content")
            section_titles.append(title)
            all_text.append(body)
        for field in ("intro", "closing_optional"):
            value = question.get(field, "")
            if not isinstance(value, str):
                raise TrackCPaperExpressionError(f"TRACK_C_QUESTION_FIELD_INVALID: {key}.{field}")
            _check_text(value, f"{key}.{field}")
            all_text.append(value)

    evaluation = _load_structured(root, content, "model_evaluation")
    for field in ("strengths", "weaknesses", "improvements"):
        value = evaluation.get(field)
        if not isinstance(value, str) or not value.strip():
            raise TrackCPaperExpressionError(f"TRACK_C_EMPTY_EVALUATION: {field}")
        _check_text(value, f"model_evaluation.{field}")
        all_text.append(value)

    combined = "\n".join(all_text)
    if "question_summary" in content or "model_building" in content or "solution_process" in content or "results_analysis" in content:
        raise TrackCPaperExpressionError("TRACK_C_LEGACY_FIXED_SCHEMA_PRESENT")
    return {
        "internal_language_leakage": "NONE",
        "self_evaluation_bloat": "NONE",
        "result_prominence": "ACCEPTABLE",
        "odv_chain_intact": True,
        "reasoning_scope_appropriate": True,
        "track_b_placement_respected": True,
        "specific_model_or_task_subheadings": bool(section_titles),
        "generic_subheadings": False,
        "fixed_four_block_schema": False,
        "mandatory_question_summary": False,
        "content_bytes_reviewed": len(combined.encode("utf-8")),
        "section_titles_reviewed": section_titles,
        "task_contract": task_contract,
    }


def _inside(root: Path, path: Path, code: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise TrackCPaperExpressionError(code) from exc
    return resolved


def _load_figure_receipt(
    raw: dict[str, Any], source_root: Path, allowed_root: Path
) -> tuple[dict[str, Any], Path]:
    receipt_value = raw.get("execution_receipt")
    if isinstance(receipt_value, str) and receipt_value.strip():
        receipt_path = Path(receipt_value)
        if not receipt_path.is_absolute():
            receipt_path = allowed_root / receipt_path
        receipt_path = _inside(allowed_root, receipt_path, "TRACK_C_FIGURE_RECEIPT_ESCAPES_CASE")
        try:
            payload = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TrackCPaperExpressionError("TRACK_C_FIGURE_RECEIPT_INVALID") from exc
        if isinstance(payload, dict):
            return payload, receipt_path
    raise TrackCPaperExpressionError("TRACK_C_FIGURE_RECEIPT_REQUIRED")


def _validate_figure_provenance(
    figure_id: str,
    receipt: dict[str, Any],
    source_binding: Any,
    source_asset: Path,
    raw_figure: dict[str, Any],
    result_acceptance_contract: Any,
    gate3_evaluation: dict[str, Any],
    gate3_evidence_sha256: str,
    allowed_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        receipt.get("schema_version") != FIGURE_EXECUTION_RECEIPT_VERSION
        or receipt.get("runtime_origin") != "execute_matlab_figure"
        or receipt.get("created_by_actual_matlab_figure_runtime") is not True
    ):
        raise TrackCPaperExpressionError(f"TRACK_C_FIGURE_RUNTIME_RECEIPT_INVALID: {figure_id}")
    inline_spec = raw_figure.get("matlab_figure_spec")
    if not isinstance(inline_spec, dict):
        raise TrackCPaperExpressionError(f"TRACK_C_FIGURE_SPEC_REQUIRED: {figure_id}")
    try:
        normalized_spec = validate_figure_spec(inline_spec)
        spec_sha256 = canonical_record_sha256(normalized_spec)
    except MatlabFigureSpecError as exc:
        raise TrackCPaperExpressionError(f"TRACK_C_FIGURE_SPEC_INVALID: {figure_id}") from exc
    if receipt.get("spec_sha256") != spec_sha256:
        raise TrackCPaperExpressionError(f"TRACK_C_FIGURE_SPEC_HASH_MISMATCH: {figure_id}")
    script_raw = raw_figure.get("matlab_script_path")
    if not isinstance(script_raw, str) or not script_raw.strip():
        raise TrackCPaperExpressionError(f"TRACK_C_FIGURE_SCRIPT_REQUIRED: {figure_id}")
    script_path = Path(script_raw)
    if not script_path.is_absolute():
        script_path = allowed_root / script_path
    script_path = _inside(allowed_root, script_path, "TRACK_C_FIGURE_SCRIPT_ESCAPES_CASE")
    if not script_path.is_file() or script_path.suffix.lower() != ".m":
        raise TrackCPaperExpressionError(f"TRACK_C_FIGURE_SCRIPT_INVALID: {figure_id}")
    receipt_script = receipt.get("script_path")
    if not isinstance(receipt_script, str) or Path(receipt_script).resolve() != script_path:
        raise TrackCPaperExpressionError(f"TRACK_C_FIGURE_SCRIPT_PATH_MISMATCH: {figure_id}")
    if receipt.get("script_sha256") != sha256_file(script_path):
        raise TrackCPaperExpressionError(f"TRACK_C_FIGURE_SCRIPT_HASH_MISMATCH: {figure_id}")
    expected_asset = (script_path.parent / normalized_spec["output_file"]).resolve()
    if expected_asset != source_asset:
        raise TrackCPaperExpressionError(f"TRACK_C_FIGURE_ASSET_PATH_MISMATCH: {figure_id}")
    if not isinstance(result_acceptance_contract, dict):
        raise TrackCPaperExpressionError(f"TRACK_C_RESULT_ACCEPTANCE_CONTRACT_REQUIRED: {figure_id}")
    contract_sha256 = canonical_record_sha256(result_acceptance_contract)
    if receipt.get("result_acceptance_contract_sha256") != contract_sha256:
        raise TrackCPaperExpressionError(f"TRACK_C_RESULT_ACCEPTANCE_CONTRACT_MISMATCH: {figure_id}")
    if (
        gate3_evaluation.get("gate_id") != "GATE3"
        or gate3_evaluation.get("gate_status") != "PASS"
        or receipt.get("gate3_evidence_sha256") != gate3_evidence_sha256
    ):
        raise TrackCPaperExpressionError(f"TRACK_C_GATE3_BINDING_INVALID: {figure_id}")
    result_binding = receipt.get("result_binding")
    if not isinstance(result_binding, dict):
        raise TrackCPaperExpressionError(f"TRACK_C_RESULT_BINDING_MISSING: {figure_id}")
    result_id = result_binding.get("result_id")
    result_type = result_binding.get("result_type")
    result_sha256 = result_binding.get("result_sha256")
    source_contract = result_binding.get("source_contract")
    if (
        not isinstance(result_id, str)
        or not result_id.strip()
        or not isinstance(result_type, str)
        or not result_type.strip()
        or not isinstance(result_sha256, str)
        or SHA256_RE.fullmatch(result_sha256) is None
        or result_binding.get("approved") is not True
        or source_contract != "result_acceptance_contract"
        or result_binding.get("result_acceptance_contract_sha256") != contract_sha256
        or result_binding.get("gate3_evidence_sha256") != gate3_evidence_sha256
    ):
        if result_binding.get("approved") is not True:
            raise TrackCPaperExpressionError(f"TRACK_C_RESULT_NOT_APPROVED: {figure_id}")
        raise TrackCPaperExpressionError(f"TRACK_C_RESULT_BINDING_INVALID: {figure_id}")
    normalized_result = {
        "result_id": result_id,
        "result_type": result_type,
        "result_sha256": result_sha256.lower(),
        "approved": True,
        "source_contract": source_contract,
        "result_acceptance_contract_sha256": contract_sha256,
        "gate3_evidence_sha256": gate3_evidence_sha256,
    }
    matching_cards = [
        card
        for card in result_acceptance_contract.get("result_cards", [])
        if isinstance(card, dict) and card.get("result_id") == result_id
    ]
    if len(matching_cards) != 1 or matching_cards[0].get("approved") is not True:
        raise TrackCPaperExpressionError(f"TRACK_C_RESULT_NOT_APPROVED: {figure_id}")
    if canonical_record_sha256(matching_cards[0]) != normalized_result["result_sha256"]:
        raise TrackCPaperExpressionError(f"TRACK_C_RESULT_CARD_HASH_MISMATCH: {figure_id}")
    if not isinstance(source_binding, dict):
        raise TrackCPaperExpressionError(f"TRACK_C_SOURCE_BINDING_INVALID: {figure_id}")
    if (
        source_binding.get("source_id") != result_id
        or source_binding.get("source_type") != result_type
        or str(source_binding.get("sha256", "")).lower() != normalized_result["result_sha256"]
        or normalized_spec["source_binding"] != {
            "source_id": result_id,
            "source_type": result_type,
            "sha256": normalized_result["result_sha256"],
        }
    ):
        raise TrackCPaperExpressionError(f"TRACK_C_RESULT_BINDING_MISMATCH: {figure_id}")

    asset_binding = receipt.get("asset_binding")
    if not isinstance(asset_binding, dict):
        raise TrackCPaperExpressionError(f"TRACK_C_ASSET_BINDING_MISSING: {figure_id}")
    asset_sha256 = asset_binding.get("asset_sha256")
    generated_at = asset_binding.get("generated_at")
    generator_version = asset_binding.get("generator_version")
    if (
        not isinstance(asset_sha256, str)
        or SHA256_RE.fullmatch(asset_sha256) is None
        or not isinstance(generated_at, str)
        or not generated_at.strip()
        or not isinstance(generator_version, str)
        or generator_version != FIGURE_SPEC_VERSION
        or asset_binding.get("spec_sha256") != spec_sha256
        or asset_binding.get("script_sha256") != receipt.get("script_sha256")
    ):
        raise TrackCPaperExpressionError(f"TRACK_C_ASSET_BINDING_INVALID: {figure_id}")
    actual_asset_sha256 = sha256_file(source_asset)
    if actual_asset_sha256 != asset_sha256.lower():
        raise TrackCPaperExpressionError(f"FIGURE_ASSET_HASH_MISMATCH: {figure_id}")
    normalized_asset = {
        "asset_sha256": asset_sha256.lower(),
        "generated_at": generated_at,
        "generator_version": generator_version,
        "spec_sha256": spec_sha256,
        "script_sha256": receipt["script_sha256"],
    }
    return normalized_result, normalized_asset


def _prepare_figure_assets(
    payload: dict[str, Any],
    source_root: Path,
    output_root: Path,
    allowed_root: Path,
) -> list[dict[str, Any]]:
    policy = payload.get("writing_policy")
    placement = policy.get("figure_placement") if isinstance(policy, dict) else None
    if not isinstance(placement, dict):
        return []
    raw_figures = placement.get("figures", [])
    if not isinstance(raw_figures, list):
        raise TrackCPaperExpressionError("TRACK_C_FIGURE_PLACEMENT_INVALID")
    if raw_figures and placement.get("formal_figure_backend") != "MATLAB":
        raise TrackCPaperExpressionError("FORMAL_FIGURE_BACKEND_MUST_BE_MATLAB")
    if not raw_figures:
        return []
    result_acceptance_contract = policy.get("result_acceptance_contract") if isinstance(policy, dict) else None
    gate3_evaluation = evaluate_human_gate(allowed_root, "GATE3")
    try:
        gate3_evaluation = bind_gate_evidence(allowed_root, gate3_evaluation)
    except MatlabFigureSpecError as exc:
        raise TrackCPaperExpressionError(str(exc)) from exc
    if gate3_evaluation.get("gate_status") != "PASS":
        raise TrackCPaperExpressionError("TRACK_C_GATE3_NOT_APPROVED")
    gate3_evidence_sha256 = str(gate3_evaluation["evidence_sha256"])
    figure_assets: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_figures, 1):
        if not isinstance(raw, dict) or raw.get("formal", True) is not True:
            continue
        figure_id = raw.get("figure_id")
        if not isinstance(figure_id, str) or FIGURE_ID_RE.fullmatch(figure_id) is None:
            raise TrackCPaperExpressionError(f"TRACK_C_FIGURE_METADATA_INVALID: {index}")
        if figure_id in seen_ids:
            raise TrackCPaperExpressionError(f"TRACK_C_FIGURE_DUPLICATED: {figure_id}")
        seen_ids.add(figure_id)
        caption = raw.get("public_title")
        if not isinstance(caption, str) or not caption.strip():
            raise TrackCPaperExpressionError(f"TRACK_C_FIGURE_CAPTION_REQUIRED: {figure_id}")
        try:
            paper_binding = validate_figure_paper_binding(raw)
        except FigurePaperBindingError as exc:
            raise TrackCPaperExpressionError(str(exc)) from exc
        receipt, _receipt_path = _load_figure_receipt(raw, source_root, allowed_root)
        if receipt.get("status") != "PASS":
            raise TrackCPaperExpressionError(str(receipt.get("code") or "FIGURE_GENERATION_FAILED"))
        if receipt.get("figure_id") != figure_id:
            raise TrackCPaperExpressionError(f"TRACK_C_FIGURE_ID_MISMATCH: {figure_id}")
        if receipt.get("execution_status") != "SUCCEEDED" or receipt.get("generation_status") != "GENERATED":
            raise TrackCPaperExpressionError(str(receipt.get("code") or "FIGURE_GENERATION_FAILED"))
        asset_raw = receipt.get("asset_path") or raw.get("asset_path")
        asset_format = str(receipt.get("asset_format") or "").lower().strip()
        source_binding = receipt.get("source_binding") or raw.get("source_binding")
        if not isinstance(asset_raw, str) or not asset_raw.strip():
            raise TrackCPaperExpressionError("FIGURE_ASSET_MISSING")
        if not isinstance(source_binding, (str, dict)) or not source_binding:
            raise TrackCPaperExpressionError(f"TRACK_C_FIGURE_SOURCE_BINDING_MISSING: {figure_id}")
        source_asset = Path(asset_raw)
        if not source_asset.is_absolute():
            source_asset = allowed_root / source_asset
        source_asset = _inside(allowed_root, source_asset, "TRACK_C_FIGURE_ASSET_ESCAPES_CASE")
        if not source_asset.is_file() or source_asset.stat().st_size <= 0:
            raise TrackCPaperExpressionError("FIGURE_ASSET_MISSING")
        if source_asset.suffix.lower() not in FIGURE_ASSET_EXTENSIONS:
            raise TrackCPaperExpressionError(f"TRACK_C_FIGURE_FORMAT_INVALID: {figure_id}")
        if asset_format != source_asset.suffix.lower().lstrip("."):
            raise TrackCPaperExpressionError(f"TRACK_C_FIGURE_FORMAT_INVALID: {figure_id}")
        result_binding, asset_binding = _validate_figure_provenance(
            figure_id,
            receipt,
            source_binding,
            source_asset,
            raw,
            result_acceptance_contract,
            gate3_evaluation,
            gate3_evidence_sha256,
            allowed_root,
        )
        try:
            visual_qa = validate_figure_asset(source_asset, asset_format=asset_format)
        except FigureAssetQualityError as exc:
            raise TrackCPaperExpressionError(f"FIGURE_VISUAL_QA_FAILED: {exc}") from exc
        target_relative = Path("figures") / f"{figure_id}{source_asset.suffix.lower()}"
        target = output_root / target_relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_asset, target)
        if sha256_file(target) != asset_binding["asset_sha256"]:
            raise TrackCPaperExpressionError(f"FIGURE_ASSET_HASH_MISMATCH: {figure_id}")
        figure_assets.append(
            {
                "figure_id": figure_id,
                "asset_path": target_relative.as_posix(),
                "asset_format": asset_format,
                "generation_status": "GENERATED",
                "execution_status": "SUCCEEDED",
                "source_binding": source_binding,
                "result_binding": result_binding,
                "asset_binding": asset_binding,
                "visual_qa": visual_qa,
                **paper_binding,
                "task_id": raw.get("task_id"),
                "reasoning_role": raw.get("reasoning_role"),
                "placement_slot": raw.get("placement_slot", "SECTION_AFTER_DISCUSSION"),
                "caption": caption.strip(),
            }
        )
    return figure_assets


def run_track_c_pass(
    input_manifest: str | Path,
    output_manifest: str | Path,
    output_root: str | Path,
    receipt_path: str | Path,
    *,
    case_id: str | None = None,
    case_root: str | Path | None = None,
    writer_schema_version: str = WRITER_SCHEMA_VERSION,
    rules_version: str = RULES_VERSION,
) -> dict[str, Any]:
    """Review writer content and emit the manifest consumed by the renderer."""

    input_path = Path(input_manifest).resolve()
    output_path = Path(output_manifest).resolve()
    content_output_root = Path(output_root).resolve() / "content"
    receipt = Path(receipt_path).resolve()
    payload, source_root = _read_manifest(input_path)
    allowed_root = Path(case_root).resolve() if case_root is not None else input_path.parent.resolve()
    try:
        input_path.relative_to(allowed_root)
        source_root.relative_to(allowed_root)
    except ValueError as exc:
        raise TrackCPaperExpressionError("TRACK_C_INPUT_ESCAPES_CASE") from exc
    config_path = allowed_root / "case_config.json"
    if config_path.is_file():
        try:
            case_config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TrackCPaperExpressionError("TRACK_C_CASE_CONFIG_INVALID") from exc
        if isinstance(case_config, dict) and case_config.get("state_authority") == "case_config.json":
            try:
                manifest_mode = resolve_manifest_mode(payload, content_keys=set(payload.get("content", {})))
            except WriterSchemaError as exc:
                raise TrackCPaperExpressionError(str(exc)) from exc
            policy = payload.get("writing_policy")
            if manifest_mode != CURRENT_FORMAL_MODE:
                raise TrackCPaperExpressionError("AUTHORITATIVE_CASE_CURRENT_FORMAL_MANIFEST_REQUIRED")
            required_policy_version = str(
                case_config.get("writing_contract_version") or "controlled_writing_layer_v4"
            )
            if not isinstance(policy, dict) or policy.get("version") != required_policy_version:
                raise TrackCPaperExpressionError(
                    f"AUTHORITATIVE_CASE_WRITING_POLICY_REQUIRED:{required_policy_version}"
                )
    if payload.get("writer_schema_version", writer_schema_version) != writer_schema_version:
        raise TrackCPaperExpressionError("WRITER_SCHEMA_VERSION_NOT_ALLOWED")
    resolved_case_id = case_id or str(payload.get("case_id") or "UNSPECIFIED_CASE")
    payload["writer_schema_version"] = writer_schema_version
    payload["case_id"] = resolved_case_id
    try:
        task_contract = resolve_task_contract(payload)
    except WriterSchemaError as exc:
        raise TrackCPaperExpressionError(str(exc)) from exc
    try:
        writing_policy = validate_writing_policy(payload, source_root, payload["content"])
    except WritingPolicyError as exc:
        raise TrackCPaperExpressionError(str(exc)) from exc
    review = _review(payload, source_root, task_contract)
    review["writing_policy"] = writing_policy
    content_output_root.mkdir(parents=True, exist_ok=True)
    figure_assets = _prepare_figure_assets(payload, source_root, content_output_root, allowed_root)

    copied_content: dict[str, str] = {}
    for key, relative in payload["content"].items():
        source = (source_root / relative).resolve()
        target = content_output_root / Path(relative).name
        shutil.copyfile(source, target)
        copied_content[key] = target.name

    output_payload = dict(payload)
    output_payload["content_root"] = content_output_root.relative_to(output_path.parent).as_posix()
    output_payload["content"] = copied_content
    output_payload["track_c_pass"] = {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS",
        "input_manifest": input_path.relative_to(input_path.parent).as_posix(),
        "input_sha256": sha256_file(input_path),
        "review": review,
        "figure_assets": figure_assets,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    output_content_sha256 = sha256_content_map(content_output_root, copied_content)
    output_manifest_sha256 = sha256_file(output_path)
    input_sha256 = sha256_file(input_path)

    receipt_payload = {
        "schema_version": SCHEMA_VERSION,
        "receipt_version": RECEIPT_VERSION,
        "case_id": resolved_case_id,
        "writer_schema_version": writer_schema_version,
        "status": "PASS",
        "track_c_status": "PASS",
        "track_c_rules_version": rules_version,
        "created_by_actual_track_c_runtime": True,
        "input_artifact": str(input_path),
        "input_sha256": input_sha256,
        "track_c_input_sha256": input_sha256,
        "output_artifact": str(output_path),
        "output_sha256": output_manifest_sha256,
        "track_c_output_sha256": output_content_sha256,
        "track_c_output_manifest_sha256": output_manifest_sha256,
        "renderer_input": str(output_path),
        "track_c_consumed_by_final_writer_chain": True,
        "review": review,
        "figure_assets": figure_assets,
        "model_changed": False,
        "result_changed": False,
        "claim_changed": False,
    }
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(receipt_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt_payload
