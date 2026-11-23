"""Read-only human Gate evaluation and competition transition authorization.

This module is intentionally a small control-plane primitive.  It reads the
existing case human-review files, never edits them, and does not infer
authorization from handoff text, decision logs, chat text, or status tokens.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from pipeline_core.python_tools.gate_authority import evaluate_gate_authority
from pipeline_core.python_tools.model_authority import verify_model_authority
from pipeline_core.python_tools.transition_case import (
    AUTHORIZATION_SCHEMA,
    REQUIRED_GATE_BY_TARGET,
    STAGE_TRANSITIONS as AUTHORITATIVE_STAGE_TRANSITIONS,
    authorize_transition as authorize_authoritative_transition,
    is_authoritative_config,
    normalize_stage,
    verify_transition_authorization,
)


GATE_FILES = {
    "GATE1": "human_review/gate_1_problem_understanding.md",
    "GATE2": "human_review/gate_2_model_choice.md",
    "GATE3": "human_review/gate_3_result_acceptance.md",
    "GATE4": "human_review/gate_4_final_submission.md",
}

COMPETITION_MODES = {"competition", "full_e2e_competition_simulation"}

STAGE_TRANSITIONS = {
    **AUTHORITATIVE_STAGE_TRANSITIONS,
    # Legacy cases used MODEL_SELECTION as a separately named predecessor.
    # It remains readable, but authoritative cases normalize it through the
    # single transition kernel.
    "MODEL_SELECTION": "MODEL_IMPLEMENTATION",
    "PAPER_GENERATION": "FINAL_FREEZE",
    "FINAL_FREEZE": "FINAL_SUBMISSION",
}

STAGE_REQUIRED_GATE = {
    **REQUIRED_GATE_BY_TARGET,
    "MODEL_SELECTION": "GATE1",
    "RESULT_ACCEPTANCE": "GATE2",
    "PAPER_GENERATION": "GATE3",
    "WRITER": "GATE3",
    "FINAL_FREEZE": "GATE4",
    "FINAL_SUBMISSION": "GATE4",
}

COMPETITION_ACTION_REQUIRED_STAGE = {
    "TEST_MODEL_EXECUTION": "MODEL_IMPLEMENTATION",
    "RUN_MODEL_SOLVER": "SOLVER_EXECUTION",
}


def _case_id(case_path: str | Path) -> str | None:
    config_path = Path(case_path) / "case_config.json"
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    value = payload.get("case_id") if isinstance(payload, dict) else None
    return str(value).strip() if value else None


def _normalize_gate_id(gate_id: str) -> str | None:
    token = str(gate_id or "").strip().upper().replace("_", "")
    if token in {"GATE1", "1"}:
        return "GATE1"
    if token in {"GATE2", "2"}:
        return "GATE2"
    if token in {"GATE3", "3"}:
        return "GATE3"
    if token in {"GATE4", "4"}:
        return "GATE4"
    return None


def _field_value(text: str, names: tuple[str, ...]) -> str:
    pattern = re.compile(
        rf"(?im)^[ \t]*(?:{'|'.join(re.escape(name) for name in names)})[ \t]*[:：=][ \t]*(.*?)[ \t]*$"
    )
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def _decision_section_lines(lines: list[str]) -> list[str]:
    start: int | None = None
    for index, line in enumerate(lines):
        if re.match(r"^\s*#{1,6}\s+.*(?:决策|decision)", line, flags=re.IGNORECASE):
            start = index + 1
            break
    if start is None:
        return []
    end = len(lines)
    for index in range(start, len(lines)):
        if re.match(r"^\s*#{1,6}\s+", lines[index]):
            end = index
            break
    return lines[start:end]


def _checkboxes(lines: list[str]) -> list[tuple[bool, str]]:
    found: list[tuple[bool, str]] = []
    for line in lines:
        match = re.match(r"^\s*-\s+\[([ xX])\]\s+(.+?)\s*$", line)
        if match:
            found.append((match.group(1).lower() == "x", match.group(2).strip()))
    return found


def _classify_gate_decision(label: str) -> str:
    """Classify one human decision label without treating reject text as approve."""

    normalized = re.sub(r"\s+", " ", str(label or "").strip().lower())
    reject_tokens = ("退回", "拒绝", "不通过", "reject", "return", "rejected", "deny")
    approve_tokens = ("通过", "批准", "允许进入", "approve", "approved", "allow", "accept")
    if any(token in normalized for token in reject_tokens):
        return "REJECT"
    if any(token in normalized for token in approve_tokens):
        return "APPROVE"
    return "UNKNOWN"


def evaluate_human_gate(case_path: str | Path, gate_id: str) -> dict[str, Any]:
    """Evaluate one existing human Gate with fail-closed semantics."""

    normalized_gate = _normalize_gate_id(gate_id)
    result: dict[str, Any] = {
        "gate_id": normalized_gate or str(gate_id),
        "gate_status": "FAIL",
        "decision": None,
        "confirmed_by": None,
        "confirmed_at": None,
        "reasons": [],
    }
    if normalized_gate is None:
        result["reasons"].append("GATE_ID_INVALID")
        return result

    gate_path = Path(case_path) / GATE_FILES[normalized_gate]
    result["path"] = GATE_FILES[normalized_gate]
    if not gate_path.is_file():
        result["reasons"].append("GATE_FILE_MISSING")
        return result
    authority = evaluate_gate_authority(
        case_path,
        case_id=_case_id(case_path),
        gate_id=normalized_gate,
        evidence_relative_path=GATE_FILES[normalized_gate],
    )
    if authority is not None:
        result["authority"] = authority
        if authority.get("status") != "PASS":
            result["reasons"].append(str(authority.get("reason") or "GATE_AUTHORITY_INVALID"))
            return result
        result.update(
            {
                "gate_status": "PASS",
                "decision": "APPROVED_BY_GATE_AUTHORITY",
                "decision_class": "APPROVE",
                "confirmed_by": authority.get("confirmed_by"),
                "confirmed_at": authority.get("confirmed_at"),
                "semantic_acceptance": authority.get("semantic_acceptance"),
            }
        )
        return result
    try:
        text = gate_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        result["reasons"].append("GATE_FILE_UNREADABLE")
        return result

    lines = text.splitlines()
    all_boxes = _checkboxes(lines)
    decision_boxes = _checkboxes(_decision_section_lines(lines))
    non_decision_count = len(all_boxes) - len(decision_boxes)
    if not all_boxes:
        result["reasons"].append("GATE_CHECKBOXES_MISSING")
    if not decision_boxes:
        result["reasons"].append("GATE_DECISION_MISSING")
    if any(not checked for checked, _ in all_boxes[:non_decision_count]):
        result["reasons"].append("GATE_REQUIRED_CHECKBOX_UNCHECKED")

    checked_decisions = [label for checked, label in decision_boxes if checked]
    if len(checked_decisions) != 1:
        result["reasons"].append("GATE_DECISION_NOT_EXACTLY_ONE")
    else:
        result["decision"] = checked_decisions[0]
        result["decision_class"] = _classify_gate_decision(checked_decisions[0])
        if result["decision_class"] == "REJECT":
            result["reasons"].append("GATE_DECISION_REJECTED")
        elif result["decision_class"] == "UNKNOWN":
            result["reasons"].append("GATE_DECISION_UNRECOGNIZED")

    confirmed_by = _field_value(text, ("确认人", "confirmed_by", "confirmed by"))
    confirmed_at = _field_value(text, ("时间", "confirmed_at", "confirmed at"))
    result["confirmed_by"] = confirmed_by or None
    result["confirmed_at"] = confirmed_at or None
    if not confirmed_by:
        result["reasons"].append("GATE_CONFIRMER_MISSING")
    if not confirmed_at:
        result["reasons"].append("GATE_CONFIRMED_AT_MISSING")

    if normalized_gate == "GATE4":
        if not re.search(r"(?im)(?:HUMAN_GATE_STATUS|HUMAN_APPROVAL_STATUS)\s*[:=]\s*APPROVED\b", text):
            result["reasons"].append("GATE4_APPROVED_MARKER_MISSING")
        approved_by = _field_value(text, ("APPROVED_BY",))
        manual_confirmation = _field_value(text, ("MANUAL_CONFIRMATION",))
        if not approved_by or not manual_confirmation:
            result["reasons"].append("GATE4_MANUAL_MARKER_INCOMPLETE")
        if approved_by and any(token in approved_by.lower() for token in ("writer", "codex", "claude", "chatgpt", "ai")):
            result["reasons"].append("GATE4_NON_HUMAN_SIGNER")

    result["reasons"] = list(dict.fromkeys(result["reasons"]))
    if not result["reasons"]:
        result["gate_status"] = "PASS"
    return result


def authorize_stage_transition(
    case_path: str | Path,
    *,
    current_stage: str,
    requested_stage: str,
    mode: str,
) -> dict[str, Any]:
    """Authorize a stage transition without executing its downstream work."""

    current = str(current_stage or "").strip().upper()
    requested = str(requested_stage or "").strip().upper()
    normalized_mode = str(mode or "").strip().lower()
    try:
        case_config = json.loads((Path(case_path) / "case_config.json").read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        case_config = {}
    if isinstance(case_config, dict) and is_authoritative_config(case_config):
        if normalized_mode not in COMPETITION_MODES | {"developer", "test"}:
            return {
                "status": "FAIL",
                "control_decision": "BLOCKED",
                "reason": "MODE_UNSUPPORTED",
                "stage_changed": False,
            }
        return authorize_authoritative_transition(
            case_path,
            requested_stage=requested,
            executor=f"run_case:{normalized_mode}",
            declared_current_stage=current,
        )
    result: dict[str, Any] = {
        "case_id": _case_id(case_path),
        "current_stage": current,
        "authorized_next_stage": STAGE_TRANSITIONS.get(current),
        "requested_stage": requested,
        "required_gate": STAGE_REQUIRED_GATE.get(requested),
        "gate_status": "NOT_REQUIRED",
        "control_decision": "BLOCKED",
        "reason": None,
        "stage_changed": False,
        "solver_executed": False,
        "model_artifact_created": False,
        "paper_created": False,
        "final_submit_created": False,
    }
    supported_modes = COMPETITION_MODES | {"developer", "test"}
    if normalized_mode not in supported_modes:
        result["reason"] = "MODE_UNSUPPORTED"
        return result
    if not current or not requested:
        result["reason"] = "CURRENT_OR_REQUESTED_STAGE_MISSING"
        return result
    if result["authorized_next_stage"] != requested:
        result["reason"] = "AUTHORIZED_NEXT_STAGE_MISMATCH"
        return result
    required_gate = result["required_gate"]
    if required_gate is None:
        result["reason"] = "REQUESTED_STAGE_NOT_PROTECTED"
        return result
    gate = evaluate_human_gate(case_path, required_gate)
    result["gate_status"] = gate["gate_status"]
    result["gate_evaluation"] = gate
    if gate["gate_status"] != "PASS":
        result["reason"] = "REQUIRED_HUMAN_GATE_NOT_PASS"
        return result
    if requested in {"MODEL_IMPLEMENTATION", "SOLVER_EXECUTION"}:
        model_authority = verify_model_authority(case_path)
        result["model_authority"] = model_authority
        if model_authority.get("status") not in {"PASS", "NOT_REQUIRED"}:
            result["reason"] = model_authority.get("reason", "MODEL_AUTHORIZATION_INVALIDATED")
            return result
        if model_authority.get("status") == "PASS":
            result["model_authority_hash"] = model_authority.get("model_authority_hash")
            result["model_authority_approval_id"] = model_authority.get("human_approval_id")
            result["model_active_entrypoint"] = model_authority.get("model_active_entrypoint")
    result["control_decision"] = "ALLOWED"
    result["reason"] = "AUTHORIZED_TRANSITION"
    return result


def verify_authorized_stage_transition(path: str | Path, requested_stage: str) -> dict[str, Any]:
    """Verify the explicit authorization status before downstream execution."""

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"status": "FAIL", "reason": "AUTHORIZED_STAGE_TRANSITION_UNREADABLE"}
    requested = str(requested_stage or "").strip().upper()
    if isinstance(payload, dict) and payload.get("schema_version") == AUTHORIZATION_SCHEMA:
        if (
            payload.get("control_decision") != "ALLOWED"
            or normalize_stage(payload.get("authorized_next_stage")) != normalize_stage(requested)
            or normalize_stage(payload.get("requested_stage")) != normalize_stage(requested)
        ):
            return {"status": "FAIL", "reason": "AUTHORIZED_STAGE_TRANSITION_INVALID"}
        return {"status": "PASS", "reason": "AUTHORIZED_STAGE_TRANSITION_VALID", "payload": payload}
    if (
        not isinstance(payload, dict)
        or payload.get("control_decision") != "ALLOWED"
        or payload.get("gate_status") != "PASS"
        or payload.get("authorized_next_stage") != requested
        or payload.get("requested_stage") != requested
    ):
        return {"status": "FAIL", "reason": "AUTHORIZED_STAGE_TRANSITION_INVALID"}
    return {"status": "PASS", "reason": "AUTHORIZED_STAGE_TRANSITION_VALID", "payload": payload}


def verify_authorized_stage_transition_for_case(
    path: str | Path,
    *,
    case_path: str | Path,
    current_stage: str,
    requested_stage: str,
) -> dict[str, Any]:
    """Verify authorization identity and stage binding before a real action."""

    basic = verify_authorized_stage_transition(path, requested_stage)
    if basic.get("status") != "PASS":
        return basic
    payload = basic["payload"]
    case_root = Path(case_path).resolve()
    authorization_path = Path(path).resolve()
    try:
        authorization_path.relative_to(case_root)
    except ValueError:
        return {"status": "FAIL", "reason": "AUTHORIZATION_OUTSIDE_CASE"}
    if payload.get("schema_version") == AUTHORIZATION_SCHEMA:
        authoritative = verify_transition_authorization(case_root, authorization_path)
        if authoritative.get("status") != "PASS":
            return authoritative
        if normalize_stage(current_stage) != normalize_stage(payload.get("current_stage")):
            return {"status": "FAIL", "reason": "AUTHORIZATION_CURRENT_STAGE_MISMATCH"}
        if normalize_stage(requested_stage) != normalize_stage(payload.get("requested_stage")):
            return {"status": "FAIL", "reason": "AUTHORIZATION_STAGE_TRANSITION_MISMATCH"}
        authoritative["live_gate_evaluation"] = payload.get("gate_evaluation")
        return authoritative
    expected_case_id = _case_id(case_root)
    requested = str(requested_stage or "").strip().upper()
    current = str(current_stage or "").strip().upper()
    expected_next_stage = STAGE_TRANSITIONS.get(current)
    if expected_next_stage != requested:
        return {"status": "FAIL", "reason": "AUTHORIZATION_STAGE_TRANSITION_MISMATCH"}
    if not expected_case_id or payload.get("case_id") != expected_case_id:
        return {"status": "FAIL", "reason": "AUTHORIZATION_CASE_MISMATCH"}
    if payload.get("current_stage") != current:
        return {"status": "FAIL", "reason": "AUTHORIZATION_CURRENT_STAGE_MISMATCH"}
    if payload.get("required_gate") != STAGE_REQUIRED_GATE.get(requested):
        return {"status": "FAIL", "reason": "AUTHORIZATION_REQUIRED_GATE_MISMATCH"}
    required_gate = STAGE_REQUIRED_GATE.get(requested)
    live_gate = evaluate_human_gate(case_root, required_gate) if required_gate else None
    if not live_gate or live_gate.get("gate_status") != "PASS":
        return {
            "status": "FAIL",
            "reason": "LIVE_HUMAN_GATE_NOT_PASS",
            "gate_evaluation": live_gate or {"gate_id": required_gate, "gate_status": "FAIL"},
        }
    basic["live_gate_evaluation"] = live_gate
    if requested in {"MODEL_IMPLEMENTATION", "SOLVER_EXECUTION"}:
        model_authority = verify_model_authority(case_root, authorization_path=authorization_path)
        basic["model_authority"] = model_authority
        if model_authority.get("status") not in {"PASS", "NOT_REQUIRED"}:
            basic["reason"] = model_authority.get("reason", "MODEL_AUTHORIZATION_INVALIDATED")
            return basic
    return basic


def dispatch_competition_stage_action(
    case_path: str | Path,
    *,
    action: str,
    authorization_path: str | Path,
    current_stage: str,
    requested_stage: str,
    timeout_seconds: int = 60,
    production_runner: Callable[[Path, int], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Consume verified authorization and execute the competition action allowlist."""

    normalized_action = str(action or "").strip().upper()
    result: dict[str, Any] = {
        "action": normalized_action,
        "control_decision": "BLOCKED",
        "reason": None,
        "authorization_verified": False,
        "execution_marker_created": False,
        "production_execution": False,
        "production_status": None,
        "result_summary_path": None,
        "model_id": None,
    }
    if normalized_action not in COMPETITION_ACTION_REQUIRED_STAGE:
        result["reason"] = "COMPETITION_ACTION_NOT_ALLOWLISTED"
        return result
    if str(requested_stage or "").strip().upper() != COMPETITION_ACTION_REQUIRED_STAGE[normalized_action]:
        result["reason"] = "ACTION_STAGE_MISMATCH"
        return result
    verification = verify_authorized_stage_transition_for_case(
        authorization_path,
        case_path=case_path,
        current_stage=current_stage,
        requested_stage=requested_stage,
    )
    result["verification"] = verification
    if verification.get("status") != "PASS":
        result["reason"] = verification.get("reason", "AUTHORIZATION_VERIFICATION_FAILED")
        return result
    result["authorization_verified"] = True
    if normalized_action == "RUN_MODEL_SOLVER":
        model_config_path = Path(case_path) / "model_config.json"
        if not model_config_path.is_file():
            result["reason"] = "PRODUCTION_MODEL_CONFIG_MISSING"
            return result
        if production_runner is None:
            result["reason"] = "PRODUCTION_EXECUTOR_MISSING"
            return result
        try:
            run_result = production_runner(Path(case_path), int(timeout_seconds))
        except Exception as exc:
            result["reason"] = "PRODUCTION_MODEL_EXECUTION_FAILED"
            result["production_error"] = str(exc)
            return result
        if not isinstance(run_result, dict) or run_result.get("status") != "ok":
            result["reason"] = "PRODUCTION_MODEL_EXECUTION_FAILED"
            result["production_status"] = run_result.get("status") if isinstance(run_result, dict) else None
            return result

        summary_path = Path(case_path) / "output" / "result_summary.json"
        if not summary_path.is_file():
            result["reason"] = "PRODUCTION_RESULT_SUMMARY_MISSING"
            return result
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            result["reason"] = "PRODUCTION_RESULT_SUMMARY_INVALID"
            result["production_error"] = str(exc)
            return result
        required_fields = ("model_id", "status", "success", "primary_results")
        if (
            not isinstance(summary, dict)
            or any(field not in summary for field in required_fields)
            or summary.get("success") is not True
            or not isinstance(summary.get("primary_results"), dict)
            or not summary.get("primary_results")
        ):
            result["reason"] = "PRODUCTION_RESULT_SUMMARY_INVALID"
            return result
        result["production_execution"] = True
        result["production_status"] = summary.get("status")
        result["result_summary_path"] = str(summary_path)
        result["model_id"] = summary.get("model_id")
        result["control_decision"] = "ALLOWED"
        result["reason"] = "AUTHORIZED_PRODUCTION_MODEL_EXECUTION"
        return result

    marker = Path(case_path) / "execution_marker.txt"
    marker.write_text("TEST_MODEL_EXECUTION\n", encoding="utf-8")
    result["control_decision"] = "ALLOWED"
    result["reason"] = "AUTHORIZED_ALLOWLISTED_ACTION"
    result["execution_marker_created"] = True
    result["execution_marker"] = str(marker)
    return result
