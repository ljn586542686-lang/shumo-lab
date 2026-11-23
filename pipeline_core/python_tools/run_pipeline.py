"""Stage dispatcher for the MathModeling2026 V3.0 pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_FOR_IMPORT = Path(__file__).resolve().parents[2]
if str(ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(ROOT_FOR_IMPORT))

from pipeline_core.python_tools.data_audit import audit_data
from pipeline_core.python_tools.final_freezer import check_freeze_ready
from pipeline_core.python_tools.human_gate import evaluate_human_gate
from pipeline_core.python_tools.result_summary_validator import validate_result_summary
from pipeline_core.python_tools.workflow_state import validate_public_stage, validate_workflow_state
from pipeline_core.python_tools.transition_case import (
    STAGE_TRANSITIONS,
    normalize_stage,
    verify_transition_authorization,
)
from pipeline_core.model_route.model_route_stage import run_model_route_stage
from pipeline_core.router.problem_type_router import (
    ProblemContractBlocked,
    build_output as build_problem_type_output,
    prepare_model_building_handoff_inputs,
    validate_model_building_handoff,
    write_model_building_handoff,
    write_case_outputs as write_problem_type_outputs,
)


SUPPORTED_STAGES = {
    "init",
    "problem",
    "data",
    "model",
    "validate",
    "literature",
    "paper",
    "disclose",
    "freeze",
}


def run_stage(
    case_dir: Path,
    stage: str,
    *,
    candidate_manifest_path: str | None = None,
    expected_candidate_manifest_sha256: str | None = None,
    expected_run_id: str | None = None,
    post_writer_reentry_path: str | None = None,
    structured_semantic_reentry_path: str | None = None,
    r3_approval_input_path: str | None = None,
    transition_authorization_path: str | None = None,
) -> dict:
    if stage not in SUPPORTED_STAGES:
        raise ValueError(f"Unsupported stage: {stage}")
    authoritative_state: dict | None = None
    verified_transition: dict | None = None
    config_path = case_dir / "case_config.json"
    if config_path.is_file():
        try:
            case_config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            case_config = {}
        state = validate_workflow_state(case_dir, case_config if isinstance(case_config, dict) else {})
        if state.get("status") == "FAIL":
            return {
                "stage": stage,
                "status": "BLOCKED",
                "reason": state.get("reason"),
                "workflow_state": state,
            }
        if state.get("status") == "PASS":
            authoritative_state = state
        stage_state = validate_public_stage(stage, state)
        if stage_state.get("status") == "FAIL":
            return {
                "stage": stage,
                "status": "BLOCKED",
                "reason": stage_state.get("reason"),
                "workflow_state": state,
                "stage_state": stage_state,
            }
        if state.get("status") == "PASS" and stage == "model":
            if not transition_authorization_path:
                return {
                    "stage": stage,
                    "status": "BLOCKED",
                    "reason": "TRANSITION_AUTHORIZATION_REQUIRED",
                    "workflow_state": state,
                    "stage_completed": False,
                }
            transition = verify_transition_authorization(case_dir, transition_authorization_path)
            verified_transition = transition
            expected_target = STAGE_TRANSITIONS.get(
                normalize_stage(state.get("canonical_current_stage") or state.get("current_stage"))
            )
            if (
                transition.get("status") != "PASS"
                or normalize_stage(transition.get("payload", {}).get("authorized_next_stage")) != expected_target
                or transition.get("payload", {}).get("executor") != f"run_pipeline:{stage}"
            ):
                return {
                    "stage": stage,
                    "status": "BLOCKED",
                    "reason": (
                        transition.get("reason")
                        if transition.get("status") != "PASS"
                        else "TRANSITION_EXECUTOR_MISMATCH"
                    ),
                    "workflow_state": state,
                    "transition_authorization": transition,
                    "stage_completed": False,
                }
    if stage == "data":
        result = audit_data(case_dir)
        if authoritative_state is not None:
            result["executor_status"] = result.get("status")
            if result.get("status") == "ok":
                result["status"] = "AWAITING_TRANSITION"
            result.update(
                {
                    "stage": stage,
                    "stage_completed": False,
                    "transition_required": True,
                    "stop_before_downstream": True,
                }
            )
        return result
    if stage == "validate":
        result = validate_result_summary(case_dir)
        if authoritative_state is not None:
            result["executor_status"] = result.get("status")
            if result.get("status") == "ok":
                result["status"] = "AWAITING_TRANSITION"
            result.update(
                {
                    "stage": stage,
                    "stage_completed": False,
                    "transition_required": True,
                    "stop_before_downstream": True,
                }
            )
        return result
    if stage == "model":
        gate = evaluate_human_gate(case_dir, "GATE1")
        if gate.get("gate_status") != "PASS":
            return {
                "stage": stage,
                "status": "BLOCKED",
                "reason": "REQUIRED_HUMAN_GATE_NOT_PASS",
                "required_gate": "GATE1",
                "gate_evaluation": gate,
            }
        try:
            router_result = build_problem_type_output(case_dir)
            write_problem_type_outputs(case_dir, router_result)
            route_stage = run_model_route_stage(case_dir, router_result)
            modeling_handoff = None
            if route_stage.get("status") == "READY_FOR_MODEL_IMPLEMENTATION":
                prepare_model_building_handoff_inputs(
                    case_dir,
                    router_result,
                    route_stage.get("outputs") or {},
                    target_stage=normalize_stage(
                        (verified_transition or {}).get("payload", {}).get("authorized_next_stage")
                        or "MODEL_IMPLEMENTATION"
                    ),
                )
                write_model_building_handoff(case_dir, router_result)
                modeling_handoff = validate_model_building_handoff(case_dir, router_result)
        except (ProblemContractBlocked, OSError, UnicodeError, ValueError) as exc:
            return {
                "stage": stage,
                "status": "BLOCKED",
                "reason": "MODEL_ROUTE_PRODUCTION_WIRING_FAILED",
                "required_gate": "GATE1",
                "gate1_status": "PASS",
                "error": str(exc),
            }
        result = {
            "stage": stage,
            "status": route_stage["status"],
            "required_gate": "GATE2",
            "gate1_status": "PASS",
            "problem_type_routing": {
                "status": router_result.get("status"),
                "primary_type": router_result.get("primary_problem_type"),
                "secondary_types": router_result.get("secondary_problem_types", []),
                "result_path": "routing/problem_type_routing_result.json",
            },
            "model_route_stage": route_stage,
            "model_building_handoff": modeling_handoff,
        }
        if authoritative_state is not None:
            result["executor_status"] = result.get("status")
            if result.get("status") == "READY_FOR_MODEL_IMPLEMENTATION":
                result["status"] = "AWAITING_TRANSITION"
            result.update(
                {
                    "stage_completed": False,
                    "transition_required": True,
                    "stop_before_downstream": True,
                }
            )
        return result
    if stage == "freeze":
        result = check_freeze_ready(
            case_dir,
            candidate_manifest_path=candidate_manifest_path,
            expected_candidate_manifest_sha256=expected_candidate_manifest_sha256,
            expected_run_id=expected_run_id,
            post_writer_reentry_path=post_writer_reentry_path,
            require_post_writer_reentry=True,
            structured_semantic_reentry_path=structured_semantic_reentry_path,
            require_structured_semantic_reentry=True,
            r3_approval_input_path=r3_approval_input_path,
        )
        if authoritative_state is not None:
            result.update(
                {
                    "stage": stage,
                    "stage_completed": False,
                    "transition_required": True,
                    "stop_before_downstream": True,
                }
            )
        return result
    return {
        "stage": stage,
        "status": "AWAITING_EXECUTOR",
        "reason": "STAGE_EXECUTOR_NOT_WIRED",
        "stage_completed": False,
        "stop_before_downstream": True,
    }


def render_cli_json(result: dict) -> str:
    """Render machine-readable JSON without depending on the Windows code page."""

    return json.dumps(result, ensure_ascii=True, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--stage", required=True, choices=sorted(SUPPORTED_STAGES))
    parser.add_argument("--candidate-manifest-path", default=None)
    parser.add_argument("--expected-candidate-manifest-sha256", default=None)
    parser.add_argument("--expected-run-id", default=None)
    parser.add_argument("--post-writer-reentry-path", default=None)
    parser.add_argument("--structured-semantic-reentry-path", default=None)
    parser.add_argument("--r3-approval-input-path", default=None)
    parser.add_argument("--transition-authorization", default=None)
    args = parser.parse_args()
    result = run_stage(
        Path(args.case),
        args.stage,
        candidate_manifest_path=args.candidate_manifest_path,
        expected_candidate_manifest_sha256=args.expected_candidate_manifest_sha256,
        expected_run_id=args.expected_run_id,
        post_writer_reentry_path=args.post_writer_reentry_path,
        structured_semantic_reentry_path=args.structured_semantic_reentry_path,
        r3_approval_input_path=args.r3_approval_input_path,
        transition_authorization_path=args.transition_authorization,
    )
    print(render_cli_json(result))
    if result.get("status") in {"BLOCKED", "FAIL"}:
        return 2
    if result.get("stage_completed") is False:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
