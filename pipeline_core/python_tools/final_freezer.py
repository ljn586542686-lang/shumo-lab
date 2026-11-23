"""Read-only final-freeze authorization predicate.

This module never creates, copies, moves, or populates ``final_submit/``.
P6-FF1-S1 accepts an explicitly supplied external authorization record, but
does not grant final-freeze readiness.  Candidate and post-writer bindings
remain fail-closed and are reported as explicit blockers.

P6-PV3 adds candidate-provenance binding: once the authorization record's
candidate manifest hash and package digest are independently revalidated,
this module also reads that manifest's own ``source_provenance_verification``
(written by package_candidate's P6-PV2A/-B finalization check) and requires
every one of its predicates to already be true, including that the
authorization record's own ``source_commit`` matches the commit already
bound inside the manifest. This module never reads a live Git HEAD and never
re-derives provenance itself — it only trusts what package_candidate already
verified at packaging time. final_freeze_ready and final_submit_allowed
remain False in this slice regardless of provenance validity; that gate is
reserved for the FF2 mock-authorization dry-run slice.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
import stat
from pathlib import Path
from typing import Any

from pipeline_core.python_tools.transition_case import (
    preflight_public_stage,
    validate_authoritative_state,
)

from pipeline_core.python_tools.post_writer_reentry import validate_post_writer_reentry
from pipeline_core.python_tools.structured_semantic_reentry import validate_structured_semantic_reentry
from pipeline_core.python_tools.r3_validator import validate_review_constraints_reentry
from pipeline_core.paper_quality.visual_reasoning_contract import validate_visual_reasoning_trace_report
from pipeline_core.paper_quality.argument_blueprint_contract import validate_argument_blueprint_trace_report


REQUIRED = [
    "output/validation/output_contract_report.md",
    "output/validation/evidence_trace_report.md",
    "output/validation/citation_audit_report.md",
    "ai_disclosure/AI工具使用详情.md",
    "human_review/gate_4_final_submission.md",
    "paper/main.pdf",
    "paper/visual_reasoning_trace_report.json",
]

DEFAULT_AUTHORIZATION_PATH = "human_review/final_submit_authorization.json"
AUTHORIZATION_SCHEMA_VERSION = "p6_ff_external_authorization_v1"
AUTHORIZATION_MODES = {"production", "mock_test"}
AUTHORIZATION_STATUSES = {"APPROVED", "REJECTED", "REVOKED"}
AUTHORIZATION_SCOPE = "final_freeze_readiness"
HASH_FIELDS = (
    "candidate_manifest_sha256",
    "candidate_package_digest",
    "run_result_sha256",
    "risk_summary_sha256",
    "result_summary_sha256",
    "paper_source_or_config_sha256",
    "route_review_sha256",
    "xval_artifact_sha256",
    "claim_check_artifact_sha256",
    "human_revision_artifact_sha256",
)
TEXT_FIELDS = (
    "schema_version",
    "authorization_mode",
    "authorization_status",
    "authorized_by_display_name",
    "authorization_record_ref",
    "authorized_at",
    "candidate_manifest_path",
    "source_commit",
    "authorization_scope",
    "not_automatic_submission_statement",
    "revocation_or_supersession_ref",
)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_bytes_once(path: Path) -> tuple[bytes | None, str | None]:
    """Read one immutable byte snapshot and hash that exact snapshot."""
    try:
        data = path.read_bytes()
    except OSError:
        return None, None
    return data, hashlib.sha256(data).hexdigest()


def _parse_json_bytes(data: bytes | None) -> dict[str, Any] | None:
    if data is None:
        return None
    try:
        payload = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _relative_case_path(case_dir: Path, configured_path: Any) -> Path | None:
    if not isinstance(configured_path, str) or not configured_path.strip():
        return None
    candidate = Path(configured_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    return case_dir / candidate


def _required_artifact_paths(case_dir: Path, config: dict[str, Any]) -> tuple[list[str], list[str]]:
    required = list(REQUIRED)
    configured = config.get("required_final_artifacts", [])
    invalid: list[str] = []
    if configured is None:
        configured = []
    if not isinstance(configured, list):
        invalid.append("case_config.required_final_artifacts (must be a list)")
        configured = []
    for item in configured:
        if not isinstance(item, str) or not item.strip():
            invalid.append("case_config.required_final_artifacts (contains invalid path)")
            continue
        if item not in required:
            required.append(item)

    missing = list(invalid)
    for item in required:
        path = _relative_case_path(case_dir, item)
        if path is None or not path.is_file() or path.stat().st_size <= 0:
            missing.append(item)
    return required, missing


def _has_human_gate_marker(gate_path: Path) -> bool:
    try:
        text = gate_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    upper = text.upper()
    forbidden_tokens = ("SELF_VERDICT", "AUTO_APPROVED", "AUTOMATIC APPROVAL", "WRITER_SIGNED")
    if any(token in upper for token in forbidden_tokens):
        return False
    approved = re.search(r"(?:HUMAN_GATE_STATUS|HUMAN_APPROVAL_STATUS)\s*[:=]\s*APPROVED\b", upper)
    approved_by = re.search(r"APPROVED_BY\s*[:=]\s*([^\r\n]+)", text, re.IGNORECASE)
    manual_confirmation = re.search(r"MANUAL_CONFIRMATION\s*[:=]\s*([^\r\n]+)", text, re.IGNORECASE)
    signer = approved_by.group(1).strip().lower() if approved_by else ""
    forbidden_signers = ("writer", "codex", "claude", "chatgpt", "ai")
    return bool(
        approved
        and approved_by
        and signer
        and not any(token in signer for token in forbidden_signers)
        and manual_confirmation
        and manual_confirmation.group(1).strip()
    )


def _has_valid_repo_authorization_record(case_dir: Path, config: dict[str, Any]) -> bool:
    """Validate a legacy repository-local record as format-only metadata."""
    configured_path = config.get("final_submit_authorization_path", DEFAULT_AUTHORIZATION_PATH)
    authorization_path = _relative_case_path(case_dir, configured_path)
    if authorization_path is None:
        return False
    record = _read_json(authorization_path)
    if record is None:
        return False
    return (
        record.get("authorization_status") == "AUTHORIZED"
        and record.get("source") == "human"
        and record.get("scope") == "final_submit"
        and bool(str(record.get("authorized_by") or "").strip())
        and bool(str(record.get("authorized_at") or "").strip())
        and bool(str(record.get("manual_confirmation") or "").strip())
    )


def _is_candidate_package(case_dir: Path) -> bool:
    manifest = _read_json(case_dir / "manifest.json")
    if manifest is not None:
        status = str(manifest.get("status") or "").lower()
        if "candidate" in status or manifest.get("for_official_submission") is False:
            return True
    return any(part in {"candidate_submit", "candidate_emergency"} for part in case_dir.parts)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _normalized_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _canonical_case_identity(case_dir: Path, root: Path) -> str | None:
    """Return the stable repo-relative identity shared with packaging."""
    try:
        relative = case_dir.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    value = relative.as_posix()
    return value if value and value != "." else None


def _validated_explicit_candidate_manifest(path: str | Path | None) -> tuple[Path | None, Path | None]:
    if path is None:
        return None, None
    raw = Path(path).expanduser()
    if not raw.is_absolute() or ".." in raw.parts or raw.name != "manifest.json":
        return None, None
    try:
        raw_stat = raw.lstat()
        if stat.S_ISLNK(raw_stat.st_mode):
            return None, None
        manifest_path = raw.resolve(strict=True)
        package_root = manifest_path.parent
        package_stat = package_root.lstat()
        if stat.S_ISLNK(package_stat.st_mode) or not manifest_path.is_file() or not package_root.is_dir():
            return None, None
        if manifest_path.relative_to(package_root).as_posix() != "manifest.json":
            return None, None
    except (OSError, ValueError):
        return None, None
    return manifest_path, package_root


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _calculate_candidate_package_digest(candidate_package: Path) -> str | None:
    """Return a canonical digest of regular files in one candidate package."""
    try:
        root_stat = candidate_package.lstat()
        if stat.S_ISLNK(root_stat.st_mode):
            return None
        root = candidate_package.resolve(strict=True)
        if not root.is_dir():
            return None
        entries: list[dict[str, str]] = []
        for path in root.rglob("*"):
            path_stat = path.lstat()
            if stat.S_ISLNK(path_stat.st_mode):
                return None
            if stat.S_ISDIR(path_stat.st_mode):
                continue
            if not stat.S_ISREG(path_stat.st_mode):
                return None
            entries.append(
                {
                    "relative_path": path.relative_to(root).as_posix(),
                    "sha256": _sha256_file(path),
                }
            )
    except OSError:
        return None
    entries.sort(key=lambda item: item["relative_path"])
    canonical = json.dumps(entries, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validated_candidate_manifest_and_package_root(record: dict[str, Any]) -> tuple[Path | None, Path | None]:
    configured_path = record.get("candidate_manifest_path")
    if not isinstance(configured_path, str) or not configured_path.strip():
        return None, None
    raw_manifest_path = Path(configured_path).expanduser()
    if not raw_manifest_path.is_absolute() or ".." in raw_manifest_path.parts or raw_manifest_path.name != "manifest.json":
        return None, None
    try:
        raw_manifest_stat = raw_manifest_path.lstat()
        if stat.S_ISLNK(raw_manifest_stat.st_mode):
            return None, None
        manifest_path = raw_manifest_path.resolve(strict=True)
        candidate_package = manifest_path.parent
        package_stat = candidate_package.lstat()
        if stat.S_ISLNK(package_stat.st_mode) or not manifest_path.is_file() or not candidate_package.is_dir():
            return None, None
        if manifest_path.relative_to(candidate_package).as_posix() != "manifest.json":
            return None, None
    except (OSError, ValueError):
        return None, None
    return manifest_path, candidate_package


def _candidate_package_root_containing_path(resolved_path: Path) -> Path | None:
    """Return the resolved candidate root containing ``resolved_path``, if any."""
    current = resolved_path if resolved_path.is_dir() else resolved_path.parent
    while True:
        manifest_path = current / "manifest.json"
        try:
            manifest_stat = manifest_path.lstat()
            if not stat.S_ISLNK(manifest_stat.st_mode):
                resolved_manifest = manifest_path.resolve(strict=True)
                candidate_package = resolved_manifest.parent
                package_stat = candidate_package.lstat()
                if (
                    not stat.S_ISLNK(package_stat.st_mode)
                    and candidate_package.is_dir()
                    and resolved_manifest.is_file()
                    and resolved_manifest.relative_to(candidate_package).as_posix() == "manifest.json"
                ):
                    return candidate_package
        except (OSError, ValueError):
            pass
        if current.parent == current:
            return None
        current = current.parent


def _revalidate_candidate_package_bindings(
    record: dict[str, Any],
    manifest_path: Path | None = None,
    candidate_package: Path | None = None,
    manifest_sha256: str | None = None,
) -> tuple[str | None, str | None, bool]:
    if manifest_path is None or candidate_package is None:
        manifest_path, candidate_package = _validated_candidate_manifest_and_package_root(record)
    if manifest_path is None or candidate_package is None:
        return None, None, False
    if manifest_sha256 is None:
        try:
            manifest_sha256 = _sha256_file(manifest_path)
        except OSError:
            return None, None, False
    package_digest = _calculate_candidate_package_digest(candidate_package)
    if package_digest is None:
        return manifest_sha256, None, False
    valid = (
        manifest_sha256.lower() == str(record.get("candidate_manifest_sha256") or "").lower()
        and package_digest.lower() == str(record.get("candidate_package_digest") or "").lower()
    )
    return manifest_sha256, package_digest, valid


def _is_valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value) is not None


def _is_valid_commit(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{7,40}", value) is not None


def _is_valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _preserves_manual_submission_boundary(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.lower()
    return "not automatically" in text and "submit" in text and "final_submit" in text


def _validate_candidate_manifest_provenance(
    manifest_path: Path,
    record: dict[str, Any],
    manifest: dict[str, Any] | None = None,
) -> tuple[list[str], bool, dict[str, Any] | None]:
    """Trust only the packaging-time provenance already recorded in the
    candidate manifest (written by package_candidate's P6-PV2A/-B
    finalization check). Never re-derive it from a live Git HEAD here —
    final_freezer must bind to the exact snapshot package_candidate already
    verified, not re-run its own verification against whatever the
    worktree happens to be at freeze time.

    Returns (blockers, valid, verification_dict_or_None). ``valid`` is
    True only when every predicate below holds; any single failure is
    reported as the general FINAL_CANDIDATE_PROVENANCE_INVALID blocker
    plus a more specific blocker naming which predicate failed.
    """
    blockers: list[str] = []
    manifest = manifest if manifest is not None else _read_json(manifest_path)
    if manifest is None:
        return ["FINAL_CANDIDATE_PROVENANCE_INVALID", "FINAL_CANDIDATE_PROVENANCE_MANIFEST_UNREADABLE"], False, None

    verification = manifest.get("source_provenance_verification")
    if not isinstance(verification, dict):
        return ["FINAL_CANDIDATE_PROVENANCE_INVALID", "FINAL_CANDIDATE_PROVENANCE_VERIFICATION_MISSING"], False, None

    if verification.get("source_provenance_required") is not True:
        blockers.append("FINAL_CANDIDATE_PROVENANCE_NOT_REQUIRED")
    if verification.get("source_provenance_applicable") is not True:
        blockers.append("FINAL_CANDIDATE_PROVENANCE_NOT_APPLICABLE")
    if verification.get("source_provenance_legacy_compatibility") is not False:
        # Training/legacy/non-applicable candidates must never reach
        # production final-freeze, regardless of any other field.
        blockers.append("FINAL_CANDIDATE_PROVENANCE_LEGACY_NOT_ALLOWED")
    if verification.get("source_provenance_finalization_passed") is not True:
        blockers.append("FINAL_CANDIDATE_PROVENANCE_FINALIZATION_FAILED")
    if verification.get("post_snapshot_verification_passed") is not True:
        blockers.append("FINAL_CANDIDATE_PROVENANCE_POST_SNAPSHOT_FAILED")
    if verification.get("packaging_head_matches_source_commit") is not True:
        blockers.append("FINAL_CANDIDATE_PROVENANCE_HEAD_MISMATCH")
    if verification.get("packaging_git_operation_in_progress") is not False:
        blockers.append("FINAL_CANDIDATE_PROVENANCE_GIT_OPERATION_IN_PROGRESS")
    unexpected_entries = verification.get("packaging_unexpected_status_entries")
    if not isinstance(unexpected_entries, list) or unexpected_entries:
        blockers.append("FINAL_CANDIDATE_PROVENANCE_UNEXPECTED_WORKTREE_CHANGE")
    manifest_blockers = verification.get("blockers")
    if not isinstance(manifest_blockers, list) or manifest_blockers:
        blockers.append("FINAL_CANDIDATE_PROVENANCE_BLOCKERS_PRESENT")
    if not _is_valid_sha256(verification.get("source_provenance_sha256")):
        blockers.append("FINAL_CANDIDATE_PROVENANCE_HASH_INVALID")

    manifest_source_commit = verification.get("source_commit")
    if not _is_valid_commit(manifest_source_commit):
        blockers.append("FINAL_CANDIDATE_PROVENANCE_COMMIT_INVALID")
    else:
        # Requirement 3: the authorization record's own source_commit must
        # equal the commit already bound inside the candidate manifest —
        # never substitute a live HEAD read for this comparison.
        authorization_source_commit = record.get("source_commit")
        if (
            not isinstance(authorization_source_commit, str)
            or authorization_source_commit.strip().lower() != str(manifest_source_commit).strip().lower()
        ):
            blockers.append("FINAL_CANDIDATE_PROVENANCE_COMMIT_MISMATCH")

    if blockers:
        blockers = ["FINAL_CANDIDATE_PROVENANCE_INVALID"] + blockers
    unique_blockers = list(dict.fromkeys(blockers))
    return unique_blockers, not unique_blockers, verification


_UPSTREAM_BINDING_ARTIFACT_NAMES = (
    "run_result",
    "risk_summary",
    "result_summary",
    "route_review",
    "xval",
    "claim_check",
    "human_revision",
)


def _validate_candidate_manifest_upstream_bindings(
    manifest_path: Path,
    manifest: dict[str, Any] | None = None,
) -> tuple[list[str], bool, dict[str, Any] | None]:
    """Trust only the candidate manifest's already-completed P6-S2B
    upstream_artifact_bindings (hash + parsed semantic status per
    applicable artifact) — never re-derive hashes or re-parse artifact
    status here. Any artifact package_candidate marked applicable but
    invalid (semantic_valid is not True) fails the whole binding, matching
    S2B's own "strictest wins" aggregation.

    Returns (blockers, valid, bindings_dict_or_None).
    """
    manifest = manifest if manifest is not None else _read_json(manifest_path)
    if manifest is None:
        return ["FINAL_CANDIDATE_UPSTREAM_BINDING_INVALID", "FINAL_CANDIDATE_UPSTREAM_BINDING_MANIFEST_UNREADABLE"], False, None

    bindings = manifest.get("upstream_artifact_bindings")
    if not isinstance(bindings, dict) or not bindings:
        return ["FINAL_CANDIDATE_UPSTREAM_BINDING_INVALID", "FINAL_CANDIDATE_UPSTREAM_BINDING_MISSING"], False, None

    blockers: list[str] = []
    for name in _UPSTREAM_BINDING_ARTIFACT_NAMES:
        entry = bindings.get(name)
        if not isinstance(entry, dict):
            blockers.append(f"FINAL_CANDIDATE_UPSTREAM_BINDING_{name.upper()}_MISSING")
            continue
        applicable = entry.get("applicable") is True
        # semantic_valid is not True still independently blocks regardless
        # of what semantic_status says — this is unchanged from before.
        semantic_valid = entry.get("semantic_valid") is True
        if not semantic_valid:
            blockers.append(f"FINAL_CANDIDATE_UPSTREAM_BINDING_{name.upper()}_INVALID")

        # Defense in depth: never trust semantic_valid alone. Independently
        # normalize and check semantic_status against an explicit allow
        # set — a manifest where semantic_valid was miscomputed (bug or
        # tampering) but semantic_status still honestly says BLOCK/FATAL/
        # etc. must still be caught here, not waved through because the
        # boolean happened to be True.
        raw_status = entry.get("semantic_status")
        normalized_status = raw_status.strip().upper() if isinstance(raw_status, str) else None
        if applicable:
            # A required/applicable artifact must be an affirmative PASS —
            # SKIPPED/NOT_APPLICABLE are only legitimate for artifacts the
            # manifest itself marked not applicable, never for one it
            # marked applicable=true.
            if normalized_status != "PASS":
                blockers.append(f"FINAL_CANDIDATE_UPSTREAM_BINDING_{name.upper()}_SEMANTIC_STATUS_INVALID")
            if semantic_valid and (
                not isinstance(entry.get("sha256"), str) or not _is_valid_sha256(entry.get("sha256"))
            ):
                blockers.append(f"FINAL_CANDIDATE_UPSTREAM_BINDING_{name.upper()}_HASH_INVALID")
        else:
            if normalized_status not in {"SKIPPED", "NOT_APPLICABLE"}:
                blockers.append(f"FINAL_CANDIDATE_UPSTREAM_BINDING_{name.upper()}_SEMANTIC_STATUS_INVALID")

    if blockers:
        blockers = ["FINAL_CANDIDATE_UPSTREAM_BINDING_INVALID"] + blockers
    unique_blockers = list(dict.fromkeys(blockers))
    return unique_blockers, not unique_blockers, bindings


def _validate_index_payload(index: dict[str, Any] | None, case_dir: Path) -> list[str]:
    """Validate a canonical index already parsed from its hashed bytes."""
    if index is None:
        return ["CANONICAL_INDEX_UNREADABLE"]
    if index.get("schema_version") != "x5_f0_canonical_result_index_v1":
        return ["CANONICAL_INDEX_SCHEMA_INVALID"]
    if index.get("status") != "PASS":
        return ["CANONICAL_INDEX_LEGACY_UNAVAILABLE"]
    entries = index.get("entries")
    if not isinstance(entries, list) or not entries:
        return ["CANONICAL_INDEX_ENTRIES_MISSING"]
    seen: set[tuple[Any, Any]] = set()
    blockers: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            blockers.append("CANONICAL_INDEX_ENTRY_INVALID")
            continue
        scope = (entry.get("question_id"), entry.get("artifact_role"))
        if scope in seen:
            blockers.append(f"CANONICAL_AUTHORITY_DUPLICATE:{scope[0]}:{scope[1]}")
        seen.add(scope)
        path_text = entry.get("artifact_path")
        if not isinstance(path_text, str) or Path(path_text).is_absolute() or ".." in Path(path_text).parts:
            blockers.append(f"CANONICAL_ARTIFACT_PATH_INVALID:{scope[0]}:{scope[1]}")
            continue
        artifact = (case_dir / path_text).resolve()
        expected = entry.get("sha256")
        if not artifact.is_file() or not isinstance(expected, str):
            blockers.append(f"CANONICAL_ARTIFACT_MISSING_OR_HASH_MISSING:{scope[0]}:{scope[1]}")
        elif _sha256_file(artifact) != expected:
            blockers.append(f"CANONICAL_HASH_MISMATCH:{scope[0]}:{scope[1]}")
    return list(dict.fromkeys(blockers))


def _validate_candidate_manifest_authoritative_index(
    manifest_path: Path,
    manifest: dict[str, Any] | None = None,
    index_data: dict[str, Any] | None = None,
    case_dir_context: Path | None = None,
) -> tuple[list[str], bool, dict[str, Any] | None]:
    """Validate the candidate manifest's canonical result pointer set.

    The index owns scope uniqueness and source-artifact hash binding. Payload
    files remain question-specific; this check never merges or rewrites them.
    """
    manifest = manifest if manifest is not None else _read_json(manifest_path)
    if manifest is None:
        return ["CANONICAL_INDEX_MANIFEST_UNREADABLE"], False, None
    pointer = manifest.get("authoritative_result_index")
    if not isinstance(pointer, dict) or pointer.get("path") != "authoritative_result_index.json":
        return ["CANONICAL_INDEX_POINTER_MISSING"], False, None
    index_path = manifest_path.parent / "authoritative_result_index.json"
    case_dir_value = manifest.get("case_dir")
    if not isinstance(case_dir_value, str) or not case_dir_value.strip():
        return ["CANONICAL_INDEX_CASE_DIR_MISSING"], False, None
    case_dir = case_dir_context if case_dir_context is not None else Path(case_dir_value)
    if case_dir_context is None and not case_dir.is_absolute():
        case_dir = Path(__file__).resolve().parents[2] / case_dir
    index = index_data if index_data is not None else _read_json(index_path)
    blockers = _validate_index_payload(index, case_dir)
    return blockers, not blockers, index


def _validate_authorization_contract(record: dict[str, Any], requested_mode: str, dry_run: bool) -> tuple[list[str], bool]:
    blockers: list[str] = []
    for field in TEXT_FIELDS:
        if not isinstance(record.get(field), str) or not record[field].strip():
            blockers.append("FINAL_AUTH_INVALID")
    for field in HASH_FIELDS:
        if not _is_valid_sha256(record.get(field)):
            blockers.append("FINAL_AUTH_INVALID")
    if record.get("schema_version") != AUTHORIZATION_SCHEMA_VERSION:
        blockers.append("FINAL_AUTH_INVALID")
    mode = record.get("authorization_mode")
    if mode not in AUTHORIZATION_MODES:
        blockers.append("FINAL_AUTH_INVALID")
    if requested_mode not in AUTHORIZATION_MODES:
        blockers.append("FINAL_AUTH_INVALID")
    if mode == "mock_test" and requested_mode == "production":
        blockers.append("FINAL_AUTH_MOCK_NOT_ALLOWED")
    elif mode != requested_mode:
        blockers.append("FINAL_AUTH_INVALID")
    if requested_mode == "mock_test" and not dry_run:
        blockers.append("FINAL_AUTH_INVALID")
    status = record.get("authorization_status")
    if status not in AUTHORIZATION_STATUSES:
        blockers.append("FINAL_AUTH_INVALID")
    elif status == "REVOKED":
        blockers.append("FINAL_AUTH_REVOKED")
    elif status == "REJECTED":
        blockers.append("FINAL_AUTH_REJECTED")
    if record.get("authorization_scope") != AUTHORIZATION_SCOPE:
        blockers.append("FINAL_AUTH_SCOPE_MISMATCH")
    if not _is_valid_timestamp(record.get("authorized_at")):
        blockers.append("FINAL_AUTH_INVALID")
    if not _is_valid_commit(record.get("source_commit")):
        blockers.append("FINAL_AUTH_INVALID")
    if not _preserves_manual_submission_boundary(record.get("not_automatic_submission_statement")):
        blockers.append("FINAL_AUTH_INVALID")
    unique_blockers = list(dict.fromkeys(blockers))
    return unique_blockers, not unique_blockers


def _base_machine_blockers(case_dir: Path, config: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    blockers: list[str] = []
    if _is_candidate_package(case_dir):
        blockers.append("CANDIDATE_PACKAGE_ONLY")
    if config.get("official_submission_mode") != "enabled":
        blockers.append("OFFICIAL_SUBMISSION_LOCKED")
    if config.get("final_submit_allowed") is not True:
        blockers.append("FINAL_SUBMIT_NOT_ALLOWED")
    if config.get("citation_ready") is not True:
        blockers.append("CITATION_NOT_READY")
    if config.get("formal_reference_allowed") is not True:
        blockers.append("FORMAL_REFERENCE_NOT_ALLOWED")
    gate_path = case_dir / "human_review" / "gate_4_final_submission.md"
    if config.get("human_gate_status") != "APPROVED" or not gate_path.exists():
        blockers.append("MISSING_HUMAN_GATE")
    elif not _has_human_gate_marker(gate_path):
        blockers.append("HUMAN_GATE_UNVERIFIABLE")
    required_artifacts, missing_artifacts = _required_artifact_paths(case_dir, config)
    if missing_artifacts:
        blockers.append("MISSING_REQUIRED_ARTIFACTS")
    visual_reasoning = _read_json(case_dir / "paper" / "visual_reasoning_trace_report.json")
    visual_reasoning_check = validate_visual_reasoning_trace_report(
        visual_reasoning or {}, case_dir / "paper"
    )
    if visual_reasoning_check.get("status") != "PASS":
        blockers.append("VISUAL_REASONING_TRACE_NOT_PASSED")
    if config.get("argument_blueprint_required") is True:
        argument_blueprint = _read_json(case_dir / "paper" / "argument_blueprint_trace_report.json")
        argument_blueprint_check = validate_argument_blueprint_trace_report(
            argument_blueprint or {}, case_dir / "paper"
        )
        if argument_blueprint_check.get("status") != "PASS":
            blockers.append("ARGUMENT_BLUEPRINT_TRACE_NOT_PASSED")
    return blockers, required_artifacts, missing_artifacts


def check_freeze_ready(
    case_dir: str | Path,
    *,
    authorization_path: str | Path | None = None,
    candidate_manifest_path: str | Path | None = None,
    expected_candidate_manifest_sha256: str | None = None,
    expected_run_id: str | None = None,
    requested_mode: str = "production",
    dry_run: bool = False,
    repo_root: str | Path | None = None,
    post_writer_reentry_path: str | Path | None = None,
    require_post_writer_reentry: bool = True,
    structured_semantic_reentry_path: str | Path | None = None,
    require_structured_semantic_reentry: bool = False,
    r3_approval_input_path: str | Path | None = None,
) -> dict[str, Any]:
    """Inspect final-freeze prerequisites without any filesystem side effects.

    ``candidate_manifest_path`` and its external expected hash are the human
    trust channel.  Authorization and post-writer records are assertions only;
    they cannot select a candidate package or establish its authority.
    """
    case_dir = _normalized_path(case_dir)
    root = _normalized_path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
    config = _read_json(case_dir / "case_config.json") or {}
    blockers, required_artifacts, missing_artifacts = _base_machine_blockers(case_dir, config)
    workflow_state = validate_authoritative_state(case_dir, config)
    if workflow_state.get("status") == "FAIL":
        blockers.append("WORKFLOW_STATE_INVALID")
    elif workflow_state.get("status") == "PASS":
        freeze_preflight = preflight_public_stage("freeze", workflow_state)
        if freeze_preflight.get("status") != "PASS":
            blockers.append("FINAL_FREEZE_STAGE_NOT_ALLOWED")
    warnings: list[str] = []
    legacy_record_valid = _has_valid_repo_authorization_record(case_dir, config)

    actual_case_identity = _canonical_case_identity(case_dir, root)
    if actual_case_identity is None:
        blockers.append("CASE_IDENTITY_UNAVAILABLE")

    trusted_manifest_path, trusted_candidate_package = _validated_explicit_candidate_manifest(candidate_manifest_path)
    trusted_manifest_bytes: bytes | None = None
    trusted_manifest: dict[str, Any] | None = None
    trusted_manifest_sha256: str | None = None
    trusted_index: dict[str, Any] | None = None
    trusted_handoff: dict[str, Any] | None = None
    trusted_index_path: Path | None = None
    trusted_handoff_path: Path | None = None
    if candidate_manifest_path is None or expected_candidate_manifest_sha256 is None:
        blockers.append("TRUSTED_CANDIDATE_ROOT_REQUIRED")
    elif trusted_manifest_path is None or trusted_candidate_package is None:
        blockers.append("TRUSTED_CANDIDATE_ROOT_INVALID")
    elif not _is_valid_sha256(expected_candidate_manifest_sha256):
        blockers.append("CANDIDATE_MANIFEST_HASH_INVALID")
    else:
        trusted_manifest_bytes, trusted_manifest_sha256 = _read_bytes_once(trusted_manifest_path)
        if trusted_manifest_sha256 is None:
            blockers.append("CANDIDATE_MANIFEST_UNREADABLE")
        elif trusted_manifest_sha256.lower() != expected_candidate_manifest_sha256.lower():
            blockers.append("CANDIDATE_MANIFEST_HASH_MISMATCH")
        else:
            trusted_manifest = _parse_json_bytes(trusted_manifest_bytes)
            if trusted_manifest is None:
                blockers.append("CANDIDATE_MANIFEST_UNREADABLE")
            else:
                manifest_identity = trusted_manifest.get("case_identity")
                if not isinstance(manifest_identity, str) or not manifest_identity.strip():
                    blockers.append("CANDIDATE_CASE_IDENTITY_MISSING")
                elif actual_case_identity is not None and manifest_identity != actual_case_identity:
                    blockers.append("CANDIDATE_CASE_IDENTITY_MISMATCH")
                manifest_case_dir = trusted_manifest.get("case_dir")
                if actual_case_identity is not None and manifest_case_dir != actual_case_identity:
                    blockers.append("CANDIDATE_CASE_IDENTITY_MISMATCH")
                manifest_run_id = trusted_manifest.get("run_id")
                if not isinstance(manifest_run_id, str) or not manifest_run_id.strip():
                    blockers.append("CANDIDATE_RUN_ID_MISSING")
                elif expected_run_id is not None and manifest_run_id != expected_run_id:
                    blockers.append("CANDIDATE_RUN_ID_MISMATCH")

                index_pointer = trusted_manifest.get("authoritative_result_index")
                if isinstance(index_pointer, dict) and index_pointer.get("path") == "authoritative_result_index.json":
                    trusted_index_path = trusted_manifest_path.parent / "authoritative_result_index.json"
                    index_bytes, index_sha256 = _read_bytes_once(trusted_index_path)
                    expected_index_sha256 = index_pointer.get("sha256")
                    if index_sha256 is None or not _is_valid_sha256(expected_index_sha256):
                        blockers.append("R1_INDEX_PROVENANCE_MISSING")
                    elif index_sha256.lower() != expected_index_sha256.lower():
                        blockers.append("R1_INDEX_PROVENANCE_MISMATCH")
                    else:
                        trusted_index = _parse_json_bytes(index_bytes)
                else:
                    blockers.append("R1_INDEX_PROVENANCE_MISSING")

                handoff_pointer = trusted_manifest.get("claim_handoff")
                if isinstance(handoff_pointer, dict) and handoff_pointer.get("path") == "claim_handoff.json":
                    trusted_handoff_path = trusted_manifest_path.parent / "claim_handoff.json"
                    handoff_bytes, handoff_sha256 = _read_bytes_once(trusted_handoff_path)
                    expected_handoff_sha256 = handoff_pointer.get("sha256")
                    if handoff_sha256 is None or not _is_valid_sha256(expected_handoff_sha256):
                        blockers.append("R2A_HANDOFF_PROVENANCE_MISSING")
                    elif handoff_sha256.lower() != expected_handoff_sha256.lower():
                        blockers.append("R2A_HANDOFF_PROVENANCE_MISMATCH")
                    else:
                        trusted_handoff = _parse_json_bytes(handoff_bytes)
                else:
                    blockers.append("R2A_HANDOFF_PROVENANCE_MISSING")

    trust_prerequisite_blockers = {
        "CASE_IDENTITY_UNAVAILABLE",
        "TRUSTED_CANDIDATE_ROOT_REQUIRED",
        "TRUSTED_CANDIDATE_ROOT_INVALID",
        "CANDIDATE_MANIFEST_HASH_INVALID",
        "CANDIDATE_MANIFEST_UNREADABLE",
        "CANDIDATE_MANIFEST_HASH_MISMATCH",
        "CANDIDATE_CASE_IDENTITY_MISSING",
        "CANDIDATE_CASE_IDENTITY_MISMATCH",
        "CANDIDATE_RUN_ID_MISSING",
        "CANDIDATE_RUN_ID_MISMATCH",
        "R1_INDEX_PROVENANCE_MISSING",
        "R1_INDEX_PROVENANCE_MISMATCH",
        "R2A_HANDOFF_PROVENANCE_MISSING",
        "R2A_HANDOFF_PROVENANCE_MISMATCH",
    }
    post_writer_trust_prerequisites_valid = (
        actual_case_identity is not None
        and trusted_manifest_path is not None
        and trusted_candidate_package is not None
        and trusted_manifest is not None
        and trusted_manifest_sha256 is not None
        and _is_valid_sha256(expected_candidate_manifest_sha256)
        and trusted_manifest_sha256.lower() == expected_candidate_manifest_sha256.lower()
        and trusted_index_path is not None
        and trusted_index is not None
        and trusted_handoff_path is not None
        and trusted_handoff is not None
        and not any(blocker in trust_prerequisite_blockers for blocker in blockers)
    )

    record: dict[str, Any] | None = None
    authorization_external = False
    authorization_contract_valid = False
    authorization_mode: str | None = None
    authorization_status: str | None = None
    calculated_candidate_manifest_sha256: str | None = None
    calculated_candidate_package_digest: str | None = None
    calculated_candidate_package_root: str | None = None
    authorization_resolved_path: str | None = None
    authorization_outside_candidate_package = False
    candidate_provenance_valid = False
    candidate_provenance_verification: dict[str, Any] | None = None
    candidate_upstream_bindings_valid = False
    candidate_upstream_bindings: dict[str, Any] | None = None
    candidate_authoritative_index_valid = False
    candidate_authoritative_index: dict[str, Any] | None = None
    post_writer_reentry: dict[str, Any] | None = None
    post_writer_reentry_status: str | None = None
    structured_semantic_reentry: dict[str, Any] | None = None
    structured_semantic_reentry_status: str | None = None
    r3_validation: dict[str, Any] | None = None
    r3_status: str | None = None

    if not post_writer_trust_prerequisites_valid:
        blockers.append("POST_WRITER_REENTRY_TRUST_PREREQUISITE_FAILED")
        post_writer_reentry_status = "NOT_EVALUATED_TRUST_PREREQUISITE_FAILED"
    elif post_writer_reentry_path is None:
        if require_post_writer_reentry:
            blockers.append("POST_WRITER_REENTRY_REQUIRED")
            post_writer_reentry_status = "NOT_EVALUATED_REENTRY_MISSING"
        else:
            blockers.append("EXPLICIT_DIAGNOSTIC_OPT_OUT")
            post_writer_reentry_status = "NOT_EVALUATED_DIAGNOSTIC_OPT_OUT"
    else:
        post_writer_reentry = validate_post_writer_reentry(
            post_writer_reentry_path,
            repo_root=root,
            trusted_index_path=trusted_index_path,
            trusted_index_sha256=(trusted_manifest.get("authoritative_result_index", {}).get("sha256") if isinstance(trusted_manifest, dict) else None),
            trusted_handoff_path=trusted_handoff_path,
            trusted_handoff_sha256=(trusted_manifest.get("claim_handoff", {}).get("sha256") if isinstance(trusted_manifest, dict) else None),
        )
        post_writer_reentry_status = post_writer_reentry.get("status")
        if post_writer_reentry.get("status") != "PASS":
            blockers.extend(post_writer_reentry.get("blockers") or ["POST_WRITER_REENTRY_BLOCKED"])
        else:
            if structured_semantic_reentry_path is None:
                structured_semantic_reentry_status = "NOT_EVALUATED_REENTRY_MISSING"
                if require_structured_semantic_reentry:
                    blockers.append("R4B_STRUCTURED_SEMANTIC_REENTRY_REQUIRED")
            else:
                structured_semantic_reentry = validate_structured_semantic_reentry(
                    structured_semantic_reentry_path,
                    repo_root=root,
                    trusted_index_path=trusted_index_path,
                    trusted_index_sha256=(trusted_manifest.get("authoritative_result_index", {}).get("sha256") if isinstance(trusted_manifest, dict) else None),
                    trusted_handoff_path=trusted_handoff_path,
                    trusted_handoff_sha256=(trusted_manifest.get("claim_handoff", {}).get("sha256") if isinstance(trusted_manifest, dict) else None),
                )
                structured_semantic_reentry_status = structured_semantic_reentry.get("status")
                if structured_semantic_reentry_status in {"SEMANTIC_BLOCK", "REVIEW_REQUIRED"}:
                    blockers.extend(structured_semantic_reentry.get("blockers") or structured_semantic_reentry.get("review_items") or ["R4B_STRUCTURED_SEMANTIC_BLOCKED"])

    # R3 is deliberately evaluated after the current R4A/R4B verdicts.  The
    # validator owns the missing/non-bool flag contract and therefore no
    # missing-key legacy bypass is allowed here.
    r3_r4a_current = dict(post_writer_reentry or {})
    r3_r4b_current = dict(structured_semantic_reentry or {})
    current_manifest_run = trusted_manifest.get("run_id") if isinstance(trusted_manifest, dict) else None
    current_package_id = trusted_manifest.get("package_id") if isinstance(trusted_manifest, dict) else None
    for verdict in (r3_r4a_current, r3_r4b_current):
        verdict.update({
            "case_identity": actual_case_identity,
            "run_id": current_manifest_run,
            "package_id": current_package_id,
        })
    r3_validation = validate_review_constraints_reentry(
        case_dir=case_dir,
        manifest_path=trusted_manifest_path,
        approval_input_path=r3_approval_input_path,
        current_r4a_verdict=r3_r4a_current if post_writer_reentry is not None else None,
        current_r4b_verdict=r3_r4b_current if structured_semantic_reentry is not None else None,
        current_claim_handoff=trusted_handoff,
        repo_root=root,
    )
    r3_status = r3_validation.get("status") if isinstance(r3_validation, dict) else None
    if r3_status in {"R3_BLOCK", "REVIEW_REQUIRED", "LEGACY_MANUAL_REVIEW_REQUIRED"}:
        blockers.extend((r3_validation or {}).get("blockers") or [f"R3_{r3_status}"])

    if authorization_path is None:
        blockers.append("FINAL_AUTH_MISSING")
    else:
        resolved_authorization_path = _normalized_path(authorization_path)
        authorization_resolved_path = str(resolved_authorization_path)
        containing_candidate_package = _candidate_package_root_containing_path(resolved_authorization_path)
        if containing_candidate_package is not None:
            calculated_candidate_package_root = str(containing_candidate_package)
            authorization_outside_candidate_package = False
        elif trusted_candidate_package is not None:
            calculated_candidate_package_root = str(trusted_candidate_package)
            authorization_outside_candidate_package = not _is_within(
                resolved_authorization_path, trusted_candidate_package
            )
        else:
            authorization_outside_candidate_package = True
        if not authorization_outside_candidate_package:
            blockers.append("FINAL_AUTH_INSIDE_CANDIDATE_PACKAGE")
        elif _is_within(resolved_authorization_path, root):
            blockers.append("FINAL_AUTH_NOT_EXTERNAL")
        elif not resolved_authorization_path.is_file():
            blockers.append("FINAL_AUTH_INVALID")
        else:
            record = _read_json(resolved_authorization_path)
            if record is None:
                blockers.append("FINAL_AUTH_INVALID")
            else:
                authorization_external = True
                authorization_mode = record.get("authorization_mode") if isinstance(record.get("authorization_mode"), str) else None
                authorization_status = record.get("authorization_status") if isinstance(record.get("authorization_status"), str) else None
                contract_blockers, authorization_contract_valid = _validate_authorization_contract(
                    record, requested_mode, dry_run
                )
                blockers.extend(contract_blockers)
                if trusted_manifest_path is None or trusted_candidate_package is None:
                    blockers.append("TRUSTED_CANDIDATE_ROOT_REQUIRED")
                    if candidate_manifest_path is not None:
                        blockers.append("FINAL_AUTH_PACKAGE_MISMATCH")
                elif authorization_contract_valid:
                    (
                        calculated_candidate_manifest_sha256,
                        calculated_candidate_package_digest,
                        candidate_package_matches,
                    ) = _revalidate_candidate_package_bindings(
                        record,
                        trusted_manifest_path,
                        trusted_candidate_package,
                        trusted_manifest_sha256,
                    )
                    if candidate_package_matches:
                        blockers.append("FINAL_AUTH_BINDINGS_NOT_VALIDATED")
                        warnings.append("FINAL_AUTH_BINDINGS_NOT_VALIDATED")
                        # The manifest bytes and package digest were validated
                        # against the external human trust input before any
                        # manifest-derived checks are consumed.
                        provenance_blockers, candidate_provenance_valid, candidate_provenance_verification = (
                            _validate_candidate_manifest_provenance(trusted_manifest_path, record, trusted_manifest)
                        )
                        blockers.extend(provenance_blockers)
                        (
                            upstream_binding_blockers,
                            candidate_upstream_bindings_valid,
                            candidate_upstream_bindings,
                        ) = _validate_candidate_manifest_upstream_bindings(trusted_manifest_path, trusted_manifest)
                        blockers.extend(upstream_binding_blockers)
                        (
                            canonical_index_blockers,
                            candidate_authoritative_index_valid,
                            candidate_authoritative_index,
                        ) = _validate_candidate_manifest_authoritative_index(
                            trusted_manifest_path,
                            trusted_manifest,
                            trusted_index,
                            case_dir,
                        )
                        blockers.extend(canonical_index_blockers)
                    else:
                        blockers.append("FINAL_AUTH_PACKAGE_MISMATCH")

    machine_preconditions_status = "PASS" if not _base_machine_blockers(case_dir, config)[0] else "FAIL"
    if machine_preconditions_status == "PASS":
        blockers.append("EXTERNAL_MANUAL_CONFIRMATION_REQUIRED")

    unique_blockers = list(dict.fromkeys(blockers))
    return {
        "status": "BLOCKED",
        "legacy_status": "fail",
        "blockers": unique_blockers,
        "warnings": list(dict.fromkeys(warnings)),
        "missing_artifacts": missing_artifacts,
        "required_artifacts": required_artifacts,
        "case_dir": str(case_dir),
        "machine_preconditions_status": machine_preconditions_status,
        "repo_authorization_record_valid": legacy_record_valid,
        "external_manual_confirmation_required": True,
        "authorization_boundary": "repo_record_is_not_external_human_confirmation",
        "candidate_ready": False,
        "final_freeze_ready": False,
        "final_freeze_dry_run_passed": False,
        "final_submit_allowed": False,
        "final_submit_created": False,
        "official_submission_completed": False,
        "authorization_mode": authorization_mode,
        "authorization_status": authorization_status,
        "authorization_external": authorization_external,
        "authorization_contract_valid": authorization_contract_valid,
        "authorization_bindings_valid": False,
        "candidate_manifest_path": str(trusted_manifest_path) if trusted_manifest_path is not None else None,
        "expected_candidate_manifest_sha256": expected_candidate_manifest_sha256,
        "candidate_manifest_sha256": trusted_manifest_sha256,
        "expected_run_id": expected_run_id,
        "actual_case_identity": actual_case_identity,
        "case_binding": "INDEPENDENT_FROM_HUMAN_INPUT",
        "run_binding": "HUMAN_ASSERTION_SUPPLEMENT",
        "authorization_outside_candidate_package": authorization_outside_candidate_package,
        "calculated_candidate_package_root": calculated_candidate_package_root,
        "authorization_resolved_path": authorization_resolved_path,
        "calculated_candidate_manifest_sha256": calculated_candidate_manifest_sha256,
        "calculated_candidate_package_digest": calculated_candidate_package_digest,
        "candidate_provenance_valid": candidate_provenance_valid,
        "candidate_provenance_verification": candidate_provenance_verification,
        "candidate_upstream_bindings_valid": candidate_upstream_bindings_valid,
        "candidate_upstream_bindings": candidate_upstream_bindings,
        "candidate_authoritative_index_valid": candidate_authoritative_index_valid,
        "candidate_authoritative_index": candidate_authoritative_index,
        "post_writer_reentry_path": str(post_writer_reentry_path) if post_writer_reentry_path is not None else None,
        "post_writer_reentry": post_writer_reentry,
        "post_writer_reentry_status": post_writer_reentry_status,
        "post_writer_trust_prerequisites_valid": post_writer_trust_prerequisites_valid,
        "post_writer_reentry_consumed": post_writer_reentry is not None,
        "post_writer_reentry_passed": bool(post_writer_reentry and post_writer_reentry.get("status") == "PASS"),
        "structured_semantic_reentry_path": str(structured_semantic_reentry_path) if structured_semantic_reentry_path is not None else None,
        "structured_semantic_reentry": structured_semantic_reentry,
        "structured_semantic_reentry_status": structured_semantic_reentry_status,
        "structured_semantic_reentry_consumed": structured_semantic_reentry is not None,
        "r3_approval_input_path": str(r3_approval_input_path) if r3_approval_input_path is not None else None,
        "r3_validation": r3_validation,
        "r3_status": r3_status,
        "r3_required": config.get("r3_required") if "r3_required" in config else None,
        "r3_approved_input_read_once": bool(r3_validation and r3_validation.get("approved_input_read_once")),
        "current_verdict_consumed": bool(r3_validation and r3_validation.get("current_verdict_consumed")),
        "historical_verdict_fallback": False,
        "downstream_gate_reached": bool(
            post_writer_reentry and post_writer_reentry.get("status") == "PASS"
            and (structured_semantic_reentry is not None or not require_structured_semantic_reentry)
            and structured_semantic_reentry_status not in {"SEMANTIC_BLOCK", "REVIEW_REQUIRED"}
            and r3_status in {"R3_PASS", "NOT_APPLICABLE"}
        ),
        "note": (
            "Read-only authorization intake; trusted package identity is supplied "
            "externally and this function never creates final_submit."
        ),
    }
