"""Cross-Implementation Consistency Gate (X5-F5.5D).

Answers the H0 question BEFORE model diagnosis proceeds:
"Do the two implementations represent the same model specification?"

If they do not, model diagnosis must carry the H0 root cause forward
rather than treating the two numbers as equivalent predictions.

Gate states (returned as gate_state string):

  CROSS_IMPL_CONSISTENT
      Both implementations agree within tolerance on value AND use the same
      target spec.  Downstream diagnosis may treat predictions as equivalent.

  CROSS_IMPL_TARGET_SPEC_DIFFERENCE
      Confirmed: implementations use different target variable specifications
      (e.g., direct c4_yield vs conv*sel/100).  The discrepancy is not a bug
      but an architecture choice.  Downstream must disclose both architectures.

  CROSS_IMPL_PREPROCESSING_DIFFERENCE
      Implementations differ only in preprocessing (e.g., feature scaling).
      OLS-invariant differences are classified here; may or may not affect
      predictions depending on model class.

  CROSS_IMPL_MODEL_SPEC_DIFFERENCE
      Implementations differ in model structure (e.g., polynomial degree,
      interaction terms, regularization) but share the same target variable.

  CROSS_IMPL_BUG_SUSPECTED
      A discrepancy exists and root cause is not yet classified.
      One or both values may be wrong.

  CROSS_IMPL_UNRESOLVED
      Discrepancy acknowledged but reproduction or root-cause analysis not
      yet complete.

  CROSS_IMPL_BLOCKED_MISSING_ARTIFACTS
      Cannot determine consistency because one or more required artifacts
      (source file, result file, reproduction script) are missing.

Anti-self-selection rule:
  This gate determines CONSISTENCY, not which architecture is "correct".
  It must not emit recommended_model, recommended_condition, final_model,
  final_condition, auto_selected_model, or auto_selected_condition.
  Presence of any of those fields is a FAIL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Gate state constants
# ---------------------------------------------------------------------------

CROSS_IMPL_CONSISTENT = "CROSS_IMPL_CONSISTENT"
CROSS_IMPL_TARGET_SPEC_DIFFERENCE = "CROSS_IMPL_TARGET_SPEC_DIFFERENCE"
CROSS_IMPL_PREPROCESSING_DIFFERENCE = "CROSS_IMPL_PREPROCESSING_DIFFERENCE"
CROSS_IMPL_MODEL_SPEC_DIFFERENCE = "CROSS_IMPL_MODEL_SPEC_DIFFERENCE"
CROSS_IMPL_BUG_SUSPECTED = "CROSS_IMPL_BUG_SUSPECTED"
CROSS_IMPL_UNRESOLVED = "CROSS_IMPL_UNRESOLVED"
CROSS_IMPL_BLOCKED_MISSING_ARTIFACTS = "CROSS_IMPL_BLOCKED_MISSING_ARTIFACTS"

_ALL_GATE_STATES: frozenset[str] = frozenset({
    CROSS_IMPL_CONSISTENT,
    CROSS_IMPL_TARGET_SPEC_DIFFERENCE,
    CROSS_IMPL_PREPROCESSING_DIFFERENCE,
    CROSS_IMPL_MODEL_SPEC_DIFFERENCE,
    CROSS_IMPL_BUG_SUSPECTED,
    CROSS_IMPL_UNRESOLVED,
    CROSS_IMPL_BLOCKED_MISSING_ARTIFACTS,
})

# Gate verdict tokens
GATE_PASS = "PASS / CROSS_IMPL_GATE_CLEARED"
GATE_BLOCK = "BLOCK / CROSS_IMPL_GATE_FAILED"
GATE_FAIL = "FAIL / CROSS_IMPL_GATE_SCHEMA_ERROR"

# Forbidden output fields — any gate result dict containing these is invalid
# Includes anti-self-verdict fields and human-decision-recommendation fields.
FORBIDDEN_OUTPUT_FIELDS: frozenset[str] = frozenset({
    # Model-selection anti-selection (original)
    "recommended_model",
    "recommended_condition",
    "final_model",
    "final_condition",
    "auto_selected_model",
    "auto_selected_condition",
    # Human-decision anti-recommendation (red-team patch #4)
    "recommended_decision",
    "recommended_decision_tendency",
    "suggested_choice",
    "preferred_choice",
    # Self-verdict anti-certification (red-team patch #1)
    "verdict_issued_by_producer",
    "producer_self_approved",
})

FAIL_SELF_VERDICT = "FAIL / SELF_VERDICT_NOT_ALLOWED"
FAIL_DECISION_RECOMMENDATION = "FAIL / HUMAN_DECISION_RECOMMENDATION_LEAKED_INTO_GATE"

# Gate scope: what this gate covers and does NOT cover (red-team patch #5)
GATE_COVERAGE_SCOPE = {
    "covers": [
        "implementation value mismatch",
        "target_spec difference",
        "model_spec difference",
        "preprocessing difference",
        "unresolved cross-implementation discrepancy",
    ],
    "does_not_prove": [
        "prediction reliability",
        "extrapolation validity",
        "local proxy consistency",
        "candidate optimality",
        "whether one architecture is scientifically preferable",
    ],
}

# Gate states that BLOCK downstream claim writing
BLOCKING_GATE_STATES: frozenset[str] = frozenset({
    CROSS_IMPL_BUG_SUSPECTED,
    CROSS_IMPL_UNRESOLVED,
    CROSS_IMPL_BLOCKED_MISSING_ARTIFACTS,
})

# Gate states that PASS but require disclosure
DISCLOSURE_REQUIRED_STATES: frozenset[str] = frozenset({
    CROSS_IMPL_TARGET_SPEC_DIFFERENCE,
    CROSS_IMPL_PREPROCESSING_DIFFERENCE,
    CROSS_IMPL_MODEL_SPEC_DIFFERENCE,
})


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class CrossImplGateResult:
    """Result of the cross-implementation consistency gate."""

    gate_state: str
    verdict: str
    case_id: str = ""
    condition_label: str = ""

    # Numeric evidence
    impl_a_value: float | None = None
    impl_b_value: float | None = None
    discrepancy_abs: float | None = None
    tolerance_used: float | None = None

    # Root cause classification
    h0_root_cause: str = ""
    root_cause_confirmed: bool = False
    reproduction_error_pp: float | None = None
    evidence_tier: str = ""

    # Downstream guidance
    disclosure_required: bool = False
    claim_strength_ceiling: str = "implementation_specific_diagnostic_only"
    b3_generation_allowed: bool = False
    citation_ready: bool = False
    formal_reference_allowed: bool = False

    # Reason text
    reason: str = ""
    blocking_reasons: list[str] = field(default_factory=list)

    @property
    def is_pass(self) -> bool:
        return self.verdict == GATE_PASS

    @property
    def is_block(self) -> bool:
        return self.verdict == GATE_BLOCK

    @property
    def is_fail(self) -> bool:
        return self.verdict == GATE_FAIL

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "gate_state": self.gate_state,
            "verdict": self.verdict,
            "case_id": self.case_id,
            "condition_label": self.condition_label,
            "impl_a_value": self.impl_a_value,
            "impl_b_value": self.impl_b_value,
            "discrepancy_abs": self.discrepancy_abs,
            "tolerance_used": self.tolerance_used,
            "h0_root_cause": self.h0_root_cause,
            "root_cause_confirmed": self.root_cause_confirmed,
            "reproduction_error_pp": self.reproduction_error_pp,
            "evidence_tier": self.evidence_tier,
            "disclosure_required": self.disclosure_required,
            "claim_strength_ceiling": self.claim_strength_ceiling,
            "b3_generation_allowed": self.b3_generation_allowed,
            "citation_ready": self.citation_ready,
            "formal_reference_allowed": self.formal_reference_allowed,
            "reason": self.reason,
            "blocking_reasons": self.blocking_reasons,
        }
        # Safety: verify no forbidden fields were accidentally added
        _check_no_forbidden_fields(d)
        return d


# ---------------------------------------------------------------------------
# Forbidden-field enforcement
# ---------------------------------------------------------------------------

def _check_no_forbidden_fields(d: dict[str, Any]) -> None:
    """Raise ValueError if d contains any forbidden selection-output fields."""
    found = FORBIDDEN_OUTPUT_FIELDS & d.keys()
    if found:
        raise ValueError(
            f"FAIL / MODEL_DIAGNOSIS_OVERSTEPPED_INTO_MODEL_SELECTION: "
            f"Forbidden output fields present: {sorted(found)}"
        )


def assert_no_forbidden_fields(result_dict: dict[str, Any]) -> None:
    """Public validator — call on any dict before returning to caller."""
    _check_no_forbidden_fields(result_dict)


# ---------------------------------------------------------------------------
# Core gate logic
# ---------------------------------------------------------------------------

def run_cross_impl_gate(
    case_id: str,
    condition_label: str,
    impl_a_value: float | None,
    impl_b_value: float | None,
    *,
    tolerance: float = 0.01,
    h0_status: str = "",
    h0_root_cause_category: str = "",
    root_cause_confirmed: bool = False,
    reproduction_error_pp: float | None = None,
    evidence_tier: str = "",
    missing_artifacts: list[str] | None = None,
) -> CrossImplGateResult:
    """Run the cross-implementation consistency gate.

    Parameters
    ----------
    case_id:
        Identifier for the case (e.g., "synthetic_series").
    condition_label:
        Human-readable condition being evaluated (e.g., "synthetic boundary").
    impl_a_value:
        Numeric prediction from implementation A (Python). None if unavailable.
    impl_b_value:
        Numeric prediction from implementation B (MATLAB). None if unavailable.
    tolerance:
        Absolute tolerance for "consistent" judgment (default 0.01 pp).
    h0_status:
        H0 audit status string (e.g., "H0_CLOSED_PYTHON_CONFIRMED").
    h0_root_cause_category:
        Category of root cause, one of:
          "target_spec_difference", "preprocessing_difference",
          "model_spec_difference", "bug_suspected", "unknown".
    root_cause_confirmed:
        True only when an independent reproduction confirmed the root cause.
    reproduction_error_pp:
        Absolute error of reproduction vs original (pp). 0.00 = perfect match.
    evidence_tier:
        Tier label for the root cause evidence.
    missing_artifacts:
        List of artifact names that could not be located.

    Returns
    -------
    CrossImplGateResult
    """
    # --- Step 0: missing artifacts block immediately ---
    if missing_artifacts:
        return CrossImplGateResult(
            gate_state=CROSS_IMPL_BLOCKED_MISSING_ARTIFACTS,
            verdict=GATE_BLOCK,
            case_id=case_id,
            condition_label=condition_label,
            impl_a_value=impl_a_value,
            impl_b_value=impl_b_value,
            reason="Cannot assess consistency: required artifacts missing.",
            blocking_reasons=[f"Missing: {a}" for a in missing_artifacts],
            b3_generation_allowed=False,
            citation_ready=False,
            formal_reference_allowed=False,
        )

    # --- Step 1: both values required ---
    if impl_a_value is None or impl_b_value is None:
        return CrossImplGateResult(
            gate_state=CROSS_IMPL_BLOCKED_MISSING_ARTIFACTS,
            verdict=GATE_BLOCK,
            case_id=case_id,
            condition_label=condition_label,
            impl_a_value=impl_a_value,
            impl_b_value=impl_b_value,
            reason="One or both implementation values are None.",
            blocking_reasons=["impl_a_value or impl_b_value is None"],
            b3_generation_allowed=False,
            citation_ready=False,
            formal_reference_allowed=False,
        )

    discrepancy = abs(impl_a_value - impl_b_value)

    # --- Step 2: consistent within tolerance ---
    if discrepancy <= tolerance:
        return CrossImplGateResult(
            gate_state=CROSS_IMPL_CONSISTENT,
            verdict=GATE_PASS,
            case_id=case_id,
            condition_label=condition_label,
            impl_a_value=impl_a_value,
            impl_b_value=impl_b_value,
            discrepancy_abs=discrepancy,
            tolerance_used=tolerance,
            h0_root_cause="none — values consistent",
            root_cause_confirmed=True,
            evidence_tier=evidence_tier,
            disclosure_required=False,
            claim_strength_ceiling="model_specific_candidate",
            b3_generation_allowed=False,
            citation_ready=False,
            formal_reference_allowed=False,
            reason=(
                f"Discrepancy {discrepancy:.4f} ≤ tolerance {tolerance}. "
                "Implementations are consistent."
            ),
        )

    # --- Step 3: discrepancy present — classify by root cause ---

    # H0 not yet investigated → UNRESOLVED
    if not h0_status or h0_status in ("", "H0_OPEN", "H0_NOT_RUN"):
        return CrossImplGateResult(
            gate_state=CROSS_IMPL_UNRESOLVED,
            verdict=GATE_BLOCK,
            case_id=case_id,
            condition_label=condition_label,
            impl_a_value=impl_a_value,
            impl_b_value=impl_b_value,
            discrepancy_abs=discrepancy,
            tolerance_used=tolerance,
            reason=(
                f"Discrepancy {discrepancy:.4f} pp found but H0 audit not completed "
                f"(h0_status='{h0_status}'). Run H0 baseline audit first."
            ),
            blocking_reasons=["H0 audit not completed"],
            b3_generation_allowed=False,
            citation_ready=False,
            formal_reference_allowed=False,
        )

    # Root cause known but not confirmed by reproduction
    if h0_root_cause_category == "target_spec_difference":
        gate_state = CROSS_IMPL_TARGET_SPEC_DIFFERENCE
    elif h0_root_cause_category == "preprocessing_difference":
        gate_state = CROSS_IMPL_PREPROCESSING_DIFFERENCE
    elif h0_root_cause_category == "model_spec_difference":
        gate_state = CROSS_IMPL_MODEL_SPEC_DIFFERENCE
    elif h0_root_cause_category == "bug_suspected":
        gate_state = CROSS_IMPL_BUG_SUSPECTED
    else:
        gate_state = CROSS_IMPL_UNRESOLVED

    is_blocking = gate_state in BLOCKING_GATE_STATES
    disclosure = gate_state in DISCLOSURE_REQUIRED_STATES

    # Claim strength ceiling: only non-blocking, confirmed states can go above diagnostic
    if root_cause_confirmed and not is_blocking:
        strength_ceiling = "implementation_specific_diagnostic_only"
    else:
        strength_ceiling = "implementation_specific_diagnostic_only"

    verdict = GATE_BLOCK if is_blocking else GATE_PASS
    reason_parts = [
        f"Discrepancy {discrepancy:.4f} pp (A={impl_a_value:.4f}, B={impl_b_value:.4f}).",
        f"H0 status: {h0_status}.",
        f"Root cause: {h0_root_cause_category}.",
    ]
    if root_cause_confirmed and reproduction_error_pp is not None:
        reason_parts.append(
            f"Reproduction confirmed (error={reproduction_error_pp:.4f} pp)."
        )
    if disclosure:
        reason_parts.append(
            "Architecture disclosure required before citing either value."
        )

    blocking_reasons: list[str] = []
    if is_blocking:
        blocking_reasons.append(
            f"gate_state={gate_state}: root cause not yet confirmed as non-bug."
        )

    return CrossImplGateResult(
        gate_state=gate_state,
        verdict=verdict,
        case_id=case_id,
        condition_label=condition_label,
        impl_a_value=impl_a_value,
        impl_b_value=impl_b_value,
        discrepancy_abs=discrepancy,
        tolerance_used=tolerance,
        h0_root_cause=h0_root_cause_category,
        root_cause_confirmed=root_cause_confirmed,
        reproduction_error_pp=reproduction_error_pp,
        evidence_tier=evidence_tier,
        disclosure_required=disclosure,
        claim_strength_ceiling=strength_ceiling,
        b3_generation_allowed=False,
        citation_ready=False,
        formal_reference_allowed=False,
        reason=" ".join(reason_parts),
        blocking_reasons=blocking_reasons,
    )


# ---------------------------------------------------------------------------
# Convenience: validate a pre-built gate result dict
# ---------------------------------------------------------------------------

def validate_gate_result_dict(d: dict[str, Any]) -> str:
    """Validate a gate result dictionary.

    Returns GATE_PASS token if valid, raises ValueError on forbidden fields
    or unknown gate_state.
    """
    assert_no_forbidden_fields(d)

    gate_state = d.get("gate_state", "")
    if gate_state not in _ALL_GATE_STATES:
        raise ValueError(
            f"Unknown gate_state '{gate_state}'. "
            f"Must be one of: {sorted(_ALL_GATE_STATES)}"
        )
    return GATE_PASS
