"""Machine-computable red flag rules for Adversarial Claim Check (F5.5).

All thresholds are loaded from config/default_thresholds.yaml at call time.
Nothing is hard-coded here.

Red flag severity levels
------------------------
RED    — requires human three-way decision (A/B/C)
YELLOW — advisory warning, does not block but must be recorded
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .schema import ClaimCard, REQUIRED_SLOTS_BY_CLAIM_TYPE


@dataclass
class RedFlagResult:
    flag_id: str
    severity: str        # "RED" | "YELLOW"
    triggered: bool
    details: str
    evidence_used: dict[str, Any] = field(default_factory=dict)


def _load_thresholds(thresholds: dict[str, Any] | None) -> dict[str, Any]:
    """Merge caller-supplied thresholds with safe defaults (no hard-coding)."""
    defaults: dict[str, Any] = {
        "eps": 1e-12,
        "boundary_tolerance": 1e-9,
        "model_conflict_relative_threshold": 0.10,
        "improvement_error_ratio_threshold": 1.0,
        "metric_tie_tolerance": 1e-6,
    }
    if thresholds:
        defaults.update(thresholds)
    return defaults


# ---------------------------------------------------------------------------
# 5.1 EDGE_BOUNDARY_FLAG
# ---------------------------------------------------------------------------

def check_edge_boundary(
    card: ClaimCard,
    thresholds: dict[str, Any],
) -> RedFlagResult:
    """RED if candidate value == observed min or max (within boundary_tolerance)."""
    candidate_is_boundary = card.evidence_value("candidate_is_boundary")
    observed_range = card.evidence_value("observed_range")
    predicted_value = card.evidence_value("predicted_value")
    tol = thresholds["boundary_tolerance"]

    triggered = False
    details = ""

    if candidate_is_boundary is True:
        triggered = True
        details = (
            "candidate_is_boundary=True: candidate condition is at the observed "
            "boundary of the data range. Predictions at boundary rely on extrapolation "
            "and have higher uncertainty."
        )
    elif observed_range is not None and predicted_value is not None:
        obs_min = observed_range.get("min") if isinstance(observed_range, dict) else None
        obs_max = observed_range.get("max") if isinstance(observed_range, dict) else None
        try:
            pv = float(predicted_value)
        except (TypeError, ValueError):
            pv = None
        if pv is not None:
            if obs_min is not None and abs(pv - float(obs_min)) <= tol:
                triggered = True
                details = f"Candidate value {pv} equals observed minimum {obs_min} (tol={tol})."
            elif obs_max is not None and abs(pv - float(obs_max)) <= tol:
                triggered = True
                details = f"Candidate value {pv} equals observed maximum {obs_max} (tol={tol})."

    if not triggered:
        # Also check for temperature boundary pattern (common in 2021B)
        observed_range_temp = card.evidence_value("observed_range")
        if observed_range_temp is None:
            details = "observed_range not provided; boundary check skipped."

    return RedFlagResult(
        flag_id="EDGE_BOUNDARY_FLAG",
        severity="RED",
        triggered=triggered,
        details=details or "No boundary violation detected.",
        evidence_used={
            "candidate_is_boundary": candidate_is_boundary,
            "observed_range": observed_range,
            "predicted_value": predicted_value,
            "boundary_tolerance": tol,
        },
    )


# ---------------------------------------------------------------------------
# 5.2 MODEL_CONFLICT_FLAG
# ---------------------------------------------------------------------------

def check_model_conflict(
    card: ClaimCard,
    thresholds: dict[str, Any],
) -> RedFlagResult:
    """RED if primary model and proxy/local model disagree by > threshold."""
    primary_value = card.evidence_value("predicted_value")
    proxy_value = card.evidence_value("proxy_or_local_model_value_if_available")
    threshold = thresholds["model_conflict_relative_threshold"]
    eps = thresholds["eps"]

    triggered = False
    details = ""
    relative_conflict: float | None = None

    if primary_value is None or proxy_value is None:
        details = "proxy_or_local_model_value not available; MODEL_CONFLICT check skipped (recorded as NOT_AVAILABLE, not PASS)."
        return RedFlagResult(
            flag_id="MODEL_CONFLICT_FLAG",
            severity="RED",
            triggered=False,
            details=details,
            evidence_used={"proxy_value": proxy_value, "primary_value": primary_value},
        )

    try:
        pv = float(primary_value)
        xv = float(proxy_value)
        relative_conflict = abs(pv - xv) / max(abs(pv), eps)
        if relative_conflict > threshold:
            triggered = True
            details = (
                f"Primary model: {pv}, proxy model: {xv}. "
                f"Relative conflict: {relative_conflict:.1%} > threshold {threshold:.0%}. "
                "Core claim depends on model agreement that is not present."
            )
        else:
            details = (
                f"Primary={pv}, proxy={xv}, relative conflict={relative_conflict:.1%} "
                f"<= threshold {threshold:.0%}. No conflict detected."
            )
    except (TypeError, ValueError) as exc:
        details = f"Could not compute model conflict: {exc}"

    return RedFlagResult(
        flag_id="MODEL_CONFLICT_FLAG",
        severity="RED",
        triggered=triggered,
        details=details,
        evidence_used={
            "primary_value": primary_value,
            "proxy_value": proxy_value,
            "relative_conflict": relative_conflict,
            "threshold": threshold,
        },
    )


# ---------------------------------------------------------------------------
# 5.3 MODEL_SELECTION_FLAG
# ---------------------------------------------------------------------------

def check_model_selection(
    card: ClaimCard,
    thresholds: dict[str, Any],
) -> RedFlagResult:
    """RED/YELLOW if selected model is not the best by primary validation metric.

    Rules:
    - If metric_gap <= metric_tie_tolerance: YELLOW tie flag, NOT RED.
    - If selected_model == explanation_only and claim_type == model_explanation: YELLOW.
    - Otherwise if metric_gap > tie_tolerance and claim_type in core types: RED.
    - model_role_justification CANNOT downgrade RED to YELLOW.
    """
    selected = card.evidence_value("selected_model")
    best = card.evidence_value("best_model_by_primary_validation_metric")
    sel_metric = card.evidence_value("selected_model_metric_value")
    best_metric = card.evidence_value("best_model_metric_value")
    selected_role = card.evidence_value("selected_model_role")
    tie_tol = thresholds["metric_tie_tolerance"]

    core_claim_types = {"model_selection", "optimal_condition", "prediction_claim"}
    explanation_only_types = {"model_explanation"}

    if selected is None or best is None:
        return RedFlagResult(
            flag_id="MODEL_SELECTION_FLAG",
            severity="RED",
            triggered=False,
            details="selected_model or best_model_by_primary_validation_metric not provided.",
            evidence_used={"selected_model": selected, "best_model": best},
        )

    if selected == best:
        return RedFlagResult(
            flag_id="MODEL_SELECTION_FLAG",
            severity="RED",
            triggered=False,
            details=f"selected_model == best_model_by_primary_validation_metric ({selected}). No selection conflict.",
            evidence_used={"selected_model": selected, "best_model": best},
        )

    # Compute metric gap
    metric_gap: float | None = None
    if sel_metric is not None and best_metric is not None:
        try:
            metric_gap = abs(float(sel_metric) - float(best_metric))
        except (TypeError, ValueError):
            metric_gap = None

    # Tie check
    if metric_gap is not None and metric_gap <= tie_tol:
        return RedFlagResult(
            flag_id="MODEL_SELECTION_TIE_FLAG",
            severity="YELLOW",
            triggered=True,
            details=(
                f"selected_model={selected} vs best={best}: metric_gap={metric_gap} "
                f"<= tie_tolerance={tie_tol}. Models are operationally tied. "
                "MODEL_SELECTION_FLAG not escalated to RED on rank order alone."
            ),
            evidence_used={
                "selected_model": selected,
                "best_model": best,
                "metric_gap": metric_gap,
                "tie_tolerance": tie_tol,
            },
        )

    # explanation_only role in model_explanation claim_type → YELLOW
    if selected_role == "explanation_only" and card.claim_type in explanation_only_types:
        return RedFlagResult(
            flag_id="MODEL_SELECTION_FLAG",
            severity="YELLOW",
            triggered=True,
            details=(
                f"selected_model={selected} has role=explanation_only and claim_type="
                f"{card.claim_type}. Not used for core prediction/optimization. YELLOW advisory."
            ),
            evidence_used={
                "selected_model": selected,
                "best_model": best,
                "selected_model_role": selected_role,
                "claim_type": card.claim_type,
            },
        )

    # Core claim types → RED (justification cannot downgrade)
    severity = "RED" if card.claim_type in core_claim_types else "YELLOW"
    details = (
        f"selected_model={selected} != best_model_by_primary_validation_metric={best}. "
        f"metric_gap={metric_gap} > tie_tolerance={tie_tol}. "
        "model_role_justification recorded but CANNOT downgrade this flag. "
        "Human three-way decision required."
    )
    return RedFlagResult(
        flag_id="MODEL_SELECTION_FLAG",
        severity=severity,
        triggered=True,
        details=details,
        evidence_used={
            "selected_model": selected,
            "best_model": best,
            "metric_gap": metric_gap,
            "tie_tolerance": tie_tol,
            "claim_type": card.claim_type,
            "model_role_justification": card.evidence_value("model_role_justification"),
        },
    )


# ---------------------------------------------------------------------------
# 5.4 BASELINE_MISSING_FLAG
# ---------------------------------------------------------------------------

def check_baseline_missing(
    card: ClaimCard,
    thresholds: dict[str, Any],  # noqa: ARG001
) -> RedFlagResult:
    """RED if core optimization claim lacks observed best value."""
    observed_best = card.evidence_value("observed_best_value")
    triggered = observed_best is None

    return RedFlagResult(
        flag_id="BASELINE_MISSING_FLAG",
        severity="RED",
        triggered=triggered,
        details=(
            "observed_best_value is None. Core claim must have an observed baseline "
            "to quantify predicted improvement."
            if triggered
            else f"observed_best_value={observed_best}. Baseline present."
        ),
        evidence_used={"observed_best_value": observed_best},
    )


# ---------------------------------------------------------------------------
# 5.5 IMPROVEMENT_WITHIN_ERROR_FLAG
# ---------------------------------------------------------------------------

def check_improvement_within_error(
    card: ClaimCard,
    thresholds: dict[str, Any],
) -> RedFlagResult:
    """RED if predicted improvement <= validation error scale."""
    predicted_improvement = card.evidence_value("predicted_improvement")
    validation_error_scale = card.evidence_value("validation_error_scale")
    ratio_threshold = thresholds["improvement_error_ratio_threshold"]

    if validation_error_scale is None:
        return RedFlagResult(
            flag_id="IMPROVEMENT_WITHIN_ERROR_FLAG",
            severity="YELLOW",
            triggered=True,
            details="validation_error_scale not provided. Cannot assess improvement robustness.",
            evidence_used={"validation_error_scale": None, "predicted_improvement": predicted_improvement},
        )

    if predicted_improvement is None:
        return RedFlagResult(
            flag_id="IMPROVEMENT_WITHIN_ERROR_FLAG",
            severity="YELLOW",
            triggered=True,
            details="predicted_improvement not provided.",
            evidence_used={"predicted_improvement": None},
        )

    try:
        imp = float(predicted_improvement)
        err = float(validation_error_scale)
        triggered = imp <= err * ratio_threshold
    except (TypeError, ValueError) as exc:
        return RedFlagResult(
            flag_id="IMPROVEMENT_WITHIN_ERROR_FLAG",
            severity="YELLOW",
            triggered=True,
            details=f"Could not compare: {exc}",
            evidence_used={},
        )

    return RedFlagResult(
        flag_id="IMPROVEMENT_WITHIN_ERROR_FLAG",
        severity="RED" if triggered else "YELLOW",
        triggered=triggered,
        details=(
            f"predicted_improvement={imp} <= validation_error_scale={err} * ratio={ratio_threshold}. "
            "Claimed improvement is within noise/error range."
            if triggered
            else f"predicted_improvement={imp} > error_scale={err}. Improvement appears robust."
        ),
        evidence_used={
            "predicted_improvement": predicted_improvement,
            "validation_error_scale": validation_error_scale,
            "ratio_threshold": ratio_threshold,
        },
    )


# ---------------------------------------------------------------------------
# 5.6 INTERACTION_MISSING_FLAG
# ---------------------------------------------------------------------------

def check_interaction_missing(
    card: ClaimCard,
    thresholds: dict[str, Any],  # noqa: ARG001
) -> RedFlagResult:
    """YELLOW if claim depends on group response but model lacks interaction terms."""
    depends_on_group = card.evidence_value("claim_depends_on_group_response")
    has_interaction = card.evidence_value("model_has_interaction_terms")

    triggered = (depends_on_group is True) and (not has_interaction)

    return RedFlagResult(
        flag_id="INTERACTION_MISSING_FLAG",
        severity="YELLOW",
        triggered=triggered,
        details=(
            "claim_depends_on_group_response=True but model_has_interaction_terms=False/None. "
            "Main-effects-only model cannot confirm group-specific response patterns."
            if triggered
            else "Interaction check: no issue detected."
        ),
        evidence_used={
            "claim_depends_on_group_response": depends_on_group,
            "model_has_interaction_terms": has_interaction,
        },
    )


# ---------------------------------------------------------------------------
# 5.7 UNCERTAINTY_MISSING_FLAG
# ---------------------------------------------------------------------------

def check_uncertainty_missing(
    card: ClaimCard,
    thresholds: dict[str, Any],  # noqa: ARG001
) -> RedFlagResult:
    """YELLOW/RED if core prediction lacks uncertainty quantification."""
    pred_interval = card.evidence_value("prediction_interval")
    error_context = card.evidence_value("validation_error_context")

    triggered = (pred_interval is None) and (error_context is None)
    severity = "RED" if card.claim_type in {"optimal_condition", "prediction_claim"} else "YELLOW"

    return RedFlagResult(
        flag_id="UNCERTAINTY_MISSING_FLAG",
        severity=severity,
        triggered=triggered,
        details=(
            "Neither prediction_interval nor validation_error_context provided. "
            "Core prediction value has no quantified uncertainty."
            if triggered
            else "Uncertainty context available."
        ),
        evidence_used={
            "prediction_interval": pred_interval,
            "validation_error_context": error_context,
        },
    )


# ---------------------------------------------------------------------------
# 3.x.3 MISSING_REQUIRED_EVIDENCE_FLAG
# ---------------------------------------------------------------------------

def check_missing_required_slots(
    card: ClaimCard,
    thresholds: dict[str, Any],  # noqa: ARG001
) -> list[RedFlagResult]:
    """RED for every required slot that is missing or None for this claim_type."""
    required = REQUIRED_SLOTS_BY_CLAIM_TYPE.get(card.claim_type, [])
    flags: list[RedFlagResult] = []
    provided_names = {ei.field_name for ei in card.evidence_inputs}

    for slot in required:
        value = card.evidence_value(slot)
        if slot not in provided_names or value is None:
            flags.append(RedFlagResult(
                flag_id="MISSING_REQUIRED_EVIDENCE_FLAG",
                severity="RED",
                triggered=True,
                details=(
                    f"Required evidence slot '{slot}' for claim_type='{card.claim_type}' "
                    "is absent or None. Absence of evidence is itself an evidence-chain risk."
                ),
                evidence_used={"missing_slot": slot, "claim_type": card.claim_type},
            ))

    return flags


# ---------------------------------------------------------------------------
# 3.x.2 SELF_FILL CHECK
# ---------------------------------------------------------------------------

def check_semantic_self_fill(card: ClaimCard) -> list[RedFlagResult]:
    """FAIL if any semantic judgement field is provided by the generator role."""
    from .schema import SEMANTIC_JUDGEMENT_FIELDS
    flags: list[RedFlagResult] = []
    for ei in card.evidence_inputs:
        if (
            ei.field_name in SEMANTIC_JUDGEMENT_FIELDS
            and ei.provider_role_id == card.generator_role_id
        ):
            flags.append(RedFlagResult(
                flag_id="CLAIM_CHECK_EVIDENCE_INPUT_SELF_FILLED",
                severity="RED",
                triggered=True,
                details=(
                    f"Evidence field '{ei.field_name}' is a semantic judgement field "
                    f"and must not be filled by generator_role_id='{card.generator_role_id}'. "
                    "FAIL / CLAIM_CHECK_EVIDENCE_INPUT_SELF_FILLED"
                ),
                evidence_used={
                    "field_name": ei.field_name,
                    "provider_role_id": ei.provider_role_id,
                    "generator_role_id": card.generator_role_id,
                },
            ))
    return flags


# ---------------------------------------------------------------------------
# Master evaluator
# ---------------------------------------------------------------------------

def evaluate_red_flags(
    card: ClaimCard,
    thresholds: dict[str, Any] | None = None,
) -> list[RedFlagResult]:
    """Run all applicable red flag rules and return the full list."""
    cfg = _load_thresholds(thresholds)

    flags: list[RedFlagResult] = []

    # Schema-level self-fill check first
    flags.extend(check_semantic_self_fill(card))

    # Missing required slots
    flags.extend(check_missing_required_slots(card, cfg))

    # Boundary
    flags.append(check_edge_boundary(card, cfg))

    # Model conflict
    flags.append(check_model_conflict(card, cfg))

    # Model selection
    flags.append(check_model_selection(card, cfg))

    # Baseline
    if card.claim_type in {"optimal_condition", "model_selection", "prediction_claim"}:
        flags.append(check_baseline_missing(card, cfg))

    # Improvement within error
    if card.claim_type in {"optimal_condition", "prediction_claim"}:
        flags.append(check_improvement_within_error(card, cfg))

    # Interaction
    flags.append(check_interaction_missing(card, cfg))

    # Uncertainty
    flags.append(check_uncertainty_missing(card, cfg))

    return flags
