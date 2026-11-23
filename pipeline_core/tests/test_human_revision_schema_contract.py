"""Regression tests for the human_revision_record.json canonical schema
contract: pipeline_core/python_tools/human_revision_check.py
::check_human_revision() (founding commit 15e2b70, byte-identical
transplant in 99e911b -- the schema has never had a second version).

These tests lock the schema against the specific defect discovered
during the FULL_GATE_CHAIN_E2E retry: the case_12_full_gate_v2 fixture's
human_revision_record.json used field names (confirmed_by/
confirmation_text/scope-as-string) that do not match what
check_human_revision() actually requires (source=="human"/
status=="COMPLETED"/reviewer_name/manual_confirmation/scope-as-array).
No production code was changed to fix this -- only the fixture's
evidence file needed field-name normalization, which this suite does
not perform (see docs/status/HUMAN_REVISION_SCHEMA_CONTRACT_STATUS.md
for the normalized artifact and its audit trail).
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pipeline_core.python_tools.human_revision_check import check_human_revision


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class HumanRevisionSchemaContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.case_dir = Path(self.temp_dir.name) / "case"
        self.case_dir.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_config(self, **overrides: object) -> None:
        config = {
            "submission_tier": "selection",
            "human_revision_mode": "full",
            "human_revision_required": True,
            "human_revision_record_path": "human_review/human_revision_record.json",
        }
        config.update(overrides)
        _write_json(self.case_dir / "case_config.json", config)

    def _canonical_record(self, **overrides: object) -> dict[str, object]:
        record = {
            "source": "human",
            "status": "COMPLETED",
            "reviewer_name": "repo_owner",
            "manual_confirmation": (
                "我已查看本次E2E夹具的核心结果、Claim Check限制项和字段修正，"
                "确认Human Revision完成。本确认仅用于全门禁E2E机制验证，"
                "不代表正式比赛提交授权，也不授权生成final_submit。"
            ),
            "scope": ["full_gate_e2e_mechanism_validation_only"],
            "reviewed_at": "2026-07-26T12:17:11+00:00",
            "final_submit_authorized": False,
        }
        record.update(overrides)
        return record

    def _write_record(self, record: dict[str, object]) -> Path:
        path = self.case_dir / "human_review" / "human_revision_record.json"
        _write_json(path, record)
        return path

    # ---- Requirement 1: canonical legitimate record passes ----

    def test_canonical_record_passes(self) -> None:
        self._write_config()
        self._write_record(self._canonical_record())
        result = check_human_revision(self.case_dir)
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["human_revision"]["completed"])
        self.assertEqual(result["blockers"], [])

    # ---- Requirement 2: source != human blocks ----

    def test_source_not_human_blocks(self) -> None:
        self._write_config()
        self._write_record(self._canonical_record(source="ai"))
        result = check_human_revision(self.case_dir)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("HUMAN_REVISION_RECORD_NOT_HUMAN", result["blockers"])

    def test_source_missing_blocks(self) -> None:
        self._write_config()
        record = self._canonical_record()
        del record["source"]
        self._write_record(record)
        result = check_human_revision(self.case_dir)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("HUMAN_REVISION_RECORD_NOT_HUMAN", result["blockers"])

    # ---- Requirement 3: status != COMPLETED blocks ----

    def test_status_not_completed_blocks(self) -> None:
        self._write_config()
        self._write_record(self._canonical_record(status="PENDING"))
        result = check_human_revision(self.case_dir)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("HUMAN_REVISION_RECORD_INCOMPLETE", result["blockers"])

    # ---- Requirement 4: reviewer_name missing blocks ----

    def test_reviewer_name_missing_blocks(self) -> None:
        self._write_config()
        record = self._canonical_record()
        del record["reviewer_name"]
        self._write_record(record)
        result = check_human_revision(self.case_dir)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("HUMAN_REVISION_RECORD_INCOMPLETE", result["blockers"])

    def test_reviewer_name_empty_string_blocks(self) -> None:
        self._write_config()
        self._write_record(self._canonical_record(reviewer_name="   "))
        result = check_human_revision(self.case_dir)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("HUMAN_REVISION_RECORD_INCOMPLETE", result["blockers"])

    # ---- Requirement 5: manual_confirmation missing or empty blocks ----

    def test_manual_confirmation_missing_blocks(self) -> None:
        self._write_config()
        record = self._canonical_record()
        del record["manual_confirmation"]
        self._write_record(record)
        result = check_human_revision(self.case_dir)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("HUMAN_REVISION_RECORD_INCOMPLETE", result["blockers"])

    def test_manual_confirmation_empty_string_blocks(self) -> None:
        self._write_config()
        self._write_record(self._canonical_record(manual_confirmation=""))
        result = check_human_revision(self.case_dir)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("HUMAN_REVISION_RECORD_INCOMPLETE", result["blockers"])

    # ---- Requirement 6: scope not a string array blocks ----

    def test_scope_as_string_blocks(self) -> None:
        self._write_config()
        self._write_record(self._canonical_record(scope="full_gate_e2e_mechanism_validation_only"))
        result = check_human_revision(self.case_dir)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("HUMAN_REVISION_RECORD_INCOMPLETE", result["blockers"])

    def test_scope_with_non_string_items_blocks(self) -> None:
        self._write_config()
        self._write_record(self._canonical_record(scope=[1, 2]))
        result = check_human_revision(self.case_dir)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("HUMAN_REVISION_RECORD_INCOMPLETE", result["blockers"])

    # ---- Requirement 7: scope empty array blocks ----

    def test_scope_empty_array_blocks(self) -> None:
        self._write_config()
        self._write_record(self._canonical_record(scope=[]))
        result = check_human_revision(self.case_dir)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("HUMAN_REVISION_RECORD_INCOMPLETE", result["blockers"])

    # ---- Requirement 8: final authorization must not be derivable from Human Revision ----

    def test_final_submit_authorized_field_does_not_grant_final_authorization(self) -> None:
        self._write_config()
        self._write_record(self._canonical_record(final_submit_authorized=False))
        result = check_human_revision(self.case_dir)
        self.assertEqual(result["status"], "PASS")
        # check_human_revision()'s return payload has no concept of final
        # submission authorization at all -- passing this gate can never by
        # itself grant final_submit_allowed/final_freeze_ready.
        self.assertNotIn("final_submit_allowed", result)
        self.assertNotIn("final_freeze_ready", result)
        self.assertFalse(result["final_submit_created"])

    # ---- Requirement 9: legacy/wrong-schema record fails closed, no fallback ----

    def test_legacy_confirmed_by_schema_fails_closed_not_silently_accepted(self) -> None:
        self._write_config()
        legacy_record = {
            "confirmed_by": "repo_owner",
            "confirmation_text": "我已查看本次E2E夹具的核心结果，确认Human Revision完成。",
            "confirmed_at": "2026-07-26T12:17:11+00:00",
            "claim_check_artifact_sha256": "cc7636fb2e58141ea84f2f8847c8ef59f203adf575d63eb67ea4073ac1dc8935",
            "scope": "full_gate_e2e_mechanism_validation_only",
            "final_submit_authorized": False,
        }
        self._write_record(legacy_record)
        result = check_human_revision(self.case_dir)
        # Neither "source" (missing -> not "human") nor scope (string, not
        # a non-empty string array) satisfy the canonical schema, so this
        # must block -- not be silently reinterpreted via any dual-schema
        # fallback.
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("HUMAN_REVISION_RECORD_NOT_HUMAN", result["blockers"])

    # ---- Requirement 10: single-byte tamper is detectable via content hash ----

    def test_single_byte_tamper_changes_record_hash(self) -> None:
        self._write_config()
        record_path = self._write_record(self._canonical_record())
        original_bytes = record_path.read_bytes()
        original_hash = hashlib.sha256(original_bytes).hexdigest()

        tampered_bytes = original_bytes + b" "
        record_path.write_bytes(tampered_bytes)
        tampered_hash = hashlib.sha256(tampered_bytes).hexdigest()

        self.assertNotEqual(original_hash, tampered_hash)
        # The tampered bytes are still valid JSON with the trailing space,
        # so check_human_revision() itself still reads it successfully;
        # the content-binding hash mismatch is what a downstream binding
        # consumer (package_candidate._build_upstream_artifact_bindings)
        # would use to detect this exact class of tamper, independent of
        # check_human_revision()'s own schema validation.
        result = check_human_revision(self.case_dir)
        self.assertEqual(result["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
