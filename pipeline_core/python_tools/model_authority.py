"""Case-bound model authority fingerprint and fail-closed verification.

The fingerprint covers the model decision and the case-local implementation
inputs that can change mathematical execution.  Review packets, README files,
tests, logs, results, and paper files are deliberately excluded.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


AUTHORITY_SCHEMA_VERSION = "model_authority_binding_v1"
REVISION_REAUTHORIZATION_SCHEMA = "post_acceptance_model_revision_reauthorization_v1"
REVISION_REAUTHORIZATION_PATH = "human_review/post_acceptance_model_revision_reauthorization.json"
MODEL_REVIEW_RELATIVE_PATH = "human_review/MODEL_REVISION_REVIEW.md"
WORKFLOW_STATE_FIELDS = {
    "status",
    "current_stage",
    "previous_stage",
    "next_allowed_stage",
    "state_revision",
    "last_transition_id",
    "last_transition_at",
    # Literature Grounding records downstream workflow readiness. These
    # fields do not change the selected route, implementation contract, or
    # executable model and therefore must not invalidate human model approval.
    "literature_status",
    "formal_literature_search_status",
    # Post-Gate3 review and writing stages record workflow readiness only.
    # They are written by transition_case state updates and do not alter the
    # approved route, implementation contract, parameters, or solver code.
    "track_b_status",
    "track_c_status",
    "paper_status",
    "post_writer_reentry_status",
    "final_review_status",
}

# These booleans already existed as ``false`` in case templates when model
# approval was granted. Literature Grounding flips them to ``true``. Preserve
# that pre-literature neutral representation for backward-compatible hashes
# while still making later workflow changes non-authoritative for the model.
MODEL_AUTHORITY_NEUTRAL_CONFIG_VALUES = {
    "citation_ready": False,
    "formal_reference_allowed": False,
}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _field(text: str, name: str) -> str:
    match = re.search(
        rf"(?im)^\s*(?:-\s*)?`?{re.escape(name)}`?\s*[:=]\s*(.*?)\s*$",
        text,
    )
    return match.group(1).strip().strip("`") if match else ""


def _assignment(text: str, name: str) -> str:
    match = re.search(rf"(?m)^\s*{re.escape(name)}\s*=\s*[\"']([^\"']+)[\"']", text)
    return match.group(1).strip() if match else ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _authority_paths(case_root: Path) -> list[Path]:
    fixed = [
        case_root / "case_config.json",
        case_root / "human_review" / "gate_2_model_choice.md",
        case_root / "handoff" / "MODEL_BUILDING_HANDOFF.md",
        case_root / "code" / "MODEL_IMPLEMENTATION_SPEC.md",
        case_root / "code" / "python" / "model_contract.py",
        case_root / "code" / "python" / "solver.py",
    ]
    # Post-Gate3 case-local solver variants are mathematical implementation
    # inputs too.  Keep tests, plotting helpers, logs, and result writers out.
    solver_variants = sorted(
        path
        for path in (case_root / "code" / "python").glob("*_solver.py")
        if not path.name.startswith("test_")
    )
    paths = fixed + solver_variants
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            unique.append(path)
            seen.add(resolved)
    return unique


def requires_model_authority_binding(case_dir: str | Path) -> bool:
    """Return whether this case has a case-local formal model to protect."""

    root = Path(case_dir)
    return all(
        (root / relative).is_file()
        for relative in (
            "human_review/gate_2_model_choice.md",
            "code/MODEL_IMPLEMENTATION_SPEC.md",
            "code/python/model_contract.py",
            "code/python/solver.py",
        )
    )


def compute_model_authority(case_dir: str | Path) -> dict[str, Any]:
    """Compute the current deterministic authority fingerprint without solving."""

    root = Path(case_dir).resolve()
    config_path = root / "case_config.json"
    config = _read_json(config_path) or {}
    contract_path = root / "code" / "python" / "model_contract.py"
    contract_text = contract_path.read_text(encoding="utf-8-sig") if contract_path.is_file() else ""
    spec_path = root / "code" / "MODEL_IMPLEMENTATION_SPEC.md"
    handoff_path = root / "handoff" / "MODEL_BUILDING_HANDOFF.md"
    handoff_text = handoff_path.read_text(encoding="utf-8-sig") if handoff_path.is_file() else ""
    route = _assignment(contract_text, "APPROVED_ROUTE_ID") or _field(handoff_text, "selected_model_route")
    family = _assignment(contract_text, "APPROVED_MODEL_FAMILY") or _field(handoff_text, "approved_model_family")

    missing: list[str] = []
    file_records: list[dict[str, str]] = []
    for path in _authority_paths(root):
        relative = path.resolve().relative_to(root).as_posix()
        if not path.is_file():
            missing.append(relative)
            continue
        if relative == "case_config.json":
            config_payload = _read_json(path) or {}
            stable_config = {
                key: value for key, value in config_payload.items() if key not in WORKFLOW_STATE_FIELDS
            }
            for key, neutral_value in MODEL_AUTHORITY_NEUTRAL_CONFIG_VALUES.items():
                if key in config_payload:
                    stable_config[key] = neutral_value
            stable_bytes = json.dumps(
                stable_config,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            file_records.append({"path": relative, "sha256": hashlib.sha256(stable_bytes).hexdigest()})
        else:
            file_records.append({"path": relative, "sha256": _sha256(path)})

    manifest = {
        "schema_version": AUTHORITY_SCHEMA_VERSION,
        "case_id": str(config.get("case_id") or root.name),
        "model_route": route,
        "model_family": family,
        "files": file_records,
    }
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "status": "FAIL" if missing else "PASS",
        "reason": "MODEL_AUTHORITY_INPUT_MISSING" if missing else "MODEL_AUTHORITY_COMPUTED",
        "case_id": manifest["case_id"],
        "model_route": route,
        "model_family": family,
        "model_authority_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "authority_manifest": manifest,
        "missing_paths": missing,
    }


def verify_model_authority(
    case_dir: str | Path,
    *,
    authorization_path: str | Path | None = None,
    entrypoint: str | None = None,
) -> dict[str, Any]:
    """Verify human-approved model hash, entrypoint, and stage binding."""

    root = Path(case_dir).resolve()
    if not requires_model_authority_binding(root):
        return {"status": "NOT_REQUIRED", "reason": "NO_CASE_LOCAL_FORMAL_MODEL"}

    current = compute_model_authority(root)
    if current.get("status") != "PASS":
        return current

    review_path = root / MODEL_REVIEW_RELATIVE_PATH
    result: dict[str, Any] = {
        "status": "FAIL",
        "reason": "MODEL_REVIEW_REQUIRED",
        "model_authority_hash": current["model_authority_hash"],
        "case_id": current["case_id"],
        "model_route": current["model_route"],
        "model_family": current["model_family"],
        "review_path": MODEL_REVIEW_RELATIVE_PATH,
    }
    if not review_path.is_file():
        return result
    try:
        review_text = review_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        result["reason"] = "MODEL_REVIEW_UNREADABLE"
        return result

    status = _field(review_text, "MODEL_AUTHORIZATION_STATUS").upper()
    approved_hash = _field(review_text, "MODEL_AUTHORITY_HASH")
    active_entrypoint = _field(review_text, "MODEL_ACTIVE_ENTRYPOINT")
    result["model_active_entrypoint"] = active_entrypoint
    if status != "APPROVED" or not approved_hash:
        return result
    if approved_hash != current["model_authority_hash"]:
        result["reason"] = "MODEL_AUTHORIZATION_INVALIDATED"
        result["approved_model_authority_hash"] = approved_hash
        return result
    if _field(review_text, "CASE_ID") != current["case_id"]:
        result["reason"] = "MODEL_AUTHORIZATION_CASE_MISMATCH"
        return result
    if _field(review_text, "MODEL_ROUTE") != current["model_route"]:
        result["reason"] = "MODEL_AUTHORIZATION_ROUTE_MISMATCH"
        return result
    if _field(review_text, "MODEL_FAMILY") != current["model_family"]:
        result["reason"] = "MODEL_AUTHORIZATION_FAMILY_MISMATCH"
        return result
    approval_id = _field(review_text, "HUMAN_APPROVAL_ID")
    approval_time = _field(review_text, "APPROVAL_TIME")
    if not approval_id or not approval_time:
        result["reason"] = "MODEL_AUTHORIZATION_HUMAN_FIELDS_MISSING"
        return result
    if not active_entrypoint or active_entrypoint.upper() in {
        "EMPTY",
        "PENDING_USER_SELECTION",
    }:
        result["reason"] = "MODEL_ACTIVE_ENTRYPOINT_REQUIRED"
        return result
    if entrypoint is not None and active_entrypoint != entrypoint:
        result["reason"] = "MODEL_ACTIVE_ENTRYPOINT_MISMATCH"
        result["expected_entrypoint"] = entrypoint
        return result

    if authorization_path is not None:
        try:
            payload = json.loads(Path(authorization_path).read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            result["reason"] = "AUTHORIZATION_STAGE_ARTIFACT_UNREADABLE"
            return result
        if not isinstance(payload, dict):
            result["reason"] = "AUTHORIZATION_STAGE_ARTIFACT_INVALID"
            return result
        if payload.get("model_authority_hash") != current["model_authority_hash"]:
            result["reason"] = "MODEL_AUTHORIZATION_INVALIDATED"
            result["authorization_artifact_model_authority_hash"] = payload.get("model_authority_hash")
            return result
        if payload.get("model_authority_approval_id") != approval_id:
            result["reason"] = "MODEL_AUTHORIZATION_APPROVAL_ID_MISMATCH"
            return result
        if payload.get("model_active_entrypoint") != active_entrypoint:
            result["reason"] = "MODEL_ACTIVE_ENTRYPOINT_MISMATCH"
            return result

    result.update(
        {
            "status": "PASS",
            "reason": "MODEL_AUTHORITY_MATCHES_HUMAN_APPROVAL",
            "human_approval_id": approval_id,
            "approval_time": approval_time,
            "model_active_entrypoint": active_entrypoint,
            "authority_manifest": current["authority_manifest"],
        }
    )
    return result


def verify_post_acceptance_revision_reauthorization(
    case_dir: str | Path,
    reauthorization_path: str | Path | None = None,
) -> dict[str, Any]:
    """Verify the narrowly-scoped human reauthorization for a model revision.

    This is deliberately not a stage transition.  It permits a formally
    accepted case to re-enter the existing solver entrypoints only when the
    current approved model hash is bound to an explicit revision record and
    the prior formal result artifacts are preserved as historical evidence.
    """

    root = Path(case_dir).resolve()
    config = _read_json(root / "case_config.json") or {}
    current_stage = str(config.get("current_stage") or "").strip().upper()
    if current_stage != "RESULT_ACCEPTANCE":
        return {"status": "FAIL", "reason": "REVISION_REAUTHORIZATION_STAGE_REQUIRED", "current_stage": current_stage}
    path = Path(reauthorization_path) if reauthorization_path is not None else root / REVISION_REAUTHORIZATION_PATH
    if not path.is_absolute():
        path = root / path
    try:
        payload = _read_json(path)
    except Exception:
        payload = None
    if not isinstance(payload, dict):
        return {"status": "FAIL", "reason": "REVISION_REAUTHORIZATION_UNREADABLE"}
    if payload.get("schema_version") != REVISION_REAUTHORIZATION_SCHEMA or payload.get("status") != "APPROVED":
        return {"status": "FAIL", "reason": "REVISION_REAUTHORIZATION_SCHEMA_INVALID"}
    if payload.get("case_id") != str(config.get("case_id") or root.name):
        return {"status": "FAIL", "reason": "REVISION_REAUTHORIZATION_CASE_MISMATCH"}
    if str(payload.get("source_stage") or "").strip().upper() != "RESULT_ACCEPTANCE":
        return {"status": "FAIL", "reason": "REVISION_REAUTHORIZATION_SOURCE_STAGE_INVALID"}
    if payload.get("formal_result_authority_invalidated_by_revision") is not True:
        return {"status": "FAIL", "reason": "REVISION_REAUTHORIZATION_INVALIDATION_FLAG_REQUIRED"}
    authority = compute_model_authority(root)
    approved_hash = str(payload.get("approved_model_authority_hash") or "")
    if authority.get("status") != "PASS" or approved_hash != authority.get("model_authority_hash"):
        return {
            "status": "FAIL",
            "reason": "REVISION_REAUTHORIZATION_MODEL_HASH_MISMATCH",
            "approved_model_authority_hash": approved_hash,
            "current_model_authority_hash": authority.get("model_authority_hash"),
        }
    preserved = payload.get("preserved_formal_result_files")
    if not isinstance(preserved, list) or not preserved:
        return {"status": "FAIL", "reason": "REVISION_REAUTHORIZATION_HISTORY_BINDING_REQUIRED"}
    checked: list[dict[str, str]] = []
    for item in preserved:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("sha256"), str):
            return {"status": "FAIL", "reason": "REVISION_REAUTHORIZATION_HISTORY_BINDING_INVALID"}
        historical = root / item["path"]
        if not historical.is_file() or _sha256(historical) != item["sha256"]:
            return {"status": "FAIL", "reason": "REVISION_REAUTHORIZATION_HISTORY_ARTIFACT_MISSING_OR_CHANGED", "path": item["path"]}
        checked.append({"path": item["path"], "sha256": item["sha256"]})
    return {
        "status": "PASS",
        "reason": "POST_ACCEPTANCE_MODEL_REVISION_REAUTHORIZATION_VALID",
        "source_stage": "RESULT_ACCEPTANCE",
        "authorized_action": "REENTER_EXISTING_SOLVER_EXECUTION",
        "model_authority_hash": authority.get("model_authority_hash"),
        "preserved_formal_result_files": checked,
        "payload": payload,
        "path": str(path),
    }
