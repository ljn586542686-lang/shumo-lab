"""Structural validator for a case-local independent XVAL artifact.

This module validates declared independent evidence.  It never runs a model,
loads a verifier, writes artifacts, or creates final submission material.
"""

from __future__ import annotations

import hashlib
import json
import string
from pathlib import Path
from typing import Any


DEFAULT_ARTIFACT_PATH = Path("output") / "validation" / "xval_independent_check.json"
STRICT_TIERS = frozenset({"selection", "formal"})
VALID_STATUSES = frozenset({"PASS", "MANUAL_REVIEW_REQUIRED", "BLOCKED"})
VALID_STRENGTHS = frozenset({
    "EXACT_SECOND_METHOD", "INDEPENDENT_RERUN", "ENUMERATION_OR_BRUTE_FORCE",
    "ANALYTIC_CHECK", "BOUNDS_OR_FEASIBILITY_ONLY", "MANUAL_REVIEW_ONLY",
})
SUPPORTED_TARGET_TYPES = frozenset({
    "scalar_objective", "decision_vector", "schedule_order", "table",
    "top_k_ranking", "feasibility_flag", "constraint_residual", "route_family",
})


def _tier_from_case(case_dir: Path) -> str:
    try:
        config = json.loads((case_dir / "case_config.json").read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "training"
    value = config.get("submission_tier") if isinstance(config, dict) else None
    return str(value).strip().lower() if value is not None and str(value).strip() else "training"


def _resolve_case_path(case_dir: Path, value: str | Path | None) -> tuple[Path | None, str | None]:
    if value is None or not str(value).strip():
        return None, None
    raw = Path(value)
    root = case_dir.resolve()
    resolved = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    try:
        return resolved, resolved.relative_to(root).as_posix()
    except ValueError:
        return None, None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in string.hexdigits for char in value)


def load_xval_independent_artifact(
    case_dir: str | Path,
    artifact_path: str | Path | None = None,
) -> tuple[dict[str, Any] | None, Path | None, str | None]:
    """Load an XVAL artifact without treating its content as trusted."""

    root = Path(case_dir)
    resolved, _relative = _resolve_case_path(root, artifact_path or DEFAULT_ARTIFACT_PATH)
    if resolved is None:
        return None, None, "unsafe_path"
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None, resolved, "missing"
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, resolved, "malformed"
    if not isinstance(payload, dict):
        return None, resolved, "malformed"
    return payload, resolved, None


def _base_result(artifact_path: str, tier: str, required: bool) -> dict[str, Any]:
    return {
        "status": "MANUAL_REVIEW_REQUIRED",
        "xval_required": required,
        "artifact_path": artifact_path,
        "blockers": [],
        "warnings": [],
        "checked_targets_count": 0,
        "comparison_results_count": 0,
        "independence_strength": None,
        "primary_method_id": None,
        "independent_method_id": None,
        "primary_provider_role_id": None,
        "independent_provider_role_id": None,
        "stale_bindings": [],
        "rerun_only_manual_required": False,
        "hash_independence_checked": False,
        "primary_code_hash_missing": False,
        "verifier_code_not_independent": False,
        "independent_output_not_independent": False,
        "route_to_blocking_status": False,
        "effective_submission_tier": tier,
        "final_submit_created": False,
    }


def _finalize(result: dict[str, Any], *, strict: bool) -> dict[str, Any]:
    result["blockers"] = list(dict.fromkeys(result["blockers"]))
    result["warnings"] = list(dict.fromkeys(result["warnings"]))
    if result["blockers"]:
        if strict:
            result["status"] = "BLOCKED"
            result["route_to_blocking_status"] = True
        else:
            result["warnings"] = list(dict.fromkeys(result["warnings"] + result["blockers"]))
            result["blockers"] = []
            result["status"] = "MANUAL_REVIEW_REQUIRED"
    return result


def _require_nonempty_strings(payload: dict[str, Any], fields: tuple[str, ...], blockers: list[str]) -> None:
    if any(not isinstance(payload.get(field), str) or not payload[field].strip() for field in fields):
        blockers.append("XVAL_INVALID_SCHEMA")


def _tolerance_value(value: object, tolerances: object) -> float | None:
    candidate = value
    if isinstance(candidate, str) and isinstance(tolerances, dict):
        candidate = tolerances.get(candidate)
    if isinstance(candidate, dict):
        candidate = candidate.get("absolute", candidate.get("abs", candidate.get("tolerance")))
    try:
        numeric = float(candidate)
    except (TypeError, ValueError):
        return None
    return numeric if numeric >= 0 else None


def _check_hash_binding(
    *,
    case_dir: Path,
    path_value: object,
    expected_sha256: object,
    stale_code: str,
    label: str,
    result: dict[str, Any],
    require_role: bool = False,
    role: object = None,
    required: object = True,
) -> None:
    if not isinstance(required, bool):
        result["blockers"].append("XVAL_CONTENT_BINDING_MALFORMED")
        return
    if not isinstance(path_value, str) or not path_value.strip() or not _valid_sha256(expected_sha256):
        result["blockers"].append("XVAL_CONTENT_BINDING_MALFORMED")
        return
    if require_role and (not isinstance(role, str) or not role.strip()):
        result["blockers"].append("XVAL_CONTENT_BINDING_MALFORMED")
        return
    path, normalized = _resolve_case_path(case_dir, path_value)
    if path is None:
        result["blockers"].append("XVAL_UNSAFE_PATH")
        result["stale_bindings"].append({"label": label, "path": path_value, "actual_sha256": "unsafe"})
        return
    if required is not True:
        return
    if not path.is_file():
        result["blockers"].append(stale_code)
        result["stale_bindings"].append({"label": label, "path": normalized, "expected_sha256": expected_sha256, "actual_sha256": "missing"})
        return
    try:
        actual = _sha256_file(path)
    except OSError:
        result["blockers"].append(stale_code)
        result["stale_bindings"].append({"label": label, "path": normalized, "expected_sha256": expected_sha256, "actual_sha256": "unreadable"})
        return
    if actual.lower() != str(expected_sha256).lower():
        result["blockers"].append(stale_code)
        result["stale_bindings"].append({"label": label, "path": normalized, "expected_sha256": expected_sha256, "actual_sha256": actual})


def validate_xval_independent_check(
    case_dir: str | Path,
    artifact_path: str | Path | None = None,
    submission_tier: str | None = None,
    required: bool | None = None,
) -> dict[str, Any]:
    """Validate independent XVAL metadata and bindings without executing either method."""

    root = Path(case_dir)
    tier = (submission_tier or _tier_from_case(root)).strip().lower()
    default_required = tier in STRICT_TIERS
    strict = default_required if required is None else required is True
    path_text = str(artifact_path or DEFAULT_ARTIFACT_PATH).replace("\\", "/")
    result = _base_result(path_text, tier, strict)
    if default_required and required is False:
        result["warnings"].append("XVAL_TIER_DEFAULT_EXPLICITLY_DISABLED")

    artifact, resolved_artifact_path, load_error = load_xval_independent_artifact(root, artifact_path)
    if load_error == "unsafe_path":
        result["blockers"].append("XVAL_UNSAFE_PATH")
        return _finalize(result, strict=strict)
    if load_error == "missing":
        result["blockers"].append("XVAL_MISSING")
        return _finalize(result, strict=strict)
    if load_error == "malformed" or artifact is None:
        result["blockers"].append("XVAL_MALFORMED")
        return _finalize(result, strict=strict)
    assert resolved_artifact_path is not None
    result["artifact_path"] = _resolve_case_path(root, resolved_artifact_path)[1] or path_text

    required_fields = (
        "version", "case_id", "submission_tier", "status", "generated_at",
        "primary_result_path", "primary_result_sha256", "primary_method_id",
        "primary_provider_role_id", "independent_method_id", "independent_provider_role_id",
        "independence_claim", "independence_strength", "checked_targets",
        "comparison_tolerances", "independent_outputs", "comparison_results", "input_bindings",
        "verifier_code_path", "verifier_code_sha256", "blockers", "warnings",
    )
    if any(field not in artifact for field in required_fields):
        result["blockers"].append("XVAL_INVALID_SCHEMA")

    status = artifact.get("status")
    if status not in VALID_STATUSES:
        result["blockers"].append("XVAL_INVALID_SCHEMA")
    elif status == "BLOCKED":
        result["blockers"].append("XVAL_STATUS_BLOCKED")
    elif status == "MANUAL_REVIEW_REQUIRED":
        result["blockers"].append("XVAL_STATUS_MANUAL_REVIEW_REQUIRED")
    elif status == "PASS" and artifact.get("blockers"):
        result["blockers"].append("XVAL_INVALID_SCHEMA")

    _require_nonempty_strings(
        artifact,
        ("version", "case_id", "submission_tier", "generated_at", "primary_method_id",
         "primary_provider_role_id", "independent_method_id", "independent_provider_role_id",
         "independence_claim", "verifier_code_path"),
        result["blockers"],
    )
    result["independence_strength"] = artifact.get("independence_strength")
    result["primary_method_id"] = artifact.get("primary_method_id")
    result["independent_method_id"] = artifact.get("independent_method_id")
    result["primary_provider_role_id"] = artifact.get("primary_provider_role_id")
    result["independent_provider_role_id"] = artifact.get("independent_provider_role_id")

    strength = artifact.get("independence_strength")
    if strength not in VALID_STRENGTHS:
        result["blockers"].append("XVAL_INDEPENDENCE_STRENGTH_INSUFFICIENT")
    elif strength == "MANUAL_REVIEW_ONLY":
        result["blockers"].append("XVAL_INDEPENDENCE_STRENGTH_INSUFFICIENT")
    if artifact.get("primary_provider_role_id") == artifact.get("independent_provider_role_id") or not artifact.get("independent_provider_role_id"):
        result["blockers"].append("XVAL_ROLE_SEPARATION_FAILED")
    if not str(artifact.get("independence_claim") or "").strip():
        result["blockers"].append("XVAL_INDEPENDENCE_CLAIM_MISSING")
    if artifact.get("primary_method_id") == artifact.get("independent_method_id"):
        if strength == "INDEPENDENT_RERUN":
            result["warnings"].append("XVAL_METHOD_RERUN_ONLY")
            result["rerun_only_manual_required"] = strict
            if strict:
                result["blockers"].append("XVAL_METHOD_RERUN_ONLY")
        else:
            result["blockers"].append("XVAL_METHOD_NOT_INDEPENDENT")

    targets = artifact.get("checked_targets")
    comparisons = artifact.get("comparison_results")
    tolerances = artifact.get("comparison_tolerances")
    result["checked_targets_count"] = len(targets) if isinstance(targets, list) else 0
    result["comparison_results_count"] = len(comparisons) if isinstance(comparisons, list) else 0
    if not isinstance(targets, list) or not targets:
        result["blockers"].append("XVAL_COMPARISON_MISSING")
        targets = []
    if not isinstance(comparisons, list) or not comparisons:
        result["blockers"].append("XVAL_COMPARISON_MISSING")
        comparisons = []
    if not isinstance(tolerances, dict) or not tolerances:
        result["blockers"].append("XVAL_TOLERANCE_MISSING")
        tolerances = {}

    comparison_by_id: dict[str, dict[str, Any]] = {}
    for comparison in comparisons:
        if not isinstance(comparison, dict) or not isinstance(comparison.get("comparison_result_id"), str) or not comparison["comparison_result_id"].strip():
            result["blockers"].append("XVAL_COMPARISON_MISSING")
            continue
        comparison_by_id[comparison["comparison_result_id"]] = comparison

    for target in targets:
        if not isinstance(target, dict):
            result["blockers"].append("XVAL_UNSUPPORTED_TARGET_TYPE")
            continue
        target_id = target.get("target_id")
        target_type = target.get("target_type")
        comparison_id = target.get("comparison_result_id")
        if not isinstance(target_id, str) or not target_id.strip() or target_type not in SUPPORTED_TARGET_TYPES:
            result["blockers"].append("XVAL_UNSUPPORTED_TARGET_TYPE")
            continue
        if not ("primary_value" in target or "primary_value_ref" in target) or not ("independent_value" in target or "independent_value_ref" in target):
            result["blockers"].append("XVAL_COMPARISON_MISSING")
            continue
        if not ("tolerance" in target or "tolerance_id" in target) or not isinstance(comparison_id, str) or not comparison_id.strip():
            result["blockers"].append("XVAL_TOLERANCE_MISSING")
            continue
        comparison = comparison_by_id.get(comparison_id)
        if comparison is None or comparison.get("target_id") != target_id:
            result["blockers"].append("XVAL_COMPARISON_MISSING")
            continue
        comparison_status = comparison.get("status")
        if comparison_status not in VALID_STATUSES or not (
            "primary_value" in comparison or "primary_value_ref" in comparison
        ) or not (
            "independent_value" in comparison or "independent_value_ref" in comparison
        ):
            result["blockers"].append("XVAL_COMPARISON_FAILED")
            continue
        if not ("tolerance" in comparison or "tolerance_id" in comparison):
            result["blockers"].append("XVAL_TOLERANCE_MISSING")
            continue
        if not ("difference" in comparison or "diagnostic" in comparison or "blocker" in comparison):
            result["blockers"].append("XVAL_COMPARISON_MISSING")
            continue
        if comparison_status != "PASS":
            result["blockers"].append("XVAL_COMPARISON_FAILED")
        if comparison_status == "BLOCKED" and not str(comparison.get("blocker") or "").strip():
            result["blockers"].append("XVAL_COMPARISON_MISSING")
        if target_type == "scalar_objective":
            primary_value = target.get("primary_value", comparison.get("primary_value"))
            independent_value = target.get("independent_value", comparison.get("independent_value"))
            tolerance = _tolerance_value(
                target.get("tolerance", target.get("tolerance_id", comparison.get("tolerance", comparison.get("tolerance_id")))),
                tolerances,
            )
            if tolerance is None:
                result["blockers"].append("XVAL_TOLERANCE_MISSING")
                continue
            try:
                difference = abs(float(primary_value) - float(independent_value))
            except (TypeError, ValueError):
                result["blockers"].append("XVAL_COMPARISON_FAILED")
                continue
            if difference > tolerance:
                result["blockers"].append("XVAL_COMPARISON_FAILED")

    _check_hash_binding(
        case_dir=root, path_value=artifact.get("primary_result_path"), expected_sha256=artifact.get("primary_result_sha256"),
        stale_code="XVAL_PRIMARY_RESULT_STALE", label="primary_result", result=result,
    )
    _check_hash_binding(
        case_dir=root, path_value=artifact.get("verifier_code_path"), expected_sha256=artifact.get("verifier_code_sha256"),
        stale_code="XVAL_VERIFIER_CODE_STALE", label="verifier_code", result=result,
    )
    primary_code_path = artifact.get("primary_code_path")
    primary_code_sha256 = artifact.get("primary_code_sha256")
    if primary_code_path is None and primary_code_sha256 is None:
        result["primary_code_hash_missing"] = True
        if strict:
            result["warnings"].append("XVAL_PRIMARY_CODE_HASH_MISSING")
    elif primary_code_path is None or primary_code_sha256 is None:
        result["blockers"].append("XVAL_CONTENT_BINDING_MALFORMED")
    else:
        _check_hash_binding(
            case_dir=root, path_value=primary_code_path, expected_sha256=primary_code_sha256,
            stale_code="XVAL_PRIMARY_CODE_STALE", label="primary_code", result=result,
        )
        if _valid_sha256(primary_code_sha256) and _valid_sha256(artifact.get("verifier_code_sha256")):
            result["hash_independence_checked"] = True
            if str(primary_code_sha256).lower() == str(artifact.get("verifier_code_sha256")).lower():
                result["verifier_code_not_independent"] = True
                result["blockers"].append("XVAL_VERIFIER_CODE_NOT_INDEPENDENT")
    bindings = artifact.get("input_bindings")
    if not isinstance(bindings, list):
        result["blockers"].append("XVAL_CONTENT_BINDING_MALFORMED")
    else:
        for index, binding in enumerate(bindings):
            if not isinstance(binding, dict):
                result["blockers"].append("XVAL_CONTENT_BINDING_MALFORMED")
                continue
            if not isinstance(binding.get("required_for_xval_validation"), bool):
                result["blockers"].append("XVAL_CONTENT_BINDING_MALFORMED")
                continue
            _check_hash_binding(
                case_dir=root, path_value=binding.get("relative_path"), expected_sha256=binding.get("sha256"),
                stale_code="XVAL_INPUT_BINDING_STALE", label=f"input_binding:{index}", result=result,
                require_role=True, role=binding.get("role"), required=binding.get("required_for_xval_validation"),
            )
    outputs = artifact.get("independent_outputs")
    if not isinstance(outputs, list):
        result["blockers"].append("XVAL_CONTENT_BINDING_MALFORMED")
    else:
        for index, output in enumerate(outputs):
            if not isinstance(output, dict):
                result["blockers"].append("XVAL_CONTENT_BINDING_MALFORMED")
                continue
            if "path" in output or "sha256" in output:
                _check_hash_binding(
                    case_dir=root, path_value=output.get("path"), expected_sha256=output.get("sha256"),
                    stale_code="XVAL_INDEPENDENT_OUTPUT_STALE", label=f"independent_output:{index}", result=result,
                )
                if _valid_sha256(output.get("sha256")) and _valid_sha256(artifact.get("primary_result_sha256")):
                    result["hash_independence_checked"] = True
                    output_path, _output_relative = _resolve_case_path(root, output.get("path"))
                    primary_path, _primary_relative = _resolve_case_path(root, artifact.get("primary_result_path"))
                    same_path = output_path is not None and primary_path is not None and output_path == primary_path
                    same_hash = str(output.get("sha256")).lower() == str(artifact.get("primary_result_sha256")).lower()
                    if same_path or same_hash:
                        result["independent_output_not_independent"] = True
                        result["blockers"].append("XVAL_INDEPENDENT_OUTPUT_NOT_INDEPENDENT")

    if not result["blockers"] and status == "PASS":
        result["status"] = "PASS"
    return _finalize(result, strict=strict)
