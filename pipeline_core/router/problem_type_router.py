#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MathModeling2026 题型路由器 baseline。

功能：
    读取当前 case 的题面抽取、数据审计、字段映射和模型库注册表，
    输出题型候选、建模路线建议、验证建议、论文结构建议和风险提示。

使用：
    python pipeline_core/router/problem_type_router.py --case-dir cases/active_competition/current_case --dry-run
    python pipeline_core/router/problem_type_router.py --case-dir cases/active_competition/current_case --write

边界：
    本脚本只做路由建议，不运行模型、不生成论文、不创建 final_submit。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_FOR_IMPORT = Path(__file__).resolve().parents[2]
if str(ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(ROOT_FOR_IMPORT))

from pipeline_core.literature.support_chain.support_chain_loader import (
    build_support_chain_context,
    load_support_chain,
    normalize_route,
    summarize_support_chain_context,
)
from pipeline_core.model_route.route_priority import resolve_model_route


CANONICAL_TYPES = [
    "geometry_inverse_localization",
    "statistical_analysis",
    "prediction_timeseries",
    "optimization_planning",
    "evaluation_decision",
    "graph_network_spatial",
    "dynamic_scheduling",
    "mechanism_fitting",
    "classification_clustering",
    "multi_objective_decision",
    "mixed_problem",
]

REGISTRY_ORDER_TO_TYPE = {
    0: "statistical_analysis",
    1: "prediction_timeseries",
    2: "optimization_planning",
    3: "evaluation_decision",
    4: "graph_network_spatial",
    5: "dynamic_scheduling",
    6: "mechanism_fitting",
    7: "classification_clustering",
    8: "multi_objective_decision",
    9: "mixed_problem",
}

TYPE_DISPLAY_NAME = {
    "geometry_inverse_localization": "空间几何 / 反问题 / 定位类",
    "statistical_analysis": "统计分析类",
    "prediction_timeseries": "预测 / 时间序列 / 完成概率类",
    "optimization_planning": "优化规划类",
    "evaluation_decision": "评价决策类",
    "graph_network_spatial": "图网络 / 空间分析类",
    "dynamic_scheduling": "动态调度类",
    "mechanism_fitting": "机理拟合类",
    "classification_clustering": "分类聚类类",
    "multi_objective_decision": "多目标决策类",
    "mixed_problem": "混合型问题",
}

TYPE_KEYWORDS = {
    "geometry_inverse_localization": [
        "纯方位",
        "无源定位",
        "方向信息",
        "方向夹角",
        "定位模型",
        "位置调整",
        "编队",
        "圆周",
        "锥形",
        "可辨识",
        "几何约束",
    ],
    "statistical_analysis": [
        "规律",
        "原因",
        "统计",
        "相关性",
        "差异",
        "完成情况",
        "分析",
        "描述统计",
    ],
    "prediction_timeseries": [
        "预测",
        "未来",
        "时间序列",
        "趋势",
        "完成概率",
        "实施效果",
        "新项目",
        "预订任务开始时间",
    ],
    "optimization_planning": [
        "优化",
        "定价方案",
        "新的任务定价",
        "最优",
        "最大化",
        "最小化",
        "成本",
        "约束",
        "目标函数",
        "方案比较",
        "价格上界",
        "价格下界",
    ],
    "evaluation_decision": [
        "评价",
        "指标",
        "权重",
        "排序",
        "综合评价",
        "信誉值",
        "实施效果评价",
    ],
    "graph_network_spatial": [
        "gps",
        "经度",
        "纬度",
        "位置",
        "距离",
        "空间",
        "密度",
        "附近会员",
        "最近会员",
        "节点",
        "边",
        "路网",
    ],
    "dynamic_scheduling": [
        "调度",
        "动态",
        "工序",
        "设备",
        "等待",
        "吞吐",
        "排程",
    ],
    "mechanism_fitting": [
        "机理",
        "物理",
        "曲线",
        "拟合",
        "微分方程",
        "参数估计",
        "边界条件",
    ],
    "classification_clustering": [
        "分类",
        "聚类",
        "类别",
        "识别",
        "分组",
        "打包",
        "空间聚类",
        "完成标记",
        "0/1",
    ],
    "multi_objective_decision": [
        "多目标",
        "权衡",
        "成本",
        "完成率",
        "效果",
        "公平",
        "风险",
        "平均涨价幅度",
    ],
    "mixed_problem": [
        "问题1",
        "问题2",
        "问题3",
        "问题4",
        "附件一",
        "附件二",
        "附件三",
        "综合",
        "多个问题",
        "统计类",
        "优化类",
        "空间",
        "定价",
        "打包",
    ],
}

FIELD_HINTS = {
    "task_price": ["statistical_analysis", "optimization_planning", "multi_objective_decision"],
    "task_finished": ["statistical_analysis", "prediction_timeseries", "classification_clustering"],
    "task_lng": ["graph_network_spatial"],
    "task_lat": ["graph_network_spatial"],
    "member_lng": ["graph_network_spatial"],
    "member_lat": ["graph_network_spatial"],
    "member_credit": ["evaluation_decision", "multi_objective_decision"],
    "booking_quota": ["optimization_planning", "multi_objective_decision"],
    "booking_start_time": ["prediction_timeseries"],
    "new_task_lng": ["graph_network_spatial", "prediction_timeseries", "optimization_planning"],
    "new_task_lat": ["graph_network_spatial", "prediction_timeseries", "optimization_planning"],
}

CASE_INPUTS = [
    "case_config.json",
    "problem_analysis.md",
    "problem/problem_contract.md",
    "intake/problem_statement_extraction.md",
    "intake/data_availability_check.md",
    "intake/constraint_completeness_check.md",
    "data/schema/data_dictionary.md",
    "data/schema/field_mapping.json",
    "data/schema/missing_value_report.md",
    "data/schema/data_quality_report.md",
    "data/schema/anomaly_review_report.md",
    "data/schema/modeling_input_boundary.md",
]

PROBLEM_ANALYSIS_TEMPLATE_MARKER = "Status: waiting for problem statement."

MODEL_LIBRARY_ASSETS = [
    "pipeline_core/model_library/model_route_registry.json",
    "pipeline_core/model_library/problem_type_taxonomy.md",
    "pipeline_core/model_library/validation_metric_catalog.md",
    "pipeline_core/model_library/sensitivity_analysis_catalog.md",
    "pipeline_core/model_library/paper_structure_by_problem_type.md",
    "pipeline_core/model_library/routing_risk_register.md",
]


class ProblemContractBlocked(ValueError):
    """Raised when the formal modeling input contract is unavailable."""


def _problem_contract_path(case_dir: Path) -> str:
    case_config = read_json(case_dir / "case_config.json")
    if isinstance(case_config, dict):
        configured = case_config.get("problem_contract_path")
        if isinstance(configured, str) and configured.strip():
            return configured.strip()
    return "problem/problem_contract.md"

PAPER_QUALITY_ASSETS = [
    "pipeline_core/paper_quality/paper_quality_layer_overview.md",
    "pipeline_core/paper_quality/evidence_chain_quality_rules.md",
    "pipeline_core/paper_quality/formula_symbol_quality_rules.md",
    "pipeline_core/paper_quality/conclusion_boundary_rules.md",
]

AI_DISCLOSURE_ASSETS = [
    "pipeline_core/evaluation/ai_usage_disclosure_rules.md",
    "pipeline_core/evaluation/showcase_layer_quality_rules.md",
]

OPTIONAL_KNOWLEDGE_CARD_ASSETS = [
    "pipeline_core/knowledge_cards/problem_type_to_knowledge_card_map.md",
    "pipeline_core/knowledge_cards/model_route_to_knowledge_card_map.md",
    "pipeline_core/knowledge_cards/knowledge_card_calling_policy.md",
    "pipeline_core/knowledge_cards/active_competition_knowledge_card_usage.md",
]

LITERATURE_KNOWLEDGE_CARD_ASSETS = [
    "pipeline_core/literature_library/knowledge_cards/knowledge_card_router.md",
    "pipeline_core/literature_library/knowledge_cards/README.md",
    "pipeline_core/literature_library/literature_index.md",
    "pipeline_core/literature_library/references.bib",
]

KNOWLEDGE_CARD_BASE = "pipeline_core/literature_library/knowledge_cards/core_competition_cards"

TYPE_KNOWLEDGE_CARD_MAP = {
    "geometry_inverse_localization": [
        "01_modeling_workflow",
        "02_problem_type_router",
        "28_motion_and_path_model",
        "31_inverse_problem_and_parameter_identification",
        "35_matlab_implementation",
        "38_model_evaluation_and_error_analysis",
        "42_ai_assisted_modeling_boundary",
    ],
    "statistical_analysis": [
        "16_probability_foundation",
        "18_numerical_characteristics",
        "20_parameter_estimation",
        "21_statistical_inference_and_confidence",
        "22_data_fitting_and_regression",
        "38_model_evaluation_and_error_analysis",
    ],
    "prediction_timeseries": [
        "23_time_series_prediction",
        "38_model_evaluation_and_error_analysis",
        "39_sensitivity_analysis",
    ],
    "optimization_planning": [
        "03_linear_programming",
        "13_greedy_and_heuristic",
        "14_nonlinear_programming",
        "39_sensitivity_analysis",
        "84_optimization_solver_selection",
        "101_theory_checker_for_optimization",
    ],
    "evaluation_decision": [
        "10_decision_theory",
        "15_multi_objective_optimization",
        "38_model_evaluation_and_error_analysis",
    ],
    "graph_network_spatial": [
        "11_network_flow",
        "28_motion_and_path_model",
        "36_data_visualization",
        "39_sensitivity_analysis",
        "46_graph_shortest_path_mst_tsp",
    ],
    "dynamic_scheduling": [
        "07_dynamic_programming",
        "09_queueing_model",
        "13_greedy_and_heuristic",
        "39_sensitivity_analysis",
    ],
    "mechanism_fitting": [
        "27_physical_mechanism_model",
        "31_inverse_problem_and_parameter_identification",
        "35_matlab_implementation",
        "38_model_evaluation_and_error_analysis",
    ],
    "classification_clustering": [
        "38_model_evaluation_and_error_analysis",
        "92_clustering_analysis_ml",
        "99_ml_baseline_and_interpretability",
        "102_theory_checker_for_machine_learning",
    ],
    "multi_objective_decision": [
        "10_decision_theory",
        "15_multi_objective_optimization",
        "39_sensitivity_analysis",
        "61_goal_programming",
    ],
    "mixed_problem": [
        "01_modeling_workflow",
        "02_problem_type_router",
        "38_model_evaluation_and_error_analysis",
        "41_evidence_chain_and_citation",
        "42_ai_assisted_modeling_boundary",
    ],
}


@dataclass
class TypeScore:
    problem_type: str
    score: float = 0.0
    matched_reasons: list[str] = field(default_factory=list)

    def add(self, points: float, reason: str) -> None:
        self.score += points
        if reason not in self.matched_reasons:
            self.matched_reasons.append(reason)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def read_json(path: Path) -> Any:
    text = read_text(path)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def load_registry(root: Path) -> dict[str, dict[str, Any]]:
    registry_path = root / "pipeline_core" / "model_library" / "model_route_registry.json"
    raw = read_json(registry_path) or {}
    items = raw.get("problem_types", [])
    registry: dict[str, dict[str, Any]] = {}
    if isinstance(items, list):
        for idx, item in enumerate(items):
            declared = item.get("problem_type") if isinstance(item, dict) else None
            canonical = normalize_type_id(str(declared)) if declared else REGISTRY_ORDER_TO_TYPE.get(idx)
            if canonical and isinstance(item, dict):
                registry[canonical] = item
    elif isinstance(items, dict):
        for raw_key, item in items.items():
            canonical = normalize_type_id(raw_key)
            if canonical and isinstance(item, dict):
                registry[canonical] = item
    return registry


def normalize_type_id(value: str) -> str | None:
    aliases = {
        "geometry_inverse_localization": "geometry_inverse_localization",
        "geometry_localization": "geometry_inverse_localization",
        "inverse_localization": "geometry_inverse_localization",
        "statistics_analysis": "statistical_analysis",
        "statistical_analysis": "statistical_analysis",
        "prediction": "prediction_timeseries",
        "prediction_timeseries": "prediction_timeseries",
        "optimization": "optimization_planning",
        "optimization_planning": "optimization_planning",
        "evaluation": "evaluation_decision",
        "evaluation_decision": "evaluation_decision",
        "graph_network_spatial_coverage": "graph_network_spatial",
        "graph_network_spatial": "graph_network_spatial",
        "spatial_analysis": "graph_network_spatial",
        "dynamic_scheduling": "dynamic_scheduling",
        "mechanism_fitting": "mechanism_fitting",
        "classification_clustering": "classification_clustering",
        "multi_objective_decision": "multi_objective_decision",
        "mixed_problem": "mixed_problem",
    }
    return aliases.get(str(value).strip())


def collect_case_inputs(
    case_dir: Path,
    *,
    validated_problem_contract_path: str | None = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    if validated_problem_contract_path is None:
        contract_validation = _raise_if_problem_contract_invalid(case_dir)
        validated_problem_contract_path = str(contract_validation["path"])
    contract_relative_path = validated_problem_contract_path.replace("\\", "/")
    case_inputs = [
        contract_relative_path if rel == "problem/problem_contract.md" else rel
        for rel in CASE_INPUTS
    ]
    checked: list[dict[str, Any]] = []
    parts: list[str] = []
    json_payloads: dict[str, Any] = {}
    for rel in case_inputs:
        path = case_dir / rel
        exists = path.exists()
        checked.append(
            {
                "path": rel.replace("\\", "/"),
                "exists": exists,
                "read": exists,
            }
        )
        if not exists:
            continue
        if path.suffix.lower() == ".json":
            payload = read_json(path)
            json_payloads[rel] = payload
            parts.append(json.dumps(payload, ensure_ascii=False) if payload is not None else read_text(path))
        else:
            content = read_text(path)
            if rel == "problem_analysis.md" and PROBLEM_ANALYSIS_TEMPLATE_MARKER in content:
                checked[-1]["read"] = False
                checked[-1]["reason"] = "TEMPLATE_PLACEHOLDER_SKIPPED"
                continue
            parts.append(content)
    return checked, "\n\n".join(parts), json_payloads


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_identity(case_dir: Path, relative_path: str) -> dict[str, Any]:
    path = (case_dir / relative_path).resolve()
    try:
        path.relative_to(case_dir.resolve())
    except ValueError:
        return {
            "path": relative_path,
            "exists": False,
            "sha256": None,
            "status": "UNSAFE_SOURCE_PATH",
        }
    if not path.is_file():
        return {
            "path": relative_path,
            "exists": False,
            "sha256": None,
            "status": "SOURCE_ARTIFACT_MISSING",
        }
    return {
        "path": relative_path,
        "exists": True,
        "sha256": _sha256_file(path),
        "status": "SOURCE_ARTIFACT_READ",
    }


def _current_formal_source_identity(case_dir: Path, item: dict[str, Any]) -> dict[str, Any]:
    """Re-read one bound source from its declared authority scope."""

    relative_path = str(item.get("path") or "")
    if item.get("scope") != "repository":
        return _source_identity(case_dir, relative_path)
    repository_path = (repo_root() / relative_path).resolve()
    try:
        repository_path.relative_to(repo_root().resolve())
    except ValueError:
        return {
            "path": relative_path,
            "exists": False,
            "sha256": None,
            "status": "UNSAFE_SOURCE_PATH",
            "scope": "repository",
        }
    if not repository_path.is_file():
        return {
            "path": relative_path,
            "exists": False,
            "sha256": None,
            "status": "SOURCE_ARTIFACT_MISSING",
            "scope": "repository",
        }
    return {
        "path": relative_path,
        "exists": True,
        "sha256": _sha256_file(repository_path),
        "status": "SOURCE_ARTIFACT_READ",
        "scope": "repository",
    }


def _bounded_excerpt(text: str, marker: str, limit: int = 6) -> str:
    lines = text.splitlines()
    start = next((idx for idx, line in enumerate(lines) if line.strip() == marker), None)
    if start is None:
        return "未找到该来源段落。"
    selected: list[str] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("## ") or stripped.startswith("### "):
            break
        if stripped:
            selected.append(stripped)
        if len(selected) >= limit:
            break
    return " ".join(selected) or "该来源段落为空。"


def _first_available_excerpt(text: str, markers: tuple[str, ...], limit: int = 6) -> str:
    """Read the first available section without inventing case-specific text."""

    for marker in markers:
        excerpt = _bounded_excerpt(text, marker, limit)
        if excerpt != "未找到该来源段落。":
            return excerpt
    return "未找到该来源段落。"


def _document_excerpt(text: str, limit: int = 8) -> str:
    """Return bounded source text when a valid contract uses unfamiliar headings."""

    selected = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        selected.append(stripped)
        if len(selected) >= limit:
            break
    return " ".join(selected) or "未找到该来源段落。"


def _read_case_text(case_dir: Path, relative_path: str) -> str:
    path = (case_dir / relative_path).resolve()
    try:
        path.relative_to(case_dir.resolve())
    except ValueError:
        return ""
    if not path.is_file():
        return ""
    return read_text(path)


def validate_problem_contract(case_dir: Path) -> dict[str, Any]:
    """Validate the case-local contract before any route or card work runs."""
    case_dir = Path(case_dir).resolve()
    relative_path = _problem_contract_path(case_dir).replace("\\", "/")
    candidate = (case_dir / relative_path).resolve()
    try:
        candidate.relative_to(case_dir)
    except ValueError:
        return {
            "status": "FAIL",
            "code": "PROBLEM_CONTRACT_UNSAFE_PATH",
            "case_id": case_dir.name,
            "path": relative_path,
            "sha256": None,
        }
    if not candidate.is_file():
        return {
            "status": "FAIL",
            "code": "PROBLEM_CONTRACT_MISSING",
            "case_id": case_dir.name,
            "path": relative_path,
            "sha256": None,
        }
    try:
        content = read_text(candidate).strip()
    except (OSError, UnicodeError):
        content = ""
    if not content:
        return {
            "status": "FAIL",
            "code": "PROBLEM_CONTRACT_EMPTY",
            "case_id": case_dir.name,
            "path": relative_path,
            "sha256": None,
        }
    case_config = read_json(case_dir / "case_config.json")
    case_id = case_config.get("case_id", case_dir.name) if isinstance(case_config, dict) else case_dir.name
    return {
        "status": "PASS",
        "code": "PROBLEM_CONTRACT_VALID",
        "case_id": case_id,
        "path": relative_path,
        "sha256": _sha256_file(candidate),
        "content_chars": len(content),
    }


def _raise_if_problem_contract_invalid(case_dir: Path) -> dict[str, Any]:
    validation = validate_problem_contract(case_dir)
    if validation.get("status") != "PASS":
        raise ProblemContractBlocked(
            "PROBLEM_CONTRACT_FAIL_CLOSED: "
            f"{validation.get('code')} at {validation.get('path')}"
        )
    return validation


def _approved_route_binding(case_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
    """Resolve only an explicit human route; never promote a router suggestion."""
    route_review = result.get("route_priority_binding") or {}
    if route_review.get("route_review_status") == "PASS" and route_review.get("selected_route_id"):
        return {
            "status": "APPROVED",
            "route": str(route_review["selected_route_id"]),
            "route_source": route_review.get("route_review_artifact_path") or "route/route_direction_review.json",
            "rejected_alternatives": [],
            "limitations": "按 route_direction_review artifact 的人工选择执行。",
        }

    decision = _read_case_text(case_dir, "decision_log.md")
    gate = _read_case_text(case_dir, "human_review/gate_2_model_choice.md")
    freeze = _read_case_text(case_dir, "model_freeze_record.md")
    try:
        from pipeline_core.python_tools.human_gate import evaluate_human_gate

        approved = evaluate_human_gate(case_dir, "GATE2").get("gate_status") == "PASS"
    except (OSError, UnicodeError, ValueError):
        approved = False
    if not approved:
        return {
            "status": "NOT_AVAILABLE",
            "route": None,
            "route_source": None,
            "rejected_alternatives": [],
            "limitations": "未发现可验证的人工批准 route artifact；不得提出 approved-route claim。",
        }

    route = None
    route_source = "decision_log.md + human_review/gate_2_model_choice.md"
    route_match = re.search(r"采用\s+`([^`]+)`\s+作为正式冻结模型基础", decision, flags=re.MULTILINE)
    if route_match:
        route = route_match.group(1).strip()
    option_match = re.search(r"SELECTED_OPTION:\s*([^\s]+)", decision, flags=re.MULTILINE)
    option = option_match.group(1).strip() if option_match else None
    if route is None:
        route_match = re.search(r"选择路线：\s*([^\r\n]+)", gate, flags=re.MULTILINE)
        route = route_match.group(1).strip() if route_match and route_match.group(1).strip() else None
        route_source = "human_review/gate_2_model_choice.md"
    if not route:
        return {
            "status": "NOT_AVAILABLE",
            "route": None,
            "route_source": None,
            "rejected_alternatives": [],
            "limitations": "人工状态存在，但 approved route 字段为空；不得从候选第一项猜测。",
        }

    rejected = []
    for line in decision.splitlines():
        stripped = line.strip()
        if stripped.startswith("- 问题 ") and "：" in stripped and "正式冻结模型基础" not in stripped:
            rejected.append(stripped[2:])
    limitations = _bounded_excerpt(decision, "Q3_RESULT_BOUNDARY:", 3)
    if limitations == "未找到该来源段落。":
        limitations = _bounded_excerpt(freeze, "冻结保护：", 5)
    if limitations == "未找到该来源段落。":
        limitations = _first_available_excerpt(
            gate,
            ("## 批准后的正式建模/求解前强制 fail-closed 条件", "## 决策"),
            5,
        )
    if limitations == "未找到该来源段落。":
        limitations = "人工 Gate 2 已通过；正式建模仍受 handoff 中的 fail-closed 边界约束。"
    return {
        "status": "APPROVED",
        "route": route,
        "selected_option": option,
        "route_source": route_source,
        "rejected_alternatives": rejected[:8],
        "limitations": limitations,
    }


def _build_formal_input_binding(case_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
    case_config = result.get("case_config") if isinstance(result.get("case_config"), dict) else {}
    contract_paths = case_config.get("problem_statement_files") or []
    configured_contract = case_config.get("problem_contract_path")
    if isinstance(configured_contract, str) and configured_contract.strip():
        contract_path = configured_contract.strip()
    else:
        contract_path = contract_paths[0] if isinstance(contract_paths, list) and contract_paths else "problem/problem_contract.md"
    contract_text = _read_case_text(case_dir, contract_path)
    decision_text = _read_case_text(case_dir, "decision_log.md")
    freeze_text = _read_case_text(case_dir, "model_freeze_record.md")
    gate_text = _read_case_text(case_dir, "human_review/gate_2_model_choice.md")
    approved_route = _approved_route_binding(case_dir, result)
    source_specs = [
        (contract_path, "Problem Contract / task scope and constraints", "Problem Contract"),
    ]
    # These are legacy optional authorities.  If a case does not contain
    # them, the validated Gate 2 and Problem Contract fallbacks remain the
    # authority; do not render absent optional files as stable hash failures.
    for optional_path, section, field in (
        ("decision_log.md", "HUMAN_MODEL_FREEZE_AND_RECORD / Q3_IMPLEMENTATION_AND_SOLVE", "Approved Route / human interpretation"),
        ("model_freeze_record.md", "CORE_MODEL_STRUCTURE / 核心假设 / 冻结保护", "Modeling Boundary"),
    ):
        if (case_dir / optional_path).is_file():
            source_specs.append((optional_path, section, field))
    source_specs.extend(
        [
            ("human_review/gate_2_model_choice.md", "决策 / CURRENT_GATE_STATUS / HUMAN_GATE_STATUS", "Approved Route / approval status"),
            ("case_config.json", "workflow metadata and state revision", "Workflow Provenance"),
            ("CURRENT_HANDOFF.md", "历史 checkpoint and current stage boundary", "Modeling Boundary"),
            ("model_routes.md", "Gate 2 candidate route artifact", "Approved Route / candidate routes"),
            ("model_route_score.xlsx", "Gate 2 route score artifact", "Approved Route / route score"),
        ]
    )
    provenance = []
    for path, section, handoff_field in source_specs:
        identity = _source_identity(case_dir, path)
        identity.update(
            {
                "source_section": section,
                "handoff_field": handoff_field,
                "binding": (
                    "INFORMATIONAL_NON_INVALIDATING"
                    if path in {"case_config.json", "CURRENT_HANDOFF.md"}
                    else "STABLE_FORMAL_AUTHORITY"
                ),
            }
        )
        provenance.append(identity)

    route_candidates = (result.get("route_priority_binding") or {}).get("route_candidates") or []
    route_candidate_text = [
        f"{item.get('priority')}: {item.get('route')} ({item.get('source')})"
        for item in route_candidates
        if isinstance(item, dict)
    ]
    support_context = result.get("support_chain_context") or {}
    support_evidence = support_context.get("consumption_evidence") or {}
    approved_route_normalized = normalize_route(model_route=str(approved_route.get("route") or ""))
    approved_route_is_normalized = bool(
        approved_route.get("status") == "APPROVED"
        and approved_route_normalized
        and approved_route_normalized == support_context.get("resolved_model_route")
    )
    if approved_route_is_normalized:
        knowledge_status = support_evidence.get("status") or "NOT_PROVEN"
        knowledge_paths = support_context.get("textbook_concept_support") or []
    else:
        knowledge_status = "NO_RELEVANT_CARD"
        knowledge_paths = []
    knowledge_sources = []
    if support_context.get("support_chain_source"):
        support_source = Path(str(support_context["support_chain_source"]))
        if support_source.is_absolute():
            try:
                support_rel = support_source.resolve().relative_to(repo_root().resolve()).as_posix()
            except ValueError:
                support_rel = support_source.as_posix()
        else:
            support_rel = support_source.as_posix()
        support_path = repo_root() / support_rel
        knowledge_sources.append({
            "path": support_rel,
            "exists": support_path.is_file(),
            "sha256": _sha256_file(support_path) if support_path.is_file() else None,
            "status": "SOURCE_ARTIFACT_READ" if support_path.is_file() else "SOURCE_ARTIFACT_MISSING",
            "scope": "repository",
            "binding": "STABLE_FORMAL_AUTHORITY",
            "source_section": "selected route support chain",
            "handoff_field": "Knowledge Support",
        })

    existing_paths = {str(item.get("path") or "") for item in provenance if isinstance(item, dict)}
    for item in knowledge_sources:
        if item.get("path") and item.get("path") not in existing_paths:
            provenance.append(dict(item))
            existing_paths.add(str(item["path"]))

    gate_boundary = _first_available_excerpt(
        gate_text,
        (
            "## 批准后的正式建模/求解前强制 fail-closed 条件",
            "## Gate 2 批准附带的实现前锁定事项",
            "## 决策",
        ),
        8,
    )
    case_task_scope = _first_available_excerpt(
        contract_text,
        ("### 1.1 实际研究对象", "### 2.1 Shared facts", "### 2.1 题面事实"),
        6,
    )
    subproblem_scope = _first_available_excerpt(
        contract_text,
        ("### 1.2 三个问题分别要求解决什么", "### 2.2 Subproblem contract", "### 2.2 子问题契约"),
        8,
    )
    relevant_constraints = _first_available_excerpt(
        contract_text,
        ("### 3.1 HARD CONSTRAINT", "### 4.2 Constraints and hard boundaries", "## 4. Modeling Boundary"),
        8,
    )
    data_boundary = _first_available_excerpt(
        contract_text,
        ("### 1.4 输入信息", "## 3. Data Boundary", "## 数据边界"),
        8,
    )
    human_interpretation = _first_available_excerpt(
        decision_text,
        ("## HUMAN_MODEL_FREEZE_AND_RECORD", "Q3_IMPLEMENTATION_AND_SOLVE:"),
        7,
    )
    if human_interpretation == "未找到该来源段落。":
        human_interpretation = _first_available_excerpt(
            gate_text,
            ("## 最终选择", "## Final Selection", "## 决策"),
            6,
        )
    frozen_assumptions = _first_available_excerpt(
        freeze_text,
        ("核心假设：", "## CORE_MODEL_STRUCTURE", "## Modeling Boundary"),
        10,
    )
    if frozen_assumptions == "未找到该来源段落。":
        frozen_assumptions = _first_available_excerpt(
            contract_text,
            ("## 4. Modeling Boundary", "## Modeling Boundary"),
            10,
        )
    prohibited_scope = _first_available_excerpt(
        freeze_text,
        ("冻结保护：", "## Scope Protection", "## Modeling Boundary"),
        8,
    )
    if prohibited_scope == "未找到该来源段落。":
        prohibited_scope = _first_available_excerpt(
            contract_text,
            ("## 4. Modeling Boundary", "## 5. Output Boundary", "## Modeling Boundary"),
            8,
        )
    cross_subproblem_boundaries = _first_available_excerpt(
        contract_text,
        ("### 1.3 三个问题的递进关系", "### 2.2 Subproblem contract", "### 2.2 子问题契约"),
        8,
    )
    contract_fallback = _document_excerpt(contract_text, 8)
    for field_name, field_value in (
        ("case_task_scope", case_task_scope),
        ("subproblem_scope", subproblem_scope),
        ("relevant_constraints", relevant_constraints),
        ("data_boundary", data_boundary),
        ("frozen_assumptions", frozen_assumptions),
        ("prohibited_scope", prohibited_scope),
        ("cross_subproblem_boundaries", cross_subproblem_boundaries),
    ):
        if field_value == "未找到该来源段落。":
            if field_name == "case_task_scope":
                case_task_scope = contract_fallback
            elif field_name == "subproblem_scope":
                subproblem_scope = contract_fallback
            elif field_name == "relevant_constraints":
                relevant_constraints = contract_fallback
            elif field_name == "data_boundary":
                data_boundary = contract_fallback
            elif field_name == "frozen_assumptions":
                frozen_assumptions = contract_fallback
            elif field_name == "prohibited_scope":
                prohibited_scope = contract_fallback
            else:
                cross_subproblem_boundaries = contract_fallback
    if human_interpretation == "未找到该来源段落。":
        human_interpretation = _document_excerpt(gate_text, 6)
    if gate_boundary == "未找到该来源段落。":
        gate_boundary = _document_excerpt(gate_text, 8)

    return {
        "problem_contract": {
            "case_task_scope": case_task_scope,
            "subproblem_scope": subproblem_scope,
            "relevant_constraints": relevant_constraints,
            "data_boundary": data_boundary,
            "human_confirmed_interpretation": human_interpretation,
            "source_path": contract_path,
        },
        "approved_route": {
            "status": approved_route["status"],
            "router_candidates": route_candidate_text,
            "human_approved_route": approved_route.get("route"),
            "selected_option": approved_route.get("selected_option"),
            "source": approved_route.get("route_source"),
            "why_selected": "正式路线来自已通过的人工 Gate 2 authority；router candidate 仅作对照记录。",
            "rejected_alternatives": approved_route.get("rejected_alternatives", []),
            "limitations": approved_route.get("limitations", ""),
        },
        "knowledge_support": {
            "status": knowledge_status,
            "support_chain_source": support_context.get("support_chain_source"),
            "selected_card_paths": knowledge_paths,
            "loaded_card_ids": support_evidence.get("loaded_card_ids", []) if approved_route_is_normalized else [],
            "note": "无与人工批准 route 对应的知识卡时不注入无关卡。" if not approved_route_is_normalized else "仅保留现有 selected-card support chain。",
            "source_identity": knowledge_sources,
        },
        "modeling_boundary": {
            "frozen_assumptions": frozen_assumptions,
            "prohibited_scope_expansion": prohibited_scope,
            "cross_subproblem_boundaries": cross_subproblem_boundaries,
            "current_stage_constraints": (
                f"source stage={case_config.get('current_stage') or 'NOT_DECLARED'}; "
                "formal handoff target=MODEL_IMPLEMENTATION; transition metadata is non-invalidating."
            ),
            "gate_boundary": gate_boundary,
        },
        "source_identity_provenance": provenance,
        "receipt_contract": {
            "receipt_name": "FORMAL_MODEL_BUILDING_INPUT_RECEIPT",
            "status": "PENDING_REAL_CODEX_SESSION",
            "required_fields": [
                "CASE_ID", "MODEL_BUILDING_HANDOFF_PATH", "HANDOFF_SHA256",
                "PROBLEM_CONTRACT_READ", "APPROVED_ROUTE_READ",
                "KNOWLEDGE_SUPPORT_STATUS", "MODELING_BOUNDARY_READ",
                "RECEIPT_GENERATED_BY",
            ],
            "generated_by": "FORMAL_CODEX_MODEL_BUILDING_SESSION",
            "prohibited_automation": "Python/router must not write a PASS receipt outside a real formal Codex modeling session.",
        },
    }


def prepare_model_building_handoff_inputs(
    case_dir: Path,
    result: dict[str, Any],
    route_outputs: dict[str, Any],
    *,
    target_stage: str = "MODEL_IMPLEMENTATION",
) -> dict[str, Any]:
    """Bind the validated Gate 2 route before writing the formal handoff.

    The route-stage validator is the authority for the exact candidate heading.
    This helper only transfers that already-validated value into the existing
    formal input contract and refreshes case-local source identities.
    """

    if route_outputs.get("status") != "READY_FOR_MODEL_IMPLEMENTATION":
        raise ValueError("MODEL_BUILDING_HANDOFF_ROUTE_NOT_READY")
    selected_route = str(route_outputs.get("resolved_selected_route") or "").strip()
    if not selected_route:
        raise ValueError("MODEL_BUILDING_HANDOFF_SELECTED_ROUTE_MISSING")
    normalized_target = str(target_stage or "").strip().upper()
    if normalized_target != "MODEL_IMPLEMENTATION":
        raise ValueError("MODEL_BUILDING_HANDOFF_TARGET_STAGE_INVALID")
    formal_binding = result.get("formal_input_binding")
    if not isinstance(formal_binding, dict):
        raise ValueError("MODEL_BUILDING_HANDOFF_FORMAL_BINDING_MISSING")
    live_approved_route = _approved_route_binding(case_dir, result)
    approved_value = str(live_approved_route.get("route") or "").strip()
    if live_approved_route.get("status") != "APPROVED" or not approved_value:
        raise ValueError("MODEL_BUILDING_HANDOFF_GATE2_ROUTE_NOT_APPROVED")
    selected_id = re.split(r"[\s:：]", selected_route, maxsplit=1)[0].strip().casefold()
    approved_id = re.split(r"[\s:：]", approved_value, maxsplit=1)[0].strip().casefold()
    if not selected_id or selected_id != approved_id:
        raise ValueError("MODEL_BUILDING_HANDOFF_GATE2_ROUTE_MISMATCH")
    approved_route = dict(formal_binding.get("approved_route") or {})
    approved_route.update(
        {
            "status": "APPROVED",
            "router_candidates": list(approved_route.get("router_candidates") or []),
            "human_approved_route": selected_route,
            "selected_option": route_outputs.get("gate2_selected_route") or selected_id.upper(),
            "source": live_approved_route.get("route_source") or "human_review/gate_2_model_choice.md",
            "limitations": live_approved_route.get("limitations") or "路线内容仍受正式建模前 fail-closed 条件约束。",
        }
    )
    formal_binding["approved_route"] = approved_route
    result["_model_building_target_stage"] = normalized_target
    result["final_model_route"] = selected_route
    result["route_source"] = str(approved_route["source"])
    result["support_chain_invocation"] = {
        **(result.get("support_chain_invocation") or {}),
        "final_model_route": selected_route,
        "route_source": str(approved_route["source"]),
    }

    # Refresh identities after the router result and route artifacts have been
    # written.  These hashes are checked again during validation.
    existing_items = {
        str(item.get("path")): dict(item)
        for item in formal_binding.get("source_identity_provenance", [])
        if isinstance(item, dict) and item.get("path")
    }
    source_paths = list(existing_items)
    problem_contract_path = str(
        (formal_binding.get("problem_contract") or {}).get("source_path")
        or "problem/problem_contract.md"
    )
    for relative in (
        "case_config.json",
        problem_contract_path,
        "human_review/gate_2_model_choice.md",
        "model_routes.md",
        "model_route_score.xlsx",
    ):
        if relative not in source_paths:
            source_paths.append(relative)
    refreshed: list[dict[str, Any]] = []
    for relative in source_paths:
        prior = existing_items.get(str(relative), {"path": str(relative)})
        item = _current_formal_source_identity(case_dir, prior)
        item.update(
            {
                "source_section": prior.get("source_section") or "formal model-building input",
                "handoff_field": prior.get("handoff_field") or "Formal Input Contract",
                "binding": (
                    "INFORMATIONAL_NON_INVALIDATING"
                    if str(relative) in {"case_config.json", "CURRENT_HANDOFF.md"}
                    else prior.get("binding") or "STABLE_FORMAL_AUTHORITY"
                ),
            }
        )
        if prior.get("scope"):
            item["scope"] = prior["scope"]
        refreshed.append(item)
    formal_binding["source_identity_provenance"] = refreshed
    return result


def asset_entry(root: Path, rel_path: str, purpose: str, required: bool = True) -> dict[str, Any]:
    path = root / rel_path
    return {
        "path": rel_path,
        "exists": path.exists(),
        "required": required,
        "purpose": purpose,
    }


def collect_asset_support(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    reused: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    model_support: list[dict[str, Any]] = []
    paper_support: list[dict[str, Any]] = []
    ai_support: list[dict[str, Any]] = []

    for rel in MODEL_LIBRARY_ASSETS:
        entry = asset_entry(root, rel, "题型识别、模型路线、验证指标、敏感性和风险规则", True)
        model_support.append(entry)
        (reused if entry["exists"] else missing).append(entry)

    for rel in PAPER_QUALITY_ASSETS:
        entry = asset_entry(root, rel, "论文质量层、证据链、公式符号和结论边界规则", True)
        paper_support.append(entry)
        (reused if entry["exists"] else missing).append(entry)

    for rel in AI_DISCLOSURE_ASSETS:
        entry = asset_entry(root, rel, "AI 使用记录双模式与展示层质量门规则", True)
        ai_support.append(entry)
        (reused if entry["exists"] else missing).append(entry)

    for rel in LITERATURE_KNOWLEDGE_CARD_ASSETS:
        entry = asset_entry(root, rel, "已有 literature_library 知识卡入口和文献索引", False)
        (reused if entry["exists"] else missing).append(entry)

    for rel in OPTIONAL_KNOWLEDGE_CARD_ASSETS:
        entry = asset_entry(root, rel, "pipeline_core/knowledge_cards 显式映射资产", False)
        if entry["exists"]:
            reused.append(entry)
        else:
            missing.append(entry)

    return reused, missing, model_support, paper_support, ai_support


def card_path(card_id: str) -> str:
    return f"{KNOWLEDGE_CARD_BASE}/{card_id}.md"


def card_exists(root: Path, card_id: str) -> bool:
    return (root / card_path(card_id)).exists()


def build_knowledge_card_support(
    root: Path,
    problem_types: list[str],
    registry: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Select only cards associated with the current routed problem types."""

    support: list[dict[str, Any]] = []
    for problem_type in problem_types:
        card_ids = TYPE_KNOWLEDGE_CARD_MAP.get(problem_type, [])
        registry_item = registry.get(problem_type, {})
        card_entries = [
            {
                "card_id": card_id,
                "path": card_path(card_id),
                "exists": card_exists(root, card_id),
            }
            for card_id in card_ids
        ]
        support.append(
            {
                "problem_type": problem_type,
                "theme": TYPE_DISPLAY_NAME.get(problem_type, problem_type),
                "problem_scope": "仅支撑当前题型的方法选择、理论边界和验证设计。",
                "model_route_supported": "、".join(registry_item.get("recommended_models", []) or []),
                "validation_supported": "、".join(registry_item.get("validation_metrics", []) or []),
                "cards": card_entries,
                "knowledge_card_gap": any(not card["exists"] for card in card_entries),
                "human_review_required": True,
                "note": "知识卡只用于理论支撑与路线解释，不等于正式参考文献，也不支撑具体赛题数值结论。",
            }
        )
    return support


def keyword_score(text: str, scores: dict[str, TypeScore]) -> None:
    lowered = text.lower()
    for problem_type, keywords in TYPE_KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in lowered:
                scores[problem_type].add(1.0, f"命中关键词：{keyword}")


def registry_keyword_score(text: str, scores: dict[str, TypeScore], registry: dict[str, dict[str, Any]]) -> None:
    lowered = text.lower()
    for problem_type, item in registry.items():
        for keyword in item.get("keywords", []) or []:
            if str(keyword).lower() in lowered:
                scores[problem_type].add(0.8, f"命中模型库关键词：{keyword}")


def field_score(text: str, json_payloads: dict[str, Any], scores: dict[str, TypeScore]) -> None:
    field_blob = text
    for payload in json_payloads.values():
        field_blob += "\n" + json.dumps(payload, ensure_ascii=False) if payload is not None else ""
    lowered = field_blob.lower()
    for field_name, problem_types in FIELD_HINTS.items():
        if field_name.lower() in lowered:
            for problem_type in problem_types:
                scores[problem_type].add(2.0, f"识别到字段结构：{field_name}")

    spatial_terms = ["gps", "经度", "纬度", "坐标", "位置", "距离"]
    if sum(1 for term in spatial_terms if term.lower() in lowered) >= 3:
        scores["graph_network_spatial"].add(4.0, "字段结构包含坐标与距离分析要素")

    if "任务标价" in field_blob or "任务定价" in field_blob or "task_price" in lowered:
        scores["optimization_planning"].add(2.0, "存在可作为决策变量或调整对象的价格字段")
        scores["statistical_analysis"].add(1.5, "存在可分析原方案规律的价格字段")

    if "任务执行情况" in field_blob or "task_finished" in lowered:
        scores["prediction_timeseries"].add(2.0, "存在完成情况字段，可建立完成概率 baseline")
        scores["statistical_analysis"].add(2.0, "存在完成情况字段，可做完成/未完成差异分析")


def structure_score(text: str, scores: dict[str, TypeScore]) -> None:
    question_count = len(re.findall(r"问题[一二三四五六七八九十0-9]", text))
    if question_count >= 3:
        scores["mixed_problem"].add(5.0, f"题面包含多个子问题：约 {question_count} 处问题标记")

    if all(marker in text for marker in ["附件一", "附件二", "附件三"]):
        scores["mixed_problem"].add(3.0, "题面包含多附件输入，需要分阶段建模")

    if "打包" in text:
        scores["classification_clustering"].add(2.0, "问题包含任务打包，可能需要空间聚类或分组")
        scores["mixed_problem"].add(2.0, "任务打包属于后续子问题，不应并入单一路线")

    if "新项目" in text or "附件三" in text:
        scores["prediction_timeseries"].add(1.5, "存在新项目定价或实施效果外推需求")
        scores["optimization_planning"].add(1.5, "新项目需要给出定价方案")

    if "成本" in text and "完成率" in text:
        scores["multi_objective_decision"].add(3.0, "同时出现成本与完成率，存在权衡目标")

    if "约束" in text or "目标函数" in text:
        scores["optimization_planning"].add(2.0, "材料提及目标或约束完整性")


def compute_scores(text: str, json_payloads: dict[str, Any], registry: dict[str, dict[str, Any]]) -> list[TypeScore]:
    scores = {problem_type: TypeScore(problem_type) for problem_type in CANONICAL_TYPES}
    keyword_score(text, scores)
    registry_keyword_score(text, scores, registry)
    field_score(text, json_payloads, scores)
    structure_score(text, scores)

    # 混合题保护：多个强子题型并存时提升 mixed_problem，避免误判为单一题型。
    strong_non_mixed = [s for t, s in scores.items() if t != "mixed_problem" and s.score >= 6]
    if len(strong_non_mixed) >= 3:
        scores["mixed_problem"].add(4.0, "多个强候选题型并存，按混合型问题处理")

    return sorted(scores.values(), key=lambda item: (-item.score, item.problem_type))


def choose_primary_and_secondary(scored: list[TypeScore]) -> tuple[str, list[str], str, str]:
    top = scored[0]
    score_map = {item.problem_type: item.score for item in scored}

    strong_types = [item.problem_type for item in scored if item.score >= max(4.0, top.score * 0.35)]
    if score_map.get("mixed_problem", 0) >= top.score - 2 and len(strong_types) >= 3:
        primary = "mixed_problem"
    else:
        primary = top.problem_type

    secondary = [item.problem_type for item in scored if item.problem_type != primary and item.score >= max(3.5, top.score * 0.30)]
    if primary == "mixed_problem":
        # 混合题需要保留关键子题型，便于后续模型路线拆分。
        for required in [
            "statistical_analysis",
            "optimization_planning",
            "graph_network_spatial",
            "prediction_timeseries",
            "multi_objective_decision",
        ]:
            if score_map.get(required, 0) > 0 and required not in secondary:
                secondary.append(required)

    gap = top.score - (scored[1].score if len(scored) > 1 else 0)
    if top.score < 6:
        confidence = "LOW"
        status = "HUMAN_CONFIRMATION_REQUIRED"
    elif primary == "mixed_problem" or gap < 4:
        confidence = "MEDIUM"
        status = "ROUTED_WITH_LIMITATION"
    else:
        confidence = "HIGH"
        status = "ROUTED"
    return primary, secondary, confidence, status


def unique_extend(target: list[str], values: list[str]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def recommendations(problem_types: list[str], registry: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    result = {
        "recommended_baseline_models": [],
        "recommended_advanced_models": [],
        "recommended_validation_metrics": [],
        "recommended_sensitivity_checks": [],
        "recommended_paper_sections": [],
    }
    for problem_type in problem_types:
        item = registry.get(problem_type, {})
        unique_extend(result["recommended_baseline_models"], item.get("baseline_models", []) or [])
        unique_extend(result["recommended_advanced_models"], item.get("advanced_models", []) or [])
        unique_extend(result["recommended_validation_metrics"], item.get("validation_metrics", []) or [])
        unique_extend(result["recommended_sensitivity_checks"], item.get("sensitivity_checks", []) or [])
        unique_extend(result["recommended_paper_sections"], item.get("paper_sections", []) or [])
    return result


def build_problem_routes(text: str) -> list[dict[str, str]]:
    problem_ids = list(
        dict.fromkeys(
            re.findall(r"问题\s*\d+(?:\s*[（(]\s*\d+\s*[）)])?", text)
        )
    )
    if problem_ids:
        return [
            {
                "problem": problem_id.replace(" ", ""),
                "route": "基于题面对象、观测关系和约束建立最小充分模型，再进入候选路线比较。",
                "scope_note": "这里只登记子问题边界，不执行模型、不预选最终路线。",
            }
            for problem_id in problem_ids[:12]
        ]
    return [
        {
            "problem": "总体",
            "route": "先按主题型建立最小闭环，再扩展到复杂子问题",
            "scope_note": "该路线只作为题型路由器 baseline 建议。",
        }
    ]


def build_risks(primary: str, secondary: list[str], confidence: str, text: str) -> list[dict[str, str]]:
    risks = [
        {
            "risk_level": "HIGH",
            "risk": "路由结果只是建模建议，不能替代人工模型选择 gate。",
            "mitigation": "进入建模前必须人工确认题型与路线。",
        },
        {
            "risk_level": "HIGH",
            "risk": "final_submit 不得由题型路由器创建或授权。",
            "mitigation": "输出中固定 final_submit_allowed=false。",
        },
    ]
    if confidence != "HIGH":
        risks.append(
            {
                "risk_level": "MEDIUM",
                "risk": "候选题型接近，存在混合题或多阶段路线风险。",
                "mitigation": "保留 secondary_problem_types，并分问题写明边界。",
            }
        )
    if primary == "mixed_problem":
        risks.append(
            {
                "risk_level": "HIGH",
                "risk": "混合型问题容易把最小闭环误写成完整赛题结果。",
                "mitigation": "明确已完成范围、未完成范围和 DRAFT_ONLY 边界。",
            }
        )
    if "case_06" in text or "case_07" in text or "case_08" in text:
        risks.append(
            {
                "risk_level": "HIGH",
                "risk": "输入文本出现机制 fixture 标记，需确认未引用 fixture 结果。",
                "mitigation": "active_competition 禁止复用机制 fixture 数据或结论。",
            }
        )
    return risks


def recommended_card_ids(knowledge_support: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for item in knowledge_support:
        for card in item.get("cards", []):
            card_id = str(card.get("card_id") or "").strip()
            if card_id and card.get("exists") and card_id not in result:
                result.append(card_id)
    return result


def knowledge_card_reasoning(problem_types: list[str], card_ids: list[str]) -> str:
    names = [TYPE_DISPLAY_NAME.get(item, item) for item in problem_types]
    if not card_ids:
        return (
            f"当前识别题型为{'、'.join(names)}，知识卡映射中没有可读取的相关卡片；"
            "不得注入无关知识卡，后续按 NO_RELEVANT_CARD 处理。"
        )
    return (
        f"当前识别题型为{'、'.join(names)}；仅选择与这些题型直接映射且文件存在的知识卡，"
        "用于方法选择、理论边界和验证设计，不支撑本题数值结论或最终模型选择。"
    )


def high_risk_theory_flags(problem_types: list[str]) -> list[str]:
    flags = ["ROUTER_SUGGESTION_REQUIRES_HUMAN_GATE2"]
    selected = set(problem_types)
    if "geometry_inverse_localization" in selected:
        flags.append("GEOMETRIC_IDENTIFIABILITY_DEGENERACY_AND_GAUGE_REVIEW_REQUIRED")
    if selected & {"optimization_planning", "multi_objective_decision", "dynamic_scheduling"}:
        flags.append("GLOBAL_OPTIMALITY_CONVEXITY_AND_SOLVER_STATUS_REVIEW_REQUIRED")
    if selected & {"statistical_analysis", "prediction_timeseries", "classification_clustering"}:
        flags.append("STATISTICAL_ASSUMPTION_GENERALIZATION_AND_DATA_LEAKAGE_REVIEW_REQUIRED")
    if "mechanism_fitting" in selected:
        flags.append("PARAMETER_IDENTIFIABILITY_AND_MODEL_MISSPECIFICATION_REVIEW_REQUIRED")
    return flags


def data_dependency_level(primary: str, case_config: dict[str, Any]) -> str:
    if primary in {"statistical_analysis", "prediction_timeseries", "classification_clustering"}:
        return "high"
    attachments = case_config.get("attachment_files")
    if isinstance(attachments, list) and attachments:
        return "medium"
    if primary == "geometry_inverse_localization":
        return "low"
    return "medium"


def human_routing_questions(primary: str, secondary: list[str]) -> list[str]:
    questions = [
        "题型路由是否覆盖全部子问题且未把辅助题型误当成最终模型？",
        "候选路线是否只使用题面与当前 case 可追溯的数据和约束？",
    ]
    if primary == "geometry_inverse_localization":
        questions.append("定位路线如何处理多解、退化构型、未知标号和相似变换不变性？")
    if secondary:
        questions.append("辅助题型是否确有必要，还是应保持为未采用候选？")
    return questions


def sanitize_support_chain_context(context: dict[str, Any]) -> dict[str, Any]:
    """Keep generic method support while removing historical-case fixtures."""

    sanitized = json.loads(json.dumps(context, ensure_ascii=False))
    sanitized["case_validation_support"] = []
    sanitized["historical_case_support_removed"] = True
    return sanitized


def build_output(case_dir: Path) -> dict[str, Any]:
    root = repo_root()
    problem_contract_validation = _raise_if_problem_contract_invalid(case_dir)
    registry = load_registry(root)
    input_files_checked, text, json_payloads = collect_case_inputs(
        case_dir,
        validated_problem_contract_path=str(problem_contract_validation["path"]),
    )
    reused_assets, missing_assets, model_support, paper_support, ai_support = collect_asset_support(root)
    scored = compute_scores(text, json_payloads, registry)
    primary, secondary, confidence, routing_status = choose_primary_and_secondary(scored)
    selected_types = [primary] + secondary
    recs = recommendations(selected_types, registry)
    knowledge_support = build_knowledge_card_support(root, selected_types, registry)
    card_ids = recommended_card_ids(knowledge_support)
    case_config = json_payloads.get("case_config.json") if isinstance(json_payloads.get("case_config.json"), dict) else {}
    support_chain_data = load_support_chain()
    route_priority_binding = resolve_model_route(case_dir, router_suggestion=primary)
    route_candidate = route_priority_binding["final_model_route"]
    route_is_human_approved = route_priority_binding.get("route_review_status") == "PASS"
    final_model_route = route_candidate if route_is_human_approved else None
    support_route = route_candidate or primary
    support_chain_context = sanitize_support_chain_context(
        build_support_chain_context(
            problem_type=primary,
            model_route=support_route,
            chain_data=support_chain_data,
        )
    )
    support_chain_context["route_priority_binding"] = route_priority_binding
    secondary_support_chain_contexts = [
        sanitize_support_chain_context(
            build_support_chain_context(problem_type=item, model_route=item, chain_data=support_chain_data)
        )
        for item in secondary
    ]

    result = {
        "case_id": case_config.get("case_id", case_dir.name),
        "case_config": case_config,
        "problem_contract_validation": problem_contract_validation,
        "problem_name": case_config.get("problem_name", "未从 case_config.json 识别"),
        "case_current_stage": case_config.get("current_stage", "not declared"),
        "input_files_checked": input_files_checked,
        "primary_problem_type": primary,
        "router_suggestion": route_priority_binding.get("router_suggestion") or primary,
        "final_model_route": final_model_route,
        "route_source": (
            route_priority_binding["route_source"]
            if route_is_human_approved
            else "NOT_SELECTED_GATE2_PENDING"
        ),
        "route_conflict_detected": route_priority_binding["route_conflict_detected"],
        "route_conflict_note": route_priority_binding["route_conflict_note"],
        "route_priority_binding": route_priority_binding,
        "primary_problem_type_name": TYPE_DISPLAY_NAME.get(primary, primary),
        "secondary_problem_types": secondary,
        "secondary_problem_type_names": [TYPE_DISPLAY_NAME.get(item, item) for item in secondary],
        "type_scores": [
            {
                "problem_type": item.problem_type,
                "problem_type_name": TYPE_DISPLAY_NAME.get(item.problem_type, item.problem_type),
                "score": round(item.score, 3),
                "matched_reasons": item.matched_reasons[:12],
            }
            for item in scored
        ],
        "confidence": confidence,
        **recs,
        "recommended_problem_routes": build_problem_routes(text),
        "reused_assets": reused_assets,
        "missing_assets": missing_assets,
        "knowledge_card_support": knowledge_support,
        "support_chain_context": support_chain_context,
        "secondary_support_chain_contexts": secondary_support_chain_contexts,
        "support_chain_invocation": {
            "invoked_by": "pipeline_core/router/problem_type_router.py",
            "source": support_chain_context["support_chain_source"],
            "router_suggestion": route_priority_binding.get("router_suggestion") or primary,
            "final_model_route": final_model_route,
            "support_route_candidate": support_route,
            "route_source": (
                route_priority_binding["route_source"]
                if route_is_human_approved
                else "NOT_SELECTED_GATE2_PENDING"
            ),
            "route_conflict_detected": route_priority_binding["route_conflict_detected"],
            "primary_summary": summarize_support_chain_context(support_chain_context),
            "formal_references_generated": False,
            "excellent_paper_as_method_citation": False,
        },
        "model_library_support": model_support,
        "paper_quality_support": paper_support,
        "ai_disclosure_mode_support": {
            "current_mode": "showcase_mode",
            "showcase_mode": "校内展示、老师沟通、队员交流、暑假训练和阶段性展示包使用概括表述。",
            "official_submission_mode": "正式比赛材料必须按当年规则透明披露具体 AI 工具、版本/型号、开发机构、使用日期、使用环节和人工复核方式。",
            "support_assets": ai_support,
        },
        "asset_reuse_note_path": "routing/asset_reuse_note.md",
        "asset_reuse_status": "KNOWLEDGE_CARD_ASSET_MISSING"
        if any(item["path"].startswith("pipeline_core/knowledge_cards/") for item in missing_assets)
        else "ALL_REQUIRED_ASSETS_AVAILABLE",
        "completed_scope": [
            "已基于当前 case 的题意分析、Problem Contract 与可用数据说明完成题型候选路由。",
            "题型路由器本轮只生成建议和知识卡边界，不改变既有模型结果。",
        ],
        "not_completed_scope": [
            "候选模型路线尚需由 model-route-generator 真实比较。",
            "最终模型路线尚需 Gate 2 人工选择。",
            "当前未运行 solver、实验、结果生成或论文流程。",
        ],
        "routing_risks": build_risks(primary, secondary, confidence, text),
        "human_gate_required": True,
        "final_submit_allowed": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": routing_status,
        "pipeline_status": "PASS_WITH_LIMITATION / ROUTER_ASSET_REUSE_KNOWLEDGE_CARD_PATCH_READY",
        "mode_boundary": "ROUTER_ONLY / NO_MODEL_EXECUTION / NOT_FOR_FINAL_SUBMISSION",
        "recommended_knowledge_cards": card_ids,
        "knowledge_card_reasoning": knowledge_card_reasoning(selected_types, card_ids),
        "high_risk_theory_flags": high_risk_theory_flags(selected_types),
        "data_dependency": data_dependency_level(primary, case_config),
        "risk_level": "high" if any(item.get("risk_level") == "HIGH" for item in build_risks(primary, secondary, confidence, text)) else "medium",
        "human_questions": human_routing_questions(primary, secondary),
    }
    result["formal_input_binding"] = _build_formal_input_binding(case_dir, result)
    return result


def markdown_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    lines = ["|" + "|".join(label for _, label in columns) + "|", "|" + "|".join("---" for _ in columns) + "|"]
    for row in rows:
        values = []
        for key, _ in columns:
            value = row.get(key, "")
            if isinstance(value, list):
                value = "；".join(map(str, value))
            values.append(str(value).replace("\n", " "))
        lines.append("|" + "|".join(values) + "|")
    return "\n".join(lines)


def render_model_building_handoff(result: dict[str, Any]) -> str:
    """Render the route-selected card bodies as the formal modeling input contract."""
    context = result.get("support_chain_context") or {}
    model_context = context.get("model_building_context") or {}
    evidence = context.get("consumption_evidence") or {}
    formal_binding = result.get("formal_input_binding") or {}
    problem_contract = formal_binding.get("problem_contract") or {}
    approved_route = formal_binding.get("approved_route") or {}
    knowledge_binding = formal_binding.get("knowledge_support") or {}
    modeling_boundary = formal_binding.get("modeling_boundary") or {}
    provenance = formal_binding.get("source_identity_provenance") or []
    target_stage = str(
        result.get("_model_building_target_stage") or "MODEL_IMPLEMENTATION"
    ).strip().upper()
    source_stage = str(result.get("case_current_stage") or "NOT_DECLARED").strip().upper()
    cards = model_context.get("cards") or []
    support_status = evidence.get("status") or model_context.get("status") or "UNKNOWN"
    if knowledge_binding.get("status") == "NO_RELEVANT_CARD":
        cards = []
        support_status = "NO_RELEVANT_CARD"
    formal_retrieved = knowledge_binding.get("selected_card_paths") or []
    formal_loaded = knowledge_binding.get("loaded_card_ids") or []
    checked_inputs = [
        f"`{item.get('path', '')}`"
        for item in result.get("input_files_checked", [])
        if isinstance(item, dict)
    ]
    if support_status == "CONSUMED":
        support_status = "READY"
    input_refs = [
        item.get("path", "")
        for item in result.get("input_files_checked", [])
        if isinstance(item, dict) and item.get("exists") and item.get("read")
    ]
    claim_boundary = " ".join(
        str(value).strip()
        for value in (
            modeling_boundary.get("frozen_assumptions"),
            modeling_boundary.get("prohibited_scope_expansion"),
            modeling_boundary.get("cross_subproblem_boundaries"),
        )
        if value and str(value).strip()
    ) or "NOT_AVAILABLE"

    lines = [
        "# MODEL_BUILDING_HANDOFF",
        "",
        "This file is an automatically generated formal model-building input contract.",
        "It is not a human gate, approval state, workflow state, or freeze artifact.",
        "",
        "## Case",
        "",
        f"- `case_id`: `{result.get('case_id', '')}`",
        f"- `problem_scope`: {result.get('problem_name', '')}",
        f"- `current_phase`: `{target_stage}`",
        f"- `input_files_checked`: {', '.join(checked_inputs) or 'none recorded'}",
        f"- Generated from orchestration source phase `{source_stage}` for formal target `{target_stage}`; the source phase is informational only.",
        "",
        "## Formal Input Contract Fields",
        "",
        f"- `CASE_ID`: `{result.get('case_id', '')}`",
        f"- `PROBLEM_CONTRACT_REF`: `{problem_contract.get('source_path', 'NOT_AVAILABLE')}`",
        "- `ROUTER_RESULT_REF`: `routing/problem_type_routing_result.json`",
        f"- `KNOWLEDGE_CARD_STATUS`: `{knowledge_binding.get('status') or support_status}`",
        f"- `RELEVANT_CARD_IDS`: `{', '.join(card.get('card_id', '') for card in cards) or 'NONE'}`",
        f"- `RELEVANT_CARD_CONTENT`: `{'INLINE_BELOW' if cards else 'NONE'}`",
        f"- `MODEL_INPUT_DATA_REFS`: `{', '.join(input_refs) or 'NONE'}`",
        f"- `CURRENT_CLAIM_BOUNDARY`: {claim_boundary}",
        "- `HUMAN_DECISION_REQUIRED`: `YES`",
        "",
        "## Problem Contract",
        "",
        f"- `SOURCE_ARTIFACT`: `{problem_contract.get('source_path', 'NOT_AVAILABLE')}`",
        f"- `case_task_scope`: {problem_contract.get('case_task_scope', '')}",
        f"- `subproblem_scope`: {problem_contract.get('subproblem_scope', '')}",
        f"- `relevant_constraints`: {problem_contract.get('relevant_constraints', '')}",
        f"- `data_boundary`: {problem_contract.get('data_boundary', '')}",
        f"- `human_confirmed_interpretation`: {problem_contract.get('human_confirmed_interpretation', '')}",
        "- The problem contract is the authoritative task/decomposition input; chat text, old review summaries, and paper prose are not substitutes.",
        "",
        "## Route",
        "",
        f"- `ROUTER_CANDIDATES`: {', '.join(approved_route.get('router_candidates') or []) or 'NONE_RECORDED'}",
        f"- `APPROVED_ROUTE_STATUS`: `{approved_route.get('status', 'NOT_AVAILABLE')}`",
        f"- `HUMAN_APPROVED_ROUTE`: `{approved_route.get('human_approved_route') or 'NOT_AVAILABLE'}`",
        f"- `selected_model_route`: `{approved_route.get('human_approved_route') or 'NOT_AVAILABLE'}`",
        f"- `approved_route_option`: `{approved_route.get('selected_option') or 'NOT_APPLICABLE'}`",
        f"- `approved_route_source`: `{approved_route.get('source') or 'NOT_AVAILABLE'}`",
        f"- `why_selected`: {approved_route.get('why_selected') or 'NOT_AVAILABLE'}",
        f"- `rejected_alternatives`: {'；'.join(approved_route.get('rejected_alternatives') or []) or 'NONE_RECORDED'}",
        f"- `route_limitations`: {approved_route.get('limitations') or 'NOT_AVAILABLE'}",
        f"- `router_route_source`: `{result.get('route_source', '')}`",
        f"- `route_conflict_detected`: `{str(bool(result.get('route_conflict_detected', False))).lower()}`",
        "- `route_approval_boundary`: router output is not a substitute for the required human model-choice review.",
        "- The formal modeling executor must use the human-approved route and must not silently promote an unapproved router suggestion.",
        "",
        "## Knowledge Support",
        "",
        f"- `KNOWLEDGE_SUPPORT_STATUS`: `{knowledge_binding.get('status') or support_status}`",
        f"- `support_chain_source`: `{knowledge_binding.get('support_chain_source') or 'NOT_AVAILABLE'}`",
        f"- `selected_card_paths`: {', '.join(knowledge_binding.get('selected_card_paths') or []) or 'NONE'}",
        f"- `RETRIEVED_COUNT`: `{len(evidence.get('retrieved_card_ids', []))}`",
        f"- `LOADED_COUNT`: `{len(evidence.get('loaded_card_ids', []))}`",
        f"- `HANDOFF_INCLUDED_COUNT`: `{len(cards)}`",
        f"- `FULL_LIBRARY_BULK_LOAD`: `{str(bool(evidence.get('full_library_bulk_load', False))).upper()}`",
        "- Knowledge cards are reference support, not mandatory commands. The modeling executor must record `USED` or `REJECTED` decisions after modeling.",
        "",
    ]

    if cards:
        for card in cards:
            card_id = str(card.get("card_id", ""))
            title = str(card.get("title", card_id))
            body = str(card.get("relevant_content", "")).strip()
            lines.extend(
                [
                    f"### {card_id}",
                    "",
                    f"Title: {title}",
                    "",
                    "Relevant content:",
                    "",
                    body,
                    "",
                    "Why relevant:",
                    f"Selected by the `{result.get('final_model_route', '')}` support chain for this case; final use remains a modeling decision.",
                    "",
                ]
            )
    elif support_status == "NO_RELEVANT_CARD":
        lines.extend(
            [
                "No route-selected knowledge card applies to this modeling scope.",
                "Proceed normally without injecting an unrelated card.",
                "",
            ]
        )

    missing = [] if knowledge_binding.get("status") == "NO_RELEVANT_CARD" else (evidence.get("missing_cards") or [])
    unreadable = [] if knowledge_binding.get("status") == "NO_RELEVANT_CARD" else (evidence.get("unreadable_cards") or [])
    if missing or unreadable:
        lines.extend(
            [
                "### Knowledge Support Loading Errors",
                "",
                "The following selected card bodies were not loaded. Surface this explicit error; do not silently skip, substitute, or bulk-search the library.",
                "",
            ]
        )
        for item in [*missing, *unreadable]:
            lines.append(
                f"- `{item.get('status', 'KNOWLEDGE_CARD_BODY_UNREADABLE')}`: `{item.get('card_id', '')}` at `{item.get('path', '')}`"
            )
        lines.append("")

    lines.extend(
        [
            "## Modeling Boundary",
            "",
            f"- `frozen_assumptions`: {modeling_boundary.get('frozen_assumptions') or 'NOT_AVAILABLE'}",
            f"- `prohibited_scope_expansion`: {modeling_boundary.get('prohibited_scope_expansion') or 'NOT_AVAILABLE'}",
            f"- `cross_subproblem_boundaries`: {modeling_boundary.get('cross_subproblem_boundaries') or 'NOT_AVAILABLE'}",
            f"- `current_stage_constraints`: {modeling_boundary.get('current_stage_constraints') or 'NOT_AVAILABLE'}",
            f"- `gate_boundary`: {modeling_boundary.get('gate_boundary') or 'NOT_AVAILABLE'}",
            f"- `current_stage`: `{target_stage}`",
            "- `mode_boundary`: `FORMAL_MODEL_BUILDING_INPUT / MODEL_IMPLEMENTATION_TARGET / NO_SOLVER_EXECUTION`",
            f"- `human_gate_required`: `{str(bool(result.get('human_gate_required', True))).lower()}`",
            "- This handoff does not approve a route, create a gate, or authorize solver execution.",
            "- Preserve the case problem contract, frozen assumptions, question boundaries, and explicit data limitations.",
            "- Do not invent data, formulas, model results, or literature support.",
            "",
            "## Consumption Evidence",
            "",
            f"- `RETRIEVED`: `{', '.join(formal_retrieved) or 'NONE'}`",
            f"- `LOADED`: `{', '.join(formal_loaded) or 'NONE'}`",
            f"- `HANDOFF_INCLUDED`: `{', '.join(card.get('card_id', '') for card in cards) or 'NONE'}`",
            "- `CODEX_READ`: `PENDING_FORMAL_MODEL_BUILDING_EXECUTION`",
            "- `USED / REJECTED`: record after the formal modeling execution; do not force adoption of a card method.",
            "",
            "## Source Identity Provenance",
            "",
            "Stable formal authorities are hash-bound and fail closed. Orchestration metadata is retained for information and does not invalidate this handoff after a legal transition commit.",
            "",
            "| SOURCE_ARTIFACT | SOURCE_SECTION / FIELD | HANDOFF_FIELD | BINDING | SHA256 | STATUS |",
            "|---|---|---|---|---|---|",
        ]
    )
    for item in provenance:
        lines.append(
            f"| `{item.get('path', '')}` | `{item.get('source_section', '')}` | `{item.get('handoff_field', '')}` | `{item.get('binding') or 'STABLE_FORMAL_AUTHORITY'}` | `{item.get('sha256') or 'NOT_AVAILABLE'}` | `{item.get('status', '')}` |"
        )
    receipt = formal_binding.get("receipt_contract") or {}
    lines.extend(
        [
            "",
            "## Formal Model-Building Input Receipt Contract",
            "",
            f"- `RECEIPT_NAME`: `{receipt.get('receipt_name', 'FORMAL_MODEL_BUILDING_INPUT_RECEIPT')}`",
            f"- `RECEIPT_STATUS`: `{receipt.get('status', 'PENDING_REAL_CODEX_SESSION')}`",
            f"- `REQUIRED_FIELDS`: `{', '.join(receipt.get('required_fields') or [])}`",
            f"- `RECEIPT_GENERATED_BY`: `{receipt.get('generated_by', 'FORMAL_CODEX_MODEL_BUILDING_SESSION')}`",
            f"- `PROHIBITED_AUTOMATION`: {receipt.get('prohibited_automation', '')}",
            "- The router may declare the contract and leave the status pending; it must not create a proven receipt when Codex has not run the formal modeling session.",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def validate_model_building_handoff(case_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
    """Prove that the written handoff contains the router-selected inputs."""
    handoff_path = Path(case_dir) / "handoff" / "MODEL_BUILDING_HANDOFF.md"
    if not handoff_path.is_file():
        raise ValueError("MODEL_BUILDING_HANDOFF_MISSING")
    handoff_text = handoff_path.read_text(encoding="utf-8")
    formal_binding = result.get("formal_input_binding") or {}
    problem_contract = formal_binding.get("problem_contract") or {}
    knowledge_binding = formal_binding.get("knowledge_support") or {}
    context = result.get("support_chain_context") or {}
    model_context = context.get("model_building_context") or {}
    cards = model_context.get("cards") or []
    required_fields = (
        "CASE_ID",
        "PROBLEM_CONTRACT_REF",
        "ROUTER_RESULT_REF",
        "KNOWLEDGE_CARD_STATUS",
        "RELEVANT_CARD_IDS",
        "RELEVANT_CARD_CONTENT",
        "MODEL_INPUT_DATA_REFS",
        "CURRENT_CLAIM_BOUNDARY",
        "HUMAN_DECISION_REQUIRED",
    )
    missing = [field for field in required_fields if f"`{field}`" not in handoff_text]
    if missing:
        raise ValueError(f"MODEL_BUILDING_HANDOFF_FIELDS_MISSING: {', '.join(missing)}")
    if "`HUMAN_DECISION_REQUIRED`: `YES`" not in handoff_text:
        raise ValueError("HUMAN_MODELING_GATE_NOT_PRESERVED")
    target_match = re.search(
        r"(?im)^-[ \t]*`current_phase`:[ \t]*`([^`]*)`[ \t]*$",
        handoff_text,
    )
    if not target_match or target_match.group(1).strip().upper() != "MODEL_IMPLEMENTATION":
        raise ValueError("MODEL_BUILDING_HANDOFF_TARGET_STAGE_MISMATCH")
    if "ROUTER_ONLY / NO_MODEL_EXECUTION" in handoff_text:
        raise ValueError("MODEL_BUILDING_HANDOFF_ROUTER_PHASE_LEAK")
    for section_name, values in (
        ("PROBLEM_CONTRACT", problem_contract),
        ("MODELING_BOUNDARY", formal_binding.get("modeling_boundary") or {}),
    ):
        if any(str(value).strip() == "未找到该来源段落。" for value in values.values()):
            raise ValueError(f"MODEL_BUILDING_HANDOFF_{section_name}_INCOMPLETE")
    expected_route = str(
        (formal_binding.get("approved_route") or {}).get("human_approved_route") or ""
    ).strip()
    if expected_route:
        route_match = re.search(
            r"(?im)^-[ \t]*`selected_model_route`:[ \t]*`([^`]*)`[ \t]*$",
            handoff_text,
        )
        if not route_match or route_match.group(1).strip() != expected_route:
            raise ValueError("MODEL_BUILDING_HANDOFF_SELECTED_ROUTE_MISMATCH")

    # Stable formal authorities remain hash-bound and fail closed. Normal
    # transition metadata is rendered for audit only and cannot invalidate a
    # valid model-input contract after a legal stage commit.
    provenance = formal_binding.get("source_identity_provenance") or []
    for item in provenance:
        if not isinstance(item, dict) or item.get("status") != "SOURCE_ARTIFACT_READ":
            continue
        relative = str(item.get("path") or "")
        expected_sha = str(item.get("sha256") or "")
        if not relative or not expected_sha:
            continue
        escaped_path = re.escape(relative)
        if item.get("binding") == "INFORMATIONAL_NON_INVALIDATING":
            if not re.search(rf"(?m)^\|\s*`{escaped_path}`\s*\|", handoff_text):
                raise ValueError(f"MODEL_BUILDING_HANDOFF_SOURCE_RECORD_MISSING: {relative}")
            continue
        escaped_sha = re.escape(expected_sha)
        row_pattern = rf"(?m)^\|\s*`{escaped_path}`\s*\|.*\|\s*`{escaped_sha}`\s*\|"
        if not re.search(row_pattern, handoff_text):
            raise ValueError(f"MODEL_BUILDING_HANDOFF_SOURCE_HASH_MISMATCH: {relative}")
        current = _current_formal_source_identity(Path(case_dir), item)
        if current.get("status") != "SOURCE_ARTIFACT_READ" or current.get("sha256") != expected_sha:
            raise ValueError(f"MODEL_BUILDING_HANDOFF_SOURCE_CHANGED: {relative}")
    if expected_route:
        expected_gate_boundary = str(
            (formal_binding.get("modeling_boundary") or {}).get("gate_boundary") or ""
        ).strip()
        if (
            expected_gate_boundary
            and expected_gate_boundary != "未找到该来源段落。"
            and expected_gate_boundary not in handoff_text
        ):
            raise ValueError("MODEL_BUILDING_HANDOFF_GATE_BOUNDARY_MISMATCH")
    knowledge_status = str(knowledge_binding.get("status") or "UNKNOWN")
    if knowledge_status == "NO_RELEVANT_CARD":
        card_payload: list[dict[str, str]] = []
    else:
        if knowledge_status not in {"CONSUMED", "READY"}:
            raise ValueError(f"KNOWLEDGE_CARD_STATUS_INVALID: {knowledge_status}")
        if not cards:
            raise ValueError("KNOWLEDGE_CARD_BODY_NOT_AVAILABLE")
        card_payload = []
        for card in cards:
            body = str(card.get("relevant_content", "")).strip()
            if not body or body not in handoff_text:
                raise ValueError(
                    f"KNOWLEDGE_CARD_BODY_NOT_IN_HANDOFF: {card.get('card_id', '')}"
                )
            card_payload.append(
                {
                    "card_id": str(card.get("card_id", "")),
                    "title": str(card.get("title", "")),
                    "relevant_content": body,
                }
            )
    contract_ref = str(problem_contract.get("source_path") or "")
    return {
        "case_id": result.get("case_id"),
        "problem_contract_ref": contract_ref,
        "router_result_ref": "routing/problem_type_routing_result.json",
        "knowledge_card_status": knowledge_status,
        "relevant_card_ids": [item["card_id"] for item in card_payload],
        "relevant_card_content": card_payload,
        "model_input_data_refs": [
            item.get("path", "")
            for item in result.get("input_files_checked", [])
            if isinstance(item, dict) and item.get("exists") and item.get("read")
        ],
        "current_claim_boundary": str(
            (formal_binding.get("modeling_boundary") or {}).get("prohibited_scope_expansion")
            or "NOT_AVAILABLE"
        ),
        "human_decision_required": "YES",
        "handoff_path": handoff_path.as_posix(),
        "handoff_sha256": _sha256_file(handoff_path),
        "status": "PASS",
    }


def write_model_building_handoff(case_dir: Path, result: dict[str, Any]) -> Path:
    """Write the formal model-building input only after route/Gate 2 checks."""

    handoff_path = Path(case_dir) / "handoff" / "MODEL_BUILDING_HANDOFF.md"
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text(render_model_building_handoff(result), encoding="utf-8")
    return handoff_path


def write_case_outputs(case_dir: Path, result: dict[str, Any]) -> None:
    routing_dir = case_dir / "routing"
    routing_dir.mkdir(parents=True, exist_ok=True)
    routing_result_path = routing_dir / "problem_type_routing_result.json"
    existing_result = read_json(routing_result_path) if routing_result_path.is_file() else None
    if isinstance(existing_result, dict) and existing_result.get("generated_at"):
        existing_semantic = {
            key: value for key, value in existing_result.items() if key != "generated_at"
        }
        current_semantic = {key: value for key, value in result.items() if key != "generated_at"}
        if existing_semantic == current_semantic:
            result["generated_at"] = existing_result["generated_at"]
    problem_type_payload = {
        "primary_type": result["primary_problem_type"],
        "secondary_types": result["secondary_problem_types"],
        "data_dependency": result["data_dependency"],
        "risk_level": result["risk_level"],
        "recommended_model_families": list(
            dict.fromkeys(
                result["recommended_baseline_models"] + result["recommended_advanced_models"]
            )
        ),
        "human_questions": result["human_questions"],
        "recommended_knowledge_cards": result["recommended_knowledge_cards"],
        "knowledge_card_reasoning": result["knowledge_card_reasoning"],
        "high_risk_theory_flags": result["high_risk_theory_flags"],
        "status": "ROUTED",
        "routing_result_ref": "routing/problem_type_routing_result.json",
    }
    (case_dir / "problem_type.json").write_text(
        json.dumps(problem_type_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    routing_result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    top_scores = result["type_scores"][:8]
    routing_report = f"""# 题型路由结果报告

状态：`PASS_WITH_LIMITATION / ROUTER_ASSET_REUSE_KNOWLEDGE_CARD_PATCH_READY`

标记：`ROUTER_ONLY / NO_MODEL_EXECUTION / NOT_FOR_FINAL_SUBMISSION`

## 1. 路由结论

- case_id：`{result['case_id']}`
- 题目：{result['problem_name']}
- 主题型：`{result['primary_problem_type']}`（{result['primary_problem_type_name']}）
- 辅助题型：{", ".join("`" + item + "`" for item in result['secondary_problem_types'])}
- 置信等级：`{result['confidence']}`
- 人工闸门：`human_gate_required=true`
- final_submit：`final_submit_allowed=false`

## 2. 题型得分

{markdown_table(top_scores, [('problem_type', '题型'), ('problem_type_name', '中文名称'), ('score', '得分'), ('matched_reasons', '主要命中依据')])}

## 3. 输入文件检查

{markdown_table(result['input_files_checked'], [('path', '文件'), ('exists', '是否存在'), ('read', '是否读取')])}

## 4. 当前边界

当前路由器只读取传入 case 的 Problem Contract、题意分析、数据审计或 schema 和 case_config，不读取其他 case，不引用机制 fixture，不运行模型。

## 5. 资产复用摘要

- 已复用资产数量：{len(result['reused_assets'])}
- 缺失资产数量：{len(result['missing_assets'])}
- 资产复用说明：`{result['asset_reuse_note_path']}`
- 知识卡支持报告：`routing/knowledge_card_support_report.md`
"""
    (routing_dir / "problem_type_routing_report.md").write_text(routing_report, encoding="utf-8")

    routes = result["recommended_problem_routes"]
    model_report = f"""# 建模路线建议

状态：`PASS_WITH_LIMITATION / ROUTER_ASSET_REUSE_KNOWLEDGE_CARD_PATCH_READY`

标记：`ROUTER_ONLY / NO_MODEL_EXECUTION / NOT_FOR_FINAL_SUBMISSION`

## 1. 分问题路线

{markdown_table(routes, [('problem', '子问题'), ('route', '建议路线'), ('scope_note', '边界说明')])}

## 2. baseline 模型候选

{chr(10).join(f'- {item}' for item in result['recommended_baseline_models'])}

## 3. advanced 模型候选

{chr(10).join(f'- {item}' for item in result['recommended_advanced_models'])}

## 4. 不得越界

- 当前输出只是题型与模型族建议，不是候选路线的真实比较结果。
- 不得把 router suggestion 写成人工已选择路线。
- 不得运行模型、生成结果或进入论文流程。
- 必须等待 `model-route-generator` 产物和 Gate 2 人工选择。

## 5. 知识卡支撑

{chr(10).join(f"- {item['theme']}：{item['model_route_supported']}" for item in result['knowledge_card_support'])}
"""
    (routing_dir / "model_route_recommendation.md").write_text(model_report, encoding="utf-8")

    validation_report = f"""# 验证与敏感性建议

状态：`PASS_WITH_LIMITATION / ROUTER_ASSET_REUSE_KNOWLEDGE_CARD_PATCH_READY`

标记：`ROUTER_ONLY / NO_MODEL_EXECUTION / NOT_FOR_FINAL_SUBMISSION`

## 1. 推荐验证指标

{chr(10).join(f'- {item}' for item in result['recommended_validation_metrics'])}

## 2. 推荐敏感性检查

{chr(10).join(f'- {item}' for item in result['recommended_sensitivity_checks'])}

## 3. 当前 case 重点

{chr(10).join(f'- {item}' for item in result['high_risk_theory_flags'])}

## 4. 资产复用提示

验证指标来自 `model_library` 的验证指标目录，论文质量边界来自 `paper_quality` 规则。知识卡仅用于路线解释和理论支撑，不替代输出证据。
"""
    (routing_dir / "validation_plan_recommendation.md").write_text(validation_report, encoding="utf-8")

    paper_report = f"""# 论文结构建议

状态：`PASS_WITH_LIMITATION / ROUTER_ASSET_REUSE_KNOWLEDGE_CARD_PATCH_READY`

标记：`ROUTER_ONLY / NO_MODEL_EXECUTION / NOT_FOR_FINAL_SUBMISSION`

## 1. 推荐章节

{chr(10).join(f'- {item}' for item in result['recommended_paper_sections'])}

## 2. 当前阶段写作边界

- 路由阶段不得生成论文正文或把候选方法写成已采用模型。
- 未运行的模型不得写成已验证，router suggestion 不得写成人工最终选择。
- 当前只能作为内部路线准备材料，不能进入正式提交。

## 3. 论文质量层联动

- 证据链检查参考 `paper_quality/evidence_chain_quality_rules.md`。
- 公式符号检查参考 `paper_quality/formula_symbol_quality_rules.md`。
- 结论边界检查参考 `paper_quality/conclusion_boundary_rules.md`。
- AI 使用披露按 `showcase_mode` 与 `official_submission_mode` 分流。
"""
    (routing_dir / "paper_structure_recommendation.md").write_text(paper_report, encoding="utf-8")

    asset_report = f"""# 资产复用说明

状态：`PASS_WITH_LIMITATION / ROUTER_ASSET_REUSE_KNOWLEDGE_CARD_PATCH_READY`

标记：`ROUTER_ONLY / NO_MODEL_EXECUTION / NOT_FOR_FINAL_SUBMISSION`

## 1. 本轮复用资产

{markdown_table(result['reused_assets'], [('path', '资产路径'), ('purpose', '用途'), ('required', '是否必需')])}

## 2. 未复用或缺失资产

{markdown_table(result['missing_assets'], [('path', '资产路径'), ('purpose', '用途'), ('required', '是否必需')])}

## 3. 未复用原因

- `pipeline_core/knowledge_cards/` 下的显式映射文件当前不存在，因此未伪造映射。
- 已使用 `pipeline_core/literature_library/knowledge_cards/` 作为可用知识卡来源。
- 知识卡只用于理论支撑和路线解释，不等于正式参考文献。

## 4. 重复造轮子风险

当前 router 已显式复用模型库、验证目录、敏感性目录、论文质量层和 AI 披露规则。后续第M步模型路线执行器必须继续读取本文件、`problem_type_routing_result.json` 和 `knowledge_card_support_report.md`，避免重新手写一套路线判断规则。

## 5. 是否允许进入第M步

允许进入第M步模型路线执行器 baseline，但第M步仍必须先执行 dry-run，不得直接运行模型或创建 `final_submit/`。
"""
    (routing_dir / "asset_reuse_note.md").write_text(asset_report, encoding="utf-8")

    support_rows = [
        {
            "theme": item["theme"],
            "problem_scope": item["problem_scope"],
            "model_route_supported": item["model_route_supported"],
            "validation_supported": item["validation_supported"],
            "knowledge_card_gap": item["knowledge_card_gap"],
            "human_review_required": item["human_review_required"],
        }
        for item in result["knowledge_card_support"]
    ]
    card_report = f"""# 知识卡支撑报告

状态：`PASS_WITH_LIMITATION / ROUTER_ASSET_REUSE_KNOWLEDGE_CARD_PATCH_READY`

标记：`ROUTER_ONLY / NO_MODEL_EXECUTION / NOT_FOR_FINAL_SUBMISSION`

## 1. 当前 case 题型口径

- 主题型：`{result['primary_problem_type']}`
- 辅助题型：{", ".join("`" + item + "`" for item in result['secondary_problem_types']) or "无"}
- 选择理由：{result['knowledge_card_reasoning']}

## 2. 知识卡主题支持

{markdown_table(support_rows, [('theme', '知识卡主题'), ('problem_scope', '对应问题'), ('model_route_supported', '支撑路线'), ('validation_supported', '支撑验证'), ('knowledge_card_gap', '是否存在缺口'), ('human_review_required', '是否需人工复核')])}

## 3. 使用边界

- 知识卡只用于理论支撑、路线解释、敏感性设计和边界提醒。
- 知识卡不等于正式参考文献。
- 正式论文如引用教材或文献，仍需执行 citation integrity 检查。
- 任何具体赛题数值结论必须来自代码、输出表、验证报告和 evidence map。
- `final_submit_allowed=false`。
"""
    (routing_dir / "knowledge_card_support_report.md").write_text(card_report, encoding="utf-8")

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MathModeling2026 题型路由器 baseline")
    parser.add_argument("--case-dir", required=True, help="当前 case 目录，例如 cases/active_competition/current_case")
    parser.add_argument("--dry-run", action="store_true", help="只打印路由结果，不写入 case 输出")
    parser.add_argument("--write", action="store_true", help="写入 case/routing 路由结果")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.dry_run and args.write:
        raise SystemExit("--dry-run 与 --write 不能同时使用")
    mode = "write" if args.write else "dry-run"
    case_dir = Path(args.case_dir)
    if not case_dir.exists() or not case_dir.is_dir():
        raise SystemExit(f"case-dir 不存在或不是目录：{case_dir}")

    try:
        result = build_output(case_dir)
    except ProblemContractBlocked as exc:
        raise SystemExit(str(exc)) from exc
    if mode == "write":
        write_case_outputs(case_dir, result)
        print(f"WROTE routing outputs to {case_dir / 'routing'}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
