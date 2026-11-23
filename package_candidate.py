"""Candidate package exporter for the X5-F0 surgical runner.

Candidate packages are review artifacts. This script never creates
final_submit and never copies full gates, raw AI logs, evidence_map, or
citation_matrix into candidate packages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import string
import subprocess
from pathlib import Path
from typing import Any

from pipeline_core.python_tools.claim_check_preflight import check_claim_check_preflight
from pipeline_core.python_tools.structured_claim_handoff import (
    StructuredClaimHandoffBlocked,
    build_structured_claim_handoff,
)
from pipeline_core.python_tools.r3_validator import (
    ARTIFACT_SCHEMA_VERSION as R3_ARTIFACT_SCHEMA_VERSION,
    R3ValidationBlocked,
    prepare_review_constraints_artifact,
)
from pipeline_core.python_tools.human_revision_check import (
    DEFAULT_RECORD_PATH as DEFAULT_HUMAN_REVISION_RECORD_PATH,
    check_human_revision,
)
from canonical_result_index import CanonicalResultIndexBlocked, build_index
from x5_surgical_utils import (
    ROOT,
    find_paper_pdf,
    inventory_source_data,
    markdown_table,
    read_json,
    register_asset,
    rel_path,
    sha256_file,
    sha256_path,
    timestamp_id,
    utc_now,
    write_json,
)


class CandidatePromotionClaimCheckBlocked(RuntimeError):
    """Structured refusal raised before any candidate package is created."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        super().__init__(json.dumps(result, ensure_ascii=False))


class CandidatePromotionUpstreamStatusBlocked(RuntimeError):
    """Structured refusal raised when persisted upstream status is unsafe."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        super().__init__(json.dumps(result, ensure_ascii=False))


class CandidatePromotionHumanRevisionBlocked(RuntimeError):
    """Structured HUMAN-REV refusal raised before candidate creation."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        super().__init__(json.dumps(result, ensure_ascii=False))


class CandidatePromotionUpstreamBindingBlocked(RuntimeError):
    """Structured refusal when an upstream artifact's hash matches but its
    own parsed status semantics are not an allowed (passing) state, or when
    any configured artifact's hash is stale/missing/unreadable.

    P6-S2B: hash equality alone is not proof of a safe artifact — a byte-
    identical copy of a BLOCKED/FAIL/fatal upstream record still hashes
    correctly. This gate parses each applicable artifact's own status
    fields and requires every one of them to be in an allowed state, with
    the strictest observed status winning across all artifacts (any single
    fatal/blocked/fail/hard_block anywhere blocks the whole binding).
    """

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        super().__init__(json.dumps(result, ensure_ascii=False))


class CandidatePromotionProvenanceBlocked(RuntimeError):
    """Structured refusal raised when source provenance finalization fails.

    Deliberately does not import run_case.py: run_case.py imports
    package_candidate at module level, so any reverse import here would be
    circular. All git-snapshot / porcelain-parsing helpers used for this
    check are independent, minimal re-implementations local to this module.
    """

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        super().__init__(json.dumps(result, ensure_ascii=False))


def _pc_normalize_git_status_path(raw_path: bytes) -> str:
    """Decode one Git porcelain path as a repository-relative POSIX path."""
    from pathlib import PurePosixPath

    path = raw_path.decode("utf-8", errors="surrogateescape").replace("\\", "/")
    normalized = str(PurePosixPath(path))
    if not path or normalized in {".", ".."} or normalized.startswith("../") or normalized.startswith("/"):
        raise ValueError("status path is not repository-relative")
    return normalized


def _pc_parse_porcelain_v1_z(stream: bytes) -> tuple[list[dict[str, str | None]], str | None]:
    """Parse Git porcelain v1 ``-z`` bytes without quote or whitespace splitting."""
    if not isinstance(stream, bytes):
        return [], "status stream is not bytes"
    if not stream:
        return [], None
    if not stream.endswith(b"\0"):
        return [], "status stream is missing its NUL terminator"
    fields = stream[:-1].split(b"\0")
    records: list[dict[str, str | None]] = []
    index = 0
    valid_status_chars = {" ", "M", "A", "D", "R", "C", "U", "?", "!"}
    try:
        while index < len(fields):
            field = fields[index]
            if len(field) < 4 or field[2:3] != b" ":
                raise ValueError("status record header is malformed")
            xy = field[:2].decode("ascii")
            if any(character not in valid_status_chars for character in xy):
                raise ValueError("status record has an invalid XY code")
            path = _pc_normalize_git_status_path(field[3:])
            original_path: str | None = None
            index += 1
            if "R" in xy or "C" in xy:
                if index >= len(fields) or not fields[index]:
                    raise ValueError("rename/copy status record is missing its original path")
                original_path = _pc_normalize_git_status_path(fields[index])
                index += 1
            records.append({"xy": xy, "path": path, "original_path": original_path})
    except (UnicodeDecodeError, ValueError) as exc:
        return [], str(exc)
    return records, None


def _pc_is_exact_status_path(path: object) -> bool:
    if not isinstance(path, str) or not path or any(token in path for token in ("*", "?", "[", "]")):
        return False
    try:
        return _pc_normalize_git_status_path(path.encode("utf-8", errors="surrogateescape")) == path
    except ValueError:
        return False


def _pc_run_git(args: list[str], root: Path, *, text: bool = True) -> tuple[str | bytes | None, bool]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=text,
            check=False,
        )
    except OSError:
        return None, False
    if completed.returncode != 0:
        return None, False
    return completed.stdout, True


def _pc_git_operation_in_progress(root: Path) -> bool:
    git_dir = root / ".git"
    if not git_dir.is_dir():
        return False
    markers = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG")
    if any((git_dir / marker).exists() for marker in markers):
        return True
    if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
        return True
    return False


def _pc_deterministic_sha256(payload: Any) -> str:
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _pc_load_upstream_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _pc_classify_expected_post_snapshot_path(entry: object, root: Path) -> dict[str, Any]:
    """Classify one source_expected_post_snapshot_paths entry.

    Returns a dict describing whether the entry is safe, whether it exists
    as a plain file, and (for repo-internal entries only) its normalized
    repo-relative path for later git-status membership checks. Repo-external
    entries are validated for existence/plain-file-ness only and are never
    treated as git-visible.
    """
    result: dict[str, Any] = {"raw": entry, "safe": False, "is_repo_internal": False, "is_plain_file": False, "repo_relative": None}
    if not isinstance(entry, str) or not entry.strip():
        return result
    if any(token in entry for token in ("*", "?", "[", "]")):
        return result
    raw = Path(entry)
    resolved_root = root.resolve()
    # unresolved_target is the path exactly as named, with only its parent
    # directory resolved (symlinks not followed at the final component) —
    # used for the is_symlink() check below. Path.resolve() follows
    # symlinks all the way through, so calling is_symlink() on its result
    # would always see the link's target, never the link itself.
    unresolved_target = raw if raw.is_absolute() else root / raw
    if raw.is_absolute():
        try:
            resolved = raw.resolve()
        except OSError:
            return result
        try:
            repo_relative = resolved.relative_to(resolved_root).as_posix()
            is_repo_internal = True
        except ValueError:
            repo_relative = None
            is_repo_internal = False
    else:
        if ".." in raw.parts:
            return result
        try:
            resolved = (root / raw).resolve()
        except OSError:
            return result
        try:
            repo_relative = resolved.relative_to(resolved_root).as_posix()
        except ValueError:
            return result
        is_repo_internal = True
    result["safe"] = True
    result["is_repo_internal"] = is_repo_internal
    result["repo_relative"] = repo_relative
    result["resolved"] = resolved
    if unresolved_target.is_symlink():
        return result
    if resolved.exists() and resolved.is_file():
        result["is_plain_file"] = True
    return result


def _verify_source_provenance(
    case_dir: Path,
    root: Path,
    effective_settings: dict[str, Any],
) -> dict[str, Any]:
    """Post-snapshot, read-only finalization check for run_case provenance.

    Diagnostic-and-gating: this function never mutates run_result.json or
    risk_summary.json (the upstream artifacts it reads), never writes a new
    standalone provenance artifact, and never sets
    source_write_registry_authoritative to true anywhere — that field must
    stay false, reflecting run_case's own honest self-report. This runs
    strictly after run_case has already exited, reading already-finalized
    files from disk, which is what breaks the self-reference cycle
    documented in the P6-PV1B2B-D0 audit: run_case cannot verify its own
    post-snapshot writes without mutating the very files being verified.
    """
    blockers: list[str] = []
    # source_provenance_required is derived only from existing, already-
    # trusted context (submission tier / the existing upstream-status
    # guard's own required flag) — never from whether source_provenance
    # happens to be present in a file. This is what stops a case from
    # escaping strict verification just because its upstream fixture
    # predates this feature: tier/requiredness decides the policy, file
    # content only decides applicability within that policy.
    required = bool(
        effective_settings.get("submission_tier") in {"selection", "formal"}
        or effective_settings.get("effective_upstream_status_required") is True
    )
    summary: dict[str, Any] = {
        "source_provenance_required": required,
        "source_provenance_applicable": False,
        "source_provenance_legacy_compatibility": False,
        "source_provenance_sha256": None,
        "source_provenance_finalization_passed": False,
        "post_snapshot_verification_passed": False,
        "run_id": None,
        "source_commit": None,
        "packaging_head_matches_source_commit": False,
        "packaging_git_operation_in_progress": False,
        "packaging_unexpected_status_entries": [],
        "blockers": blockers,
    }

    upstream_paths = {item["field"]: item["path"] for item in effective_settings.get("upstream_status_paths", [])}
    run_result_path_text = upstream_paths.get("candidate_upstream_run_result_path")
    risk_summary_path_text = upstream_paths.get("candidate_upstream_risk_summary_path")

    run_result_payload = _pc_load_upstream_json(Path(run_result_path_text)) if run_result_path_text else None
    risk_summary_payload = _pc_load_upstream_json(Path(risk_summary_path_text)) if risk_summary_path_text else None
    run_result_has_provenance = isinstance(run_result_payload, dict) and "source_provenance" in run_result_payload
    risk_summary_has_provenance = isinstance(risk_summary_payload, dict) and "source_provenance" in risk_summary_payload

    if not required and not run_result_has_provenance and not risk_summary_has_provenance:
        # Neither upstream artifact was produced by a run_case invocation
        # that emits source_provenance (or nothing was configured at all —
        # the common training/test/homework case), and this case is not
        # under a policy that requires provenance finalization. Explicit
        # legacy compatibility mode: candidate creation may proceed, but the
        # manifest must say plainly that nothing was verified, never that
        # finalization passed.
        summary["source_provenance_legacy_compatibility"] = True
        return summary

    # Past this point we are either required, or at least one side already
    # carries source_provenance (the legacy no-op branch above is the only
    # way to skip strict validation, and it only fires when neither side
    # has provenance at all). Any file carrying source_provenance makes
    # this check applicable, regardless of what the other file has — a
    # one-sided mismatch below still counts as applicable, it just fails.
    if run_result_has_provenance or risk_summary_has_provenance:
        summary["source_provenance_applicable"] = True

    if not run_result_path_text or not risk_summary_path_text:
        blockers.append("PROVENANCE_INPUT_MISSING")
        return summary

    if run_result_payload is None or risk_summary_payload is None:
        blockers.append("PROVENANCE_INPUT_MISSING")
        return summary

    if not run_result_has_provenance or not risk_summary_has_provenance:
        # Either both sides carry source_provenance, or neither does (the
        # legacy branch above). Exactly one side carrying it is always a
        # blocking inconsistency, at any tier — never a degrade-to-legacy
        # signal.
        if run_result_has_provenance != risk_summary_has_provenance:
            blockers.append("PROVENANCE_MISMATCH")
        else:
            blockers.append("PROVENANCE_INPUT_MISSING")
        return summary

    run_result_run_id = run_result_payload.get("run_id")
    risk_summary_run_id = risk_summary_payload.get("run_id")
    if (
        not isinstance(run_result_run_id, str)
        or not run_result_run_id
        or run_result_run_id != risk_summary_run_id
    ):
        blockers.append("PROVENANCE_RUN_ID_MISMATCH")
        return summary
    summary["run_id"] = run_result_run_id

    run_result_provenance = run_result_payload.get("source_provenance")
    risk_summary_provenance = risk_summary_payload.get("source_provenance")
    if (
        not isinstance(run_result_provenance, dict)
        or not isinstance(risk_summary_provenance, dict)
        or run_result_provenance != risk_summary_provenance
    ):
        blockers.append("PROVENANCE_MISMATCH")
        return summary

    source_provenance = run_result_provenance
    summary["source_provenance_sha256"] = _pc_deterministic_sha256(source_provenance)
    summary["source_commit"] = source_provenance.get("source_commit_after")

    runtime_ok = (
        source_provenance.get("source_provenance_capture_ok") is True
        and source_provenance.get("source_worktree_clean_before") is True
        and source_provenance.get("source_commit_stable") is True
        and isinstance(source_provenance.get("source_commit_before"), str)
        and bool(source_provenance.get("source_commit_before"))
        and source_provenance.get("source_commit_before") == source_provenance.get("source_commit_after")
        and source_provenance.get("source_write_registry_complete") is True
        and source_provenance.get("source_write_registry_scope") == "ALL_PRE_SNAPSHOT_WRITES"
        and source_provenance.get("source_write_registry_excluded_classes") == []
        and source_provenance.get("source_write_registry_authoritative") is False
    )
    if not runtime_ok:
        blockers.append("PROVENANCE_RUNTIME_INCOMPLETE")

    expected_paths = source_provenance.get("source_expected_post_snapshot_paths")
    if not isinstance(expected_paths, list) or not expected_paths:
        blockers.append("PROVENANCE_POST_SNAPSHOT_MISSING")
        expected_paths = []

    repo_internal_relative_paths: set[str] = set()
    post_snapshot_ok = bool(expected_paths)
    for entry in expected_paths:
        classification = _pc_classify_expected_post_snapshot_path(entry, root)
        if not classification["safe"]:
            blockers.append("PROVENANCE_PATH_UNSAFE")
            post_snapshot_ok = False
            continue
        if not classification["is_plain_file"]:
            blockers.append("PROVENANCE_POST_SNAPSHOT_MISSING")
            post_snapshot_ok = False
            continue
        if classification["is_repo_internal"]:
            repo_internal_relative_paths.add(classification["repo_relative"])
    summary["post_snapshot_verification_passed"] = post_snapshot_ok

    if _pc_git_operation_in_progress(root):
        summary["packaging_git_operation_in_progress"] = True
        blockers.append("PROVENANCE_GIT_OPERATION_IN_PROGRESS")

    head_output, head_ok = _pc_run_git(["rev-parse", "--verify", "HEAD"], root)
    current_head = head_output.strip() if head_ok and isinstance(head_output, str) else None
    source_commit_after = source_provenance.get("source_commit_after")

    if isinstance(source_commit_after, str) and source_commit_after:
        _, commit_exists = _pc_run_git(["cat-file", "-e", f"{source_commit_after}^{{commit}}"], root)
        if not commit_exists:
            blockers.append("PROVENANCE_COMMIT_MISSING")
    else:
        blockers.append("PROVENANCE_COMMIT_MISSING")

    if current_head is not None and current_head == source_commit_after:
        summary["packaging_head_matches_source_commit"] = True
    else:
        summary["packaging_head_matches_source_commit"] = False
        blockers.append("PROVENANCE_HEAD_DRIFT")

    status_output, status_ok = _pc_run_git(
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"], root, text=False
    )
    if not status_ok:
        blockers.append("PROVENANCE_UNEXPECTED_WORKTREE_CHANGE")
    else:
        records, parse_error = _pc_parse_porcelain_v1_z(status_output if isinstance(status_output, bytes) else b"")
        if parse_error:
            blockers.append("PROVENANCE_UNEXPECTED_WORKTREE_CHANGE")
        else:
            allowed_paths: set[str] = set()
            written_repo_paths = source_provenance.get("source_written_repo_paths")
            if isinstance(written_repo_paths, list):
                allowed_paths.update(path for path in written_repo_paths if isinstance(path, str))
            allowed_paths.update(repo_internal_relative_paths)

            unexpected: list[dict[str, Any]] = []
            for record in records:
                xy = record.get("xy")
                path = record.get("path")
                original_path = record.get("original_path")
                if not isinstance(xy, str) or not _pc_is_exact_status_path(path):
                    unexpected.append(record)
                    continue
                if original_path is not None and not _pc_is_exact_status_path(original_path):
                    unexpected.append(record)
                    continue
                ends = [path] if original_path is None else [path, original_path]
                if any(end not in allowed_paths for end in ends):
                    unexpected.append(record)
            summary["packaging_unexpected_status_entries"] = unexpected
            if unexpected:
                blockers.append("PROVENANCE_UNEXPECTED_WORKTREE_CHANGE")

    deduped_blockers = list(dict.fromkeys(blockers))
    blockers.clear()
    blockers.extend(deduped_blockers)
    summary["source_provenance_finalization_passed"] = not blockers
    return summary


def _effective_candidate_settings(case_dir: Path) -> dict[str, Any]:
    config = read_json(case_dir / "case_config.json", default={}) or {}
    config = config if isinstance(config, dict) else {}
    raw_tier = config.get("submission_tier")
    submission_tier = str(raw_tier).strip().lower() if raw_tier is not None and str(raw_tier).strip() else "training"
    tier_requires_claim_check = submission_tier in {"selection", "formal"}
    tier_requires_upstream_status = submission_tier in {"selection", "formal"}
    applied: list[str] = []
    explicitly_disabled: list[str] = []
    if config.get("claim_check_required") is None:
        effective_required = tier_requires_claim_check
        if tier_requires_claim_check:
            applied.append("claim_check")
    else:
        effective_required = config.get("claim_check_required") is True
        if tier_requires_claim_check and config.get("claim_check_required") is False:
            explicitly_disabled.append("claim_check")
    upstream_paths: list[dict[str, str]] = []
    for field in ("candidate_upstream_run_result_path", "candidate_upstream_risk_summary_path"):
        value = config.get(field)
        if not isinstance(value, str) or not value.strip():
            continue
        raw_path = Path(value)
        if raw_path.is_absolute():
            resolved = raw_path.resolve()
        elif ".." not in raw_path.parts:
            resolved = (case_dir / raw_path).resolve()
        else:
            resolved = Path()
        upstream_paths.append({"field": field, "path": str(resolved) if resolved != Path() else ""})
    if tier_requires_upstream_status:
        applied.append("candidate_upstream_status")
    return {
        "submission_tier": submission_tier,
        "effective_claim_check_required": effective_required,
        "structured_claim_handoff_required": config.get("structured_claim_handoff_required") is True,
        "tier_default_applied": applied,
        "tier_default_explicitly_disabled": explicitly_disabled,
        "effective_upstream_status_required": tier_requires_upstream_status,
        "upstream_status_paths": upstream_paths,
        "upstream_status": [],
        "upstream_blocker": None,
        "upstream_content_binding_checked": False,
        "stale_upstream_status": False,
        "content_binding_mismatches": [],
        "warnings": [
            f"TIER_DEFAULT_EXPLICITLY_DISABLED: {field}" for field in explicitly_disabled
        ],
    }


def _upstream_block_reason(payload: dict[str, Any], source_name: str) -> str | None:
    if payload.get("risk_status") == "BLOCK" or payload.get("status") == "fatal":
        return "UPSTREAM_STATUS_BLOCK"
    fatal_count = payload.get("fatal_count")
    if isinstance(fatal_count, int) and not isinstance(fatal_count, bool) and fatal_count > 0:
        return "UPSTREAM_FATAL_COUNT"
    if payload.get("validation_continue_allowed") is False:
        return "UPSTREAM_VALIDATION_CONTINUE_DISABLED"
    if payload.get("safe_promotion") is False:
        return "UPSTREAM_SAFE_PROMOTION_DISABLED"
    risks = payload.get("risks")
    if isinstance(risks, list) and any(isinstance(item, dict) and item.get("fatal") is True for item in risks):
        return "UPSTREAM_FATAL_RISK_ITEM"
    if source_name == "candidate_upstream_risk_summary_path" and isinstance(fatal_count, int) and isinstance(risks, list):
        return None
    if payload.get("risk_status") == "PASS" or payload.get("status") in {"ok", "PASS"}:
        return None
    return "UPSTREAM_STATUS_UNKNOWN"


def _content_binding_mismatches(case_dir: Path, payload: dict[str, Any], upstream_status_path: str) -> list[dict[str, Any]]:
    bindings = payload.get("upstream_content_bindings", payload.get("validated_content_hashes"))
    if not isinstance(bindings, list) or not bindings:
        return [{"reason": "missing_or_malformed_content_bindings", "upstream_status_path": upstream_status_path}]
    mismatches: list[dict[str, Any]] = []
    allowed_sha256 = set(string.hexdigits)
    case_root = case_dir.resolve()
    for index, binding in enumerate(bindings):
        evidence: dict[str, Any] = {"binding_index": index, "upstream_status_path": upstream_status_path}
        if not isinstance(binding, dict):
            mismatches.append({**evidence, "reason": "malformed_binding"})
            continue
        relative_path = binding.get("relative_path")
        expected = binding.get("sha256")
        logical_name = binding.get("logical_name")
        source_role = binding.get("source_role")
        required = binding.get("required_for_candidate_validation")
        evidence.update({"path": relative_path, "expected_sha256": expected})
        if not all(isinstance(value, str) and value.strip() for value in (relative_path, logical_name, source_role)) or not isinstance(required, bool):
            mismatches.append({**evidence, "reason": "malformed_binding"})
            continue
        if not isinstance(expected, str) or len(expected) != 64 or any(char not in allowed_sha256 for char in expected):
            mismatches.append({**evidence, "reason": "malformed_binding"})
            continue
        if not required:
            continue
        raw_path = Path(relative_path)
        if raw_path.is_absolute() or ".." in raw_path.parts:
            mismatches.append({**evidence, "actual_sha256": "unsafe", "reason": "unsafe_path"})
            continue
        current_path = (case_dir / raw_path).resolve()
        try:
            current_path.relative_to(case_root)
        except ValueError:
            mismatches.append({**evidence, "actual_sha256": "unsafe", "reason": "unsafe_path"})
            continue
        if not current_path.is_file():
            mismatches.append({**evidence, "actual_sha256": "missing", "reason": "missing_file"})
            continue
        try:
            actual = sha256_file(current_path)
        except OSError:
            mismatches.append({**evidence, "actual_sha256": "unreadable", "reason": "unreadable_file"})
            continue
        if actual.lower() != expected.lower():
            mismatches.append({**evidence, "actual_sha256": actual, "reason": "sha256_mismatch"})
    if not any(
        isinstance(binding, dict)
        and binding.get("source_role") == "result_summary"
        and binding.get("required_for_candidate_validation") is True
        for binding in bindings
    ):
        mismatches.append({"reason": "required_result_summary_binding_missing", "upstream_status_path": upstream_status_path})
    return mismatches


# Ordered from least to most severe; used to pick the single strictest
# semantic_status across all applicable artifacts. Statuses observed on
# individual artifacts are normalized into this vocabulary before ranking.
_SEMANTIC_STATUS_SEVERITY = {
    "PASS": 0,
    "SKIPPED": 0,
    "NOT_APPLICABLE": 0,
    "MANUAL_REVIEW_REQUIRED": 1,
    "UNKNOWN": 2,
    "FAIL": 3,
    "BLOCK": 3,
    "BLOCKED": 3,
    "FATAL": 3,
    "HARD_BLOCK": 3,
}


def _pc_semantic_status_rank(status: str) -> int:
    return _SEMANTIC_STATUS_SEVERITY.get(status, 2)


def _pc_hash_artifact_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        return sha256_file(path)
    except OSError:
        return None


def _pc_run_result_semantic_status(payload: dict[str, Any]) -> str:
    if payload.get("risk_status") == "BLOCK" or payload.get("status") == "fatal":
        return "BLOCK"
    fatal_count = payload.get("fatal_count")
    if isinstance(fatal_count, int) and not isinstance(fatal_count, bool) and fatal_count > 0:
        return "FATAL"
    if payload.get("validation_continue_allowed") is False or payload.get("safe_promotion") is False:
        return "BLOCK"
    if payload.get("risk_status") == "PASS" or payload.get("status") in {"ok", "PASS"}:
        return "PASS"
    return "UNKNOWN"


def _pc_risk_summary_semantic_status(payload: dict[str, Any]) -> str:
    risks = payload.get("risks")
    if isinstance(risks, list) and any(isinstance(item, dict) and item.get("fatal") is True for item in risks):
        return "FATAL"
    fatal_count = payload.get("fatal_count")
    if isinstance(fatal_count, int) and not isinstance(fatal_count, bool) and fatal_count > 0:
        return "FATAL"
    if payload.get("risk_status") == "BLOCK":
        return "BLOCK"
    if isinstance(risks, list):
        # A risk_summary with no fatal items and a parseable risks list is
        # the artifact's own definition of passing — it never depends on a
        # risk_status field the writer may not have set.
        return "PASS"
    return "UNKNOWN"


def _pc_result_summary_semantic_status(payload: dict[str, Any]) -> str:
    status = payload.get("status")
    if status in {"fatal", "BLOCK", "FAIL"}:
        return "BLOCK"
    if status in {"ok", "PASS"}:
        return "PASS"
    return "UNKNOWN"


def _pc_route_review_semantic_status(payload: dict[str, Any]) -> str:
    status = payload.get("route_review_status")
    if status in {"PASS"}:
        return "PASS"
    if status in {"BLOCKED", "FAIL", "FATAL"}:
        return "BLOCK"
    if status == "MANUAL_REVIEW_REQUIRED":
        return "MANUAL_REVIEW_REQUIRED"
    blockers = payload.get("blockers")
    if isinstance(blockers, list) and blockers:
        return "BLOCK"
    return "UNKNOWN"


def _pc_xval_semantic_status(payload: dict[str, Any]) -> str:
    status = payload.get("xval_status") or payload.get("status")
    if status in {"PASS"}:
        pass
    elif status in {"FAIL", "BLOCK", "FATAL", "BLOCKED"}:
        return "BLOCK"
    fatal_contribution = payload.get("fatal_count_contribution")
    if isinstance(fatal_contribution, int) and not isinstance(fatal_contribution, bool) and fatal_contribution > 0:
        return "FATAL"
    if payload.get("validation_continue_allowed") is False:
        return "BLOCK"
    if status == "PASS":
        return "PASS"
    if status == "SKIPPED":
        return "SKIPPED"
    return "UNKNOWN"


# Only artifacts with a genuine, already-established schema_version contract
# in this codebase are checked here (x5_surgical_utils.RESULT_SUMMARY_REQUIRED_FIELDS,
# write_risk_summary's "x5_f0_risk_summary_v1"). route_review/xval/run_result
# have no schema_version field anywhere in their producers — fabricating a
# version contract for them would violate "不虚构不存在的版本字段" — so they
# are intentionally excluded from this map.
_SCHEMA_VERSION_ALLOWED = {
    "risk_summary": {"x5_f0_risk_summary_v1"},
    "result_summary": {"x5_f0_result_summary_v1", "x5_f1_math_core_result_summary_v1"},
}


def _pc_bind_one_artifact(
    *,
    name: str,
    path: Path | None,
    payload: dict[str, Any] | None,
    semantic_status_fn,
) -> dict[str, Any]:
    binding: dict[str, Any] = {
        "applicable": path is not None,
        "sha256": None,
        "schema_version": None,
        "semantic_status": "NOT_APPLICABLE",
        "semantic_valid": True,
        "reason": None,
    }
    if path is None:
        return binding
    sha256 = _pc_hash_artifact_file(path)
    if sha256 is None:
        binding.update(semantic_status="UNKNOWN", semantic_valid=False, reason="artifact_unreadable_or_missing")
        return binding
    binding["sha256"] = sha256
    if payload is None:
        binding.update(semantic_status="UNKNOWN", semantic_valid=False, reason="artifact_malformed_json")
        return binding

    # schema_version is never required to be present — S2B does not
    # fabricate a "version field mandatory" contract for artifacts whose
    # real producers (e.g. run_case.py's risk_summary writer predating this
    # check) never guaranteed one. "field missing" stays schema_version:
    # null and is not itself a failure. Only once a version IS present does
    # strict validation apply: unknown, unsupported, or non-string values
    # fail-closed, for any artifact with a known contract in
    # _SCHEMA_VERSION_ALLOWED.
    allowed_versions = _SCHEMA_VERSION_ALLOWED.get(name)
    if allowed_versions is not None and "schema_version" in payload:
        raw_version = payload.get("schema_version")
        binding["schema_version"] = raw_version if isinstance(raw_version, str) else None
        if not isinstance(raw_version, str) or raw_version not in allowed_versions:
            binding.update(semantic_status="UNKNOWN", semantic_valid=False, reason="schema_version_invalid_or_unknown")
            return binding

    status = semantic_status_fn(payload)
    binding["semantic_status"] = status
    binding["semantic_valid"] = _pc_semantic_status_rank(status) == 0
    if not binding["semantic_valid"]:
        binding["reason"] = "semantic_status_not_allowed"
    return binding


def _resolve_optional_case_config_path(case_dir: Path, config: dict[str, Any], field: str) -> Path | None:
    value = config.get(field)
    if not isinstance(value, str) or not value.strip():
        return None
    raw_path = Path(value)
    if raw_path.is_absolute():
        resolved = raw_path.resolve()
    elif ".." not in raw_path.parts:
        resolved = (case_dir / raw_path).resolve()
    else:
        return None
    return resolved


def _build_upstream_artifact_bindings(
    case_dir: Path,
    config: dict[str, Any],
    effective_settings: dict[str, Any],
    *,
    claim_check_result: dict[str, Any] | None,
    human_revision_result: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """Consolidated hash + semantic binding for every applicable upstream
    artifact. Only computes/validates artifacts that are actually
    configured or already known to apply — never fabricates a binding for
    an artifact this case does not use.

    Returns (bindings_summary_for_manifest, blockers). An empty blockers
    list means every applicable artifact's content hash was verified fresh
    and its own parsed status was in an allowed (non-blocking) state.
    """
    bindings: dict[str, Any] = {}
    blockers: list[str] = []
    worst_rank = 0

    def _register(name: str, binding: dict[str, Any]) -> None:
        nonlocal worst_rank
        bindings[name] = {key: value for key, value in binding.items() if key != "reason" or value is not None}
        if not binding["applicable"]:
            return
        rank = _pc_semantic_status_rank(binding["semantic_status"])
        worst_rank = max(worst_rank, rank)
        if not binding["semantic_valid"]:
            blockers.append(f"UPSTREAM_BINDING_{name.upper()}_INVALID")

    upstream_paths = {item["field"]: item["path"] for item in effective_settings.get("upstream_status_paths", [])}
    run_result_path_text = upstream_paths.get("candidate_upstream_run_result_path")
    risk_summary_path_text = upstream_paths.get("candidate_upstream_risk_summary_path")

    run_result_payload = None
    run_result_path = None
    if run_result_path_text:
        run_result_path = Path(run_result_path_text)
        run_result_payload = _pc_load_upstream_json(run_result_path) if run_result_path.is_file() else None
    _register(
        "run_result",
        _pc_bind_one_artifact(
            name="run_result",
            path=run_result_path if run_result_path_text else None,
            payload=run_result_payload,
            semantic_status_fn=_pc_run_result_semantic_status,
        ),
    )

    risk_summary_payload = None
    risk_summary_path = None
    if risk_summary_path_text:
        risk_summary_path = Path(risk_summary_path_text)
        risk_summary_payload = _pc_load_upstream_json(risk_summary_path) if risk_summary_path.is_file() else None
    _register(
        "risk_summary",
        _pc_bind_one_artifact(
            name="risk_summary",
            path=risk_summary_path if risk_summary_path_text else None,
            payload=risk_summary_payload,
            semantic_status_fn=_pc_risk_summary_semantic_status,
        ),
    )

    # requirement 5's run_id/provenance agreement between run_result and
    # risk_summary is intentionally NOT re-checked here: P6-PV2A/PV2A-B's
    # _verify_source_provenance already owns that exact comparison (with
    # its own well-established CandidatePromotionProvenanceBlocked
    # exception, PROVENANCE_RUN_ID_MISMATCH/PROVENANCE_MISMATCH blocker
    # vocabulary, and dedicated regression coverage) and runs immediately
    # after this gate in package_candidate(). Duplicating the same
    # comparison here under a different exception type would just race the
    # existing, more specific gate for the same underlying defect — S2B's
    # own scope is the hash+semantic-status binding per artifact, which is
    # still fully enforced above for both run_result and risk_summary
    # individually.
    result_summary_path = case_dir / "output" / "result_summary.json"
    result_summary_payload = _pc_load_upstream_json(result_summary_path) if result_summary_path.is_file() else None
    _register(
        "result_summary",
        _pc_bind_one_artifact(
            name="result_summary",
            path=result_summary_path if result_summary_path.is_file() else None,
            payload=result_summary_payload,
            semantic_status_fn=_pc_result_summary_semantic_status,
        ),
    )

    route_review_path = _resolve_optional_case_config_path(case_dir, config, "candidate_upstream_route_review_path")
    route_review_payload = (
        _pc_load_upstream_json(route_review_path) if route_review_path is not None and route_review_path.is_file() else None
    )
    _register(
        "route_review",
        _pc_bind_one_artifact(
            name="route_review",
            path=route_review_path,
            payload=route_review_payload,
            semantic_status_fn=_pc_route_review_semantic_status,
        ),
    )

    xval_path = _resolve_optional_case_config_path(case_dir, config, "candidate_upstream_xval_path")
    xval_payload = _pc_load_upstream_json(xval_path) if xval_path is not None and xval_path.is_file() else None
    _register(
        "xval",
        _pc_bind_one_artifact(
            name="xval",
            path=xval_path,
            payload=xval_payload,
            semantic_status_fn=_pc_xval_semantic_status,
        ),
    )

    # claim_check / human_revision: unlike run_result/risk_summary/etc,
    # these have no single upstream JSON file — their preflight result
    # (already computed by the earlier, independent
    # _enforce_claim_check_candidate_gate / _enforce_human_revision_
    # candidate_gate calls, which already block on their own failures) is
    # used only to *locate* the real on-disk artifact(s) it inspected.
    # This function then independently hashes those artifacts' actual
    # bytes and independently re-derives semantic_valid from their own
    # parsed claim/record status fields — it does not simply trust that
    # the earlier gate already filtered out every bad state, since a
    # gate wired up wrong or bypassed in the future must still be caught
    # here on its own evidence.
    claim_check_required = effective_settings.get("effective_claim_check_required") is True
    if claim_check_result is not None:
        claims = claim_check_result.get("claims")
        claims = claims if isinstance(claims, list) else []
        card_entries: list[dict[str, str]] = []
        claim_statuses: list[str] = []
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            claim_statuses.append(str(claim.get("status") or "UNKNOWN"))
            card_path_text = claim.get("claim_card_path")
            if isinstance(card_path_text, str) and card_path_text.strip():
                card_path = Path(card_path_text)
                if card_path.is_file():
                    file_hash = _pc_hash_artifact_file(card_path)
                    if file_hash is not None:
                        card_entries.append({"relative_path": card_path.name, "sha256": file_hash})
        card_entries.sort(key=lambda item: item["relative_path"])
        claim_card_hash = _pc_deterministic_sha256(card_entries) if card_entries else None

        claim_status = claim_check_result.get("status")
        applicable = claim_check_required or bool(claims) or claim_status not in {None, "SKIPPED"}
        if not applicable:
            semantic = "NOT_APPLICABLE"
            valid = True
        elif claim_status == "SKIPPED" and not claim_check_required:
            semantic = "SKIPPED"
            valid = True
        elif not claim_statuses:
            if claim_status == "PASS":
                # The earlier gate's own PASS determination with no
                # per-claim breakdown available (e.g. no core claims were
                # configured for this case at all) is a legitimate PASS —
                # there is simply nothing more granular to independently
                # verify. This is distinct from a BLOCKED/unrecognized
                # top-level status with no claim evidence, which is the
                # "unrecognized/missing status" case that must fail-closed
                # instead (handled by the else branch below).
                semantic = "PASS"
                valid = True
            else:
                # Applicable, non-PASS top-level status, and no individual
                # claim statuses were recoverable from the artifact
                # evidence either — fail-closed rather than pass by
                # default severity ranking.
                semantic = "UNKNOWN"
                valid = False
        else:
            # Strictest-wins across every individual claim's own status,
            # independent of the top-level claim_check_result["status"]
            # the earlier gate already computed.
            normalized = [
                "PASS" if status == "PASS" else ("MANUAL_REVIEW_REQUIRED" if status == "MANUAL_REVIEW_REQUIRED" else "BLOCK")
                for status in claim_statuses
            ]
            worst = max(normalized, key=_pc_semantic_status_rank)
            semantic = worst
            valid = _pc_semantic_status_rank(worst) == 0
        binding = {
            "applicable": applicable,
            "sha256": claim_card_hash,
            "schema_version": None,
            "semantic_status": semantic,
            "semantic_valid": valid,
            "reason": None if valid else "claim_check_not_passed",
        }
        _register("claim_check", binding)
    else:
        _register(
            "claim_check",
            {"applicable": False, "sha256": None, "schema_version": None, "semantic_status": "NOT_APPLICABLE", "semantic_valid": True, "reason": None},
        )

    human_revision_required = False
    if human_revision_result is not None:
        revision_view = human_revision_result.get("human_revision") or {}
        human_revision_required = revision_view.get("required") is True
        record_path_text = revision_view.get("record_path")
        record_path = (
            case_dir / record_path_text
            if isinstance(record_path_text, str) and record_path_text.strip()
            else case_dir / DEFAULT_HUMAN_REVISION_RECORD_PATH
        )
        record_hash = _pc_hash_artifact_file(record_path)
        applicable = human_revision_required or record_path.is_file()

        if not applicable:
            semantic = "NOT_APPLICABLE"
            valid = True
        elif record_hash is None:
            # Applicable but the record file could not be hashed (missing
            # or unreadable) — independent of what the earlier gate's own
            # "completed" boolean said, an artifact this function cannot
            # itself verify by its own bytes must fail-closed when
            # required.
            semantic = "UNKNOWN" if human_revision_required else "SKIPPED"
            valid = not human_revision_required
        else:
            record_payload = _pc_load_upstream_json(record_path)
            record_status = record_payload.get("status") if isinstance(record_payload, dict) else None
            if record_status == "COMPLETED":
                semantic = "PASS"
                valid = True
            elif record_status is None:
                semantic = "UNKNOWN"
                valid = not human_revision_required
            else:
                semantic = "BLOCK"
                valid = not human_revision_required
        binding = {
            "applicable": applicable,
            "sha256": record_hash,
            "schema_version": None,
            "semantic_status": semantic,
            "semantic_valid": valid,
            "reason": None if valid else "human_revision_not_satisfied",
        }
        _register("human_revision", binding)
    else:
        _register(
            "human_revision",
            {"applicable": False, "sha256": None, "schema_version": None, "semantic_status": "NOT_APPLICABLE", "semantic_valid": True, "reason": None},
        )

    unique_blockers = list(dict.fromkeys(blockers))
    return bindings, unique_blockers


def _enforce_upstream_status_candidate_guard(case_dir: Path, effective_settings: dict[str, Any]) -> None:
    if effective_settings["effective_upstream_status_required"] is not True:
        return
    paths = effective_settings["upstream_status_paths"]
    if not paths:
        effective_settings["upstream_blocker"] = "UPSTREAM_STATUS_MISSING"
        raise CandidatePromotionUpstreamStatusBlocked({
            "status": "BLOCKED", "code": "CANDIDATE_PROMOTION_UPSTREAM_STATUS_MISSING",
            "message": "Candidate promotion requires configured upstream run status for selection/formal tier.",
            "candidate_package_created": False, "final_submit_created": False, **effective_settings,
        })
    statuses: list[dict[str, Any]] = []
    for item in paths:
        path_text = item["path"]
        path = Path(path_text) if path_text else None
        if path is None or not path.is_file():
            reason = "UPSTREAM_STATUS_PATH_MISSING_OR_UNSAFE"
        else:
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                payload = None
            reason = "UPSTREAM_STATUS_MALFORMED" if not isinstance(payload, dict) else _upstream_block_reason(payload, item["field"])
        statuses.append({"field": item["field"], "path": path_text, "status": "BLOCKED" if reason else "PASS", "reason": reason})
        if reason:
            effective_settings["upstream_status"] = statuses
            effective_settings["upstream_blocker"] = reason
            raise CandidatePromotionUpstreamStatusBlocked({
                "status": "BLOCKED", "code": "CANDIDATE_PROMOTION_UPSTREAM_BLOCKED",
                "message": "Candidate promotion blocked by persisted upstream run status.",
                "candidate_package_created": False, "final_submit_created": False, **effective_settings,
            })
        mismatches = _content_binding_mismatches(case_dir, payload, path_text)
        effective_settings["upstream_content_binding_checked"] = True
        if mismatches:
            effective_settings["upstream_status"] = statuses
            effective_settings["upstream_blocker"] = "UPSTREAM_STATUS_STALE"
            effective_settings["stale_upstream_status"] = True
            effective_settings["content_binding_mismatches"] = mismatches
            raise CandidatePromotionUpstreamStatusBlocked({
                "status": "BLOCKED", "code": "CANDIDATE_PROMOTION_UPSTREAM_STATUS_STALE",
                "message": "Candidate promotion blocked because persisted upstream validation content is stale or unsafe.",
                "candidate_package_created": False, "final_submit_created": False, **effective_settings,
            })
    effective_settings["upstream_status"] = statuses


def _enforce_claim_check_candidate_gate(
    case_dir: Path,
    effective_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    effective_settings = effective_settings or _effective_candidate_settings(case_dir)
    claim_check = check_claim_check_preflight(case_dir)
    claim_status = claim_check.get("status")
    effective_required = effective_settings["effective_claim_check_required"] is True
    structured_required = False
    config = read_json(case_dir / "case_config.json", default={}) or {}
    if isinstance(config, dict):
        structured_required = config.get("structured_claim_handoff_required") is True
    if claim_status == "PASS" or (claim_status == "SKIPPED" and not effective_required):
        if structured_required:
            try:
                claim_check["structured_claim_handoff"] = build_structured_claim_handoff(
                    case_dir, claim_check, required=True
                )
            except StructuredClaimHandoffBlocked as exc:
                raise CandidatePromotionClaimCheckBlocked(
                    {
                        "status": "BLOCKED",
                        "code": "CANDIDATE_PROMOTION_STRUCTURED_CLAIM_HANDOFF_BLOCKED",
                        "message": str(exc),
                        "underlying_codes": [exc.code],
                        "claim_check_status": claim_status,
                        "claim_check": claim_check,
                        "structured_claim_handoff_block": {
                            "code": exc.code,
                            "details": exc.details,
                        },
                        "candidate_package_created": False,
                        "final_submit_created": False,
                        **effective_settings,
                    }
                ) from exc
        else:
            claim_check["structured_claim_handoff"] = build_structured_claim_handoff(
                case_dir, claim_check, required=False
            )
        return claim_check

    underlying_codes = list(claim_check.get("blockers") or [])
    if claim_status == "SKIPPED" and effective_required:
        underlying_codes.append("CLAIM_CHECK_REQUIRED_BUT_NO_CORE_CLAIMS")
    if claim_status == "MANUAL_REVIEW_REQUIRED":
        underlying_codes.append("CLAIM_CHECK_MANUAL_REVIEW_REQUIRED")
    underlying_codes = list(dict.fromkeys(underlying_codes))
    raise CandidatePromotionClaimCheckBlocked(
        {
            "status": "BLOCKED",
            "code": "CANDIDATE_PROMOTION_CLAIM_CHECK_BLOCKED",
            "message": "Candidate promotion blocked by claim-check preflight.",
            "claim_check_status": claim_status,
            "underlying_codes": underlying_codes,
            "claim_check": claim_check,
            "candidate_package_created": False,
            "final_submit_created": False,
            **effective_settings,
        }
    )


def _enforce_human_revision_candidate_gate(case_dir: Path) -> dict[str, Any]:
    human_revision = check_human_revision(case_dir)
    revision_status = human_revision.get("status")
    revision_view = human_revision.get("human_revision") or {}
    required = revision_view.get("required") is True
    if revision_status in {"PASS", "SKIPPED"}:
        return human_revision
    if revision_status == "MANUAL_REVIEW_REQUIRED" and not required:
        return human_revision

    underlying_codes = list(human_revision.get("blockers") or [])
    underlying_codes.extend(human_revision.get("warnings") or [])
    underlying_codes = list(dict.fromkeys(str(code) for code in underlying_codes if str(code).strip()))
    raise CandidatePromotionHumanRevisionBlocked(
        {
            "status": "BLOCKED",
            "code": "CANDIDATE_PROMOTION_HUMAN_REVISION_BLOCKED",
            "message": "Candidate promotion blocked by HUMAN-REV preflight.",
            "human_revision_status": revision_status,
            "underlying_codes": underlying_codes,
            "human_revision": human_revision,
            "candidate_package_created": False,
            "final_submit_created": False,
        }
    )


def _latest_package(base_dir: Path, prefix: str) -> Path | None:
    if not base_dir.exists():
        return None
    candidates = sorted([p for p in base_dir.iterdir() if p.is_dir() and p.name.startswith(prefix)])
    return candidates[-1] if candidates else None


def _copy_file(src: Path, dst: Path, copied: list[dict[str, Any]]) -> None:
    if not src.exists() or not src.is_file():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    copied.append({"source": rel_path(src), "target": rel_path(dst), "sha256": sha256_file(dst)})


def _copy_dir(src: Path, dst: Path, copied: list[dict[str, Any]]) -> None:
    if not src.exists() or not src.is_dir():
        return
    for item in sorted(p for p in src.rglob("*") if p.is_file()):
        _copy_file(item, dst / item.relative_to(src), copied)


def _package_manifest_files(package_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in sorted(p for p in package_dir.rglob("*") if p.is_file() and p.name != "manifest.json"):
        rows.append(
            {
                "path": item.relative_to(package_dir).as_posix(),
                "size": item.stat().st_size,
                "sha256": sha256_file(item),
            }
        )
    return rows


def _read_manifest(path: Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    manifest = path / "manifest.json"
    if not manifest.exists():
        return None
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _diff_manifests(previous: dict[str, Any] | None, current_files: list[dict[str, Any]]) -> dict[str, Any]:
    previous_files = {}
    if previous:
        previous_files = {item["path"]: item for item in previous.get("files", []) if "path" in item}
    current_by_path = {item["path"]: item for item in current_files}
    added = sorted(set(current_by_path) - set(previous_files))
    removed = sorted(set(previous_files) - set(current_by_path))
    changed = sorted(
        path
        for path in set(previous_files).intersection(current_by_path)
        if previous_files[path].get("sha256") != current_by_path[path].get("sha256")
    )
    return {
        "previous_candidate": previous.get("package_id") if previous else None,
        "added": added,
        "removed": removed,
        "changed": changed,
        "summary": {
            "added_count": len(added),
            "removed_count": len(removed),
            "changed_count": len(changed),
        },
    }


def _same_case(left: str | None, right: Path) -> bool:
    if not left:
        return False
    normalized_left = left.replace("\\", "/").strip("./").lower()
    normalized_right = rel_path(right).replace("\\", "/").strip("./").lower()
    return normalized_left == normalized_right


def _collect_inherited_fatal_items(root: Path, case_dir: Path) -> list[dict[str, Any]]:
    work_tmp = root / "work_tmp"
    if not work_tmp.exists():
        return []
    inherited: list[dict[str, Any]] = []
    for run_dir in sorted((p for p in work_tmp.iterdir() if p.is_dir() and p.name.startswith("run_")), reverse=True):
        run_config = read_json(run_dir / "run_config.json", default={}) or {}
        if not _same_case(run_config.get("case_dir"), case_dir):
            continue
        risk_path = run_dir / "risk_summary.json"
        risk_summary = read_json(risk_path, default={}) or {}
        for item in risk_summary.get("risks", []):
            if not item.get("fatal"):
                continue
            inherited.append(
                {
                    "source_run_id": run_config.get("run_id") or run_dir.name,
                    "source_file": rel_path(risk_path),
                    "source_module": item.get("category"),
                    "severity": item.get("severity"),
                    "fatal": True,
                    "fatal_reason": item.get("message"),
                    "path": item.get("path"),
                    "created_at": item.get("created_at"),
                }
            )
    return inherited


def _write_emergency_risk_summary(package_dir: Path, inherited_fatal_items: list[dict[str, Any]]) -> None:
    status = "emergency_with_fatal_source" if inherited_fatal_items else "emergency_no_known_fatal_source"
    payload = {
        "schema_version": "x5_f0_emergency_risk_summary_v1",
        "generated_at": utc_now(),
        "emergency_export": True,
        "status": status,
        "inherited_fatal_count": len(inherited_fatal_items),
        "inherited_fatal_items": inherited_fatal_items,
        "final_submit_created": False,
        "not_final_submit_statement": "This candidate_emergency package is not final_submit and does not represent compliance approval.",
    }
    write_json(package_dir / "risk_summary.json", payload)
    rows = [
        {
            "source_run_id": item.get("source_run_id"),
            "source_module": item.get("source_module"),
            "fatal_reason": item.get("fatal_reason"),
            "path": item.get("path") or "",
            "source_file": item.get("source_file"),
        }
        for item in inherited_fatal_items
    ]
    body = markdown_table(
        rows,
        [
            ("source_run_id", "Source Run"),
            ("source_module", "Module"),
            ("fatal_reason", "Fatal Reason"),
            ("path", "Path"),
            ("source_file", "Source File"),
        ],
    )
    (package_dir / "risk_summary.md").write_text(
        f"""# Emergency Risk Summary

Status: `{status}`

- emergency_export: `true`
- inherited_fatal_count: `{len(inherited_fatal_items)}`
- final_submit_created: `false`
- not_final_submit: `true`

This package is not `final_submit` and does not represent compliance approval.

## Inherited Fatal Items

{body}
""",
        encoding="utf-8",
    )


def _write_supporting_docs(
    case_dir: Path,
    package_dir: Path,
    *,
    emergency: bool,
    copied: list[dict[str, Any]],
    inherited_fatal_items: list[dict[str, Any]],
) -> None:
    risk_source = case_dir / "output" / "validation" / "risk_summary.md"
    if emergency:
        _write_emergency_risk_summary(package_dir, inherited_fatal_items)
    elif risk_source.exists():
        _copy_file(risk_source, package_dir / "risk_summary.md", copied)
    else:
        (package_dir / "risk_summary.md").write_text(
            "# Risk Summary\n\nNo promoted risk_summary.md was found. Review the latest work_tmp run before release.\n",
            encoding="utf-8",
        )
    checklist = f"""# Submission Checklist

Status: `CANDIDATE_ONLY / NOT_FINAL_SUBMIT`

- final_submit_created: `false`
- emergency_export: `{str(emergency).lower()}`
- paper_pdf_present: `{str((package_dir / 'paper.pdf').exists()).lower()}`
- figures_present: `{str((package_dir / 'figures').exists()).lower()}`
- tables_present: `{str((package_dir / 'tables').exists()).lower()}`
- results_present: `{str((package_dir / 'results').exists()).lower()}`

Manual checks still required:

- units
- formulas
- literature support
- model explanation
- conclusion boundaries
- AI disclosure according to current contest rules
"""
    (package_dir / "submission_checklist.md").write_text(checklist, encoding="utf-8")

    disclosure_dir = case_dir / "ai_disclosure"
    disclosure_files = sorted(p.name for p in disclosure_dir.glob("*") if p.is_file()) if disclosure_dir.exists() else []
    ai_summary = f"""# AI Disclosure Summary

This is a summary stub for candidate review. It is not a full AI usage disclosure.

- ai_disclosure_dir_exists: `{str(disclosure_dir.exists()).lower()}`
- visible_disclosure_files: `{', '.join(disclosure_files) if disclosure_files else 'none'}`
- offline_import_template: `compliance/offline_ai_import_template.md`
- manual_completion_required_before_release: `true`
"""
    (package_dir / "ai_disclosure_summary.md").write_text(ai_summary, encoding="utf-8")

    code_dir = case_dir / "code"
    code_rows = []
    if code_dir.exists():
        for item in sorted(p for p in code_dir.rglob("*") if p.is_file()):
            code_rows.append({"path": rel_path(item), "size": item.stat().st_size, "sha256": sha256_file(item)})
    code_summary = "# Code Summary\n\n"
    code_summary += "Code is summarized for review; full source is not copied by this candidate exporter.\n\n"
    code_summary += markdown_table(code_rows, [("path", "Path"), ("size", "Size"), ("sha256", "SHA256")])
    (package_dir / "code_summary.md").write_text(code_summary, encoding="utf-8")


def package_candidate(
    *,
    case_dir: Path,
    root: Path = ROOT,
    run_id: str | None = None,
    emergency: bool = False,
    paper_only: bool = False,
    single_file: str | None = None,
    r3_approved_source_path: str | Path | None = None,
    r3_approval_input_path: str | Path | None = None,
) -> dict[str, Any]:
    case_identity = rel_path(case_dir, root)
    if Path(case_identity).is_absolute() or case_identity in {"", "."}:
        raise CandidatePromotionProvenanceBlocked(
            {
                "status": "BLOCKED",
                "code": "CANDIDATE_CASE_IDENTITY_UNAVAILABLE",
                "message": "Candidate packaging requires a stable repository-relative case identity.",
                "candidate_package_created": False,
                "final_submit_created": False,
            }
        )
    effective_settings = _effective_candidate_settings(case_dir)
    _enforce_upstream_status_candidate_guard(case_dir, effective_settings)
    claim_check_result = _enforce_claim_check_candidate_gate(case_dir, effective_settings)
    human_revision_result = _enforce_human_revision_candidate_gate(case_dir)

    config = read_json(case_dir / "case_config.json", default={}) or {}
    config = config if isinstance(config, dict) else {}
    upstream_artifact_bindings, binding_blockers = _build_upstream_artifact_bindings(
        case_dir,
        config,
        effective_settings,
        claim_check_result=claim_check_result,
        human_revision_result=human_revision_result,
    )
    if binding_blockers:
        raise CandidatePromotionUpstreamBindingBlocked(
            {
                "status": "BLOCKED",
                "code": "CANDIDATE_PROMOTION_UPSTREAM_BINDING_INVALID",
                "message": "Candidate promotion blocked by upstream artifact hash/semantic binding failure.",
                "candidate_package_created": False,
                "final_submit_created": False,
                "binding_blockers": binding_blockers,
                "upstream_artifact_bindings": upstream_artifact_bindings,
                **effective_settings,
            }
        )
    provenance_verification = _verify_source_provenance(case_dir, root, effective_settings)
    _provenance_gate_applies = (
        provenance_verification["source_provenance_required"]
        or provenance_verification["source_provenance_applicable"]
    )
    if _provenance_gate_applies and not provenance_verification["source_provenance_finalization_passed"]:
        raise CandidatePromotionProvenanceBlocked(
            {
                "status": "BLOCKED",
                "code": "CANDIDATE_PROMOTION_PROVENANCE_BLOCKED",
                "message": "Candidate promotion blocked by source provenance finalization failure.",
                "candidate_package_created": False,
                "final_submit_created": False,
                "source_provenance_verification": provenance_verification,
                **effective_settings,
            }
        )
    run_id = run_id or timestamp_id("run")
    r3_required_present = "r3_required" in config
    r3_required = config.get("r3_required") if r3_required_present else None
    r3_prepared: dict[str, Any] | None = None
    if not r3_required_present:
        raise CandidatePromotionUpstreamBindingBlocked(
            {
                "status": "BLOCKED",
                "code": "R3_REQUIRED_FLAG_MISSING",
                "candidate_package_created": False,
                "final_submit_created": False,
                "run_id": run_id,
            }
        )
    if not isinstance(r3_required, bool):
        raise CandidatePromotionUpstreamBindingBlocked(
            {
                "status": "BLOCKED",
                "code": "R3_REQUIRED_FLAG_INVALID",
                "candidate_package_created": False,
                "final_submit_created": False,
                "run_id": run_id,
            }
        )
    if r3_required is True:
        if r3_approved_source_path is None or r3_approval_input_path is None:
            raise CandidatePromotionUpstreamBindingBlocked(
                {
                    "status": "BLOCKED",
                    "code": "R3_APPROVAL_INPUT_REQUIRED",
                    "candidate_package_created": False,
                    "final_submit_created": False,
                    "run_id": run_id,
                }
            )
        try:
            r3_prepared = prepare_review_constraints_artifact(
                approved_source_path=r3_approved_source_path,
                approval_input_path=r3_approval_input_path,
                case_identity=case_identity,
                run_id=run_id,
            )
        except R3ValidationBlocked as exc:
            raise CandidatePromotionUpstreamBindingBlocked(
                {
                    "status": "BLOCKED",
                    "code": str(exc),
                    "candidate_package_created": False,
                    "final_submit_created": False,
                    "run_id": run_id,
                    "r3_required": True,
                }
            ) from exc
    try:
        canonical_result_index = build_index(case_dir, config, run_id)
    except CanonicalResultIndexBlocked as exc:
        result = dict(exc.result)
        result.update({
            "candidate_package_created": False,
            "final_submit_created": False,
            "run_id": run_id,
        })
        raise CandidatePromotionUpstreamBindingBlocked(result) from exc
    base_dir = root / ("candidate_emergency" if emergency else "candidate_submit")
    prefix = "emergency_" if emergency else "candidate_"
    previous_dir = _latest_package(base_dir, prefix)
    package_dir = base_dir / timestamp_id(prefix.rstrip("_"))
    package_dir.mkdir(parents=True, exist_ok=False)
    copied: list[dict[str, Any]] = []
    inherited_fatal_items = _collect_inherited_fatal_items(root, case_dir) if emergency else []

    if single_file and previous_dir:
        shutil.copytree(previous_dir, package_dir, dirs_exist_ok=True)
        src = case_dir / single_file
        if src.exists() and src.is_file():
            _copy_file(src, package_dir / single_file, copied)
    else:
        # Formal packaging accepts only the canonical production PDF.  Review
        # copies under final_candidate/paper_v*/handoff are never searched.
        pdf_path = case_dir / "paper" / "main.pdf"
        if not pdf_path.is_file():
            pdf_path = None
        if pdf_path:
            _copy_file(pdf_path, package_dir / "paper.pdf", copied)
        if not paper_only:
            _copy_dir(case_dir / "output" / "figures", package_dir / "figures", copied)
            _copy_dir(case_dir / "output" / "tables", package_dir / "tables", copied)
            _copy_dir(case_dir / "output" / "results", package_dir / "results", copied)
            _copy_file(case_dir / "output" / "result_summary.json", package_dir / "results" / "result_summary.json", copied)
            _copy_file(case_dir / "output" / "result_summary.txt", package_dir / "results" / "result_summary.txt", copied)

    _write_supporting_docs(
        case_dir,
        package_dir,
        emergency=emergency,
        copied=copied,
        inherited_fatal_items=inherited_fatal_items,
    )
    authoritative_index_path = package_dir / "authoritative_result_index.json"
    write_json(authoritative_index_path, canonical_result_index)
    authoritative_index_bytes = authoritative_index_path.read_bytes()
    authoritative_index_sha256 = hashlib.sha256(authoritative_index_bytes).hexdigest()
    structured_handoff = claim_check_result.get("structured_claim_handoff")
    claim_handoff_path: Path | None = None
    claim_handoff_sha256: str | None = None
    if isinstance(structured_handoff, dict) and structured_handoff.get("status") == "PASS":
        claim_handoff_path = package_dir / "claim_handoff.json"
        write_json(claim_handoff_path, structured_handoff)
        claim_handoff_bytes = claim_handoff_path.read_bytes()
        claim_handoff_sha256 = hashlib.sha256(claim_handoff_bytes).hexdigest()
    review_constraints_path: Path | None = None
    review_constraints_sha256: str | None = None
    if r3_prepared is not None:
        review_constraints_path = package_dir / "review_constraints.json"
        write_json(review_constraints_path, r3_prepared["artifact"])
        review_constraints_sha256 = hashlib.sha256(review_constraints_path.read_bytes()).hexdigest()
    files = _package_manifest_files(package_dir)
    previous_manifest = _read_manifest(previous_dir)
    diff = _diff_manifests(previous_manifest, files)
    # PACKAGE_ID_IS_NOT_TRUST_AUTHORITY: hashes and case/run bindings are authoritative.
    manifest = {
        "schema_version": "x5_f0_candidate_manifest_v1",
        "package_id": package_dir.name,
        "created_at": utc_now(),
        "case_dir": case_identity,
        "case_identity": case_identity,
        "run_id": run_id,
        "emergency_export": emergency,
        "status": "emergency_with_fatal_source" if emergency and inherited_fatal_items else ("emergency_candidate" if emergency else "candidate"),
        "inherited_fatal_count": len(inherited_fatal_items),
        "inherited_fatal_items": inherited_fatal_items,
        "paper_only": paper_only,
        "single_file": single_file,
        "final_submit_created": False,
        "for_official_submission": False,
        **effective_settings,
        "source_provenance_verification": provenance_verification,
        "upstream_artifact_bindings": upstream_artifact_bindings,
        "claim_handoff": {
            "path": "claim_handoff.json" if isinstance(structured_handoff, dict) and structured_handoff.get("status") == "PASS" else None,
            "sha256": claim_handoff_sha256,
            "schema_version": structured_handoff.get("schema_version") if isinstance(structured_handoff, dict) else None,
            "status": structured_handoff.get("status") if isinstance(structured_handoff, dict) else None,
            "legacy_claim_handoff": structured_handoff.get("legacy_claim_handoff") if isinstance(structured_handoff, dict) else None,
            "claim_count": len(structured_handoff.get("claims", [])) if isinstance(structured_handoff, dict) and isinstance(structured_handoff.get("claims"), list) else 0,
            "result_authority": "R1_CANONICAL_INDEX",
        },
        "r3_required": r3_required if r3_required_present else None,
        "review_constraints": {
            "path": "review_constraints.json" if review_constraints_path is not None else None,
            "sha256": review_constraints_sha256,
            "schema_version": R3_ARTIFACT_SCHEMA_VERSION if review_constraints_path is not None else None,
            "artifact_role": "r3_review_constraints" if review_constraints_path is not None else None,
            "approval_input_read_once": bool(r3_prepared and r3_prepared.get("approved_input_read_count") == 1),
        },
        "authoritative_result_index": {
            "path": "authoritative_result_index.json",
            "sha256": authoritative_index_sha256,
            "schema_version": canonical_result_index["schema_version"],
            "authority_scope_key": canonical_result_index["authority_scope_key"],
            "entry_count": len(canonical_result_index["entries"]),
        },
        "copied_files": copied,
        "files": files,
        "diff": diff,
        "excluded_by_design": [
            "full human gate directory",
            "full AI raw logs",
            "full evidence_map",
            "full citation_matrix",
        ],
    }
    manifest_path = package_dir / "manifest.json"
    write_json(manifest_path, manifest)
    diff_md = f"""# Candidate Diff

- previous_candidate: `{diff['previous_candidate'] or 'none'}`
- added_count: `{diff['summary']['added_count']}`
- removed_count: `{diff['summary']['removed_count']}`
- changed_count: `{diff['summary']['changed_count']}`

## Added
{chr(10).join('- ' + item for item in diff['added']) if diff['added'] else 'None'}

## Removed
{chr(10).join('- ' + item for item in diff['removed']) if diff['removed'] else 'None'}

## Changed
{chr(10).join('- ' + item for item in diff['changed']) if diff['changed'] else 'None'}
"""
    (package_dir / "candidate_diff.md").write_text(diff_md, encoding="utf-8")
    write_json(package_dir / "candidate_diff.json", diff)
    register_asset(
        root=root,
        asset_type="candidate_package",
        path=package_dir,
        source_script="package_candidate.py",
        source_data=inventory_source_data(case_dir),
        run_id=run_id,
        status=manifest["status"],
    )
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    return {
        "package_dir": rel_path(package_dir),
        "manifest": manifest,
        "candidate_manifest_path": rel_path(manifest_path, root),
        "candidate_manifest_sha256": manifest_sha256,
        "candidate_package_digest": sha256_path(package_dir),
        "case_identity": case_identity,
        "run_id": run_id,
        **effective_settings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="X5-F0 candidate package exporter")
    parser.add_argument("--case-dir", required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--emergency", action="store_true")
    parser.add_argument("--paper-only", action="store_true")
    parser.add_argument("--single-file", default=None)
    parser.add_argument("--r3-approved-source-path", default=None)
    parser.add_argument("--r3-approval-input-path", default=None)
    args = parser.parse_args()
    try:
        result = package_candidate(
            case_dir=Path(args.case_dir),
            run_id=args.run_id,
            emergency=args.emergency,
            paper_only=args.paper_only,
            single_file=args.single_file,
            r3_approved_source_path=args.r3_approved_source_path,
            r3_approval_input_path=args.r3_approval_input_path,
        )
    except (
        CandidatePromotionUpstreamStatusBlocked,
        CandidatePromotionClaimCheckBlocked,
        CandidatePromotionHumanRevisionBlocked,
        CandidatePromotionUpstreamBindingBlocked,
        CandidatePromotionProvenanceBlocked,
    ) as exc:
        print(json.dumps(exc.result, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
