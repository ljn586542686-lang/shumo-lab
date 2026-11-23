"""Schema constants and dataclass for Adversarial Claim Check (F5.5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Enumerated string sets — validated at runtime, not via enum to stay simple
# ---------------------------------------------------------------------------

ALLOWED_CLAIM_TYPES: frozenset[str] = frozenset({
    "model_selection",
    "optimal_condition",
    "experiment_design",
    "sensitivity_claim",
    "stability_claim",
    "evaluation_claim",
    "prediction_claim",
    "classification_claim",
    "model_explanation",
})

ALLOWED_HUMAN_DECISIONS: frozenset[str] = frozenset({
    "A_ACCEPT_AS_CORE_CLAIM",
    "B_DOWNGRADE_CLAIM",
    "C_REJECT_AND_REWORK",
    "PENDING",
})

ALLOWED_CLAIM_STRENGTHS: frozenset[str] = frozenset({
    "core_conclusion",
    "priority_candidate",
    "model_specific_candidate",
    "trend_evidence_only",
    "diagnostic_only",
    "not_allowed",
})

# source_type values for evidence_inputs entries
ALLOWED_SOURCE_TYPES: frozenset[str] = frozenset({
    "computed_from_raw_data",
    "computed_from_model_result",
    "extracted_from_structured_report",
    "manual_required",
    "adversarial_semantic_judgement",
    "paper_text_extraction",
    "not_available",
})

# Semantic judgement fields that MUST NOT be self-filled by the generator role
SEMANTIC_JUDGEMENT_FIELDS: frozenset[str] = frozenset({
    "claim_depends_on_group_response",
    "claim_relies_on_extrapolation",
    "claim_uses_model_selection_justification",
    "claim_has_mechanism_interpretation",
    "claim_is_core_conclusion",
    "claim_should_require_baseline",
})

# Required evidence slots per claim_type
REQUIRED_SLOTS_BY_CLAIM_TYPE: dict[str, list[str]] = {
    "optimal_condition": [
        "candidate_condition",
        "predicted_value",
        "observed_range",
        "selected_model",
        "validation_metrics_by_model",
        "observed_best_value",
        "observed_best_condition",
        "predicted_improvement",
        "validation_error_scale",
        "candidate_is_boundary",
        "alternative_model_predictions_or_rankings",
        "proxy_or_local_model_value_if_available",
    ],
    "model_selection": [
        "selected_model",
        "best_model_by_primary_validation_metric",
        "primary_validation_metric",
        "selected_model_metric_value",
        "best_model_metric_value",
        "metric_gap",
        "model_role_justification",
    ],
    "prediction_claim": [
        "selected_model",
        "predicted_value",
        "validation_error_scale",
        "validation_metrics_by_model",
    ],
    "sensitivity_claim": [
        "perturbation_parameter",
        "baseline_value",
        "perturbation_range",
        "normalized_delta_range",
    ],
    "stability_claim": [
        "time_range",
        "initial_value",
        "final_value",
        "relative_change",
    ],
}


@dataclass
class EvidenceInput:
    field_name: str
    value: Any = None
    source_type: str = "not_available"
    source_path: str = ""
    provider_role_id: str = ""
    required_for_claim_types: list[str] = field(default_factory=list)
    missing_policy: str = "trigger_red_flag"
    verification_status: str = "unverified"
    notes: str = ""

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.source_type not in ALLOWED_SOURCE_TYPES:
            errors.append(
                f"evidence_input '{self.field_name}': invalid source_type '{self.source_type}'"
            )
        return errors


@dataclass
class ClaimCard:
    claim_id: str
    claim_title: str
    claim_type: str
    case_id: str
    source_section: str
    claim_text: str
    generator_role_id: str
    adversarial_role_id: str
    generator_statement: str = ""
    adversarial_critique: str = ""
    evidence_inputs: list[EvidenceInput] = field(default_factory=list)
    machine_red_flags: list[dict[str, Any]] = field(default_factory=list)
    human_decision: str = "PENDING"
    recommended_decision: str = "PENDING"
    allowed_claim_strength: str = "not_allowed"
    required_revisions: list[str] = field(default_factory=list)
    status: str = "PENDING"
    limitations: list[str] = field(default_factory=list)

    def validate_schema(self) -> list[str]:
        errors: list[str] = []
        if self.claim_type not in ALLOWED_CLAIM_TYPES:
            errors.append(f"Invalid claim_type: '{self.claim_type}'")
        if self.human_decision not in ALLOWED_HUMAN_DECISIONS:
            errors.append(f"Invalid human_decision: '{self.human_decision}'")
        if self.allowed_claim_strength not in ALLOWED_CLAIM_STRENGTHS:
            errors.append(
                f"Invalid allowed_claim_strength: '{self.allowed_claim_strength}'"
            )
        if self.generator_role_id == self.adversarial_role_id:
            errors.append(
                "FAIL / CLAIM_CHECK_EVIDENCE_INPUT_SELF_FILLED: "
                "generator_role_id must differ from adversarial_role_id"
            )
        for ei in self.evidence_inputs:
            errors.extend(ei.validate())
        return errors

    def evidence_by_name(self, name: str) -> EvidenceInput | None:
        for ei in self.evidence_inputs:
            if ei.field_name == name:
                return ei
        return None

    def evidence_value(self, name: str) -> Any:
        ei = self.evidence_by_name(name)
        return ei.value if ei is not None else None
