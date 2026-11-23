from __future__ import annotations

import json
import re
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
from pipeline_core.evaluation.red_team_quality_gate import run_u4_red_team_dry_run
from pipeline_core.model_route.route_priority import resolve_model_route

STATUS_JSON = ROOT / "pipeline_core" / "status" / "case_status.json"
EVAL_DIR = ROOT / "pipeline_core" / "evaluation"
ACTIVE_CASE = ROOT / "cases" / "active_competition" / "current_case"
PAPER_DRAFT_DIR = ROOT / "pipeline_core" / "paper_draft"

QUALITY_BLOCK_RULE_PATHS = {
    "quality_gate_block_response_rules": ROOT
    / "pipeline_core"
    / "paper_quality"
    / "rules"
    / "quality_gate_block_response_rules.md",
    "showcase_to_formal_upgrade_block_rules": ROOT
    / "pipeline_core"
    / "paper_quality"
    / "rules"
    / "showcase_to_formal_upgrade_block_rules.md",
}
QUALITY_BLOCK_TASK_LIST = ROOT / "pipeline_core" / "test_results" / "l4_2_quality_gate_block_task_list.md"


SHOWCASE_FORBIDDEN_AI_TERMS = [
    "ChatGPT",
    "Codex",
    "GPT",
    "具体模型名称",
    "完整提示词",
    "完整对话",
    "底层系统实现细节",
]

AI_DISCLOSURE_MODE_SHOWCASE = "showcase_mode"
AI_DISCLOSURE_MODE_OFFICIAL = "official_submission_mode"

SHOWCASE_ALLOWED_AI_WORDING = [
    "智能辅助工具",
    "AI 辅助工具",
    "辅助建模工具",
    "智能化文本与代码辅助工具",
]

OFFICIAL_REQUIRED_DISCLOSURE_FIELDS = [
    "AI 工具名称",
    "版本",
    "型号",
    "开发机构",
    "使用日期",
    "使用环节",
    "生成或辅助内容",
    "人工复核",
    "队员独立完成核心建模与分析",
]

SHOWCASE_FORBIDDEN_BOUNDARY_TERMS = [
    "全局最优方案",
    "最终正式论文",
    "正式提交版本",
    "自动写论文系统",
    "自动提交系统",
    "完整解决 2017B",
    "已完成问题3",
    "已完成问题4",
    "预测完成率就是真实完成率",
]

README_STALE_TERMS = [
    "TEMPLATE_ONLY",
    "NOT_READY_FOR_MODELING",
    "empty baseline template",
    "does not contain a real competition problem yet",
]

FIGURE_ENGLISH_TERMS = [
    "TASK SPATIAL DISTRIBUTION",
    "PRICE DISTRIBUTION",
    "COMPLETION PRICE COMPARISON",
    "ORIGINAL VS NEW PRICE",
    "PREDICTED COMPLETION",
    "GREEN COMPLETED",
    "RED UNFINISHED",
    "Measured vs fitted curve",
]

ALLOWED_ENGLISH_TERMS = {
    "AUC",
    "CSV",
    "JSON",
    "baseline",
    "DRAFT_ONLY",
    "NOT_FOR_FINAL_SUBMISSION",
    "log-loss",
    "Brier",
}

REAL_MINIMAL_CASES = {
    "case_09_real_optimization_test",
    "case_10_real_prediction_timeseries_test",
    "case_11_real_evaluation_decision_test",
}

MECHANISM_FIXTURES = {
    "case_06_full_constraint_regression_test",
    "case_07_infeasible_model_regression_test",
    "case_08_pipeline_integrated_regression_test",
}


@dataclass
class Finding:
    level: str
    message: str
    path: str = ""


@dataclass
class CaseEvaluation:
    case_id: str
    current_status: str
    risk_level: str = "LOW"
    findings: list[Finding] = field(default_factory=list)
    final_submit_allowed: bool = False
    final_submit_exists: bool = False
    official_pdf_files: list[str] = field(default_factory=list)
    auto_gate_found: bool = False
    manual_approval_token_found: bool = False


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_text(path: Path, limit: int = 300_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def risk_max(left: str, right: str) -> str:
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    return right if order[right] > order[left] else left


def add_finding(eval_item: CaseEvaluation, level: str, message: str, path: Path | None = None) -> None:
    eval_item.risk_level = risk_max(eval_item.risk_level, level)
    eval_item.findings.append(Finding(level, message, rel(path) if path else ""))


def is_raw_pdf(path: Path, case_path: Path) -> bool:
    try:
        parts = path.relative_to(case_path).parts
    except ValueError:
        return False
    return len(parts) >= 2 and parts[0] == "data" and parts[1] == "raw"


def is_showcase_pdf(path: Path, case_path: Path) -> bool:
    try:
        parts = path.relative_to(case_path).parts
    except ValueError:
        return False
    return bool(parts) and parts[0] == "showcase_pdf"


def case_config(case_path: Path) -> dict[str, Any]:
    path = case_path / "case_config.json"
    if not path.exists():
        return {}
    try:
        return read_json(path)
    except json.JSONDecodeError:
        return {"_parse_error": True}


def line_has_negation(line: str) -> bool:
    negations = ["未", "不", "不得", "禁止", "不是", "不能", "仅", "只", "not", "no"]
    return any(token in line for token in negations)


def scan_gate_status(case_path: Path) -> tuple[bool, bool]:
    human_review = case_path / "human_review"
    if not human_review.exists():
        return False, False

    auto_gate = False
    manual_token = False
    for path in human_review.glob("*.md"):
        text = read_text(path)
        for line in text.splitlines():
            upper = line.upper()
            if "MANUAL_APPROVAL_BY_USER" in line and not line_has_negation(line):
                manual_token = True
            if re.search(r"\b(AUTO_APPROVED|APPROVED)\b", upper) and not line_has_negation(line):
                auto_gate = True
    return auto_gate, manual_token


def evaluate_status_case(case: dict[str, Any], root_final_submit_exists: bool) -> CaseEvaluation:
    case_id = case.get("case_id", "UNKNOWN")
    case_path = ROOT / "cases" / case_id
    item = CaseEvaluation(
        case_id=case_id,
        current_status=case.get("current_status", "UNKNOWN"),
        final_submit_allowed=bool(case.get("final_submit_allowed", False)),
        final_submit_exists=(case_path / "final_submit").exists(),
    )

    if not case_path.exists():
        add_finding(item, "HIGH", "case directory missing", case_path)
        return item

    config = case_config(case_path)
    if config.get("_parse_error"):
        add_finding(item, "MEDIUM", "case_config.json parse error", case_path / "case_config.json")
    if item.final_submit_allowed or bool(config.get("final_submit_allowed", False)):
        add_finding(item, "HIGH", "final_submit_allowed is true")
    if root_final_submit_exists:
        add_finding(item, "HIGH", "global final_submit/ exists", ROOT / "final_submit")
    if item.final_submit_exists:
        add_finding(item, "HIGH", "case final_submit/ exists", case_path / "final_submit")

    item.official_pdf_files = [
        rel(path)
        for path in case_path.rglob("*.pdf")
        if not is_raw_pdf(path, case_path) and not is_showcase_pdf(path, case_path)
    ]
    if item.official_pdf_files:
        add_finding(item, "HIGH", "generated PDF outside raw/showcase_pdf detected")

    item.auto_gate_found, item.manual_approval_token_found = scan_gate_status(case_path)
    if item.auto_gate_found:
        add_finding(item, "HIGH", "automatic human gate approval wording detected")
    if item.manual_approval_token_found:
        add_finding(item, "HIGH", "manual approval token appears without negation context")

    if case_id == "case_05_thermal_curve_test" and "PARTIAL_MODEL_ONLY" not in item.current_status:
        add_finding(item, "HIGH", "case_05 must remain PARTIAL_MODEL_ONLY")
    if case_id in MECHANISM_FIXTURES:
        if case.get("counts_in_six_round") or not case.get("is_mechanism_fixture"):
            add_finding(item, "HIGH", "mechanism fixture is misclassified")
    if case_id in REAL_MINIMAL_CASES:
        status = item.current_status.upper()
        if not any(marker in status for marker in ["MINIMAL", "PROBLEM1", "COVERAGE", "COMPLETED"]):
            add_finding(item, "HIGH", "minimal-loop boundary marker missing")

    return item


def contains_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def markdown_looks_english(path: Path) -> bool:
    text = read_text(path, limit=20_000)
    if len(text.strip()) < 200:
        return False
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    ascii_words = len(re.findall(r"\b[a-zA-Z]{4,}\b", text))
    return ascii_words > 80 and chinese_chars < 80


def scan_text_files(paths: list[Path], terms: list[str]) -> list[str]:
    hits: list[str] = []
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        for lineno, line in enumerate(read_text(path).splitlines(), start=1):
            for term in terms:
                if term in line:
                    hits.append(f"{rel(path)}:{lineno}: {line.strip()[:160]}")
                    break
    return hits


def evaluate_ai_usage_disclosure(paths: list[Path], mode: str) -> list[str]:
    """Return AI disclosure risk hits for a specific disclosure mode.

    showcase_mode: school/internal display material should use generalized wording.
    official_submission_mode: official contest material must disclose concrete tool facts.
    """
    hits: list[str] = []
    existing_paths = [path for path in paths if path.exists() and path.is_file()]

    if mode == AI_DISCLOSURE_MODE_SHOWCASE:
        return scan_text_files(existing_paths, SHOWCASE_FORBIDDEN_AI_TERMS)

    if mode == AI_DISCLOSURE_MODE_OFFICIAL:
        combined_text = "\n".join(read_text(path) for path in existing_paths)
        if not combined_text.strip():
            return ["official_submission_mode: AI disclosure material is missing"]

        missing_fields = [
            field for field in OFFICIAL_REQUIRED_DISCLOSURE_FIELDS if field not in combined_text
        ]
        if missing_fields:
            hits.append(
                "official_submission_mode: missing required disclosure fields: "
                + ", ".join(missing_fields)
            )

        uses_only_generic = any(term in combined_text for term in SHOWCASE_ALLOWED_AI_WORDING)
        has_concrete_name_hint = any(
            token in combined_text
            for token in ["OpenAI", "ChatGPT", "Codex", "GPT", "Claude", "Gemini", "DeepSeek", "Kimi", "通义", "文心"]
        )
        if uses_only_generic and not has_concrete_name_hint:
            hits.append(
                "official_submission_mode: generic AI wording cannot replace concrete tool disclosure"
            )
        return hits

    return [f"unknown AI disclosure mode: {mode}"]


def gather_markdown_files(*dirs: Path) -> list[Path]:
    files: list[Path] = []
    for directory in dirs:
        if directory.exists():
            files.extend(directory.rglob("*.md"))
    return files


def load_quality_block_rule_context() -> dict[str, Any]:
    """Load L4.2 quality-block rules as explicit evaluator inputs."""
    rule_files: dict[str, dict[str, Any]] = {}
    for rule_name, path in QUALITY_BLOCK_RULE_PATHS.items():
        text = read_text(path)
        rule_files[rule_name] = {
            "path": rel(path),
            "exists": path.exists(),
            "loaded": bool(text.strip()),
            "length": len(text),
            "mentions_showcase_block": "SHOWCASE_QUALITY_GATE_FAILED" in text,
            "mentions_pending_human_gate": "PENDING_MANUAL_REVIEW" in text,
            "mentions_final_submit_block": "final_submit" in text,
        }

    return {
        "rules_invoked": all(item["loaded"] for item in rule_files.values()),
        "rule_files": rule_files,
    }


def build_quality_gate_block_response(
    *,
    boundary_wording_risk: bool,
    figure_language_residual_risk: bool,
    citation_ready: bool,
    formal_reference_allowed: bool,
    manual_review_required: bool,
) -> dict[str, Any]:
    """Apply L4.2 block-response rules to a quality-gate state."""
    rule_context = load_quality_block_rule_context()
    blocking_reasons: list[str] = []
    remediation_tasks: list[str] = []

    if not rule_context["rules_invoked"]:
        blocking_reasons.append("quality block rule files are missing or unreadable")
        remediation_tasks.append("restore and review L4.2 quality block rule files")
    if boundary_wording_risk:
        blocking_reasons.append("showcase boundary wording risk")
        remediation_tasks.append("review and revise boundary wording before any formal upgrade")
    if figure_language_residual_risk:
        blocking_reasons.append("figure language residual risk")
        remediation_tasks.append("recheck figure titles, axes, legends, and captions")
    if citation_ready:
        blocking_reasons.append("citation_ready unexpectedly true before manual citation review")
        remediation_tasks.append("reset citation_ready to false until formal metadata review passes")
    if formal_reference_allowed:
        blocking_reasons.append("formal_reference_allowed unexpectedly true")
        remediation_tasks.append("block formal references until manual citation review passes")
    if manual_review_required:
        blocking_reasons.append("manual review is still required")

    blocked = bool(
        boundary_wording_risk
        or figure_language_residual_risk
        or citation_ready
        or formal_reference_allowed
        or manual_review_required
        or not rule_context["rules_invoked"]
    )

    return {
        "rule_context": rule_context,
        "block_status": (
            "BLOCKED / SHOWCASE_QUALITY_GATE_FAILED"
            if blocked
            else "PASS_WITH_LIMITATION / SHOWCASE_QUALITY_GATE_PASSED_WITH_LIMITATION"
        ),
        "blocking_reasons": blocking_reasons,
        "risk_level": "HIGH" if blocked else "LOW",
        "affected_scope": [
            "showcase package",
            "showcase PDF",
            "formal paper upgrade path",
            "final_submit gate",
        ],
        "training_showcase_allowed": True,
        "formal_upgrade_allowed": False,
        "final_submit_allowed": False,
        "human_gate_status": "PENDING_MANUAL_REVIEW",
        "manual_review_required": manual_review_required,
        "citation_ready": citation_ready,
        "formal_reference_allowed": formal_reference_allowed,
        "remediation_task_list_created": QUALITY_BLOCK_TASK_LIST.exists()
        and QUALITY_BLOCK_TASK_LIST.stat().st_size > 0,
        "remediation_tasks": remediation_tasks,
        "official_submission_mode_modified": False,
        "formal_paper_generation_allowed": False,
        "formal_references_generation_allowed": False,
    }


def evaluate_quality_block_rule_invocation_scenario() -> dict[str, Any]:
    """Minimal L4.2b scenario used to verify block-rule invocation."""
    return build_quality_gate_block_response(
        boundary_wording_risk=True,
        figure_language_residual_risk=True,
        citation_ready=False,
        formal_reference_allowed=False,
        manual_review_required=True,
    )


def evaluate_active_showcase() -> tuple[list[Finding], dict[str, Any]]:
    findings: list[Finding] = []
    def add(level: str, message: str, path: Path | None = None) -> None:
        findings.append(Finding(level, message, rel(path) if path else ""))

    figures = ACTIVE_CASE / "output" / "figures"
    tables_showcase = ACTIVE_CASE / "output" / "tables_showcase"
    showcase = ACTIVE_CASE / "showcase"
    showcase_pdf = ACTIVE_CASE / "showcase_pdf"
    readme = ACTIVE_CASE / "README.md"
    meta = ROOT / "pipeline_core" / "meta"
    audit = ROOT / "pipeline_core" / "audit"
    code_dir = ACTIVE_CASE / "code"

    figure_scripts = list(code_dir.glob("*.py"))
    figure_term_hits = scan_text_files(figure_scripts, FIGURE_ENGLISH_TERMS)
    if figure_term_hits:
        add("MEDIUM", "possible English figure labels remain in generation scripts")

    if showcase.exists():
        if not tables_showcase.exists() or not any(tables_showcase.glob("*_zh.csv")):
            add("MEDIUM", "showcase stage exists but output/tables_showcase/ Chinese CSV files are missing")
        if not (showcase / "2017B_showcase_tables.xlsx").exists():
            add("MEDIUM", "showcase Excel workbook is missing", showcase / "2017B_showcase_tables.xlsx")

    if readme.exists():
        text = read_text(readme)
        if any(term in text for term in README_STALE_TERMS):
            add("HIGH", "current_case README still contains stale template status", readme)
    else:
        add("MEDIUM", "current_case README missing", readme)

    display_markdown_files = gather_markdown_files(showcase, showcase_pdf)
    markdown_files = display_markdown_files + gather_markdown_files(meta, audit)
    english_reports = [rel(path) for path in markdown_files if markdown_looks_english(path)]
    if english_reports:
        add("MEDIUM", "human-facing markdown appears English-dominant: " + "; ".join(english_reports[:8]))

    ai_disclosure_mode = AI_DISCLOSURE_MODE_SHOWCASE
    ai_hits = evaluate_ai_usage_disclosure(
        display_markdown_files + ([readme] if readme.exists() else []),
        ai_disclosure_mode,
    )
    if ai_hits:
        add("HIGH", "specific AI name or sensitive AI-use wording found")

    boundary_hits = scan_text_files(markdown_files, SHOWCASE_FORBIDDEN_BOUNDARY_TERMS)
    if boundary_hits:
        add("HIGH", "showcase boundary violation wording found")

    block_response = build_quality_gate_block_response(
        boundary_wording_risk=bool(boundary_hits),
        figure_language_residual_risk=bool(figure_term_hits),
        citation_ready=False,
        formal_reference_allowed=False,
        manual_review_required=True,
    )
    if not block_response["rule_context"]["rules_invoked"]:
        add("HIGH", "L4.2 quality block rules are not loaded by evaluator")

    final_submit_found = (ROOT / "final_submit").exists() or (ACTIVE_CASE / "final_submit").exists()
    if final_submit_found:
        add("HIGH", "final_submit/ exists")

    config = case_config(ACTIVE_CASE)
    if bool(config.get("final_submit_allowed", False)):
        add("HIGH", "active case final_submit_allowed is true", ACTIVE_CASE / "case_config.json")

    pdf_path = showcase_pdf / "2017B_stage_showcase_revised.pdf"
    pdf_found = pdf_path.exists() and pdf_path.stat().st_size > 0
    excel_found = (showcase / "2017B_showcase_tables.xlsx").exists()
    revised_review = (showcase_pdf / "pdf_manual_review_gate.md").exists()

    summary = {
        "current_case_id": config.get("case_id", "active_competition/current_case"),
        "showcase_pdf_found": pdf_found,
        "showcase_excel_found": excel_found,
        "readme_template_status_found": bool(readme.exists() and any(term in read_text(readme) for term in README_STALE_TERMS)),
        "specific_ai_name_found": bool(ai_hits),
        "ai_disclosure_mode": ai_disclosure_mode,
        "official_submission_mode_supported": True,
        "boundary_violation_found": bool(boundary_hits),
        "quality_block_rule_invocation": block_response,
        "human_gate_status": block_response["human_gate_status"],
        "formal_upgrade_allowed": block_response["formal_upgrade_allowed"],
        "remediation_task_list_created": block_response["remediation_task_list_created"],
        "final_submit_found": final_submit_found,
        "official_pdf_found": False,
        "pdf_manual_review_found": revised_review,
        "high_risk_issues": [f.message for f in findings if f.level == "HIGH"],
        "medium_risk_issues": [f.message for f in findings if f.level == "MEDIUM"],
    }
    summary["overall_status"] = (
        "BLOCKED / SHOWCASE_QUALITY_GATE_FAILED"
        if summary["high_risk_issues"]
        else "PASS_WITH_LIMITATION / AI_DISCLOSURE_DUAL_MODE_READY"
    )
    summary["next_recommended_action"] = "第H步：目录分类一致性审计，处理 case_01 / case_02 分类问题"
    return findings, summary


def render_showcase_report(findings: list[Finding], summary: dict[str, Any]) -> str:
    lines = [
        "# 展示层质量回归检查报告",
        "",
        f"生成日期：{date.today().isoformat()}",
        "",
        f"状态：`{summary['overall_status']}`",
        "",
        "## 检查范围",
        "",
        "1. `cases/active_competition/current_case/output/figures/`",
        "2. `cases/active_competition/current_case/output/tables_showcase/`",
        "3. `cases/active_competition/current_case/showcase/`",
        "4. `cases/active_competition/current_case/showcase_pdf/`",
        "5. `cases/active_competition/current_case/README.md`",
        "6. `pipeline_core/meta/`",
        "7. `pipeline_core/audit/`",
        "",
        "## 核心结论",
        "",
        f"- 展示版 PDF 存在：`{summary['showcase_pdf_found']}`",
        f"- 中文展示 Excel 存在：`{summary['showcase_excel_found']}`",
        f"- README 旧模板状态命中：`{summary['readme_template_status_found']}`",
        f"- 具体 AI 名称风险：`{summary['specific_ai_name_found']}`",
        f"- 结论边界越界风险：`{summary['boundary_violation_found']}`",
        f"- final_submit 风险：`{summary['final_submit_found']}`",
        "",
        "## 风险清单",
        "",
    ]
    if findings:
        for item in findings:
            suffix = f"（{item.path}）" if item.path else ""
            lines.append(f"- {item.level}: {item.message}{suffix}")
    else:
        lines.append("- 未发现高风险或中风险问题。")

    lines.extend(
        [
            "",
            "## 回灌结论",
            "",
            "本轮暴露出的展示层问题已经回灌到质量评测器和展示层规则文档中。后续展示包、PDF/PPT 或 active_competition 新阶段材料，都应执行图表语言、表格展示版、README 状态、AI 使用记录、结论边界和 final_submit 安全检查。",
        ]
    )
    return "\n".join(lines) + "\n"


def render_case_report(evaluations: list[CaseEvaluation]) -> str:
    lines = [
        "# Unified Case Quality Evaluation Report",
        "",
        f"Generated at: {date.today().isoformat()}",
        "",
        "This report is generated by `evaluate_case_quality.py` and does not authorize final submission.",
        "",
        "| case_id | status | risk | final_submit_allowed | issues |",
        "|---|---|---|---|---|",
    ]
    for item in evaluations:
        issues = "<br>".join(f"{f.level}: {f.message}" for f in item.findings) if item.findings else "none"
        lines.append(f"| `{item.case_id}` | `{item.current_status}` | {item.risk_level} | {item.final_submit_allowed} | {issues} |")
    return "\n".join(lines) + "\n"


def evaluate_support_chain_quality() -> tuple[list[Finding], dict[str, Any]]:
    findings: list[Finding] = []
    chain_data = load_support_chain()
    required_routes = {
        "optimization_test_prompt": "optimization_planning",
        "statistics_test_prompt": "statistics_analysis",
        "stat_opt_mixed_test_prompt": "mixed_problem",
        "mechanism_fitting_test_prompt": "mechanism_fitting",
    }
    contexts = {
        name: build_support_chain_context(
            problem_type=route,
            model_route=route,
            chain_data=chain_data,
        )
        for name, route in required_routes.items()
    }

    missing_contexts = [
        name for name, context in contexts.items() if not context.get("support_chain_found", False)
    ]
    missing_rules = [
        name for name, context in contexts.items() if not context.get("excellent_paper_structure_rules")
    ]
    formal_reference_allowed = any(
        bool(context.get("formal_reference_allowed", False)) for context in contexts.values()
    )
    citation_ready_found = any(bool(context.get("citation_ready", False)) for context in contexts.values())
    excellent_as_method = any(
        bool(context.get("excellent_paper_as_method_citation", False)) for context in contexts.values()
    )

    if missing_contexts:
        findings.append(
            Finding(
                "MEDIUM",
                "support_chain_context missing for routes: " + ", ".join(missing_contexts),
                "pipeline_core/literature/support_chain/model_route_support_chain_matrix.json",
            )
        )
    if missing_rules:
        findings.append(
            Finding(
                "MEDIUM",
                "excellent paper structure rules missing for routes: " + ", ".join(missing_rules),
                "pipeline_core/paper_quality/rules/",
            )
        )
    if formal_reference_allowed:
        findings.append(
            Finding(
                "HIGH",
                "support chain unexpectedly allows formal references",
                "pipeline_core/literature/support_chain/model_route_support_chain_matrix.json",
            )
        )
    if citation_ready_found:
        findings.append(
            Finding(
                "HIGH",
                "method citation support is marked citation_ready before manual review",
                "pipeline_core/literature/support_chain/model_route_support_chain_matrix.json",
            )
        )
    if excellent_as_method:
        findings.append(
            Finding(
                "HIGH",
                "excellent paper structure sample is treated as method citation",
                "pipeline_core/literature/support_chain/model_route_support_chain_matrix.json",
            )
        )

    summary = {
        "support_chain_source": chain_data.get("source_path", ""),
        "support_chain_context_found": not missing_contexts,
        "support_chain_required_rules_found": not missing_rules,
        "support_chain_contexts": {
            name: summarize_support_chain_context(context) for name, context in contexts.items()
        },
        "support_chain_formal_reference_allowed_found": formal_reference_allowed,
        "support_chain_citation_ready_found": citation_ready_found,
        "excellent_paper_as_method_citation_found": excellent_as_method,
    }
    return findings, summary


def evaluate_model_route_binding_quality() -> tuple[list[Finding], dict[str, Any]]:
    findings: list[Finding] = []
    chain_data = load_support_chain()
    cases = [
        {
            "case_id": "case_09_real_optimization_test",
            "case_dir": ROOT / "cases" / "case_09_real_optimization_test",
            "router_suggestion": "dynamic_scheduling",
            "expected_final_model_route": "optimization_planning",
        },
        {
            "case_id": "case_10_real_prediction_timeseries_test",
            "case_dir": ROOT / "cases" / "case_10_real_prediction_timeseries_test",
            "router_suggestion": "classification_clustering",
            "expected_final_model_route": "prediction_timeseries",
        },
        {
            "case_id": "case_11_real_evaluation_decision_test",
            "case_dir": ROOT / "cases" / "case_11_real_evaluation_decision_test",
            "router_suggestion": "evaluation_decision",
            "expected_final_model_route": "graph_network_spatial_coverage",
        },
        {
            "case_id": "active_competition_2017B_photo_pricing",
            "case_dir": ACTIVE_CASE,
            "router_suggestion": "mixed_problem",
            "expected_final_model_route": "mixed_problem",
        },
    ]
    bindings: dict[str, Any] = {}
    for item in cases:
        binding = resolve_model_route(item["case_dir"], router_suggestion=item["router_suggestion"])
        context = build_support_chain_context(
            problem_type=item["router_suggestion"],
            model_route=binding["final_model_route"],
            chain_data=chain_data,
        )
        bindings[item["case_id"]] = {
            "route_binding": binding,
            "support_chain_summary": summarize_support_chain_context(context),
            "excellent_paper_structure_rules": context.get("excellent_paper_structure_rules", []),
        }
        if binding["final_model_route"] != item["expected_final_model_route"]:
            findings.append(
                Finding(
                    "HIGH",
                    f"final_model_route mismatch for {item['case_id']}: "
                    f"{binding['final_model_route']} != {item['expected_final_model_route']}",
                    str(item["case_dir"]),
                )
            )
        if not context.get("support_chain_found", False):
            findings.append(
                Finding("MEDIUM", f"support_chain_context missing for {item['case_id']}", str(item["case_dir"]))
            )
        if not context.get("excellent_paper_structure_rules"):
            findings.append(
                Finding("MEDIUM", f"excellent paper structure rule missing for {item['case_id']}", str(item["case_dir"]))
            )
        if context.get("citation_ready", False) or context.get("formal_reference_allowed", False):
            findings.append(
                Finding("HIGH", f"citation boundary violated for {item['case_id']}", str(item["case_dir"]))
            )
        if context.get("excellent_paper_as_method_citation", False):
            findings.append(
                Finding("HIGH", f"structure sample treated as method citation for {item['case_id']}", str(item["case_dir"]))
            )

    summary = {
        "model_route_binding_priority_checked": True,
        "model_route_bindings": bindings,
        "model_route_binding_high_risk_found": any(item.level == "HIGH" for item in findings),
        "model_route_binding_medium_risk_found": any(item.level == "MEDIUM" for item in findings),
    }
    return findings, summary


def evaluate_n_paper_draft_loop_quality() -> tuple[list[Finding], dict[str, Any]]:
    findings: list[Finding] = []
    draft_path = PAPER_DRAFT_DIR / "N_draft_outline_or_sample.md"
    quality_gate_path = PAPER_DRAFT_DIR / "N_draft_quality_gate_report.md"
    citation_guard_path = PAPER_DRAFT_DIR / "N_citation_placeholder_and_reference_guard_report.md"
    human_gate_path = PAPER_DRAFT_DIR / "N_human_gate_and_final_submit_guard_report.md"
    remediation_path = PAPER_DRAFT_DIR / "N_draft_remediation_task_list.md"

    paths = [draft_path, quality_gate_path, citation_guard_path, human_gate_path, remediation_path]
    missing_paths = [path for path in paths if not path.exists()]
    for path in missing_paths:
        findings.append(Finding("MEDIUM", "N paper draft loop artifact missing", str(path)))

    combined_text = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in paths if path.exists())
    if draft_path.exists() and "DRAFT_ONLY" not in draft_path.read_text(encoding="utf-8", errors="ignore"):
        findings.append(Finding("HIGH", "N draft sample is missing DRAFT_ONLY marker", str(draft_path)))

    forbidden_patterns = {
        "official submission readiness true marker": r"official_submission_ready\s*[:=]\s*true",
        "final submit allowed true marker": r"final_submit_allowed\s*[:=]\s*true",
        "formal reference allowed true marker": r"formal_reference_allowed\s*[:=]\s*true",
        "citation ready true marker": r"citation_ready\s*[:=]\s*true",
        "human gate approved marker": r"human_gate_status\s*[:=]\s*APPROVED",
        "formal paper generated marker": r"formal_paper_generated\s*[:=]\s*true",
        "formal references generated marker": r"formal_references_generated\s*[:=]\s*true",
        "global optimum claim marker": r"global_optimum_claim\s*[:=]\s*true",
        "prediction as actual claim marker": r"prediction_as_actual_claim\s*[:=]\s*true",
    }
    for label, pattern in forbidden_patterns.items():
        if re.search(pattern, combined_text, flags=re.IGNORECASE):
            findings.append(Finding("HIGH", f"N draft loop contains forbidden {label}", "pipeline_core/paper_draft/"))

    required_terms = [
        "citation_ready=false",
        "formal_reference_allowed=false",
        "manual_review_required=true",
        "final_submit_allowed=false",
        "PENDING_MANUAL_REVIEW",
        "remediation_task_list_created",
    ]
    for term in required_terms:
        if term not in combined_text:
            findings.append(Finding("MEDIUM", f"N draft loop missing required guard term: {term}", "pipeline_core/paper_draft/"))

    summary = {
        "n_paper_draft_loop_checked": True,
        "n_draft_sample_found": draft_path.exists(),
        "n_draft_only_marker_found": draft_path.exists()
        and "DRAFT_ONLY" in draft_path.read_text(encoding="utf-8", errors="ignore"),
        "n_quality_gate_report_found": quality_gate_path.exists(),
        "n_citation_guard_report_found": citation_guard_path.exists(),
        "n_human_gate_report_found": human_gate_path.exists(),
        "n_remediation_task_list_found": remediation_path.exists(),
        "n_formal_upgrade_allowed": False,
        "n_final_submit_allowed": False,
        "n_human_gate_status": "PENDING_MANUAL_REVIEW",
        "n_draft_revision_required": True,
        "n_high_risk_found": any(item.level == "HIGH" for item in findings),
        "n_medium_risk_found": any(item.level == "MEDIUM" for item in findings),
    }
    return findings, summary


def main() -> int:
    EVAL_DIR.mkdir(parents=True, exist_ok=True)

    evaluations: list[CaseEvaluation] = []
    if STATUS_JSON.exists():
        status = read_json(STATUS_JSON)
        root_final_submit_exists = (ROOT / "final_submit").exists()
        evaluations = [evaluate_status_case(case, root_final_submit_exists) for case in status.get("cases", [])]
        (EVAL_DIR / "quality_evaluation_report.md").write_text(render_case_report(evaluations), encoding="utf-8")

    showcase_findings, showcase_summary = evaluate_active_showcase()
    support_findings, support_summary = evaluate_support_chain_quality()
    route_findings, route_summary = evaluate_model_route_binding_quality()
    draft_findings, draft_summary = evaluate_n_paper_draft_loop_quality()
    red_team_summary = run_u4_red_team_dry_run(write_output=True)
    showcase_findings.extend(support_findings)
    showcase_findings.extend(route_findings)
    showcase_findings.extend(draft_findings)
    showcase_summary.update(support_summary)
    showcase_summary.update(route_summary)
    showcase_summary.update(draft_summary)
    showcase_summary.update(
        {
            "u4_red_team_status": red_team_summary.get("status"),
            "u4_red_team_modes_checked": red_team_summary.get("modes_checked", []),
            "u4_draft_status": red_team_summary.get("draft_status"),
            "u4_submission_candidate_status": red_team_summary.get("submission_candidate_status"),
            "u4_official_submission_status": red_team_summary.get("official_submission_status"),
            "u4_final_submit_status": red_team_summary.get("final_submit_status"),
            "u4_high_award_status": red_team_summary.get("high_award_status"),
            "u4_formal_upgrade_allowed": red_team_summary.get("formal_upgrade_allowed"),
            "u4_final_submit_allowed": red_team_summary.get("final_submit_allowed"),
            "u4_red_team_manual_review_required": red_team_summary.get("manual_review_required"),
            "u4_red_team_global_asset_reuse_index_json_parse": red_team_summary.get(
                "global_asset_reuse_index_json_parse"
            ),
        }
    )
    if any(item.level == "HIGH" for item in support_findings + route_findings + draft_findings):
        showcase_summary["overall_status"] = "BLOCKED / SHOWCASE_QUALITY_GATE_SAFETY_VIOLATION"
    elif (support_findings or route_findings or draft_findings) and "BLOCKED" not in str(showcase_summary.get("overall_status", "")):
        showcase_summary["overall_status"] = "PASS_WITH_LIMITATION / SHOWCASE_QUALITY_GATE_WITH_SUPPORT_CHAIN_WARNINGS"
    (EVAL_DIR / "showcase_quality_evaluation_report.md").write_text(
        render_showcase_report(showcase_findings, showcase_summary),
        encoding="utf-8",
    )
    (EVAL_DIR / "showcase_quality_evaluation_summary.json").write_text(
        json.dumps(showcase_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(showcase_summary["overall_status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
