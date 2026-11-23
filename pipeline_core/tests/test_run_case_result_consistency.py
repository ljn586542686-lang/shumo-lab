from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run_case as run_case_module


class RunCaseResultConsistencyTests(unittest.TestCase):
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
            "result_consistency_enabled": False,
            "result_consistency_required": False,
            "result_consistency_manifest_path": "output/validation/result_consistency_manifest.json",
        }
        config.update(overrides)
        return config

    def _write_json(self, root: Path, relative_path: str, payload: dict[str, object]) -> None:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _write_result(self, value: float = 1234.567, **overrides: object) -> None:
        payload: dict[str, object] = {
            "case_id": "fixture_case",
            "status": "ok",
            "validation_status": "PASS",
            "fatal_items": [],
            "metrics": {"objective_value": value},
        }
        payload.update(overrides)
        self._write_json(self.run_dir, "output/result_summary.json", payload)

    def _write_manifest(self) -> None:
        manifest: dict[str, object] = {
            "version": "1.0",
            "case_id": "fixture_case",
            "canonical_result_summary": "output/result_summary.json",
            "reported_values": [{
                "value_id": "objective_value",
                "json_pointer": "/metrics/objective_value",
                "unit": "units",
                "display_precision": 2,
            }],
            "text_sources": [{"source_id": "main_tex", "path": "paper/main.tex", "kind": "tex"}],
            "anchors": [{
                "anchor_id": "abstract_objective",
                "source_id": "main_tex",
                "value_id": "objective_value",
                "required": True,
            }],
            "producer_scripts": ["code/solve.py"],
            "code_output_paths": ["output/result_summary.json"],
            "appendix_code_paths": ["code/solve.py"],
            "uniqueness_claims": [],
        }
        self._write_json(
            self.case_dir,
            "output/validation/result_consistency_manifest.json",
            manifest,
        )

    def _write_case_sources(self, rendered_value: str = "1,234.57") -> None:
        paper = self.case_dir / "paper" / "main.tex"
        paper.parent.mkdir(parents=True, exist_ok=True)
        paper.write_text(f"The objective value is {rendered_value}.", encoding="utf-8")
        code = self.case_dir / "code" / "solve.py"
        code.parent.mkdir(parents=True, exist_ok=True)
        code.write_text("# fixture producer\n", encoding="utf-8")

    def _write_passing_fixture(self) -> None:
        self._write_result()
        self._write_manifest()
        self._write_case_sources()

    def _run(self, config: dict[str, object]) -> dict[str, object] | None:
        return run_case_module._run_result_consistency_if_configured(
            case_dir=self.case_dir,
            run_dir=self.run_dir,
            case_config=config,
            risks=self.risks,
        )

    def test_default_disabled_config_does_not_call_or_write_report(self) -> None:
        with mock.patch(
            "validation.result_consistency_contract.check_result_consistency"
        ) as check:
            result = self._run(self._config())
        self.assertIsNone(result)
        check.assert_not_called()
        self.assertFalse((self.run_dir / "output" / "validation" / "result_consistency_report.json").exists())
        self.assertFalse((self.root / "final_submit").exists())

    def test_enabled_matching_fixture_passes_and_uses_staged_result(self) -> None:
        self._write_passing_fixture()
        result = self._run(self._config(result_consistency_enabled=True))
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(self.risks, [])
        self.assertTrue((self.run_dir / "output" / "validation" / "result_consistency_report.json").is_file())
        self.assertFalse((self.case_dir / "output" / "result_summary.json").exists())

    def test_required_missing_manifest_adds_fatal_risk(self) -> None:
        self._write_result()
        result = self._run(self._config(result_consistency_required=True))
        self.assertIn("RESULT_CONTRACT_MANIFEST_MISSING", result["blockers"])
        self.assertTrue(self.risks[-1]["fatal"])

    def test_required_missing_result_summary_adds_fatal_risk(self) -> None:
        self._write_manifest()
        self._write_case_sources()
        result = self._run(self._config(result_consistency_required=True))
        self.assertIn("RESULT_SUMMARY_MISSING", result["blockers"])
        self.assertTrue(self.risks[-1]["fatal"])

    def test_value_mismatch_adds_fatal_risk(self) -> None:
        self._write_result()
        self._write_manifest()
        self._write_case_sources("1,230.00")
        result = self._run(self._config(result_consistency_enabled=True, result_consistency_required=True))
        self.assertIn("RESULT_VALUE_MISMATCH", result["blockers"])
        self.assertTrue(self.risks[-1]["fatal"])

    def test_fatal_result_summary_adds_fatal_risk(self) -> None:
        self._write_passing_fixture()
        self._write_result(status="fatal", validation_status="FATAL", fatal_items=[{"code": "fixture"}])
        result = self._run(self._config(result_consistency_required=True))
        self.assertIn("RESULT_SUMMARY_FATAL_STATUS", result["blockers"])
        self.assertTrue(self.risks[-1]["fatal"])

    def test_manual_review_is_explicit_nonfatal_risk(self) -> None:
        report = {
            "status": "MANUAL_REVIEW_REQUIRED",
            "checks": [],
            "blockers": [],
            "warnings": ["RESULT_CONSISTENCY_MANUAL_REVIEW_REQUIRED"],
            "manual_review_items": ["PDF source requires review"],
            "canonical_result_summary": "output/result_summary.json",
            "manifest_path": "fixture",
            "final_submit_created": False,
        }
        with mock.patch(
            "validation.result_consistency_contract.check_result_consistency",
            return_value=report,
        ):
            result = self._run(self._config(result_consistency_enabled=True))
        self.assertEqual(result["status"], "MANUAL_REVIEW_REQUIRED")
        self.assertFalse(self.risks[-1]["fatal"])
        self.assertIn("RESULT_CONSISTENCY_MANUAL_REVIEW_REQUIRED", str(self.risks[-1]["message"]))

    def test_staged_report_and_no_final_submit(self) -> None:
        self._write_passing_fixture()
        self._run(self._config(result_consistency_required=True))
        self.assertTrue((self.run_dir / "output" / "validation" / "result_consistency_report.md").is_file())
        self.assertFalse((self.root / "final_submit").exists())


if __name__ == "__main__":
    unittest.main()
