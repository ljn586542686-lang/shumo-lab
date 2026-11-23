"""Route-stage validation for the case-local route direction review artifact."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


REQUIRED_TIERS = frozenset({"selection", "formal"})
VALID_STATUSES = frozenset({"PASS", "MANUAL_REVIEW_REQUIRED", "BLOCKED"})
ARTIFACT_RELATIVE_PATH = Path("route") / "route_direction_review.json"
VALID_REVIEW_RECORD_TYPES = frozenset({"external_chat", "human_review_note", "review_commit", "other_traceable_record"})
ROUTING_INCIDENT_ACTIONS = frozenset({"REVIEW_ONLY", "RULE_CHANGE_PROPOSED", "NO_CHANGE_JUSTIFIED"})


def _tier(case_config: dict[str, Any]) -> str:
    value = case_config.get("submission_tier")
    return str(value).strip().lower() if value is not None and str(value).strip() else "training"


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_case_file(case_dir: Path, value: object) -> tuple[Path | None, str | None]:
    if not isinstance(value, str) or not value.strip():
        return None, None
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        return None, None
    resolved_case = case_dir.resolve()
    resolved = (case_dir / relative).resolve()
    try:
        return resolved, resolved.relative_to(resolved_case).as_posix()
    except ValueError:
        return None, None


def _result_generated_at(case_dir: Path) -> datetime | None:
    result_path = case_dir / "output" / "result_summary.json"
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return _parse_timestamp(payload.get("generated_at")) if isinstance(payload, dict) else None


def _git_file_commit_timestamp(case_dir: Path, path: Path) -> int | None:
    """Return a clean, tracked file's latest git commit time without trusting JSON metadata."""

    try:
        root_result = subprocess.run(
            ["git", "-C", str(case_dir), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if root_result.returncode != 0:
            return None
        repo_root = Path(root_result.stdout.strip()).resolve()
        relative = path.resolve().relative_to(repo_root).as_posix()
        tracked = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "--error-unmatch", "--", relative],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if tracked.returncode != 0:
            return None
        for diff_args in (("diff", "--quiet", "HEAD", "--"), ("diff", "--cached", "--quiet", "HEAD", "--")):
            clean = subprocess.run(
                ["git", "-C", str(repo_root), *diff_args, relative],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if clean.returncode != 0:
                return None
        timestamp = subprocess.run(
            ["git", "-C", str(repo_root), "log", "-1", "--format=%ct", "--", relative],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if timestamp.returncode != 0 or not timestamp.stdout.strip():
            return None
        return int(timestamp.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _apply_git_timing_anchor(case_dir: Path, artifact_path: Path, result: dict[str, Any]) -> None:
    """Compare clean git evidence for review and result files; never claim an unavailable anchor passed."""

    result_path = case_dir / "output" / "result_summary.json"
    review_commit_time = _git_file_commit_timestamp(case_dir, artifact_path)
    result_commit_time = _git_file_commit_timestamp(case_dir, result_path) if result_path.is_file() else None
    result["git_timing_anchor_status"] = "UNAVAILABLE"
    result["route_review_git_commit_time"] = review_commit_time
    result["result_summary_git_commit_time"] = result_commit_time
    if review_commit_time is None or result_commit_time is None:
        result["warnings"].append("ROUTE_REVIEW_GIT_TIMING_ANCHOR_UNAVAILABLE")
        return
    if review_commit_time > result_commit_time:
        result["blockers"].append("ROUTE_REVIEW_POST_HOC_GIT_ANCHOR_FAILED")
        result["post_hoc_suspected"] = True
        result["git_timing_anchor_status"] = "FAILED"
        return
    result["git_timing_anchor_status"] = "PASS"


def _base_result(case_dir: Path, tier: str, required: bool) -> dict[str, Any]:
    return {
        "route_review_required": required,
        "route_review_status": "MANUAL_REVIEW_REQUIRED",
        "route_review_artifact_path": ARTIFACT_RELATIVE_PATH.as_posix(),
        "selected_route_id": None,
        "selected_model_family": None,
        "blockers": [],
        "warnings": [],
        "provider_role_id": None,
        "reviewer_role_id": None,
        "review_record_ref": None,
        "review_record_type": None,
        "git_timing_anchor_status": "UNAVAILABLE",
        "route_review_git_commit_time": None,
        "result_summary_git_commit_time": None,
        "rejected_alternatives_count": 0,
        "post_hoc_suspected": False,
        "route_conflict": False,
        "routing_incident_required": False,
        "routing_incident_status": "NOT_REQUIRED",
        "effective_submission_tier": tier,
    }


def _finalize(result: dict[str, Any], *, required: bool) -> dict[str, Any]:
    if result["blockers"]:
        if required:
            result["route_review_status"] = "BLOCKED"
        else:
            result["warnings"] = list(dict.fromkeys(result["warnings"] + result["blockers"]))
            result["blockers"] = []
            result["route_review_status"] = "MANUAL_REVIEW_REQUIRED"
    return result


def _validate_routing_incident(artifact: dict[str, Any], result: dict[str, Any], *, required: bool) -> None:
    """Record a low-confidence/overridden route as an incident, not a special case."""

    suggestion = artifact.get("router_suggestion")
    confidence = str(artifact.get("router_confidence") or "").upper()
    selected = str(artifact.get("selected_route_id") or "")
    incident_required = confidence == "LOW" or (isinstance(suggestion, str) and suggestion.strip() and suggestion != selected)
    result["routing_incident_required"] = incident_required
    if not incident_required:
        return
    incident = artifact.get("routing_incident")
    if not isinstance(incident, dict):
        result["routing_incident_status"] = "MISSING"
        message = "ROUTING_INCIDENT_MISSING"
        if required:
            result["blockers"].append(message)
        else:
            result["warnings"].append(message)
        return
    required_fields = ("observed_suggestion", "selected_route_id", "root_cause_hypothesis", "action")
    if any(not isinstance(incident.get(field), str) or not incident[field].strip() for field in required_fields):
        result["routing_incident_status"] = "INVALID"
        result["blockers" if required else "warnings"].append("ROUTING_INCIDENT_INVALID")
        return
    if incident["observed_suggestion"] != suggestion or incident["selected_route_id"] != selected:
        result["routing_incident_status"] = "INVALID"
        result["blockers" if required else "warnings"].append("ROUTING_INCIDENT_BINDING_MISMATCH")
        return
    if incident["action"] not in ROUTING_INCIDENT_ACTIONS or incident.get("case_specific_override") is True:
        result["routing_incident_status"] = "INVALID"
        result["blockers" if required else "warnings"].append("ROUTING_INCIDENT_ACTION_INVALID")
        return
    result["routing_incident_status"] = "PASS"


def validate_route_direction_review(
    case_dir: Path,
    case_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the case-local route review without evaluating model semantics."""

    if case_config is None:
        try:
            case_config = json.loads((case_dir / "case_config.json").read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            case_config = {}
    case_config = case_config if isinstance(case_config, dict) else {}
    tier = _tier(case_config)
    required = tier in REQUIRED_TIERS or case_config.get("route_direction_review_required") is True
    result = _base_result(case_dir, tier, required)
    artifact_path = case_dir / ARTIFACT_RELATIVE_PATH
    if not artifact_path.is_file():
        result["blockers"].append("ROUTE_REVIEW_MISSING")
        return _finalize(result, required=required)

    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        result["blockers"].append("ROUTE_REVIEW_MALFORMED")
        return _finalize(result, required=required)
    if not isinstance(artifact, dict):
        result["blockers"].append("ROUTE_REVIEW_MALFORMED")
        return _finalize(result, required=required)

    result["selected_route_id"] = artifact.get("selected_route_id")
    result["selected_model_family"] = artifact.get("selected_model_family")
    result["provider_role_id"] = artifact.get("provider_role_id")
    result["reviewer_role_id"] = artifact.get("reviewer_role_id")
    review_evidence = artifact.get("review_evidence")
    if isinstance(review_evidence, dict):
        result["review_record_ref"] = review_evidence.get("review_record_ref")
        result["review_record_type"] = review_evidence.get("review_record_type")
    alternatives = artifact.get("rejected_alternatives")
    result["rejected_alternatives_count"] = len(alternatives) if isinstance(alternatives, list) else 0
    _validate_routing_incident(artifact, result, required=required)

    required_fields = (
        "version", "case_id", "submission_tier", "status", "selected_direction",
        "selected_route_id", "selected_model_family", "evidence_inputs",
        "rejected_alternatives", "red_team_challenge", "red_team_response",
        "provider_role_id", "reviewer_role_id", "immutable_after_result_generation",
        "result_generation_state_at_review",
    )
    if any(field not in artifact for field in required_fields) or not ("frozen_at" in artifact or "created_at" in artifact):
        result["blockers"].append("ROUTE_REVIEW_INVALID_SCHEMA")
    status = artifact.get("status")
    if status not in VALID_STATUSES:
        result["blockers"].append("ROUTE_REVIEW_INVALID_SCHEMA")
    elif status == "BLOCKED":
        result["blockers"].append("ROUTE_REVIEW_STATUS_BLOCKED")
    elif status == "MANUAL_REVIEW_REQUIRED":
        result["blockers"].append("ROUTE_REVIEW_MANUAL_REVIEW_REQUIRED")

    for field in ("version", "case_id", "submission_tier"):
        if not isinstance(artifact.get(field), str) or not artifact[field].strip():
            result["blockers"].append("ROUTE_REVIEW_INVALID_SCHEMA")
            break
    for field in ("selected_direction", "selected_route_id", "selected_model_family", "provider_role_id", "reviewer_role_id"):
        if not isinstance(artifact.get(field), str) or not artifact[field].strip():
            result["blockers"].append("ROUTE_REVIEW_INVALID_SCHEMA")
            break

    evidence_inputs = artifact.get("evidence_inputs")
    if not isinstance(evidence_inputs, list):
        result["blockers"].append("ROUTE_REVIEW_INVALID_SCHEMA")
    elif required and not evidence_inputs:
        result["blockers"].append("ROUTE_REVIEW_EVIDENCE_MISSING")
    else:
        for item in evidence_inputs:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not item["path"].strip() or not isinstance(item.get("role"), str) or not item["role"].strip():
                result["blockers"].append("ROUTE_REVIEW_INVALID_SCHEMA")
                break
            evidence_path, _normalized = _safe_case_file(case_dir, item["path"])
            if evidence_path is None or (required and not evidence_path.is_file()):
                result["blockers"].append("ROUTE_REVIEW_EVIDENCE_MISSING")
                break

    if not isinstance(alternatives, list):
        result["blockers"].append("ROUTE_REVIEW_INVALID_SCHEMA")
    else:
        route_ids: set[str] = set()
        for alternative in alternatives:
            if not isinstance(alternative, dict) or not all(
                isinstance(alternative.get(field), str) and alternative[field].strip()
                for field in ("route_id", "model_family", "rejection_reason")
            ):
                result["blockers"].append(
                    "ROUTE_REVIEW_EMPTY_REJECTION_REASON"
                    if isinstance(alternative, dict) and not str(alternative.get("rejection_reason") or "").strip()
                    else "ROUTE_REVIEW_INVALID_SCHEMA"
                )
                break
            route_id = alternative["route_id"].strip()
            if route_id in route_ids or route_id == str(artifact.get("selected_route_id") or "").strip():
                result["blockers"].append("ROUTE_REVIEW_INVALID_SCHEMA")
                break
            route_ids.add(route_id)
        if required and len(alternatives) < 2:
            result["blockers"].append("ROUTE_REVIEW_INSUFFICIENT_ALTERNATIVES")

    if required:
        if not isinstance(artifact.get("red_team_challenge"), str) or not artifact["red_team_challenge"].strip():
            result["blockers"].append("ROUTE_REVIEW_INVALID_SCHEMA")
        if not isinstance(artifact.get("red_team_response"), str) or not artifact["red_team_response"].strip():
            result["blockers"].append("ROUTE_REVIEW_INVALID_SCHEMA")
        if artifact.get("provider_role_id") == artifact.get("reviewer_role_id") or not artifact.get("reviewer_role_id"):
            result["blockers"].append("ROUTE_REVIEW_ROLE_SEPARATION_FAILED")
        if not isinstance(review_evidence, dict) or not isinstance(review_evidence.get("review_record_ref"), str) or not review_evidence["review_record_ref"].strip() or review_evidence.get("review_record_type") not in VALID_REVIEW_RECORD_TYPES:
            result["blockers"].append("ROUTE_REVIEW_STRUCTURAL_EVIDENCE_MISSING")
        if artifact.get("immutable_after_result_generation") is not True:
            result["blockers"].append("ROUTE_REVIEW_INVALID_SCHEMA")
        if artifact.get("result_generation_state_at_review") != "not_started":
            result["blockers"].append("ROUTE_REVIEW_POST_HOC_SUSPECTED")
            result["post_hoc_suspected"] = True
    elif not isinstance(review_evidence, dict) or not isinstance(review_evidence.get("review_record_ref"), str) or not review_evidence["review_record_ref"].strip() or review_evidence.get("review_record_type") not in VALID_REVIEW_RECORD_TYPES:
        result["warnings"].append("ROUTE_REVIEW_STRUCTURAL_EVIDENCE_MISSING")

    review_timestamp = _parse_timestamp(artifact.get("frozen_at") or artifact.get("created_at"))
    if review_timestamp is None:
        result["blockers"].append("ROUTE_REVIEW_INVALID_SCHEMA")
    generated_at = _result_generated_at(case_dir)
    if review_timestamp is not None and generated_at is not None and review_timestamp > generated_at:
        result["blockers"].append("ROUTE_REVIEW_POST_HOC_SUSPECTED")
        result["post_hoc_suspected"] = True

    _apply_git_timing_anchor(case_dir, artifact_path, result)

    result["blockers"] = list(dict.fromkeys(result["blockers"]))
    if not result["blockers"]:
        result["route_review_status"] = "PASS" if status == "PASS" else "MANUAL_REVIEW_REQUIRED"
    return _finalize(result, required=required)
