"""X5-F0 one-click surgical runner.

Default usage:
    python run_case.py

The default path is intentionally conservative. It loads the active current
case, runs data health and lightweight validation, writes all artifacts to
work_tmp first, promotes only on hard-check success, and never creates
final_submit.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import PurePosixPath
from pathlib import Path
from typing import Any

from package_candidate import package_candidate
from x5_surgical_utils import (
    ROOT,
    WriteRegistry,
    add_risk,
    detect_current_case,
    has_fatal_risk,
    inventory_files,
    inventory_source_data,
    load_case_config,
    markdown_table,
    python_executable,
    read_json,
    register_asset,
    rel_path,
    sha256_file,
    run_subprocess_with_timeout,
    safe_promote,
    scan_bad_values,
    scan_structured_result_files,
    timestamp_id,
    utc_now,
    validate_result_summary_file,
    write_json,
    write_risk_summary,
)
from pipeline_core.python_tools.case_paths import resolve_case_relative_path
from pipeline_core.python_tools.human_gate import (
    COMPETITION_MODES,
    authorize_stage_transition,
    dispatch_competition_stage_action,
    verify_authorized_stage_transition,
    verify_authorized_stage_transition_for_case,
)
from pipeline_core.python_tools.model_authority import (
    requires_model_authority_binding,
    verify_model_authority,
)
from pipeline_core.python_tools.competition_model_governance import (
    evaluate_model_execution_governance,
)
from pipeline_core.python_tools.workflow_state import validate_workflow_state
from pipeline_core.python_tools.transition_case import (
    AUTHORIZATION_SCHEMA as CASE_TRANSITION_AUTHORIZATION_SCHEMA,
    write_transition_authorization,
)


STAGES = ["run", "validate", "package", "paper-only", "emergency-export", "release-with-risk"]
ISOLATED_LEGACY_METADATA_PATHS = {"$.schema_version", "$.generated_at", "$.run_id"}
SUBMISSION_TIERS = {"training", "test", "homework", "selection", "formal"}
TIER_DEFAULT_REQUIRED_TIERS = {"selection", "formal"}
DEFAULT_RESULT_CONSISTENCY_MANIFEST_PATH = "output/validation/result_consistency_manifest.json"
DEFAULT_XVAL_INDEPENDENT_ARTIFACT_PATH = "output/validation/xval_independent_check.json"


class IntakeGateWaiting(RuntimeError):
    """Stop the runner after intake until a human confirms Gate 1."""

# WriteRegistry tokens. Diagnostic only: see WriteRegistry in
# x5_surgical_utils.py. A token may only be required if the corresponding
# write genuinely completes before the registry snapshot embedded in
# risk_summary.json/run_result.json is captured (see
# _attach_write_registry_provenance). risk_summary.json, risk_summary.md,
# and run_result.json can never be observed tokens: the snapshot must be
# taken once, before either file exists, so it can be embedded identically
# in both — a file can never observe its own write. Those three are always
# reported via source_expected_post_snapshot_paths instead, explicitly
# unverified (source_expected_post_snapshot_write_verified = False).
ISOLATED_TOKEN_ERROR_LOG = "ISOLATED_SMOKE_ERROR_LOG"
ISOLATED_TOKEN_STAGED_SUMMARY_COPY = "ISOLATED_SMOKE_STAGED_SUMMARY_COPY"
ISOLATED_ALWAYS_REQUIRED_TOKENS = frozenset({ISOLATED_TOKEN_ERROR_LOG})

RUN_CASE_EXCEPTION_TOKEN_ERROR_LOG = "RUN_CASE_EXCEPTION_ERROR_LOG"
# _update_compliance_queue always runs before the after-snapshot in the
# except-Exception tail of run_case(), so its write is a genuine
# pre-snapshot observed write too, not merely an expected post-snapshot
# path — tracking it as a required token is a stronger guarantee than the
# expected-path fallback and still satisfies "must not be silently
# excluded from the registry contract."
RUN_CASE_EXCEPTION_TOKEN_COMPLIANCE_QUEUE = "RUN_CASE_EXCEPTION_COMPLIANCE_QUEUE"
RUN_CASE_EXCEPTION_REQUIRED_TOKENS = frozenset(
    {
        RUN_CASE_EXCEPTION_TOKEN_ERROR_LOG,
        RUN_CASE_EXCEPTION_TOKEN_COMPLIANCE_QUEUE,
    }
)

# Normal-success path (run_case()'s main try block, not the except tail).
# These four are the only pre-snapshot observed writes wired up so far —
# deterministic, unconditional, triggered directly by run_case() itself with
# no subprocess, quality-gate, or safe_promote involvement. When this scope
# is complete=true it means only these four writes are confirmed; it makes
# no claim about subprocess output, quality-gate output, safe_promote, or
# the run's overall provenance.
RUN_CASE_SUCCESS_TOKEN_RUN_CONFIG = "RUN_CASE_SUCCESS_RUN_CONFIG_WRITE"
RUN_CASE_SUCCESS_TOKEN_COMPLIANCE_QUEUE = "RUN_CASE_SUCCESS_COMPLIANCE_QUEUE_WRITE"
RUN_CASE_SUCCESS_TOKEN_COMPLIANCE_QUEUE_SNAPSHOT = "RUN_CASE_SUCCESS_COMPLIANCE_QUEUE_SNAPSHOT_WRITE"
RUN_CASE_SUCCESS_TOKEN_ERROR_LOG = "RUN_CASE_SUCCESS_ERROR_LOG_WRITE"
RUN_CASE_SUCCESS_REQUIRED_TOKENS = frozenset(
    {
        RUN_CASE_SUCCESS_TOKEN_RUN_CONFIG,
        RUN_CASE_SUCCESS_TOKEN_COMPLIANCE_QUEUE,
        RUN_CASE_SUCCESS_TOKEN_COMPLIANCE_QUEUE_SNAPSHOT,
        RUN_CASE_SUCCESS_TOKEN_ERROR_LOG,
    }
)
# The normal-success branch tracks a wide but still explicitly bounded
# subset of its pre-snapshot writes — unlike the isolated smoke and
# exception branches, which track their entire pre-snapshot observed write
# set. As of P6-PV1B2A4B this covers every pre-snapshot write this branch
# makes: the deterministic direct writes, the Data Health / PDF Checklist
# subprocess outputs, Quality Gates outputs, safe_promote's target/backup
# diff, and asset registration (register_asset / manifests/assets_manifest.json).
# Nothing pre-snapshot remains excluded, so excluded_classes is empty.
RUN_CASE_SUCCESS_SCOPE = "ALL_PRE_SNAPSHOT_WRITES"
RUN_CASE_SUCCESS_EXCLUDED_CLASSES: list[str] = []

# Conditional direct writes: still triggered directly by run_case() itself
# (no subprocess, quality-gate, or safe_promote involvement), but only when
# the corresponding runtime branch actually fires. Each token is added to
# required_tokens only for a given run if its branch's own success signal
# (a non-None return value, or a call known to write unconditionally once
# invoked) confirms the write actually happened — an inactive branch's
# token never appears in required_tokens for that run, so completeness is
# never demanded for writes that could not have occurred.
RUN_CASE_SUCCESS_TOKEN_RESULT_SUMMARY_JSON = "RUN_CASE_SUCCESS_RESULT_SUMMARY_JSON_WRITE"
RUN_CASE_SUCCESS_TOKEN_RESULT_SUMMARY_TXT = "RUN_CASE_SUCCESS_RESULT_SUMMARY_TXT_WRITE"
RUN_CASE_SUCCESS_TOKEN_EFFECTIVE_PAPER_CONFIG = "RUN_CASE_SUCCESS_EFFECTIVE_PAPER_CONFIG_WRITE"
RUN_CASE_SUCCESS_TOKEN_PAPER_PREFLIGHT_REPORT = "RUN_CASE_SUCCESS_PAPER_PREFLIGHT_REPORT_WRITE"
RUN_CASE_SUCCESS_TOKEN_FORMAL_PAPER_BUILD = "RUN_CASE_SUCCESS_FORMAL_PAPER_BUILD"
RUN_CASE_SUCCESS_TOKEN_RESULT_CONSISTENCY_REPORT = "RUN_CASE_SUCCESS_RESULT_CONSISTENCY_REPORT_WRITE"
RUN_CASE_SUCCESS_TOKEN_RELEASE_CHECK_JSON = "RUN_CASE_SUCCESS_RELEASE_CHECK_JSON_WRITE"
RUN_CASE_SUCCESS_TOKEN_RELEASE_CHECK_MD = "RUN_CASE_SUCCESS_RELEASE_CHECK_MD_WRITE"
RUN_CASE_SUCCESS_TOKEN_VALIDATION_SUMMARY_JSON = "RUN_CASE_SUCCESS_VALIDATION_SUMMARY_JSON_WRITE"
RUN_CASE_SUCCESS_TOKEN_VALIDATION_SUMMARY_MD = "RUN_CASE_SUCCESS_VALIDATION_SUMMARY_MD_WRITE"
# Data Health / PDF Checklist are run via subprocess. run_subprocess_with_timeout's
# return value (returncode/timed_out) never proves the target files were
# actually produced — only that the log file itself was written and the
# process's exit status. Each token below is therefore only ever confirmed
# by record_write's own independent existence check at registry-construction
# time, never by trusting the subprocess result or a function's return value.
RUN_CASE_SUCCESS_TOKEN_DATA_HEALTH_LOG = "RUN_CASE_SUCCESS_DATA_HEALTH_LOG_WRITE"
RUN_CASE_SUCCESS_TOKEN_DATA_HEALTH_JSON = "RUN_CASE_SUCCESS_DATA_HEALTH_JSON_WRITE"
RUN_CASE_SUCCESS_TOKEN_DATA_HEALTH_MD = "RUN_CASE_SUCCESS_DATA_HEALTH_MD_WRITE"
RUN_CASE_SUCCESS_TOKEN_PDF_CHECKLIST_LOG = "RUN_CASE_SUCCESS_PDF_CHECKLIST_LOG_WRITE"
RUN_CASE_SUCCESS_TOKEN_PDF_CHECKLIST_JSON = "RUN_CASE_SUCCESS_PDF_CHECKLIST_JSON_WRITE"
RUN_CASE_SUCCESS_TOKEN_PDF_CHECKLIST_MD = "RUN_CASE_SUCCESS_PDF_CHECKLIST_MD_WRITE"
RUN_CASE_SUCCESS_TOKEN_QG_REPORT_JSON = "RUN_CASE_SUCCESS_QG_REPORT_JSON_WRITE"
RUN_CASE_SUCCESS_TOKEN_QG_MANUAL_REVIEW_TODO = "RUN_CASE_SUCCESS_QG_MANUAL_REVIEW_TODO_WRITE"
RUN_CASE_SUCCESS_TOKEN_QG_REPORT_MD = "RUN_CASE_SUCCESS_QG_REPORT_MD_WRITE"
# safe_promote's return value is a dynamic list of relative paths (not a
# fixed set of files known at import time), so it cannot use one static
# token per file the way the writers above do. Instead this single token
# covers the whole target+backup diff for a run: it is only added to
# required_tokens when run_output_dir actually contains files to promote
# (see the pre-call snapshot in run_case()), and it only ever completes
# when every promoted target and every conditional backup independently
# verifies (existence, regular-file, sha256 match against the pre-call
# snapshot) — see the post-call verification block in run_case().
RUN_CASE_SUCCESS_TOKEN_SAFE_PROMOTE_DIFF_COMPLETE = "RUN_CASE_SUCCESS_SAFE_PROMOTE_DIFF_COMPLETE"
# register_asset writes to a root-level file (manifests/assets_manifest.json)
# shared across runs, so this token — like SAFE_PROMOTE_DIFF_COMPLETE — is a
# single dynamic-path token covering the whole set of asset entries this run
# is expected to add, not one static token per call. It only completes when
# every expected entry (built from the same conditions the register_asset
# call loop itself uses) is found, as an exact multiset, among the manifest
# entries whose run_id matches this run — never by trusting register_asset's
# return value or call count alone. See the pre/post verification block in
# run_case().
RUN_CASE_SUCCESS_TOKEN_ASSETS_MANIFEST_WRITE = "RUN_CASE_SUCCESS_ASSETS_MANIFEST_WRITE"
RUN_CASE_SUCCESS_CONDITIONAL_TOKENS = frozenset(
    {
        RUN_CASE_SUCCESS_TOKEN_RESULT_SUMMARY_JSON,
        RUN_CASE_SUCCESS_TOKEN_RESULT_SUMMARY_TXT,
        RUN_CASE_SUCCESS_TOKEN_EFFECTIVE_PAPER_CONFIG,
        RUN_CASE_SUCCESS_TOKEN_PAPER_PREFLIGHT_REPORT,
        RUN_CASE_SUCCESS_TOKEN_FORMAL_PAPER_BUILD,
        RUN_CASE_SUCCESS_TOKEN_RESULT_CONSISTENCY_REPORT,
        RUN_CASE_SUCCESS_TOKEN_RELEASE_CHECK_JSON,
        RUN_CASE_SUCCESS_TOKEN_RELEASE_CHECK_MD,
        RUN_CASE_SUCCESS_TOKEN_VALIDATION_SUMMARY_JSON,
        RUN_CASE_SUCCESS_TOKEN_VALIDATION_SUMMARY_MD,
        RUN_CASE_SUCCESS_TOKEN_DATA_HEALTH_LOG,
        RUN_CASE_SUCCESS_TOKEN_DATA_HEALTH_JSON,
        RUN_CASE_SUCCESS_TOKEN_DATA_HEALTH_MD,
        RUN_CASE_SUCCESS_TOKEN_PDF_CHECKLIST_LOG,
        RUN_CASE_SUCCESS_TOKEN_PDF_CHECKLIST_JSON,
        RUN_CASE_SUCCESS_TOKEN_PDF_CHECKLIST_MD,
        RUN_CASE_SUCCESS_TOKEN_QG_REPORT_JSON,
        RUN_CASE_SUCCESS_TOKEN_QG_MANUAL_REVIEW_TODO,
        RUN_CASE_SUCCESS_TOKEN_QG_REPORT_MD,
        RUN_CASE_SUCCESS_TOKEN_SAFE_PROMOTE_DIFF_COMPLETE,
        RUN_CASE_SUCCESS_TOKEN_ASSETS_MANIFEST_WRITE,
    }
)


def _attach_write_registry_provenance(
    source_provenance: dict[str, Any],
    registry: WriteRegistry,
    *,
    expected_post_snapshot_paths: list[str],
    scope: str = "PRE_SNAPSHOT_OBSERVED_WRITES",
    excluded_classes: list[str] | None = None,
) -> None:
    """Embed a snapshot of registry state into an existing provenance dict.

    Diagnostic only. Must never set source_worktree_clean_after,
    source_provenance_valid, safe_promotion_allowed,
    authorization_bindings_valid, final_freeze_ready, or
    final_submit_allowed — those remain out of scope for this primitive.
    source_write_registry_authoritative is always False: no scope, however
    complete, establishes authoritative provenance for the run.

    source_write_registry_complete reflects only the given scope — writes
    the registry actually saw complete before this snapshot was taken.
    "PRE_SNAPSHOT_OBSERVED_WRITES" (the default) means the branch's full set
    of pre-snapshot observed writes was tracked (isolated smoke and the
    exception tails). Callers that only track a narrower, explicitly
    excluded subset (e.g. normal-success's four core deterministic writes)
    must pass a distinct scope string and list what was excluded via
    excluded_classes, so completeness under that scope is never read as
    completeness of the whole run.

    expected_post_snapshot_paths lists files this branch will still write
    afterward (typically the file(s) this very snapshot gets embedded
    into); source_expected_post_snapshot_write_verified is always False
    here because this primitive performs no post-hoc verification of them.
    """

    source_provenance["source_write_registry_scope"] = scope
    source_provenance["source_write_registry_authoritative"] = False
    if excluded_classes is not None:
        source_provenance["source_write_registry_excluded_classes"] = list(excluded_classes)
    source_provenance["source_written_repo_paths"] = sorted(registry.written_repo_paths)
    source_provenance["source_external_written_paths"] = sorted(registry.external_written_paths)
    source_provenance["source_registry_errors"] = list(registry.registry_errors)
    source_provenance["source_required_write_tokens"] = sorted(registry.required_tokens)
    source_provenance["source_completed_write_tokens"] = sorted(registry.completed_tokens)
    source_provenance["source_write_registry_complete"] = registry.diagnostic_completeness()
    source_provenance["source_expected_post_snapshot_paths"] = list(expected_post_snapshot_paths)
    source_provenance["source_expected_post_snapshot_write_verified"] = False


def _normalize_git_status_path(raw_path: bytes) -> str:
    """Decode one Git porcelain path as a repository-relative POSIX path."""
    path = raw_path.decode("utf-8", errors="surrogateescape").replace("\\", "/")
    normalized = str(PurePosixPath(path))
    if not path or normalized in {".", ".."} or normalized.startswith("../") or normalized.startswith("/"):
        raise ValueError("status path is not repository-relative")
    return normalized


def _parse_porcelain_v1_z(stream: bytes) -> tuple[list[dict[str, str | None]], str | None]:
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
            path = _normalize_git_status_path(field[3:])
            original_path: str | None = None
            index += 1
            if "R" in xy or "C" in xy:
                if index >= len(fields) or not fields[index]:
                    raise ValueError("rename/copy status record is missing its original path")
                original_path = _normalize_git_status_path(fields[index])
                index += 1
            records.append({"xy": xy, "path": path, "original_path": original_path})
    except (UnicodeDecodeError, ValueError) as exc:
        return [], str(exc)
    return records, None


def _status_entry_text(record: dict[str, str | None]) -> str:
    original_path = record.get("original_path")
    return f"{record['xy']} {record['path']}" + (f" <- {original_path}" if original_path else "")


def _is_exact_status_path(path: object) -> bool:
    if not isinstance(path, str) or not path or any(token in path for token in ("*", "?", "[", "]")):
        return False
    try:
        return _normalize_git_status_path(path.encode("utf-8", errors="surrogateescape")) == path
    except ValueError:
        return False


def _classify_git_status_records(
    before_records: list[dict[str, str | None]],
    after_records: list[dict[str, str | None]],
    exact_generated_paths: set[str],
    expected_post_snapshot_paths: set[str],
) -> dict[str, Any]:
    """Classify raw status records using exact paths only; this function is diagnostic-only."""
    errors: list[str] = []

    def record_signature(record: dict[str, str | None]) -> tuple[str, str, str | None] | None:
        xy = record.get("xy")
        path = record.get("path")
        original_path = record.get("original_path")
        if not isinstance(xy, str) or len(xy) != 2 or not _is_exact_status_path(path):
            return None
        if original_path is not None and not _is_exact_status_path(original_path):
            return None
        return xy, path, original_path

    def record_paths(record: dict[str, str | None]) -> set[str]:
        return {path for path in (record.get("path"), record.get("original_path")) if isinstance(path, str)}

    before_signatures: set[tuple[str, str, str | None]] = set()
    before_paths: set[str] = set()
    for record in before_records:
        signature = record_signature(record)
        paths = record_paths(record)
        if signature is None or signature in before_signatures or paths & before_paths:
            errors.append("before status records are malformed or ambiguous")
            continue
        before_signatures.add(signature)
        before_paths.update(paths)

    after_signatures: set[tuple[str, str, str | None]] = set()
    observed_after_paths: set[str] = set()
    generated: list[dict[str, str | None]] = []
    unexpected: list[dict[str, str | None]] = []
    for record in after_records:
        signature = record_signature(record)
        paths = record_paths(record)
        if signature is None or signature in after_signatures or paths & observed_after_paths:
            errors.append("after status records are malformed or ambiguous")
            unexpected.append(record)
            continue
        after_signatures.add(signature)
        observed_after_paths.update(paths)
        if signature in before_signatures:
            continue
        if paths & before_paths:
            unexpected.append(record)
        elif paths and paths.issubset(exact_generated_paths):
            generated.append(record)
        else:
            unexpected.append(record)

    invalid_generated = sorted(path for path in exact_generated_paths if not _is_exact_status_path(path))
    invalid_expected = sorted(path for path in expected_post_snapshot_paths if not _is_exact_status_path(path))
    if invalid_generated:
        errors.append("exact_generated_paths contains a non-exact path")
    if invalid_expected:
        errors.append("expected_post_snapshot_paths contains a non-exact path")
    observed_expected = sorted(path for path in expected_post_snapshot_paths if path in observed_after_paths)
    if observed_expected:
        errors.append("expected_post_snapshot_paths contains an already-observed path")
    expected_post_snapshot_entries = [
        {"path": path}
        for path in sorted(expected_post_snapshot_paths - observed_after_paths)
        if _is_exact_status_path(path)
    ]
    return {
        "preexisting_status_entries": before_records,
        "generated_status_entries_after": generated,
        "expected_post_snapshot_entries": expected_post_snapshot_entries,
        "unexpected_status_entries_after": unexpected,
        "classification_errors": errors,
    }


def _git_provenance_snapshot(repo_root: Path) -> dict[str, Any]:
    """Capture raw Git state without assigning any trust or promotion meaning."""
    errors: list[str] = []

    def run_git(arguments: list[str], label: str, *, text: bool = True) -> tuple[str | bytes | None, bool]:
        try:
            completed = subprocess.run(
                ["git", *arguments],
                cwd=repo_root,
                capture_output=True,
                text=text,
                check=False,
            )
        except OSError as exc:
            errors.append(f"{label}: {type(exc).__name__}: {exc}")
            return None, False
        if completed.returncode != 0:
            detail = completed.stderr or completed.stdout or f"exit {completed.returncode}"
            if isinstance(detail, bytes):
                detail = detail.decode("utf-8", errors="replace")
            detail = detail.strip()
            errors.append(f"{label}: {detail[:200]}")
            return None, False
        return completed.stdout, True

    commit_output, commit_ok = run_git(["rev-parse", "--verify", "HEAD"], "git rev-parse")
    branch_output, branch_ok = run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], "git symbolic-ref")
    if not branch_ok and commit_ok:
        errors.pop()
    status_output, status_ok = run_git(["status", "--porcelain=v1", "-z", "--untracked-files=all"], "git status", text=False)
    commit = commit_output.strip() if commit_ok and isinstance(commit_output, str) else None
    branch = branch_output.strip() if branch_ok and isinstance(branch_output, str) else None
    status_records: list[dict[str, str | None]] = []
    if status_ok:
        status_records, parse_error = _parse_porcelain_v1_z(status_output if isinstance(status_output, bytes) else b"")
        if parse_error:
            errors.append(f"git status parse: {parse_error}")
            status_ok = False
    status_entries = [_status_entry_text(record) for record in status_records]
    capture_ok = commit_ok and status_ok
    return {
        "commit": commit,
        "branch": branch,
        "detached_head": bool(commit_ok and not branch_ok),
        "status_entries": status_entries,
        "status_records": status_records,
        "raw_worktree_clean": len(status_records) == 0 if status_ok else None,
        "capture_ok": capture_ok,
        "capture_error": "; ".join(errors) if errors else None,
    }


def _runtime_source_provenance(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Build one diagnostic-only provenance record from two Git snapshots."""
    capture_ok = before["capture_ok"] is True and after["capture_ok"] is True
    stable = bool(capture_ok and before["commit"] and after["commit"] and before["commit"] == after["commit"])
    errors = [item for item in (before.get("capture_error"), after.get("capture_error")) if item]
    if not capture_ok:
        status = "CAPTURE_UNAVAILABLE"
    elif not stable:
        status = "COMMIT_CHANGED_DURING_RUN"
    else:
        status = "CAPTURED_UNCLASSIFIED_OUTPUTS"
    return {
        "source_commit_before": before["commit"],
        "source_commit_after": after["commit"],
        "source_branch": before["branch"],
        "source_detached_head": before["detached_head"],
        "source_worktree_clean_before": before["raw_worktree_clean"],
        "source_worktree_clean_after_raw": after["raw_worktree_clean"],
        "source_status_entries_before": before["status_entries"],
        "source_status_entries_after_raw": after["status_entries"],
        "source_status_records_before": before.get("status_records", []),
        "source_status_records_after_raw": after.get("status_records", []),
        "source_commit_stable": stable,
        "source_provenance_capture_ok": capture_ok,
        "source_provenance_status": status,
        "source_provenance_errors": errors,
    }


PROVENANCE_SAFE_INPUT_SNAPSHOT_TOKEN = "PROVENANCE_SAFE_INPUT_SNAPSHOT_WRITE"
PROVENANCE_SAFE_BINDING_EVIDENCE_PATH = "work_tmp/ov11b2_artifact_binding_evidence.json"


def _provenance_safe_relative_to(root: Path, path: Path) -> str:
    """Return a root-relative POSIX path or fail closed."""

    return path.resolve().relative_to(root.resolve()).as_posix()


def _provenance_safe_reject_symlink_components(base: Path, relative: Path) -> Path:
    """Resolve a case-relative path without allowing a symlink escape."""

    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("provenance-safe path must be a non-empty case-relative path")
    resolved_base = base.resolve()
    candidate = base / relative
    current = base
    for part in relative.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError(f"provenance-safe path traverses a symlink: {relative.as_posix()}")
    resolved_candidate = candidate.resolve()
    try:
        resolved_candidate.relative_to(resolved_base)
    except ValueError as exc:
        raise ValueError(f"provenance-safe path escapes case root: {relative.as_posix()}") from exc
    return resolved_candidate


def _provenance_safe_output_dir(case_dir: Path, requested: str | Path) -> Path:
    """Validate an explicit output directory below ``case_dir/work_tmp`` only."""

    raw = Path(requested)
    if raw.is_absolute() or ".." in raw.parts or not raw.parts or raw.parts[0] != "work_tmp":
        raise ValueError("provenance-safe output must be a relative path below case work_tmp/")
    work_tmp = case_dir / "work_tmp"
    if work_tmp.exists() and work_tmp.is_symlink():
        raise ValueError("case work_tmp/ must not be a symlink in provenance-safe mode")
    work_tmp.mkdir(parents=True, exist_ok=True)
    resolved_work_tmp = work_tmp.resolve()
    resolved_target = _provenance_safe_reject_symlink_components(case_dir, raw)
    try:
        resolved_target.relative_to(resolved_work_tmp)
    except ValueError as exc:
        raise ValueError("provenance-safe output escapes case work_tmp/") from exc
    if resolved_target.exists():
        raise ValueError("provenance-safe output directory must not already exist")
    return resolved_target


def _provenance_safe_input_file(case_dir: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} path is missing")
    candidate = _provenance_safe_reject_symlink_components(case_dir, Path(value))
    if not candidate.is_file():
        raise ValueError(f"{label} is missing or is not a regular file")
    return candidate


def _provenance_safe_input_binding(case_dir: Path, path: Path, logical_name: str, source_role: str) -> dict[str, Any]:
    """Build an ``upstream_content_bindings`` entry.

    ``relative_path`` must be case_dir-relative: this is the contract
    ``package_candidate.py::_content_binding_mismatches()`` resolves it
    against (``case_dir / relative_path``), and matches the case-dir-relative
    paths ``run_case.py::_validated_content_bindings()`` has always produced
    (e.g. ``"output/result_summary.json"``). Passing the worktree root here
    instead produces a doubled ``cases/<case>/cases/<case>/...`` path on the
    consumer side.
    """

    return {
        "logical_name": logical_name,
        "relative_path": _provenance_safe_relative_to(case_dir, path),
        "sha256": sha256_file(path),
        "required_for_candidate_validation": True,
        "source_role": source_role,
    }


def run_provenance_safe(
    case_dir: Path,
    output_dir: str | Path,
    *,
    command_argv: list[str] | None = None,
) -> dict[str, Any]:
    """Create a work_tmp-only provenance record without promotion or packaging.

    This explicit mode is intentionally narrower than :func:`run_case`: it
    never calls data/model/paper gates, safe_promote, assets registration,
    compliance writers, candidate packaging, or final freezing.  It reads
    the pre-existing 2025C-style canonical result and OV11B2 evidence, then
    emits only the established ``run_result.json``/``risk_summary.json``
    artifact pair plus runtime metadata below a caller-selected ``work_tmp``
    child.  The regular runner's default behavior is unchanged.
    """

    root = ROOT.resolve()
    case_dir = case_dir.resolve()
    safe_output = _provenance_safe_output_dir(case_dir, output_dir)
    provenance_before = _git_provenance_snapshot(root)
    started_at = utc_now()
    safe_output.mkdir(parents=True, exist_ok=False)
    risks: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    case_config: dict[str, Any] = {}
    paper_source_commit: str | None = None

    config_path = case_dir / "case_config.json"
    try:
        config_path = _provenance_safe_input_file(case_dir, "case_config.json", "case_config")
        loaded_config = read_json(config_path, default=None)
        if not isinstance(loaded_config, dict):
            raise ValueError("case_config is unreadable or is not a JSON object")
        case_config = loaded_config
        bindings.append(_provenance_safe_input_binding(case_dir, config_path, "case_config", "case_config"))
        paper_source_commit = case_config.get("source_commit") if isinstance(case_config.get("source_commit"), str) else None
        if not paper_source_commit:
            raise ValueError("case_config source_commit is missing")

        canonical = _provenance_safe_input_file(
            case_dir, case_config.get("canonical_result_path"), "canonical result"
        )
        canonical_hash = sha256_file(canonical)
        expected_canonical_hash = str(case_config.get("canonical_result_sha256") or "")
        if canonical_hash.lower() != expected_canonical_hash.lower():
            raise ValueError("canonical result SHA-256 does not match case_config")
        bindings.append(_provenance_safe_input_binding(case_dir, canonical, "canonical_result", "result_summary"))

        b2_pdf = _provenance_safe_input_file(
            case_dir, case_config.get("ov11b2_isolated_rebuild_output_path"), "OV11B2 PDF"
        )
        b2_hash = sha256_file(b2_pdf)
        expected_b2_hash = str(case_config.get("ov11b2_isolated_rebuild_sha256") or "")
        if b2_hash.lower() != expected_b2_hash.lower():
            raise ValueError("OV11B2 PDF SHA-256 does not match case_config")
        bindings.append(_provenance_safe_input_binding(case_dir, b2_pdf, "ov11b2_pdf", "paper_pdf"))

        binding_evidence = _provenance_safe_input_file(
            case_dir, PROVENANCE_SAFE_BINDING_EVIDENCE_PATH, "OV11B2 binding evidence"
        )
        binding_payload = read_json(binding_evidence, default=None)
        if not isinstance(binding_payload, dict):
            raise ValueError("OV11B2 binding evidence is unreadable")
        evidence_hash = str(((binding_payload.get("artifacts") or {}).get("isolated_pdf_sha256")) or "")
        if evidence_hash.lower() != b2_hash.lower():
            raise ValueError("OV11B2 binding evidence does not bind the configured PDF")
        bindings.append(_provenance_safe_input_binding(case_dir, binding_evidence, "ov11b2_binding_evidence", "provenance_evidence"))
    except ValueError as exc:
        add_risk(
            risks,
            severity="ERROR",
            category="provenance_safe_input",
            message=str(exc),
            fatal=True,
        )

    if provenance_before.get("raw_worktree_clean") is not True:
        add_risk(
            risks,
            severity="ERROR",
            category="provenance_safe_worktree",
            message="initial worktree is not clean; provenance-safe execution is blocked",
            fatal=True,
        )

    run_id = safe_output.name
    input_snapshot = {
        "schema_version": "x5_provenance_safe_runtime_input_v1",
        "run_id": run_id,
        "runner_commit": provenance_before.get("commit"),
        "paper_source_commit": paper_source_commit,
        "started_at": started_at,
        "command": list(command_argv or []),
        "initial_worktree_clean": provenance_before.get("raw_worktree_clean"),
        "input_bindings": bindings,
        "candidate_package_authorized": False,
        "final_freeze_authorized": False,
        "final_submit_creation_authorized": False,
    }
    input_snapshot_path = safe_output / "input_binding_snapshot.json"
    write_json(input_snapshot_path, input_snapshot)
    registry = WriteRegistry(root=root, required_tokens=frozenset({PROVENANCE_SAFE_INPUT_SNAPSHOT_TOKEN}))
    registry.record_write(input_snapshot_path, token=PROVENANCE_SAFE_INPUT_SNAPSHOT_TOKEN)

    provenance_after = _git_provenance_snapshot(root)
    source_provenance = _runtime_source_provenance(provenance_before, provenance_after)
    expected_post_snapshot_paths = [
        _provenance_safe_relative_to(root, safe_output / "risk_summary.json"),
        _provenance_safe_relative_to(root, safe_output / "risk_summary.md"),
        _provenance_safe_relative_to(root, safe_output / "run_result.json"),
        _provenance_safe_relative_to(root, safe_output / "provenance_runtime_metadata.json"),
    ]
    _attach_write_registry_provenance(
        source_provenance,
        registry,
        expected_post_snapshot_paths=expected_post_snapshot_paths,
        scope=RUN_CASE_SUCCESS_SCOPE,
        excluded_classes=RUN_CASE_SUCCESS_EXCLUDED_CLASSES,
    )
    source_provenance.update(
        {
            "provenance_safe_mode": True,
            "runner_commit": provenance_before.get("commit"),
            "paper_source_commit": paper_source_commit,
            "initial_worktree_clean": provenance_before.get("raw_worktree_clean"),
        }
    )

    fatal_count = sum(1 for item in risks if item.get("fatal"))
    runtime_complete = fatal_count == 0
    semantic_status = "PASS" if runtime_complete else "BLOCKED"
    completed_at = utc_now()
    write_risk_summary(safe_output, risks, source_provenance=source_provenance)
    risk_path = safe_output / "risk_summary.json"
    risk_payload = read_json(risk_path, default={}) or {}
    risk_payload.update(
        {
            "run_id": run_id,
            "status": "ok" if runtime_complete else "fatal",
            "risk_status": "PASS" if runtime_complete else "BLOCK",
            "validation_continue_allowed": runtime_complete,
            "safe_promotion_allowed": False,
            "semantic_status": semantic_status,
            "upstream_content_bindings": bindings,
            "candidate_package_authorized": False,
            "final_freeze_authorized": False,
            "final_submit_creation_authorized": False,
            "candidate_package_created": False,
            "final_submit_created": False,
        }
    )
    write_json(risk_path, risk_payload)

    result = {
        "run_id": run_id,
        "stage": "provenance-safe",
        "status": "ok" if runtime_complete else "fatal",
        "risk_status": "PASS" if runtime_complete else "BLOCK",
        "semantic_status": semantic_status,
        "fatal_count": fatal_count,
        "run_dir": _provenance_safe_relative_to(root, safe_output),
        "runner_commit": provenance_before.get("commit"),
        "paper_source_commit": paper_source_commit,
        "started_at": started_at,
        "completed_at": completed_at,
        "command": list(command_argv or []),
        "runner_exit_code": 0 if runtime_complete else 2,
        "initial_worktree_clean": provenance_before.get("raw_worktree_clean"),
        "source_provenance": source_provenance,
        "upstream_content_bindings": bindings,
        "validation_continue_allowed": runtime_complete,
        "safe_promotion_allowed": False,
        "candidate_package_authorized": False,
        "final_freeze_authorized": False,
        "final_submit_creation_authorized": False,
        "candidate_package_created": False,
        "final_submit_created": False,
    }
    result_path = safe_output / "run_result.json"
    write_json(result_path, result)
    metadata = {
        "schema_version": "x5_provenance_safe_runtime_metadata_v1",
        "run_id": run_id,
        "runner_commit": provenance_before.get("commit"),
        "paper_source_commit": paper_source_commit,
        "started_at": started_at,
        "completed_at": completed_at,
        "command": list(command_argv or []),
        "runner_exit_code": result["runner_exit_code"],
        "initial_worktree_clean": provenance_before.get("raw_worktree_clean"),
        "semantic_status": semantic_status,
        "input_bindings": bindings,
        "output_artifacts": [
            {"relative_path": _provenance_safe_relative_to(root, result_path), "sha256": sha256_file(result_path)},
            {"relative_path": _provenance_safe_relative_to(root, risk_path), "sha256": sha256_file(risk_path)},
            {"relative_path": _provenance_safe_relative_to(root, safe_output / "risk_summary.md"), "sha256": sha256_file(safe_output / "risk_summary.md")},
        ],
        "candidate_package_authorized": False,
        "final_freeze_authorized": False,
        "final_submit_creation_authorized": False,
        "candidate_package_created": False,
        "final_submit_created": False,
    }
    write_json(safe_output / "provenance_runtime_metadata.json", metadata)
    return result


def _effective_run_gate_settings(
    case_dir: Path,
    case_config: dict[str, Any],
    risks: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Overlay tier defaults onto existing gate fields without changing the case file."""

    effective_config = dict(case_config)
    raw_tier = case_config.get("submission_tier")
    submission_tier = str(raw_tier).strip().lower() if raw_tier is not None and str(raw_tier).strip() else "training"
    tier_default_applied: list[str] = []
    explicitly_disabled: list[str] = []
    tier_requires_defaults = submission_tier in TIER_DEFAULT_REQUIRED_TIERS

    if raw_tier is not None and str(raw_tier).strip() and submission_tier not in SUBMISSION_TIERS:
        add_risk(
            risks,
            severity="WARNING",
            category="tier_defaults",
            message=f"UNKNOWN_SUBMISSION_TIER: {submission_tier}; current safe behavior is preserved",
            fatal=False,
        )

    for field in ("result_consistency_required", "result_consistency_enabled"):
        if case_config.get(field) is None:
            effective_config[field] = tier_requires_defaults
            if tier_requires_defaults:
                tier_default_applied.append(field)
        elif tier_requires_defaults and case_config.get(field) is False:
            explicitly_disabled.append(field)

    if case_config.get("result_consistency_manifest_path") is None:
        effective_config["result_consistency_manifest_path"] = DEFAULT_RESULT_CONSISTENCY_MANIFEST_PATH
        if tier_requires_defaults:
            tier_default_applied.append("result_consistency_manifest_path")

    xval_explicitly_disabled = False
    for field in ("xval_independent_required", "xval_independent_enabled"):
        if case_config.get(field) is None:
            effective_config[field] = tier_requires_defaults
            if tier_requires_defaults:
                tier_default_applied.append(field)
        elif tier_requires_defaults and case_config.get(field) is False:
            xval_explicitly_disabled = True
    if case_config.get("xval_independent_artifact_path") is None:
        effective_config["xval_independent_artifact_path"] = DEFAULT_XVAL_INDEPENDENT_ARTIFACT_PATH
        if tier_requires_defaults:
            tier_default_applied.append("xval_independent_artifact_path")
    if xval_explicitly_disabled:
        explicitly_disabled.append("xval_independent")

    paper_config_path = case_dir / "paper" / "paper_preflight_config.json"
    paper_config = read_json(paper_config_path, default={}) if paper_config_path.is_file() else {}
    paper_config = paper_config if isinstance(paper_config, dict) else {}
    paper_enabled_explicit = paper_config.get("enabled") is not None
    if paper_enabled_explicit:
        effective_paper_enabled = paper_config.get("enabled") is True
        if tier_requires_defaults and paper_config.get("enabled") is False:
            explicitly_disabled.append("paper_preflight")
    else:
        effective_paper_enabled = tier_requires_defaults
        if tier_requires_defaults and paper_config_path.is_file():
            tier_default_applied.append("paper_preflight")

    for field in explicitly_disabled:
        add_risk(
            risks,
            severity="WARNING",
            category="tier_defaults",
            message=f"TIER_DEFAULT_EXPLICITLY_DISABLED: {field}",
            fatal=False,
        )

    if tier_requires_defaults and not paper_config_path.is_file():
        add_risk(
            risks,
            severity="ERROR",
            category="paper_hard_error_preflight",
            message="PAPER_PREFLIGHT_CONFIG_MISSING: selection/formal tier requires paper/paper_preflight_config.json",
            fatal=True,
            path=rel_path(paper_config_path),
            recommendation="Add the existing paper preflight configuration before promotion.",
        )

    settings = {
        "submission_tier": submission_tier,
        "effective_result_consistency_required": effective_config.get("result_consistency_required") is True,
        "effective_result_consistency_enabled": effective_config.get("result_consistency_enabled") is True,
        "effective_result_consistency_manifest_path": effective_config.get("result_consistency_manifest_path"),
        "effective_xval_independent_required": effective_config.get("xval_independent_required") is True,
        "effective_xval_independent_enabled": effective_config.get("xval_independent_enabled") is True,
        "effective_xval_independent_artifact_path": effective_config.get("xval_independent_artifact_path"),
        "effective_paper_preflight_enabled": effective_paper_enabled,
        "tier_default_applied": tier_default_applied,
        "tier_default_explicitly_disabled": explicitly_disabled,
    }
    return effective_config, settings


def _ensure_static_templates(root: Path) -> None:
    (root / "manifests").mkdir(exist_ok=True)
    assets_manifest = root / "manifests" / "assets_manifest.json"
    if not assets_manifest.exists():
        write_json(
            assets_manifest,
            {
                "schema_version": "x5_f0_assets_manifest_v1",
                "status": "MINIMAL_IDENTITY_BINDING",
                "allowed_types": ["result", "figure", "table", "paper_pdf", "candidate_package"],
                "assets": [],
                "created_at": utc_now(),
                "updated_at": utc_now(),
            },
        )
    compliance_dir = root / "compliance"
    compliance_dir.mkdir(exist_ok=True)
    offline_template = compliance_dir / "offline_ai_import_template.md"
    if not offline_template.exists():
        offline_template.write_text(
            """# Offline AI Import Template

Paste offline AI interaction summaries here before release. Do not paste private raw logs unless contest rules allow it.

## Batch Import Format

| Date | Tool | Version | Task Stage | Prompt Summary | Output Summary | Adopted | Human Modification | Needs Manual Disclosure |
|---|---|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |  | yes |
""",
            encoding="utf-8",
        )
    release_template = root / "release_authorization.json"
    if not release_template.exists():
        write_json(
            release_template,
            {
                "schema_version": "x5_f0_release_authorization_v1",
                "final_submit_auto_creation_allowed": False,
                "release_requires_manual_authorization": True,
                "captain_confirmation": {
                    "name": "",
                    "approved": False,
                    "approved_at": None,
                    "note": "Default release path requires captain confirmation.",
                },
                "member_approvals": [
                    {"name": "", "role": "member_1", "approved": False, "approved_at": None},
                    {"name": "", "role": "member_2", "approved": False, "approved_at": None},
                    {"name": "", "role": "member_3", "approved": False, "approved_at": None},
                ],
                "two_of_three_allowed_when_captain_unavailable": True,
                "fatal_risk_override_requires_record": True,
                "fatal_risk_override_record": "",
                "authorization_log": [],
            },
        )


def _update_compliance_queue(root: Path, run_id: str, risks: list[dict[str, Any]], generated_files: list[str]) -> dict[str, Any]:
    queue_path = root / "compliance" / "compliance_todo_queue.json"
    existing = read_json(queue_path, default={}) or {}
    items = existing.get("items") or [
        {
            "id": "manual_ai_disclosure_completion",
            "category": "ai_disclosure",
            "source": "manual_required",
            "status": "open",
            "blocking_before_release": True,
            "description": "Complete AI tool usage details according to the current contest rules.",
        },
        {
            "id": "offline_ai_import_review",
            "category": "ai_disclosure",
            "source": "user_manual_import",
            "status": "open",
            "blocking_before_release": False,
            "description": "Import summaries of offline AI conversations if any were used.",
        },
        {
            "id": "official_rules_manual_check",
            "category": "compliance",
            "source": "manual_required",
            "status": "open",
            "blocking_before_release": True,
            "description": "Manually verify the current official contest rules before release.",
        },
        {
            "id": "release_authorization_completion",
            "category": "release",
            "source": "manual_required",
            "status": "open",
            "blocking_before_release": True,
            "description": "Fill release_authorization.json before any release action.",
        },
    ]
    open_items = [item for item in items if item.get("status") != "done"]
    blocking_items = [item for item in open_items if item.get("blocking_before_release")]
    payload = {
        "schema_version": "x5_f0_compliance_todo_queue_v1",
        "updated_at": utc_now(),
        "last_run_id": run_id,
        "auto_ai_disclosure_completed": False,
        "note": "This queue records machine-visible generation and manual disclosure todos. It is not automatic AI disclosure.",
        "counts": {
            "open_items": len(open_items),
            "blocking_before_release": len(blocking_items),
            "nonblocking_items": len(open_items) - len(blocking_items),
            "fatal_risks_in_last_run": sum(1 for risk in risks if risk.get("fatal")),
        },
        "machine_visible_records": (existing.get("machine_visible_records") or [])[-20:]
        + [
            {
                "run_id": run_id,
                "created_at": utc_now(),
                "source_script": "run_case.py",
                "generated_files": generated_files,
                "fatal_risk_count": sum(1 for risk in risks if risk.get("fatal")),
            }
        ],
        "items": items,
        "offline_ai_import_template": "compliance/offline_ai_import_template.md",
    }
    write_json(queue_path, payload)
    return payload


def _write_result_summary(run_dir: Path, case_dir: Path, case_config: dict[str, Any], data_health: dict[str, Any]) -> dict[str, Any]:
    existing_output = {
        "figures": inventory_files(case_dir / "output" / "figures"),
        "tables": inventory_files(case_dir / "output" / "tables"),
        "results": inventory_files(case_dir / "output" / "results"),
    }
    payload = {
        "schema_version": "x5_f0_result_summary_v1",
        "result_schema_id": "x5_f0_canonical_result_schema_v1",
        "producer_schema_version": "x5_f0_result_summary_v1",
        "generated_at": utc_now(),
        "run_id": run_dir.name,
        "case_dir": rel_path(case_dir),
        "case_id": case_config.get("case_id", case_dir.name),
        "mode": "surgical_runner_minimal",
        "not_a_model_claim": True,
        "final_submit_created": False,
        "data_health_status": data_health.get("status"),
        "data_file_count": data_health.get("file_count", 0),
        "existing_output_inventory": existing_output,
        "conclusions": [],
        "limitations": [
            "X5-F0 does not implement automatic modeling.",
            "No global optimality claim is made.",
            "Predictions, if present in case outputs, must remain labeled as predictions.",
        ],
    }
    output_dir = run_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "result_summary.json", payload)
    txt = f"""X5-F0 Result Summary

run_id: {run_dir.name}
case_id: {payload['case_id']}
data_health_status: {payload['data_health_status']}
final_submit_created: false

This summary was generated by the minimal surgical runner. It does not claim automatic modeling, global optimality, or official submission readiness.
"""
    (output_dir / "result_summary.txt").write_text(txt, encoding="utf-8")
    return payload


def _write_math_core_summary(
    run_dir: Path,
    case_dir: Path,
    model_config: dict[str, Any],
    modeling_handoff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from math_core.registry import run_model

    effective_model_config = dict(model_config)
    if modeling_handoff is not None:
        # The existing registry remains the solver consumer. Unknown context
        # fields are intentionally additive and do not alter model formulas.
        effective_model_config["model_building_handoff"] = modeling_handoff
    result = run_model(effective_model_config, run_id=run_dir.name, case_dir=case_dir)
    output_dir = run_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "result_summary.json", result)
    write_json(run_dir / "result_summary.json", result)
    txt = f"""X5-F1 Math Core Result Summary

run_id: {run_dir.name}
model_id: {result.get('model_id')}
model_family: {result.get('model_family')}
success: {str(result.get('success')).lower()}
status: {result.get('status')}
fatal_count: {result.get('fatal_count')}
warning_count: {result.get('warning_count')}
final_submit_created: false

This summary was generated by the minimal math_core baseline. It does not claim AutoML, full modeling coverage, or official submission readiness.
"""
    (output_dir / "result_summary.txt").write_text(txt, encoding="utf-8")
    return result


def _run_pre_modeling_production_wiring(
    case_dir: Path,
    run_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run intake first, then consume the existing contract chain after Gate 1."""
    from pipeline_core.intake.problem_intake import build_problem_contract_candidate
    from pipeline_core.router.problem_type_router import (
        build_output,
        prepare_model_building_handoff_inputs,
        validate_model_building_handoff,
        validate_problem_contract,
        write_model_building_handoff,
        write_case_outputs,
    )
    from pipeline_core.model_route.model_route_stage import run_model_route_stage
    from pipeline_core.python_tools.human_gate import evaluate_human_gate

    config = read_json(case_dir / "case_config.json", default={}) or {}
    configured_contract = config.get("problem_contract_path")
    if not isinstance(configured_contract, str) or not configured_contract.strip():
        legacy_problem_files = config.get("problem_statement_files")
        configured_contract = (
            legacy_problem_files[0]
            if isinstance(legacy_problem_files, list)
            and legacy_problem_files
            and isinstance(legacy_problem_files[0], str)
            and legacy_problem_files[0].lower().endswith((".md", ".json"))
            else "problem/problem_contract.md"
        )
    contract_relative = configured_contract
    contract_path = (case_dir / str(contract_relative)).resolve()
    try:
        contract_path.relative_to(case_dir.resolve())
    except ValueError as exc:
        raise ValueError("PROBLEM_CONTRACT_UNSAFE_PATH") from exc

    if not contract_path.is_file():
        intake = build_problem_contract_candidate(case_dir)
    else:
        intake = {
            "status": "CANDIDATE_ALREADY_PRESENT",
            "intake_status": "CANDIDATE_PENDING_GATE1",
            "case_id": config.get("case_id", case_dir.name),
            "official_problem_file": (
                (config.get("official_problem_statement_files") or [None])[0]
            ),
            "candidate_path": str(contract_relative).replace("\\", "/"),
        }

    # This function is the production boundary between intake and routing.
    # Gate 1 is therefore mandatory even when a contract candidate already
    # exists or an older case omitted the optional intake_required flag.
    gate = evaluate_human_gate(case_dir, "GATE1")
    if gate.get("gate_status") != "PASS":
        trace = {
            "status": "WAITING_FOR_GATE1",
            "stop_before_downstream": True,
            "intake_status": intake.get("intake_status"),
            "problem_contract_runtime": "INTAKE_CANDIDATE_GENERATED",
            "problem_contract_candidate_generated": intake.get("status")
            in {"CANDIDATE_GENERATED", "CANDIDATE_ALREADY_PRESENT"},
            "problem_contract_candidate_ref": intake.get("candidate_path"),
            "problem_contract_source": intake.get("official_problem_file"),
            "gate1_status": "WAITING_FOR_HUMAN_CONFIRMATION",
            "gate1_evaluation": gate,
            "router_runtime": "NOT_RUN",
            "knowledge_card_runtime": "NOT_RUN",
            "model_building_handoff_runtime": "NOT_RUN",
            "solver_runtime": "NOT_RUN",
            "human_decision_required": "YES",
        }
        write_json(run_dir / "pre_modeling_intake.json", trace)
        return trace, None

    contract = validate_problem_contract(case_dir)
    if contract.get("status") != "PASS":
        raise ValueError(
            "PROBLEM_CONTRACT_FAIL_CLOSED: "
            f"{contract.get('code')} at {contract.get('path')}"
        )
    router_result = build_output(case_dir)
    write_case_outputs(case_dir, router_result)
    route_stage = run_model_route_stage(case_dir, router_result)
    if route_stage.get("status") != "READY_FOR_MODEL_IMPLEMENTATION":
        waiting_status = route_stage.get("status")
        trace = {
            "status": waiting_status,
            "stop_before_downstream": True,
            "problem_contract_runtime": "AUTO_AND_CONSUMED",
            "problem_contract_ref": contract.get("path"),
            "problem_contract_sha256": contract.get("sha256"),
            "router_runtime": "AUTO_AND_CONSUMED",
            "router_output_consumed": True,
            "router_result_ref": "routing/problem_type_routing_result.json",
            "knowledge_card_runtime": "ROUTED_FOR_MODEL_ROUTE_EXECUTOR",
            "model_route_generation_runtime": waiting_status,
            "model_route_generation_handoff_ref": route_stage.get("handoff", {}).get("path"),
            "model_route_generation_handoff_sha256": route_stage.get("handoff", {}).get("sha256"),
            "model_building_handoff_runtime": "NOT_RUN_BEFORE_GATE2",
            "solver_runtime": "NOT_RUN",
            "human_decision_required": "YES",
            "route_stage": route_stage,
        }
        write_json(run_dir / "pre_modeling_production_wiring.json", trace)
        return trace, None
    prepare_model_building_handoff_inputs(
        case_dir,
        router_result,
        route_stage.get("outputs") or {},
        target_stage="MODEL_IMPLEMENTATION",
    )
    write_model_building_handoff(case_dir, router_result)
    handoff = validate_model_building_handoff(case_dir, router_result)
    trace = {
        "status": "PASS",
        "problem_contract_runtime": "AUTO_AND_CONSUMED",
        "problem_contract_ref": contract.get("path"),
        "problem_contract_sha256": contract.get("sha256"),
        "router_runtime": "AUTO_AND_CONSUMED",
        "router_output_consumed": True,
        "router_result_ref": handoff.get("router_result_ref"),
        "model_route_generation_runtime": "VALIDATED_AND_GATE2_PASSED",
        "model_route_generation_handoff_ref": route_stage.get("handoff", {}).get("path"),
        "knowledge_card_runtime": (
            "NO_RELEVANT_CARD_SUPPORTED"
            if handoff.get("knowledge_card_status") == "NO_RELEVANT_CARD"
            else "AUTO_AND_CONSUMED"
        ),
        "knowledge_card_l3_context_proven": bool(
            handoff.get("relevant_card_content")
            or handoff.get("knowledge_card_status") == "NO_RELEVANT_CARD"
        ),
        "knowledge_card_status": handoff.get("knowledge_card_status"),
        "relevant_card_ids": handoff.get("relevant_card_ids", []),
        "model_building_handoff_runtime": "AUTO_AND_CONSUMED",
        "model_building_handoff_consumed": True,
        "model_building_handoff_ref": handoff.get("handoff_path"),
        "model_building_handoff_sha256": handoff.get("handoff_sha256"),
        "human_modeling_gate_preserved": True,
        "human_decision_required": handoff.get("human_decision_required"),
    }
    write_json(run_dir / "pre_modeling_production_wiring.json", trace)
    return trace, handoff


def _verify_competition_solver_authorization(
    case_dir: Path,
    *,
    competition_mode: bool,
    authorization_path: Path | None,
    current_stage: str | None,
    requested_stage: str | None,
) -> dict[str, Any]:
    """Re-verify the existing authorization immediately before solver dispatch."""
    if not competition_mode:
        return {
            "status": "NOT_REQUIRED",
            "reason": "NON_COMPETITION_MODE",
            "authorization_verified": False,
        }
    if authorization_path is None or not current_stage or not requested_stage:
        return {
            "status": "FAIL",
            "reason": "COMPETITION_SOLVER_AUTHORIZATION_REQUIRED",
            "authorization_verified": False,
        }
    verification = verify_authorized_stage_transition_for_case(
        authorization_path,
        case_path=case_dir,
        current_stage=current_stage,
        requested_stage=requested_stage,
    )
    verification = dict(verification)
    verification["authorization_verified"] = verification.get("status") == "PASS"
    return verification


def _run_model_aware_validation(run_dir: Path, risks: list[dict[str, Any]]) -> dict[str, Any]:
    from validation.model_aware_validator import validate_result_summary_file as validate_model_summary

    report = validate_model_summary(run_dir / "output" / "result_summary.json", output_dir=run_dir)
    for item in report.get("hard_assertions", []):
        if item.get("status") == "FAIL":
            add_risk(
                risks,
                severity="ERROR",
                category=f"model_validation.{item.get('name')}",
                message=str(item.get("message") or "model-aware validation hard assertion failed"),
                fatal=True,
                path=item.get("path"),
                recommendation="Keep artifacts in work_tmp and do not promote.",
            )
    for item in report.get("soft_warnings", []):
        add_risk(
            risks,
            severity="WARNING",
            category=f"model_validation.{item.get('name')}",
            message=str(item.get("message") or "model-aware validation warning"),
            fatal=False,
            path=item.get("path"),
        )
    return report


def _run_f2_1_validation_patch(run_dir: Path, case_dir: Path) -> dict[str, Any]:
    from validation.paper_number_extractor import write_paper_number_candidates
    from validation.unified_validation_report import write_unified_validation_report

    write_paper_number_candidates(case_dir, run_dir)
    return write_unified_validation_report(run_dir, run_dir / "output" / "result_summary.json")


def _write_f2_1_evidence(
    *,
    run_dir: Path,
    case_dir: Path,
    run_id: str,
    generated_files: list[str],
    success: bool,
) -> dict[str, Any]:
    from validation.evidence_writer import update_global_evidence_map, write_run_evidence_map

    run_payload = write_run_evidence_map(
        run_dir=run_dir,
        case_dir=case_dir,
        run_id=run_id,
        extra_artifacts=generated_files,
    )
    if success:
        update_global_evidence_map(run_payload=run_payload)
    return run_payload


def _run_core_quality_gates(run_dir: Path, case_dir: Path, run_id: str, risks: list[dict[str, Any]]) -> dict[str, Any]:
    from quality_gates import run_quality_gates

    report = run_quality_gates(case_dir=case_dir, run_dir=run_dir, run_id=run_id)
    if not report.get("promotion_allowed", True):
        add_risk(
            risks,
            severity="ERROR",
            category="quality_gates",
            message=f"core quality gates blocked promotion: hard_block_count={report.get('hard_block_count')}",
            fatal=True,
            path=rel_path(run_dir / "output" / "quality" / "qg_report.json"),
            recommendation="Repair HARD_BLOCK quality gate failures before promotion.",
        )
    for item in report.get("results", []):
        if item.get("severity") == "SOFT_BLOCK" and item.get("status") == "FAIL":
            add_risk(
                risks,
                severity="WARNING",
                category=f"quality_gates.{item.get('rule_id')}",
                message=str(item.get("message") or "quality gate soft block"),
                fatal=False,
                path=rel_path(run_dir / "output" / "quality" / "qg_report.json"),
            )
        elif item.get("status") in {"WARNING", "MANUAL_REVIEW_REQUIRED"}:
            add_risk(
                risks,
                severity="WARNING",
                category=f"quality_gates.{item.get('rule_id')}",
                message=str(item.get("message") or "quality gate warning"),
                fatal=False,
                path=rel_path(run_dir / "output" / "quality" / "manual_review_todo.json"),
            )
    return report


def _optional_evidence_requested(case_config: dict[str, Any]) -> bool:
    sensitivity_hook = case_config.get("sensitivity_hook")
    tournament_hook = case_config.get("tournament_hook")
    return any(
        (
            case_config.get("sensitivity_required") is True,
            case_config.get("tournament_required") is True,
            isinstance(sensitivity_hook, dict) and sensitivity_hook.get("enabled") is True,
            isinstance(tournament_hook, dict) and tournament_hook.get("enabled") is True,
        )
    )


def _run_optional_evidence_hooks_if_configured(
    *,
    case_dir: Path,
    run_dir: Path,
    case_config: dict[str, Any],
    risks: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not _optional_evidence_requested(case_config):
        return None

    from pipeline_core.python_tools.optional_evidence_hooks import (
        check_optional_evidence_hooks,
        write_optional_evidence_hooks_report,
    )

    report = check_optional_evidence_hooks(case_dir, output_root=run_dir)
    write_optional_evidence_hooks_report(report, run_dir)
    status = str(report.get("status") or "")
    report_path = rel_path(run_dir / "output" / "validation" / "optional_evidence_hooks_report.json")
    detail_codes = list(report.get("blockers") or []) + list(report.get("warnings") or [])
    detail = ", ".join(str(item) for item in detail_codes) or status or "unknown status"

    if status == "BLOCKED":
        add_risk(
            risks,
            severity="ERROR",
            category="optional_evidence_hooks",
            message=f"optional evidence hooks blocked workflow: {detail}",
            fatal=True,
            path=report_path,
            recommendation="Repair required optional-evidence configuration or execution before promotion.",
        )
    elif status == "MANUAL_REVIEW_REQUIRED":
        tier = str(report.get("submission_tier") or case_config.get("submission_tier") or "training").lower()
        fatal = tier in {"selection", "formal"}
        add_risk(
            risks,
            severity="ERROR" if fatal else "WARNING",
            category="optional_evidence_hooks",
            message=f"optional evidence hooks require manual review for tier={tier}: {detail}",
            fatal=fatal,
            path=report_path,
            recommendation="Complete manual review before promotion." if fatal else "Review optional evidence before paper use.",
        )
    elif status not in {"PASS", "SKIPPED"}:
        add_risk(
            risks,
            severity="ERROR",
            category="optional_evidence_hooks",
            message=f"optional evidence hooks returned unsupported status: {detail}",
            fatal=True,
            path=report_path,
        )
    return report


def _result_consistency_requested(case_config: dict[str, Any]) -> bool:
    return (
        case_config.get("result_consistency_enabled") is True
        or case_config.get("result_consistency_required") is True
    )


def _run_result_consistency_if_configured(
    *,
    case_dir: Path,
    run_dir: Path,
    case_config: dict[str, Any],
    risks: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not _result_consistency_requested(case_config):
        return None

    from validation.result_consistency_contract import (
        check_result_consistency,
        write_result_consistency_report,
    )

    configured_manifest = case_config.get(
        "result_consistency_manifest_path",
        "output/validation/result_consistency_manifest.json",
    )
    manifest_relative = Path(configured_manifest) if isinstance(configured_manifest, str) else Path("")
    if not configured_manifest or manifest_relative.is_absolute() or ".." in manifest_relative.parts:
        add_risk(
            risks,
            severity="ERROR",
            category="result_consistency",
            message="result consistency manifest path is missing or unsafe",
            fatal=True,
            recommendation="Use a non-empty case-relative result_consistency_manifest_path.",
        )
        return None

    report = check_result_consistency(
        case_dir,
        manifest_path=case_dir / manifest_relative,
        output_root=run_dir,
    )
    write_result_consistency_report(report, run_dir)
    status = str(report.get("status") or "")
    report_path = rel_path(run_dir / "output" / "validation" / "result_consistency_report.json")
    detail_codes = list(report.get("blockers") or []) + list(report.get("warnings") or [])
    detail = ", ".join(str(item) for item in detail_codes) or status or "unknown status"

    if status == "BLOCKED":
        add_risk(
            risks,
            severity="ERROR",
            category="result_consistency",
            message=f"result consistency blocked promotion: {detail}",
            fatal=True,
            path=report_path,
            recommendation="Repair the canonical result contract before promotion.",
        )
    elif status == "MANUAL_REVIEW_REQUIRED":
        add_risk(
            risks,
            severity="WARNING",
            category="result_consistency",
            message=f"result consistency requires manual review: {detail}",
            fatal=False,
            path=report_path,
            recommendation="Complete manual review before submitted-facing use.",
        )
    elif status != "PASS":
        add_risk(
            risks,
            severity="ERROR",
            category="result_consistency",
            message=f"result consistency returned unsupported status: {detail}",
            fatal=True,
            path=report_path,
        )
    return report


def _run_paper_preflight_if_enabled(
    *,
    case_dir: Path,
    run_dir: Path,
    risks: list[dict[str, Any]],
    effective_settings: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    config_path = case_dir / "paper" / "paper_preflight_config.json"
    config = read_json(config_path, default={}) or {}
    effective_enabled = (
        effective_settings.get("effective_paper_preflight_enabled") is True
        if effective_settings is not None
        else config.get("enabled") is True
    )
    if not effective_enabled or not config_path.is_file():
        return None

    from validation.paper_hard_error_preflight import (
        check_paper_hard_errors,
        write_paper_hard_error_report,
    )

    effective_config_path = config_path
    if effective_settings is not None:
        effective_config = dict(config) if isinstance(config, dict) else {}
        effective_config["enabled"] = True
        effective_config["submission_tier"] = effective_settings["submission_tier"]
        effective_config_path = run_dir / "effective_paper_config.json"
        write_json(effective_config_path, effective_config)
    report = check_paper_hard_errors(case_dir, config_path=effective_config_path)
    write_paper_hard_error_report(report, run_dir)
    status = str(report.get("status") or "")
    tier = str(
        (effective_settings or {}).get("submission_tier")
        or report.get("submission_tier")
        or config.get("submission_tier")
        or "training"
    ).lower()
    report_path = rel_path(run_dir / "output" / "validation" / "paper_hard_error_report.json")
    detail_codes = list(report.get("blockers") or []) + list(report.get("warnings") or [])
    detail = ", ".join(str(item) for item in detail_codes) or status or "unknown status"

    if status == "BLOCKED":
        add_risk(
            risks,
            severity="ERROR",
            category="paper_hard_error_preflight",
            message=f"paper hard-error preflight blocked promotion: {detail}",
            fatal=True,
            path=report_path,
            recommendation="Repair submitted-facing paper hard errors before promotion.",
        )
    elif status == "MANUAL_REVIEW_REQUIRED":
        fatal = tier in {"selection", "formal"}
        add_risk(
            risks,
            severity="ERROR" if fatal else "WARNING",
            category="paper_hard_error_preflight",
            message=f"paper hard-error preflight requires manual review for tier={tier}: {detail}",
            fatal=fatal,
            path=report_path,
            recommendation="Complete paper manual review before promotion." if fatal else "Review submitted-facing paper warnings.",
        )
    elif status not in {"PASS", "SKIPPED"}:
        add_risk(
            risks,
            severity="ERROR",
            category="paper_hard_error_preflight",
            message=f"paper hard-error preflight returned unsupported status: {detail}",
            fatal=True,
            path=report_path,
        )
    return report


def _xval_independent_requested(case_config: dict[str, Any]) -> bool:
    return (
        case_config.get("xval_independent_enabled") is True
        or case_config.get("xval_independent_required") is True
    )


def _run_xval_independent_if_configured(
    *,
    case_dir: Path,
    current_result_summary_path: Path,
    case_config: dict[str, Any],
    effective_settings: dict[str, Any],
    risks: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Consume the supplied XVAL artifact without executing either method."""

    if not _xval_independent_requested(case_config):
        return None

    from validation.xval_independent import validate_xval_independent_check

    required = effective_settings.get("effective_xval_independent_required") is True
    artifact_path = effective_settings.get("effective_xval_independent_artifact_path")
    report = validate_xval_independent_check(
        case_dir,
        artifact_path=artifact_path,
        submission_tier=str(effective_settings.get("submission_tier") or "training"),
        required=required,
    )
    _bind_xval_to_current_result_summary(
        case_dir=case_dir,
        current_result_summary_path=current_result_summary_path,
        report=report,
        required=required,
    )
    status = str(report.get("status") or "")
    detail_codes = list(report.get("blockers") or []) + list(report.get("warnings") or [])
    detail = ", ".join(str(item) for item in detail_codes) or status or "unknown status"
    report_path = str(report.get("artifact_path") or artifact_path or DEFAULT_XVAL_INDEPENDENT_ARTIFACT_PATH)

    if status == "PASS":
        return report

    fatal = required
    if status == "BLOCKED":
        message = f"XVAL independent check blocked promotion: {detail}"
    elif status == "MANUAL_REVIEW_REQUIRED":
        message = f"XVAL independent check requires manual review: {detail}"
    else:
        message = f"XVAL independent check returned unsupported status: {detail}"
        fatal = True if required else False
    add_risk(
        risks,
        severity="ERROR" if fatal else "WARNING",
        category="xval_independent",
        message=message,
        fatal=fatal,
        path=report_path,
        recommendation=(
            "Provide a valid independent-check artifact before promotion."
            if fatal
            else "Review or provide the optional independent-check artifact before submitted-facing use."
        ),
    )
    return report


def _bind_xval_to_current_result_summary(
    *,
    case_dir: Path,
    current_result_summary_path: Path,
    report: dict[str, Any],
    required: bool,
) -> None:
    """Reject an artifact whose primary hash does not bind this run's summary."""

    if report.get("status") != "PASS":
        return
    artifact_file = _case_relative_file(case_dir, report.get("artifact_path"))
    if artifact_file is None or not current_result_summary_path.is_file():
        return
    artifact = read_json(artifact_file[0], default={}) or {}
    if not isinstance(artifact, dict):
        return
    primary_path = artifact.get("primary_result_path")
    expected_sha256 = artifact.get("primary_result_sha256")
    current_sha256 = sha256_file(current_result_summary_path)
    current_path_is_declared = primary_path == "output/result_summary.json"
    if current_path_is_declared and isinstance(expected_sha256, str) and expected_sha256.lower() == current_sha256.lower():
        return

    code = "XVAL_PRIMARY_RESULT_STALE"
    report.setdefault("stale_bindings", []).append(
        {
            "label": "primary_result_current_run",
            "path": "output/result_summary.json",
            "expected_sha256": expected_sha256,
            "actual_sha256": current_sha256,
        }
    )
    if required:
        report.setdefault("blockers", []).append(code)
        report["status"] = "BLOCKED"
        report["route_to_blocking_status"] = True
    else:
        report.setdefault("warnings", []).append(code)
        report["status"] = "MANUAL_REVIEW_REQUIRED"


def _xval_machine_record(
    report: dict[str, Any] | None,
    effective_settings: dict[str, Any],
    risks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Keep XVAL state visible in run artifacts without creating an XVAL artifact."""

    required = effective_settings.get("effective_xval_independent_required") is True
    enabled = effective_settings.get("effective_xval_independent_enabled") is True or required
    record = {
        "xval_enabled": enabled,
        "xval_required": required,
        "xval_status": "SKIPPED" if report is None else report.get("status"),
        "xval_artifact_path": effective_settings.get(
            "effective_xval_independent_artifact_path", DEFAULT_XVAL_INDEPENDENT_ARTIFACT_PATH
        ),
        "blockers": [] if report is None else list(report.get("blockers") or []),
        "warnings": [] if report is None else list(report.get("warnings") or []),
        "checked_targets_count": 0 if report is None else int(report.get("checked_targets_count") or 0),
        "comparison_results_count": 0 if report is None else int(report.get("comparison_results_count") or 0),
        "independence_strength": None if report is None else report.get("independence_strength"),
        "stale_bindings": [] if report is None else list(report.get("stale_bindings") or []),
    }
    record["fatal_count_contribution"] = sum(
        1 for item in risks if item.get("category") == "xval_independent" and item.get("fatal")
    )
    record["validation_continue_allowed"] = record["fatal_count_contribution"] == 0
    record["safe_promotion_allowed"] = record["fatal_count_contribution"] == 0
    return record


def _case_relative_file(case_dir: Path, relative_path: object) -> tuple[Path, str] | None:
    """Return a safe, existing case-relative file and its normalized path."""

    if not isinstance(relative_path, str):
        return None
    resolved = resolve_case_relative_path(case_dir, relative_path)
    if resolved is None or not resolved.is_file():
        return None
    return resolved, resolved.relative_to(case_dir.resolve()).as_posix()


def _validated_content_bindings(
    *,
    case_dir: Path,
    case_config: dict[str, Any],
    effective_settings: dict[str, Any],
    xval_report: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Describe the case files consumed by a successful upstream validation run."""

    bindings: list[dict[str, Any]] = []

    def add_binding(logical_name: str, relative_path: object, required: bool, source_role: str) -> None:
        safe_file = _case_relative_file(case_dir, relative_path)
        if safe_file is None:
            return
        path, normalized = safe_file
        bindings.append(
            {
                "logical_name": logical_name,
                "relative_path": normalized,
                "sha256": sha256_file(path),
                "required_for_candidate_validation": required,
                "source_role": source_role,
            }
        )

    add_binding("result_summary", "output/result_summary.json", True, "result_summary")

    if _result_consistency_requested(case_config):
        add_binding(
            "result_consistency_manifest",
            case_config.get("result_consistency_manifest_path", DEFAULT_RESULT_CONSISTENCY_MANIFEST_PATH),
            True,
            "result_manifest",
        )

    if effective_settings.get("effective_paper_preflight_enabled") is True:
        config_path = "paper/paper_preflight_config.json"
        config_file = _case_relative_file(case_dir, config_path)
        if config_file is not None:
            add_binding("paper_preflight_config", config_path, True, "paper_config")
            config = read_json(config_file[0], default={}) or {}
            sources = config.get("submitted_text_sources", []) if isinstance(config, dict) else []
            if isinstance(sources, list):
                for index, source in enumerate(sources):
                    source_path = source.get("path") if isinstance(source, dict) else source
                    add_binding(f"paper_source_{index + 1}", source_path, True, "paper_source")

    if xval_report is not None:
        add_binding(
            "xval_independent_check",
            xval_report.get("artifact_path") or effective_settings.get("effective_xval_independent_artifact_path"),
            effective_settings.get("effective_xval_independent_required") is True,
            "xval_independent_check",
        )
    return bindings


def _attach_upstream_content_bindings(run_dir: Path, bindings: list[dict[str, Any]]) -> None:
    """Add content bindings to the existing machine-readable upstream risk artifact."""

    risk_path = run_dir / "risk_summary.json"
    risk_summary = read_json(risk_path, default={}) or {}
    if isinstance(risk_summary, dict):
        risk_summary["upstream_content_bindings"] = bindings
        write_json(risk_path, risk_summary)


def _attach_xval_machine_record(run_dir: Path, record: dict[str, Any]) -> None:
    """Expose consumed XVAL state in the existing risk summaries."""

    risk_path = run_dir / "risk_summary.json"
    risk_summary = read_json(risk_path, default={}) or {}
    if isinstance(risk_summary, dict):
        risk_summary["xval_independent"] = record
        write_json(risk_path, risk_summary)
    markdown_path = run_dir / "risk_summary.md"
    if markdown_path.is_file():
        summary = markdown_path.read_text(encoding="utf-8")
        summary += (
            "\n## XVAL Independent Check\n\n"
            f"- xval_enabled: `{str(record['xval_enabled']).lower()}`\n"
            f"- xval_required: `{str(record['xval_required']).lower()}`\n"
            f"- xval_status: `{record['xval_status']}`\n"
            f"- xval_artifact_path: `{record['xval_artifact_path']}`\n"
            f"- fatal_count_contribution: `{record['fatal_count_contribution']}`\n"
            f"- validation_continue_allowed: `{str(record['validation_continue_allowed']).lower()}`\n"
            f"- safe_promotion_allowed: `{str(record['safe_promotion_allowed']).lower()}`\n"
        )
        markdown_path.write_text(summary, encoding="utf-8")


def _write_validation_summary(
    run_dir: Path,
    case_dir: Path,
    risks: list[dict[str, Any]],
    result_summary: dict[str, Any] | None,
    pdf_check: dict[str, Any],
    *,
    strict_pdf: bool,
) -> dict[str, Any]:
    hard_checks = [
        {"name": "result_summary_json_exists", "status": "pass" if (run_dir / "output" / "result_summary.json").exists() else "fail"},
        {"name": "result_summary_txt_exists", "status": "pass" if (run_dir / "output" / "result_summary.txt").exists() else "fail"},
        {"name": "no_nan_inf_empty_required", "status": "pass"},
        {"name": "pdf_can_open", "status": "pass" if any(c["name"] == "pdf_can_open" and c["status"] == "pass" for c in pdf_check.get("checks", [])) else ("fail" if strict_pdf else "warning")},
        {"name": "necessary_figures_present", "status": "warning", "detail": "case-specific figure requirements require manual confirmation"},
    ]
    if result_summary is not None:
        generated_findings = validate_result_summary_file(run_dir / "output" / "result_summary.json")
        bad_numbers = [item for item in generated_findings if item["issue"] == "NaN_or_Inf"]
        required_missing = [item for item in generated_findings if item["issue"].startswith("required_")]
        if generated_findings:
            hard_checks[2]["status"] = "fail"
            hard_checks[2]["detail"] = {
                "bad_numbers": bad_numbers[:20],
                "required_missing": required_missing[:20],
                "all_findings": generated_findings[:20],
            }
    else:
        hard_checks[2]["status"] = "fail"
    for check in hard_checks:
        if check.get("status") == "fail":
            add_risk(
                risks,
                severity="ERROR",
                category="validation",
                message=f"hard validation failed: {check['name']}",
                fatal=True,
                recommendation="Keep artifacts in work_tmp and do not promote.",
            )

    soft_tips = [
        "numeric differences should be reviewed against previous promoted outputs",
        "constraint residuals, if generated by model code, should be reviewed manually",
        "suspected stale figures should be checked by timestamps and source scripts",
        "heuristic model fluctuation must not be written as global optimality",
        "paper hard-coded numbers should be manually reconciled with outputs",
    ]
    manual_checklist = ["units", "formulas", "literature support", "model explanation", "conclusion boundaries"]
    payload = {
        "schema_version": "x5_f0_validation_summary_v1",
        "generated_at": utc_now(),
        "hard_checks": hard_checks,
        "soft_tips": soft_tips,
        "manual_checklist": manual_checklist,
        "forbidden_automation": [
            "PDF OCR numeric auto comparison",
            "fatal blocking on every LaTeX number mismatch",
            "automatic global optimality judgment",
        ],
    }
    validation_dir = run_dir / "output" / "validation"
    validation_dir.mkdir(parents=True, exist_ok=True)
    write_json(validation_dir / "validation_summary.json", payload)
    md = "# X5-F0 Validation Summary\n\n"
    md += "## Hard Checks\n\n"
    md += markdown_table(hard_checks, [("name", "Check"), ("status", "Status"), ("detail", "Detail")])
    md += "\n\n## Soft Tips\n\n" + "\n".join(f"- {item}" for item in soft_tips)
    md += "\n\n## Manual Checklist\n\n" + "\n".join(f"- {item}" for item in manual_checklist)
    md += "\n\n## Forbidden Automation\n\n" + "\n".join(f"- {item}" for item in payload["forbidden_automation"])
    (validation_dir / "validation_summary.md").write_text(md, encoding="utf-8")
    shutil.copy2(run_dir / "pdf_checklist.json", validation_dir / "pdf_checklist.json")
    shutil.copy2(run_dir / "pdf_checklist.md", validation_dir / "pdf_checklist.md")
    return payload


def _evaluate_release(root: Path, run_dir: Path, risks: list[dict[str, Any]]) -> dict[str, Any]:
    auth = read_json(root / "release_authorization.json", default={}) or {}
    captain_ok = bool((auth.get("captain_confirmation") or {}).get("approved"))
    member_approvals = auth.get("member_approvals") or []
    member_count = sum(1 for item in member_approvals if item.get("approved"))
    fatal_count = sum(1 for item in risks if item.get("fatal"))
    override_record = str(auth.get("fatal_risk_override_record") or "").strip()
    authorized = (captain_ok or member_count >= 2) and (fatal_count == 0 or bool(override_record))
    payload = {
        "schema_version": "x5_f0_release_check_v1",
        "generated_at": utc_now(),
        "authorized_for_release_with_risk": authorized,
        "captain_approved": captain_ok,
        "member_approval_count": member_count,
        "fatal_risk_count": fatal_count,
        "fatal_override_record_present": bool(override_record),
        "final_submit_created": False,
        "note": "This check never creates final_submit.",
    }
    write_json(run_dir / "release_authorization_check.json", payload)
    md = f"""# Release Authorization Check

- authorized_for_release_with_risk: `{str(authorized).lower()}`
- captain_approved: `{str(captain_ok).lower()}`
- member_approval_count: `{member_count}`
- fatal_risk_count: `{fatal_count}`
- fatal_override_record_present: `{str(bool(override_record)).lower()}`
- final_submit_created: `false`
"""
    (run_dir / "release_authorization_check.md").write_text(md, encoding="utf-8")
    if not authorized:
        add_risk(
            risks,
            severity="ERROR",
            category="release_authorization",
            message="release-with-risk is not authorized by captain confirmation or 2-of-3 rule",
            fatal=True,
            recommendation="Fill release_authorization.json manually. Do not create final_submit automatically.",
        )
    return payload


def _write_error_log(run_dir: Path, risks: list[dict[str, Any]], exc: Exception | None = None) -> None:
    lines = [f"run_dir: {rel_path(run_dir)}", f"generated_at: {utc_now()}"]
    if exc:
        lines.append(f"exception: {type(exc).__name__}: {exc}")
    fatal = [item for item in risks if item.get("fatal")]
    if fatal:
        lines.append("[fatal risks]")
        lines.extend(f"- {item.get('category')}: {item.get('message')}" for item in fatal)
    else:
        lines.append("no fatal runtime exception recorded")
    (run_dir / "error.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _isolated_smoke_result(
    *,
    run_dir: Path | None,
    stage: str,
    risks: list[dict[str, Any]],
    existing_summary_reused: bool,
    effective_settings: dict[str, Any] | None = None,
    exception: str | None = None,
    source_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fatal_count = sum(1 for risk in risks if risk.get("fatal"))
    result = {
        "run_id": run_dir.name if run_dir is not None else None,
        "stage": stage,
        "status": "fatal" if fatal_count else "ok",
        "risk_status": "BLOCK" if fatal_count else "PASS",
        "run_dir": rel_path(run_dir) if run_dir is not None else None,
        "fatal_count": fatal_count,
        "isolated_smoke": True,
        "existing_summary_reused": existing_summary_reused,
        "global_writes_blocked": True,
        "promotion_disabled": True,
        "candidate_package_created": False,
        "final_submit_created": False,
    }
    result.update(effective_settings or {})
    if source_provenance is not None:
        result["source_provenance"] = source_provenance
    if exception:
        result["exception"] = exception
    return result


def _run_isolated_smoke(
    stage: str,
    case_dir: Path,
    run_dir: Path | None,
    *,
    root: Path,
    provenance_before: dict[str, Any],
) -> dict[str, Any]:
    """Reuse a trusted summary while containing every write inside ``run_dir``."""

    risks: list[dict[str, Any]] = []
    if run_dir is None:
        add_risk(
            risks,
            severity="ERROR",
            category="isolated_smoke",
            message="isolated smoke requires an explicit run_dir",
            fatal=True,
        )
        return _isolated_smoke_result(
            run_dir=None,
            stage=stage,
            risks=risks,
            existing_summary_reused=False,
            source_provenance=_runtime_source_provenance(provenance_before, _git_provenance_snapshot(root)),
        )

    run_dir = run_dir.resolve()
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        add_risk(
            risks,
            severity="ERROR",
            category="isolated_smoke",
            message=f"isolated run_dir cannot be created: {exc}",
            fatal=True,
            path=rel_path(run_dir),
        )
        return _isolated_smoke_result(
            run_dir=run_dir,
            stage=stage,
            risks=risks,
            existing_summary_reused=False,
            exception=str(exc),
            source_provenance=_runtime_source_provenance(provenance_before, _git_provenance_snapshot(root)),
        )

    existing_summary_reused = False
    effective_settings: dict[str, Any] = {}
    xval_report: dict[str, Any] | None = None
    try:
        case_config = load_case_config(case_dir)
        workflow_state = validate_workflow_state(case_dir, case_config)
        if workflow_state.get("status") == "FAIL":
            add_risk(
                risks,
                severity="ERROR",
                category="workflow_state",
                message=str(workflow_state.get("reason") or "WORKFLOW_STATE_INVALID"),
                fatal=True,
                path=rel_path(case_dir / "CURRENT_HANDOFF.md"),
                recommendation="Synchronize CURRENT_HANDOFF.md with the authoritative case_config.json state before continuing.",
            )
            raise RuntimeError(str(workflow_state.get("reason") or "WORKFLOW_STATE_INVALID"))
        case_config, effective_settings = _effective_run_gate_settings(case_dir, case_config, risks)
        if stage not in {"run", "validate"}:
            add_risk(
                risks,
                severity="ERROR",
                category="isolated_smoke",
                message="isolated smoke supports only run or validate stage; candidate and release stages are disabled",
                fatal=True,
            )
        else:
            summary_path = case_dir / "output" / "result_summary.json"
            if not summary_path.is_file():
                add_risk(
                    risks,
                    severity="ERROR",
                    category="isolated_smoke",
                    message="existing output/result_summary.json is missing",
                    fatal=True,
                    path=rel_path(summary_path),
                )
            else:
                try:
                    summary_text = summary_path.read_text(encoding="utf-8-sig")
                    result_summary = json.loads(summary_text)
                    if not isinstance(result_summary, dict):
                        raise ValueError("result_summary.json root must be a JSON object")
                except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                    add_risk(
                        risks,
                        severity="ERROR",
                        category="isolated_smoke",
                        message=f"existing output/result_summary.json is malformed: {exc}",
                        fatal=True,
                        path=rel_path(summary_path),
                    )
                else:
                    staged_summary = run_dir / "output" / "result_summary.json"
                    staged_summary.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(summary_path, staged_summary)
                    existing_summary_reused = True
                    summary_findings = validate_result_summary_file(staged_summary)
                    legacy_metadata_findings = [
                        finding
                        for finding in summary_findings
                        if finding.get("issue") == "required_field_missing"
                        and finding.get("json_path") in ISOLATED_LEGACY_METADATA_PATHS
                    ]
                    for finding in summary_findings:
                        if finding in legacy_metadata_findings:
                            continue
                        if finding["issue"] == "math_core_fatal_count_positive":
                            continue
                        add_risk(
                            risks,
                            severity="ERROR",
                            category="result_guard",
                            message=f"bad existing result_summary: {finding['issue']} at {finding.get('json_path')}",
                            fatal=True,
                            path=finding.get("file_path"),
                        )

                    _run_optional_evidence_hooks_if_configured(
                        case_dir=case_dir,
                        run_dir=run_dir,
                        case_config=case_config,
                        risks=risks,
                    )
                    consistency_report = _run_result_consistency_if_configured(
                        case_dir=case_dir,
                        run_dir=run_dir,
                        case_config=case_config,
                        risks=risks,
                    )
                    if legacy_metadata_findings:
                        compatibility_allowed = (
                            case_config.get("result_consistency_required") is True
                            and isinstance(consistency_report, dict)
                            and consistency_report.get("status") == "PASS"
                        )
                        if compatibility_allowed:
                            missing_paths = sorted(
                                str(finding.get("json_path")) for finding in legacy_metadata_findings
                            )
                            add_risk(
                                risks,
                                severity="WARNING",
                                category="legacy_result_summary_compatibility",
                                message=(
                                    "LEGACY_RESULT_SUMMARY_METADATA_COMPATIBLE_IN_ISOLATED_SMOKE: "
                                    f"result consistency passed; missing metadata fields: {', '.join(missing_paths)}"
                                ),
                                fatal=False,
                                path=rel_path(staged_summary),
                                recommendation="Keep this allowance isolated; do not weaken normal result-summary validation.",
                            )
                        else:
                            for finding in legacy_metadata_findings:
                                add_risk(
                                    risks,
                                    severity="ERROR",
                                    category="result_guard",
                                    message=(
                                        "legacy metadata compatibility requires result_consistency_required=true "
                                        f"and PASS: {finding['issue']} at {finding.get('json_path')}"
                                    ),
                                    fatal=True,
                                    path=finding.get("file_path"),
                                )
                    _run_paper_preflight_if_enabled(
                        case_dir=case_dir,
                        run_dir=run_dir,
                        risks=risks,
                        effective_settings=effective_settings,
                    )
                    xval_report = _run_xval_independent_if_configured(
                        case_dir=case_dir,
                        current_result_summary_path=staged_summary,
                        case_config=case_config,
                        effective_settings=effective_settings,
                        risks=risks,
                    )

        bindings = _validated_content_bindings(
            case_dir=case_dir,
            case_config=case_config,
            effective_settings=effective_settings,
            xval_report=xval_report,
        )
        xval_record = _xval_machine_record(xval_report, effective_settings, risks)

        isolated_required_tokens = set(ISOLATED_ALWAYS_REQUIRED_TOKENS)
        if existing_summary_reused:
            isolated_required_tokens.add(ISOLATED_TOKEN_STAGED_SUMMARY_COPY)
        registry = WriteRegistry(root=root, required_tokens=frozenset(isolated_required_tokens))

        _write_error_log(run_dir, risks)
        registry.record_write(run_dir / "error.log", token=ISOLATED_TOKEN_ERROR_LOG)
        if existing_summary_reused:
            registry.record_write(staged_summary, token=ISOLATED_TOKEN_STAGED_SUMMARY_COPY)

        source_provenance = _runtime_source_provenance(provenance_before, _git_provenance_snapshot(root))
        expected_post_snapshot_paths = [
            rel_path(run_dir / "risk_summary.json"),
            rel_path(run_dir / "risk_summary.md"),
            rel_path(run_dir / "run_result.json"),
        ]
        _attach_write_registry_provenance(
            source_provenance, registry, expected_post_snapshot_paths=expected_post_snapshot_paths
        )
        write_risk_summary(run_dir, risks, source_provenance=source_provenance)

        _attach_upstream_content_bindings(run_dir, bindings)
        _attach_xval_machine_record(run_dir, xval_record)
        if (run_dir / "output" / "validation").exists():
            shutil.copy2(run_dir / "risk_summary.md", run_dir / "output" / "validation" / "risk_summary.md")
            shutil.copy2(run_dir / "risk_summary.json", run_dir / "output" / "validation" / "risk_summary.json")
        result = _isolated_smoke_result(
            run_dir=run_dir,
            stage=stage,
            risks=risks,
            existing_summary_reused=existing_summary_reused,
            effective_settings=effective_settings,
            source_provenance=source_provenance,
        )
        result["upstream_content_bindings"] = bindings
        result.update(xval_record)
        result["xval_independent"] = xval_record
        write_json(run_dir / "run_result.json", result)
        return result
    except Exception as exc:
        add_risk(
            risks,
            severity="ERROR",
            category="isolated_smoke",
            message=f"isolated smoke failed cleanly: {exc}",
            fatal=True,
        )
        exception_registry = WriteRegistry(root=root, required_tokens=ISOLATED_ALWAYS_REQUIRED_TOKENS)
        try:
            _write_error_log(run_dir, risks, exc)
            exception_registry.record_write(run_dir / "error.log", token=ISOLATED_TOKEN_ERROR_LOG)
        except OSError:
            pass  # registry stays incomplete; diagnostic only, no promotion impact
        source_provenance = _runtime_source_provenance(provenance_before, _git_provenance_snapshot(root))
        expected_post_snapshot_paths = [
            rel_path(run_dir / "risk_summary.json"),
            rel_path(run_dir / "risk_summary.md"),
            rel_path(run_dir / "run_result.json"),
        ]
        # error.log is registered above, strictly before this snapshot is
        # taken, so it can be a genuine observed required token.
        # risk_summary.json/.md and run_result.json are written after this
        # point and share this exact source_provenance dict reference, so
        # they can never observe themselves — they are reported as expected
        # post-snapshot paths instead.
        _attach_write_registry_provenance(
            source_provenance, exception_registry, expected_post_snapshot_paths=expected_post_snapshot_paths
        )
        result = _isolated_smoke_result(
            run_dir=run_dir,
            stage=stage,
            risks=risks,
            existing_summary_reused=existing_summary_reused,
            effective_settings=effective_settings,
            exception=str(exc),
            source_provenance=source_provenance,
        )
        try:
            write_risk_summary(run_dir, risks, source_provenance=source_provenance)
            write_json(run_dir / "run_result.json", result)
        except OSError:
            pass
        return result


def _find_xelatex() -> Path | None:
    candidates: list[Path] = []
    resolved = shutil.which("xelatex")
    if resolved:
        candidates.append(Path(resolved))
    candidates.extend(
        [
            Path("D:/Tex/texlive/2025/bin/windows/xelatex.exe"),
            Path("C:/texlive/2025/bin/windows/xelatex.exe"),
            Path(os.environ.get("LOCALAPPDATA", "C:/__missing_localappdata__"))
            / "Programs"
            / "MiKTeX"
            / "miktex"
            / "bin"
            / "x64"
            / "xelatex.exe",
        ]
    )
    return next((path for path in candidates if path.is_file()), None)


def _run_formal_paper_build(case_dir: Path, timeout_seconds: int) -> dict[str, Any]:
    """Run the canonical case-local figure -> Track C -> renderer -> XeLaTeX chain."""

    from pipeline_core.paper_quality.matlab_figure_pipeline import generate_declared_matlab_figures
    from pipeline_core.paper_quality.model_to_paper_contract import validate_writer_input
    from pipeline_core.paper_quality.pdf_layout_checks import (
        PDFLayoutCheckError,
        build_layout_report,
        validate_manual_visual_review,
    )
    from pipeline_core.paper_quality.track_c_paper_expression import run_track_c_pass
    from pipeline_core.paper_template.paper_renderer import render_paper

    paper_dir = case_dir / "paper"
    writer_manifest = paper_dir / "writer_manifest.json"
    if not writer_manifest.is_file():
        raise RuntimeError("FORMAL_WRITER_MANIFEST_MISSING")
    case_config = load_case_config(case_dir)
    case_id = str(case_config.get("case_id") or case_dir.name)
    contract = validate_writer_input(case_dir, input_paths=[writer_manifest.relative_to(case_dir)])
    if contract.get("status") != "PASS":
        raise RuntimeError("MODEL_TO_PAPER_CONTRACT_BLOCKED")

    figure_generation = generate_declared_matlab_figures(
        writer_manifest,
        case_dir,
        timeout_seconds=timeout_seconds,
        derived_manifest=paper_dir / "writer_manifest_with_figures.json",
    )
    track_input_manifest = Path(figure_generation["writer_manifest"])

    track_output_manifest = paper_dir / "track_c_output_manifest.json"
    track_receipt = paper_dir / "track_c_receipt.json"
    track_result = run_track_c_pass(
        track_input_manifest,
        track_output_manifest,
        paper_dir / "track_c_output",
        track_receipt,
        case_id=case_id,
        case_root=case_dir,
        writer_schema_version="WRITER_SCHEMA_V2",
    )
    render_record = render_paper(
        track_output_manifest,
        paper_dir,
        formal=True,
        receipt_path=track_receipt,
        expected_case_id=case_id,
    )

    xelatex = _find_xelatex()
    if xelatex is None:
        raise RuntimeError("XELATEX_NOT_AVAILABLE")
    env = os.environ.copy()
    fontconfig = xelatex.parent.parent.parent / "tlpkg" / "tlpostcode" / "xetex" / "conf" / "fonts.conf"
    if fontconfig.is_file():
        env["FONTCONFIG_FILE"] = str(fontconfig)
    compile_records: list[dict[str, Any]] = []
    for index in (1, 2):
        log_path = paper_dir / f"xelatex_pass{index}.log"
        command = [str(xelatex), "-interaction=nonstopmode", "-halt-on-error", "main.tex"]
        try:
            with log_path.open("w", encoding="utf-8") as log_handle:
                completed = subprocess.run(
                    command,
                    cwd=paper_dir,
                    env=env,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    timeout=timeout_seconds,
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"XELATEX_TIMEOUT_PASS_{index}") from exc
        compile_records.append({"pass": index, "exit_code": completed.returncode, "log": rel_path(log_path)})
        if completed.returncode != 0:
            raise RuntimeError(f"XELATEX_BUILD_FAILED_PASS_{index}")
    canonical_tex = paper_dir / "main.tex"
    canonical_pdf = paper_dir / "main.pdf"
    if not canonical_tex.is_file() or not canonical_pdf.is_file():
        raise RuntimeError("CANONICAL_PAPER_OUTPUT_MISSING")
    try:
        pdf_layout_audit = build_layout_report(
            [paper_dir / "xelatex_pass1.log", paper_dir / "xelatex_pass2.log"],
            pdf_path=canonical_pdf,
        )
    except PDFLayoutCheckError as exc:
        raise RuntimeError(f"PDF_LAYOUT_AUDIT_BLOCKED: {exc}") from exc
    if pdf_layout_audit["status"] == "FAIL":
        raise RuntimeError("PDF_LAYOUT_AUDIT_BLOCKED")
    visual_review_path = paper_dir / "pdf_check" / "manual_visual_review.json"
    if visual_review_path.is_file():
        try:
            visual_review = validate_manual_visual_review(
                json.loads(visual_review_path.read_text(encoding="utf-8")), canonical_pdf
            )
        except (OSError, UnicodeError, json.JSONDecodeError, PDFLayoutCheckError) as exc:
            visual_review = {"status": "FAIL", "reason": f"PDF_VISUAL_REVIEW_INVALID:{exc}"}
    else:
        visual_review = {"status": "NOT_RUN", "reason": "PDF_VISUAL_REVIEW_REQUIRED_FOR_TRANSITION"}
    pdf_layout_audit["manual_visual_review"] = visual_review
    trace_path = paper_dir / "formal_paper_production_trace.json"
    trace = {
        "schema_version": "formal_paper_production_trace_v1",
        "status": "AUTOMATED_BUILD_PASS",
        "paper_render_transition_ready": visual_review.get("status") == "PASS",
        "case_id": case_id,
        "run_case_entry": "run_case.py",
        "model_to_paper_contract": contract,
        "figure_generation": figure_generation,
        "track_c_receipt": track_result,
        "renderer": render_record,
        "canonical_paper_tex": rel_path(canonical_tex),
        "canonical_paper_pdf": rel_path(canonical_pdf),
        "compile": compile_records,
        "pdf_layout_audit": pdf_layout_audit,
        "run_case_production_path_only": True,
        "model_changed": False,
        "result_changed": False,
        "claim_changed": False,
        "canonical_result_changed": False,
    }
    trace["tracked_paths"] = [
        str(path)
        for path in (
            track_output_manifest,
            track_receipt,
            paper_dir / "render_manifest.json",
            canonical_tex,
            canonical_pdf,
            paper_dir / "xelatex_pass1.log",
            paper_dir / "xelatex_pass2.log",
            trace_path,
        )
    ]
    if visual_review_path.is_file():
        trace["tracked_paths"].append(str(visual_review_path))
    figure_tracked_paths = [
        *figure_generation.get("figure_receipts", []),
        *figure_generation.get("generated_assets", []),
        *figure_generation.get("generated_scripts", []),
    ]
    if figure_generation.get("status") == "PASS":
        figure_tracked_paths.insert(0, figure_generation.get("writer_manifest"))
    for raw_path in figure_tracked_paths:
        if not raw_path:
            continue
        candidate = Path(str(raw_path))
        if not candidate.is_absolute():
            candidate = case_dir / candidate
        trace["tracked_paths"].append(str(candidate))
    write_json(trace_path, trace)
    return trace


def run_case(
    stage: str,
    case_dir: Path,
    timeout_seconds: int,
    single_file: str | None = None,
    *,
    isolated_smoke: bool = False,
    run_dir: Path | None = None,
    require_pre_modeling_wiring: bool = False,
    competition_mode: bool = False,
    competition_authorization_path: Path | None = None,
    competition_current_stage: str | None = None,
    competition_requested_stage: str | None = None,
) -> dict[str, Any]:
    root = ROOT
    provenance_before = _git_provenance_snapshot(root)
    if isolated_smoke:
        return _run_isolated_smoke(
            stage,
            case_dir,
            run_dir,
            root=root,
            provenance_before=provenance_before,
        )

    _ensure_static_templates(root)
    run_id = timestamp_id("run")
    run_dir = root / "work_tmp" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    risks: list[dict[str, Any]] = []
    generated_files: list[str] = []
    result_summary: dict[str, Any] | None = None
    math_core_summary_generated = False
    effective_settings: dict[str, Any] = {}
    xval_report: dict[str, Any] | None = None
    formal_paper_result: dict[str, Any] | None = None
    pre_modeling_result: dict[str, Any] | None = None
    modeling_handoff: dict[str, Any] | None = None
    competition_authorization_result: dict[str, Any] | None = None
    model_authority_result: dict[str, Any] | None = None
    model_governance_result: dict[str, Any] | None = None
    # (path, token) pairs confirmed written so far. The WriteRegistry itself
    # is constructed once, after every conditional branch below has already
    # run, because required_tokens must include only the conditional tokens
    # whose branch actually fired this run — that set isn't known until the
    # branches have executed. Each entry here is appended only once its
    # branch's own success signal (a non-None return value, or a call known
    # to write unconditionally once invoked without raising) has confirmed
    # the write happened; record_write() still independently re-verifies
    # each path at the point the registry is finally constructed.
    confirmed_writes: list[tuple[Path, str]] = []
    # Relative POSIX paths safe_promote's run_output_dir contained at the
    # moment it was called (empty unless that branch actually runs). Used
    # both by the post-call verification block and, at registry-construction
    # time, to decide whether SAFE_PROMOTE_DIFF_COMPLETE is required for
    # this run at all — a run that never had anything to promote must not
    # demand that token.
    safe_promote_source_paths: set[str] = set()
    # (asset_type, path, run_id, status) multiset this run expects to add to
    # manifests/assets_manifest.json — populated by mirroring the existing
    # register_asset call loop's own conditions (see the promotion branch
    # below). Empty unless that branch actually runs and has something to
    # register; used the same way as safe_promote_source_paths above to
    # decide whether ASSETS_MANIFEST_WRITE is required for this run at all.
    expected_asset_entries: Counter[tuple[str, str, str, str]] = Counter()
    try:
        case_config = load_case_config(case_dir)
        workflow_state = validate_workflow_state(case_dir, case_config)
        if workflow_state.get("status") == "FAIL":
            add_risk(
                risks,
                severity="ERROR",
                category="workflow_state",
                message=str(workflow_state.get("reason") or "WORKFLOW_STATE_INVALID"),
                fatal=True,
                path=rel_path(case_dir / "CURRENT_HANDOFF.md"),
                recommendation="Synchronize CURRENT_HANDOFF.md with the authoritative case_config.json state before continuing.",
            )
            raise RuntimeError(str(workflow_state.get("reason") or "WORKFLOW_STATE_INVALID"))
        case_config, effective_settings = _effective_run_gate_settings(case_dir, case_config, risks)
        if case_config.get("case_config_missing"):
            add_risk(
                risks,
                severity="WARNING",
                category="config",
                message="case_config.json is missing; fallback config was used",
                path=rel_path(case_dir / "case_config.json"),
            )
        write_json(
            run_dir / "run_config.json",
            {
                "run_id": run_id,
                "stage": stage,
                "case_dir": rel_path(case_dir),
                "timeout_seconds": timeout_seconds,
                "final_submit_created": False,
                **effective_settings,
            },
        )
        confirmed_writes.append((run_dir / "run_config.json", RUN_CASE_SUCCESS_TOKEN_RUN_CONFIG))

        # This is the last control-plane boundary before any model-building
        # or solver work.  Keep it independent of pre-modeling wiring so
        # direct Python callers, developer runners, and
        # require_pre_modeling_wiring=False cannot bypass Gate 2.
        formal_competition_execution = bool(
            competition_mode
            or effective_settings.get("submission_tier") == "formal"
        )
        if formal_competition_execution and stage in {"run", "validate"}:
            competition_authorization_result = _verify_competition_solver_authorization(
                case_dir,
                competition_mode=True,
                authorization_path=competition_authorization_path,
                current_stage=competition_current_stage,
                requested_stage=competition_requested_stage,
            )
            if competition_authorization_result.get("status") != "PASS":
                add_risk(
                    risks,
                    severity="ERROR",
                    category="competition_solver_authorization",
                    message=str(
                        competition_authorization_result.get("reason")
                        or "COMPETITION_SOLVER_AUTHORIZATION_FAILED"
                    ),
                    fatal=True,
                    recommendation="Provide a valid existing human_gate authorization artifact before solver execution.",
                )
                raise RuntimeError(
                    str(
                        competition_authorization_result.get("reason")
                        or "COMPETITION_SOLVER_AUTHORIZATION_FAILED"
                    )
                )

        # This guard is intentionally independent of competition_mode and of
        # pre-modeling wiring.  A case-local formal model must not be reached
        # through developer mode, a direct run_case() call, or
        # require_pre_modeling_wiring=False after its approved model changes.
        if stage in {"run", "validate"} and requires_model_authority_binding(case_dir):
            model_authority_result = verify_model_authority(
                case_dir,
                authorization_path=competition_authorization_path,
            )
            if model_authority_result.get("status") not in {"PASS", "NOT_REQUIRED"}:
                add_risk(
                    risks,
                    severity="ERROR",
                    category="model_authority",
                    message=str(
                        model_authority_result.get("reason")
                        or "MODEL_AUTHORIZATION_INVALIDATED"
                    ),
                    fatal=True,
                    path=model_authority_result.get("review_path"),
                    recommendation="Create a new human-approved MODEL_REVISION_REVIEW.md for the current model authority hash before solver execution.",
                )
                raise RuntimeError(
                    str(
                        model_authority_result.get("reason")
                        or "MODEL_AUTHORIZATION_INVALIDATED"
                    )
                )

        if stage in {"package", "paper-only", "emergency-export"}:
            result = package_candidate(
                case_dir=case_dir,
                run_id=run_id,
                emergency=stage == "emergency-export",
                paper_only=stage == "paper-only",
                single_file=single_file,
            )
            generated_files.append(result["package_dir"])
            inherited_fatal_count = int((result.get("manifest") or {}).get("inherited_fatal_count") or 0)
            if stage == "emergency-export" and inherited_fatal_count:
                add_risk(
                    risks,
                    severity="ERROR",
                    category="emergency_export",
                    message=f"emergency export inherited {inherited_fatal_count} fatal source risk(s)",
                    fatal=True,
                    path=result["package_dir"],
                    recommendation="Use the package only as an emergency candidate. Do not submit it as final_submit.",
                )
            add_risk(
                risks,
                severity="WARNING",
                category="candidate",
                message="candidate package generated; it is not final_submit",
                path=result["package_dir"],
            )
        else:
            # A newly initialized official case must stop at intake before
            # data-health, quality-gate, routing, or solver work.  If Gate 1
            # is already passed, the normal downstream branch may continue
            # and reuses the pre-modeling result captured here.
            if case_config.get("intake_required") is True:
                pre_modeling_result, modeling_handoff = _run_pre_modeling_production_wiring(
                    case_dir=case_dir,
                    run_dir=run_dir,
                )
                if pre_modeling_result.get("stop_before_downstream"):
                    waiting_for_gate1 = pre_modeling_result.get("status") == "WAITING_FOR_GATE1"
                    add_risk(
                        risks,
                        severity="ERROR",
                        category="pre_modeling_gate_waiting",
                        message=(
                            "Problem contract candidate generated; waiting for human Gate 1 confirmation."
                            if waiting_for_gate1
                            else "Model-route production stage is waiting for the executor outputs or human Gate 2."
                        ),
                        fatal=True,
                        path=rel_path(
                            case_dir
                            / (
                                "human_review/gate_1_problem_understanding.md"
                                if waiting_for_gate1
                                else "handoff/MODEL_ROUTE_GENERATION_HANDOFF.md"
                            )
                        ),
                        recommendation=(
                            "Complete and manually confirm Gate 1 before router, knowledge card, model-building handoff, or solver execution."
                            if waiting_for_gate1
                            else "Complete validated model-route artifacts, then obtain human Gate 2 approval before model-building handoff or solver execution."
                        ),
                    )
                    raise IntakeGateWaiting(
                        f"PRE_MODELING_WAIT: {pre_modeling_result.get('status')}"
                    )
            _run_optional_evidence_hooks_if_configured(
                case_dir=case_dir,
                run_dir=run_dir,
                case_config=case_config,
                risks=risks,
            )
            data_cmd = [
                python_executable(),
                str(root / "data_health_check.py"),
                "--case-dir",
                str(case_dir),
                "--run-dir",
                str(run_dir),
            ]
            subprocess_result = run_subprocess_with_timeout(
                data_cmd,
                timeout_seconds,
                root,
                run_dir / "subprocess_logs" / "data_health_check.log",
            )
            confirmed_writes.append(
                (
                    run_dir / "subprocess_logs" / "data_health_check.log",
                    RUN_CASE_SUCCESS_TOKEN_DATA_HEALTH_LOG,
                )
            )
            confirmed_writes.append(
                (run_dir / "data_health_report.json", RUN_CASE_SUCCESS_TOKEN_DATA_HEALTH_JSON)
            )
            confirmed_writes.append(
                (run_dir / "data_health_report.md", RUN_CASE_SUCCESS_TOKEN_DATA_HEALTH_MD)
            )
            if subprocess_result.get("timed_out"):
                add_risk(
                    risks,
                    severity="ERROR",
                    category="timeout",
                    message="data health check timed out",
                    fatal=True,
                    path=subprocess_result.get("log_path"),
                )
            data_health = read_json(run_dir / "data_health_report.json", default={}) or {}
            for risk in data_health.get("risks", []):
                risks.append(risk)

            existing_bad = scan_structured_result_files(case_dir / "output")
            for finding in existing_bad:
                add_risk(
                    risks,
                    severity="ERROR",
                    category="result_guard",
                    message=f"bad existing structured result detected: {finding.get('issue')}",
                    fatal=True,
                    path=finding.get("file_path") or finding.get("path"),
                )

            # Selection/formal cases use the existing production chain before
            # the solver. Training/test and isolated smoke behavior remain
            # unchanged unless the caller explicitly requests this wiring.
            should_run_pre_modeling = bool(
                require_pre_modeling_wiring
                or effective_settings.get("submission_tier") in TIER_DEFAULT_REQUIRED_TIERS
                or case_config.get("intake_required") is True
                or str(case_config.get("current_stage") or "").strip().upper()
                in {"INTAKE", "GATE_1_MANUAL_REVIEW", "GATE_1_MANUAL_CONFIRMATION"}
                or str(case_config.get("problem_contract_status") or "").strip().upper()
                == "READY_FOR_GATE_1_MANUAL_REVIEW"
            )
            if should_run_pre_modeling and pre_modeling_result is None and not has_fatal_risk(risks):
                try:
                    pre_modeling_result, modeling_handoff = _run_pre_modeling_production_wiring(
                        case_dir=case_dir,
                        run_dir=run_dir,
                    )
                    if pre_modeling_result.get("stop_before_downstream"):
                        waiting_for_gate1 = pre_modeling_result.get("status") == "WAITING_FOR_GATE1"
                        add_risk(
                            risks,
                            severity="ERROR",
                            category="pre_modeling_gate_waiting",
                            message=(
                                "Problem contract candidate generated; waiting for human Gate 1 confirmation."
                                if waiting_for_gate1
                                else "Model-route production stage is waiting for the executor outputs or human Gate 2."
                            ),
                            fatal=True,
                            path=rel_path(
                                case_dir
                                / (
                                    "human_review/gate_1_problem_understanding.md"
                                    if waiting_for_gate1
                                    else "handoff/MODEL_ROUTE_GENERATION_HANDOFF.md"
                                )
                            ),
                            recommendation=(
                                "Complete and manually confirm Gate 1 before router, knowledge card, model-building handoff, or solver execution."
                                if waiting_for_gate1
                                else "Complete validated model-route artifacts, then obtain human Gate 2 approval before model-building handoff or solver execution."
                            ),
                        )
                except Exception as exc:
                    add_risk(
                        risks,
                        severity="ERROR",
                        category="pre_modeling_production_wiring",
                        message=str(exc),
                        fatal=True,
                        path=rel_path(case_dir / "problem" / "problem_contract.md"),
                        recommendation="Repair the problem contract, router, selected card body, or MODEL_BUILDING_HANDOFF before solver execution.",
                    )

            if not has_fatal_risk(risks):
                model_config_path = case_dir / "model_config.json"
                model_config = read_json(model_config_path, default=None) if model_config_path.exists() else None
                if model_config_path.exists() and not isinstance(model_config, dict):
                    add_risk(
                        risks,
                        severity="ERROR",
                        category="math_core",
                        message="model_config.json exists but cannot be parsed as a JSON object",
                        fatal=True,
                        path=rel_path(model_config_path),
                    )
                elif isinstance(model_config, dict):
                    model_governance_result = evaluate_model_execution_governance(
                        case_dir,
                        case_config,
                        model_config,
                    )
                    if model_governance_result.get("status") != "PASS":
                        add_risk(
                            risks,
                            severity="ERROR",
                            category="competition_model_governance",
                            message=str(
                                model_governance_result.get("reason")
                                or "COMPETITION_MODEL_GOVERNANCE_BLOCKED"
                            ),
                            fatal=True,
                            path=rel_path(case_dir / "case_config.json"),
                            recommendation="Resolve the case-declared governance precondition before formal model execution.",
                        )
                        raise RuntimeError(
                            str(
                                model_governance_result.get("reason")
                                or "COMPETITION_MODEL_GOVERNANCE_BLOCKED"
                            )
                        )
                    result_summary = _write_math_core_summary(
                        run_dir,
                        case_dir,
                        model_config,
                        modeling_handoff=modeling_handoff,
                    )
                    math_core_summary_generated = True
                    for item in result_summary.get("risk_items", []):
                        add_risk(
                            risks,
                            severity="ERROR" if item.get("fatal") else item.get("severity", "INFO"),
                            category=f"math_core.{item.get('category') or 'risk'}",
                            message=str(item.get("message") or "math_core risk"),
                            fatal=bool(item.get("fatal")),
                            path=item.get("path"),
                            recommendation=item.get("recommendation"),
                        )
                else:
                    result_summary = _write_result_summary(run_dir, case_dir, case_config, data_health)
                for finding in validate_result_summary_file(run_dir / "output" / "result_summary.json"):
                    if finding["issue"] == "math_core_fatal_count_positive":
                        continue
                    add_risk(
                        risks,
                        severity="ERROR",
                        category="result_guard",
                        message=f"bad generated result_summary: {finding['issue']} at {finding.get('json_path')}",
                        fatal=True,
                        path=finding.get("file_path"),
                    )
                if math_core_summary_generated:
                    _run_model_aware_validation(run_dir, risks)
                if result_summary is not None:
                    confirmed_writes.append(
                        (
                            run_dir / "output" / "result_summary.json",
                            RUN_CASE_SUCCESS_TOKEN_RESULT_SUMMARY_JSON,
                        )
                    )
                    confirmed_writes.append(
                        (
                            run_dir / "output" / "result_summary.txt",
                            RUN_CASE_SUCCESS_TOKEN_RESULT_SUMMARY_TXT,
                        )
                    )
            else:
                add_risk(
                    risks,
                    severity="ERROR",
                    category="promotion",
                    message="fatal risk before result summary generation; promotion is disabled",
                    fatal=True,
                )

            result_consistency_report = _run_result_consistency_if_configured(
                case_dir=case_dir,
                run_dir=run_dir,
                case_config=case_config,
                risks=risks,
            )
            if result_consistency_report is not None:
                confirmed_writes.append(
                    (
                        run_dir / "output" / "validation" / "result_consistency_report.json",
                        RUN_CASE_SUCCESS_TOKEN_RESULT_CONSISTENCY_REPORT,
                    )
                )

            strict_pdf = stage == "release-with-risk"
            pdf_cmd = [
                python_executable(),
                str(root / "pdf_checklist.py"),
                "--case-dir",
                str(case_dir),
                "--run-dir",
                str(run_dir),
            ]
            if strict_pdf:
                pdf_cmd.append("--strict")
            pdf_subprocess = run_subprocess_with_timeout(
                pdf_cmd,
                timeout_seconds,
                root,
                run_dir / "subprocess_logs" / "pdf_checklist.log",
            )
            confirmed_writes.append(
                (run_dir / "subprocess_logs" / "pdf_checklist.log", RUN_CASE_SUCCESS_TOKEN_PDF_CHECKLIST_LOG)
            )
            confirmed_writes.append(
                (run_dir / "pdf_checklist.json", RUN_CASE_SUCCESS_TOKEN_PDF_CHECKLIST_JSON)
            )
            confirmed_writes.append(
                (run_dir / "pdf_checklist.md", RUN_CASE_SUCCESS_TOKEN_PDF_CHECKLIST_MD)
            )
            if pdf_subprocess.get("timed_out"):
                add_risk(
                    risks,
                    severity="ERROR",
                    category="timeout",
                    message="PDF checklist timed out",
                    fatal=True,
                    path=pdf_subprocess.get("log_path"),
                )
            pdf_check = read_json(run_dir / "pdf_checklist.json", default={}) or {"checks": []}
            if result_summary is not None:
                _write_validation_summary(run_dir, case_dir, risks, result_summary, pdf_check, strict_pdf=strict_pdf)
                confirmed_writes.append(
                    (
                        run_dir / "output" / "validation" / "validation_summary.json",
                        RUN_CASE_SUCCESS_TOKEN_VALIDATION_SUMMARY_JSON,
                    )
                )
                confirmed_writes.append(
                    (
                        run_dir / "output" / "validation" / "validation_summary.md",
                        RUN_CASE_SUCCESS_TOKEN_VALIDATION_SUMMARY_MD,
                    )
                )
                if math_core_summary_generated:
                    _run_f2_1_validation_patch(run_dir, case_dir)

            if (case_dir / "paper" / "writer_manifest.json").is_file():
                try:
                    formal_paper_result = _run_formal_paper_build(case_dir, timeout_seconds)
                    for raw_path in formal_paper_result.get("tracked_paths", []):
                        confirmed_writes.append((Path(raw_path), RUN_CASE_SUCCESS_TOKEN_FORMAL_PAPER_BUILD))
                    generated_files.extend(
                        [
                            formal_paper_result["canonical_paper_tex"],
                            formal_paper_result["canonical_paper_pdf"],
                        ]
                    )
                except Exception as exc:
                    formal_paper_result = {
                        "status": "BLOCKED",
                        "code": str(exc),
                        "run_case_production_path_only": True,
                    }
                    add_risk(
                        risks,
                        severity="ERROR",
                        category="formal_paper_production",
                        message=f"formal paper production blocked: {exc}",
                        fatal=True,
                        path=rel_path(case_dir / "paper" / "writer_manifest.json"),
                        recommendation="Repair the model-to-paper, Track C receipt, renderer, or XeLaTeX production chain.",
                    )

            paper_preflight_report = None
            if formal_paper_result is None or formal_paper_result.get("status") == "PASS":
                paper_preflight_report = _run_paper_preflight_if_enabled(
                    case_dir=case_dir,
                    run_dir=run_dir,
                    risks=risks,
                    effective_settings=effective_settings,
                )
            if paper_preflight_report is not None:
                confirmed_writes.append(
                    (
                        run_dir / "effective_paper_config.json",
                        RUN_CASE_SUCCESS_TOKEN_EFFECTIVE_PAPER_CONFIG,
                    )
                )
                confirmed_writes.append(
                    (
                        run_dir / "output" / "validation" / "paper_hard_error_report.json",
                        RUN_CASE_SUCCESS_TOKEN_PAPER_PREFLIGHT_REPORT,
                    )
                )

            xval_report = _run_xval_independent_if_configured(
                case_dir=case_dir,
                current_result_summary_path=run_dir / "output" / "result_summary.json",
                case_config=case_config,
                effective_settings=effective_settings,
                risks=risks,
            )

            _run_core_quality_gates(run_dir, case_dir, run_id, risks)
            confirmed_writes.append(
                (run_dir / "output" / "quality" / "qg_report.json", RUN_CASE_SUCCESS_TOKEN_QG_REPORT_JSON)
            )
            confirmed_writes.append(
                (
                    run_dir / "output" / "quality" / "manual_review_todo.json",
                    RUN_CASE_SUCCESS_TOKEN_QG_MANUAL_REVIEW_TODO,
                )
            )
            confirmed_writes.append(
                (run_dir / "output" / "quality" / "qg_report.md", RUN_CASE_SUCCESS_TOKEN_QG_REPORT_MD)
            )

            if stage == "release-with-risk":
                _evaluate_release(root, run_dir, risks)
                confirmed_writes.append(
                    (
                        run_dir / "release_authorization_check.json",
                        RUN_CASE_SUCCESS_TOKEN_RELEASE_CHECK_JSON,
                    )
                )
                confirmed_writes.append(
                    (
                        run_dir / "release_authorization_check.md",
                        RUN_CASE_SUCCESS_TOKEN_RELEASE_CHECK_MD,
                    )
                )

            if not has_fatal_risk(risks) and stage in {"run", "validate"}:
                safe_promote_run_output_dir = run_dir / "output"
                safe_promote_case_output_dir = case_dir / "output"
                safe_promote_backup_dir = run_dir / "backup_before_promotion"
                # Pre-call snapshot: exactly the regular files run_output_dir
                # contains right now, plus whatever case_output_dir already
                # has at those same relative paths (None if absent). This is
                # the only way to later distinguish created vs overwritten —
                # safe_promote's return value alone cannot (see
                # P6-PV1B2A4-D0). Only paths under run_output_dir are
                # enumerated; case_output_dir's own unrelated pre-existing
                # files are never touched or registered.
                safe_promote_source_sha256: dict[str, str] = {}
                safe_promote_target_before_sha256: dict[str, str | None] = {}
                if safe_promote_run_output_dir.exists():
                    for safe_promote_src in sorted(
                        p for p in safe_promote_run_output_dir.rglob("*") if p.is_file()
                    ):
                        safe_promote_rel = safe_promote_src.relative_to(safe_promote_run_output_dir).as_posix()
                        safe_promote_source_sha256[safe_promote_rel] = sha256_file(safe_promote_src)
                        safe_promote_target_before = safe_promote_case_output_dir / safe_promote_rel
                        safe_promote_target_before_sha256[safe_promote_rel] = (
                            sha256_file(safe_promote_target_before)
                            if safe_promote_target_before.is_file()
                            else None
                        )
                safe_promote_source_paths = set(safe_promote_source_sha256.keys())

                promoted = safe_promote(
                    safe_promote_run_output_dir, safe_promote_case_output_dir, safe_promote_backup_dir
                )

                # Post-call verification: the registry never trusts
                # safe_promote's return value alone. It must exactly match
                # the pre-call source set, and every promoted target (plus
                # every conditional backup) must independently verify by
                # existence, regular-file-ness, and sha256 against the
                # pre-call snapshot before SAFE_PROMOTE_DIFF_COMPLETE is
                # allowed to complete for any path.
                safe_promote_verification_errors: list[str] = []
                safe_promote_promoted_set = set(promoted)
                if safe_promote_promoted_set != safe_promote_source_paths:
                    safe_promote_missing = safe_promote_source_paths - safe_promote_promoted_set
                    safe_promote_unexpected = safe_promote_promoted_set - safe_promote_source_paths
                    if safe_promote_missing:
                        safe_promote_verification_errors.append(
                            f"safe_promote missing expected paths: {sorted(safe_promote_missing)}"
                        )
                    if safe_promote_unexpected:
                        safe_promote_verification_errors.append(
                            f"safe_promote returned unexpected paths: {sorted(safe_promote_unexpected)}"
                        )
                elif len(promoted) != len(safe_promote_promoted_set):
                    safe_promote_verification_errors.append("safe_promote returned duplicate relative paths")

                safe_promote_target_paths: list[Path] = []
                safe_promote_backup_paths: list[Path] = []
                if not safe_promote_verification_errors:
                    for safe_promote_rel in sorted(safe_promote_source_paths):
                        safe_promote_target = safe_promote_case_output_dir / safe_promote_rel
                        safe_promote_expected_sha256 = safe_promote_source_sha256[safe_promote_rel]
                        if not safe_promote_target.is_file():
                            safe_promote_verification_errors.append(
                                f"safe_promote target missing: {safe_promote_rel}"
                            )
                            continue
                        if sha256_file(safe_promote_target) != safe_promote_expected_sha256:
                            safe_promote_verification_errors.append(
                                f"safe_promote target content mismatch: {safe_promote_rel}"
                            )
                            continue
                        safe_promote_target_paths.append(safe_promote_target)

                        safe_promote_backup_path = safe_promote_backup_dir / safe_promote_rel
                        safe_promote_expected_backup_sha256 = safe_promote_target_before_sha256[safe_promote_rel]
                        if safe_promote_expected_backup_sha256 is not None:
                            if not safe_promote_backup_path.is_file():
                                safe_promote_verification_errors.append(
                                    f"safe_promote backup missing: {safe_promote_rel}"
                                )
                                continue
                            if sha256_file(safe_promote_backup_path) != safe_promote_expected_backup_sha256:
                                safe_promote_verification_errors.append(
                                    f"safe_promote backup content mismatch: {safe_promote_rel}"
                                )
                                continue
                            safe_promote_backup_paths.append(safe_promote_backup_path)
                        elif safe_promote_backup_path.exists():
                            safe_promote_verification_errors.append(
                                f"unexpected backup present for newly created target: {safe_promote_rel}"
                            )

                if not safe_promote_verification_errors:
                    for safe_promote_target_path in safe_promote_target_paths:
                        confirmed_writes.append(
                            (safe_promote_target_path, RUN_CASE_SUCCESS_TOKEN_SAFE_PROMOTE_DIFF_COMPLETE)
                        )
                    for safe_promote_backup_path in safe_promote_backup_paths:
                        confirmed_writes.append(
                            (safe_promote_backup_path, RUN_CASE_SUCCESS_TOKEN_SAFE_PROMOTE_DIFF_COMPLETE)
                        )

                generated_files.extend(rel_path(case_dir / "output" / item) for item in promoted)
                source_data = inventory_source_data(case_dir)

                # Expected (asset_type, path, run_id, status) multiset, built
                # by mirroring the register_asset call loop's own conditions
                # below — a read-only prediction, not a change to what gets
                # registered. `path` uses the same rel_path(path, root) form
                # register_asset itself uses for the manifest entry's `path`
                # field, so both sides are directly comparable.
                for rel_item in promoted:
                    expected_promoted_path = case_dir / "output" / rel_item
                    if expected_promoted_path.name.startswith("result_summary"):
                        expected_asset_entries[
                            ("result", rel_path(expected_promoted_path, root), run_id, "promoted")
                        ] += 1
                for expected_figure in (
                    sorted((case_dir / "output" / "figures").rglob("*"))
                    if (case_dir / "output" / "figures").exists()
                    else []
                ):
                    if expected_figure.is_file():
                        expected_asset_entries[
                            ("figure", rel_path(expected_figure, root), run_id, "existing_promoted")
                        ] += 1
                for expected_table in (
                    sorted((case_dir / "output" / "tables").rglob("*"))
                    if (case_dir / "output" / "tables").exists()
                    else []
                ):
                    if expected_table.is_file():
                        expected_asset_entries[
                            ("table", rel_path(expected_table, root), run_id, "existing_promoted")
                        ] += 1
                expected_paper_pdf = case_dir / "paper" / "main.pdf"
                if expected_paper_pdf.exists():
                    expected_asset_entries[
                        ("paper_pdf", rel_path(expected_paper_pdf, root), run_id, "existing_promoted")
                    ] += 1

                for rel_item in promoted:
                    promoted_path = case_dir / "output" / rel_item
                    if promoted_path.name.startswith("result_summary"):
                        register_asset(
                            root=root,
                            asset_type="result",
                            path=promoted_path,
                            source_script="run_case.py",
                            source_data=source_data,
                            run_id=run_id,
                            status="promoted",
                        )
                for figure in sorted((case_dir / "output" / "figures").rglob("*")) if (case_dir / "output" / "figures").exists() else []:
                    if figure.is_file():
                        register_asset(root=root, asset_type="figure", path=figure, source_script="run_case.py", source_data=source_data, run_id=run_id, status="existing_promoted")
                for table in sorted((case_dir / "output" / "tables").rglob("*")) if (case_dir / "output" / "tables").exists() else []:
                    if table.is_file():
                        register_asset(root=root, asset_type="table", path=table, source_script="run_case.py", source_data=source_data, run_id=run_id, status="existing_promoted")
                paper_pdf = case_dir / "paper" / "main.pdf"
                if paper_pdf.exists():
                    register_asset(root=root, asset_type="paper_pdf", path=paper_pdf, source_script="run_case.py", source_data=source_data, run_id=run_id, status="existing_promoted")

                # Post-call verification: never trust register_asset's return
                # value or call count alone. Read the manifest back, filter
                # to this run's own entries by run_id, and compare as an
                # exact multiset against what was expected above. Historical
                # entries from other runs are never touched or compared.
                assets_manifest_path = root / "manifests" / "assets_manifest.json"
                assets_manifest_payload = read_json(assets_manifest_path, default={}) or {}
                assets_manifest_list = (
                    assets_manifest_payload.get("assets", [])
                    if isinstance(assets_manifest_payload, dict)
                    else []
                )
                actual_current_run_asset_entries: Counter[tuple[str, str, str, str]] = Counter(
                    (item.get("type"), item.get("path"), item.get("run_id"), item.get("status"))
                    for item in assets_manifest_list
                    if isinstance(item, dict) and item.get("run_id") == run_id
                )
                if expected_asset_entries and actual_current_run_asset_entries == expected_asset_entries:
                    confirmed_writes.append(
                        (assets_manifest_path, RUN_CASE_SUCCESS_TOKEN_ASSETS_MANIFEST_WRITE)
                    )
            elif stage in {"run", "validate"}:
                add_risk(
                    risks,
                    severity="ERROR",
                    category="promotion",
                    message="promotion skipped because fatal risks exist",
                    fatal=True,
                )

        bindings = _validated_content_bindings(
            case_dir=case_dir,
            case_config=case_config,
            effective_settings=effective_settings,
            xval_report=xval_report,
        )
        xval_record = _xval_machine_record(xval_report, effective_settings, risks)
        copy_validation_summary = (run_dir / "output" / "validation").exists()
        promote_validation_summary = copy_validation_summary and not has_fatal_risk(risks) and stage in {"run", "validate"}
        promoted_validation = case_dir / "output" / "validation"
        if promote_validation_summary:
            generated_files.extend(
                [
                    rel_path(promoted_validation / "risk_summary.md"),
                    rel_path(promoted_validation / "risk_summary.json"),
                ]
            )
        queue = _update_compliance_queue(root, run_id, risks, generated_files)
        confirmed_writes.append(
            (root / "compliance" / "compliance_todo_queue.json", RUN_CASE_SUCCESS_TOKEN_COMPLIANCE_QUEUE)
        )
        write_json(run_dir / "compliance_todo_queue_snapshot.json", queue)
        confirmed_writes.append(
            (
                run_dir / "compliance_todo_queue_snapshot.json",
                RUN_CASE_SUCCESS_TOKEN_COMPLIANCE_QUEUE_SNAPSHOT,
            )
        )
        _write_error_log(run_dir, risks)
        confirmed_writes.append((run_dir / "error.log", RUN_CASE_SUCCESS_TOKEN_ERROR_LOG))
        # status is computed here (rather than at its original spot right
        # before `result = {...}`) purely so manifests/evidence_map.json can
        # be listed as an expected-post-snapshot path when it will actually
        # be written (update_global_evidence_map only runs when success is
        # true). This is a pure boolean expression with no side effects;
        # moving it earlier does not change its value or any branch below.
        status = "fatal" if has_fatal_risk(risks) else "ok"
        source_provenance = _runtime_source_provenance(provenance_before, _git_provenance_snapshot(root))
        expected_post_snapshot_paths = [
            rel_path(run_dir / "risk_summary.json"),
            rel_path(run_dir / "risk_summary.md"),
            rel_path(run_dir / "run_result.json"),
            rel_path(run_dir / "evidence_map.json"),
        ]
        if status == "ok":
            expected_post_snapshot_paths.append(rel_path(root / "manifests" / "evidence_map.json"))
        if copy_validation_summary:
            expected_post_snapshot_paths.extend(
                [
                    rel_path(run_dir / "output" / "validation" / "risk_summary.json"),
                    rel_path(run_dir / "output" / "validation" / "risk_summary.md"),
                ]
            )
        if promote_validation_summary:
            expected_post_snapshot_paths.extend(
                [
                    rel_path(promoted_validation / "risk_summary.json"),
                    rel_path(promoted_validation / "risk_summary.md"),
                ]
            )
        # The registry is constructed here, only now that every conditional
        # branch above has run — required_tokens is the always-required set
        # plus exactly the conditional tokens whose branch actually fired
        # this run (an inactive branch's token was never appended to
        # confirmed_writes, so it is never demanded here). Completeness
        # under this scope still covers only these deterministic direct
        # writes plus subprocess/quality-gate/safe_promote output — not
        # asset registration (manifests/assets_manifest.json).
        active_conditional_tokens = {
            token for _, token in confirmed_writes if token in RUN_CASE_SUCCESS_CONDITIONAL_TOKENS
        }
        # SAFE_PROMOTE_DIFF_COMPLETE is required whenever there was anything
        # to promote this run, regardless of whether verification actually
        # succeeded — this is what makes a verification failure show up as
        # registry incomplete (required but never completed) instead of
        # silently never being demanded at all. A run with nothing to
        # promote (safe_promote_source_paths empty) never requires it.
        if safe_promote_source_paths:
            active_conditional_tokens.add(RUN_CASE_SUCCESS_TOKEN_SAFE_PROMOTE_DIFF_COMPLETE)
        # Same rationale as SAFE_PROMOTE_DIFF_COMPLETE above: required
        # whenever this run expected to add any asset entries, regardless of
        # whether the post-call multiset comparison actually matched — a run
        # with nothing to register (expected_asset_entries empty) never
        # requires it.
        if expected_asset_entries:
            active_conditional_tokens.add(RUN_CASE_SUCCESS_TOKEN_ASSETS_MANIFEST_WRITE)
        registry = WriteRegistry(
            root=root, required_tokens=RUN_CASE_SUCCESS_REQUIRED_TOKENS | active_conditional_tokens
        )
        for confirmed_path, confirmed_token in confirmed_writes:
            registry.record_write(confirmed_path, token=confirmed_token)
        _attach_write_registry_provenance(
            source_provenance,
            registry,
            expected_post_snapshot_paths=expected_post_snapshot_paths,
            scope=RUN_CASE_SUCCESS_SCOPE,
            excluded_classes=RUN_CASE_SUCCESS_EXCLUDED_CLASSES,
        )
        write_risk_summary(run_dir, risks, source_provenance=source_provenance)
        _attach_upstream_content_bindings(run_dir, bindings)
        _attach_xval_machine_record(run_dir, xval_record)
        if copy_validation_summary:
            shutil.copy2(run_dir / "risk_summary.md", run_dir / "output" / "validation" / "risk_summary.md")
            shutil.copy2(run_dir / "risk_summary.json", run_dir / "output" / "validation" / "risk_summary.json")
            if promote_validation_summary:
                promoted_validation.mkdir(parents=True, exist_ok=True)
                shutil.copy2(run_dir / "risk_summary.md", promoted_validation / "risk_summary.md")
                shutil.copy2(run_dir / "risk_summary.json", promoted_validation / "risk_summary.json")
        result = {
            "run_id": run_id,
            "stage": stage,
            "status": status,
            "run_dir": rel_path(run_dir),
            "fatal_count": sum(1 for risk in risks if risk.get("fatal")),
            "final_submit_created": False,
            "formal_paper_production": formal_paper_result,
            "pre_modeling_production_wiring": pre_modeling_result,
            "competition_solver_authorization": competition_authorization_result,
            "model_authority": model_authority_result,
            "model_governance": model_governance_result,
            "upstream_content_bindings": bindings,
            "source_provenance": source_provenance,
            **effective_settings,
            **xval_record,
        }
        result["xval_independent"] = xval_record
        write_json(run_dir / "run_result.json", result)
        _write_f2_1_evidence(
            run_dir=run_dir,
            case_dir=case_dir,
            run_id=run_id,
            generated_files=generated_files,
            success=status == "ok",
        )
        return result
    except Exception as exc:
        if not isinstance(exc, IntakeGateWaiting):
            add_risk(
                risks,
                severity="ERROR",
                category="runner_exception",
                message=str(exc),
                fatal=True,
            )
        run_case_exception_registry = WriteRegistry(
            root=root, required_tokens=RUN_CASE_EXCEPTION_REQUIRED_TOKENS
        )
        _update_compliance_queue(root, run_id, risks, generated_files)
        run_case_exception_registry.record_write(
            root / "compliance" / "compliance_todo_queue.json",
            token=RUN_CASE_EXCEPTION_TOKEN_COMPLIANCE_QUEUE,
        )
        _write_error_log(run_dir, risks, exc)
        run_case_exception_registry.record_write(
            run_dir / "error.log", token=RUN_CASE_EXCEPTION_TOKEN_ERROR_LOG
        )
        source_provenance = _runtime_source_provenance(provenance_before, _git_provenance_snapshot(root))
        expected_post_snapshot_paths = [
            rel_path(run_dir / "risk_summary.json"),
            rel_path(run_dir / "risk_summary.md"),
            rel_path(run_dir / "run_result.json"),
        ]
        # compliance_todo_queue.json and error.log are registered above,
        # strictly before this snapshot is taken, so they are genuine
        # observed required tokens. risk_summary.json/.md and run_result.json
        # are written after this point and share this exact
        # source_provenance dict reference, so they can never observe
        # themselves — they are reported as expected post-snapshot paths.
        _attach_write_registry_provenance(
            source_provenance,
            run_case_exception_registry,
            expected_post_snapshot_paths=expected_post_snapshot_paths,
        )
        write_risk_summary(run_dir, risks, source_provenance=source_provenance)
        result = {
            "run_id": run_id,
            "stage": stage,
            "status": "fatal",
            "run_dir": rel_path(run_dir),
            "fatal_count": sum(1 for risk in risks if risk.get("fatal")),
            "final_submit_created": False,
            "exception": str(exc),
            "pre_modeling_production_wiring": pre_modeling_result,
            "competition_solver_authorization": competition_authorization_result,
            "model_authority": model_authority_result,
            "model_governance": model_governance_result,
            "source_provenance": source_provenance,
            **effective_settings,
        }
        write_json(run_dir / "run_result.json", result)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="X5-F0 one-click surgical runner")
    parser.add_argument("--stage", choices=STAGES, default="run")
    parser.add_argument(
        "--mode",
        choices=["developer", "test", "competition", "FULL_E2E_COMPETITION_SIMULATION"],
        default="developer",
        help="Competition mode performs fail-closed stage authorization only before downstream work.",
    )
    parser.add_argument("--current-stage", default=None)
    parser.add_argument("--requested-stage", default=None)
    parser.add_argument("--authorization-output", default=None)
    parser.add_argument("--authorization-input", default=None)
    parser.add_argument("--stage-action", choices=["TEST_MODEL_EXECUTION", "RUN_MODEL_SOLVER"], default=None)
    parser.add_argument("--case-dir", default=None, help="Advanced override. Defaults to cases/active_competition/current_case.")
    parser.add_argument("--timeout-seconds", type=int, default=60)
    parser.add_argument("--single-file", default=None, help="Advanced package-only update path relative to the case directory.")
    parser.add_argument(
        "--isolated-smoke",
        action="store_true",
        help="Reuse the existing result summary and contain all writes in --run-dir.",
    )
    parser.add_argument("--run-dir", default=None, help="Exclusive output directory required by --isolated-smoke.")
    parser.add_argument(
        "--provenance-safe",
        action="store_true",
        help="Explicit work_tmp-only provenance mode; requires --run-dir below the selected case's work_tmp/.",
    )
    args = parser.parse_args()
    case_dir = Path(args.case_dir) if args.case_dir else detect_current_case(ROOT)
    main_case_config = load_case_config(case_dir)
    main_workflow_state = validate_workflow_state(case_dir, main_case_config)
    authoritative_workflow = main_workflow_state.get("status") == "PASS"
    production_authorization_context: dict[str, Any] = {
        "path": Path(args.authorization_input) if args.authorization_input else None,
        "current_stage": args.current_stage,
        "requested_stage": args.requested_stage,
    }

    def persist_stage_authorization(payload: dict[str, Any]) -> Path:
        if payload.get("schema_version") == CASE_TRANSITION_AUTHORIZATION_SCHEMA:
            return write_transition_authorization(case_dir, payload)
        if not args.authorization_output:
            raise ValueError("AUTHORIZATION_OUTPUT_REQUIRED")
        path = Path(args.authorization_output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def production_runner(production_case_dir: Path, timeout_seconds: int) -> dict[str, Any]:
        return run_case(
            "run",
            production_case_dir,
            timeout_seconds,
            # Keep legacy/training solver cases independent.  run_case()
            # still enables the formal pre-modeling chain automatically for
            # selection/formal tiers and for cases that explicitly declare
            # intake/current-stage routing state.
            require_pre_modeling_wiring=False,
            competition_mode=True,
            competition_authorization_path=production_authorization_context.get("path"),
            competition_current_stage=production_authorization_context.get("current_stage"),
            competition_requested_stage=production_authorization_context.get("requested_stage"),
        )

    if args.mode.lower() in COMPETITION_MODES:
        if args.authorization_input and not args.stage_action:
            result = {
                "control_decision": "BLOCKED",
                "reason": "STAGE_ACTION_REQUIRED_FOR_AUTHORIZATION_INPUT",
                "stage_changed": False,
                "solver_executed": False,
                "model_artifact_created": False,
                "paper_created": False,
                "final_submit_created": False,
            }
        elif not args.current_stage or not args.requested_stage:
            result = {
                "control_decision": "BLOCKED",
                "reason": "CURRENT_OR_REQUESTED_STAGE_MISSING",
                "stage_changed": False,
                "solver_executed": False,
                "model_artifact_created": False,
                "paper_created": False,
                "final_submit_created": False,
            }
        elif args.authorization_input:
            result = dispatch_competition_stage_action(
                case_dir,
                action=args.stage_action,
                authorization_path=Path(args.authorization_input),
                current_stage=args.current_stage,
                requested_stage=args.requested_stage,
                timeout_seconds=args.timeout_seconds,
                production_runner=production_runner,
            )
        else:
            result = authorize_stage_transition(
                case_dir,
                current_stage=args.current_stage,
                requested_stage=args.requested_stage,
                mode=args.mode,
            )
        if result.get("control_decision") == "ALLOWED" and args.stage_action and not args.authorization_input:
            if not args.authorization_output and result.get("schema_version") != CASE_TRANSITION_AUTHORIZATION_SCHEMA:
                result["control_decision"] = "BLOCKED"
                result["reason"] = "AUTHORIZATION_OUTPUT_REQUIRED_FOR_STAGE_ACTION"
            else:
                authorization_path = persist_stage_authorization(result)
                result["authorization_artifact"] = str(authorization_path)
                production_authorization_context["path"] = authorization_path
                execution = dispatch_competition_stage_action(
                    case_dir,
                    action=args.stage_action,
                    authorization_path=authorization_path,
                    current_stage=args.current_stage,
                    requested_stage=args.requested_stage,
                    timeout_seconds=args.timeout_seconds,
                    production_runner=production_runner,
                )
                result["execution"] = execution
                if execution.get("control_decision") != "ALLOWED":
                    result["control_decision"] = "BLOCKED"
                    result["reason"] = execution.get("reason", "DOWNSTREAM_AUTHORIZATION_FAILED")
        elif result.get("control_decision") == "ALLOWED" and (
            args.authorization_output or result.get("schema_version") == CASE_TRANSITION_AUTHORIZATION_SCHEMA
        ):
            authorization_path = persist_stage_authorization(result)
            result["authorization_artifact"] = str(authorization_path)
        elif (
            result.get("control_decision") == "ALLOWED"
            and not args.authorization_input
            and not args.authorization_output
        ):
            result["control_decision"] = "BLOCKED"
            result["reason"] = "AUTHORIZATION_OUTPUT_REQUIRED"
        if result.get("control_decision") != "ALLOWED":
            result["status_token"] = "STAGE_EXECUTION_NOT_AUTHORIZED"
        elif authoritative_workflow:
            result.update(
                {
                    "status": "AWAITING_TRANSITION",
                    "stage_completed": False,
                    "transition_required": True,
                    "stop_before_downstream": True,
                    "workflow_state": main_workflow_state,
                }
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("control_decision") != "ALLOWED":
            return 2
        return 3 if authoritative_workflow else 0
    if args.provenance_safe:
        if args.run_dir is None:
            parser.error("--provenance-safe requires --run-dir below case work_tmp/")
        result = run_provenance_safe(
            case_dir,
            args.run_dir,
            command_argv=list(sys.argv),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int(result.get("runner_exit_code", 2))
    result = run_case(
        args.stage,
        case_dir,
        args.timeout_seconds,
        single_file=args.single_file,
        isolated_smoke=args.isolated_smoke,
        run_dir=Path(args.run_dir) if args.run_dir else None,
    )
    run_succeeded = result.get("status") == "ok"
    if authoritative_workflow:
        result["executor_status"] = result.get("status")
        if run_succeeded:
            result["status"] = "AWAITING_TRANSITION"
        result.update(
            {
                "stage_completed": False,
                "transition_required": True,
                "stop_before_downstream": True,
                "workflow_state": main_workflow_state,
            }
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not run_succeeded:
        return 2
    return 3 if authoritative_workflow else 0


if __name__ == "__main__":
    raise SystemExit(main())
