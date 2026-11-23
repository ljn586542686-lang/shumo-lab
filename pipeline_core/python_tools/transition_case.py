"""Single authoritative state-transition kernel for formal case workflows.

Executors may create model, validation, literature, or paper artifacts, but
they cannot advance an authoritative case by themselves.  A transition uses
three steps: prepare an authorization, create a hash-bound completion receipt,
and commit it here.  Only the commit operation writes ``case_config.json`` and
the ``CURRENT_HANDOFF.md`` stage mirror.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT_FOR_IMPORT = Path(__file__).resolve().parents[2]
if str(ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(ROOT_FOR_IMPORT))


AUTHORIZATION_SCHEMA = "case_stage_transition_authorization_v1"
COMPLETION_SCHEMA = "case_stage_completion_receipt_v1"
TRANSITION_AUTHORITY = "transition_case_v1"
MIRROR_STATUS = "STATUS_MIRROR_CASE_CONFIG"
SUBMISSION_READY_SCHEMA = "submission_ready_without_package_v1"
SUBMISSION_READY_RECORD = "output/validation/submission_ready_record.json"
SUBMISSION_READY_BOUND_FILES = (
    "paper/main.pdf",
    "human_review/gate_4_final_submission.md",
    "output/validation/output_contract_report.md",
    "output/validation/evidence_trace_report.md",
    "output/validation/citation_audit_report.md",
    "ai_disclosure/AI工具使用详情.md",
)

STAGE_TRANSITIONS = {
    "CASE_INTAKE": "PROBLEM_UNDERSTANDING",
    "PROBLEM_UNDERSTANDING": "MODEL_ROUTE_GENERATION",
    "MODEL_ROUTE_GENERATION": "MODEL_IMPLEMENTATION",
    "MODEL_IMPLEMENTATION": "SOLVER_EXECUTION",
    "SOLVER_EXECUTION": "RESULT_VALIDATION",
    "RESULT_VALIDATION": "RESULT_ACCEPTANCE",
    "RESULT_ACCEPTANCE": "TRACK_A",
    "TRACK_A": "LITERATURE_GROUNDING",
    "LITERATURE_GROUNDING": "TRACK_B",
    "TRACK_B": "TRACK_C",
    "TRACK_C": "PAPER_RENDER",
    "PAPER_RENDER": "POST_WRITER_REENTRY",
    "POST_WRITER_REENTRY": "FINAL_REVIEW",
    "FINAL_REVIEW": "SUBMISSION_READY",
}

STAGE_ALIASES = {
    "INTAKE": "CASE_INTAKE",
    "MODEL_REVIEW": "MODEL_ROUTE_GENERATION",
    "MODEL_SELECTION": "MODEL_ROUTE_GENERATION",
    "PHASE_4_RESULT_VALIDATION_IN_PROGRESS": "RESULT_VALIDATION",
    "PHASE_4_RESULT_VALIDATION_READY_FOR_GATE3": "RESULT_ACCEPTANCE",
    "PAPER_GENERATION": "TRACK_C",
    "FINAL_FREEZE": "SUBMISSION_READY",
    "FINAL_SUBMISSION": "SUBMISSION_READY",
}

REQUIRED_GATE_BY_TARGET = {
    "MODEL_ROUTE_GENERATION": "GATE1",
    "MODEL_IMPLEMENTATION": "GATE2",
    "SOLVER_EXECUTION": "GATE2",
    "TRACK_A": "GATE3",
    "SUBMISSION_READY": "GATE4",
}

PUBLIC_STAGE_ALLOWED_CURRENT = {
    "init": {"CASE_INTAKE"},
    "problem": {"CASE_INTAKE", "PROBLEM_UNDERSTANDING"},
    "data": {"CASE_INTAKE", "PROBLEM_UNDERSTANDING"},
    "model": {"PROBLEM_UNDERSTANDING", "MODEL_ROUTE_GENERATION"},
    "validate": {"SOLVER_EXECUTION", "RESULT_VALIDATION", "RESULT_ACCEPTANCE"},
    "literature": {"TRACK_A", "LITERATURE_GROUNDING"},
    "paper": {"TRACK_C", "PAPER_RENDER"},
    "disclose": {"PAPER_RENDER", "POST_WRITER_REENTRY", "FINAL_REVIEW"},
    "freeze": {"FINAL_REVIEW", "SUBMISSION_READY"},
}

REQUIRED_ARTIFACT_GROUPS: dict[str, tuple[tuple[str, ...], ...]] = {
    "PROBLEM_UNDERSTANDING": (
        ("problem/problem_contract.md", "problem_contract.md"),
        ("problem_analysis.md",),
    ),
    "MODEL_ROUTE_GENERATION": (
        ("routing/problem_type_routing_result.json",),
        ("model_routes.md",),
        ("handoff/MODEL_ROUTE_GENERATION_HANDOFF.md",),
    ),
    "MODEL_IMPLEMENTATION": (("handoff/MODEL_BUILDING_HANDOFF.md",),),
    "SOLVER_EXECUTION": (
        ("output/result_summary.json",),
        ("output/result_summary.txt",),
    ),
    "RESULT_VALIDATION": (
        ("output/validation/output_contract_report.md",),
        ("output/validation/evidence_trace_report.md",),
    ),
    "RESULT_ACCEPTANCE": (
        ("output/result_summary.json",),
        ("output/validation/output_contract_report.md",),
        ("output/validation/evidence_trace_report.md",),
    ),
    "TRACK_A": (("output/validation/track_a_review.md",),),
    "LITERATURE_GROUNDING": (
        ("literature/method_support.md",),
        ("literature/citation_matrix.md",),
    ),
    "TRACK_C": (("handoff/TRACK_B_TO_TRACK_C.md",),),
    "PAPER_RENDER": (
        ("paper/main.pdf",),
        ("paper/formal_paper_production_trace.json",),
        ("paper/visual_reasoning_trace_report.json",),
        ("paper/pdf_check/manual_visual_review.json",),
    ),
    "FINAL_REVIEW": (
        ("output/validation/citation_audit_report.md",),
        ("output/validation/final_submit_checklist.md",),
        ("ai_disclosure/AI工具使用详情.md",),
    ),
}

ALLOWED_STATE_UPDATES_BY_TARGET = {
    "LITERATURE_GROUNDING": {
        "literature_status",
        "formal_literature_search_status",
        "citation_ready",
        "formal_reference_allowed",
    },
    "TRACK_B": {"track_b_status"},
    "TRACK_C": {"track_c_status"},
    "PAPER_RENDER": {"paper_status"},
    "POST_WRITER_REENTRY": {"post_writer_reentry_status"},
    "FINAL_REVIEW": {"final_review_status"},
}


class TransitionError(RuntimeError):
    """A machine-readable fail-closed transition error."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TransitionError(f"JSON_UNREADABLE:{path.as_posix()}") from exc
    if not isinstance(payload, dict):
        raise TransitionError(f"JSON_OBJECT_REQUIRED:{path.as_posix()}")
    return payload


def _inside(case_root: Path, path: str | Path, reason: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = case_root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(case_root.resolve())
    except ValueError as exc:
        raise TransitionError(reason) from exc
    return resolved


def _relative(case_root: Path, path: Path) -> str:
    return path.resolve().relative_to(case_root.resolve()).as_posix()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.transition-tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def normalize_stage(stage: str | None) -> str:
    token = str(stage or "").strip().upper()
    return STAGE_ALIASES.get(token, token)


def _field(text: str, name: str) -> str:
    match = re.search(
        rf"(?im)^[ \t]*(?:-[ \t]*)?`?{re.escape(name)}`?[ \t]*[:=][ \t]*`?([^`\r\n]+)`?[ \t]*$",
        text,
    )
    return match.group(1).strip() if match else ""


def is_authoritative_config(config: dict[str, Any]) -> bool:
    return str(config.get("state_authority") or "").strip() == "case_config.json"


def validate_authoritative_state(case_dir: str | Path, config: dict[str, Any] | None = None) -> dict[str, Any]:
    root = Path(case_dir)
    config_path = root / "case_config.json"
    try:
        payload = config if isinstance(config, dict) else _read_json(config_path)
    except TransitionError as exc:
        return {"status": "FAIL", "reason": str(exc)}
    if not is_authoritative_config(payload):
        return {"status": "NOT_REQUIRED", "authority": None}
    if payload.get("transition_authority") not in {None, TRANSITION_AUTHORITY}:
        return {
            "status": "FAIL",
            "reason": "TRANSITION_AUTHORITY_UNSUPPORTED",
            "transition_authority": payload.get("transition_authority"),
        }
    raw_stage = str(payload.get("current_stage") or "").strip().upper()
    current_stage = normalize_stage(raw_stage)
    if current_stage not in STAGE_TRANSITIONS and current_stage != "SUBMISSION_READY":
        return {
            "status": "FAIL",
            "reason": "CURRENT_STAGE_UNSUPPORTED",
            "current_stage": raw_stage or None,
        }
    mirror_path = root / str(payload.get("current_handoff_path") or "CURRENT_HANDOFF.md")
    if not mirror_path.is_file():
        return {"status": "FAIL", "reason": "CURRENT_HANDOFF_MISSING", "path": mirror_path.as_posix()}
    try:
        mirror_text = mirror_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return {"status": "FAIL", "reason": "CURRENT_HANDOFF_UNREADABLE", "path": mirror_path.as_posix()}
    if _field(mirror_text, "CURRENT_HANDOFF_STATUS") != MIRROR_STATUS:
        return {"status": "FAIL", "reason": "CURRENT_HANDOFF_NOT_A_CONFIG_MIRROR"}
    case_id = str(payload.get("case_id") or root.name).strip()
    if _field(mirror_text, "CASE_ID") != case_id:
        return {"status": "FAIL", "reason": "CURRENT_HANDOFF_CASE_ID_MISMATCH"}
    if _field(mirror_text, "STATE_AUTHORITY") != "case_config.json":
        return {"status": "FAIL", "reason": "CURRENT_HANDOFF_STATE_AUTHORITY_MISMATCH"}
    if _field(mirror_text, "TRANSITION_AUTHORITY") != TRANSITION_AUTHORITY:
        return {"status": "FAIL", "reason": "CURRENT_HANDOFF_TRANSITION_AUTHORITY_MISMATCH"}
    handoff_raw = _field(mirror_text, "CURRENT_STAGE").upper()
    if not raw_stage or handoff_raw != raw_stage:
        return {
            "status": "FAIL",
            "reason": "CURRENT_STAGE_STATE_MISMATCH",
            "case_config_stage": raw_stage or None,
            "current_handoff_stage": handoff_raw or None,
        }
    revision = payload.get("state_revision", 0)
    if not isinstance(revision, int) or revision < 0:
        return {"status": "FAIL", "reason": "STATE_REVISION_INVALID"}
    mirror_revision = _field(mirror_text, "STATE_REVISION")
    if mirror_revision != str(revision):
        return {
            "status": "FAIL",
            "reason": "CURRENT_HANDOFF_STATE_REVISION_MISMATCH",
            "current_handoff_state_revision": mirror_revision or None,
            "case_config_state_revision": revision,
        }
    expected_next = str(
        payload.get("next_allowed_stage") or STAGE_TRANSITIONS.get(current_stage) or "NONE"
    ).strip().upper()
    handoff_next = _field(mirror_text, "NEXT_ALLOWED_STAGE").upper()
    if handoff_next != expected_next:
        return {
            "status": "FAIL",
            "reason": "CURRENT_HANDOFF_NEXT_STAGE_MISMATCH",
            "current_handoff_next_stage": handoff_next or None,
            "case_config_next_stage": expected_next,
        }
    expected_transition_id = str(payload.get("last_transition_id") or "NONE").strip()
    handoff_transition_id = _field(mirror_text, "LAST_TRANSITION_ID")
    if handoff_transition_id != expected_transition_id:
        return {
            "status": "FAIL",
            "reason": "CURRENT_HANDOFF_LAST_TRANSITION_ID_MISMATCH",
            "current_handoff_last_transition_id": handoff_transition_id or None,
            "case_config_last_transition_id": expected_transition_id,
        }
    return {
        "status": "PASS",
        "authority": "case_config.json",
        "transition_authority": TRANSITION_AUTHORITY,
        "current_stage": raw_stage,
        "canonical_current_stage": current_stage,
        "state_revision": revision,
        "config_path": config_path.as_posix(),
        "config_sha256": (
            _sha256(config_path)
            if config_path.is_file()
            else _sha256_bytes(_canonical_json(payload).encode("utf-8"))
        ),
        "mirror_path": mirror_path.as_posix(),
        "mirror_sha256": _sha256(mirror_path),
    }


def preflight_public_stage(stage: str, state: dict[str, Any]) -> dict[str, Any]:
    if state.get("status") != "PASS":
        return {"status": "NOT_REQUIRED"}
    requested = str(stage or "").strip().lower()
    current = normalize_stage(str(state.get("canonical_current_stage") or state.get("current_stage") or ""))
    allowed = PUBLIC_STAGE_ALLOWED_CURRENT.get(requested)
    if allowed is None:
        return {"status": "FAIL", "reason": "PUBLIC_STAGE_UNSUPPORTED", "requested_stage": requested}
    if current not in allowed:
        return {
            "status": "FAIL",
            "reason": (
                "PUBLIC_MODEL_STAGE_NOT_ALLOWED_BY_CURRENT_STATE"
                if requested == "model"
                else "PUBLIC_STAGE_NOT_ALLOWED_BY_CURRENT_STATE"
            ),
            "current_stage": current,
            "requested_stage": requested,
            "allowed_current_stages": sorted(allowed),
        }
    return {"status": "PASS", "current_stage": current, "requested_stage": requested}


def _gate_evaluation(case_root: Path, gate_id: str | None) -> dict[str, Any] | None:
    if gate_id is None:
        return None
    from pipeline_core.python_tools.human_gate import evaluate_human_gate

    return evaluate_human_gate(case_root, gate_id)


def authorize_transition(
    case_dir: str | Path,
    *,
    requested_stage: str,
    executor: str,
    declared_current_stage: str | None = None,
) -> dict[str, Any]:
    root = Path(case_dir).resolve()
    try:
        config = _read_json(root / "case_config.json")
    except TransitionError as exc:
        return {"status": "FAIL", "control_decision": "BLOCKED", "reason": str(exc)}
    state = validate_authoritative_state(root, config)
    if state.get("status") != "PASS":
        return {"status": "FAIL", "control_decision": "BLOCKED", "reason": state.get("reason"), "state": state}
    current = state["canonical_current_stage"]
    requested = normalize_stage(requested_stage)
    if declared_current_stage and normalize_stage(declared_current_stage) != current:
        return {
            "status": "FAIL",
            "control_decision": "BLOCKED",
            "reason": "DECLARED_CURRENT_STAGE_MISMATCH",
            "declared_current_stage": normalize_stage(declared_current_stage),
            "authoritative_current_stage": current,
        }
    expected = STAGE_TRANSITIONS.get(current)
    if requested != expected:
        return {
            "status": "FAIL",
            "control_decision": "BLOCKED",
            "reason": "AUTHORIZED_NEXT_STAGE_MISMATCH",
            "current_stage": current,
            "authorized_next_stage": expected,
            "requested_stage": requested,
        }
    executor_id = str(executor or "").strip()
    if not executor_id:
        return {"status": "FAIL", "control_decision": "BLOCKED", "reason": "EXECUTOR_ID_REQUIRED"}
    required_gate = REQUIRED_GATE_BY_TARGET.get(requested)
    gate = _gate_evaluation(root, required_gate)
    if gate is not None and gate.get("gate_status") != "PASS":
        return {
            "status": "FAIL",
            "control_decision": "BLOCKED",
            "reason": "REQUIRED_HUMAN_GATE_NOT_PASS",
            "required_gate": required_gate,
            "gate_evaluation": gate,
        }
    payload: dict[str, Any] = {
        "schema_version": AUTHORIZATION_SCHEMA,
        "case_id": str(config.get("case_id") or root.name),
        "current_stage": current,
        "source_stage_raw": state["current_stage"],
        "authorized_next_stage": requested,
        "requested_stage": requested,
        "required_gate": required_gate,
        "gate_status": gate.get("gate_status") if gate else "NOT_REQUIRED",
        "gate_evaluation": gate,
        "executor": executor_id,
        "state_revision": state["state_revision"],
        "case_config_sha256": state["config_sha256"],
        "current_handoff_sha256": state["mirror_sha256"],
        "control_decision": "ALLOWED",
        "reason": "AUTHORIZED_TRANSITION",
        "stage_changed": False,
        "solver_executed": False,
        "model_artifact_created": False,
        "paper_created": False,
        "final_submit_created": False,
        "issued_at": _now(),
    }
    if requested in {"MODEL_IMPLEMENTATION", "SOLVER_EXECUTION"}:
        from pipeline_core.python_tools.model_authority import verify_model_authority

        model_authority = verify_model_authority(root)
        payload["model_authority"] = model_authority
        if model_authority.get("status") not in {"PASS", "NOT_REQUIRED"}:
            return {
                "status": "FAIL",
                "control_decision": "BLOCKED",
                "reason": model_authority.get("reason", "MODEL_AUTHORIZATION_INVALIDATED"),
                "model_authority": model_authority,
            }
        if model_authority.get("status") == "PASS":
            payload["model_authority_hash"] = model_authority.get("model_authority_hash")
            payload["model_authority_approval_id"] = model_authority.get("human_approval_id")
            payload["model_active_entrypoint"] = model_authority.get("model_active_entrypoint")
    authorization_base = dict(payload)
    payload["authorization_id"] = _sha256_bytes(_canonical_json(authorization_base).encode("utf-8"))
    payload["status"] = "PASS"
    return payload


def write_transition_authorization(case_dir: str | Path, payload: dict[str, Any]) -> Path:
    if payload.get("schema_version") != AUTHORIZATION_SCHEMA or payload.get("control_decision") != "ALLOWED":
        raise TransitionError("AUTHORIZATION_NOT_WRITABLE")
    root = Path(case_dir).resolve()
    identifier = str(payload.get("authorization_id") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", identifier):
        raise TransitionError("AUTHORIZATION_ID_INVALID")
    path = root / "handoff" / "stage_transitions" / f"{identifier}.authorization.json"
    if path.exists():
        existing = _read_json(path)
        if existing != payload:
            raise TransitionError("AUTHORIZATION_ID_COLLISION")
        return path
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def verify_transition_authorization(case_dir: str | Path, authorization_path: str | Path) -> dict[str, Any]:
    root = Path(case_dir).resolve()
    try:
        path = _inside(root, authorization_path, "AUTHORIZATION_OUTSIDE_CASE")
        payload = _read_json(path)
    except TransitionError as exc:
        return {"status": "FAIL", "reason": str(exc)}
    if payload.get("schema_version") != AUTHORIZATION_SCHEMA:
        return {"status": "FAIL", "reason": "AUTHORIZED_STAGE_TRANSITION_INVALID"}
    identifier = payload.get("authorization_id")
    base = {key: value for key, value in payload.items() if key not in {"authorization_id", "status"}}
    if identifier != _sha256_bytes(_canonical_json(base).encode("utf-8")):
        return {"status": "FAIL", "reason": "AUTHORIZATION_ID_MISMATCH"}
    state = validate_authoritative_state(root)
    if state.get("status") != "PASS":
        return {"status": "FAIL", "reason": state.get("reason"), "state": state}
    if normalize_stage(payload.get("current_stage")) != state["canonical_current_stage"]:
        return {"status": "FAIL", "reason": "AUTHORIZATION_CURRENT_STAGE_MISMATCH"}
    if payload.get("state_revision") != state["state_revision"]:
        return {"status": "FAIL", "reason": "AUTHORIZATION_STATE_REVISION_MISMATCH"}
    if payload.get("case_config_sha256") != state["config_sha256"]:
        return {"status": "FAIL", "reason": "AUTHORIZATION_CASE_CONFIG_HASH_MISMATCH"}
    if payload.get("current_handoff_sha256") != state["mirror_sha256"]:
        return {"status": "FAIL", "reason": "AUTHORIZATION_HANDOFF_HASH_MISMATCH"}
    if normalize_stage(payload.get("authorized_next_stage")) != STAGE_TRANSITIONS.get(state["canonical_current_stage"]):
        return {"status": "FAIL", "reason": "AUTHORIZATION_STAGE_TRANSITION_MISMATCH"}
    required_gate = REQUIRED_GATE_BY_TARGET.get(normalize_stage(payload.get("authorized_next_stage")))
    if payload.get("required_gate") != required_gate:
        return {"status": "FAIL", "reason": "AUTHORIZATION_REQUIRED_GATE_MISMATCH"}
    gate = _gate_evaluation(root, required_gate)
    if gate is not None and gate.get("gate_status") != "PASS":
        return {"status": "FAIL", "reason": "LIVE_HUMAN_GATE_NOT_PASS", "gate_evaluation": gate}
    return {"status": "PASS", "reason": "AUTHORIZED_STAGE_TRANSITION_VALID", "payload": payload, "path": path}


def _artifact_bindings(case_root: Path, artifacts: Iterable[str | Path | tuple[str, str | Path]]) -> list[dict[str, str]]:
    bindings: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in artifacts:
        role = "evidence"
        raw_path: str | Path
        if isinstance(item, tuple):
            role, raw_path = item
        else:
            raw_path = item
        path = _inside(case_root, raw_path, "COMPLETION_ARTIFACT_OUTSIDE_CASE")
        if not path.is_file():
            raise TransitionError(f"COMPLETION_ARTIFACT_MISSING:{_relative(case_root, path)}")
        relative = _relative(case_root, path)
        if relative in seen:
            continue
        seen.add(relative)
        bindings.append({"role": str(role), "path": relative, "sha256": _sha256(path)})
    return bindings


def build_completion_receipt(
    case_dir: str | Path,
    *,
    authorization_path: str | Path,
    artifacts: Iterable[str | Path | tuple[str, str | Path]],
    executor_status: str,
    state_updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(case_dir).resolve()
    verified = verify_transition_authorization(root, authorization_path)
    if verified.get("status") != "PASS":
        return {"status": "FAIL", "reason": verified.get("reason"), "authorization": verified}
    status = str(executor_status or "").strip().upper()
    if status != "PASS":
        return {"status": "FAIL", "reason": "EXECUTOR_STATUS_NOT_PASS", "executor_status": status}
    try:
        bindings = _artifact_bindings(root, artifacts)
    except TransitionError as exc:
        return {"status": "FAIL", "reason": str(exc)}
    if not bindings:
        return {"status": "FAIL", "reason": "COMPLETION_ARTIFACT_BINDINGS_REQUIRED"}
    auth = verified["payload"]
    auth_path = verified["path"]
    return {
        "schema_version": COMPLETION_SCHEMA,
        "status": "PASS",
        "case_id": auth["case_id"],
        "authorization_id": auth["authorization_id"],
        "authorization_path": _relative(root, auth_path),
        "authorization_sha256": _sha256(auth_path),
        "source_stage": auth["current_stage"],
        "target_stage": auth["authorized_next_stage"],
        "executor": auth["executor"],
        "executor_status": status,
        "artifact_bindings": bindings,
        "state_updates": dict(state_updates or {}),
        "completed_at": _now(),
    }


def write_completion_receipt(case_dir: str | Path, payload: dict[str, Any]) -> Path:
    if payload.get("schema_version") != COMPLETION_SCHEMA or payload.get("status") != "PASS":
        raise TransitionError("COMPLETION_RECEIPT_NOT_WRITABLE")
    root = Path(case_dir).resolve()
    identifier = str(payload.get("authorization_id") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", identifier):
        raise TransitionError("AUTHORIZATION_ID_INVALID")
    path = root / "handoff" / "stage_transitions" / f"{identifier}.completion.json"
    if path.exists():
        existing = _read_json(path)
        if existing != payload:
            raise TransitionError("COMPLETION_RECEIPT_ALREADY_EXISTS")
        return path
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def _required_artifact_groups(config: dict[str, Any], target: str) -> tuple[tuple[str, ...], ...]:
    groups = list(REQUIRED_ARTIFACT_GROUPS.get(target, ()))
    if target == "PAPER_RENDER" and config.get("argument_blueprint_required") is True:
        groups.append(("paper/argument_blueprint_trace_report.json",))
    if target == "TRACK_A":
        groups = [(str(config.get("track_a_review_path") or "output/validation/track_a_review.md"),)]
    elif target == "TRACK_B":
        groups = [(str(config.get("track_b_review_path") or "paper/track_b/00_TRACK_B_REVIEW.md"),)]
    elif target == "POST_WRITER_REENTRY":
        configured = str(config.get("post_writer_reentry_path") or "").strip()
        if configured:
            groups = [(configured,)]
    elif target == "SUBMISSION_READY":
        package_authorized = bool(config.get("final_submit_allowed")) or str(
            config.get("official_submission_mode") or "locked"
        ).strip().lower() != "locked"
        groups = [("final_submit/",)] if package_authorized else [(SUBMISSION_READY_RECORD,)]
    return tuple(groups)


def _validate_submission_ready_record(case_root: Path, config: dict[str, Any], bound_paths: set[str]) -> dict[str, Any]:
    """Validate Gate 4 closure without pretending a submission package exists."""

    if SUBMISSION_READY_RECORD not in bound_paths:
        return {"status": "FAIL", "reason": "SUBMISSION_READY_RECORD_NOT_BOUND"}
    if (case_root / "final_submit").exists():
        return {"status": "FAIL", "reason": "UNAUTHORIZED_FINAL_SUBMIT_PRESENT"}
    if bool(config.get("final_submit_allowed")):
        return {"status": "FAIL", "reason": "SUBMISSION_READY_RECORD_CONFLICTS_WITH_PACKAGE_AUTHORIZATION"}
    if str(config.get("official_submission_mode") or "locked").strip().lower() != "locked":
        return {"status": "FAIL", "reason": "SUBMISSION_READY_RECORD_REQUIRES_LOCKED_OFFICIAL_MODE"}
    try:
        record = _read_json(case_root / SUBMISSION_READY_RECORD)
    except TransitionError as exc:
        return {"status": "FAIL", "reason": str(exc)}
    if record.get("schema_version") != SUBMISSION_READY_SCHEMA:
        return {"status": "FAIL", "reason": "SUBMISSION_READY_RECORD_SCHEMA_INVALID"}
    if str(record.get("status") or "").strip().upper() != "READY_WITHOUT_PACKAGE":
        return {"status": "FAIL", "reason": "SUBMISSION_READY_RECORD_STATUS_INVALID"}
    required_flags = {
        "gate4_approved": True,
        "final_submit_created": False,
        "package_creation_authorized": False,
        "official_submission_mode_locked": True,
    }
    for key, expected in required_flags.items():
        if record.get(key) is not expected:
            return {"status": "FAIL", "reason": f"SUBMISSION_READY_RECORD_FLAG_INVALID:{key}"}
    bindings = record.get("artifact_sha256")
    if not isinstance(bindings, dict):
        return {"status": "FAIL", "reason": "SUBMISSION_READY_RECORD_HASHES_REQUIRED"}
    for relative in SUBMISSION_READY_BOUND_FILES:
        path = case_root / relative
        if not path.is_file():
            return {"status": "FAIL", "reason": f"SUBMISSION_READY_SOURCE_MISSING:{relative}"}
        if bindings.get(relative) != _sha256(path):
            return {"status": "FAIL", "reason": f"SUBMISSION_READY_SOURCE_HASH_MISMATCH:{relative}"}
    return {"status": "PASS", "record_path": SUBMISSION_READY_RECORD}


def _validate_receipt_artifacts(case_root: Path, config: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    bindings = receipt.get("artifact_bindings")
    if not isinstance(bindings, list) or not bindings:
        return {"status": "FAIL", "reason": "COMPLETION_ARTIFACT_BINDINGS_REQUIRED"}
    bound_paths: set[str] = set()
    roles: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, dict):
            return {"status": "FAIL", "reason": "COMPLETION_ARTIFACT_BINDING_INVALID"}
        try:
            path = _inside(case_root, str(binding.get("path") or ""), "COMPLETION_ARTIFACT_OUTSIDE_CASE")
        except TransitionError as exc:
            return {"status": "FAIL", "reason": str(exc)}
        if not path.is_file() or binding.get("sha256") != _sha256(path):
            return {"status": "FAIL", "reason": "COMPLETION_ARTIFACT_HASH_MISMATCH", "path": binding.get("path")}
        bound_paths.add(_relative(case_root, path))
        roles.add(str(binding.get("role") or ""))
    target = normalize_stage(receipt.get("target_stage"))
    for alternatives in _required_artifact_groups(config, target):
        satisfied = False
        for relative in alternatives:
            candidate = case_root / relative
            if relative.endswith("/"):
                if candidate.is_dir() and any(item.is_file() for item in candidate.rglob("*")):
                    satisfied = True
                    break
            elif relative in bound_paths and candidate.is_file():
                satisfied = True
                break
        if not satisfied:
            return {
                "status": "FAIL",
                "reason": "REQUIRED_STAGE_ARTIFACT_NOT_BOUND",
                "target_stage": target,
                "alternatives": list(alternatives),
            }
    if target == "POST_WRITER_REENTRY" and not _required_artifact_groups(config, target):
        if "post_writer_reentry" not in roles:
            return {"status": "FAIL", "reason": "POST_WRITER_REENTRY_BINDING_REQUIRED"}
    if target == "TRACK_B":
        review = next((case_root / path for path in bound_paths if path.endswith("00_TRACK_B_REVIEW.md")), None)
        if review is None:
            return {"status": "FAIL", "reason": "TRACK_B_REVIEW_BINDING_REQUIRED"}
        text = review.read_text(encoding="utf-8-sig")
        if not re.search(r"(?im)^\s*TRACK_B_(?:REVIEW_VERDICT|DISPOSITION)\s*=\s*PASS(?:_WITH_LIMITATIONS)?\s*$", text):
            return {"status": "FAIL", "reason": "TRACK_B_REVIEW_NOT_PASSED"}
    if target == "TRACK_C":
        authorization = case_root / "handoff" / "TRACK_B_TO_TRACK_C.md"
        text = authorization.read_text(encoding="utf-8-sig") if authorization.is_file() else ""
        if not re.search(r"(?im)^\s*TRACK_C_EXECUTION_AUTHORIZATION\s*=\s*YES\s*$", text):
            return {"status": "FAIL", "reason": "TRACK_C_EXECUTION_NOT_AUTHORIZED"}
    if target == "PAPER_RENDER":
        from pipeline_core.paper_quality.pdf_layout_checks import validate_manual_visual_review
        from pipeline_core.paper_quality.visual_reasoning_contract import validate_visual_reasoning_trace_report
        from pipeline_core.paper_quality.argument_blueprint_contract import validate_argument_blueprint_trace_report

        review_path = case_root / "paper" / "pdf_check" / "manual_visual_review.json"
        try:
            review = _read_json(review_path)
            visual = validate_manual_visual_review(review, case_root / "paper" / "main.pdf")
        except (TransitionError, OSError, ValueError) as exc:
            return {"status": "FAIL", "reason": f"PDF_VISUAL_REVIEW_INVALID:{exc}"}
        if visual.get("status") != "PASS":
            return {"status": "FAIL", "reason": visual.get("reason", "PDF_VISUAL_REVIEW_INVALID")}
        reasoning_path = case_root / "paper" / "visual_reasoning_trace_report.json"
        try:
            reasoning = _read_json(reasoning_path)
        except (TransitionError, OSError, ValueError) as exc:
            return {"status": "FAIL", "reason": f"VISUAL_REASONING_TRACE_INVALID:{exc}"}
        reasoning_check = validate_visual_reasoning_trace_report(reasoning, case_root / "paper")
        if reasoning_check.get("status") != "PASS":
            return {"status": "FAIL", "reason": reasoning_check.get("reason", "VISUAL_REASONING_TRACE_NOT_PASSED")}
        if config.get("argument_blueprint_required") is True:
            blueprint_path = case_root / "paper" / "argument_blueprint_trace_report.json"
            try:
                blueprint = _read_json(blueprint_path)
            except (TransitionError, OSError, ValueError) as exc:
                return {"status": "FAIL", "reason": f"ARGUMENT_BLUEPRINT_TRACE_INVALID:{exc}"}
            blueprint_check = validate_argument_blueprint_trace_report(blueprint, case_root / "paper")
            if blueprint_check.get("status") != "PASS":
                return {
                    "status": "FAIL",
                    "reason": blueprint_check.get("reason", "ARGUMENT_BLUEPRINT_TRACE_NOT_PASSED"),
                }
    if target == "SUBMISSION_READY" and SUBMISSION_READY_RECORD in bound_paths:
        readiness = _validate_submission_ready_record(case_root, config, bound_paths)
        if readiness.get("status") != "PASS":
            return readiness
    return {"status": "PASS", "bound_paths": sorted(bound_paths)}


def _render_handoff_state(config: dict[str, Any]) -> str:
    """Render every mutable mirror field from authoritative config.

    The handoff is intentionally a compact state mirror. Transition history
    belongs to ``handoff/stage_transition_log.jsonl`` and stage-specific facts
    belong to their review reports. Re-rendering the whole mirror prevents
    stale prose such as ``TRACK_A_NOT_STARTED=true`` from surviving a commit.
    """

    case_id = str(config.get("case_id") or "UNKNOWN_CASE").strip()
    current_stage = str(config.get("current_stage") or "").strip().upper()
    canonical_current = normalize_stage(current_stage)
    next_stage = str(
        config.get("next_allowed_stage") or STAGE_TRANSITIONS.get(canonical_current) or "NONE"
    ).strip().upper()
    revision = config.get("state_revision", 0)
    transition_id = str(config.get("last_transition_id") or "NONE").strip()
    return (
        f"# {case_id} Current Handoff\n\n"
        f"`CURRENT_HANDOFF_STATUS={MIRROR_STATUS}`\n\n"
        f"- `CASE_ID`: `{case_id}`\n"
        f"- `CURRENT_STAGE`: `{current_stage}`\n"
        "- `STATE_AUTHORITY`: `case_config.json`\n"
        f"- `TRANSITION_AUTHORITY`: `{TRANSITION_AUTHORITY}`\n"
        f"- `STATE_REVISION`: `{revision}`\n"
        f"- `NEXT_ALLOWED_STAGE`: `{next_stage}`\n"
        f"- `LAST_TRANSITION_ID`: `{transition_id}`\n\n"
        "本文件只镜像 `case_config.json` 的当前机器状态，不保存阶段叙事或历史状态。\n"
        "转换历史见 `handoff/stage_transition_log.jsonl`；阶段产物与限制见对应审查报告。\n"
        "任何阶段推进必须由 `pipeline_core/python_tools/transition_case.py` 提交。\n"
    )


def _restore_text(path: Path, original: str | None) -> None:
    if original is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    _atomic_write(path, original)


def _commit_state_bundle(
    *,
    case_root: Path,
    config_path: Path,
    config_text: str,
    mirror_path: Path,
    mirror_text: str,
    journal_path: Path,
    journal_record: dict[str, Any],
) -> None:
    """Write the authoritative state bundle fail-closed with rollback.

    The mirror is written before the authoritative config. A hard process stop
    between those writes therefore cannot advance the authoritative state. For
    ordinary I/O failures, every touched file is restored to its exact prior
    contents.
    """

    originals = {
        config_path: config_path.read_text(encoding="utf-8-sig"),
        mirror_path: mirror_path.read_text(encoding="utf-8-sig"),
        journal_path: journal_path.read_text(encoding="utf-8-sig") if journal_path.is_file() else None,
    }
    old_journal = originals[journal_path] or ""
    journal_text = old_journal + _canonical_json(journal_record) + "\n"
    try:
        _atomic_write(mirror_path, mirror_text)
        _atomic_write(config_path, config_text)
        _atomic_write(journal_path, journal_text)
        final_state = validate_authoritative_state(case_root)
        if final_state.get("status") != "PASS":
            raise TransitionError("POST_COMMIT_STATE_INVALID:" + str(final_state.get("reason") or "UNKNOWN"))
    except Exception as exc:
        rollback_errors: list[str] = []
        for path in (journal_path, config_path, mirror_path):
            try:
                _restore_text(path, originals[path])
            except Exception as rollback_exc:  # pragma: no cover - catastrophic filesystem failure
                rollback_errors.append(f"{path.name}:{type(rollback_exc).__name__}")
        detail = type(exc).__name__
        if rollback_errors:
            detail += ";ROLLBACK_FAILED=" + ",".join(rollback_errors)
        raise TransitionError("STATE_BUNDLE_WRITE_FAILED:" + detail) from exc


def _allowed_state_updates(target: str, updates: Any) -> tuple[dict[str, Any] | None, str | None]:
    if updates is None:
        return {}, None
    if not isinstance(updates, dict):
        return None, "STATE_UPDATES_INVALID"
    allowed = ALLOWED_STATE_UPDATES_BY_TARGET.get(target, set())
    forbidden = sorted(set(updates) - allowed)
    if forbidden:
        return None, "STATE_UPDATE_NOT_ALLOWED:" + ",".join(forbidden)
    if target == "LITERATURE_GROUNDING":
        literature_status = str(updates.get("literature_status") or "").strip().upper()
        if literature_status not in {"VERIFIED", "NOT_REQUIRED"}:
            return None, "LITERATURE_STATUS_NOT_CLOSED"
    return dict(updates), None


def commit_transition(
    case_dir: str | Path,
    *,
    authorization_path: str | Path,
    completion_receipt_path: str | Path,
) -> dict[str, Any]:
    root = Path(case_dir).resolve()
    lock_path = root / "handoff" / ".stage_transition.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return {"status": "FAIL", "reason": "STAGE_TRANSITION_LOCKED"}
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode("utf-8"))
        os.close(descriptor)
        verified = verify_transition_authorization(root, authorization_path)
        if verified.get("status") != "PASS":
            return {"status": "FAIL", "reason": verified.get("reason"), "authorization": verified}
        try:
            receipt_path = _inside(root, completion_receipt_path, "COMPLETION_RECEIPT_OUTSIDE_CASE")
            receipt = _read_json(receipt_path)
        except TransitionError as exc:
            return {"status": "FAIL", "reason": str(exc)}
        auth = verified["payload"]
        auth_path = verified["path"]
        if receipt.get("schema_version") != COMPLETION_SCHEMA or receipt.get("status") != "PASS":
            return {"status": "FAIL", "reason": "COMPLETION_RECEIPT_INVALID"}
        if receipt.get("authorization_id") != auth.get("authorization_id"):
            return {"status": "FAIL", "reason": "COMPLETION_AUTHORIZATION_ID_MISMATCH"}
        if receipt.get("authorization_sha256") != _sha256(auth_path):
            return {"status": "FAIL", "reason": "COMPLETION_AUTHORIZATION_HASH_MISMATCH"}
        if normalize_stage(receipt.get("source_stage")) != normalize_stage(auth.get("current_stage")):
            return {"status": "FAIL", "reason": "COMPLETION_SOURCE_STAGE_MISMATCH"}
        target = normalize_stage(auth.get("authorized_next_stage"))
        if normalize_stage(receipt.get("target_stage")) != target:
            return {"status": "FAIL", "reason": "COMPLETION_TARGET_STAGE_MISMATCH"}
        if receipt.get("executor") != auth.get("executor") or receipt.get("executor_status") != "PASS":
            return {"status": "FAIL", "reason": "COMPLETION_EXECUTOR_MISMATCH"}
        config = _read_json(root / "case_config.json")
        artifact_check = _validate_receipt_artifacts(root, config, receipt)
        if artifact_check.get("status") != "PASS":
            return artifact_check
        updates, update_error = _allowed_state_updates(target, receipt.get("state_updates"))
        if update_error:
            return {"status": "FAIL", "reason": update_error}
        mirror_path = root / str(config.get("current_handoff_path") or "CURRENT_HANDOFF.md")
        config.update(updates or {})
        if target == "SUBMISSION_READY":
            config["human_gate_status"] = "APPROVED"
            if SUBMISSION_READY_RECORD in artifact_check.get("bound_paths", []):
                config["submission_package_status"] = "NOT_CREATED_BY_USER_BOUNDARY"
        config["transition_authority"] = TRANSITION_AUTHORITY
        config["previous_stage"] = str(config.get("current_stage") or "")
        config["current_stage"] = target
        config["status"] = target
        config["next_allowed_stage"] = STAGE_TRANSITIONS.get(target)
        config["state_revision"] = int(config.get("state_revision", 0)) + 1
        config["last_transition_id"] = auth["authorization_id"]
        config["last_transition_at"] = _now()
        journal = root / "handoff" / "stage_transition_log.jsonl"
        record = {
            "authorization_id": auth["authorization_id"],
            "case_id": auth["case_id"],
            "source_stage": auth["current_stage"],
            "target_stage": target,
            "executor": auth["executor"],
            "completion_receipt": _relative(root, receipt_path),
            "committed_at": config["last_transition_at"],
            "state_revision": config["state_revision"],
        }
        try:
            mirror_text = _render_handoff_state(config)
            _commit_state_bundle(
                case_root=root,
                config_path=root / "case_config.json",
                config_text=json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                mirror_path=mirror_path,
                mirror_text=mirror_text,
                journal_path=journal,
                journal_record=record,
            )
        except (OSError, UnicodeError, TransitionError) as exc:
            return {"status": "FAIL", "reason": str(exc), "stage_changed": False}
        final_state = validate_authoritative_state(root)
        if final_state.get("status") != "PASS":
            return {"status": "FAIL", "reason": "POST_COMMIT_STATE_INVALID", "state": final_state}
        return {
            "status": "PASS",
            "reason": "STAGE_TRANSITION_COMMITTED",
            "stage_changed": True,
            "source_stage": auth["current_stage"],
            "current_stage": target,
            "next_allowed_stage": STAGE_TRANSITIONS.get(target),
            "state_revision": config["state_revision"],
            "authorization_id": auth["authorization_id"],
            "artifact_check": artifact_check,
        }
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _parse_artifacts(values: list[str]) -> list[str | tuple[str, str]]:
    parsed: list[str | tuple[str, str]] = []
    for value in values:
        if "=" in value:
            role, path = value.split("=", 1)
            parsed.append((role, path))
        else:
            parsed.append(value)
    return parsed


def _parse_updates(values: list[str]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    for value in values:
        if "=" not in value:
            raise TransitionError("STATE_UPDATE_FORMAT_INVALID")
        key, raw = value.split("=", 1)
        lowered = raw.strip().lower()
        updates[key.strip()] = True if lowered == "true" else False if lowered == "false" else raw.strip()
    return updates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Authoritative case stage-transition kernel")
    parser.add_argument("--case", required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status")
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--to", required=True)
    prepare.add_argument("--executor", required=True)
    prepare.add_argument("--declared-current", default=None)

    receipt = subparsers.add_parser("receipt")
    receipt.add_argument("--authorization", required=True)
    receipt.add_argument("--artifact", action="append", default=[])
    receipt.add_argument("--executor-status", default="PASS")
    receipt.add_argument("--state-update", action="append", default=[])

    commit = subparsers.add_parser("commit")
    commit.add_argument("--authorization", required=True)
    commit.add_argument("--receipt", required=True)

    args = parser.parse_args(argv)
    case = Path(args.case)
    try:
        if args.command == "status":
            result = validate_authoritative_state(case)
        elif args.command == "prepare":
            result = authorize_transition(
                case,
                requested_stage=args.to,
                executor=args.executor,
                declared_current_stage=args.declared_current,
            )
            if result.get("status") == "PASS":
                path = write_transition_authorization(case, result)
                result["authorization_path"] = _relative(case.resolve(), path)
                result["authorization_sha256"] = _sha256(path)
        elif args.command == "receipt":
            result = build_completion_receipt(
                case,
                authorization_path=args.authorization,
                artifacts=_parse_artifacts(args.artifact),
                executor_status=args.executor_status,
                state_updates=_parse_updates(args.state_update),
            )
            if result.get("status") == "PASS":
                path = write_completion_receipt(case, result)
                result["completion_receipt_path"] = _relative(case.resolve(), path)
                result["completion_receipt_sha256"] = _sha256(path)
        else:
            result = commit_transition(
                case,
                authorization_path=args.authorization,
                completion_receipt_path=args.receipt,
            )
    except TransitionError as exc:
        result = {"status": "FAIL", "reason": str(exc)}
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result.get("status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
