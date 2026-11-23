from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run_case as run_case_module


FORBIDDEN_EDITION = "\u7b2c\u4e94\u7248"
OBJECTIVE_ANCHOR = "\u76ee\u6807\u51fd\u6570\u6765\u6e90"
CONSTRAINT_ANCHOR = "\u7ea6\u675f\u6765\u6e90"


class RunCasePaperPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.case_dir = self.root / "case"
        self.run_dir = self.root / "run"
        self.case_dir.mkdir()
        self.run_dir.mkdir()
        self.risks: list[dict[str, object]] = []

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_config(self, **overrides: object) -> None:
        config: dict[str, object] = {
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
            "code_appendix_required": False,
            "declared_code_files": [],
            "appendix_code_files": [],
            "appendix_markers": [],
            "allow_passive_provenance_internal_only": True,
        }
        config.update(overrides)
        path = self.case_dir / "paper" / "paper_preflight_config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config), encoding="utf-8")

    def _write_source(self, body: str | None = None, title: str = "Allowed Title") -> None:
        if body is None:
            body = f"{OBJECTIVE_ANCHOR} is attachment A.\n{CONSTRAINT_ANCHOR} is attachment B."
        path = self.case_dir / "paper" / "main.tex"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"\\title{{{title}}}\n{body}\n", encoding="utf-8")

    def _run(self) -> dict[str, object] | None:
        return run_case_module._run_paper_preflight_if_enabled(
            case_dir=self.case_dir,
            run_dir=self.run_dir,
            risks=self.risks,
        )

    def test_disabled_config_does_not_call_or_write_report(self) -> None:
        self._write_config(enabled=False)
        with mock.patch("validation.paper_hard_error_preflight.check_paper_hard_errors") as check:
            result = self._run()
        self.assertIsNone(result)
        check.assert_not_called()
        self.assertFalse((self.run_dir / "output" / "validation" / "paper_hard_error_report.json").exists())
        self.assertFalse((self.root / "final_submit").exists())

    def test_enabled_clean_training_paper_passes(self) -> None:
        self._write_config()
        self._write_source()
        result = self._run()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(self.risks, [])
        self.assertTrue((self.run_dir / "output" / "validation" / "paper_hard_error_report.json").is_file())

    def test_training_missing_anchors_records_nonfatal_warning(self) -> None:
        self._write_config()
        self._write_source(body="No source anchors are present.")
        result = self._run()
        self.assertEqual(result["status"], "MANUAL_REVIEW_REQUIRED")
        self.assertFalse(self.risks[-1]["fatal"])
        self.assertIn("OBJECTIVE_SOURCE_EXPLANATION_MISSING", str(self.risks[-1]["message"]))

    def test_formal_missing_anchors_adds_fatal_risk(self) -> None:
        self._write_config(submission_tier="formal")
        self._write_source(body="No source anchors are present.")
        result = self._run()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(self.risks[-1]["fatal"])

    def test_internal_workflow_term_blocks(self) -> None:
        self._write_config()
        self._write_source(body=f"Codex\n{OBJECTIVE_ANCHOR}\n{CONSTRAINT_ANCHOR}")
        result = self._run()
        self.assertIn("INTERNAL_WORKFLOW_TERM_FOUND", result["blockers"])
        self.assertTrue(self.risks[-1]["fatal"])

    def test_forbidden_title_term_blocks(self) -> None:
        self._write_config()
        self._write_source(title=FORBIDDEN_EDITION)
        result = self._run()
        self.assertIn("TITLE_FORBIDDEN_EDITION_WORDING", result["blockers"])
        self.assertTrue(self.risks[-1]["fatal"])

    def test_manual_review_result_is_not_silently_passed(self) -> None:
        self._write_config(submission_tier="selection")
        report = {
            "status": "MANUAL_REVIEW_REQUIRED",
            "checks": [],
            "blockers": [],
            "warnings": ["PAPER_PREFLIGHT_MANUAL_REVIEW_REQUIRED"],
            "manual_review_items": ["fixture review"],
            "submission_tier": "selection",
            "scanned_sources": [],
            "skipped_sources": [],
            "final_submit_created": False,
        }
        with mock.patch(
            "validation.paper_hard_error_preflight.check_paper_hard_errors",
            return_value=report,
        ):
            result = self._run()
        self.assertEqual(result["status"], "MANUAL_REVIEW_REQUIRED")
        self.assertTrue(self.risks[-1]["fatal"])

    def test_blocked_result_is_explicit_fatal_risk(self) -> None:
        self._write_config()
        report = {
            "status": "BLOCKED",
            "checks": [],
            "blockers": ["SUBMITTED_TEXT_SOURCE_MISSING"],
            "warnings": [],
            "manual_review_items": [],
            "submission_tier": "training",
            "scanned_sources": [],
            "skipped_sources": [],
            "final_submit_created": False,
        }
        with mock.patch(
            "validation.paper_hard_error_preflight.check_paper_hard_errors",
            return_value=report,
        ):
            self._run()
        self.assertTrue(self.risks[-1]["fatal"])
        self.assertIn("SUBMITTED_TEXT_SOURCE_MISSING", str(self.risks[-1]["message"]))

    def test_staged_report_and_no_final_submit(self) -> None:
        self._write_config()
        self._write_source()
        self._run()
        self.assertTrue((self.run_dir / "output" / "validation" / "paper_hard_error_report.md").is_file())
        self.assertFalse((self.root / "final_submit").exists())


if __name__ == "__main__":
    unittest.main()
