"""Dry-run wrapper for F5.5 Adversarial Prize Claim Check (X5-F5.5B).

Loads the canonical synthetic boundary example claim card, runs the red-flag evaluation
pipeline, and prints a structured result.  Designed to be the minimal
invocation hook that proves the math_core.claim_check module is reachable from
the pipeline_core layer.

Usage:
    python pipeline_core/claim_check/run_claim_check_dryrun.py

Exit codes:
    0 — dry-run completed (RED flags are expected for the synthetic boundary card; this
        is correct behaviour, not a failure)
    1 — module import or JSON load error
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Allow running from repo root without installing as a package
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

EXAMPLE_CARD_PATH = (
    Path(__file__).parent / "examples" / "synthetic_boundary.json"
)


def load_claim_card_from_json(path: Path):
    """Load a ClaimCard from a JSON file (pipeline_core format)."""
    from math_core.claim_check.schema import ClaimCard, EvidenceInput

    raw = json.loads(path.read_text(encoding="utf-8"))

    evidence_inputs = [
        EvidenceInput(**{
            k: v for k, v in ei.items()
            if k in EvidenceInput.__dataclass_fields__
        })
        for ei in raw.get("evidence_inputs", [])
    ]

    card_fields = {
        k: v for k, v in raw.items()
        if k in ClaimCard.__dataclass_fields__ and k != "evidence_inputs"
    }
    card_fields["evidence_inputs"] = evidence_inputs

    # Reset evaluator output fields so the machine recomputes fresh.
    # Use dataclass defaults: allowed_claim_strength="not_allowed", human_decision="PENDING".
    card_fields["machine_red_flags"] = []
    card_fields["status"] = "PENDING"
    card_fields["recommended_decision"] = "PENDING"
    card_fields["allowed_claim_strength"] = "not_allowed"
    card_fields["human_decision"] = "PENDING"

    return ClaimCard(**card_fields)


def run_dryrun(card_path: Path | None = None) -> dict:
    """Run the claim check pipeline on one card.  Returns result dict."""
    from math_core.claim_check.adversarial_claim_check import evaluate_claim

    path = card_path or EXAMPLE_CARD_PATH
    card = load_claim_card_from_json(path)
    result_card = evaluate_claim(card)

    red_flags = [
        f for f in result_card.machine_red_flags
        if f.get("triggered") and f.get("severity") == "RED"
    ]
    yellow_flags = [
        f for f in result_card.machine_red_flags
        if f.get("triggered") and f.get("severity") == "YELLOW"
    ]

    return {
        "claim_id": result_card.claim_id,
        "claim_type": result_card.claim_type,
        "status": result_card.status,
        "recommended_decision": result_card.recommended_decision,
        "allowed_claim_strength": result_card.allowed_claim_strength,
        "human_decision": result_card.human_decision,
        "red_flag_ids": [f["flag_id"] for f in red_flags],
        "yellow_flag_ids": [f["flag_id"] for f in yellow_flags],
        "red_flag_count": len(red_flags),
        "yellow_flag_count": len(yellow_flags),
    }


def main() -> int:
    print("=" * 60)
    print("F5.5 Adversarial Prize Claim Check — dry-run")
    print(f"Card: {EXAMPLE_CARD_PATH.relative_to(_REPO_ROOT)}")
    print("=" * 60)

    try:
        result = run_dryrun()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"claim_id            : {result['claim_id']}")
    print(f"claim_type          : {result['claim_type']}")
    print(f"status              : {result['status']}")
    print(f"recommended_decision: {result['recommended_decision']}")
    print(f"allowed_strength    : {result['allowed_claim_strength']}")
    print(f"human_decision      : {result['human_decision']}")
    print(f"red_flags  ({result['red_flag_count']:2d})     : {result['red_flag_ids']}")
    print(f"yellow_flags ({result['yellow_flag_count']:2d})   : {result['yellow_flag_ids']}")
    print("=" * 60)
    print("Dry-run complete. RED flags are expected for synthetic boundary card.")
    print("Safety: no final_submit created, no cases/ or archive/ modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
