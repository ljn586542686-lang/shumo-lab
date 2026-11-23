"""Read-only draft and hash-display interface for R3 human approval.

This module deliberately stops before formal approval and package/finalizer
integration. It never writes ``r3_approval_input.json`` and never calls
``package_candidate`` or ``final_freezer``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


DRAFT_SCHEMA_VERSION = "s0_r3_review_constraints_draft_v1"
APPROVAL_TEMPLATE_SCHEMA_VERSION = "s0_r3_approval_input_v1"
DRAFT_STATUS = "DRAFT_NOT_AUTHORITY"
CONSTRAINT_TYPES = {
    "RESULT_REFERENCE",
    "CLAIM_REFERENCE",
    "DEFINITION_REFERENCE",
    "PRESERVE_REQUIRED_INCLUSION",
}
REVIEW_MODES = {"DETERMINISTIC", "MANUAL_REVIEW_REQUIRED"}
REQUIRED_ACTIONS = {
    "PRESERVE_AUTHORITY_REFERENCE",
    "PRESERVE_REQUIRED_INCLUSION",
    "FORBID_UNAPPROVED_REPLACEMENT",
    "MANUAL_REVIEW",
}
ENFORCEMENT_TYPES = {"R4A_CLAIM_ANCHOR", "R4B_STRUCTURED_CONSTRAINT", "MANUAL_REVIEW_ONLY"}
ITEM_FIELDS = {
    "constraint_id",
    "constraint_type",
    "authority_reference",
    "target_reference",
    "required_action",
    "review_mode",
    "enforcement_reference",
    "source_evidence_references",
}
FORBIDDEN_PAYLOAD_FIELDS = {
    "timestamp",
    "approval_reference",
    "operator_reference",
    "package_hash",
    "artifact_hash",
    "draft_status",
    "trusted_manifest_sha256",
}


class R3ApprovalInterfaceBlocked(ValueError):
    """Raised when a draft cannot satisfy the non-authority interface."""


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _copy_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _validate_items(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        raise R3ApprovalInterfaceBlocked("DRAFT_ITEMS_MUST_BE_LIST")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            raise R3ApprovalInterfaceBlocked("DRAFT_ITEM_MUST_BE_OBJECT")
        missing = sorted(ITEM_FIELDS - set(raw))
        if missing:
            raise R3ApprovalInterfaceBlocked(f"DRAFT_ITEM_FIELDS_MISSING:{','.join(missing)}")
        constraint_id = raw.get("constraint_id")
        if not isinstance(constraint_id, str) or not constraint_id.strip():
            raise R3ApprovalInterfaceBlocked("DRAFT_CONSTRAINT_ID_REQUIRED")
        constraint_id = constraint_id.strip()
        if constraint_id in seen:
            raise R3ApprovalInterfaceBlocked("DRAFT_DUPLICATE_CONSTRAINT_ID")
        seen.add(constraint_id)
        if raw.get("constraint_type") not in CONSTRAINT_TYPES:
            raise R3ApprovalInterfaceBlocked("DRAFT_CONSTRAINT_TYPE_INVALID")
        if raw.get("review_mode") not in REVIEW_MODES:
            raise R3ApprovalInterfaceBlocked("DRAFT_REVIEW_MODE_INVALID")
        if raw.get("required_action") not in REQUIRED_ACTIONS:
            raise R3ApprovalInterfaceBlocked("DRAFT_REQUIRED_ACTION_INVALID")
        enforcement = raw.get("enforcement_reference")
        if not isinstance(enforcement, dict) or enforcement.get("enforcement_type") not in ENFORCEMENT_TYPES:
            raise R3ApprovalInterfaceBlocked("DRAFT_ENFORCEMENT_REFERENCE_INVALID")
        refs = raw.get("source_evidence_references")
        if not isinstance(refs, list) or any(not isinstance(item, str) or not item.strip() for item in refs):
            raise R3ApprovalInterfaceBlocked("DRAFT_SOURCE_REFERENCES_INVALID")
        normalized_item = _copy_json(raw)
        normalized_item["constraint_id"] = constraint_id
        normalized.append(normalized_item)
    return sorted(normalized, key=lambda item: item["constraint_id"])


def build_review_constraints_draft(case_identity: str, run_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Create a proposal artifact; it is never an approval or trust source."""

    if not isinstance(case_identity, str) or not case_identity.strip():
        raise R3ApprovalInterfaceBlocked("DRAFT_CASE_IDENTITY_REQUIRED")
    if not isinstance(run_id, str) or not run_id.strip():
        raise R3ApprovalInterfaceBlocked("DRAFT_RUN_ID_REQUIRED")
    normalized = _validate_items(items)
    return {
        "schema_version": DRAFT_SCHEMA_VERSION,
        "status": DRAFT_STATUS,
        "case_identity": case_identity.strip(),
        "run_id": run_id.strip(),
        "items": normalized,
        "notice": "DRAFT_NOT_AUTHORITY; human approval is required before any formal input exists.",
    }


def canonicalize_approved_constraint_payload(items: Any) -> bytes:
    """Canonicalize only approved constraint content, excluding metadata."""

    if isinstance(items, dict):
        extra = set(items) - {"items"}
        if extra:
            forbidden = sorted(extra.intersection(FORBIDDEN_PAYLOAD_FIELDS))
            raise R3ApprovalInterfaceBlocked(
                "APPROVED_PAYLOAD_METADATA_FORBIDDEN:" + ",".join(forbidden or sorted(extra))
            )
        items = items.get("items")
    normalized = _validate_items(items)
    return _canonical_json_bytes({"items": normalized})


def payload_sha256_from_draft(path: str | Path) -> dict[str, Any]:
    """Read a draft once and display its payload hash without writing approval."""

    draft_path = Path(path)
    raw = draft_path.read_bytes()
    payload = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(payload, dict) or payload.get("schema_version") != DRAFT_SCHEMA_VERSION:
        raise R3ApprovalInterfaceBlocked("DRAFT_SCHEMA_INVALID")
    if payload.get("status") != DRAFT_STATUS:
        raise R3ApprovalInterfaceBlocked("DRAFT_STATUS_NOT_AUTHORITY")
    canonical = canonicalize_approved_constraint_payload(payload.get("items"))
    return {
        "R3_APPROVED_CONSTRAINT_PAYLOAD_SHA256": hashlib.sha256(canonical).hexdigest(),
        "case_identity": payload.get("case_identity"),
        "run_id": payload.get("run_id"),
        "constraint_count": len(payload.get("items") or []),
        "read_count": 1,
        "helper_output": "DISPLAY_ONLY",
        "formal_approval_input_written": False,
    }


def build_approval_input_template(case_identity: str = "<FILL_FROM_DRAFT>", run_id: str = "<FILL_FROM_DRAFT>") -> dict[str, str]:
    """Return a human-fill template; never fill the approved hash."""

    return {
        "schema_version": APPROVAL_TEMPLATE_SCHEMA_VERSION,
        "case_identity": case_identity,
        "run_id": run_id,
        "approved_constraint_set_sha256": "<HUMAN_MUST_FILL>",
        "approval_reference": "<HUMAN_MUST_FILL>",
        "status": "TEMPLATE_NOT_APPROVAL",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Display an R3 draft payload hash only.")
    parser.add_argument("--draft", required=True)
    args = parser.parse_args()
    print(json.dumps(payload_sha256_from_draft(args.draft), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
