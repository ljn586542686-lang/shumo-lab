"""F5.5c Claim Check Enforcement Layer (X5-F5.5C).

Enforces that core conclusions may only enter a paper after claim check
settlement is complete.  "Called" is not enough — the result must be
settled (human decision issued, no unresolved RED flags, allowed strength
compatible with proposed wording).

Responsibilities (pipeline_core layer, NOT math_core):
  - Determine whether a claim type requires claim check at all.
  - Guard against self-filled core-claim classification.
  - Block core conclusion writing when settlement is missing or incomplete.
  - Check that proposed wording does not exceed allowed_claim_strength.

Does NOT:
  - Compute red flags (that is math_core.claim_check.red_flag_rules).
  - Run evaluate_claim (that is math_core.claim_check.adversarial_claim_check).
  - Generate or modify any paper, PDF, or case artifact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HIGH_RISK_CLAIM_TYPES: frozenset[str] = frozenset({
    "optimal_condition",
    "model_selection",
    "prediction_claim",
    "experiment_design",
    "sensitivity_claim",
    "stability_claim",
    "evaluation_claim",
    "classification_claim",
})

# Wording tokens that indicate a strong / core-conclusion-level claim
_STRONG_WORDING_TOKENS: tuple[str, ...] = (
    "全局最优", "最终最优", "确定最优", "跨模型一致最优",
    "实验真实结果", "最优条件", "最终确定", "全局最优条件",
)

# Wording tokens forbidden at each strength level (inclusive of stronger levels below)
_FORBIDDEN_BY_STRENGTH: dict[str, tuple[str, ...]] = {
    "priority_candidate": (
        "全局最优", "最终最优", "确定最优", "实验真实结果", "跨模型一致最优",
    ),
    "model_specific_candidate": (
        "全局最优", "最终最优", "确定最优", "实验真实结果", "跨模型一致最优",
    ),
    "trend_evidence_only": (
        "全局最优", "最终最优", "确定最优", "实验真实结果", "跨模型一致最优",
        "证明", "最终结论", "核心依据",
    ),
    "diagnostic_only": (
        "全局最优", "最终最优", "确定最优", "实验真实结果", "跨模型一致最优",
        "证明", "最终结论", "核心依据", "主结论", "直接证明",
    ),
    # not_allowed: any non-empty wording is forbidden
}

# Narrow, deterministic English escalation phrases.  These are deliberately
# curated combinations of proof language and global-optimality language; this
# is not a general semantic or NLP checker.
_ENGLISH_FORBIDDEN_PHRASES: tuple[str, ...] = (
    "this proves the global optimum",
    "this proves global optimality",
    "this proves globally optimal",
    "the solution is globally optimal",
    "the result proves the global optimum",
    "the result proves global optimality",
    "proved the global optimum",
    "proved global optimality",
)


def _normalize_english_wording(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _english_lexical_block_token(proposed_wording: str) -> str | None:
    normalized = _normalize_english_wording(proposed_wording)
    for phrase in _ENGLISH_FORBIDDEN_PHRASES:
        if phrase in normalized:
            return phrase
    # Catch the minimal compositional variants without blocking ordinary
    # mentions of either word family in isolation.
    proof_tokens = ("prove", "proves", "proved")
    optimality_tokens = ("global optimum", "global optimality", "globally optimal")
    if any(token in normalized for token in proof_tokens) and any(
        token in normalized for token in optimality_tokens
    ):
        proof = next(token for token in proof_tokens if token in normalized)
        optimality = next(token for token in optimality_tokens if token in normalized)
        return f"{proof} + {optimality}"
    return None

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

PASS_READY = "PASS / CLAIM_READY_FOR_CORE_WRITING"
BLOCK_MISSING = "BLOCK / CLAIM_CHECK_REQUIRED_BUT_MISSING"
BLOCK_UNRESOLVED = "BLOCK / CLAIM_CHECK_UNRESOLVED_BLOCK"
BLOCK_REJECTED = "BLOCK / CLAIM_REJECTED_OR_NOT_ALLOWED"
BLOCK_WORDING = "BLOCK / CLAIM_WORDING_EXCEEDS_ALLOWED_STRENGTH"
FAIL_SELF_FILLED = "FAIL / CORE_CLAIM_CLASSIFICATION_SELF_FILLED"
FAIL_PROVIDER_MISSING = "FAIL / CORE_CLAIM_CLASSIFICATION_PROVIDER_MISSING"


@dataclass
class EnforcementResult:
    verdict: str
    reason: str
    claim_id: str = ""
    allowed_claim_strength: str = ""
    blocked_token: str = ""

    @property
    def is_pass(self) -> bool:
        return self.verdict == PASS_READY

    @property
    def is_block(self) -> bool:
        return self.verdict.startswith("BLOCK")

    @property
    def is_fail(self) -> bool:
        return self.verdict.startswith("FAIL")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def requires_claim_check(claim_type: str, is_core_conclusion: bool) -> bool:
    """Return True if this claim must have a completed claim_check_result.

    High-risk claim types always require claim check.
    Any claim explicitly marked as a core conclusion also requires it.
    """
    if claim_type in HIGH_RISK_CLAIM_TYPES:
        return True
    if is_core_conclusion is True:
        return True
    return False


def validate_core_claim_classification(
    claim_type: str,
    is_core_conclusion: bool,
    claim_type_provider_role_id: str | None,
    generator_role_id: str,
) -> EnforcementResult | None:
    """Guard against generator self-filling core claim classification.

    Returns an EnforcementResult (FAIL) if a violation is detected,
    or None if classification appears legitimate.

    Rules:
      - provider_role_id must be provided if claim is high-risk or core.
      - provider_role_id must differ from generator_role_id.
    """
    is_high_risk = claim_type in HIGH_RISK_CLAIM_TYPES
    if not (is_high_risk or is_core_conclusion):
        return None  # not a high-risk claim; classification guard not required

    if not claim_type_provider_role_id:
        return EnforcementResult(
            verdict=FAIL_PROVIDER_MISSING,
            reason=(
                f"claim_type '{claim_type}' is high-risk or marked core_conclusion "
                "but claim_type_provider_role_id is missing or empty."
            ),
        )

    if claim_type_provider_role_id == generator_role_id:
        return EnforcementResult(
            verdict=FAIL_SELF_FILLED,
            reason=(
                f"claim_type_provider_role_id '{claim_type_provider_role_id}' "
                f"equals generator_role_id '{generator_role_id}'. "
                "Paper generator must not self-classify high-risk or core claims."
            ),
        )

    return None  # legitimate classification


def check_wording_compatible(
    proposed_wording: str,
    allowed_claim_strength: str,
) -> EnforcementResult | None:
    """Return a BLOCK result if proposed_wording exceeds allowed_claim_strength.

    Returns None if wording is compatible.
    """
    if allowed_claim_strength == "not_allowed":
        if proposed_wording.strip():
            return EnforcementResult(
                verdict=BLOCK_REJECTED,
                reason=(
                    "allowed_claim_strength is 'not_allowed'. "
                    "No wording may be written into core conclusions."
                ),
                allowed_claim_strength=allowed_claim_strength,
                blocked_token="(any non-empty wording)",
            )
        return None

    if allowed_claim_strength in _FORBIDDEN_BY_STRENGTH:
        english_token = _english_lexical_block_token(proposed_wording)
        if english_token is not None:
            return EnforcementResult(
                verdict=BLOCK_WORDING,
                reason=(
                    f"Proposed wording contains curated English escalation '{english_token}' "
                    f"which exceeds allowed_claim_strength '{allowed_claim_strength}'."
                ),
                allowed_claim_strength=allowed_claim_strength,
                blocked_token=english_token,
            )

    forbidden = _FORBIDDEN_BY_STRENGTH.get(allowed_claim_strength, ())
    for token in forbidden:
        if token in proposed_wording:
            return EnforcementResult(
                verdict=BLOCK_WORDING,
                reason=(
                    f"Proposed wording contains '{token}' which exceeds "
                    f"allowed_claim_strength '{allowed_claim_strength}'."
                ),
                allowed_claim_strength=allowed_claim_strength,
                blocked_token=token,
            )
    return None


def enforce_claim_ready_for_writing(
    claim_id: str,
    claim_type: str,
    is_core_conclusion: bool,
    claim_check_result: dict[str, Any] | None,
    proposed_wording: str = "",
    claim_type_provider_role_id: str | None = None,
    generator_role_id: str = "",
) -> EnforcementResult:
    """Main enforcement gate.

    Returns EnforcementResult.  PASS only when settlement is complete and
    proposed wording does not exceed allowed strength.

    Args:
        claim_id: identifier for this claim (for reporting only).
        claim_type: must be in ALLOWED_CLAIM_TYPES.
        is_core_conclusion: True if this sentence is the paper's core result.
        claim_check_result: dict with keys from run_dryrun() output, or None.
            Expected keys: status, human_decision, recommended_decision,
                           allowed_claim_strength, red_flag_ids, red_flag_count.
        proposed_wording: the sentence the paper generator intends to write.
        claim_type_provider_role_id: who classified this claim type.
        generator_role_id: the paper generator's role id.

    Returns:
        EnforcementResult with verdict, reason, and optional blocked_token.
    """
    # --- Step 0: classification guard ---
    if generator_role_id or claim_type_provider_role_id:
        guard = validate_core_claim_classification(
            claim_type=claim_type,
            is_core_conclusion=is_core_conclusion,
            claim_type_provider_role_id=claim_type_provider_role_id,
            generator_role_id=generator_role_id,
        )
        if guard is not None:
            guard.claim_id = claim_id
            return guard

    # --- Step 1: does this claim require claim check? ---
    if not requires_claim_check(claim_type, is_core_conclusion):
        return EnforcementResult(
            verdict=PASS_READY,
            reason="Claim type is not high-risk and is_core_conclusion is False; no gate required.",
            claim_id=claim_id,
        )

    # --- Step 2: result must exist ---
    if claim_check_result is None:
        return EnforcementResult(
            verdict=BLOCK_MISSING,
            reason=(
                f"Claim type '{claim_type}' requires claim check "
                "but claim_check_result is None."
            ),
            claim_id=claim_id,
        )

    human_decision: str = claim_check_result.get("human_decision", "PENDING")
    allowed_strength: str = claim_check_result.get("allowed_claim_strength", "not_allowed")
    red_flag_count: int = claim_check_result.get("red_flag_count", 0)
    red_flag_ids: list[str] = claim_check_result.get("red_flag_ids", [])

    # --- Step 3: RED + PENDING ---
    if red_flag_count > 0 and human_decision == "PENDING":
        return EnforcementResult(
            verdict=BLOCK_UNRESOLVED,
            reason=(
                f"{red_flag_count} RED flag(s) present "
                f"({', '.join(red_flag_ids[:3])}{'...' if len(red_flag_ids) > 3 else ''}) "
                "and human_decision is PENDING. "
                "Human must issue A / B / C decision before writing."
            ),
            claim_id=claim_id,
            allowed_claim_strength=allowed_strength,
        )

    # --- Step 4: rejected or not_allowed ---
    if human_decision == "C_REJECT_AND_REWORK" or allowed_strength == "not_allowed":
        return EnforcementResult(
            verdict=BLOCK_REJECTED,
            reason=(
                f"human_decision='{human_decision}', "
                f"allowed_claim_strength='{allowed_strength}'. "
                "Must rework and re-run claim check before writing."
            ),
            claim_id=claim_id,
            allowed_claim_strength=allowed_strength,
        )

    # --- Step 5: wording check ---
    if proposed_wording:
        wording_block = check_wording_compatible(proposed_wording, allowed_strength)
        if wording_block is not None:
            wording_block.claim_id = claim_id
            return wording_block

    # --- Step 6: pass ---
    return EnforcementResult(
        verdict=PASS_READY,
        reason=(
            f"human_decision='{human_decision}', "
            f"allowed_claim_strength='{allowed_strength}'. "
            "Settlement complete and wording compatible."
        ),
        claim_id=claim_id,
        allowed_claim_strength=allowed_strength,
    )
