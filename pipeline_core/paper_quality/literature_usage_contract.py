"""Record literature as an input to modelling, not a bibliography afterthought."""

from __future__ import annotations

from typing import Any


class LiteratureUsageContractError(ValueError):
    pass


_SOURCE_TYPES = {"official_problem", "standard", "method", "parameter", "validation"}
_REVIEW_STATUSES = {"PENDING_HUMAN_REVIEW", "APPROVED", "REJECTED"}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LiteratureUsageContractError(f"LITERATURE_USAGE_REQUIRED_TEXT: {field}")
    return value.strip()


def _failure(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def validate_literature_usage_contract(spec: dict[str, Any], *, cited_keys: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise LiteratureUsageContractError("LITERATURE_USAGE_CONTRACT_INVALID")
    cited_keys = cited_keys or set()
    required = bool(spec.get("external_evidence_required", False))
    plan_required = bool(spec.get("formal_literature_plan_required", True))
    self_contained = bool(spec.get("self_contained_problem", False))
    records = spec.get("records", [])
    if not isinstance(records, list):
        raise LiteratureUsageContractError("LITERATURE_USAGE_RECORDS_INVALID")
    failures: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    seen: set[str] = set()
    approved_count = 0
    approved_chinese_count = 0
    for index, record in enumerate(records, 1):
        if not isinstance(record, dict):
            raise LiteratureUsageContractError(f"LITERATURE_USAGE_RECORD_INVALID: {index}")
        record_id = _text(record.get("record_id"), f"records[{index}].record_id")
        if record_id in seen:
            failures.append(_failure("LITERATURE_USAGE_RECORD_DUPLICATED", record_id))
        seen.add(record_id)
        for field in ("citation_key", "knowledge_gap", "decision_influenced", "paper_location"):
            try:
                _text(record.get(field), f"records[{index}].{field}")
            except LiteratureUsageContractError:
                failures.append(_failure("LITERATURE_USAGE_FIELD_MISSING", f"{record_id}.{field}"))
        for field in ("citation_reason", "competition_suitability"):
            try:
                _text(record.get(field), f"records[{index}].{field}")
            except LiteratureUsageContractError:
                failures.append(_failure("LITERATURE_REVIEW_EXPLANATION_MISSING", f"{record_id}.{field}"))
        language = record.get("language", "zh")
        if language not in {"zh", "en"}:
            failures.append(_failure("LITERATURE_LANGUAGE_INVALID", record_id))
        if language == "en" and not isinstance(record.get("language_exception_reason"), str):
            failures.append(_failure("NON_CHINESE_LITERATURE_EXCEPTION_REQUIRED", record_id))
        review_status = record.get("human_review_status", "PENDING_HUMAN_REVIEW")
        if review_status not in _REVIEW_STATUSES:
            failures.append(_failure("LITERATURE_REVIEW_STATUS_INVALID", record_id))
        if review_status != "APPROVED":
            failures.append(_failure("LITERATURE_HUMAN_REVIEW_REQUIRED", record_id))
        else:
            approved_count += 1
            if language == "zh":
                approved_chinese_count += 1
        source_type = record.get("source_type")
        if source_type not in _SOURCE_TYPES:
            failures.append(_failure("LITERATURE_USAGE_SOURCE_TYPE_INVALID", record_id))
        citation_key = str(record.get("citation_key") or "")
        if citation_key and citation_key not in cited_keys:
            failures.append(_failure("LITERATURE_USAGE_CITATION_NOT_IN_PAPER", f"{record_id}.{citation_key}"))
    if (required or plan_required) and not records:
        failures.append(_failure("LITERATURE_USAGE_REQUIRED", "external evidence declared but no source record supplied"))
    if required and self_contained:
        failures.append(_failure("LITERATURE_USAGE_POLICY_CONTRADICTION", "cannot require external evidence and declare self-contained"))
    minimum_approved = spec.get("minimum_approved_records", 1)
    minimum_chinese = spec.get("minimum_chinese_records", 1)
    recommended_approved = spec.get("recommended_approved_records", 3)
    if any(not isinstance(value, int) or value < 0 for value in (minimum_approved, minimum_chinese, recommended_approved)):
        raise LiteratureUsageContractError("LITERATURE_USAGE_MINIMUM_INVALID")
    if approved_count < minimum_approved:
        failures.append(_failure("LITERATURE_APPROVED_COUNT_TOO_LOW", f"expected at least {minimum_approved}, found {approved_count}"))
    if approved_chinese_count < minimum_chinese:
        failures.append(_failure("CHINESE_LITERATURE_COUNT_TOO_LOW", f"expected at least {minimum_chinese}, found {approved_chinese_count}"))
    if approved_count < recommended_approved:
        warnings.append(_failure("LITERATURE_SUPPORT_THIN", f"recommended at least {recommended_approved}, found {approved_count}"))
    return {
        "status": "FAIL" if failures else ("WARNING" if warnings else "PASS"),
        "hard_failures": failures,
        "warnings": warnings,
        "record_ids": sorted(seen),
        "external_evidence_required": required,
        "self_contained_problem": self_contained,
        "formal_literature_plan_required": plan_required,
        "approved_count": approved_count,
        "approved_chinese_count": approved_chinese_count,
        "contract_version": str(spec.get("version") or "literature_usage_contract_v1"),
    }


def build_literature_review_packet(spec: dict[str, Any]) -> str:
    """Create a user-facing candidate packet before any citation is approved."""

    records = spec.get("records", []) if isinstance(spec, dict) else []
    if not isinstance(records, list):
        raise LiteratureUsageContractError("LITERATURE_USAGE_RECORDS_INVALID")
    lines = ["# 文献引用候选审查", "", "以下文献尚未自动进入论文；请逐项决定是否接受。", ""]
    for index, record in enumerate(records, 1):
        if not isinstance(record, dict):
            raise LiteratureUsageContractError(f"LITERATURE_USAGE_RECORD_INVALID: {index}")
        record_id = _text(record.get("record_id"), f"records[{index}].record_id")
        lines.extend([
            f"## {record_id}",
            f"- 引用键：{_text(record.get('citation_key'), f'records[{index}].citation_key')}",
            f"- 文献语言：{record.get('language', 'zh')}",
            f"- 要解决的知识缺口：{_text(record.get('knowledge_gap'), f'records[{index}].knowledge_gap')}",
            f"- 拟支持的建模/验证决定：{_text(record.get('decision_influenced'), f'records[{index}].decision_influenced')}",
            f"- 引用原因：{_text(record.get('citation_reason'), f'records[{index}].citation_reason')}",
            f"- 对本科数模论文的适合性：{_text(record.get('competition_suitability'), f'records[{index}].competition_suitability')}",
            f"- 当前状态：{record.get('human_review_status', 'PENDING_HUMAN_REVIEW')}",
            "- 用户决定：[ ] 接受  [ ] 拒绝  [ ] 需要替换",
            "",
        ])
    return "\n".join(lines) + "\n"
