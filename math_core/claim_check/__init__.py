"""Adversarial Prize Claim Check — X5-F5.5 baseline.

Prevents AI from writing weak or boundary conclusions as strong core claims.
Mechanism: generator statement + independent adversarial critique +
           machine red flags + human three-way decision.

Anti-degradation rules
----------------------
1. No bureaucratic overhead: zero red flags → auto-pass, no human gate.
2. No rubber-stamp: adversarial critique must come from an independent role,
   not the paper generator; semantic evidence inputs must not be self-filled.
"""

from .adversarial_claim_check import evaluate_claim, ClaimCard
from .red_flag_rules import evaluate_red_flags, RedFlagResult
from .schema import (
    ALLOWED_CLAIM_TYPES,
    ALLOWED_HUMAN_DECISIONS,
    ALLOWED_CLAIM_STRENGTHS,
    ALLOWED_SOURCE_TYPES,
)

__all__ = [
    "evaluate_claim",
    "ClaimCard",
    "evaluate_red_flags",
    "RedFlagResult",
    "ALLOWED_CLAIM_TYPES",
    "ALLOWED_HUMAN_DECISIONS",
    "ALLOWED_CLAIM_STRENGTHS",
    "ALLOWED_SOURCE_TYPES",
]
