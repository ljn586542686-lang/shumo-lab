from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipeline_core.python_tools.human_revision_check import (
    check_human_revision,
    write_human_revision_report,
)


class HumanRevisionCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.case_dir = Path(self.temp_dir.name) / "case"
        self.case_dir.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_config(self, **overrides: object) -> None:
        config = {
            "submission_tier": "training",
            "human_revision_mode": "off",
            "human_revision_required": False,
            "human_revision_record_path": "human_review/human_revision_record.json",
        }
        config.update(overrides)
        (self.case_dir / "case_config.json").write_text(json.dumps(config), encoding="utf-8")

    def _write_record(self, **overrides: object) -> None:
        record = {
            "status": "COMPLETED",
            "reviewer_name": "Human Reviewer",
            "reviewer_role": "manual reviewer",
            "reviewed_at": "2026-07-11T10:00:00+08:00",
            "scope": ["paper wording"],
            "manual_confirmation": "I manually reviewed this scope.",
            "source": "human",
        }
        record.update(overrides)
        path = self.case_dir / "human_review" / "human_revision_record.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record), encoding="utf-8")

    def test_default_training_off_is_skipped(self) -> None:
        self._write_config()
        result = check_human_revision(self.case_dir)
        self.assertEqual(result["status"], "SKIPPED")
        self.assertIn("HUMAN_REVISION_NOT_REQUESTED", result["warnings"])

    def test_test_tier_off_is_skipped(self) -> None:
        self._write_config(submission_tier="test")
        result = check_human_revision(self.case_dir)
        self.assertEqual(result["status"], "SKIPPED")

    def test_homework_brief_is_recommended_not_blocked(self) -> None:
        self._write_config(submission_tier="homework", human_revision_mode="brief")
        result = check_human_revision(self.case_dir)
        self.assertEqual(result["status"], "MANUAL_REVIEW_REQUIRED")
        self.assertNotEqual(result["status"], "BLOCKED")

    def test_selection_full_is_recommended_not_blocked(self) -> None:
        self._write_config(submission_tier="selection", human_revision_mode="full")
        result = check_human_revision(self.case_dir)
        self.assertIn("HUMAN_REVISION_RECOMMENDED", result["warnings"])
        self.assertNotEqual(result["status"], "BLOCKED")

    def test_formal_full_is_recommended_not_blocked(self) -> None:
        self._write_config(submission_tier="formal", human_revision_mode="full")
        result = check_human_revision(self.case_dir)
        self.assertIn("HUMAN_REVISION_RECOMMENDED", result["warnings"])
        self.assertNotEqual(result["status"], "BLOCKED")

    def test_required_missing_record_blocks(self) -> None:
        self._write_config(human_revision_required=True, human_revision_mode="full")
        result = check_human_revision(self.case_dir)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("HUMAN_REVISION_RECORD_MISSING", result["blockers"])

    def test_required_malformed_record_blocks_as_unreadable(self) -> None:
        self._write_config(human_revision_required=True, human_revision_mode="full")
        path = self.case_dir / "human_review" / "human_revision_record.json"
        path.parent.mkdir(parents=True)
        path.write_text("{broken", encoding="utf-8")
        result = check_human_revision(self.case_dir)
        self.assertIn("HUMAN_REVISION_RECORD_UNREADABLE", result["blockers"])

    def test_required_pending_record_is_incomplete(self) -> None:
        self._write_config(human_revision_required=True, human_revision_mode="full")
        self._write_record(status="PENDING")
        result = check_human_revision(self.case_dir)
        self.assertIn("HUMAN_REVISION_RECORD_INCOMPLETE", result["blockers"])

    def test_required_non_human_source_blocks(self) -> None:
        self._write_config(human_revision_required=True, human_revision_mode="full")
        self._write_record(source="ai")
        result = check_human_revision(self.case_dir)
        self.assertIn("HUMAN_REVISION_RECORD_NOT_HUMAN", result["blockers"])

    def test_required_valid_human_record_passes(self) -> None:
        self._write_config(human_revision_required=True, human_revision_mode="full")
        self._write_record()
        result = check_human_revision(self.case_dir)
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["human_revision"]["completed"])

    def test_unknown_submission_tier_never_passes(self) -> None:
        self._write_config(submission_tier="unknown")
        result = check_human_revision(self.case_dir)
        self.assertEqual(result["status"], "MANUAL_REVIEW_REQUIRED")
        self.assertIn("UNKNOWN_SUBMISSION_TIER", result["warnings"])

    def test_required_unsafe_record_path_blocks(self) -> None:
        self._write_config(human_revision_required=True, human_revision_record_path="../record.json")
        result = check_human_revision(self.case_dir)
        self.assertIn("HUMAN_REVISION_REQUIRED_MISSING", result["blockers"])

    def test_default_check_does_not_write_or_create_final_submit(self) -> None:
        self._write_config()
        result = check_human_revision(self.case_dir)
        self.assertFalse((self.case_dir / "output").exists())
        self.assertFalse((self.case_dir / "final_submit").exists())
        self.assertFalse(result["final_submit_created"])

    def test_report_writer_is_explicit(self) -> None:
        self._write_config()
        result = check_human_revision(self.case_dir)
        json_path, markdown_path = write_human_revision_report(result, self.case_dir)
        self.assertTrue(json_path.is_file())
        self.assertTrue(markdown_path.is_file())
        self.assertFalse((self.case_dir / "final_submit").exists())


if __name__ == "__main__":
    unittest.main()
