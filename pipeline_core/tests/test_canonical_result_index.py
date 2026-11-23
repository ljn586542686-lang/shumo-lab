from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from canonical_result_index import CanonicalResultIndexBlocked, build_index, validate_index


class CanonicalResultIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.case = Path(self.tmp.name) / "case"
        (self.case / "output").mkdir(parents=True)
        self.summary = self.case / "output" / "result_summary.json"
        self.summary.write_text(json.dumps({"schema_version": "x5_f0_result_summary_v1", "run_id": "r1", "status": "PASS"}), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_positive_control_and_hash_binding(self) -> None:
        index = build_index(self.case, {}, "r1")
        index_path = self.case / "authoritative_result_index.json"
        index_path.write_text(json.dumps(index), encoding="utf-8")
        blockers, loaded = validate_index(index_path, self.case)
        self.assertEqual(blockers, [])
        self.assertEqual(loaded["authority_scope_key"], ["question_id", "artifact_role"])

    def test_missing_authority_blocks(self) -> None:
        with self.assertRaises(CanonicalResultIndexBlocked) as caught:
            build_index(self.case, {"authoritative_required_scopes": [["Q4", "primary_result"]]}, "r1")
        self.assertIn("CANONICAL_AUTHORITY_MISSING:Q4:primary_result", caught.exception.result["blockers"])

    def test_duplicate_same_scope_blocks_without_fallback(self) -> None:
        second = self.case / "output" / "result_summary_2.json"
        second.write_text(self.summary.read_text(encoding="utf-8"), encoding="utf-8")
        config = {
            "authoritative_result_artifacts": [
                {"question_id": "Q1", "artifact_role": "primary_result", "path": "output/result_summary.json"},
                {"question_id": "Q1", "artifact_role": "primary_result", "path": "output/result_summary_2.json"},
            ],
            "authoritative_required_scopes": [["Q1", "primary_result"]],
        }
        with self.assertRaises(CanonicalResultIndexBlocked) as caught:
            build_index(self.case, config, "r1")
        self.assertIn("CANONICAL_AUTHORITY_DUPLICATE:Q1:primary_result", caught.exception.result["blockers"])

    def test_hash_mismatch_blocks(self) -> None:
        index = build_index(self.case, {}, "r1")
        index_path = self.case / "authoritative_result_index.json"
        index_path.write_text(json.dumps(index), encoding="utf-8")
        self.summary.write_text("changed", encoding="utf-8")
        blockers, _ = validate_index(index_path, self.case)
        self.assertIn("CANONICAL_HASH_MISMATCH:case:primary_result", blockers)

    def test_q4_validation_role_cannot_be_primary(self) -> None:
        validation = self.case / "output" / "validation.json"
        validation.write_text(json.dumps({"artifact_role": "validation_evidence", "status": "PASS"}), encoding="utf-8")
        config = {
            "authoritative_result_artifacts": [
                {"question_id": "Q4", "artifact_role": "primary_result", "path": "output/validation.json"},
            ],
            "authoritative_required_scopes": [["Q4", "primary_result"]],
        }
        with self.assertRaises(CanonicalResultIndexBlocked) as caught:
            build_index(self.case, config, "r1")
        self.assertIn("CANONICAL_ROLE_MISMATCH:Q4:primary_result", caught.exception.result["blockers"])


if __name__ == "__main__":
    unittest.main()

