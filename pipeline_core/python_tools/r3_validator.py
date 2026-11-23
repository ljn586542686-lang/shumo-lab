"""Fail-closed validation for externally approved R3 review constraints.

This module is deliberately independent from ``package_candidate`` and
``final_freezer``.  It reads the external approval input and the attached
artifact once each, compares the canonical payload hash, and validates only
machine identities.  Filesystem paths are evidence pointers, never authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pipeline_core.python_tools.r3_approval_interface import (
    R3ApprovalInterfaceBlocked,
    canonicalize_approved_constraint_payload,
)


APPROVAL_SCHEMA_VERSION = "s0_r3_approval_input_v1"
ARTIFACT_SCHEMA_VERSION = "s0_r3_review_constraints_v1"
ENFORCEMENT_TYPES = {"R4A_CLAIM_ANCHOR", "R4B_STRUCTURED_CONSTRAINT", "MANUAL_REVIEW_ONLY"}
REVIEW_MODES = {"DETERMINISTIC", "MANUAL_REVIEW_REQUIRED"}
REQUIRED_ACTIONS = {"PRESERVE_REQUIRED_INCLUSION", "PRESERVE_AUTHORITY_REFERENCE"}
SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class R3ValidationBlocked(ValueError):
    """Raised by artifact preparation when an approved source is unsafe."""


def _read_bytes_once(path: Path) -> tuple[bytes | None, str | None]:
    try:
        raw = path.read_bytes()
    except OSError:
        return None, None
    return raw, hashlib.sha256(raw).hexdigest()


def _parse(raw: bytes | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _valid_sha(value: Any) -> bool:
    return isinstance(value, str) and SHA_RE.fullmatch(value) is not None


def _case_identity(case_dir: Path, repo_root: Path) -> str | None:
    try:
        return case_dir.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return None


def read_approval_input_once(path: str | Path) -> dict[str, Any]:
    """Read and parse the external human input exactly once."""

    raw, raw_hash = _read_bytes_once(Path(path))
    payload = _parse(raw)
    if payload is None:
        raise R3ValidationBlocked("R3_APPROVAL_INPUT_UNREADABLE")
    if payload.get("schema_version") != APPROVAL_SCHEMA_VERSION:
        raise R3ValidationBlocked("R3_APPROVAL_SCHEMA_INVALID")
    if not isinstance(payload.get("case_identity"), str) or not payload["case_identity"].strip():
        raise R3ValidationBlocked("R3_APPROVAL_CASE_IDENTITY_MISSING")
    if not isinstance(payload.get("run_id"), str) or not payload["run_id"].strip():
        raise R3ValidationBlocked("R3_APPROVAL_RUN_ID_MISSING")
    if not _valid_sha(payload.get("approved_constraint_set_sha256")):
        raise R3ValidationBlocked("R3_APPROVAL_HASH_INVALID")
    if not isinstance(payload.get("approval_reference"), str) or not payload["approval_reference"].strip():
        raise R3ValidationBlocked("R3_APPROVAL_REFERENCE_MISSING")
    return {
        "payload": payload,
        "raw_sha256": raw_hash,
        "read_count": 1,
    }


def _validate_machine_item(item: Any) -> list[str]:
    blockers: list[str] = []
    if not isinstance(item, dict):
        return ["R3_ITEM_INVALID"]
    for section_name in ("authority_reference", "target_reference", "enforcement_reference"):
        section = item.get(section_name)
        if not isinstance(section, dict):
            blockers.append(f"R3_{section_name.upper()}_MISSING:{item.get('constraint_id')}")
            continue
        if "path" in section or "reference" in section:
            blockers.append(f"R3_ARBITRARY_PATH_REFERENCE:{item.get('constraint_id')}")
    mode = item.get("review_mode")
    if item.get("required_action") not in REQUIRED_ACTIONS:
        blockers.append(f"R3_REQUIRED_ACTION_INVALID:{item.get('constraint_id')}")
    enforcement = item.get("enforcement_reference")
    if mode not in REVIEW_MODES:
        blockers.append(f"R3_REVIEW_MODE_INVALID:{item.get('constraint_id')}")
    if isinstance(enforcement, dict):
        etype = enforcement.get("enforcement_type")
        if etype not in ENFORCEMENT_TYPES:
            blockers.append(f"R3_ENFORCEMENT_TYPE_INVALID:{item.get('constraint_id')}")
        if etype == "R1_CANONICAL_INDEX" or etype == "R2A_CLAIM":
            blockers.append(f"R3_AUTHORITY_USED_AS_ENFORCEMENT:{item.get('constraint_id')}")
        if etype == "R4B_STRUCTURED_CONSTRAINT":
            if not str(enforcement.get("claim_id") or "").strip() or not str(enforcement.get("constraint_type") or "").strip() or not str(enforcement.get("symbol") or "").strip():
                blockers.append(f"R3_R4B_CONSTRAINT_SYMBOL_MISSING:{item.get('constraint_id')}")
    authority = item.get("authority_reference")
    if isinstance(authority, dict):
        atype = authority.get("authority_type")
        if atype == "R1_CANONICAL_INDEX":
            required = ("question_id", "artifact_role", "run_id", "result_schema_id")
            if any(not str(authority.get(key) or "").strip() for key in required):
                blockers.append(f"R3_R1_ADDRESS_INCOMPLETE:{item.get('constraint_id')}")
        elif atype == "R2A_CLAIM":
            if not str(authority.get("claim_id") or "").strip():
                blockers.append(f"R3_R2A_CLAIM_ID_MISSING:{item.get('constraint_id')}")
        else:
            blockers.append(f"R3_AUTHORITY_TYPE_INVALID:{item.get('constraint_id')}")
    target = item.get("target_reference")
    if isinstance(target, dict) and (
        not str(target.get("claim_id") or "").strip()
        or not str(target.get("anchor_id") or "").strip()
    ):
        blockers.append(f"R3_TARGET_IDENTITY_INCOMPLETE:{item.get('constraint_id')}")
    return blockers


def _forbidden_formal_fields(artifact: dict[str, Any]) -> list[str]:
    forbidden = {
        "status", "artifact_self_hash", "manifest_hash", "trusted_manifest_hash",
        "package_identity", "historical_verdict", "historical_pass", "post_writer_reviewer_pass",
    }
    return [f"R3_FORBIDDEN_FORMAL_FIELD:{key}" for key in forbidden if key in artifact]


def _validate_items(items: Any) -> list[str]:
    if not isinstance(items, list) or not items:
        return ["R3_ITEMS_MISSING"]
    blockers: list[str] = []
    ids: set[str] = set()
    for item in items:
        cid = str(item.get("constraint_id") or "") if isinstance(item, dict) else ""
        if not cid or cid in ids:
            blockers.append(f"R3_CONSTRAINT_ID_INVALID:{cid}")
        ids.add(cid)
        blockers.extend(_validate_machine_item(item))
    return blockers


def prepare_review_constraints_artifact(
    *,
    approved_source_path: str | Path,
    approval_input_path: str | Path,
    case_identity: str,
    run_id: str,
) -> dict[str, Any]:
    """Build a formal artifact from one source snapshot and one approval snapshot."""

    source_raw, source_sha = _read_bytes_once(Path(approved_source_path))
    source = _parse(source_raw)
    if source is None or source.get("schema_version") != "s0_r3_review_constraints_draft_v1":
        raise R3ValidationBlocked("R3_APPROVED_SOURCE_INVALID")
    approval_info = read_approval_input_once(approval_input_path)
    approval = approval_info["payload"]
    if approval.get("case_identity") != case_identity or approval.get("run_id") != run_id:
        raise R3ValidationBlocked("R3_APPROVAL_CASE_RUN_MISMATCH")
    if source.get("case_identity") != case_identity or source.get("run_id") != run_id:
        raise R3ValidationBlocked("R3_SOURCE_CASE_RUN_MISMATCH")
    if source.get("status") != "DRAFT_NOT_AUTHORITY":
        raise R3ValidationBlocked("R3_SOURCE_STATUS_INVALID")
    items = source.get("items")
    blockers = _validate_items(items)
    if blockers:
        raise R3ValidationBlocked(";".join(blockers))
    try:
        canonical = canonicalize_approved_constraint_payload(items)
    except R3ApprovalInterfaceBlocked as exc:
        raise R3ValidationBlocked(str(exc)) from exc
    canonical_sha = hashlib.sha256(canonical).hexdigest()
    if canonical_sha.lower() != str(approval["approved_constraint_set_sha256"]).lower():
        raise R3ValidationBlocked("R3_APPROVAL_HASH_MISMATCH")
    artifact = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "case_identity": case_identity,
        "run_id": run_id,
        "items": json.loads(json.dumps(items, ensure_ascii=False, allow_nan=False)),
    }
    return {
        "artifact": artifact,
        "source_sha256": source_sha,
        "canonical_sha256": canonical_sha,
        "approval": approval_info,
        "source_read_count": 1,
        "approved_input_read_count": approval_info["read_count"],
    }


def read_r3_required_flag(case_dir: str | Path) -> tuple[bool | None, list[str]]:
    try:
        config = json.loads((case_dir / "case_config.json").read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None, ["R3_REQUIRED_CONFIG_UNREADABLE"]
    if not isinstance(config, dict) or "r3_required" not in config:
        return None, ["R3_REQUIRED_FLAG_MISSING"]
    value = config.get("r3_required")
    if not isinstance(value, bool):
        return None, ["R3_REQUIRED_FLAG_INVALID"]
    return value, []


# Kept as a private compatibility alias for callers that imported the draft
# helper while R3 was still a design artifact.
_r3_required = read_r3_required_flag


def _current_enforcement_checks(
    items: list[dict[str, Any]],
    r4a: dict[str, Any] | None,
    r4b: dict[str, Any] | None,
    current_claim_handoff: dict[str, Any] | None = None,
) -> list[str]:
    blockers: list[str] = []
    if r4a is None or r4a.get("status") != "PASS":
        blockers.append("R3_CURRENT_R4A_VERDICT_NOT_PASS")
    if r4b is None or r4b.get("status") != "SEMANTIC_PASS":
        blockers.append("R3_CURRENT_R4B_VERDICT_NOT_PASS")
    for item in items:
        enforcement = item.get("enforcement_reference") or {}
        cid = str(item.get("constraint_id") or "")
        if enforcement.get("enforcement_type") == "R4A_CLAIM_ANCHOR":
            if r4a is not None and r4a.get("status") == "PASS":
                required = str(enforcement.get("claim_id") or "")
                claims = set(str(x) for x in (r4a.get("required_claim_ids") or []))
                if not claims and isinstance(current_claim_handoff, dict):
                    claims = {
                        str(claim.get("claim_id"))
                        for claim in (current_claim_handoff.get("claims") or [])
                        if isinstance(claim, dict) and claim.get("claim_id")
                    }
                if required and claims and required not in claims:
                    blockers.append(f"R3_R4A_CLAIM_ID_NOT_CURRENT:{cid}")
        elif enforcement.get("enforcement_type") == "R4B_STRUCTURED_CONSTRAINT":
            # R4B's SEMANTIC_PASS remains an effect verdict.  Identity is
            # resolved only against the trusted current R2A handoff, never
            # against the R4B evidence payload (which may repeat an
            # attacker-controlled symbol).
            required_claim_id = str(enforcement.get("claim_id") or "")
            required_type = str(enforcement.get("constraint_type") or "")
            required_symbol = str(enforcement.get("symbol") or "")
            if not required_claim_id or not required_type or not required_symbol:
                blockers.append("R3_R4B_CONSTRAINT_SYMBOL_MISSING")
                continue
            # Keep the independent R4B effect/coverage check: a semantic pass
            # must actually report this claim and constraint type.  The
            # symbol is deliberately excluded here; it is trusted only from
            # the current handoff identity below.
            evidence = r4b.get("evidence") if isinstance(r4b, dict) else None
            if isinstance(r4b, dict) and isinstance(r4b.get("structured_semantic_reentry"), dict):
                evidence = r4b["structured_semantic_reentry"].get("evidence")
            if not any(isinstance(entry, dict) and str(entry.get("claim_id") or "") == required_claim_id and str(entry.get("constraint") or "") == required_type for entry in evidence or []):
                blockers.append(f"R3_R4B_CONSTRAINT_ID_NOT_CURRENT:{cid}")
            approved_matches: list[dict[str, Any]] = []
            if isinstance(current_claim_handoff, dict):
                for claim in current_claim_handoff.get("claims") or []:
                    if not isinstance(claim, dict) or str(claim.get("claim_id") or "") != required_claim_id:
                        continue
                    constraints = claim.get("approved_constraints")
                    if not isinstance(constraints, list):
                        continue
                    for constraint in constraints:
                        if not isinstance(constraint, dict):
                            continue
                        if (str(constraint.get("constraint_type") or "") == required_type
                                and str(constraint.get("symbol") or "") == required_symbol):
                            approved_matches.append(constraint)
            if not approved_matches:
                blockers.append(f"R3_R4B_CONSTRAINT_IDENTITY_NOT_CURRENT:{cid}")
            elif len(approved_matches) > 1:
                blockers.append(f"R3_R4B_CONSTRAINT_IDENTITY_AMBIGUOUS:{cid}")
    return blockers


def validate_review_constraints_reentry(
    *,
    case_dir: str | Path,
    manifest_path: str | Path | None,
    approval_input_path: str | Path | None,
    current_r4a_verdict: dict[str, Any] | None,
    current_r4b_verdict: dict[str, Any] | None,
    current_claim_handoff: dict[str, Any] | None = None,
    repo_root: str | Path,
) -> dict[str, Any]:
    """Validate the manifest-bound formal artifact against current R4 verdicts."""

    case = Path(case_dir).resolve()
    root = Path(repo_root).resolve()
    required, flag_blockers = read_r3_required_flag(case)
    if required is None:
        return {"status": "R3_BLOCK", "blockers": flag_blockers, "read_count": 0, "current_verdict_consumed": False}
    if required is False:
        return {"status": "LEGACY_MANUAL_REVIEW_REQUIRED", "blockers": [], "read_count": 0, "current_verdict_consumed": False}
    blockers: list[str] = []
    if manifest_path is None:
        blockers.append("R3_MANIFEST_REQUIRED")
    if approval_input_path is None:
        blockers.append("R3_APPROVAL_INPUT_REQUIRED")
    manifest: dict[str, Any] | None = None
    artifact: dict[str, Any] | None = None
    manifest_path_obj = Path(manifest_path) if manifest_path is not None else None
    if manifest_path_obj is not None:
        try:
            manifest = json.loads(manifest_path_obj.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            blockers.append("R3_MANIFEST_UNREADABLE")
    actual_identity = _case_identity(case, root)
    if isinstance(manifest, dict):
        if manifest.get("case_identity") != actual_identity or manifest.get("case_dir") != actual_identity:
            blockers.append("R3_CASE_IDENTITY_MISMATCH")
        if not isinstance(manifest.get("run_id"), str) or not manifest.get("run_id"):
            blockers.append("R3_RUN_ID_MISSING")
        pointer = manifest.get("review_constraints")
        if not isinstance(pointer, dict) or pointer.get("path") != "review_constraints.json":
            blockers.append("R3_ARTIFACT_POINTER_MISSING")
        else:
            artifact_path = manifest_path_obj.parent / "review_constraints.json"
            raw, artifact_sha = _read_bytes_once(artifact_path)
            artifact = _parse(raw)
            if artifact_sha is None or artifact_sha.lower() != str(pointer.get("sha256") or "").lower():
                blockers.append("R3_ARTIFACT_HASH_MISMATCH")
            if pointer.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
                blockers.append("R3_ARTIFACT_SCHEMA_POINTER_INVALID")
    approval_info: dict[str, Any] | None = None
    if approval_input_path is not None:
        try:
            approval_info = read_approval_input_once(approval_input_path)
        except R3ValidationBlocked as exc:
            blockers.append(str(exc))
    if isinstance(manifest, dict) and isinstance(artifact, dict) and approval_info is not None:
        blockers.extend(_forbidden_formal_fields(artifact))
        approval = approval_info["payload"]
        if artifact.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
            blockers.append("R3_ARTIFACT_SCHEMA_INVALID")
        if artifact.get("case_identity") != manifest.get("case_identity") or artifact.get("run_id") != manifest.get("run_id"):
            blockers.append("R3_ARTIFACT_CASE_RUN_MISMATCH")
        if approval.get("case_identity") != artifact.get("case_identity") or approval.get("run_id") != artifact.get("run_id"):
            blockers.append("R3_APPROVAL_CASE_RUN_MISMATCH")
        try:
            canonical_sha = hashlib.sha256(canonicalize_approved_constraint_payload(artifact.get("items"))).hexdigest()
        except R3ApprovalInterfaceBlocked:
            canonical_sha = None
            blockers.append("R3_ARTIFACT_ITEMS_INVALID")
        if canonical_sha is None or canonical_sha.lower() != str(approval.get("approved_constraint_set_sha256") or "").lower():
            blockers.append("R3_APPROVAL_HASH_MISMATCH")
        blockers.extend(_validate_items(artifact.get("items")))
        expected_identity = manifest.get("case_identity")
        expected_run = manifest.get("run_id")
        expected_package = manifest.get("package_id")
        for label, verdict in (("R4A", current_r4a_verdict), ("R4B", current_r4b_verdict)):
            if not isinstance(verdict, dict):
                continue
            if expected_identity is not None and verdict.get("case_identity") not in {None, expected_identity}:
                blockers.append(f"R3_CURRENT_{label}_CASE_IDENTITY_MISMATCH")
            if expected_run is not None and verdict.get("run_id") not in {None, expected_run}:
                blockers.append(f"R3_CURRENT_{label}_RUN_ID_MISMATCH")
            if expected_package is not None and verdict.get("package_id") not in {None, expected_package}:
                blockers.append(f"R3_CURRENT_{label}_PACKAGE_ID_MISMATCH")
        for item in artifact.get("items") or []:
            if isinstance(item, dict) and item.get("review_mode") == "MANUAL_REVIEW_REQUIRED":
                blockers.append(f"R3_REVIEW_REQUIRED:{item.get('constraint_id')}")
        deterministic_items = [
            item for item in (artifact.get("items") or [])
            if isinstance(item, dict) and item.get("review_mode") == "DETERMINISTIC"
        ]
        blockers.extend(
            _current_enforcement_checks(
                deterministic_items,
                current_r4a_verdict,
                current_r4b_verdict,
                current_claim_handoff,
            )
        )
    status = "R3_PASS" if not blockers else "R3_BLOCK"
    if any(str(item).startswith("R3_REVIEW_REQUIRED") for item in blockers):
        status = "REVIEW_REQUIRED"
    return {
        "status": status,
        "blockers": list(dict.fromkeys(blockers)),
        "read_count": approval_info["read_count"] if approval_info else 0,
        "approved_input_read_once": approval_info is not None,
        "current_verdict_consumed": current_r4a_verdict is not None and current_r4b_verdict is not None,
        "historical_verdict_fallback": False,
        "artifact_schema_version": artifact.get("schema_version") if isinstance(artifact, dict) else None,
    }
