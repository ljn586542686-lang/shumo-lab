"""Shared validators for X5-F1 minimal math core models."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


VALID_SEVERITIES = {"fatal", "warning", "info"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def classify_risk(severity: str | None = None, *, fatal: bool | None = None) -> str:
    if fatal is True:
        return "fatal"
    normalized = (severity or "info").strip().lower()
    if normalized in {"error", "fatal"}:
        return "fatal"
    if normalized in {"warn", "warning"}:
        return "warning"
    if normalized in VALID_SEVERITIES:
        return normalized
    return "info"


def build_risk_item(
    *,
    severity: str = "info",
    category: str,
    message: str,
    fatal: bool | None = None,
    path: str | None = None,
    field: str | None = None,
    recommendation: str | None = None,
) -> dict[str, Any]:
    normalized = classify_risk(severity, fatal=fatal)
    return {
        "severity": normalized,
        "fatal": normalized == "fatal",
        "category": category,
        "message": message,
        "path": path,
        "field": field,
        "recommendation": recommendation,
        "created_at": utc_now(),
    }


def _is_bad_number(value: Any) -> bool:
    return isinstance(value, float) and (math.isnan(value) or math.isinf(value))


def check_nan_inf(value: Any, path: str = "$") -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if _is_bad_number(value):
        findings.append({"path": path, "issue": "NaN_or_Inf"})
    elif isinstance(value, dict):
        for key, child in value.items():
            findings.extend(check_nan_inf(child, f"{path}.{key}"))
    elif isinstance(value, (list, tuple)):
        for idx, child in enumerate(value):
            findings.extend(check_nan_inf(child, f"{path}[{idx}]"))
    return findings


def _empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def check_required_fields(payload: dict[str, Any], required_fields: list[str] | tuple[str, ...]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for field in required_fields:
        if field not in payload:
            findings.append({"path": f"$.{field}", "issue": "required_field_missing"})
        elif _empty(payload.get(field)):
            findings.append({"path": f"$.{field}", "issue": "required_field_empty"})
    return findings


def check_non_empty_result(payload: Any, path: str = "$") -> list[dict[str, Any]]:
    if _empty(payload):
        return [{"path": path, "issue": "empty_result"}]
    return []


def check_numeric_array(
    value: Any,
    *,
    field: str,
    allow_empty: bool = False,
) -> tuple[list[float], list[dict[str, Any]]]:
    risks: list[dict[str, Any]] = []
    if value is None:
        return [], [
            build_risk_item(
                severity="fatal",
                category="input_validation",
                message=f"{field} is missing",
                field=field,
            )
        ]
    if not isinstance(value, (list, tuple)):
        return [], [
            build_risk_item(
                severity="fatal",
                category="input_validation",
                message=f"{field} must be a numeric array",
                field=field,
            )
        ]
    if not value and not allow_empty:
        return [], [
            build_risk_item(
                severity="fatal",
                category="input_validation",
                message=f"{field} must not be empty",
                field=field,
            )
        ]
    numbers: list[float] = []
    for idx, item in enumerate(value):
        try:
            number = float(item)
        except (TypeError, ValueError):
            risks.append(
                build_risk_item(
                    severity="fatal",
                    category="input_validation",
                    message=f"{field}[{idx}] is not numeric",
                    field=f"{field}[{idx}]",
                )
            )
            continue
        if math.isnan(number) or math.isinf(number):
            risks.append(
                build_risk_item(
                    severity="fatal",
                    category="input_validation",
                    message=f"{field}[{idx}] is NaN/Inf",
                    field=f"{field}[{idx}]",
                )
            )
            continue
        numbers.append(number)
    return numbers, risks


def sanitize_for_json(value: Any, path: str = "$") -> tuple[Any, list[dict[str, Any]]]:
    risks: list[dict[str, Any]] = []
    if _is_bad_number(value):
        return None, [
            build_risk_item(
                severity="fatal",
                category="json_safety",
                message=f"NaN/Inf removed from JSON output at {path}",
                field=path,
            )
        ]
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, child in value.items():
            safe_child, child_risks = sanitize_for_json(child, f"{path}.{key}")
            cleaned[str(key)] = safe_child
            risks.extend(child_risks)
        return cleaned, risks
    if isinstance(value, (list, tuple)):
        cleaned_list: list[Any] = []
        for idx, child in enumerate(value):
            safe_child, child_risks = sanitize_for_json(child, f"{path}[{idx}]")
            cleaned_list.append(safe_child)
            risks.extend(child_risks)
        return cleaned_list, risks
    if isinstance(value, (str, int, bool)) or value is None:
        return value, risks
    if isinstance(value, float):
        return value, risks
    return str(value), risks
