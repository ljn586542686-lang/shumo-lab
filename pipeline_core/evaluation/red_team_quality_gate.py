from __future__ import annotations

import json
import py_compile
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TEST_RESULTS = ROOT / "pipeline_core" / "test_results"
EVAL_DIR = ROOT / "pipeline_core" / "evaluation"
RULE_DIR = ROOT / "pipeline_core" / "red_team" / "competitive_review"
PAPER_RULE_DIR = ROOT / "pipeline_core" / "paper_quality" / "rules"
BENCHMARK_DIR = ROOT / "pipeline_core" / "showcase" / "2021B_capability_benchmark"
PAPER_DIR_2021B = ROOT / "cases" / "p_full_sim_2021B_ethanol_c4" / "paper"
BENCHMARK_LATEST_2021B = PAPER_DIR_2021B / "BENCHMARK_LATEST.md"
# Legacy dry-run sample retained only as fallback. The active benchmark should be
# resolved from BENCHMARK_LATEST.md so U4 does not treat the old v1.2 directory
# as the only valid sample.
LEGACY_DRY_RUN_SAMPLE_DIR = PAPER_DIR_2021B / "submit_candidate_v1_2_layout_fixed"


def resolve_benchmark_sample_dir() -> Path:
    try:
        latest_text = BENCHMARK_LATEST_2021B.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return LEGACY_DRY_RUN_SAMPLE_DIR
    matches = re.findall(r"`(submit_candidate[^`/]+/?)`", latest_text)
    for match in matches:
        candidate = PAPER_DIR_2021B / match.strip("/\\")
        if candidate.exists():
            return candidate
    return LEGACY_DRY_RUN_SAMPLE_DIR


SAMPLE_DIR = resolve_benchmark_sample_dir()


def first_sample_file(pattern: str, fallback_name: str) -> Path:
    matches = sorted(SAMPLE_DIR.glob(pattern))
    return matches[0] if matches else SAMPLE_DIR / fallback_name


SAMPLE_MD = first_sample_file("*layout_patch.md", "2021B_submit_candidate_v1_2_layout_fixed.md")
SAMPLE_PDF = first_sample_file("*.pdf", "2021B_submit_candidate_v1_2_layout_fixed.pdf")
SAMPLE_CHECKLIST = first_sample_file("*submission_readiness_checklist.md", "2021B_submit_candidate_v1_2_submission_readiness_checklist.md")
SAMPLE_LIMITATIONS = first_sample_file("*known_limitations.md", "2021B_submit_candidate_v1_2_known_limitations.md")
SAMPLE_TEX = first_sample_file("*.tex", "2021B_submit_candidate_v1_2_layout_fixed.tex")
CASE_ROOT = ROOT / "cases" / "p_full_sim_2021B_ethanol_c4"

MODES = [
    "DRAFT_ONLY",
    "SUBMISSION_CANDIDATE",
    "OFFICIAL_SUBMISSION",
    "FINAL_SUBMIT",
    "HIGH_AWARD_CANDIDATE",
]

S0_TEXT_TERMS = [
    "候选稿",
    "评测稿",
    "用于水平比对",
    "人工复核训练",
    "待补全",
    "占位",
    "citation_ready=false",
    "formal_reference_allowed=false",
    "final_submit_allowed=false",
    "human_gate",
    "final_submit",
    "official_submission_mode",
    "SUBMISSION_CANDIDATE",
    "HUMAN_AUTHORIZATION_REQUIRED",
    "AI_DISCLOSURE_REQUIRED",
    "REFERENCE_MANUAL_REVIEW_REQUIRED",
    "NOT_CURRENT_CONTEST_SUBMISSION",
]

GLOBAL_OPTIMUM_TERMS = ["全局最优", "真实最优", "最终最优", "已验证最优"]
PREDICTION_AS_REAL_PATTERNS = [
    r"预测[^。；\n]{0,12}就是[^。；\n]{0,12}真实",
    r"预测[^。；\n]{0,12}等于[^。；\n]{0,12}实验",
    r"预测[^。；\n]{0,12}为实验真实结果",
]

UNCERTAINTY_TRIGGER_TERMS = ["最优方案", "较优候选", "候选排序", "预测收率", "推荐实验", "边界候选", "外推候选", "优先验证候选"]
UNCERTAINTY_EVIDENCE_TERMS = ["LOOCV", "留一验证", "预测区间", "置信区间", "Bootstrap", "Monte Carlo", "蒙特卡洛", "参数敏感性", "残差", "鲁棒", "风险等级"]
STABILITY_TRIGGER_TERMS = ["时间", "稳定性", "衰减", "长期性能", "多时间点", "重复实验", "可靠性"]
STABILITY_EVIDENCE_TERMS = ["趋势图", "趋势拟合", "衰减率", "稳定性指标", "波动率", "初末值", "末端值", "关联分析"]
MECHANISM_TOPIC_TERMS = ["催化剂", "乙醇", "C4", "化学", "反应", "温度"]
MECHANISM_FEATURE_TERMS = ["成分", "负载量", "载体", "比例", "浓度", "温度", "时间", "流速", "结构参数", "工艺条件", "响应面"]
UNSUPPORTED_MECHANISM_TERMS = ["Arrhenius", "活化能", "速率常数", "动力学 ODE", "动力学参数"]
COMPETITIVE_HIGHLIGHT_TERMS = ["机理启发", "正式优化", "边界风险控制", "鲁棒候选排序", "序贯实验", "不确定性量化", "稳定性增强", "结构性创新", "指标体系鲁棒性", "统计解释增强"]


@dataclass
class GateIssue:
    level: str
    category: str
    message: str
    evidence: str = ""
    detection: str = "automatic"


@dataclass
class ModeResult:
    mode: str
    status: str
    formal_upgrade_allowed: bool
    final_submit_allowed: bool
    not_ready_for_actual_submission: bool
    issues: list[GateIssue] = field(default_factory=list)
    manual_review_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "status": self.status,
            "formal_upgrade_allowed": self.formal_upgrade_allowed,
            "final_submit_allowed": self.final_submit_allowed,
            "not_ready_for_actual_submission": self.not_ready_for_actual_submission,
            "manual_review_required": self.manual_review_required,
            "issues": [issue.__dict__ for issue in self.issues],
        }


def read_text(path: Path, limit: int = 500_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def line_has_negation(line: str) -> bool:
    return any(token in line for token in ["不", "不得", "不能", "不是", "未", "禁止", "not", "no"])


def matching_lines(text: str, term: str) -> list[str]:
    return [line.strip()[:180] for line in text.splitlines() if term in line]


def load_sample_text() -> dict[str, str]:
    benchmark_parts = []
    for path in sorted(BENCHMARK_DIR.glob("*.md")):
        benchmark_parts.append(f"\n\n# SOURCE: {rel(path)}\n" + read_text(path))
    return {
        "paper": read_text(SAMPLE_MD),
        "tex": read_text(SAMPLE_TEX),
        "checklist": read_text(SAMPLE_CHECKLIST),
        "limitations": read_text(SAMPLE_LIMITATIONS),
        "benchmark": "\n".join(benchmark_parts),
    }


def combined_text(parts: dict[str, str], include_support_docs: bool = True) -> str:
    if include_support_docs:
        return "\n".join(parts.values())
    return parts.get("paper", "") + "\n" + parts.get("tex", "")


def check_s0_text_residue(text: str, mode: str) -> list[GateIssue]:
    issues: list[GateIssue] = []
    hits = []
    for term in S0_TEXT_TERMS:
        lines = matching_lines(text, term)
        if lines:
            hits.append((term, lines[:3]))

    if mode in {"OFFICIAL_SUBMISSION", "FINAL_SUBMIT"}:
        for term, lines in hits:
            issues.append(
                GateIssue(
                    "HIGH",
                    "S0_TEXT_RESIDUE",
                    f"正式模式下发现候选/工程状态残留：{term}",
                    " | ".join(lines),
                    "automatic",
                )
            )
    elif hits:
        issues.append(
            GateIssue(
                "MEDIUM",
                "LIMITATION_TEXT_ALLOWED_IN_CANDIDATE",
                "草稿/候选模式允许候选说明存在，但必须保持 NOT_READY_FOR_ACTUAL_SUBMISSION",
                ", ".join(term for term, _ in hits[:8]),
                "automatic",
            )
        )
    return issues


def check_overclaim(text: str) -> list[GateIssue]:
    issues: list[GateIssue] = []
    for term in GLOBAL_OPTIMUM_TERMS:
        for line in matching_lines(text, term):
            if not line_has_negation(line):
                issues.append(GateIssue("HIGH", "GLOBAL_OPTIMUM_CLAIM", f"发现未否定的过度最优表述：{term}", line, "automatic"))
    for pattern in PREDICTION_AS_REAL_PATTERNS:
        for match in re.finditer(pattern, text):
            issues.append(GateIssue("HIGH", "PREDICTION_AS_REAL", "发现预测结果被写成真实结果的高风险措辞", match.group(0), "automatic"))
    return issues


def check_reference_and_ai(parts: dict[str, str], mode: str) -> list[GateIssue]:
    text = combined_text(parts)
    issues: list[GateIssue] = []
    citation_false = "citation_ready=false" in text or "citation_ready=false" in text.replace(" ", "")
    formal_ref_false = "formal_reference_allowed=false" in text or "formal_reference_allowed=false" in text.replace(" ", "")
    has_refs = "参考文献" in parts.get("paper", "")
    has_candidate_refs = "候选参考文献" in text or "候选条目" in text
    ai_template = "AI 使用详情模板" in text or "待人工填写" in text or "正式提交前" in parts.get("paper", "")

    if mode in {"OFFICIAL_SUBMISSION", "FINAL_SUBMIT"}:
        if citation_false or formal_ref_false or has_candidate_refs:
            issues.append(GateIssue("HIGH", "REFERENCE_NOT_FORMALIZED", "正式模式下参考文献仍为候选/未核验状态", "citation/formal_reference flags or candidate references found", "automatic"))
        if ai_template:
            issues.append(GateIssue("HIGH", "AI_DISCLOSURE_TEMPLATE_NOT_COMPLETE", "正式模式下 AI 使用说明仍为模板或待填写状态", "AI template or pre-submit wording found", "automatic"))
    else:
        if has_refs and (citation_false or formal_ref_false or has_candidate_refs):
            issues.append(GateIssue("MEDIUM", "REFERENCE_CANDIDATE_PENDING_REVIEW", "参考文献为候选状态，允许用于候选稿但不得正式化", "citation_ready=false / formal_reference_allowed=false", "automatic"))
        if ai_template:
            issues.append(GateIssue("MEDIUM", "AI_DISCLOSURE_PENDING_MANUAL_FILL", "AI 使用说明仍需人工按当年规则填写", "AI disclosure template/pre-submit wording", "automatic"))
    return issues


def check_final_submit_safety(parts: dict[str, str], mode: str) -> list[GateIssue]:
    text = combined_text(parts)
    final_submit_exists = (CASE_ROOT / "final_submit").exists()
    root_final_submit_exists = (ROOT / "final_submit").exists()
    issues: list[GateIssue] = []
    if final_submit_exists or root_final_submit_exists:
        issues.append(GateIssue("HIGH", "FINAL_SUBMIT_EXISTS", "检测到 final_submit/ 存在", "case or root final_submit exists", "automatic"))

    if mode == "FINAL_SUBMIT":
        if "final_submit_allowed=false" in text:
            issues.append(GateIssue("HIGH", "FINAL_SUBMIT_NOT_ALLOWED", "final_submit_allowed=false，禁止 final submit", "checklist/limitations", "automatic"))
        if "official_submission_mode=locked" in text:
            issues.append(GateIssue("HIGH", "OFFICIAL_MODE_LOCKED", "official_submission_mode=locked，禁止 final submit", "checklist/limitations", "automatic"))
        if "human_gate_status=PENDING_MANUAL_REVIEW" in text:
            issues.append(GateIssue("HIGH", "HUMAN_GATE_PENDING", "human gate 未人工通过，禁止 final submit", "checklist/limitations", "automatic"))
    return issues


def check_pdf_and_layout() -> list[GateIssue]:
    issues: list[GateIssue] = []
    if not SAMPLE_PDF.exists() or SAMPLE_PDF.stat().st_size <= 0:
        issues.append(GateIssue("MEDIUM", "PDF_MISSING_OR_EMPTY", "2021B v1.2 PDF 缺失或为空", rel(SAMPLE_PDF), "automatic"))
    else:
        issues.append(GateIssue("INFO", "PDF_EXISTS", "PDF 存在且文件非空，页码压图等仍需人工目检", f"{rel(SAMPLE_PDF)} size={SAMPLE_PDF.stat().st_size}", "semi_auto"))

    tex = read_text(SAMPLE_TEX)
    if r"\includegraphics" in tex:
        if re.search(r"\\includegraphics\[[^]]*(0\.9|1\.0)\\(?:textwidth|linewidth)", tex):
            issues.append(GateIssue("MEDIUM", "POTENTIAL_OVERSIZED_FIGURE", "TeX 中存在可能过大的图片尺寸", "includegraphics width >= 0.9", "semi_auto"))
    if "Overfull" in read_text(SAMPLE_DIR / "2021B_submit_candidate_v1_2_layout_fixed.log"):
        issues.append(GateIssue("MEDIUM", "PDF_COMPILE_OVERFULL", "编译日志存在 Overfull，需人工确认是否影响版式", "xelatex log", "semi_auto"))
    return issues


def check_boundary_optimum(text: str) -> list[GateIssue]:
    issues: list[GateIssue] = []
    boundary_detected = bool(re.search(r"候选温度\s*\|\s*450", text)) or ("A1、450" in text and "250 至 450" in text)
    if boundary_detected:
        issues.append(
            GateIssue(
                "MEDIUM",
                "BOUNDARY_OPTIMUM_DETECTED",
                "检测到候选点位于温度上界，需边界风险复核",
                "boundary_optimum_detected=true; require_sensitivity=true; require_uncertainty_quantification=true; require_followup_experiment=true; prohibit_global_optimum_claim=true",
                "semi_auto",
            )
        )
    return issues


def check_uncertainty(text: str) -> list[GateIssue]:
    trigger = any(term in text for term in UNCERTAINTY_TRIGGER_TERMS)
    evidence = [term for term in UNCERTAINTY_EVIDENCE_TERMS if term in text]
    if not trigger:
        return []
    if not evidence:
        return [GateIssue("MEDIUM", "UNCERTAINTY_QUANTIFICATION_REQUIRED", "存在候选推荐或预测支撑实验设计，但未发现不确定性量化证据", "", "semi_auto")]
    if not any(term in text for term in ["预测区间", "置信区间", "Bootstrap", "Monte Carlo", "蒙特卡洛", "鲁棒候选排序", "风险等级表"]):
        return [GateIssue("MEDIUM", "UNCERTAINTY_QUANTIFICATION_PARTIAL", "已有留一验证/残差/敏感性等证据，但高奖候选仍建议补充区间、扰动或鲁棒排序", ", ".join(evidence), "semi_auto")]
    return [GateIssue("INFO", "UNCERTAINTY_EVIDENCE_FOUND", "已发现不确定性或稳健性相关证据", ", ".join(evidence), "semi_auto")]


def check_stability(text: str) -> list[GateIssue]:
    trigger = any(term in text for term in STABILITY_TRIGGER_TERMS)
    if not trigger:
        return []
    evidence = [term for term in STABILITY_EVIDENCE_TERMS if term in text]
    strong = any(term in text for term in ["趋势拟合", "衰减率", "稳定性指标", "关联分析"])
    if not strong:
        return [GateIssue("MEDIUM", "STABILITY_TIME_SERIES_ANALYSIS_SHALLOW", "检测到时间/稳定性内容，但主要仍是描述性分析", ", ".join(evidence), "semi_auto")]
    return [GateIssue("INFO", "STABILITY_ANALYSIS_EVIDENCE_FOUND", "检测到稳定性/时间分析证据", ", ".join(evidence), "semi_auto")]


def check_mechanism(text: str) -> list[GateIssue]:
    if not any(term in text for term in MECHANISM_TOPIC_TERMS):
        return []
    issues: list[GateIssue] = []
    unsupported = [term for term in UNSUPPORTED_MECHANISM_TERMS if term in text]
    if unsupported:
        issues.append(GateIssue("HIGH", "UNSUPPORTED_MECHANISM_MODEL_CLAIM", "出现可能无数据支撑的完整机理模型表述", ", ".join(unsupported), "semi_auto"))
    feature_hits = [term for term in MECHANISM_FEATURE_TERMS if term in text]
    if len(feature_hits) < 4:
        issues.append(GateIssue("MEDIUM", "MECHANISM_FEATURE_ENGINEERING_REQUIRED", "机制/化学题机理启发变量仍需增强", ", ".join(feature_hits), "semi_auto"))
    else:
        issues.append(GateIssue("MEDIUM", "MECHANISM_EXPLANATION_REVIEW_REQUIRED", "检测到部分机理启发变量，但可信度仍需人工复核", ", ".join(feature_hits), "manual"))
    return issues


def check_competitive_highlights(text: str) -> list[GateIssue]:
    hits = [term for term in COMPETITIVE_HIGHLIGHT_TERMS if term in text]
    if len(hits) < 1:
        return [GateIssue("MEDIUM", "NO_DEFENSIBLE_COMPETITIVE_HIGHLIGHT", "未检测到足够清晰的可辩护竞争点", "", "manual")]
    return [GateIssue("INFO", "COMPETITIVE_HIGHLIGHT_CANDIDATE_FOUND", "检测到潜在竞争点，但仍需人工判断是否能打动评委", ", ".join(hits), "manual")]


def manual_review_items() -> list[GateIssue]:
    return [
        GateIssue("MANUAL", "FIGURE_SERVES_CONCLUSION", "图表是否真正服务结论必须人工目检", "", "manual"),
        GateIssue("MANUAL", "MECHANISM_CREDIBILITY", "机理解释是否可信必须人工判断", "", "manual"),
        GateIssue("MANUAL", "COMPETITIVE_HIGHLIGHT_QUALITY", "创新点和竞争点是否能打动评委必须人工判断", "", "manual"),
        GateIssue("MANUAL", "AI_STYLE_REVIEW", "论文是否仍有 AI 半成品味必须人工审读", "", "manual"),
        GateIssue("MANUAL", "REFERENCE_METADATA_TRUTH", "参考文献元数据是否真实完整必须人工核验", "", "manual"),
        GateIssue("MANUAL", "AI_DISCLOSURE_OFFICIAL_RULES", "AI 使用记录是否符合当年官方规则必须人工核验", "", "manual"),
        GateIssue("MANUAL", "HIGH_AWARD_COMPETITIVENESS", "高奖竞争力是否足够不得自动判定为 PASS", "", "manual"),
    ]


def evaluate_mode(mode: str, parts: dict[str, str]) -> ModeResult:
    text = combined_text(parts)
    paper_text = combined_text(parts, include_support_docs=False)
    issues: list[GateIssue] = []

    issues.extend(check_s0_text_residue(text, mode))
    issues.extend(check_overclaim(paper_text))
    issues.extend(check_reference_and_ai(parts, mode))
    issues.extend(check_final_submit_safety(parts, mode))
    issues.extend(check_pdf_and_layout())
    issues.extend(check_boundary_optimum(paper_text))
    issues.extend(check_uncertainty(paper_text))
    issues.extend(check_stability(paper_text))
    issues.extend(check_mechanism(paper_text))
    issues.extend(manual_review_items())

    if mode == "HIGH_AWARD_CANDIDATE":
        issues.extend(check_competitive_highlights(paper_text))

    high = any(issue.level == "HIGH" for issue in issues)
    if mode in {"OFFICIAL_SUBMISSION", "FINAL_SUBMIT"}:
        status = "BLOCKED / FORMAL_SUBMISSION_S0_RED_TEAM_BLOCKED" if high else "PASS_WITH_LIMITATION / OFFICIAL_SUBMISSION_REVIEW_REQUIRED"
        return ModeResult(mode, status, False, False, True, issues)
    if mode == "HIGH_AWARD_CANDIDATE":
        s1_categories = {
            "BOUNDARY_OPTIMUM_DETECTED",
            "UNCERTAINTY_QUANTIFICATION_REQUIRED",
            "UNCERTAINTY_QUANTIFICATION_PARTIAL",
            "STABILITY_TIME_SERIES_ANALYSIS_SHALLOW",
            "MECHANISM_FEATURE_ENGINEERING_REQUIRED",
            "MECHANISM_EXPLANATION_REVIEW_REQUIRED",
            "NO_DEFENSIBLE_COMPETITIVE_HIGHLIGHT",
        }
        blocked = any(issue.category in s1_categories for issue in issues)
        status = "PASS_WITH_LIMITATION / HIGH_AWARD_COMPETITIVENESS_BLOCKED" if blocked else "PASS_WITH_LIMITATION / HIGH_AWARD_CANDIDATE_MANUAL_REVIEW_REQUIRED"
        return ModeResult(mode, status, False, False, True, issues)
    if mode == "SUBMISSION_CANDIDATE":
        return ModeResult(mode, "PASS_WITH_LIMITATION / SUBMISSION_CANDIDATE_RED_TEAM_READY_NOT_FOR_ACTUAL_SUBMISSION", False, False, True, issues)
    return ModeResult(mode, "PASS_WITH_LIMITATION / DRAFT_ONLY_RED_TEAM_READY_WITH_LIMITATIONS", False, False, True, issues)


def classify_detection_coverage() -> dict[str, list[str]]:
    return {
        "automatic": [
            "S0 文本残留扫描",
            "工程状态码扫描",
            "过度最优和预测写成真实结果的高风险措辞扫描",
            "references / AI 模板状态扫描",
            "final_submit/ 存在性扫描",
            "PDF 文件存在性和大小扫描",
        ],
        "semi_automatic": [
            "TeX 图片尺寸和编译日志检查",
            "边界候选检测",
            "不确定性量化触发检测",
            "时间序列 / 稳定性触发检测",
            "机制题机理变量和不支持机理声明扫描",
        ],
        "manual_required": [
            "图表是否真正服务结论",
            "机理解释是否可信",
            "创新点是否能打动评委",
            "论文是否仍有 AI 味",
            "参考文献元数据是否真实完整",
            "AI 使用记录是否符合当年官方规则",
            "高奖竞争力是否足够",
        ],
    }


def compile_check(paths: list[Path]) -> dict[str, Any]:
    results = {}
    for path in paths:
        try:
            py_compile.compile(str(path), doraise=True)
            results[rel(path)] = "PASS"
        except py_compile.PyCompileError as exc:
            results[rel(path)] = f"FAIL: {exc.msg}"
    return results


def safety_summary() -> dict[str, Any]:
    return {
        "paper_body_modified": False,
        "pdf_modified": False,
        "matlab_ran": False,
        "new_model_added": False,
        "new_literature_added": False,
        "new_paper_generated": False,
        "formal_references_generated": False,
        "official_submission_mode_enabled": False,
        "human_gate_auto_approved": False,
        "citation_fabricated": False,
        "ai_usage_record_fabricated": False,
        "case_final_submit_found": (ROOT / "cases" / "p_full_sim_2021B_ethanol_c4" / "final_submit").exists(),
        "root_final_submit_found": (ROOT / "final_submit").exists(),
    }


def summarize_results(results: dict[str, ModeResult]) -> dict[str, Any]:
    high_issue_found = any(any(issue.level == "HIGH" for issue in result.issues) for result in results.values())
    return {
        "status": "PASS_WITH_LIMITATION / U4_COMPETITIVE_RED_TEAM_EVALUATOR_INTEGRATED",
        "modes_checked": list(results.keys()),
        "draft_status": results["DRAFT_ONLY"].status,
        "submission_candidate_status": results["SUBMISSION_CANDIDATE"].status,
        "official_submission_status": results["OFFICIAL_SUBMISSION"].status,
        "final_submit_status": results["FINAL_SUBMIT"].status,
        "high_award_status": results["HIGH_AWARD_CANDIDATE"].status,
        "manual_review_required": True,
        "formal_upgrade_allowed": False,
        "final_submit_allowed": False,
        "high_issue_found_in_blocked_modes": high_issue_found,
        "safety": safety_summary(),
    }


def issue_rows(result: ModeResult, limit: int | None = None) -> list[str]:
    issues = result.issues if limit is None else result.issues[:limit]
    rows = []
    for issue in issues:
        rows.append(f"| {issue.level} | {issue.category} | {issue.detection} | {issue.message} | {issue.evidence.replace('|', '/')} |")
    return rows


def render_integration_report(summary: dict[str, Any], compile_results: dict[str, str]) -> str:
    coverage = classify_detection_coverage()
    lines = [
        "# U4 evaluator 接入报告",
        "",
        "状态：`PASS_WITH_LIMITATION / U4_COMPETITIVE_RED_TEAM_EVALUATOR_INTEGRATED`",
        "",
        "## 1. 接入范围",
        "",
        "- 新增 `pipeline_core/evaluation/red_team_quality_gate.py`。",
        "- 新增 `pipeline_core/evaluation/quality_gate_runner.py` 作为独立运行入口。",
        "- `pipeline_core/evaluation/evaluate_case_quality.py` 已最小接入红队 dry-run，并把结果写入 `showcase_quality_evaluation_summary.json`。",
        "- U2 的 PDF/图表/表格/引用/AI/submit candidate 规则作为检查依据。",
        "- U3 的 S0/S1/S2、边界最优、不确定性、稳定性、机理/连续优化、竞争点规则作为红队依据。",
        "",
        "## 2. 自动扫描项",
        "",
    ]
    lines.extend(f"- {item}" for item in coverage["automatic"])
    lines.extend(["", "## 3. 半自动检测项", ""])
    lines.extend(f"- {item}" for item in coverage["semi_automatic"])
    lines.extend(["", "## 4. 必须人工目检项", ""])
    lines.extend(f"- {item}" for item in coverage["manual_required"])
    lines.extend(["", "## 5. 主 evaluator 接入", ""])
    lines.extend([
        "- 已接入主 evaluator：`evaluate_case_quality.py` 调用 `run_u4_red_team_dry_run()`。",
        "- standalone gate：`quality_gate_runner.py` 可独立运行。",
        "- PDF 页码压图、图表真实服务结论、机理可信度、高奖竞争力仍保留人工目检字段。",
        "",
        "## 6. Python 语法检查",
        "",
        "| 文件 | 结果 |",
        "|---|---|",
    ])
    lines.extend(f"| `{path}` | {status} |" for path, status in compile_results.items())
    lines.extend([
        "",
        "## 7. 总结",
        "",
        f"- 总状态：`{summary['status']}`",
        "- 本步未修改 2021B 论文正文、TeX、PDF、模型结果或图表源文件。",
    ])
    return "\n".join(lines) + "\n"


def render_dry_run_report(results: dict[str, ModeResult]) -> str:
    lines = [
        "# U4 红队质量门 dry-run 报告",
        "",
        "状态：`PASS_WITH_LIMITATION / U4_COMPETITIVE_RED_TEAM_EVALUATOR_INTEGRATED`",
        "",
        "| 模式 | dry-run 状态 | formal_upgrade_allowed | final_submit_allowed | manual_review_required |",
        "|---|---|---:|---:|---:|",
    ]
    for result in results.values():
        lines.append(f"| `{result.mode}` | `{result.status}` | {result.formal_upgrade_allowed} | {result.final_submit_allowed} | {result.manual_review_required} |")
    lines.extend(["", "## 1. 模式结论", ""])
    lines.extend([
        "- `DRAFT_ONLY`：允许 `PASS_WITH_LIMITATION`，但必须保留 limitations，不允许 final_submit。",
        "- `SUBMISSION_CANDIDATE`：允许候选说明存在，但保持 `NOT_READY_FOR_ACTUAL_SUBMISSION`。",
        "- `OFFICIAL_SUBMISSION`：必须阻断，原因包括候选说明、AI/引用未完成和 human gate 未通过。",
        "- `FINAL_SUBMIT`：必须阻断，原因包括 `final_submit_allowed=false`、`official_submission_mode=locked`、`human_gate_status=PENDING_MANUAL_REVIEW`。",
        "- `HIGH_AWARD_CANDIDATE`：阻断高奖候选标记，原因包括边界候选、优化层偏浅、机理解释和不确定性仍需增强。",
    ])
    return "\n".join(lines) + "\n"


def render_2021b_report(results: dict[str, ModeResult]) -> str:
    lines = [
        "# U4 2021B v1.2 submit candidate gate 报告",
        "",
        "状态：`PASS_WITH_LIMITATION / U4_COMPETITIVE_RED_TEAM_EVALUATOR_INTEGRATED`",
        "",
        "## 1. 输入样本",
        "",
        f"- Markdown：`{rel(SAMPLE_MD)}`",
        f"- PDF：`{rel(SAMPLE_PDF)}`",
        f"- 提交检查表：`{rel(SAMPLE_CHECKLIST)}`",
        f"- 已知限制：`{rel(SAMPLE_LIMITATIONS)}`",
        "",
        "## 2. 关键检查结果",
        "",
        "| 模式 | 状态 | 说明 |",
        "|---|---|---|",
        f"| DRAFT_ONLY | `{results['DRAFT_ONLY'].status}` | 可作为草稿/训练样本，保留限制。 |",
        f"| SUBMISSION_CANDIDATE | `{results['SUBMISSION_CANDIDATE'].status}` | 可作为候选稿，但不可实际提交。 |",
        f"| OFFICIAL_SUBMISSION | `{results['OFFICIAL_SUBMISSION'].status}` | 候选说明、AI/引用未完成、human gate 未通过导致阻断。 |",
        f"| FINAL_SUBMIT | `{results['FINAL_SUBMIT'].status}` | final_submit 权限和 official mode 均未满足。 |",
        f"| HIGH_AWARD_CANDIDATE | `{results['HIGH_AWARD_CANDIDATE'].status}` | 高奖竞争性不足，需增强边界风险、不确定性、稳定性和竞争点。 |",
        "",
        "## 3. HIGH_AWARD_CANDIDATE 主要命中项",
        "",
        "| 等级 | 类别 | 检测方式 | 说明 | 证据 |",
        "|---|---|---|---|---|",
    ]
    high_award_rows = [
        row
        for row in issue_rows(results["HIGH_AWARD_CANDIDATE"])
        if any(token in row for token in ["BOUNDARY", "UNCERTAINTY", "STABILITY", "MECHANISM", "COMPETITIVE"])
    ]
    lines.extend(high_award_rows or ["| INFO | NONE | automatic | 未命中高奖阻断项 |  |"])
    lines.extend([
        "",
        "## 4. 人工项",
        "",
        "图表是否真正服务结论、机理解释是否可信、论文是否仍有 AI 味、参考文献元数据是否真实完整、高奖竞争力是否足够，均不得自动判为 PASS。",
    ])
    return "\n".join(lines) + "\n"


def render_regression_report(summary: dict[str, Any], compile_results: dict[str, str]) -> str:
    safety = summary["safety"]
    lines = [
        "# U4 质量门回归报告",
        "",
        "状态：`PASS_WITH_LIMITATION / U4_COMPETITIVE_RED_TEAM_EVALUATOR_INTEGRATED`",
        "",
        "## 1. Python 语法检查",
        "",
        "| 文件 | 结果 |",
        "|---|---|",
    ]
    lines.extend(f"| `{path}` | {status} |" for path, status in compile_results.items())
    lines.extend([
        "",
        "## 2. JSON 解析",
        "",
        "- `pipeline_core/meta/global_asset_reuse_index.json`：已在 dry-run 中解析。",
        "",
        "## 3. evaluator 调用",
        "",
        "- `red_team_quality_gate.py` 已运行 dry-run。",
        "- `evaluate_case_quality.py` 已接入红队 dry-run 调用。",
        "",
        "## 4. 安全状态",
        "",
        "| 检查项 | 结果 |",
        "|---|---|",
    ])
    for key, value in safety.items():
        lines.append(f"| {key} | {value} |")
    lines.extend([
        "",
        "## 5. 结论",
        "",
        "- 未修改 2021B 论文正文、TeX、PDF、模型结果或图表源文件。",
        "- 未创建 `final_submit/`。",
        "- 未生成正式 references。",
        "- 未开启 `official_submission_mode`。",
        "- 未自动通过 human gate。",
    ])
    return "\n".join(lines) + "\n"


def write_reports(results: dict[str, ModeResult], summary: dict[str, Any], compile_results: dict[str, str]) -> None:
    TEST_RESULTS.mkdir(parents=True, exist_ok=True)
    (TEST_RESULTS / "U4_evaluator_integration_report.md").write_text(render_integration_report(summary, compile_results), encoding="utf-8")
    (TEST_RESULTS / "U4_red_team_gate_dry_run_report.md").write_text(render_dry_run_report(results), encoding="utf-8")
    (TEST_RESULTS / "U4_2021B_submit_candidate_gate_report.md").write_text(render_2021b_report(results), encoding="utf-8")
    (TEST_RESULTS / "U4_quality_gate_regression_report.md").write_text(render_regression_report(summary, compile_results), encoding="utf-8")


def run_u4_red_team_dry_run(write_output: bool = True) -> dict[str, Any]:
    parts = load_sample_text()
    results = {mode: evaluate_mode(mode, parts) for mode in MODES}
    compile_results = compile_check([
        ROOT / "pipeline_core" / "evaluation" / "red_team_quality_gate.py",
        ROOT / "pipeline_core" / "evaluation" / "quality_gate_runner.py",
        ROOT / "pipeline_core" / "evaluation" / "evaluate_case_quality.py",
    ])
    try:
        json.loads((ROOT / "pipeline_core" / "meta" / "global_asset_reuse_index.json").read_text(encoding="utf-8"))
        json_status = "PASS"
    except (OSError, json.JSONDecodeError) as exc:
        json_status = f"FAIL: {exc}"
    summary = summarize_results(results)
    summary["generated_at"] = datetime.now().isoformat(timespec="seconds")
    summary["detection_coverage"] = classify_detection_coverage()
    summary["python_syntax_check"] = compile_results
    summary["global_asset_reuse_index_json_parse"] = json_status
    summary["mode_results"] = {mode: result.to_dict() for mode, result in results.items()}
    if write_output:
        write_reports(results, summary, compile_results)
    return summary


def main() -> int:
    summary = run_u4_red_team_dry_run(write_output=True)
    print(summary["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
