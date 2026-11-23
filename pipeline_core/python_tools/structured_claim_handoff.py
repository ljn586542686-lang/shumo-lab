"""Structured handoff for high-risk core claims.

This module is deliberately small and consumes the existing claim card and
claim-check preflight artifacts.  It does not create a claim registry, alter
claim wording rules, or perform semantic classification.  Ordinary prose is
outside this contract; only configured high-risk core claims are materialized.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from pipeline_core.claim_check.enforce_claim_check import HIGH_RISK_CLAIM_TYPES

P0_FIELDS = (
    "claim_id",
    "claim_type",
    "approved_strength",
    "approved_wording",
    "evidence_scope",
    "limitations",
    "source_evidence_references",
    "claim_check_status",
)
V2_SCHEMA_VERSION = "s0_r2a_structured_claim_handoff_v2"
CONSTRAINT_TYPES = {"SIGN", "LOWER_BOUND", "UPPER_BOUND", "INTERVAL", "FROZEN_VALUE"}


class StructuredClaimHandoffBlocked(ValueError):
    """Raised when the structured claim contract cannot be satisfied."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _case_path(case_dir: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = Path(value)
    if raw.is_absolute() or ".." in raw.parts:
        return None
    root = case_dir.resolve()
    resolved = (root / raw).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def _nonempty(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return value is not None


def _anchor_from_claim(claim: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    primary = claim.get("primary_claim_anchor", claim.get("paper_anchor"))
    secondary = claim.get("secondary_references", [])
    if not isinstance(secondary, list):
        raise StructuredClaimHandoffBlocked(
            "CLAIM_ANCHOR_SCHEMA_ERROR", "secondary_references must be a list"
        )
    if primary is not None and not isinstance(primary, dict):
        raise StructuredClaimHandoffBlocked(
            "CLAIM_ANCHOR_SCHEMA_ERROR", "primary claim anchor must be an object"
        )
    if any(not isinstance(item, dict) for item in secondary):
        raise StructuredClaimHandoffBlocked(
            "CLAIM_ANCHOR_SCHEMA_ERROR", "secondary references must be objects"
        )
    return primary, secondary


def build_structured_claim_handoff(
    case_dir: str | Path,
    claim_check_result: dict[str, Any],
    *,
    required: bool,
) -> dict[str, Any]:
    """Build the handoff from the current production claim inputs.

    ``required=False`` is an explicit legacy compatibility path.  It is
    represented in the returned object and is never labelled as the new
    schema.  When required, every configured high-risk core claim must satisfy
    the complete contract before a candidate package can be created.
    """

    if not required:
        return {
            "status": "LEGACY_CLAIM_HANDOFF_NOT_REQUIRED",
            "schema_version": None,
            "lock_claims_not_prose": True,
            "legacy_claim_handoff": True,
            "claims": [],
            "result_authority": "R1_CANONICAL_INDEX",
        }

    case_dir = Path(case_dir)
    config = _read_json(case_dir / "case_config.json") or {}
    configured = config.get("core_claims")
    if not isinstance(configured, list) or not configured:
        raise StructuredClaimHandoffBlocked(
            "CLAIM_HANDOFF_REQUIRED_BUT_NO_CORE_CLAIMS",
            "structured claim handoff is required but no core_claims are configured",
        )

    result_claims = claim_check_result.get("claims")
    result_by_id = {
        item.get("claim_id"): item
        for item in (result_claims if isinstance(result_claims, list) else [])
        if isinstance(item, dict) and item.get("claim_id")
    }
    structured_claims: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_anchors: set[str] = set()

    for raw_claim in configured:
        if not isinstance(raw_claim, dict):
            raise StructuredClaimHandoffBlocked("CLAIM_HANDOFF_SCHEMA_ERROR", "core claim is not an object")
        claim_id = str(raw_claim.get("claim_id") or "").strip()
        claim_type = str(raw_claim.get("claim_type") or "").strip()
        if not claim_id or not claim_type:
            raise StructuredClaimHandoffBlocked("CLAIM_HANDOFF_SCHEMA_ERROR", "claim_id and claim_type are required")
        is_core = raw_claim.get("is_core_conclusion") is True
        if claim_type not in HIGH_RISK_CLAIM_TYPES or not is_core:
            continue
        if claim_id in seen_ids:
            raise StructuredClaimHandoffBlocked("CLAIM_HANDOFF_DUPLICATE_CLAIM_ID", f"duplicate claim_id: {claim_id}")
        seen_ids.add(claim_id)

        card_path = _case_path(case_dir, raw_claim.get("claim_card_path"))
        if card_path is None or not card_path.is_file():
            raise StructuredClaimHandoffBlocked("CLAIM_HANDOFF_CARD_MISSING", f"claim card missing: {claim_id}")
        card = _read_json(card_path)
        if card is None:
            raise StructuredClaimHandoffBlocked("CLAIM_HANDOFF_CARD_UNREADABLE", f"claim card unreadable: {claim_id}")
        result_claim = result_by_id.get(claim_id) or {}
        claim_status = str(result_claim.get("status") or "")
        if claim_status != "PASS":
            raise StructuredClaimHandoffBlocked(
                "CLAIM_HANDOFF_CLAIM_NOT_PASS",
                f"claim {claim_id} did not pass the existing claim gate",
                details={"claim_id": claim_id, "status": claim_status},
            )

        proposed_path = _case_path(case_dir, raw_claim.get("proposed_wording_path"))
        if proposed_path is None or not proposed_path.is_file():
            raise StructuredClaimHandoffBlocked("CLAIM_HANDOFF_WORDING_MISSING", f"wording missing: {claim_id}")
        proposed_wording = proposed_path.read_text(encoding="utf-8")

        approved_strength = card.get("approved_strength")
        approved_wording = card.get("approved_wording")
        evidence_scope = card.get("evidence_scope")
        limitations = card.get("limitations")
        source_refs = card.get("source_evidence_references")
        if "approved_constraints" in raw_claim:
            approved_constraints = raw_claim.get("approved_constraints")
        elif "approved_constraints" in card:
            approved_constraints = card.get("approved_constraints")
        else:
            raise StructuredClaimHandoffBlocked(
                "BLOCK_SCHEMA_INVALID:R2A_APPROVED_CONSTRAINTS_EXPLICIT_REQUIRED",
                f"approved_constraints must be explicitly declared: {claim_id}",
                details={"claim_id": claim_id},
            )
        paper_span_reference = raw_claim.get("paper_span_reference", card.get("paper_span_reference"))
        values = {
            "claim_id": claim_id,
            "claim_type": claim_type,
            "approved_strength": approved_strength,
            "approved_wording": approved_wording,
            "evidence_scope": evidence_scope,
            "limitations": limitations,
            "source_evidence_references": source_refs,
            "claim_check_status": claim_status,
        }
        missing = [name for name, value in values.items() if not _nonempty(value)]
        if missing:
            raise StructuredClaimHandoffBlocked(
                "CLAIM_HANDOFF_REQUIRED_FIELD_MISSING",
                f"claim {claim_id} is missing required fields: {', '.join(missing)}",
                details={"claim_id": claim_id, "missing_fields": missing},
            )
        if not isinstance(source_refs, list) or any(not isinstance(item, str) or not item.strip() for item in source_refs):
            raise StructuredClaimHandoffBlocked(
                "CLAIM_HANDOFF_SOURCE_REFERENCE_SCHEMA_ERROR", f"invalid source references: {claim_id}"
            )
        if approved_constraints is None:
            raise StructuredClaimHandoffBlocked(
                "BLOCK_SCHEMA_INVALID:R2A_APPROVED_CONSTRAINTS_EXPLICIT_REQUIRED",
                f"approved_constraints cannot be null: {claim_id}",
                details={"claim_id": claim_id},
            )
        if not isinstance(approved_constraints, list):
            raise StructuredClaimHandoffBlocked("CLAIM_HANDOFF_CONSTRAINT_SCHEMA_ERROR", f"invalid constraints: {claim_id}")
        normalized_constraints: list[dict[str, Any]] = []
        for constraint in approved_constraints:
            if not isinstance(constraint, dict) or constraint.get("constraint_type") not in CONSTRAINT_TYPES:
                raise StructuredClaimHandoffBlocked("CLAIM_HANDOFF_CONSTRAINT_SCHEMA_ERROR", f"invalid constraint type: {claim_id}")
            item = dict(constraint)
            symbol = item.get("symbol")
            if not isinstance(symbol, str) or not symbol.strip():
                raise StructuredClaimHandoffBlocked("BLOCK_SCHEMA_INVALID:R2A_CONSTRAINT_SYMBOL_REQUIRED", f"constraint symbol missing: {claim_id}")
            ctype = item["constraint_type"]
            if ctype in {"LOWER_BOUND", "UPPER_BOUND"}:
                value = item.get("value")
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
                    raise StructuredClaimHandoffBlocked("BLOCK_SCHEMA_INVALID:R2A_CONSTRAINT_VALUE_REQUIRED", f"constraint value missing: {claim_id}")
            elif ctype == "SIGN":
                if item.get("sign") not in {"NON_NEGATIVE", "NON_POSITIVE"}:
                    raise StructuredClaimHandoffBlocked("BLOCK_SCHEMA_INVALID:R2A_CONSTRAINT_SIGN_REQUIRED", f"constraint sign missing: {claim_id}")
            elif ctype == "INTERVAL":
                lower, upper = item.get("lower"), item.get("upper")
                if (
                    not isinstance(lower, (int, float)) or isinstance(lower, bool)
                    or not isinstance(upper, (int, float)) or isinstance(upper, bool)
                    or not math.isfinite(float(lower)) or not math.isfinite(float(upper))
                    or float(lower) > float(upper)
                ):
                    raise StructuredClaimHandoffBlocked("BLOCK_SCHEMA_INVALID:R2A_INTERVAL_BOUNDS_REQUIRED", f"interval bounds invalid: {claim_id}")
            if item["constraint_type"] == "FROZEN_VALUE" and not isinstance(item.get("canonical_value_reference"), dict):
                raise StructuredClaimHandoffBlocked("BLOCK_SCHEMA_INVALID:R2A_CANONICAL_REFERENCE_MISSING", f"frozen value reference missing: {claim_id}")
            tolerance = item.get("comparison_tolerance")
            if tolerance is not None:
                if not isinstance(tolerance, dict):
                    raise StructuredClaimHandoffBlocked("BLOCK_SCHEMA_INVALID:R2A_TOLERANCE_SCHEMA_ERROR", f"invalid comparison tolerance: {claim_id}")
                abs_tol = tolerance.get("abs_tolerance", 0.0)
                rel_tol = tolerance.get("rel_tolerance", 0.0)
                if (
                    not isinstance(abs_tol, (int, float)) or isinstance(abs_tol, bool)
                    or not isinstance(rel_tol, (int, float)) or isinstance(rel_tol, bool)
                    or not math.isfinite(float(abs_tol)) or not math.isfinite(float(rel_tol))
                    or float(abs_tol) < 0 or float(rel_tol) < 0
                ):
                    raise StructuredClaimHandoffBlocked("BLOCK_SCHEMA_INVALID:R2A_TOLERANCE_SCHEMA_ERROR", f"invalid comparison tolerance: {claim_id}")
                item["comparison_tolerance"] = {"abs_tolerance": float(abs_tol), "rel_tolerance": float(rel_tol)}
            canonical = item.get("canonical_value_reference")
            if isinstance(canonical, dict):
                forbidden = {"expected_value", "canonical_value", "value", "abs_tolerance", "rel_tolerance"}
                if forbidden.intersection(canonical):
                    raise StructuredClaimHandoffBlocked("BLOCK_SCHEMA_INVALID:R2A_CANONICAL_REFERENCE_NOT_ADDRESS_ONLY", f"canonical reference must be address-only: {claim_id}")
            if ctype == "FROZEN_VALUE" and tolerance is None:
                raise StructuredClaimHandoffBlocked("BLOCK_SCHEMA_INVALID:R2A_FROZEN_TOLERANCE_EXPLICIT_REQUIRED", f"frozen tolerance missing: {claim_id}")
            normalized_constraints.append(item)
        if normalized_constraints and not isinstance(paper_span_reference, dict):
            raise StructuredClaimHandoffBlocked("BLOCK_SCHEMA_INVALID:R2A_PAPER_SPAN_REFERENCE_MISSING", f"paper span reference missing: {claim_id}")
        if normalized_constraints and isinstance(paper_span_reference, dict):
            span_claim_id = str(paper_span_reference.get("claim_id") or "").strip()
            if span_claim_id != claim_id:
                raise StructuredClaimHandoffBlocked("BLOCK_SCHEMA_INVALID:R2A_PAPER_SPAN_CLAIM_ID_MISMATCH", f"paper span claim mismatch: {claim_id}")
        if str(approved_wording).strip() != proposed_wording.strip():
            raise StructuredClaimHandoffBlocked(
                "CLAIM_HANDOFF_APPROVED_WORDING_MISMATCH",
                f"proposed wording does not match approved wording: {claim_id}",
            )

        primary, secondary = _anchor_from_claim(raw_claim)
        if primary is None:
            raise StructuredClaimHandoffBlocked("CLAIM_HANDOFF_PRIMARY_ANCHOR_MISSING", f"primary anchor missing: {claim_id}")
        anchor_id = str(primary.get("anchor_id") or "").strip()
        anchor_claim_id = str(primary.get("claim_id") or "").strip()
        if not anchor_id or anchor_claim_id != claim_id:
            raise StructuredClaimHandoffBlocked(
                "CLAIM_HANDOFF_CLAIM_ANCHOR_MISMATCH", f"claim/anchor mismatch: {claim_id}"
            )
        if primary.get("role") != "PRIMARY_CLAIM_ANCHOR":
            raise StructuredClaimHandoffBlocked("CLAIM_HANDOFF_PRIMARY_ANCHOR_INVALID", f"invalid primary anchor: {claim_id}")
        if anchor_id in seen_anchors:
            raise StructuredClaimHandoffBlocked("CLAIM_HANDOFF_DUPLICATE_PRIMARY_ANCHOR", f"duplicate anchor: {anchor_id}")
        seen_anchors.add(anchor_id)
        for secondary_ref in secondary:
            if not str(secondary_ref.get("reference") or secondary_ref.get("anchor_id") or "").strip():
                raise StructuredClaimHandoffBlocked("CLAIM_HANDOFF_SECONDARY_ANCHOR_INVALID", f"invalid secondary reference: {claim_id}")

        structured_claims.append(
            {
                **values,
                "approved_constraints": normalized_constraints,
                "paper_span_reference": paper_span_reference,
                "traceability": {
                    "primary_claim_anchor": primary,
                    "secondary_references": secondary,
                },
            }
        )

    if not structured_claims:
        raise StructuredClaimHandoffBlocked(
            "CLAIM_HANDOFF_REQUIRED_BUT_NO_HIGH_RISK_CORE_CLAIMS",
            "no high-risk core claims were eligible for structured handoff",
        )
    if claim_check_result.get("status") != "PASS":
        raise StructuredClaimHandoffBlocked(
            "CLAIM_HANDOFF_CLAIM_GATE_NOT_PASS", "top-level claim-check gate did not pass"
        )

    return {
        "schema_version": V2_SCHEMA_VERSION,
        "status": "PASS",
        "lock_claims_not_prose": True,
        "legacy_claim_handoff": False,
        "result_authority": "R1_CANONICAL_INDEX",
        "claims": structured_claims,
        "traceability_contract": {
            "primary_claim_anchor_uniqueness": True,
            "anchor_match_requires_claim_id": True,
            "ordinary_prose_outside_contract": True,
        },
    }
