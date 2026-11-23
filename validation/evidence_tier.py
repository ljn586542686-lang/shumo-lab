"""Evidence tier labels for X5-F4.1 anti-self-certification audits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


E0_SYNTHETIC_FIXTURE = "E0_SYNTHETIC_FIXTURE"
E1_REAL_CASE_WITH_MANUAL_OVERLAY = "E1_REAL_CASE_WITH_MANUAL_OVERLAY"
E2_REAL_CASE_NO_OVERLAY = "E2_REAL_CASE_NO_OVERLAY"
E3_REAL_CASE_AUTO_INIT_OVERLAY = "E3_REAL_CASE_AUTO_INIT_OVERLAY"
E4_ADVERSARIAL_NEAR_MISS = "E4_ADVERSARIAL_NEAR_MISS"

VALID_EVIDENCE_TIERS = {
    E0_SYNTHETIC_FIXTURE,
    E1_REAL_CASE_WITH_MANUAL_OVERLAY,
    E2_REAL_CASE_NO_OVERLAY,
    E3_REAL_CASE_AUTO_INIT_OVERLAY,
    E4_ADVERSARIAL_NEAR_MISS,
}


@dataclass(frozen=True)
class EvidenceTierRecord:
    evidence_tier: str
    label: str
    description: str
    limitations: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_tier": self.evidence_tier,
            "label": self.label,
            "description": self.description,
            "limitations": self.limitations,
        }


TIER_RECORDS = {
    E0_SYNTHETIC_FIXTURE: EvidenceTierRecord(
        E0_SYNTHETIC_FIXTURE,
        "synthetic fixture",
        "Purpose-built fixture, useful for regression and edge behavior.",
        ["Do not present as real case evidence."],
    ),
    E1_REAL_CASE_WITH_MANUAL_OVERLAY: EvidenceTierRecord(
        E1_REAL_CASE_WITH_MANUAL_OVERLAY,
        "real case with manual overlay",
        "Historical or real-like case copied to work_tmp with manually added quality_gates metadata.",
        ["Overlay may bias coverage; report overlay fields explicitly."],
    ),
    E2_REAL_CASE_NO_OVERLAY: EvidenceTierRecord(
        E2_REAL_CASE_NO_OVERLAY,
        "real case no overlay",
        "Historical or real-like case copied to work_tmp and scanned without quality_gates overlay.",
        ["Many rules may be skipped because metadata is absent."],
    ),
    E3_REAL_CASE_AUTO_INIT_OVERLAY: EvidenceTierRecord(
        E3_REAL_CASE_AUTO_INIT_OVERLAY,
        "real case auto-init overlay",
        "Historical or real-like case copied to work_tmp with machine-initialized minimal overlay from local artifacts.",
        ["Auto-init fields are only inferred metadata, not author-confirmed facts."],
    ),
    E4_ADVERSARIAL_NEAR_MISS: EvidenceTierRecord(
        E4_ADVERSARIAL_NEAR_MISS,
        "adversarial near miss",
        "Fixture designed to sit near thresholds and expose false-positive or false-negative behavior.",
        ["Does not represent frequency in real contests."],
    ),
}


def tier_record(evidence_tier: str) -> dict[str, Any]:
    if evidence_tier not in VALID_EVIDENCE_TIERS:
        raise ValueError(f"unsupported evidence_tier: {evidence_tier}")
    return TIER_RECORDS[evidence_tier].as_dict()


def attach_evidence_tier(payload: dict[str, Any], evidence_tier: str, *, notes: str = "") -> dict[str, Any]:
    record = tier_record(evidence_tier)
    result = dict(payload)
    result["evidence_tier"] = evidence_tier
    result["evidence_tier_record"] = record
    if notes:
        result["evidence_tier_notes"] = notes
    return result


def classify_qg_result(result: dict[str, Any], evidence_tier: str) -> dict[str, Any]:
    item = attach_evidence_tier(result, evidence_tier)
    if item.get("status") == "SKIPPED":
        item["error_category"] = "UNREVIEWED_SKIPPED"
    elif item.get("rule_id") in {"QG-FIG-08", "QG-REF-09", "QG-ABST-10"} and item.get("manual_review_required"):
        item["error_category"] = "PAPER_SEMANTIC_MANUAL_REVIEW"
    elif item.get("rule_id") == "QG-DATA-01":
        item["error_category"] = "DATA_CONTRACT_ERROR"
    elif item.get("rule_id") == "QG-EXTRAP-02":
        item["error_category"] = "MODEL_RISK_DISCLOSURE_ERROR"
    else:
        item["error_category"] = "QUALITY_GATE_SIGNAL"
    return item
