"""Fail-closed model-route generation handoff and artifact validation.

The Python pipeline does not invent semantic modeling routes.  It prepares a
case-bound execution contract for the VSCode/Codex executor, validates the
returned route artifacts, and preserves the human Gate 2 boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from pipeline_core.python_tools.human_gate import evaluate_human_gate


HANDOFF_RELATIVE_PATH = "handoff/MODEL_ROUTE_GENERATION_HANDOFF.md"
ROUTES_RELATIVE_PATH = "model_routes.md"
SCORE_RELATIVE_PATH = "model_route_score.xlsx"
GATE2_RELATIVE_PATH = "human_review/gate_2_model_choice.md"
REQUIRED_PROBLEM_TYPE_FIELDS = (
    "primary_type",
    "secondary_types",
    "data_dependency",
    "risk_level",
    "recommended_model_families",
    "human_questions",
    "recommended_knowledge_cards",
    "knowledge_card_reasoning",
    "high_risk_theory_flags",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _bound_case_file(root: Path, relative_path: str, *, missing_reason: str) -> Path:
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("MODEL_ROUTE_INPUT_OUTSIDE_CASE") from exc
    if not candidate.is_file():
        raise ValueError(missing_reason)
    return candidate


def validate_problem_type_artifact(case_dir: str | Path) -> dict[str, Any]:
    root = Path(case_dir).resolve()
    path = root / "problem_type.json"
    payload = _read_json(path)
    if payload is None:
        return {
            "status": "FAIL",
            "reason": "PROBLEM_TYPE_ARTIFACT_MISSING_OR_INVALID",
            "path": "problem_type.json",
        }
    missing = [field for field in REQUIRED_PROBLEM_TYPE_FIELDS if field not in payload]
    if missing:
        return {
            "status": "FAIL",
            "reason": "PROBLEM_TYPE_REQUIRED_FIELDS_MISSING",
            "path": "problem_type.json",
            "missing_fields": missing,
        }
    if not isinstance(payload.get("primary_type"), str) or not payload["primary_type"].strip():
        return {
            "status": "FAIL",
            "reason": "PROBLEM_TYPE_PRIMARY_REQUIRED",
            "path": "problem_type.json",
        }
    if not isinstance(payload.get("knowledge_card_reasoning"), str) or not payload[
        "knowledge_card_reasoning"
    ].strip():
        return {
            "status": "FAIL",
            "reason": "KNOWLEDGE_CARD_REASONING_REQUIRED",
            "path": "problem_type.json",
        }
    for field in (
        "secondary_types",
        "recommended_model_families",
        "human_questions",
        "recommended_knowledge_cards",
        "high_risk_theory_flags",
    ):
        if not isinstance(payload.get(field), list):
            return {
                "status": "FAIL",
                "reason": "PROBLEM_TYPE_FIELD_TYPE_INVALID",
                "path": "problem_type.json",
                "field": field,
            }
    return {
        "status": "PASS",
        "reason": "PROBLEM_TYPE_ARTIFACT_VALID",
        "path": "problem_type.json",
        "sha256": _sha256(path),
        "payload": payload,
    }


def render_model_route_generation_handoff(
    case_dir: str | Path,
    router_result: dict[str, Any],
) -> str:
    root = Path(case_dir).resolve()
    config = _read_json(root / "case_config.json") or {}
    problem_type = validate_problem_type_artifact(root)
    if problem_type.get("status") != "PASS":
        raise ValueError(str(problem_type.get("reason")))
    payload = problem_type["payload"]
    cards = payload.get("recommended_knowledge_cards") or []
    flags = payload.get("high_risk_theory_flags") or []
    contract = router_result.get("problem_contract_validation") or {}
    contract_relative = str(contract.get("path") or "problem/problem_contract.md")
    analysis_relative = str(config.get("problem_analysis_path") or "problem_analysis.md")
    contract_path = _bound_case_file(
        root, contract_relative, missing_reason="PROBLEM_CONTRACT_INPUT_MISSING"
    )
    analysis_path = _bound_case_file(
        root, analysis_relative, missing_reason="PROBLEM_ANALYSIS_INPUT_MISSING"
    )
    config_path = _bound_case_file(
        root, "case_config.json", missing_reason="CASE_CONFIG_INPUT_MISSING"
    )
    router_path = _bound_case_file(
        root,
        "routing/problem_type_routing_result.json",
        missing_reason="ROUTER_RESULT_INPUT_MISSING",
    )
    data_availability = {
        "source_policy": config.get("source_policy"),
        "problem_statement_files": config.get("problem_statement_files", []),
        "attachment_files": config.get("attachment_files", []),
    }
    upstream_artifacts: list[dict[str, str]] = []
    for relative in (
        "model/model_formulation.md",
        "model_critique.md",
        "data_audit_report.md",
    ):
        candidate = (root / relative).resolve()
        if candidate.is_file():
            upstream_artifacts.append({"path": relative, "sha256": _sha256(candidate)})
    return f"""# Model Route Generation Handoff

`HANDOFF_SCHEMA`: `model_route_generation_handoff_v1`

`CASE_ID`: `{config.get('case_id', root.name)}`

`EXECUTOR`: `VSCODE_MODEL_ROUTE_EXECUTOR`

`HUMAN_GATE_1`: `PASSED_BY_HUMAN`

`HUMAN_GATE_2`: `PENDING_MANUAL_REVIEW`

`FINAL_MODEL_SELECTED`: `NO`

`SOLVER_ALLOWED`: `NO`

## Bound Inputs

- `PROBLEM_CONTRACT_PATH`: `{contract_relative}`
- `PROBLEM_CONTRACT_SHA256`: `{_sha256(contract_path)}`
- `PROBLEM_ANALYSIS_PATH`: `{analysis_relative}`
- `PROBLEM_ANALYSIS_SHA256`: `{_sha256(analysis_path)}`
- `CASE_CONFIG_PATH`: `case_config.json`
- `CASE_CONFIG_SHA256`: `{_sha256(config_path)}`
- `PROBLEM_TYPE_PATH`: `problem_type.json`
- `PROBLEM_TYPE_SHA256`: `{problem_type.get('sha256', '')}`
- `ROUTER_RESULT_PATH`: `routing/problem_type_routing_result.json`
- `ROUTER_RESULT_SHA256`: `{_sha256(router_path)}`
- `DATA_AVAILABILITY`: `{json.dumps(data_availability, ensure_ascii=False)}`
- `UPSTREAM_ARTIFACTS`: `{json.dumps(upstream_artifacts, ensure_ascii=False)}`
- `PRIMARY_TYPE`: `{payload.get('primary_type')}`
- `SECONDARY_TYPES`: `{json.dumps(payload.get('secondary_types', []), ensure_ascii=False)}`
- `RECOMMENDED_KNOWLEDGE_CARDS`: `{json.dumps(cards, ensure_ascii=False)}`
- `HIGH_RISK_THEORY_FLAGS`: `{json.dumps(flags, ensure_ascii=False)}`

## Required Executor Action

Use the repository `model-route-generator` skill to generate only genuinely
compared candidate routes.  The number of routes is determined by real
comparability; do not fill unused A-E slots.  Each recorded route must cover
its problem scope, variables, objective or residual, constraints, core formula
framework, required data, algorithm proposal, implementation cost, validation,
interpretability, failure risk, knowledge-card support and theory-check need.
Read every bound input above, including every existing item in
`UPSTREAM_ARTIFACTS`; do not weaken or silently discard a passed model-review
boundary.

## Required Outputs

- `{ROUTES_RELATIVE_PATH}`
- `{SCORE_RELATIVE_PATH}`
- pending questions in `{GATE2_RELATIVE_PATH}`

The workbook must be a real XLSX file.  Route names and count must match the
Markdown artifact.  Score direction and weights must be explicit.
Each Markdown route heading must use exactly `## 路线 <route_id>：<route_name>`
(or `## Route <route_id>: <route_name>`).  The full heading value after
`路线`/`Route` must appear unchanged in the workbook.

## Forbidden Actions

- Do not select or approve the final route.
- Do not check Gate 2 boxes or fill human identity/time fields.
- In Gate 2, only add pending questions; do not fill `选择路线` or `选择理由`.
- Do not create solver code, run MATLAB/Python models, generate results, paper,
  references or final_submit.
- Do not modify pipeline_core, knowledge cards, another case, or any human
  approval field.
- Do not claim an unrun model is validated, converged, optimal or superior.

After the executor writes the outputs, rerun the public model-stage program.
The program, not the prompt, validates artifacts and decides whether the case
may wait for Gate 2.
"""


def write_model_route_generation_handoff(
    case_dir: str | Path,
    router_result: dict[str, Any],
) -> dict[str, Any]:
    root = Path(case_dir).resolve()
    path = root / HANDOFF_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    text = render_model_route_generation_handoff(root, router_result)
    path.write_text(text, encoding="utf-8")
    return {
        "status": "PASS",
        "reason": "MODEL_ROUTE_GENERATION_HANDOFF_READY",
        "path": HANDOFF_RELATIVE_PATH,
        "sha256": _sha256(path),
    }


def _route_names(text: str) -> list[str]:
    names: list[str] = []
    pattern = re.compile(
        r"(?im)^##[ \t]+(?P<label>route|路线)[ \t]+(?P<value>[^\r\n]*)$"
    )
    for match in pattern.finditer(text):
        label = match.group("label").casefold()
        name = match.group("value").strip()
        separator = ":" if label == "route" else "："
        if not re.fullmatch(
            rf"[^\s:：]+[ \t]*{re.escape(separator)}[ \t]*\S(?:.*\S)?",
            name,
        ):
            raise ValueError("MODEL_ROUTE_HEADING_FORMAT_INVALID")
        if name and name not in names:
            names.append(name)
    return names


def _xlsx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as workbook:
        required = {
            "[Content_Types].xml",
            "_rels/.rels",
            "xl/workbook.xml",
            "xl/_rels/workbook.xml.rels",
        }
        if not required.issubset(set(workbook.namelist())):
            raise ValueError("MODEL_ROUTE_SCORE_XLSX_STRUCTURE_INVALID")
        if not any(name.startswith("xl/worksheets/sheet") and name.endswith(".xml") for name in workbook.namelist()):
            raise ValueError("MODEL_ROUTE_SCORE_XLSX_STRUCTURE_INVALID")
        pieces: list[str] = []
        for name in workbook.namelist():
            if name == "xl/sharedStrings.xml" or name.startswith("xl/worksheets/sheet"):
                try:
                    root = ElementTree.fromstring(workbook.read(name))
                except ElementTree.ParseError as exc:
                    raise ValueError("MODEL_ROUTE_SCORE_XLSX_XML_INVALID") from exc
                pieces.extend(node.text or "" for node in root.iter() if node.text)
        return "\n".join(pieces)


def _selected_route(gate_text: str) -> str:
    match = re.search(
        r"(?im)^[ \t]*(?:选择路线|selected[_ ]route)[ \t]*[:：=][ \t]*([^\r\n]*?)[ \t]*$",
        gate_text,
    )
    return match.group(1).strip() if match else ""


def _normalized_route_name(value: str) -> str:
    return re.sub(r"\s+", "", value).replace("：", ":").casefold()


def _resolve_selected_route(selected: str, route_names: list[str]) -> str | None:
    selected_key = _normalized_route_name(selected)
    if not selected_key:
        return None
    exact = [name for name in route_names if _normalized_route_name(name) == selected_key]
    if len(exact) == 1:
        return exact[0]
    route_ids: dict[str, list[str]] = {}
    for name in route_names:
        route_id = re.split(r"[\s:：]", name.strip(), maxsplit=1)[0]
        route_ids.setdefault(_normalized_route_name(route_id), []).append(name)
    id_matches = route_ids.get(selected_key, [])
    return id_matches[0] if len(id_matches) == 1 else None


def validate_model_route_outputs(case_dir: str | Path) -> dict[str, Any]:
    root = Path(case_dir).resolve()
    routes_path = root / ROUTES_RELATIVE_PATH
    score_path = root / SCORE_RELATIVE_PATH
    routes_placeholder = False
    if routes_path.is_file():
        try:
            routes_placeholder = "Status: waiting for confirmed problem analysis" in routes_path.read_text(
                encoding="utf-8-sig"
            )
        except (OSError, UnicodeError):
            routes_placeholder = False
    if (not routes_path.exists() or routes_placeholder) and not score_path.exists():
        return {
            "status": "AWAITING_EXECUTOR",
            "reason": "MODEL_ROUTE_OUTPUTS_NOT_GENERATED",
            "required_outputs": [ROUTES_RELATIVE_PATH, SCORE_RELATIVE_PATH],
        }
    if not routes_path.is_file() or not score_path.is_file():
        return {
            "status": "FAIL",
            "reason": "MODEL_ROUTE_OUTPUT_SET_INCOMPLETE",
            "missing_paths": [
                relative
                for relative, path in (
                    (ROUTES_RELATIVE_PATH, routes_path),
                    (SCORE_RELATIVE_PATH, score_path),
                )
                if not path.is_file()
            ],
        }
    try:
        routes_text = routes_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return {"status": "FAIL", "reason": "MODEL_ROUTES_UNREADABLE"}
    if "Status: waiting for confirmed problem analysis" in routes_text:
        return {"status": "FAIL", "reason": "MODEL_ROUTES_TEMPLATE_PLACEHOLDER_REMAINS"}
    try:
        route_names = _route_names(routes_text)
    except ValueError as exc:
        return {"status": "FAIL", "reason": str(exc)}
    if not route_names:
        return {"status": "FAIL", "reason": "MODEL_ROUTE_RECORDS_REQUIRED"}
    try:
        workbook_text = _xlsx_text(score_path)
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        return {
            "status": "FAIL",
            "reason": str(exc) if str(exc).startswith("MODEL_ROUTE_SCORE_") else "MODEL_ROUTE_SCORE_XLSX_INVALID",
        }
    missing_in_workbook = [name for name in route_names if name not in workbook_text]
    if missing_in_workbook:
        return {
            "status": "FAIL",
            "reason": "MODEL_ROUTE_NAME_MISMATCH",
            "missing_in_workbook": missing_in_workbook,
        }
    gate_path = root / GATE2_RELATIVE_PATH
    gate_text = gate_path.read_text(encoding="utf-8-sig") if gate_path.is_file() else ""
    checked_count = len(re.findall(r"(?im)^\s*-\s*\[[xX]\]", gate_text))
    gate = evaluate_human_gate(root, "GATE2")
    selected_route = _selected_route(gate_text)
    resolved_route = _resolve_selected_route(selected_route, route_names)
    if gate.get("gate_status") == "PASS" and not selected_route:
        status = "FAIL"
        reason = "GATE2_SELECTED_ROUTE_REQUIRED"
    elif gate.get("gate_status") == "PASS" and resolved_route is None:
        status = "FAIL"
        reason = "GATE2_SELECTED_ROUTE_NOT_IN_CANDIDATES"
    elif gate.get("gate_status") == "PASS":
        status = "READY_FOR_MODEL_IMPLEMENTATION"
        reason = "MODEL_ROUTES_VALID_GATE2_PASSED"
    else:
        status = "AWAITING_GATE2"
        reason = "MODEL_ROUTES_VALID_GATE2_PENDING"
    return {
        "status": status,
        "reason": reason,
        "route_count": len(route_names),
        "route_names": route_names,
        "gate2_selected_route": selected_route or None,
        "resolved_selected_route": resolved_route,
        "routes_sha256": _sha256(routes_path),
        "score_sha256": _sha256(score_path),
        "gate2_checked_count": checked_count,
        "gate2_evaluation": gate,
        "solver_allowed": status == "READY_FOR_MODEL_IMPLEMENTATION",
    }


def run_model_route_stage(
    case_dir: str | Path,
    router_result: dict[str, Any],
) -> dict[str, Any]:
    handoff = write_model_route_generation_handoff(case_dir, router_result)
    outputs = validate_model_route_outputs(case_dir)
    return {
        "stage": "model_route_generation",
        "status": outputs["status"],
        "handoff": handoff,
        "outputs": outputs,
        "human_gate2_required": True,
        "final_model_selected": outputs["status"] == "READY_FOR_MODEL_IMPLEMENTATION",
        "solver_allowed": outputs.get("solver_allowed", False),
    }
