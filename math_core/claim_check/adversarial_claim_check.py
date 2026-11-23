"""Adversarial Prize Claim Check — main evaluator (X5-F5.5).

F5.5 DOES:
  - Surface evidence-chain gaps for each core claim before it enters the paper.
  - Expose model conflict, boundary risk, baseline absence, error scale issues.
  - Require human three-way decision (A/B/C) when any RED flag is raised.
  - Auto-pass when zero red flags fire (prevents bureaucratic overhead).

F5.5 DOES NOT:
  - Auto-select the main model.
  - Prove the paper will win a prize.
  - Close the math_core upper-bound open item.
  - Replace domain judgment or formal reference verification.
  - Let model_role_justification wash RED flags to YELLOW.

Anti-degradation invariant:
  "adversarial_critique must be produced by an independent adversarial role/prompt,
   not by the paper generator prompt."
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from .red_flag_rules import RedFlagResult, evaluate_red_flags
from .schema import ClaimCard, ALLOWED_HUMAN_DECISIONS


def _has_red(flags: list[RedFlagResult]) -> bool:
    return any(f.triggered and f.severity == "RED" for f in flags)


def _recommended_decision(flags: list[RedFlagResult]) -> str:
    if not _has_red(flags):
        return "A_ACCEPT_AS_CORE_CLAIM"
    # MODEL_SELECTION_FLAG RED → B by default
    ms_red = any(
        f.triggered and f.severity == "RED" and f.flag_id == "MODEL_SELECTION_FLAG"
        for f in flags
    )
    baseline_red = any(
        f.triggered and f.severity == "RED" and f.flag_id == "BASELINE_MISSING_FLAG"
        for f in flags
    )
    if baseline_red:
        return "C_REJECT_AND_REWORK"
    if ms_red:
        return "B_DOWNGRADE_CLAIM"
    return "B_DOWNGRADE_CLAIM"


def _allowed_strength(flags: list[RedFlagResult], card: ClaimCard) -> str:
    red_ids = {f.flag_id for f in flags if f.triggered and f.severity == "RED"}
    if "BASELINE_MISSING_FLAG" in red_ids:
        return "not_allowed"
    if "CLAIM_CHECK_EVIDENCE_INPUT_SELF_FILLED" in red_ids:
        return "not_allowed"
    if _has_red(flags):
        return "model_specific_candidate"
    return "priority_candidate"


def evaluate_claim(
    card: ClaimCard,
    thresholds: dict[str, Any] | None = None,
) -> ClaimCard:
    """Evaluate a ClaimCard: run schema validation, red flags, assign decisions.

    Returns the mutated card with machine_red_flags, recommended_decision,
    allowed_claim_strength, and status populated.
    Does NOT set human_decision (remains PENDING until human acts).
    """
    # Schema validation
    schema_errors = card.validate_schema()
    if schema_errors:
        card.status = "SCHEMA_ERROR"
        card.machine_red_flags = [
            {"flag_id": "SCHEMA_ERROR", "severity": "RED", "triggered": True,
             "details": err}
            for err in schema_errors
        ]
        card.recommended_decision = "C_REJECT_AND_REWORK"
        card.allowed_claim_strength = "not_allowed"
        return card

    # Red flag evaluation
    flags = evaluate_red_flags(card, thresholds)
    card.machine_red_flags = [dataclasses.asdict(f) for f in flags]

    # Decisions
    card.recommended_decision = _recommended_decision(flags)
    card.allowed_claim_strength = _allowed_strength(flags, card)

    # Status
    if _has_red(flags):
        card.status = "RED_FLAG_REQUIRES_HUMAN_DECISION"
    elif any(f.triggered and f.severity == "YELLOW" for f in flags):
        card.status = "YELLOW_ADVISORY_AUTO_PASS"
    else:
        card.status = "AUTO_PASS"

    # human_decision stays PENDING — must be set by a human
    if card.status == "AUTO_PASS" and card.human_decision == "PENDING":
        card.human_decision = "PENDING"  # stays PENDING; auto-pass means no gate needed

    return card


def card_to_dict(card: ClaimCard) -> dict[str, Any]:
    return dataclasses.asdict(card)


def write_claim_card(card: ClaimCard, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(card_to_dict(card), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
