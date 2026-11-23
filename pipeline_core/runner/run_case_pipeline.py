from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline_core.literature.support_chain.support_chain_loader import (
    build_support_chain_context,
    load_support_chain,
    summarize_support_chain_context,
)
from pipeline_core.model_route.route_priority import resolve_model_route_from_metadata

RUNNER_DIR = ROOT / "pipeline_core" / "runner"
STATUS_PATH = ROOT / "pipeline_core" / "status" / "case_status.json"
QUALITY_PATH = ROOT / "pipeline_core" / "evaluation" / "quality_evaluation_summary.json"
FREEZE_PATH = ROOT / "pipeline_core" / "freeze" / "freeze_manifest.json"
DEFAULT_REPORT = RUNNER_DIR / "runner_dry_run_report.md"


MECHANISM_FIXTURE_CASES = {
    "case_06_full_constraint_regression_test",
    "case_07_infeasible_model_regression_test",
    "case_08_pipeline_integrated_regression_test",
}

MINIMAL_ONLY_CASES = {
    "case_09_real_optimization_test": ["MINIMAL", "ROUND4", "COMPLETED_MINIMAL_ONLY"],
    "case_10_real_prediction_timeseries_test": ["PROBLEM1", "ROUND5", "COMPLETED_PROBLEM1_ONLY"],
    "case_11_real_evaluation_decision_test": ["COVERAGE", "ROUND6", "COMPLETED_COVERAGE_ONLY"],
}

ARTIFACT_FIELDS = {
    "raw_inputs": "raw inputs",
    "code_files": "code",
    "result_files": "result files",
    "table_outputs": "tables",
    "validation_reports": "validation reports",
    "paper_draft_files": "paper drafts",
    "literature_files": "literature files",
    "human_review_files": "human review files",
}


@dataclass
class CasePlan:
    case_id: str
    case_role: str
    current_status: str
    final_submit_allowed: bool
    freeze_allowed: bool
    known_limitations: list[str]
    available_artifacts: list[str]
    missing_artifacts: list[str]
    recommended_next_action: str
    active_competition_isolation_test: str
    risk_level: str = "LOW"
    safety_findings: list[str] = field(default_factory=list)
    support_chain_context: dict[str, Any] = field(default_factory=dict)
    route_priority_binding: dict[str, Any] = field(default_factory=dict)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def hard_risk(findings: list[str], message: str) -> None:
    findings.append(f"HIGH_RISK: {message}")


def artifact_status(freeze_case: dict[str, Any]) -> tuple[list[str], list[str]]:
    available: list[str] = []
    missing: list[str] = []
    for field, label in ARTIFACT_FIELDS.items():
        items = freeze_case.get(field, [])
        if items:
            available.append(f"{label} ({len(items)})")
        else:
            missing.append(label)
    return available, missing


def case_directory(case_id: str) -> Path | None:
    path = ROOT / "cases" / case_id
    return path if path.exists() else None


def route_metadata_for_case(case: dict[str, Any]) -> tuple[dict[str, Any], Path | None]:
    metadata = dict(case)
    case_dir = case_directory(str(case.get("case_id", "")))
    if case_dir:
        config_path = case_dir / "case_config.json"
        if config_path.exists():
            try:
                metadata.update(json.loads(config_path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                metadata["case_config_parse_error"] = str(config_path)
    return metadata, case_dir


def active_competition_status(case: dict[str, Any]) -> str:
    if case.get("is_mechanism_fixture"):
        return "no: mechanism fixture cannot enter active_competition"
    return (
        "no: regression pressure-test case; may inform active_competition "
        "isolation design but must not be promoted as an active submission"
    )


def infer_support_chain_route(case: dict[str, Any]) -> str:
    case_id = str(case.get("case_id", ""))
    text = " / ".join(
        str(case.get(key, ""))
        for key in ("problem_type", "problem_title", "completed_stage", "current_status")
    )
    if case_id == "case_09_real_optimization_test":
        return "optimization_planning"
    if case_id == "case_10_real_prediction_timeseries_test":
        return "prediction_timeseries"
    if case_id == "case_11_real_evaluation_decision_test":
        return "graph_network_spatial_coverage"
    if case_id == "case_05_thermal_curve_test":
        return "mechanism_fitting"
    if any(token in text for token in ("分类", "聚类", "成分")):
        return "statistics_analysis"
    if any(token in text for token in ("优化", "规划", "调度")):
        return "optimization_planning"
    if any(token in text for token in ("统计", "回归", "预测")):
        return "statistics_analysis"
    if any(token in text for token in ("图网络", "空间", "覆盖")):
        return "graph_network_spatial_coverage"
    return str(case.get("problem_type", "mixed_problem"))


def evaluate_case_plan(
    case: dict[str, Any],
    freeze_case: dict[str, Any],
    global_findings: list[str],
    support_chain_data: dict[str, Any],
) -> CasePlan:
    case_id = case["case_id"]
    findings = list(global_findings)
    status = str(case.get("current_status", "UNKNOWN"))
    final_allowed = bool(case.get("final_submit_allowed", False)) or bool(
        freeze_case.get("final_submit_allowed", False)
    )
    freeze_allowed = bool(freeze_case.get("freeze_allowed", False))

    if final_allowed:
        hard_risk(findings, "final_submit_allowed=true in status or freeze manifest")

    if bool(case.get("human_gate_status", {}).get("auto_approved", False)) or bool(
        freeze_case.get("auto_gate_approved", False)
    ):
        hard_risk(findings, "automatic human gate approval detected")

    if bool(freeze_case.get("official_pdf_generated", False)):
        hard_risk(findings, "generated/formal PDF detected")

    if case_id == "case_05_thermal_curve_test" and "PARTIAL_MODEL_ONLY" not in status:
        hard_risk(findings, "case_05 is not marked PARTIAL_MODEL_ONLY")

    if case_id in MECHANISM_FIXTURE_CASES:
        if not case.get("is_mechanism_fixture") or case.get("counts_in_six_round"):
            hard_risk(findings, "mechanism fixture is counted as a real-problem round")

    if case_id in MINIMAL_ONLY_CASES:
        status_upper = status.upper()
        if not any(marker in status_upper for marker in MINIMAL_ONLY_CASES[case_id]):
            hard_risk(findings, "minimal-only real-problem boundary missing")

    available, missing = artifact_status(freeze_case)
    route_metadata, route_case_dir = route_metadata_for_case(case)
    route_priority_binding = resolve_model_route_from_metadata(
        route_metadata,
        router_suggestion=infer_support_chain_route(case),
        case_dir=route_case_dir,
    )
    support_chain_context = build_support_chain_context(
        problem_type=str(case.get("problem_type", "")),
        model_route=route_priority_binding["final_model_route"],
        chain_data=support_chain_data,
    )
    support_chain_context["route_priority_binding"] = route_priority_binding
    if not support_chain_context.get("support_chain_found", False):
        findings.append("MEDIUM_RISK: support_chain_context not found for inferred model route")
    if support_chain_context.get("formal_reference_allowed", False):
        hard_risk(findings, "support chain unexpectedly allows formal references")
    if support_chain_context.get("excellent_paper_as_method_citation", False):
        hard_risk(findings, "excellent paper sample is treated as method citation")
    risk_level = "HIGH" if any(item.startswith("HIGH_RISK") for item in findings) else "LOW"

    return CasePlan(
        case_id=case_id,
        case_role=str(case.get("case_role", "unknown")),
        current_status=status,
        final_submit_allowed=final_allowed,
        freeze_allowed=freeze_allowed,
        known_limitations=list(case.get("known_limitations", [])),
        available_artifacts=available,
        missing_artifacts=missing,
        recommended_next_action=str(case.get("next_action", "manual review before any next step")),
        active_competition_isolation_test=active_competition_status(case),
        risk_level=risk_level,
        safety_findings=findings or ["dry-run safety checks passed for this case"],
        support_chain_context=support_chain_context,
        route_priority_binding=route_priority_binding,
    )


def load_inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    status = load_json(STATUS_PATH)
    quality = load_json(QUALITY_PATH)
    freeze = load_json(FREEZE_PATH)
    return status, quality, freeze


def select_cases(status: dict[str, Any], case_id: str | None, all_cases: bool) -> list[dict[str, Any]]:
    cases = list(status.get("cases", []))
    if case_id:
        selected = [case for case in cases if case.get("case_id") == case_id]
        if not selected:
            raise SystemExit(f"Unknown case_id: {case_id}")
        return selected
    if all_cases or not case_id:
        return cases
    return cases


def build_plans(selected: list[dict[str, Any]], quality: dict[str, Any], freeze: dict[str, Any]) -> list[CasePlan]:
    freeze_by_id = {case["case_id"]: case for case in freeze.get("cases", [])}
    global_findings: list[str] = []

    if (ROOT / "final_submit").exists():
        hard_risk(global_findings, "project root final_submit/ exists")
    if bool(quality.get("final_submit_allowed_any", False)):
        hard_risk(global_findings, "quality summary reports final_submit_allowed_any=true")
    if bool(quality.get("official_pdf_generated_any", False)):
        hard_risk(global_findings, "quality summary reports official_pdf_generated_any=true")
    if bool(quality.get("auto_gate_approved_any", False)):
        hard_risk(global_findings, "quality summary reports auto_gate_approved_any=true")
    if quality.get("high_risk_cases"):
        hard_risk(
            global_findings,
            "quality summary reports high-risk cases: " + ", ".join(quality["high_risk_cases"]),
        )

    support_chain_data = load_support_chain()
    plans: list[CasePlan] = []
    for case in selected:
        case_id = case["case_id"]
        freeze_case = freeze_by_id.get(case_id)
        if freeze_case is None:
            freeze_case = {"case_id": case_id, "freeze_allowed": False}
            gf = list(global_findings)
            hard_risk(gf, "case missing from freeze manifest")
            plans.append(evaluate_case_plan(case, freeze_case, gf, support_chain_data))
        else:
            plans.append(evaluate_case_plan(case, freeze_case, global_findings, support_chain_data))
    return plans


def list_cases(status: dict[str, Any]) -> str:
    lines = [
        "case_id | case_role | current_status | final_submit_allowed",
        "--- | --- | --- | ---",
    ]
    for case in status.get("cases", []):
        lines.append(
            f"{case['case_id']} | {case.get('case_role', '')} | "
            f"{case.get('current_status', '')} | {case.get('final_submit_allowed', False)}"
        )
    return "\n".join(lines)


def render_report(
    plans: list[CasePlan],
    status: dict[str, Any],
    quality: dict[str, Any],
    freeze: dict[str, Any],
) -> str:
    high_cases = [plan.case_id for plan in plans if plan.risk_level == "HIGH"]
    real_count = sum(1 for case in status.get("cases", []) if case.get("is_real_problem"))
    fixture_count = sum(1 for case in status.get("cases", []) if case.get("is_mechanism_fixture"))
    root_final = (ROOT / "final_submit").exists()
    formal_pdf_any = bool(quality.get("official_pdf_generated_any", False)) or any(
        bool(case.get("official_pdf_generated", False)) for case in freeze.get("cases", [])
    )
    auto_gate_any = bool(quality.get("auto_gate_approved_any", False)) or any(
        bool(case.get("auto_gate_approved", False)) for case in freeze.get("cases", [])
    )
    dry_run_passed = not high_cases and not root_final and not formal_pdf_any and not auto_gate_any
    overall_status = (
        "PASS_WITH_LIMITATION / RUNNER_DRY_RUN_BASELINE_READY"
        if dry_run_passed
        else "WARNING / RUNNER_DRY_RUN_HIGH_RISK_FOUND"
    )

    lines = [
        "# Runner Dry-Run Report",
        "",
        f"Generated at: {date.today().isoformat()}",
        "",
        f"Overall status: `{overall_status}`",
        "",
        "This report is generated by `pipeline_core/runner/run_case_pipeline.py` in dry-run mode.",
        "The runner did not execute models, generate papers, move directories, create `final_submit/`, or approve gates.",
        "",
        "## Summary",
        "",
        f"- Total case count: `{len(status.get('cases', []))}`",
        f"- Real-problem case count: `{real_count}`",
        f"- Mechanism fixture count: `{fixture_count}`",
        f"- High-risk cases in this dry-run selection: `{', '.join(high_cases) if high_cases else 'none'}`",
        f"- Medium-risk cases from quality evaluator: `{', '.join(quality.get('medium_risk_cases', [])) if quality.get('medium_risk_cases') else 'none'}`",
        f"- Project root `final_submit/` found: `{str(root_final).lower()}`",
        f"- Generated/formal PDF found: `{str(formal_pdf_any).lower()}`",
        f"- Automatic gate found: `{str(auto_gate_any).lower()}`",
        f"- Dry-run passed: `{str(dry_run_passed).lower()}`",
        "",
        "## Case Dry-Run Plan",
        "",
        "| case_id | role | current_status | support_chain | final_submit_allowed | freeze_allowed | risk | active_competition_isolation_test | missing_artifacts | recommended_next_action |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for plan in plans:
        missing = "<br>".join(plan.missing_artifacts) if plan.missing_artifacts else "none"
        support_summary = summarize_support_chain_context(plan.support_chain_context)
        support_chain_label = (
            f"{support_summary['resolved_model_route']} / found={str(support_summary['support_chain_found']).lower()}"
        )
        lines.append(
            f"| `{plan.case_id}` | `{plan.case_role}` | `{plan.current_status}` | `{support_chain_label}` | "
            f"`{str(plan.final_submit_allowed).lower()}` | `{str(plan.freeze_allowed).lower()}` | "
            f"`{plan.risk_level}` | {plan.active_competition_isolation_test} | {missing} | "
            f"{plan.recommended_next_action} |"
        )

    lines.extend(["", "## Detailed Case Checks", ""])
    for plan in plans:
        lines.extend(
            [
                f"### {plan.case_id}",
                "",
                f"- case_role: `{plan.case_role}`",
                f"- current_status: `{plan.current_status}`",
                f"- final_submit_allowed: `{str(plan.final_submit_allowed).lower()}`",
                f"- freeze_allowed: `{str(plan.freeze_allowed).lower()}`",
                f"- available_artifacts: {', '.join(plan.available_artifacts) if plan.available_artifacts else 'none'}",
                f"- missing_artifacts: {', '.join(plan.missing_artifacts) if plan.missing_artifacts else 'none'}",
                f"- support_chain_context: `{summarize_support_chain_context(plan.support_chain_context)}`",
                f"- route_priority_binding: `{plan.route_priority_binding}`",
                f"- active_competition_isolation_test: {plan.active_competition_isolation_test}",
                "",
                "Known limitations:",
            ]
        )
        lines.extend([f"- {item}" for item in plan.known_limitations] or ["- none recorded"])
        lines.extend(["", "Safety findings:"])
        lines.extend([f"- {item}" for item in plan.safety_findings])
        lines.append("")

    lines.extend(
        [
            "## Hard Safety Boundaries",
            "",
            "- Runner baseline supports dry-run only.",
            "- Runner baseline does not run model code.",
            "- Runner baseline does not generate paper drafts or formal PDFs.",
            "- Runner baseline does not create or modify `final_submit/`.",
            "- Runner baseline does not write `MANUAL_APPROVAL_BY_USER`.",
            "- Runner baseline does not auto-approve human gates.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MathModeling2026 baseline dry-run pipeline runner. It never executes models."
    )
    parser.add_argument("--list-cases", action="store_true", help="List cases from case_status.json.")
    parser.add_argument("--case-id", help="Select one case for dry-run planning.")
    parser.add_argument("--all", action="store_true", help="Select all cases for dry-run planning.")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Dry-run mode. This is always enabled.")
    parser.add_argument(
        "--report",
        default=str(DEFAULT_REPORT),
        help="Dry-run report path. Defaults to pipeline_core/runner/runner_dry_run_report.md.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    status, quality, freeze = load_inputs()

    if args.list_cases:
        print(list_cases(status))
        return 0

    selected = select_cases(status, args.case_id, args.all)
    plans = build_plans(selected, quality, freeze)
    report = render_report(plans, status, quality, freeze)
    report_path = Path(args.report)
    if not report_path.is_absolute():
        report_path = ROOT / report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    high_cases = [plan.case_id for plan in plans if plan.risk_level == "HIGH"]
    if high_cases:
        print("WARNING / RUNNER_DRY_RUN_HIGH_RISK_FOUND")
        print("high_risk_cases=" + ",".join(high_cases))
        return 2

    print("PASS_WITH_LIMITATION / RUNNER_DRY_RUN_BASELINE_READY")
    print(f"report={rel(report_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
