"""Small, opt-in governance guards for competition model execution.

The guards validate case-declared metadata only.  They do not know any
problem-specific algorithm, do not change workflow stages, and keep cases
without an explicit governance contract on the legacy path.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any


GOVERNANCE_SCHEMA_VERSION = "competition_model_governance_v1"
_GOVERNANCE_KEYS = {
    "numerical_certification",
    "hard_feasibility_constraint",
    "optimization_order",
    "compute_budget",
}
_OBJECTIVE_NOT_STARTED = {"NOT_STARTED", "PENDING", "SKIPPED", "BLOCKED"}
_ROUTE_STATUSES = {"ACTIVE", "REJECTED_WITH_EVIDENCE", "ACCEPTED"}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().upper() in {"YES", "TRUE", "PASS", "APPROVED"}


def _declaration_state(value: Any) -> tuple[str, bool | None]:
    """Return VALID/INVALID and the enabled value for a contract declaration."""

    if isinstance(value, bool):
        return "VALID", value
    if isinstance(value, dict):
        enabled = value.get("enabled", True)
        if isinstance(enabled, bool):
            return "VALID", enabled
        if isinstance(enabled, str) and enabled.strip().upper() in {"YES", "TRUE", "NO", "FALSE"}:
            return "VALID", enabled.strip().upper() in {"YES", "TRUE"}
        return "INVALID", None
    if isinstance(value, str) and value.strip().upper() in {
        "YES", "TRUE", "NO", "FALSE"
    }:
        return "VALID", value.strip().upper() in {"YES", "TRUE"}
    return "INVALID", None


def _section(case_config: dict[str, Any], name: str) -> Any:
    nested = case_config.get("competition_governance")
    if isinstance(nested, dict) and name in nested:
        return nested[name]
    return case_config.get(name)


def _section_present(case_config: dict[str, Any], name: str) -> bool:
    nested = case_config.get("competition_governance")
    return (isinstance(nested, dict) and name in nested) or name in case_config


def governance_enabled(case_config: dict[str, Any]) -> bool:
    """Return whether the case explicitly opts into competition governance."""

    nested = case_config.get("competition_governance")
    if isinstance(nested, dict):
        return nested.get("enabled", True) is not False
    if isinstance(nested, bool):
        return nested
    if "competition_governance_enabled" in case_config:
        return _truthy(case_config["competition_governance_enabled"])
    return any(key in case_config for key in _GOVERNANCE_KEYS)


def _inside_case(case_root: Path, relative_path: str) -> Path | None:
    if not relative_path:
        return None
    candidate = (case_root / relative_path).resolve()
    try:
        candidate.relative_to(case_root.resolve())
    except ValueError:
        return None
    return candidate


def _value(sources: tuple[dict[str, Any], ...], names: tuple[str, ...]) -> Any:
    for source in sources:
        for name in names:
            if name in source:
                return source[name]
    return None


def validate_numerical_certification(case_root: str | Path, case_config: dict[str, Any]) -> dict[str, Any]:
    certification = _section(case_config, "numerical_certification")
    if not isinstance(certification, dict):
        return {"status": "FAIL", "reason": "NUMERICAL_CERTIFICATION_MISSING"}
    status = str(certification.get("status") or "").strip().upper()
    evidence = str(certification.get("evidence_path") or "").strip()
    evidence_file = _inside_case(Path(case_root), evidence)
    if status != "PASS":
        return {"status": "FAIL", "reason": "NUMERICAL_CERTIFICATION_NOT_PASS", "declared_status": status or None}
    if evidence_file is None or not evidence_file.is_file():
        return {
            "status": "FAIL",
            "reason": "NUMERICAL_CERTIFICATION_EVIDENCE_MISSING",
            "evidence_path": evidence or None,
        }
    return {"status": "PASS", "reason": "NUMERICAL_CERTIFICATION_VALID", "evidence_path": evidence}


def evaluate_proxy_cost(
    case_config: dict[str, Any],
    *,
    proxy_runtime_seconds: float | None,
    full_estimate_seconds: float | None,
    batch_continuation: bool = True,
) -> dict[str, Any]:
    """Check one completed proxy before allowing another batch to continue."""

    budget = _section(case_config, "compute_budget")
    if not isinstance(budget, dict):
        return {"status": "PASS", "reason": "PROXY_COST_GUARD_NOT_CONFIGURED", "legacy_compatible": True}
    if proxy_runtime_seconds is None or full_estimate_seconds is None:
        return {"status": "PASS", "reason": "PROXY_COST_EVIDENCE_NOT_YET_AVAILABLE"}
    try:
        proxy_runtime = float(proxy_runtime_seconds)
        full_estimate = float(full_estimate_seconds)
        fraction = float(budget.get("proxy_max_fraction_of_full", 0.10))
    except (TypeError, ValueError):
        return {"status": "FAIL", "reason": "COMPUTE_BUDGET_VALUE_INVALID"}
    if not all(math.isfinite(value) for value in (proxy_runtime, full_estimate, fraction)) or full_estimate <= 0 or fraction < 0:
        return {"status": "FAIL", "reason": "COMPUTE_BUDGET_VALUE_INVALID"}
    ratio = proxy_runtime / full_estimate
    too_expensive = ratio > fraction
    return {
        "status": "BLOCKED" if too_expensive and batch_continuation else "PASS",
        "reason": "PROXY_TOO_EXPENSIVE" if too_expensive else "PROXY_COST_WITHIN_LIMIT",
        "PROXY_TOO_EXPENSIVE": "YES" if too_expensive else "NO",
        "proxy_runtime_seconds": proxy_runtime,
        "full_estimate_seconds": full_estimate,
        "proxy_runtime_fraction": ratio,
        "proxy_max_fraction_of_full": fraction,
        "batch_continuation": batch_continuation,
    }


def evaluate_route_activation(route: dict[str, Any], *, explicit_override: bool = False) -> dict[str, Any]:
    """Protect a case-local rejected route from silent reactivation."""

    route_id_value = route.get("route_id")
    route_id = route_id_value.strip() if isinstance(route_id_value, str) else ""
    status = str(route.get("status") or "").strip().upper()
    if not route_id:
        return {"status": "FAIL", "reason": "ROUTE_ID_MISSING"}
    if status not in _ROUTE_STATUSES:
        return {"status": "FAIL", "reason": "ROUTE_STATUS_INVALID", "route_id": route_id}
    if status == "REJECTED_WITH_EVIDENCE" and not route.get("evidence"):
        return {"status": "FAIL", "reason": "REJECTED_ROUTE_EVIDENCE_MISSING", "route_id": route_id}
    if explicit_override:
        return {
            "status": "BLOCKED",
            "reason": "ROUTE_OVERRIDE_UNSUPPORTED",
            "route_id": route_id,
        }
    if status == "REJECTED_WITH_EVIDENCE":
        return {"status": "BLOCKED", "reason": "REJECTED_ROUTE_REACTIVATION_BLOCKED", "route_id": route_id}
    return {
        "status": "PASS",
        "reason": "ROUTE_ACTIVATION_ALLOWED",
        "route_id": route_id,
        "explicit_override": explicit_override,
    }


def evaluate_model_execution_governance(
    case_root: str | Path,
    case_config: dict[str, Any],
    model_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate opt-in guards immediately before formal model execution."""

    model_config = model_config or {}
    if not governance_enabled(case_config):
        return {
            "status": "PASS",
            "reason": "COMPETITION_GOVERNANCE_NOT_ENABLED",
            "legacy_compatible": True,
        }

    certification = validate_numerical_certification(case_root, case_config)
    certification_pass = certification.get("status") == "PASS"
    result: dict[str, Any] = {
        "schema_version": GOVERNANCE_SCHEMA_VERSION,
        "status": "PASS",
        "reason": "COMPETITION_GOVERNANCE_VALID",
        "formal_optimization_allowed": certification_pass,
        "canonical_result_trusted": certification_pass,
        "route_override_supported": False,
    }
    result["numerical_certification"] = certification
    if not certification_pass:
        result.update(
            {
                "status": "BLOCKED",
                "reason": "NUMERICAL_CERTIFICATION_REQUIRED",
                "formal_optimization_allowed": False,
                "canonical_result_trusted": False,
            }
        )

    hard_constraint = _section(case_config, "hard_feasibility_constraint")
    hard_present = _section_present(case_config, "hard_feasibility_constraint")
    hard_state, hard_enabled = _declaration_state(hard_constraint) if hard_present else ("ABSENT", False)
    if hard_state == "INVALID":
        result.update({"status": "BLOCKED", "reason": "HARD_FEASIBILITY_DECLARATION_INVALID"})
    elif hard_enabled:
        order = str(_section(case_config, "optimization_order") or "").strip().upper()
        feasibility_status = str(_value((model_config, case_config), ("FEASIBILITY_STATUS",)) or "").strip().upper()
        baseline_found = str(_value((model_config, case_config), ("FEASIBLE_BASELINE_FOUND",)) or "").strip().upper()
        objective_status = str(_value((model_config, case_config), ("OBJECTIVE_OPTIMIZATION_STATUS",)) or "").strip().upper()
        result["feasibility"] = {
            "hard_constraint_declared": True,
            "optimization_order": order or None,
            "FEASIBILITY_STATUS": feasibility_status or None,
            "FEASIBLE_BASELINE_FOUND": baseline_found or None,
            "OBJECTIVE_OPTIMIZATION_STATUS": objective_status or None,
        }
        if not order:
            result.update({"status": "BLOCKED", "reason": "FEASIBILITY_ORDER_REQUIRED"})
        elif order != "FEASIBILITY_THEN_OBJECTIVE":
            result.update({"status": "BLOCKED", "reason": "FEASIBILITY_ORDER_INVALID"})
        objective_started = bool(objective_status and objective_status not in _OBJECTIVE_NOT_STARTED)
        if objective_started and (feasibility_status not in {"FEASIBLE", "PASS", "CONFIRMED"} or baseline_found != "YES"):
            result.update({"status": "BLOCKED", "reason": "FEASIBLE_BASELINE_REQUIRED_BEFORE_OBJECTIVE"})

    budget = _section(case_config, "compute_budget")
    if isinstance(budget, dict):
        result["compute_budget_declared"] = True

    nested_governance = case_config.get("competition_governance")
    route_governance_declared = isinstance(nested_governance, dict) and "model_routes" in nested_governance
    if "competition_governance_enabled" in case_config and "model_routes" in case_config:
        route_governance_declared = True
    if route_governance_declared:
        route_records = _section(case_config, "model_routes")
        if not isinstance(route_records, list):
            result.update({"status": "BLOCKED", "reason": "MODEL_ROUTES_DECLARATION_INVALID"})
        else:
            normalized_records: dict[str, dict[str, Any]] = {}
            ledger_invalid = False
            for item in route_records:
                if not isinstance(item, dict) or not isinstance(item.get("route_id"), str) or not item["route_id"].strip():
                    ledger_invalid = True
                    break
                normalized_id = item["route_id"].strip().casefold()
                if normalized_id in normalized_records:
                    ledger_invalid = True
                    break
                normalized_records[normalized_id] = item
            if ledger_invalid:
                result.update({"status": "BLOCKED", "reason": "MODEL_ROUTES_DECLARATION_INVALID"})
            else:
                requested_value = _value((model_config, case_config), ("route_id",))
                requested_route = requested_value.strip().casefold() if isinstance(requested_value, str) else ""
                if normalized_records and not requested_route:
                    result.update({"status": "BLOCKED", "reason": "ROUTE_ID_REQUIRED"})
                elif requested_route and requested_route not in normalized_records:
                    result.update({"status": "BLOCKED", "reason": "ROUTE_NOT_IN_LEDGER"})
                elif requested_route:
                    route_result = evaluate_route_activation(
                        normalized_records[requested_route], explicit_override=False
                    )
                    result["route_activation"] = route_result
                    if route_result.get("status") != "PASS":
                        result.update({"status": "BLOCKED", "reason": route_result.get("reason")})

    return result
