from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from validation.paper_hard_error_preflight import check_paper_hard_errors


CORRECT_NAME = "\u5218\u6bc5\u7136"
WRONG_NAME_ONE = "\u5218\u806a\u7136"
WRONG_NAME_TWO = "\u5218\u7131\u7136"
FORBIDDEN_EDITION = "\u7b2c\u4e94\u7248"
OBJECTIVE_ANCHOR = "\u76ee\u6807\u51fd\u6570\u6765\u6e90"
CONSTRAINT_ANCHOR = "\u7ea6\u675f\u6765\u6e90"


class PaperHardErrorPreflightTests(unittest.TestCase):
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

    def _write_config(self, **overrides: object) -> None:
        config = {
            "version": "1.0",
            "enabled": True,
            "submission_tier": "training",
            "submitted_text_sources": [{"path": "paper/main.tex", "kind": "tex"}],
            "expected_identity": {"name": "", "class": "", "enabled": False},
            "forbidden_title_terms": [FORBIDDEN_EDITION],
            "objective_source_required": True,
            "constraint_source_required": True,
            "objective_source_anchors": [OBJECTIVE_ANCHOR],
            "constraint_source_anchors": [CONSTRAINT_ANCHOR],
            "code_appendix_required": True,
            "declared_code_files": ["code/solve.py"],
            "appendix_code_files": ["code/solve.py"],
            "appendix_markers": [],
            "allow_passive_provenance_internal_only": True,
        }
        config.update(overrides)
        self._write_json("paper/paper_preflight_config.json", config)

    def _write_passing_fixture(self, **config_overrides: object) -> None:
        source = self.case_dir / "paper" / "main.tex"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            "\\title{Allowed Title}\n"
            "The " + OBJECTIVE_ANCHOR + " is attachment A.\n"
            "The " + CONSTRAINT_ANCHOR + " is attachment B.\n",
            encoding="utf-8",
        )
        code = self.case_dir / "code" / "solve.py"
        code.parent.mkdir(parents=True, exist_ok=True)
        code.write_text("# fixture\n", encoding="utf-8")
        self._write_config(**config_overrides)

    def _replace_source(self, text: str) -> None:
        (self.case_dir / "paper" / "main.tex").write_text(text, encoding="utf-8")

    def test_passing_tex_fixture(self) -> None:
        self._write_passing_fixture()
        result = check_paper_hard_errors(self.case_dir)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["blockers"], [])
        self.assertFalse(result["final_submit_created"])

    def test_missing_config_requires_manual_review(self) -> None:
        result = check_paper_hard_errors(self.case_dir)
        self.assertEqual(result["status"], "MANUAL_REVIEW_REQUIRED")
        self.assertIn("PAPER_PREFLIGHT_CONFIG_MISSING", result["warnings"])

    def test_missing_submitted_source_blocks(self) -> None:
        self._write_config()
        result = check_paper_hard_errors(self.case_dir)
        self.assertIn("SUBMITTED_TEXT_SOURCE_MISSING", result["blockers"])

    def test_forbidden_title_blocks(self) -> None:
        self._write_passing_fixture()
        self._replace_source("\\title{" + FORBIDDEN_EDITION + "}\n" + OBJECTIVE_ANCHOR + "\n" + CONSTRAINT_ANCHOR)
        result = check_paper_hard_errors(self.case_dir)
        self.assertIn("TITLE_FORBIDDEN_EDITION_WORDING", result["blockers"])

    def test_first_wrong_identity_name_blocks(self) -> None:
        self._write_passing_fixture(expected_identity={"name": CORRECT_NAME, "class": "", "enabled": True})
        self._replace_source("\\author{" + WRONG_NAME_ONE + "}\n" + OBJECTIVE_ANCHOR + "\n" + CONSTRAINT_ANCHOR)
        result = check_paper_hard_errors(self.case_dir)
        self.assertIn("IDENTITY_NAME_MISMATCH", result["blockers"])

    def test_second_wrong_identity_name_blocks(self) -> None:
        self._write_passing_fixture(expected_identity={"name": CORRECT_NAME, "class": "", "enabled": True})
        self._replace_source("\\author{" + WRONG_NAME_TWO + "}\n" + OBJECTIVE_ANCHOR + "\n" + CONSTRAINT_ANCHOR)
        result = check_paper_hard_errors(self.case_dir)
        self.assertIn("IDENTITY_NAME_MISMATCH", result["blockers"])

    def test_correct_identity_name_passes(self) -> None:
        self._write_passing_fixture(expected_identity={"name": CORRECT_NAME, "class": "", "enabled": True})
        self._replace_source("\\author{" + CORRECT_NAME + "}\n" + OBJECTIVE_ANCHOR + "\n" + CONSTRAINT_ANCHOR)
        result = check_paper_hard_errors(self.case_dir)
        self.assertNotIn("IDENTITY_NAME_MISMATCH", result["blockers"])

    def test_high_confidence_internal_term_blocks(self) -> None:
        self._write_passing_fixture()
        self._replace_source("\\title{Allowed}\nCodex\n" + OBJECTIVE_ANCHOR + "\n" + CONSTRAINT_ANCHOR)
        result = check_paper_hard_errors(self.case_dir)
        self.assertIn("INTERNAL_WORKFLOW_TERM_FOUND", result["blockers"])

    def test_ambiguous_ai_term_requires_manual_review(self) -> None:
        self._write_passing_fixture()
        self._replace_source("\\title{Allowed}\nAI\n" + OBJECTIVE_ANCHOR + "\n" + CONSTRAINT_ANCHOR)
        result = check_paper_hard_errors(self.case_dir)
        self.assertEqual(result["status"], "MANUAL_REVIEW_REQUIRED")
        self.assertIn("INTERNAL_WORKFLOW_TERM_AMBIGUOUS", result["warnings"])

    def test_training_missing_objective_anchor_requires_manual_review(self) -> None:
        self._write_passing_fixture()
        self._replace_source("\\title{Allowed}\n" + CONSTRAINT_ANCHOR)
        result = check_paper_hard_errors(self.case_dir)
        self.assertEqual(result["status"], "MANUAL_REVIEW_REQUIRED")
        self.assertNotIn("OBJECTIVE_SOURCE_EXPLANATION_MISSING", result["blockers"])
        self.assertIn("OBJECTIVE_SOURCE_EXPLANATION_MISSING", result["warnings"])

    def test_training_missing_constraint_anchor_requires_manual_review(self) -> None:
        self._write_passing_fixture()
        self._replace_source("\\title{Allowed}\n" + OBJECTIVE_ANCHOR)
        result = check_paper_hard_errors(self.case_dir)
        self.assertEqual(result["status"], "MANUAL_REVIEW_REQUIRED")
        self.assertNotIn("CONSTRAINT_SOURCE_EXPLANATION_MISSING", result["blockers"])
        self.assertIn("CONSTRAINT_SOURCE_EXPLANATION_MISSING", result["warnings"])

    def test_training_required_code_appendix_missing_requires_manual_review(self) -> None:
        self._write_passing_fixture(appendix_code_files=[], appendix_markers=[])
        result = check_paper_hard_errors(self.case_dir)
        self.assertEqual(result["status"], "MANUAL_REVIEW_REQUIRED")
        self.assertNotIn("CODE_APPENDIX_REQUIRED_MISSING", result["blockers"])
        self.assertIn("CODE_APPENDIX_REQUIRED_MISSING", result["warnings"])

    def test_test_and_homework_missing_objective_anchor_require_manual_review(self) -> None:
        for tier in ("test", "homework"):
            with self.subTest(tier=tier):
                self._write_passing_fixture(submission_tier=tier)
                self._replace_source("\\title{Allowed}\n" + CONSTRAINT_ANCHOR)
                result = check_paper_hard_errors(self.case_dir)
                self.assertEqual(result["status"], "MANUAL_REVIEW_REQUIRED")
                self.assertIn("OBJECTIVE_SOURCE_EXPLANATION_MISSING", result["warnings"])

    def test_selection_missing_objective_anchor_blocks(self) -> None:
        self._write_passing_fixture(submission_tier="selection")
        self._replace_source("\\title{Allowed}\n" + CONSTRAINT_ANCHOR)
        result = check_paper_hard_errors(self.case_dir)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("OBJECTIVE_SOURCE_EXPLANATION_MISSING", result["blockers"])

    def test_formal_missing_constraint_anchor_blocks(self) -> None:
        self._write_passing_fixture(submission_tier="formal")
        self._replace_source("\\title{Allowed}\n" + OBJECTIVE_ANCHOR)
        result = check_paper_hard_errors(self.case_dir)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("CONSTRAINT_SOURCE_EXPLANATION_MISSING", result["blockers"])

    def test_formal_required_code_appendix_missing_blocks(self) -> None:
        self._write_passing_fixture(
            submission_tier="formal", appendix_code_files=[], appendix_markers=[]
        )
        result = check_paper_hard_errors(self.case_dir)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("CODE_APPENDIX_REQUIRED_MISSING", result["blockers"])

    def test_unknown_submission_tier_requires_manual_review(self) -> None:
        self._write_passing_fixture(submission_tier="unknown-tier")
        result = check_paper_hard_errors(self.case_dir)
        self.assertEqual(result["status"], "MANUAL_REVIEW_REQUIRED")
        self.assertIn("UNKNOWN_SUBMISSION_TIER", result["warnings"])

    def test_missing_appendix_code_file_blocks(self) -> None:
        self._write_passing_fixture(appendix_code_files=["code/missing.py"])
        result = check_paper_hard_errors(self.case_dir)
        self.assertIn("CODE_APPENDIX_OUT_OF_SYNC", result["blockers"])

    def test_deceptive_anti_plagiarism_bait_blocks(self) -> None:
        self._write_passing_fixture()
        self._replace_source("\\title{Allowed}\nanti-plagiarism bait\n" + OBJECTIVE_ANCHOR + "\n" + CONSTRAINT_ANCHOR)
        result = check_paper_hard_errors(self.case_dir)
        self.assertIn("DECEPTIVE_ANTI_PLAGIARISM_TEXT_FOUND", result["blockers"])

    def test_visible_provenance_marker_blocks(self) -> None:
        self._write_passing_fixture()
        self._replace_source("\\title{Allowed}\nprovenance fingerprint\n" + OBJECTIVE_ANCHOR + "\n" + CONSTRAINT_ANCHOR)
        result = check_paper_hard_errors(self.case_dir)
        self.assertIn("PROVENANCE_VISIBLE_MARKER_FORBIDDEN", result["blockers"])

    def test_docx_without_extractor_requires_manual_review(self) -> None:
        self._write_passing_fixture(submitted_text_sources=[{"path": "paper/main.docx", "kind": "docx"}])
        (self.case_dir / "paper" / "main.docx").write_bytes(b"fixture")
        result = check_paper_hard_errors(self.case_dir)
        self.assertEqual(result["status"], "MANUAL_REVIEW_REQUIRED")
        self.assertIn("DOCX_TEXT_EXTRACTION_NOT_AVAILABLE", result["warnings"])


if __name__ == "__main__":
    unittest.main()
