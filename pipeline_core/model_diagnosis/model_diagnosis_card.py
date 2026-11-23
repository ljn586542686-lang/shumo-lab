"""Model Diagnosis Card (X5-F5.5D).

Downstream of the H0 cross-implementation gate.  Answers:
  "Why is this model weak / contradictory?  What should the human check next?"

The card is a structured diagnostic artefact.  It does NOT select a model,
does NOT recommend a condition, and does NOT issue a PASS verdict on the
model it was asked to diagnose.

Schema (all fields described below):
  diagnosis_id                 — unique identifier for this card
  diagnosis_type               — one of ALLOWED_DIAGNOSIS_TYPES
  case_id                      — case the card applies to
  upstream_gate                — gate result that triggered this card
  trigger_claim_id             — claim ID that triggered the gate check
  trigger_source               — which agent/module produced the triggering claim
  machine_trigger_evidence     — structured evidence that triggered this card
  h0_root_cause_line           — one-line summary of H0 root cause (carried forward)
  diagnostic_hypotheses        — list of {id, hypothesis, priority, evidence_needed}
  adversarial_questions        — list of questions the human reviewer should answer
  required_evidence_to_check   — list of {item, why_needed, evidence_tier}
  human_judgement_points       — list of {point, options, implication}
  known_system_blindspots      — list of {blindspot_id, description, applies_to_this_case}
  allowed_output_strength      — always "diagnostic_directions_only"
  forbidden_fields             — list of field names that must NOT appear in output

FAIL token for forbidden field violations:
  FAIL / MODEL_DIAGNOSIS_OVERSTEPPED_INTO_MODEL_SELECTION
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pipeline_core.model_diagnosis.cross_impl_consistency_gate import (
    FORBIDDEN_OUTPUT_FIELDS,
    CrossImplGateResult,
    assert_no_forbidden_fields,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALLOWED_DIAGNOSIS_TYPES: frozenset[str] = frozenset({
    "cross_impl_disagreement",
    "single_impl_low_r2",
    "extrapolation_risk",
    "interaction_term_missing",
    "target_spec_ambiguity",
    "boundary_uncertainty",
    "multicollinearity_suspected",
    "high_leverage_condition",
})

ALLOWED_OUTPUT_STRENGTH = "diagnostic_directions_only"

FAIL_OVERSTEPPED = "FAIL / MODEL_DIAGNOSIS_OVERSTEPPED_INTO_MODEL_SELECTION"
FAIL_SELF_VERDICT = "FAIL / SELF_VERDICT_NOT_ALLOWED"
FAIL_DECISION_RECOMMENDATION = "FAIL / HUMAN_DECISION_RECOMMENDATION_LEAKED_INTO_GATE"

# Priority levels for hypotheses
HYPOTHESIS_PRIORITIES: frozenset[str] = frozenset({"HIGH", "MEDIUM", "LOW"})

# Risk flag identifiers (red-team patch #3)
RISK_FLAG_CROSS_CATALYST_RANGE_BORROWED = "CROSS_CATALYST_RANGE_BORROWED"
RISK_FLAG_DOUBLE_EXTRAPOLATED_PRODUCT = "DOUBLE_EXTRAPOLATED_PRODUCT_RISK"

# Artifact status values (red-team patch #1 — anti self-verdict)
ARTIFACT_STATUS_GENERATED_FOR_REVIEW = "GENERATED_FOR_REVIEW"
ARTIFACT_STATUS_INDEPENDENTLY_REVIEWED = "INDEPENDENTLY_REVIEWED"

# Test scope metadata (red-team patch #2)
TEST_SCOPE = {
    "tests_verify": [
        "schema fields exist and are correctly typed",
        "forbidden output fields are absent",
        "risk flags are serialized",
        "cross_impl gate fields are written",
        "allowed_output_strength == 'diagnostic_directions_only'",
        "producer_role_id != verdict_issuer_role_id (anti self-verdict)",
    ],
    "tests_do_not_verify": [
        "synthetic boundary prediction reliability",
        "whether T=450 extrapolation is scientifically acceptable",
        "whether conv*sel/100 is preferable to direct c4_yield for this problem",
        "whether human A/B/C decision is correct",
        "physical plausibility of predicted component responses outside the observed range",
        "whether A3's 450C observation justifies using 450C for A1",
    ],
}


# ---------------------------------------------------------------------------
# Sub-structures (plain dicts for easy JSON serialisation)
# ---------------------------------------------------------------------------

def make_diagnostic_hypothesis(
    hyp_id: str,
    hypothesis: str,
    priority: str,
    evidence_needed: list[str],
) -> dict[str, Any]:
    if priority not in HYPOTHESIS_PRIORITIES:
        raise ValueError(
            f"Invalid priority '{priority}'. Must be one of {sorted(HYPOTHESIS_PRIORITIES)}"
        )
    return {
        "id": hyp_id,
        "hypothesis": hypothesis,
        "priority": priority,
        "evidence_needed": evidence_needed,
    }


def make_required_evidence(
    item: str,
    why_needed: str,
    evidence_tier: str,
) -> dict[str, Any]:
    return {
        "item": item,
        "why_needed": why_needed,
        "evidence_tier": evidence_tier,
    }


def make_human_judgement_point(
    point: str,
    options: list[str],
    implication: str,
) -> dict[str, Any]:
    return {
        "point": point,
        "options": options,
        "implication": implication,
    }


def make_cross_catalyst_range_borrowed_flag(
    candidate_catalyst: str,
    candidate_temperature: float,
    candidate_max_observed_temperature: float,
    grid_upper_bound: float,
    grid_upper_bound_source_catalyst: str,
    justified_by_data: str = "unknown",
) -> dict[str, Any]:
    """Build a CROSS_CATALYST_RANGE_BORROWED risk flag (red-team patch #3).

    justified_by_data must be "true", "false", or "unknown" (default).
    Must NOT be automatically set to "true" without human confirmation.
    """
    if justified_by_data not in ("true", "false", "unknown"):
        raise ValueError(
            f"justified_by_data must be 'true', 'false', or 'unknown', got '{justified_by_data}'"
        )
    triggered = (
        candidate_max_observed_temperature < candidate_temperature
        and grid_upper_bound >= candidate_temperature
    )
    return {
        "flag_id": RISK_FLAG_CROSS_CATALYST_RANGE_BORROWED,
        "triggered": triggered,
        "candidate_catalyst": candidate_catalyst,
        "candidate_temperature": candidate_temperature,
        "candidate_max_observed_temperature": candidate_max_observed_temperature,
        "temperature_gap_beyond_training": candidate_temperature - candidate_max_observed_temperature,
        "grid_upper_bound": grid_upper_bound,
        "grid_upper_bound_source_catalyst": grid_upper_bound_source_catalyst,
        "borrowed_range_source_catalyst": grid_upper_bound_source_catalyst,
        "borrowed_range_source_temperature": grid_upper_bound,
        "borrowed_range_justified_by_data": justified_by_data,
        "interpretation": (
            f"{candidate_catalyst} max observed temperature is {candidate_max_observed_temperature}C. "
            f"The optimization grid extends to {grid_upper_bound}C based on {grid_upper_bound_source_catalyst}'s observations. "
            f"{candidate_catalyst}'s valid extrapolation bound cannot automatically inherit "
            f"{grid_upper_bound_source_catalyst}'s observed upper bound without human confirmation."
        ),
    }


def make_double_extrapolated_product_risk_flag(
    temperature_axis: dict[str, Any],
    product_chain_components: list[str],
    product_chain_reason: str,
) -> dict[str, Any]:
    """Build a structured DOUBLE_EXTRAPOLATED_PRODUCT_RISK flag (red-team patch #6).

    temperature_axis: {name, observed_max, candidate_temperature, gap}
    product_chain_components: list of subprediction names evaluated at OOS point
    product_chain_reason: why both subpredictions are OOS
    """
    return {
        "flag_id": RISK_FLAG_DOUBLE_EXTRAPOLATED_PRODUCT,
        "status": True,
        "extrapolation_axis_1": {
            "name": temperature_axis.get("name", "temperature"),
            "observed_max_for_candidate": temperature_axis.get("observed_max"),
            "candidate_temperature": temperature_axis.get("candidate_temperature"),
            "gap": temperature_axis.get("gap"),
            "unit": temperature_axis.get("unit", "C"),
        },
        "extrapolation_axis_2": {
            "name": "product_chain_subprediction",
            "components": product_chain_components,
            "reason": product_chain_reason,
            "note": (
                "The second item is not an independent physical variable axis. "
                "Both subpredictions in the product chain are evaluated at a "
                "temperature point with no training observation for the candidate catalyst. "
                "Extrapolation errors from each subprediction combine nonlinearly in the product."
            ),
        },
    }


def assert_no_self_verdict(
    producer_role_id: str,
    verdict_issuer_role_id: str | None,
) -> None:
    """Raise ValueError if producer is issuing their own verdict (red-team patch #1)."""
    if verdict_issuer_role_id is not None and producer_role_id == verdict_issuer_role_id:
        raise ValueError(
            f"{FAIL_SELF_VERDICT}: producer_role_id '{producer_role_id}' "
            f"equals verdict_issuer_role_id '{verdict_issuer_role_id}'. "
            "The agent that produces a card must not issue its own PASS verdict."
        )


def assert_no_decision_recommendation(d: dict[str, Any]) -> None:
    """Raise ValueError if d contains any human-decision recommendation fields (red-team patch #4)."""
    from pipeline_core.model_diagnosis.cross_impl_consistency_gate import FORBIDDEN_OUTPUT_FIELDS
    decision_rec_fields = {
        "recommended_decision", "recommended_decision_tendency",
        "suggested_choice", "preferred_choice",
    }
    found = decision_rec_fields & d.keys()
    if found:
        raise ValueError(
            f"{FAIL_DECISION_RECOMMENDATION}: "
            f"Forbidden human-decision recommendation fields present: {sorted(found)}"
        )


def make_blindspot(
    blindspot_id: str,
    description: str,
    applies_to_this_case: bool,
) -> dict[str, Any]:
    return {
        "blindspot_id": blindspot_id,
        "description": description,
        "applies_to_this_case": applies_to_this_case,
    }


# ---------------------------------------------------------------------------
# Diagnosis card dataclass
# ---------------------------------------------------------------------------

@dataclass
class ModelDiagnosisCard:
    diagnosis_id: str
    diagnosis_type: str
    case_id: str
    upstream_gate: str
    trigger_claim_id: str
    trigger_source: str
    machine_trigger_evidence: dict[str, Any]
    h0_root_cause_line: str
    diagnostic_hypotheses: list[dict[str, Any]] = field(default_factory=list)
    adversarial_questions: list[str] = field(default_factory=list)
    required_evidence_to_check: list[dict[str, Any]] = field(default_factory=list)
    human_judgement_points: list[dict[str, Any]] = field(default_factory=list)
    known_system_blindspots: list[dict[str, Any]] = field(default_factory=list)
    allowed_output_strength: str = ALLOWED_OUTPUT_STRENGTH
    forbidden_fields: list[str] = field(
        default_factory=lambda: sorted(FORBIDDEN_OUTPUT_FIELDS)
    )

    def __post_init__(self) -> None:
        if self.diagnosis_type not in ALLOWED_DIAGNOSIS_TYPES:
            raise ValueError(
                f"Unknown diagnosis_type '{self.diagnosis_type}'. "
                f"Must be one of: {sorted(ALLOWED_DIAGNOSIS_TYPES)}"
            )
        if self.allowed_output_strength != ALLOWED_OUTPUT_STRENGTH:
            raise ValueError(
                f"allowed_output_strength must be '{ALLOWED_OUTPUT_STRENGTH}', "
                f"got '{self.allowed_output_strength}'."
            )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "diagnosis_id": self.diagnosis_id,
            "diagnosis_type": self.diagnosis_type,
            "case_id": self.case_id,
            "upstream_gate": self.upstream_gate,
            "trigger_claim_id": self.trigger_claim_id,
            "trigger_source": self.trigger_source,
            "machine_trigger_evidence": self.machine_trigger_evidence,
            "h0_root_cause_line": self.h0_root_cause_line,
            "diagnostic_hypotheses": self.diagnostic_hypotheses,
            "adversarial_questions": self.adversarial_questions,
            "required_evidence_to_check": self.required_evidence_to_check,
            "human_judgement_points": self.human_judgement_points,
            "known_system_blindspots": self.known_system_blindspots,
            "allowed_output_strength": self.allowed_output_strength,
            "forbidden_fields": self.forbidden_fields,
        }
        # Safety enforcement: no forbidden selection fields allowed
        assert_no_forbidden_fields(d)
        return d


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_diagnosis_card(
    diagnosis_id: str,
    diagnosis_type: str,
    case_id: str,
    upstream_gate_result: CrossImplGateResult | str,
    trigger_claim_id: str,
    trigger_source: str,
    machine_trigger_evidence: dict[str, Any],
    h0_root_cause_line: str,
    diagnostic_hypotheses: list[dict[str, Any]] | None = None,
    adversarial_questions: list[str] | None = None,
    required_evidence_to_check: list[dict[str, Any]] | None = None,
    human_judgement_points: list[dict[str, Any]] | None = None,
    known_system_blindspots: list[dict[str, Any]] | None = None,
) -> ModelDiagnosisCard:
    """Construct and validate a ModelDiagnosisCard.

    Parameters
    ----------
    upstream_gate_result:
        Either a CrossImplGateResult or its gate_state string.
    """
    if isinstance(upstream_gate_result, CrossImplGateResult):
        gate_str = upstream_gate_result.gate_state
    else:
        gate_str = str(upstream_gate_result)

    card = ModelDiagnosisCard(
        diagnosis_id=diagnosis_id,
        diagnosis_type=diagnosis_type,
        case_id=case_id,
        upstream_gate=gate_str,
        trigger_claim_id=trigger_claim_id,
        trigger_source=trigger_source,
        machine_trigger_evidence=machine_trigger_evidence,
        h0_root_cause_line=h0_root_cause_line,
        diagnostic_hypotheses=diagnostic_hypotheses or [],
        adversarial_questions=adversarial_questions or [],
        required_evidence_to_check=required_evidence_to_check or [],
        human_judgement_points=human_judgement_points or [],
        known_system_blindspots=known_system_blindspots or [],
    )
    # Validate no forbidden fields in the output dict
    card.to_dict()
    return card


# ---------------------------------------------------------------------------
# Output-field safety check (callable from test suite)
# ---------------------------------------------------------------------------

def check_card_dict_safe(d: dict[str, Any]) -> str:
    """Verify a card dict has no forbidden fields and correct output strength.

    Returns 'SAFE' or raises ValueError.
    """
    assert_no_forbidden_fields(d)
    strength = d.get("allowed_output_strength", "")
    if strength != ALLOWED_OUTPUT_STRENGTH:
        raise ValueError(
            f"allowed_output_strength must be '{ALLOWED_OUTPUT_STRENGTH}', got '{strength}'."
        )
    return "SAFE"
