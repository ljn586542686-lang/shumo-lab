from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from pipeline_core.python_tools.r3_approval_interface import (
    DRAFT_SCHEMA_VERSION,
    R3ApprovalInterfaceBlocked,
    build_approval_input_template,
    build_review_constraints_draft,
    canonicalize_approved_constraint_payload,
    payload_sha256_from_draft,
)


def _item(identifier: str = "R3-PROPOSED-001") -> dict:
    return {
        "constraint_id": identifier,
        "constraint_type": "CLAIM_REFERENCE",
        "authority_reference": {"authority_type": "R2A_CLAIM", "claim_id": "C-PROPOSED"},
        "target_reference": {"target_type": "paper_span", "claim_id": "C-PROPOSED"},
        "required_action": "PRESERVE_REQUIRED_INCLUSION",
        "review_mode": "MANUAL_REVIEW_REQUIRED",
        "enforcement_reference": {"enforcement_type": "MANUAL_REVIEW_ONLY"},
        "source_evidence_references": ["R3C_PROPOSED_CONSTRAINT"],
    }


class R3ApprovalInterfaceTests(unittest.TestCase):
    def test_draft_is_explicitly_non_authority(self) -> None:
        draft = build_review_constraints_draft("cases/example", "run-1", [_item()])
        self.assertEqual(draft["schema_version"], DRAFT_SCHEMA_VERSION)
        self.assertEqual(draft["status"], "DRAFT_NOT_AUTHORITY")
        self.assertNotIn("approved_constraint_set_sha256", draft)

    def test_order_is_deterministic(self) -> None:
        first = canonicalize_approved_constraint_payload([_item("B"), _item("A")])
        second = canonicalize_approved_constraint_payload([_item("A"), _item("B")])
        self.assertEqual(first, second)

    def test_content_change_changes_hash(self) -> None:
        first = canonicalize_approved_constraint_payload([_item()])
        changed = _item()
        changed["required_action"] = "FORBID_UNAPPROVED_REPLACEMENT"
        second = canonicalize_approved_constraint_payload([changed])
        self.assertNotEqual(hashlib.sha256(first).hexdigest(), hashlib.sha256(second).hexdigest())

    def test_duplicate_id_blocks(self) -> None:
        with self.assertRaisesRegex(R3ApprovalInterfaceBlocked, "DRAFT_DUPLICATE_CONSTRAINT_ID"):
            build_review_constraints_draft("cases/example", "run-1", [_item(), _item()])

    def test_hash_helper_reads_once_and_only_displays(self) -> None:
        draft = build_review_constraints_draft("cases/example", "run-1", [_item()])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review_constraints_draft.json"
            path.write_text(json.dumps(draft), encoding="utf-8")
            result = payload_sha256_from_draft(path)
            self.assertEqual(result["read_count"], 1)
            self.assertEqual(result["helper_output"], "DISPLAY_ONLY")
            self.assertFalse(result["formal_approval_input_written"])
            self.assertFalse((Path(directory) / "r3_approval_input.json").exists())

    def test_hash_helper_rejects_non_draft(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps({"status": "APPROVED"}), encoding="utf-8")
            with self.assertRaisesRegex(R3ApprovalInterfaceBlocked, "DRAFT_SCHEMA_INVALID"):
                payload_sha256_from_draft(path)

    def test_template_is_not_approval(self) -> None:
        template = build_approval_input_template()
        self.assertEqual(template["status"], "TEMPLATE_NOT_APPROVAL")
        self.assertEqual(template["approved_constraint_set_sha256"], "<HUMAN_MUST_FILL>")

    def test_module_has_no_production_consumers(self) -> None:
        source = Path(__file__).parents[1].joinpath("python_tools", "r3_approval_interface.py").read_text(encoding="utf-8")
        self.assertNotIn("package_candidate(", source)
        self.assertNotIn("check_freeze_ready(", source)


if __name__ == "__main__":
    unittest.main()
