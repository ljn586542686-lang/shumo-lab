"""OV11C3A: run the provenance-safe runner inside an isolated git worktree.

``run_case.run_provenance_safe`` already fails closed when the process's own
working tree (``x5_surgical_utils.ROOT``) is not clean, but it never creates
its own execution environment — it trusts whatever tree the caller already
happens to be standing in. The real repository root routinely carries
long-lived untracked ``work_tmp/`` artifacts from earlier OV11 stages (and,
incidentally, unrelated untracked directories from other in-flight work), so
running the safe mode directly against the main worktree observes that
pre-existing noise as worktree pollution before the run itself ever starts.

This module is the missing outer layer: it creates a fresh, detached
``git worktree`` from a pinned baseline commit (the same pattern already used
by OV11B2's isolated rebuild), invokes ``run_case.py --provenance-safe`` as a
subprocess with that worktree as its working directory, and then re-verifies
the resulting provenance pair with
``package_candidate._verify_source_provenance`` from inside that same
isolated worktree so the verification sees exactly the tree the run actually
produced. It never touches the main repository worktree's tracked or
untracked files, never authorizes candidate packaging or final submission,
and never deletes the worktree it creates -- cleanup is left to an explicit,
separate, human-reviewed step.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from x5_surgical_utils import ROOT, read_json, utc_now, write_json

# case_config.json fields that name gitignored work_tmp/ evidence files
# run_provenance_safe() requires as input. git worktree add only checks out
# *tracked* content, so anything under a case's gitignored work_tmp/ (the
# OV11B2 isolated-rebuild PDF and its binding evidence) is absent from a
# freshly created worktree by construction, not by any fault of the runner
# itself. This orchestrator must copy exactly these named files across the
# worktree boundary -- and nothing else from work_tmp/ -- so the isolated
# run sees the same evidence the main worktree already verified.
#
# OV11C3C: package_candidate()'s main entry additionally reads
# candidate_upstream_run_result_path/candidate_upstream_risk_summary_path
# (also gitignored work_tmp/ evidence) before it ever reaches provenance
# verification. Without copying these too, an isolated run would see
# UPSTREAM_STATUS_PATH_MISSING_OR_UNSAFE (a path-transport artifact of this
# orchestrator, not a real production blocker) instead of whatever the
# upstream status file's actual persisted semantic state is -- masking the
# real first-effective-blocker with a false one. Copying them does not
# change what package_candidate() concludes; it only lets the isolated run
# see the same bytes the main worktree already has.
_REQUIRED_GITIGNORED_EVIDENCE_CONFIG_FIELDS = ("ov11b2_isolated_rebuild_output_path",)
_REQUIRED_GITIGNORED_EVIDENCE_FIXED_RELATIVE_PATHS = ("work_tmp/ov11b2_artifact_binding_evidence.json",)

# Fields whose evidence copy must fail closed (raise) rather than silently
# skip on a missing source, an out-of-bounds path, or a post-copy hash
# mismatch. Unlike the OV11B2 fields above (which run_provenance_safe()
# itself already fails closed on when missing, so a silent skip here is
# redundant rather than dangerous), these feed package_candidate()'s main
# entry directly -- a silently-skipped copy would let that entry see a
# missing file and report UPSTREAM_STATUS_PATH_MISSING_OR_UNSAFE, which is
# indistinguishable from a real production blocker unless this transport
# step itself refuses to proceed on ambiguity.
_FAIL_CLOSED_GITIGNORED_EVIDENCE_CONFIG_FIELDS = (
    "candidate_upstream_run_result_path",
    "candidate_upstream_risk_summary_path",
)


class ProvenanceSafeOrchestrationError(ValueError):
    """Raised when the isolated run cannot even be attempted safely."""


def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _require_clean_main_repo_for_worktree_add(root: Path) -> None:
    """``git worktree add`` only needs the target path free and the commit
    resolvable; it does not require the *main* worktree to be clean. This
    check exists only to reject an in-progress merge/rebase/cherry-pick,
    which can make ``git worktree add`` behave unpredictably."""

    git_dir = root / ".git"
    for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "rebase-merge", "rebase-apply", "BISECT_LOG"):
        if (git_dir / marker).exists():
            raise ProvenanceSafeOrchestrationError(
                f"main repository has an in-progress git operation ({marker}); refusing to create a worktree"
            )


def _resolve_baseline_commit(root: Path, baseline_commit: str) -> str:
    result = _run_git(["rev-parse", "--verify", f"{baseline_commit}^{{commit}}"], cwd=root)
    if result.returncode != 0:
        raise ProvenanceSafeOrchestrationError(
            f"baseline commit does not resolve to a real commit: {baseline_commit!r}: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def create_isolated_worktree(root: Path, worktree_path: Path, baseline_commit: str) -> str:
    """Create a fresh, detached worktree at ``worktree_path`` pinned to
    ``baseline_commit``. Fails closed if the path already exists or the
    commit cannot be resolved. Returns the resolved commit hash."""

    _require_clean_main_repo_for_worktree_add(root)
    resolved_commit = _resolve_baseline_commit(root, baseline_commit)
    if worktree_path.exists():
        raise ProvenanceSafeOrchestrationError(f"worktree target path already exists: {worktree_path}")
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    # -c core.autocrlf=false: on a Windows checkout with the platform-common
    # core.autocrlf=true, a fresh `git worktree add` rewrites LF blobs to
    # CRLF on checkout, so the new worktree's file bytes -- and therefore
    # their SHA-256 -- would no longer match what the commit object store
    # actually holds (reproduced directly: an 11683-byte LF blob became an
    # 11795-byte CRLF file with 112 line endings rewritten). Disabling it
    # for this checkout keeps the isolated worktree byte-identical to the
    # pinned commit, which is the only correct interpretation of "pinned to
    # a baseline commit" for a provenance verification step.
    result = _run_git(
        ["-c", "core.autocrlf=false", "worktree", "add", "--detach", str(worktree_path), resolved_commit],
        cwd=root,
    )
    if result.returncode != 0:
        raise ProvenanceSafeOrchestrationError(
            f"git worktree add failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return resolved_commit


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_one_evidence_file(
    *,
    main_case_dir: Path,
    worktree_case_dir: Path,
    relative: str,
    fail_closed: bool,
) -> dict[str, Any] | None:
    """Copy a single named evidence file across the worktree boundary.

    Returns a copy record on success, or None if the file is absent/unsafe
    and ``fail_closed`` is False (the legacy, silent-skip behavior kept for
    the OV11B2 fields run_provenance_safe() already fails closed on itself).
    When ``fail_closed`` is True, any of a missing source, an out-of-bounds
    path, or a post-copy hash mismatch raises instead of skipping -- this
    transport step must not let package_candidate()'s main entry observe an
    ambiguous "file just isn't there" in place of the file's real, already-
    bound upstream status.
    """

    resolved_case_root = main_case_dir.resolve()
    raw_path = Path(relative)
    source = (main_case_dir / raw_path).resolve()
    try:
        source.relative_to(resolved_case_root)
    except ValueError:
        if fail_closed:
            raise ProvenanceSafeOrchestrationError(
                f"gitignored evidence path escapes case root: {relative}"
            ) from None
        return None
    if not source.is_file():
        if fail_closed:
            raise ProvenanceSafeOrchestrationError(
                f"gitignored evidence source file is missing or not a regular file: {relative}"
            )
        return None

    destination = worktree_case_dir / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    source_hash = _sha256_file(source)
    destination_hash = _sha256_file(destination)
    if source_hash != destination_hash:
        raise ProvenanceSafeOrchestrationError(
            f"gitignored evidence copy hash mismatch for {relative}: "
            f"source={source_hash} destination={destination_hash}"
        )
    return {
        "relative_path": relative,
        "sha256": source_hash,
        "source_size_bytes": source.stat().st_size,
        "destination_size_bytes": destination.stat().st_size,
    }


def copy_gitignored_evidence_into_worktree(
    *,
    main_case_dir: Path,
    worktree_case_dir: Path,
    include_package_candidate_upstream_status: bool = False,
) -> list[dict[str, Any]]:
    """Copy the fixed set of gitignored evidence files a provenance-safe run
    (and, when requested, package_candidate()'s main entry) requires, from
    the main case directory (a fixed, pinned baseline commit's checkout --
    never an unrelated or possibly-modified working tree) into the isolated
    worktree's case directory, verifying byte-for-byte identity after the
    copy.

    This function copies only the specific paths named by
    ``_REQUIRED_GITIGNORED_EVIDENCE_CONFIG_FIELDS``,
    ``_REQUIRED_GITIGNORED_EVIDENCE_FIXED_RELATIVE_PATHS``, and -- only when
    ``include_package_candidate_upstream_status`` is True --
    ``_FAIL_CLOSED_GITIGNORED_EVIDENCE_CONFIG_FIELDS`` -- never the entire
    ``work_tmp/`` tree, and never any other gitignored file not on this
    explicit allowlist -- so no unrelated untracked artifact crosses the
    isolation boundary. The content, status fields, hashes, or semantics of
    the copied files are never modified; this function only transports
    existing bytes and verifies they arrived unchanged.

    ``include_package_candidate_upstream_status`` defaults to False so a
    plain ``run_provenance_safe()`` isolated run (which never reads
    candidate_upstream_run_result_path/candidate_upstream_risk_summary_path)
    is not forced to require case fixtures it has no use for. Callers that
    go on to invoke package_candidate()'s main entry inside the same
    worktree must pass True, or that entry will observe those two files as
    simply missing rather than seeing their real persisted upstream status.
    """

    copied: list[dict[str, Any]] = []
    config = read_json(main_case_dir / "case_config.json", default=None)

    legacy_relative_paths: list[str] = list(_REQUIRED_GITIGNORED_EVIDENCE_FIXED_RELATIVE_PATHS)
    fail_closed_relative_paths: list[str] = []
    if isinstance(config, dict):
        for field in _REQUIRED_GITIGNORED_EVIDENCE_CONFIG_FIELDS:
            value = config.get(field)
            if isinstance(value, str) and value.strip():
                legacy_relative_paths.append(value.strip())
        if include_package_candidate_upstream_status:
            for field in _FAIL_CLOSED_GITIGNORED_EVIDENCE_CONFIG_FIELDS:
                value = config.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise ProvenanceSafeOrchestrationError(
                        f"case_config.json is missing a value for required evidence field: {field}"
                    )
                fail_closed_relative_paths.append(value.strip())
    elif include_package_candidate_upstream_status and _FAIL_CLOSED_GITIGNORED_EVIDENCE_CONFIG_FIELDS:
        raise ProvenanceSafeOrchestrationError(
            "case_config.json is unreadable; cannot resolve required fail-closed evidence fields"
        )

    for relative in legacy_relative_paths:
        record = _copy_one_evidence_file(
            main_case_dir=main_case_dir,
            worktree_case_dir=worktree_case_dir,
            relative=relative,
            fail_closed=False,
        )
        if record is not None:
            copied.append(record)

    for relative in fail_closed_relative_paths:
        record = _copy_one_evidence_file(
            main_case_dir=main_case_dir,
            worktree_case_dir=worktree_case_dir,
            relative=relative,
            fail_closed=True,
        )
        assert record is not None  # fail_closed=True never returns None; it raises instead
        copied.append(record)

    return copied


def run_isolated_provenance_safe(
    *,
    case_relative_dir: str,
    run_dir_relative_to_case: str,
    worktree_path: Path,
    baseline_commit: str,
    root: Path | None = None,
) -> dict[str, Any]:
    """Create an isolated worktree, run ``--provenance-safe`` inside it, and
    re-verify the result from inside that same worktree.

    ``case_relative_dir`` and ``run_dir_relative_to_case`` are interpreted
    exactly as ``run_case.py --case-dir``/``--run-dir`` interpret them, but
    resolved against the isolated worktree, never the main repository.
    Nothing outside ``worktree_path`` is written by this function except the
    single orchestration record this call returns to its caller for the
    caller to persist under the main repo's own ``work_tmp/``.
    """

    resolved_root = (root or ROOT).resolve()
    started_at = utc_now()
    resolved_commit = create_isolated_worktree(resolved_root, worktree_path, baseline_commit)

    case_dir_in_worktree = worktree_path / case_relative_dir
    if not case_dir_in_worktree.is_dir():
        raise ProvenanceSafeOrchestrationError(
            f"case directory does not exist inside isolated worktree: {case_dir_in_worktree}"
        )

    copied_evidence = copy_gitignored_evidence_into_worktree(
        main_case_dir=resolved_root / case_relative_dir,
        worktree_case_dir=case_dir_in_worktree,
    )

    run_argv = [
        sys.executable,
        "run_case.py",
        "--provenance-safe",
        "--case-dir",
        str(case_dir_in_worktree),
        "--run-dir",
        run_dir_relative_to_case,
    ]
    run_completed = subprocess.run(
        run_argv,
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )

    run_dir_abs = case_dir_in_worktree / run_dir_relative_to_case
    run_result_path = run_dir_abs / "run_result.json"
    risk_summary_path = run_dir_abs / "risk_summary.json"

    run_result_payload: dict[str, Any] | None = None
    risk_summary_payload: dict[str, Any] | None = None
    if run_result_path.is_file():
        try:
            run_result_payload = json.loads(run_result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            run_result_payload = None
    if risk_summary_path.is_file():
        try:
            risk_summary_payload = json.loads(risk_summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            risk_summary_payload = None

    verification: dict[str, Any] | None = None
    verification_error: str | None = None
    if run_result_payload is not None and risk_summary_payload is not None:
        try:
            verify_argv = [
                sys.executable,
                "-c",
                (
                    "import json, sys\n"
                    "from pathlib import Path\n"
                    "from package_candidate import _verify_source_provenance\n"
                    "case_dir = Path(sys.argv[1])\n"
                    "root = Path(sys.argv[2])\n"
                    "settings = {\n"
                    "    'submission_tier': 'formal',\n"
                    "    'effective_upstream_status_required': True,\n"
                    "    'upstream_status_paths': [\n"
                    "        {'field': 'candidate_upstream_run_result_path', 'path': sys.argv[3]},\n"
                    "        {'field': 'candidate_upstream_risk_summary_path', 'path': sys.argv[4]},\n"
                    "    ],\n"
                    "}\n"
                    "result = _verify_source_provenance(case_dir, root, settings)\n"
                    "print(json.dumps(result, ensure_ascii=False))\n"
                ),
                str(case_dir_in_worktree),
                str(worktree_path),
                str(run_result_path),
                str(risk_summary_path),
            ]
            verify_completed = subprocess.run(
                verify_argv,
                cwd=worktree_path,
                capture_output=True,
                text=True,
                check=False,
            )
            if verify_completed.returncode == 0 and verify_completed.stdout.strip():
                verification = json.loads(verify_completed.stdout.strip().splitlines()[-1])
            else:
                verification_error = (
                    verify_completed.stderr.strip() or verify_completed.stdout.strip() or "verification subprocess failed"
                )
        except (OSError, json.JSONDecodeError) as exc:
            verification_error = f"{type(exc).__name__}: {exc}"
    else:
        verification_error = "run_result.json or risk_summary.json missing after provenance-safe execution"

    post_run_status = _run_git(["status", "--porcelain=v1", "--untracked-files=all"], cwd=worktree_path)

    orchestration_record = {
        "schema_version": "ov11c3a_isolated_provenance_safe_orchestration_v1",
        "started_at": started_at,
        "completed_at": utc_now(),
        "baseline_commit_requested": baseline_commit,
        "baseline_commit_resolved": resolved_commit,
        "worktree_path": str(worktree_path),
        "case_relative_dir": case_relative_dir,
        "run_dir_relative_to_case": run_dir_relative_to_case,
        "copied_gitignored_evidence": copied_evidence,
        "run_argv": run_argv,
        "run_exit_code": run_completed.returncode,
        "run_stdout_tail": run_completed.stdout[-4000:],
        "run_stderr_tail": run_completed.stderr[-4000:],
        "run_result": run_result_payload,
        "risk_summary": risk_summary_payload,
        "post_run_verification": verification,
        "post_run_verification_error": verification_error,
        "post_run_worktree_status_raw": post_run_status.stdout if post_run_status.returncode == 0 else None,
        "candidate_package_authorized": False,
        "final_freeze_authorized": False,
        "final_submit_creation_authorized": False,
        "candidate_package_created": False,
        "final_submit_created": False,
    }
    return orchestration_record


_PACKAGE_CANDIDATE_SUBPROCESS_SCRIPT = """
import json
import sys
from pathlib import Path

from package_candidate import (
    CandidatePromotionClaimCheckBlocked,
    CandidatePromotionHumanRevisionBlocked,
    CandidatePromotionProvenanceBlocked,
    CandidatePromotionUpstreamBindingBlocked,
    CandidatePromotionUpstreamStatusBlocked,
    package_candidate,
)

case_dir = Path(sys.argv[1])
root = Path(sys.argv[2])

known_blocked_exceptions = (
    CandidatePromotionUpstreamStatusBlocked,
    CandidatePromotionClaimCheckBlocked,
    CandidatePromotionHumanRevisionBlocked,
    CandidatePromotionUpstreamBindingBlocked,
    CandidatePromotionProvenanceBlocked,
)

outcome = {"outcome_kind": None, "exception_type": None, "result": None, "traceback": None}
try:
    result = package_candidate(case_dir=case_dir, root=root)
    outcome["outcome_kind"] = "RETURNED"
    outcome["result"] = result
except known_blocked_exceptions as exc:
    outcome["outcome_kind"] = "KNOWN_BLOCKED_EXCEPTION"
    outcome["exception_type"] = type(exc).__name__
    outcome["result"] = getattr(exc, "result", None)
except Exception as exc:  # noqa: BLE001 - deliberately broad: this is an
    # observation harness. An unexpected/uncaught exception from
    # package_candidate() itself is exactly the kind of finding this task
    # must record, not swallow or convert into a false BLOCKED result.
    import traceback as tb_module

    outcome["outcome_kind"] = "UNEXPECTED_EXCEPTION"
    outcome["exception_type"] = type(exc).__name__
    outcome["exception_message"] = str(exc)
    outcome["traceback"] = tb_module.format_exc()

print(json.dumps(outcome, ensure_ascii=False, default=str))
"""


def run_isolated_package_candidate(
    *,
    case_relative_dir: str,
    worktree_path: Path,
    baseline_commit: str,
    root: Path | None = None,
) -> dict[str, Any]:
    """Create an isolated worktree and invoke package_candidate()'s real
    main entry inside it as a subprocess, capturing its full structured
    outcome -- a normal return, one of its known structured "blocked"
    exceptions (each of which already carries a status/code/message
    result dict), or a genuinely unexpected exception -- without ever
    letting the subprocess's own sys.exit or traceback terminate this
    orchestration silently.

    This function calls the real package_candidate() main entry, not any
    of its individual gate helpers in isolation, so the gate execution
    order and the first gate that actually raises are exactly what
    production code would produce.

    include_package_candidate_upstream_status=True is always passed to the
    gitignored-evidence copy step, since package_candidate()'s main entry
    always reads candidate_upstream_run_result_path/
    candidate_upstream_risk_summary_path before reaching provenance.
    """

    resolved_root = (root or ROOT).resolve()
    started_at = utc_now()
    resolved_commit = create_isolated_worktree(resolved_root, worktree_path, baseline_commit)

    case_dir_in_worktree = worktree_path / case_relative_dir
    if not case_dir_in_worktree.is_dir():
        raise ProvenanceSafeOrchestrationError(
            f"case directory does not exist inside isolated worktree: {case_dir_in_worktree}"
        )

    pre_run_status = _run_git(["status", "--porcelain=v1", "--untracked-files=all"], cwd=worktree_path)

    copied_evidence = copy_gitignored_evidence_into_worktree(
        main_case_dir=resolved_root / case_relative_dir,
        worktree_case_dir=case_dir_in_worktree,
        include_package_candidate_upstream_status=True,
    )

    run_argv = [
        sys.executable,
        "-c",
        _PACKAGE_CANDIDATE_SUBPROCESS_SCRIPT,
        str(case_dir_in_worktree),
        str(worktree_path),
    ]
    run_completed = subprocess.run(
        run_argv,
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )

    outcome: dict[str, Any] | None = None
    outcome_parse_error: str | None = None
    if run_completed.stdout.strip():
        try:
            outcome = json.loads(run_completed.stdout.strip().splitlines()[-1])
        except json.JSONDecodeError as exc:
            outcome_parse_error = f"{type(exc).__name__}: {exc}"
    else:
        outcome_parse_error = "subprocess produced no stdout"

    post_run_status = _run_git(["status", "--porcelain=v1", "--untracked-files=all"], cwd=worktree_path)

    candidate_submit_dir = worktree_path / "candidate_submit"
    candidate_emergency_dir = worktree_path / "candidate_emergency"
    final_submit_dir = worktree_path / "final_submit"
    case_final_submit_dir = case_dir_in_worktree / "final_submit"

    orchestration_record = {
        "schema_version": "ov11c3c_isolated_package_candidate_orchestration_v1",
        "started_at": started_at,
        "completed_at": utc_now(),
        "baseline_commit_requested": baseline_commit,
        "baseline_commit_resolved": resolved_commit,
        "worktree_path": str(worktree_path),
        "case_relative_dir": case_relative_dir,
        "copied_gitignored_evidence": copied_evidence,
        "pre_run_worktree_status_raw": pre_run_status.stdout if pre_run_status.returncode == 0 else None,
        "pre_run_worktree_strictly_clean": (pre_run_status.returncode == 0 and pre_run_status.stdout == ""),
        "run_argv": [run_argv[0], "-c", "<inline script>", *run_argv[3:]],
        "run_exit_code": run_completed.returncode,
        "run_stdout_tail": run_completed.stdout[-8000:],
        "run_stderr_tail": run_completed.stderr[-8000:],
        "outcome": outcome,
        "outcome_parse_error": outcome_parse_error,
        "post_run_worktree_status_raw": post_run_status.stdout if post_run_status.returncode == 0 else None,
        "post_run_worktree_strictly_clean": (post_run_status.returncode == 0 and post_run_status.stdout == ""),
        "candidate_submit_dir_created": candidate_submit_dir.exists(),
        "candidate_emergency_dir_created": candidate_emergency_dir.exists(),
        "final_submit_dir_created_at_root": final_submit_dir.exists(),
        "final_submit_dir_created_in_case": case_final_submit_dir.exists(),
        "candidate_package_authorized": False,
        "final_freeze_authorized": False,
        "final_submit_creation_authorized": False,
    }
    return orchestration_record


def persist_orchestration_record(root: Path, record: dict[str, Any], destination: Path) -> Path:
    """Write the orchestration record under the caller-provided destination,
    which must already be validated by the caller to live under the main
    repository's ``work_tmp/``. This function performs no path safety
    validation of its own; callers must not pass an unvalidated path."""

    write_json(destination, record)
    return destination
