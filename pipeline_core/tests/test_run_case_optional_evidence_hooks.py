from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run_case as run_case_module


class RunCaseOptionalEvidenceHooksTests(unittest.TestCase):
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

    @staticmethod
    def _config(**overrides: object) -> dict[str, object]:
        config: dict[str, object] = {
            "submission_tier": "training",
            "sensitivity_required": False,
            "tournament_required": False,
            "sensitivity_hook": {"enabled": False},
            "tournament_hook": {"enabled": False},
        }
        config.update(overrides)
        return config

    @staticmethod
    def _report(status: str = "PASS", tier: str = "training", **overrides: object) -> dict[str, object]:
        report: dict[str, object] = {
            "status": status,
            "checks": [],
            "blockers": [],
            "warnings": [],
            "manual_review_items": [],
            "submission_tier": tier,
            "sensitivity": {"executed": status == "PASS"},
            "tournament": {"executed": False},
            "final_submit_created": False,
        }
        report.update(overrides)
        return report

    def _run(self, config: dict[str, object], report: dict[str, object]) -> tuple[dict[str, object] | None, mock.Mock]:
        with mock.patch(
            "pipeline_core.python_tools.optional_evidence_hooks.check_optional_evidence_hooks",
            return_value=report,
        ) as check:
            result = run_case_module._run_optional_evidence_hooks_if_configured(
                case_dir=self.case_dir,
                run_dir=self.run_dir,
                case_config=config,
                risks=self.risks,
            )
        return result, check

    def test_default_disabled_config_does_not_call_or_write_report(self) -> None:
        with mock.patch(
            "pipeline_core.python_tools.optional_evidence_hooks.check_optional_evidence_hooks"
        ) as check:
            result = run_case_module._run_optional_evidence_hooks_if_configured(
                case_dir=self.case_dir,
                run_dir=self.run_dir,
                case_config=self._config(),
                risks=self.risks,
            )
        self.assertIsNone(result)
        check.assert_not_called()
        self.assertFalse((self.run_dir / "output" / "validation" / "optional_evidence_hooks_report.json").exists())
        self.assertFalse((self.root / "final_submit").exists())

    def test_sensitivity_required_calls_hook_and_pass_continues(self) -> None:
        result, check = self._run(self._config(sensitivity_required=True), self._report())
        self.assertEqual(result["status"], "PASS")
        check.assert_called_once_with(self.case_dir, output_root=self.run_dir)
        self.assertEqual(self.risks, [])
        self.assertTrue((self.run_dir / "output" / "validation" / "optional_evidence_hooks_report.json").is_file())

    def test_tournament_required_calls_hook(self) -> None:
        result, check = self._run(self._config(tournament_required=True), self._report())
        self.assertEqual(result["status"], "PASS")
        check.assert_called_once()

    def test_enabled_optional_sensitivity_calls_hook(self) -> None:
        config = self._config(sensitivity_hook={"enabled": True})
        result, check = self._run(config, self._report())
        self.assertEqual(result["status"], "PASS")
        check.assert_called_once()

    def test_blocked_hook_adds_fatal_risk(self) -> None:
        report = self._report("BLOCKED", blockers=["SENSITIVITY_HOOK_EXECUTION_FAILED"])
        self._run(self._config(sensitivity_required=True), report)
        self.assertTrue(self.risks[0]["fatal"])
        self.assertIn("SENSITIVITY_HOOK_EXECUTION_FAILED", str(self.risks[0]["message"]))

    def test_manual_review_warns_for_training_and_blocks_formal(self) -> None:
        training_report = self._report(
            "MANUAL_REVIEW_REQUIRED",
            warnings=["OPTIONAL_EVIDENCE_HOOK_MANUAL_REVIEW_REQUIRED"],
        )
        self._run(self._config(sensitivity_hook={"enabled": True}), training_report)
        self.assertFalse(self.risks[-1]["fatal"])

        self.risks.clear()
        formal_report = self._report(
            "MANUAL_REVIEW_REQUIRED",
            tier="formal",
            warnings=["OPTIONAL_EVIDENCE_HOOK_MANUAL_REVIEW_REQUIRED"],
        )
        self._run(
            self._config(submission_tier="formal", sensitivity_hook={"enabled": True}),
            formal_report,
        )
        self.assertTrue(self.risks[-1]["fatal"])

    def test_unknown_submission_tier_is_explicit_warning(self) -> None:
        report = self._report(
            "MANUAL_REVIEW_REQUIRED",
            tier="unknown-tier",
            warnings=["UNKNOWN_SUBMISSION_TIER"],
        )
        self._run(
            self._config(submission_tier="unknown-tier", tournament_hook={"enabled": True}),
            report,
        )
        self.assertFalse(self.risks[-1]["fatal"])
        self.assertIn("UNKNOWN_SUBMISSION_TIER", str(self.risks[-1]["message"]))

    def test_reports_are_staged_and_final_submit_remains_absent(self) -> None:
        self._run(self._config(tournament_required=True), self._report())
        self.assertTrue((self.run_dir / "output" / "validation" / "optional_evidence_hooks_report.md").is_file())
        self.assertFalse((self.root / "final_submit").exists())


if __name__ == "__main__":
    unittest.main()
