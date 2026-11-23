"""Minimal production-consumer adapter for the recovered 2023D Q3 solver.

This module does not implement a new orchestration framework.  It adapts the
existing ``math_core.registry.run_model`` contract to the case-specific Q3
CLI.  The solver is invoked only when the existing production consumer calls
``run`` after its external human-gate authorization path has succeeded.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from .base import make_result_summary
from .validators import build_risk_item


CASE_ID = "2023D_SHEEP_SPACE_UTILIZATION"
DEFAULT_SOLVER_PATH = "model/q3_solver.py"
EXPECTED_SOURCE_SHA256 = "ADB906E9C57959D8DEF559076F343B70FE304A3967973C4A30BD76CF905226D0"
REQUIRED_CASE_ARTIFACTS = ("results/production_schedule.csv",)
EXPECTED_OUTPUTS = (
    "results/q3_policy_comparison.csv",
    "results/q3_recommended_plan.csv",
    "results/q3_pen_usage_summary.csv",
    "results/q3_sensitivity_results.csv",
    "evidence/q3_simulation_checks.json",
    "evidence/q3_convergence_check.json",
    "evidence/q3_information_leak_check.json",
    "evidence/q3_pooling_rule_check.json",
    "evidence/q3_deterministic_reduction_check.json",
    "evidence/q3_solver_stdout.log",
)


MODEL_SPEC = {
    "model_id": "competition.2023d_q3",
    "model_family": "competition_q3",
    "inputs_schema": {
        "solver_path": "case-relative Python source path",
        "solver_sha256": "expected SHA-256 of solver source",
        "execution_mode": "stochastic_q3",
        "timeout_seconds": "positive integer",
    },
    "outputs_schema": {
        "status": "adapter/solver status string",
        "solver_executed": "boolean",
        "solver_returncode": "integer or null",
        "source_sha256": "actual SHA-256 of solver source",
        "expected_outputs": "case-relative output paths",
        "stdout": "solver stdout when executed",
    },
    "supported": True,
    "limitations": [
        "Q3 numerical claims remain downstream raw results and require result acceptance.",
        "The adapter does not prove global optimality.",
        "The existing competition consumer must authorize the stage before calling this adapter.",
    ],
}


def _source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def build_q3_execution_request(config: dict[str, Any], case_dir: Path) -> dict[str, Any]:
    """Validate config and build the subprocess request without executing it."""

    case_root = case_dir.resolve()
    risks: list[dict[str, Any]] = []
    if case_root.name != CASE_ID:
        risks.append(
            build_risk_item(
                severity="fatal",
                category="adapter_input",
                message=f"case_id mismatch: expected {CASE_ID}, got {case_root.name}",
                field="case_dir",
            )
        )

    solver_relative = str(config.get("solver_path") or DEFAULT_SOLVER_PATH).replace("\\", "/")
    solver_path = (case_root / solver_relative).resolve()
    try:
        solver_path.relative_to(case_root)
    except ValueError:
        risks.append(
            build_risk_item(
                severity="fatal",
                category="adapter_input",
                message="solver_path must remain below case_dir",
                field="solver_path",
            )
        )

    if not solver_path.is_file():
        risks.append(
            build_risk_item(
                severity="fatal",
                category="adapter_input",
                message=f"Q3 solver source is missing: {solver_relative}",
                field="solver_path",
            )
        )
        actual_sha256 = None
    else:
        actual_sha256 = _source_sha256(solver_path)

    required_case_artifacts: list[str] = []
    for relative_path in REQUIRED_CASE_ARTIFACTS:
        artifact_path = case_root / relative_path
        if not artifact_path.is_file():
            risks.append(
                build_risk_item(
                    severity="fatal",
                    category="adapter_input",
                    message=f"required case-local artifact is missing: {relative_path}",
                    field="required_case_artifacts",
                )
            )
            continue
        try:
            with artifact_path.open("rb") as handle:
                handle.read(1)
        except OSError as exc:
            risks.append(
                build_risk_item(
                    severity="fatal",
                    category="adapter_input",
                    message=f"required case-local artifact is unreadable: {relative_path}: {exc}",
                    field="required_case_artifacts",
                )
            )
            continue
        required_case_artifacts.append(relative_path)

    expected_sha256 = str(config.get("solver_sha256") or EXPECTED_SOURCE_SHA256).upper()
    if actual_sha256 is not None and actual_sha256 != expected_sha256:
        risks.append(
            build_risk_item(
                severity="fatal",
                category="adapter_provenance",
                message="Q3 solver SHA-256 does not match configured source hash",
                field="solver_sha256",
            )
        )

    execution_mode = str(config.get("execution_mode") or "").strip()
    if execution_mode != "stochastic_q3":
        risks.append(
            build_risk_item(
                severity="fatal",
                category="adapter_input",
                message="execution_mode must be stochastic_q3",
                field="execution_mode",
            )
        )

    try:
        timeout_seconds = int(config.get("timeout_seconds", 1800))
    except (TypeError, ValueError):
        timeout_seconds = 0
    if timeout_seconds <= 0:
        risks.append(
            build_risk_item(
                severity="fatal",
                category="adapter_input",
                message="timeout_seconds must be a positive integer",
                field="timeout_seconds",
            )
        )

    return {
        "adapter_status": "READY" if not risks else "BLOCKED",
        "case_id": CASE_ID,
        "solver_path": solver_relative,
        "solver_sha256": actual_sha256,
        "expected_solver_sha256": expected_sha256,
        "execution_mode": execution_mode,
        "timeout_seconds": timeout_seconds,
        "command": [sys.executable, str(solver_path)],
        "cwd": str(case_root),
        "expected_outputs": list(EXPECTED_OUTPUTS),
        "required_case_artifacts": required_case_artifacts,
        "risks": risks,
    }


def run(config: dict[str, Any], *, run_id: str, case_dir: Path | None = None) -> dict[str, Any]:
    """Run the case-specific solver through the existing registry consumer."""

    if case_dir is None:
        risk = build_risk_item(
            severity="fatal",
            category="adapter_input",
            message="case_dir is required for the 2023D Q3 adapter",
            field="case_dir",
        )
        return make_result_summary(
            run_id=run_id,
            case_id=CASE_ID,
            model_id=MODEL_SPEC["model_id"],
            model_family=MODEL_SPEC["model_family"],
            success=False,
            status="adapter_input_invalid",
            risk_items=[risk],
            limitations=MODEL_SPEC["limitations"],
            failure_reason="case_dir_missing",
        )

    request = build_q3_execution_request(config, case_dir)
    if request["risks"]:
        return make_result_summary(
            run_id=run_id,
            case_id=CASE_ID,
            model_id=MODEL_SPEC["model_id"],
            model_family=MODEL_SPEC["model_family"],
            success=False,
            status="adapter_input_invalid",
            metrics={"source_sha256": request["solver_sha256"]},
            primary_results={
                "solver_executed": False,
                "expected_outputs": request["expected_outputs"],
            },
            risk_items=request["risks"],
            limitations=MODEL_SPEC["limitations"],
            failure_reason="adapter_input_invalid",
        )

    try:
        completed = subprocess.run(
            request["command"],
            cwd=request["cwd"],
            capture_output=True,
            text=True,
            timeout=request["timeout_seconds"],
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return make_result_summary(
            run_id=run_id,
            case_id=CASE_ID,
            model_id=MODEL_SPEC["model_id"],
            model_family=MODEL_SPEC["model_family"],
            success=False,
            status="solver_timeout",
            metrics={"source_sha256": request["solver_sha256"]},
            primary_results={"solver_executed": True, "solver_returncode": None},
            risk_items=[
                build_risk_item(
                    severity="fatal",
                    category="solver",
                    message=f"Q3 solver timed out after {request['timeout_seconds']} seconds: {exc}",
                )
            ],
            limitations=MODEL_SPEC["limitations"],
            failure_reason="solver_timeout",
        )

    case_root = Path(request["cwd"])
    missing_outputs = [
        relative_path
        for relative_path in request["expected_outputs"]
        if not (case_root / relative_path).is_file()
    ]
    success = completed.returncode == 0 and not missing_outputs
    if completed.returncode != 0:
        status = "q3_solver_failed"
        risks = [
            build_risk_item(
                severity="fatal",
                category="solver",
                message=f"Q3 solver returned exit code {completed.returncode}",
            )
        ]
    elif missing_outputs:
        status = "q3_output_contract_failed"
        risks = [
            build_risk_item(
                severity="fatal",
                category="output_contract",
                message=f"Q3 solver completed but expected outputs are missing: {missing_outputs}",
            )
        ]
    else:
        status = "q3_solver_completed"
        risks = []
    return make_result_summary(
        run_id=run_id,
        case_id=CASE_ID,
        model_id=MODEL_SPEC["model_id"],
        model_family=MODEL_SPEC["model_family"],
        success=success,
        status=status,
        metrics={"source_sha256": request["solver_sha256"]},
        primary_results={
            "solver_executed": True,
            "solver_returncode": completed.returncode,
            "expected_outputs": request["expected_outputs"],
            "missing_outputs": missing_outputs,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        },
        risk_items=risks,
        limitations=MODEL_SPEC["limitations"],
        failure_reason=None if success else "q3_solver_failed",
    )
