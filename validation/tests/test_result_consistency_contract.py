from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from validation.result_consistency_contract import check_result_consistency


class ResultConsistencyContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.case_dir = Path(self.temp_dir.name) / "case"
        self.case_dir.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_json(self, relative_path: str, payload: dict) -> None:
        path = self.case_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _write_result_summary(self, **overrides: object) -> None:
        payload = {
            "case_id": "fixture_case",
            "status": "ok",
            "validation_status": "PASS",
            "fatal_items": [],
            "metrics": {"objective_value": 1234.567},
        }
        payload.update(overrides)
        self._write_json("output/result_summary.json", payload)

    def _write_manifest(self, **overrides: object) -> None:
        manifest = {
            "version": "1.0",
            "case_id": "fixture_case",
            "canonical_result_summary": "output/result_summary.json",
            "reported_values": [
                {
                    "value_id": "objective_value",
                    "json_pointer": "/metrics/objective_value",
                    "unit": "units",
                    "display_precision": 2,
                    "claim_scope": "result",
                }
            ],
            "text_sources": [{"source_id": "main_tex", "path": "paper/main.tex", "kind": "tex"}],
            "anchors": [
                {
                    "anchor_id": "abstract_objective",
                    "source_id": "main_tex",
                    "section": "abstract",
                    "value_id": "objective_value",
                    "expected_rendering": "auto",
                    "required": True,
                }
            ],
            "producer_scripts": ["code/solve.py"],
            "code_output_paths": ["output/result_summary.json"],
            "appendix_code_paths": ["code/solve.py"],
            "uniqueness_claims": [],
        }
        manifest.update(overrides)
        self._write_json("output/validation/result_consistency_manifest.json", manifest)

    def _write_passing_fixture(self) -> None:
        self._write_result_summary()
        source = self.case_dir / "paper" / "main.tex"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("The objective value is 1,234.57.", encoding="utf-8")
        code = self.case_dir / "code" / "solve.py"
        code.parent.mkdir(parents=True, exist_ok=True)
        code.write_text("# fixture producer\n", encoding="utf-8")
        self._write_manifest()

    def test_passing_fixture_uses_canonical_result_summary(self) -> None:
        self._write_passing_fixture()
        result = check_result_consistency(self.case_dir)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["canonical_result_summary"], "output/result_summary.json")
        self.assertFalse(result["final_submit_created"])

    def test_missing_result_summary_blocks(self) -> None:
        self._write_manifest()
        result = check_result_consistency(self.case_dir)
        self.assertIn("RESULT_SUMMARY_MISSING", result["blockers"])

    def test_unreadable_result_summary_blocks(self) -> None:
        path = self.case_dir / "output" / "result_summary.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        self._write_manifest()
        result = check_result_consistency(self.case_dir)
        self.assertIn("RESULT_SUMMARY_UNREADABLE", result["blockers"])

    def test_missing_manifest_blocks(self) -> None:
        self._write_result_summary()
        result = check_result_consistency(self.case_dir)
        self.assertIn("RESULT_CONTRACT_MANIFEST_MISSING", result["blockers"])

    def test_empty_manifest_is_unresolved(self) -> None:
        self._write_result_summary()
        self._write_manifest(reported_values=[])
        result = check_result_consistency(self.case_dir)
        self.assertIn("RESULT_CONTRACT_UNRESOLVED", result["blockers"])

    def test_invalid_reported_value_pointer_blocks(self) -> None:
        self._write_passing_fixture()
        self._write_manifest(reported_values=[{"value_id": "objective_value", "json_pointer": "/metrics/missing", "display_precision": 2}])
        result = check_result_consistency(self.case_dir)
        self.assertIn("REPORTED_VALUE_POINTER_INVALID", result["blockers"])

    def test_missing_text_source_blocks(self) -> None:
        self._write_passing_fixture()
        (self.case_dir / "paper" / "main.tex").unlink()
        result = check_result_consistency(self.case_dir)
        self.assertIn("SOURCE_TEXT_MISSING", result["blockers"])

    def test_anchor_numeric_mismatch_blocks(self) -> None:
        self._write_passing_fixture()
        (self.case_dir / "paper" / "main.tex").write_text("The objective value is 1230.00.", encoding="utf-8")
        result = check_result_consistency(self.case_dir)
        self.assertIn("RESULT_VALUE_MISMATCH", result["blockers"])

    def test_missing_producer_script_blocks(self) -> None:
        self._write_passing_fixture()
        (self.case_dir / "code" / "solve.py").unlink()
        result = check_result_consistency(self.case_dir)
        self.assertIn("CODE_FILE_MISSING", result["blockers"])

    def test_missing_appendix_code_file_blocks(self) -> None:
        self._write_passing_fixture()
        self._write_manifest(appendix_code_paths=["code/missing_appendix.py"])
        result = check_result_consistency(self.case_dir)
        self.assertIn("APPENDIX_CODE_SYNC_MISSING", result["blockers"])

    def test_required_uniqueness_evidence_missing_blocks(self) -> None:
        self._write_passing_fixture()
        self._write_manifest(
            uniqueness_claims=[
                {
                    "claim_id": "unique_optimum",
                    "value_id": "objective_value",
                    "required": True,
                    "verification_evidence_path": "output/validation/unique_optimum_check.json",
                }
            ]
        )
        result = check_result_consistency(self.case_dir)
        self.assertIn("UNIQUE_OPTIMUM_EVIDENCE_MISSING", result["blockers"])

    def test_fatal_result_summary_blocks(self) -> None:
        self._write_passing_fixture()
        self._write_result_summary(status="fatal", validation_status="FATAL", fatal_items=[{"code": "fixture"}])
        result = check_result_consistency(self.case_dir)
        self.assertIn("RESULT_SUMMARY_FATAL_STATUS", result["blockers"])
        self.assertEqual(result["status"], "BLOCKED")

    def test_explicit_adapter_fallback_is_accepted(self) -> None:
        self._write_passing_fixture()
        source = self.case_dir / "output" / "result_summary.json"
        payload = json.loads(source.read_text(encoding="utf-8"))
        source.unlink()
        self._write_json("output/adapted_result_summary.json", payload)
        self._write_json("output/result_summary_adapter_report.json", {"validation_continue_allowed": True})
        self._write_manifest(code_output_paths=["output/adapted_result_summary.json"])
        result = check_result_consistency(self.case_dir)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["canonical_result_summary"], "output/adapted_result_summary.json")

    def test_pdf_source_is_manual_review_not_numeric_ocr(self) -> None:
        self._write_passing_fixture()
        pdf_path = self.case_dir / "paper" / "main.pdf"
        pdf_path.write_bytes(b"not scanned as text 1234.57")
        self._write_manifest(
            text_sources=[{"source_id": "main_pdf", "path": "paper/main.pdf", "kind": "pdf"}],
            anchors=[
                {
                    "anchor_id": "abstract_objective",
                    "source_id": "main_pdf",
                    "value_id": "objective_value",
                    "required": True,
                }
            ],
        )
        result = check_result_consistency(self.case_dir)
        self.assertEqual(result["status"], "MANUAL_REVIEW_REQUIRED")
        self.assertEqual(result["blockers"], [])


if __name__ == "__main__":
    unittest.main()
