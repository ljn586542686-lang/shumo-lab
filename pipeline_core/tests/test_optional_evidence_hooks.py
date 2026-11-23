from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipeline_core.python_tools.optional_evidence_hooks import (
    check_optional_evidence_hooks,
    write_optional_evidence_hooks_report,
)


class OptionalEvidenceHooksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.case_dir = Path(self.temp_dir.name) / "case"
        self.case_dir.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_config(self, **overrides: object) -> None:
        config = {
            "submission_tier": "training",
            "sensitivity_required": False,
            "tournament_required": False,
            "sensitivity_skip_reason": "",
            "tournament_skip_reason": "",
            "sensitivity_hook": {
                "enabled": False,
                "adapter": "",
                "output_dir": "output/sensitivity",
            },
            "tournament_hook": {
                "enabled": False,
                "adapter": "",
                "output_dir": "output/optimizer_arena",
            },
        }
        config.update(overrides)
        (self.case_dir / "case_config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )

    def _hook(self, name: str, **overrides: object) -> dict[str, object]:
        hook: dict[str, object] = {
            "enabled": True,
            "adapter": f"code/python/{name}_hook.py:build_config",
            "output_dir": f"output/{name}",
        }
        hook.update(overrides)
        return hook

    def _write_adapter(self, filename: str, source: str) -> None:
        path = self.case_dir / "code" / "python" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    def _sensitivity_adapter_source(self, expected_output: str = "sensitivity_report.json") -> str:
        return (
            "def build_config(*, case_dir, output_dir):\n"
            "    def model_runner(params):\n"
            "        return float(params['x'])\n"
            "    return {\n"
            "        'kwargs': {\n"
            "            'config': {\n"
            "                'model_runner': model_runner,\n"
            "                'baseline_params': {'x': 10.0},\n"
            "                'parameter_grid': [{\n"
            "                    'parameter_name': 'x',\n"
            "                    'baseline_value': 10.0,\n"
            "                    'perturbation_type': 'grid_value',\n"
            "                    'values': [9.0, 11.0],\n"
            "                    'absolute_or_relative': 'absolute',\n"
            "                    'perturbation_scale': 1.0,\n"
            "                }],\n"
            "                'metric_name': 'fixture_score',\n"
            "            },\n"
            "            'run_id': 'fixture_sensitivity',\n"
            "        },\n"
            f"        'expected_outputs': [{expected_output!r}],\n"
            "    }\n"
        )

    def _tournament_adapter_source(self, expected_output: str = "model_comparison_report.json") -> str:
        return (
            "def build_config(*, case_dir, output_dir):\n"
            "    return {\n"
            "        'kwargs': {\n"
            "            'config': {\n"
            "                'candidate_models': [\n"
            "                    {'model_id': 'a', 'success': True, 'metrics': {'RMSE': 1.0}},\n"
            "                    {'model_id': 'b', 'success': True, 'metrics': {'RMSE': 2.0}},\n"
            "                ],\n"
            "                'metrics': [{'metric_name': 'RMSE', 'direction': 'minimize'}],\n"
            "                'selection_reason': 'tiny fixture comparison',\n"
            "            },\n"
            "            'run_id': 'fixture_tournament',\n"
            "        },\n"
            f"        'expected_outputs': [{expected_output!r}],\n"
            "    }\n"
        )

    def test_default_training_test_and_homework_skip_without_execution(self) -> None:
        for tier in ("training", "test", "homework"):
            with self.subTest(tier=tier):
                self._write_config(submission_tier=tier)
                result = check_optional_evidence_hooks(self.case_dir)
                self.assertEqual(result["status"], "SKIPPED")
                self.assertFalse(result["sensitivity"]["executed"])
                self.assertFalse(result["tournament"]["executed"])
                self.assertFalse(result["final_submit_created"])

    def test_selection_missing_skip_reasons_blocks(self) -> None:
        self._write_config(submission_tier="selection")
        result = check_optional_evidence_hooks(self.case_dir)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("FORMAL_SKIP_REASON_MISSING", result["blockers"])

    def test_formal_missing_skip_reasons_blocks(self) -> None:
        self._write_config(submission_tier="formal")
        result = check_optional_evidence_hooks(self.case_dir)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("FORMAL_SKIP_REASON_MISSING", result["blockers"])

    def test_formal_skip_reasons_allow_skipped_status(self) -> None:
        self._write_config(
            submission_tier="formal",
            sensitivity_skip_reason="not applicable",
            tournament_skip_reason="single-model case",
        )
        result = check_optional_evidence_hooks(self.case_dir)
        self.assertEqual(result["status"], "SKIPPED")

    def test_unknown_tier_requires_manual_review(self) -> None:
        self._write_config(submission_tier="unknown-tier")
        result = check_optional_evidence_hooks(self.case_dir)
        self.assertEqual(result["status"], "MANUAL_REVIEW_REQUIRED")
        self.assertIn("UNKNOWN_SUBMISSION_TIER", result["warnings"])

    def test_sensitivity_required_disabled_blocks(self) -> None:
        self._write_config(sensitivity_required=True)
        result = check_optional_evidence_hooks(self.case_dir, which="sensitivity")
        self.assertIn("SENSITIVITY_HOOK_DISABLED", result["blockers"])

    def test_sensitivity_required_empty_adapter_blocks(self) -> None:
        self._write_config(sensitivity_required=True, sensitivity_hook=self._hook("sensitivity", adapter=""))
        result = check_optional_evidence_hooks(self.case_dir, which="sensitivity")
        self.assertIn("SENSITIVITY_HOOK_ADAPTER_MISSING", result["blockers"])

    def test_sensitivity_absolute_adapter_blocks(self) -> None:
        self._write_config(sensitivity_required=True, sensitivity_hook=self._hook("sensitivity", adapter="C:\\outside.py:build_config"))
        result = check_optional_evidence_hooks(self.case_dir, which="sensitivity")
        self.assertIn("SENSITIVITY_HOOK_ADAPTER_UNSAFE", result["blockers"])

    def test_sensitivity_traversal_adapter_blocks(self) -> None:
        self._write_config(sensitivity_required=True, sensitivity_hook=self._hook("sensitivity", adapter="../outside.py:build_config"))
        result = check_optional_evidence_hooks(self.case_dir, which="sensitivity")
        self.assertIn("SENSITIVITY_HOOK_ADAPTER_UNSAFE", result["blockers"])

    def test_tournament_required_disabled_blocks(self) -> None:
        self._write_config(tournament_required=True)
        result = check_optional_evidence_hooks(self.case_dir, which="tournament")
        self.assertIn("TOURNAMENT_HOOK_DISABLED", result["blockers"])

    def test_tournament_required_empty_adapter_blocks(self) -> None:
        self._write_config(tournament_required=True, tournament_hook=self._hook("tournament", adapter=""))
        result = check_optional_evidence_hooks(self.case_dir, which="tournament")
        self.assertIn("TOURNAMENT_HOOK_ADAPTER_MISSING", result["blockers"])

    def test_tournament_absolute_adapter_blocks(self) -> None:
        self._write_config(tournament_required=True, tournament_hook=self._hook("tournament", adapter="C:\\outside.py:build_config"))
        result = check_optional_evidence_hooks(self.case_dir, which="tournament")
        self.assertIn("TOURNAMENT_HOOK_ADAPTER_UNSAFE", result["blockers"])

    def test_tournament_traversal_adapter_blocks(self) -> None:
        self._write_config(tournament_required=True, tournament_hook=self._hook("tournament", adapter="../outside.py:build_config"))
        result = check_optional_evidence_hooks(self.case_dir, which="tournament")
        self.assertIn("TOURNAMENT_HOOK_ADAPTER_UNSAFE", result["blockers"])

    def test_safe_required_missing_adapter_file_blocks_load(self) -> None:
        self._write_config(sensitivity_required=True, sensitivity_hook=self._hook("sensitivity"))
        result = check_optional_evidence_hooks(self.case_dir, which="sensitivity")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["sensitivity"]["executed"])
        self.assertIn("SENSITIVITY_HOOK_LOAD_FAILED", result["blockers"])

    def test_sensitivity_missing_adapter_function_blocks_load(self) -> None:
        self._write_adapter(
            "sensitivity_hook.py",
            "def other(*, case_dir, output_dir):\n    return {}\n",
        )
        self._write_config(sensitivity_required=True, sensitivity_hook=self._hook("sensitivity"))
        result = check_optional_evidence_hooks(self.case_dir, which="sensitivity")
        self.assertIn("SENSITIVITY_HOOK_LOAD_FAILED", result["blockers"])

    def test_sensitivity_adapter_exception_blocks_execution(self) -> None:
        self._write_adapter(
            "sensitivity_hook.py",
            "def build_config(*, case_dir, output_dir):\n    raise RuntimeError('fixture failure')\n",
        )
        self._write_config(sensitivity_required=True, sensitivity_hook=self._hook("sensitivity"))
        result = check_optional_evidence_hooks(self.case_dir, which="sensitivity")
        self.assertIn("SENSITIVITY_HOOK_EXECUTION_FAILED", result["blockers"])

    def test_sensitivity_executes_existing_math_core_and_writes_evidence(self) -> None:
        self._write_adapter("sensitivity_hook.py", self._sensitivity_adapter_source())
        self._write_config(sensitivity_required=True, sensitivity_hook=self._hook("sensitivity"))
        result = check_optional_evidence_hooks(self.case_dir, which="sensitivity")
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["sensitivity"]["executed"])
        self.assertTrue(Path(result["sensitivity"]["evidence_files"][0]).is_file())
        self.assertFalse(result["final_submit_created"])

    def test_sensitivity_missing_expected_evidence_blocks(self) -> None:
        self._write_adapter(
            "sensitivity_hook.py", self._sensitivity_adapter_source("missing_sensitivity.json")
        )
        self._write_config(sensitivity_required=True, sensitivity_hook=self._hook("sensitivity"))
        result = check_optional_evidence_hooks(self.case_dir, which="sensitivity")
        self.assertIn("SENSITIVITY_REQUIRED_EVIDENCE_MISSING", result["blockers"])

    def test_tournament_missing_adapter_function_blocks_load(self) -> None:
        self._write_adapter(
            "tournament_hook.py",
            "def other(*, case_dir, output_dir):\n    return {}\n",
        )
        self._write_config(tournament_required=True, tournament_hook=self._hook("tournament"))
        result = check_optional_evidence_hooks(self.case_dir, which="tournament")
        self.assertIn("TOURNAMENT_HOOK_LOAD_FAILED", result["blockers"])

    def test_tournament_adapter_exception_blocks_execution(self) -> None:
        self._write_adapter(
            "tournament_hook.py",
            "def build_config(*, case_dir, output_dir):\n    raise RuntimeError('fixture failure')\n",
        )
        self._write_config(tournament_required=True, tournament_hook=self._hook("tournament"))
        result = check_optional_evidence_hooks(self.case_dir, which="tournament")
        self.assertIn("TOURNAMENT_HOOK_EXECUTION_FAILED", result["blockers"])

    def test_tournament_executes_existing_math_core_and_writes_evidence(self) -> None:
        self._write_adapter("tournament_hook.py", self._tournament_adapter_source())
        self._write_config(tournament_required=True, tournament_hook=self._hook("tournament"))
        result = check_optional_evidence_hooks(self.case_dir, which="tournament")
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["tournament"]["executed"])
        self.assertTrue(Path(result["tournament"]["evidence_files"][0]).is_file())

    def test_tournament_missing_expected_evidence_blocks(self) -> None:
        self._write_adapter(
            "tournament_hook.py", self._tournament_adapter_source("missing_tournament.json")
        )
        self._write_config(tournament_required=True, tournament_hook=self._hook("tournament"))
        result = check_optional_evidence_hooks(self.case_dir, which="tournament")
        self.assertIn("TOURNAMENT_REQUIRED_EVIDENCE_MISSING", result["blockers"])

    def test_which_runs_only_requested_hook(self) -> None:
        self._write_adapter("sensitivity_hook.py", self._sensitivity_adapter_source())
        self._write_adapter("tournament_hook.py", self._tournament_adapter_source())
        self._write_config(
            sensitivity_required=True,
            tournament_required=True,
            sensitivity_hook=self._hook("sensitivity"),
            tournament_hook=self._hook("tournament"),
        )
        sensitivity_result = check_optional_evidence_hooks(self.case_dir, which="sensitivity")
        self.assertTrue(sensitivity_result["sensitivity"]["executed"])
        self.assertFalse(sensitivity_result["tournament"]["executed"])
        tournament_result = check_optional_evidence_hooks(self.case_dir, which="tournament")
        self.assertFalse(tournament_result["sensitivity"]["executed"])
        self.assertTrue(tournament_result["tournament"]["executed"])

    def test_report_writer_is_explicit(self) -> None:
        self._write_config()
        result = check_optional_evidence_hooks(self.case_dir)
        validation_dir = self.case_dir / "output" / "validation"
        self.assertFalse(validation_dir.exists())
        json_path, markdown_path = write_optional_evidence_hooks_report(result, self.case_dir)
        self.assertTrue(json_path.is_file())
        self.assertTrue(markdown_path.is_file())
        self.assertFalse(result["final_submit_created"])


if __name__ == "__main__":
    unittest.main()
