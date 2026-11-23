from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import run_case as run_case_module
from scripts.stabilization_freeze import evaluate


class RunCaseXvalValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.case_dir = self.root / "case"
        self.case_dir.mkdir()
        self._write_selection_prerequisites()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _write_selection_prerequisites(self, *, tier: str = "selection", **config_overrides: object) -> None:
        summary = {
            "schema_version": "xval_run_case_fixture_v1",
            "generated_at": "2026-07-12T00:00:00+00:00",
            "run_id": "xval_fixture_run",
            "case_id": "xval_fixture",
            "status": "ok",
            "metrics": {"objective_value": 1234.5},
            "final_submit_created": False,
        }
        self._write_json(self.case_dir / "output" / "result_summary.json", summary)
        self._write_json(
            self.case_dir / "case_config.json",
            {"case_id": "xval_fixture", "submission_tier": tier, **config_overrides},
        )
        self._write_json(
            self.case_dir / "output" / "validation" / "result_consistency_manifest.json",
            {
                "version": "1.0",
                "case_id": "xval_fixture",
                "reported_values": [{
                    "value_id": "objective_value",
                    "json_pointer": "/metrics/objective_value",
                    "unit": "",
                    "display_precision": 1,
                }],
                "text_sources": [{"source_id": "main", "path": "paper/main.md", "kind": "md"}],
                "anchors": [{
                    "anchor_id": "objective",
                    "source_id": "main",
                    "value_id": "objective_value",
                    "required": True,
                }],
                "producer_scripts": [],
                "code_output_paths": ["output/result_summary.json"],
                "appendix_code_paths": [],
                "uniqueness_claims": [],
            },
        )
        paper = self.case_dir / "paper" / "main.md"
        paper.parent.mkdir(parents=True, exist_ok=True)
        paper.write_text("The objective value is 1234.5.\n", encoding="utf-8")
        self._write_json(
            self.case_dir / "paper" / "paper_preflight_config.json",
            {
                "version": "1.0",
                "submitted_text_sources": [{"path": "paper/main.md", "kind": "md"}],
                "expected_identity": {"name": "", "class": "", "enabled": False},
                "forbidden_title_terms": [],
                "objective_source_required": False,
                "constraint_source_required": False,
                "code_appendix_required": False,
                "declared_code_files": [],
                "appendix_code_files": [],
                "appendix_markers": [],
                "allow_passive_provenance_internal_only": True,
            },
        )

    def _write_valid_xval(self, **overrides: object) -> Path:
        primary_code = self.case_dir / "code" / "primary.py"
        verifier_code = self.case_dir / "code" / "verifier.py"
        independent_output = self.case_dir / "output" / "validation" / "independent_output.json"
        primary_code.parent.mkdir(parents=True, exist_ok=True)
        primary_code.write_text("# primary fixture\n", encoding="utf-8")
        verifier_code.write_text("# independent verifier fixture\n", encoding="utf-8")
        self._write_json(independent_output, {"objective_value": 1234.5, "source": "independent"})
        result_path = self.case_dir / "output" / "result_summary.json"
        payload: dict[str, object] = {
            "version": "1.0",
            "case_id": "xval_fixture",
            "submission_tier": "selection",
            "status": "PASS",
            "generated_at": "2026-07-12T00:01:00+00:00",
            "primary_result_path": "output/result_summary.json",
            "primary_result_sha256": self._sha(result_path),
            "primary_code_path": "code/primary.py",
            "primary_code_sha256": self._sha(primary_code),
            "primary_method_id": "primary_method",
            "primary_provider_role_id": "primary_provider",
            "independent_method_id": "independent_method",
            "independent_provider_role_id": "independent_provider",
            "independence_claim": "A separately implemented scalar comparison was reviewed.",
            "independence_strength": "EXACT_SECOND_METHOD",
            "checked_targets": [{
                "target_id": "objective",
                "target_type": "scalar_objective",
                "primary_value": 1234.5,
                "independent_value": 1234.5,
                "tolerance": 0.0,
                "comparison_result_id": "objective_compare",
            }],
            "comparison_tolerances": {"absolute": 0.0},
            "independent_outputs": [{"path": "output/validation/independent_output.json", "sha256": self._sha(independent_output)}],
            "comparison_results": [{
                "comparison_result_id": "objective_compare",
                "target_id": "objective",
                "status": "PASS",
                "primary_value": 1234.5,
                "independent_value": 1234.5,
                "tolerance": 0.0,
                "difference": 0.0,
            }],
            "input_bindings": [],
            "verifier_code_path": "code/verifier.py",
            "verifier_code_sha256": self._sha(verifier_code),
            "blockers": [],
            "warnings": [],
        }
        payload.update(overrides)
        path = self.case_dir / "output" / "validation" / "xval_independent_check.json"
        self._write_json(path, payload)
        return path

    def _run(self, label: str) -> tuple[dict[str, object], Path]:
        run_dir = self.root / label
        result = run_case_module.run_case("run", self.case_dir, 5, isolated_smoke=True, run_dir=run_dir)
        return result, run_dir

    def _risk(self, run_dir: Path) -> dict[str, object]:
        return json.loads((run_dir / "risk_summary.json").read_text(encoding="utf-8"))

    def test_selection_missing_xval_blocks(self) -> None:
        result, run_dir = self._run("selection_missing")
        self.assertEqual(result["risk_status"], "BLOCK")
        self.assertEqual(result["xval_status"], "BLOCKED")
        self.assertIn("XVAL_MISSING", result["blockers"])
        self.assertTrue(self._risk(run_dir)["xval_independent"]["fatal_count_contribution"])

    def test_formal_missing_xval_blocks(self) -> None:
        self._write_selection_prerequisites(tier="formal")
        result, _ = self._run("formal_missing")
        self.assertEqual(result["risk_status"], "BLOCK")
        self.assertIn("XVAL_MISSING", result["blockers"])

    def test_selection_malformed_xval_blocks(self) -> None:
        artifact = self.case_dir / "output" / "validation" / "xval_independent_check.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("{not-json", encoding="utf-8")
        result, _ = self._run("selection_malformed")
        self.assertEqual(result["risk_status"], "BLOCK")
        self.assertIn("XVAL_MALFORMED", result["blockers"])

    def test_training_missing_xval_is_nonfatal_by_default(self) -> None:
        self._write_selection_prerequisites(tier="training")
        result, run_dir = self._run("training_missing")
        self.assertEqual(result["risk_status"], "PASS")
        self.assertEqual(result["xval_status"], "SKIPPED")
        self.assertFalse(self._risk(run_dir)["xval_independent"]["xval_required"])

    def test_valid_selection_xval_passes_and_is_written_to_outputs(self) -> None:
        artifact = self._write_valid_xval()
        result, run_dir = self._run("selection_valid")
        self.assertEqual(result["risk_status"], "PASS")
        self.assertEqual(result["xval_status"], "PASS")
        self.assertEqual(result["checked_targets_count"], 1)
        risk = self._risk(run_dir)
        self.assertEqual(risk["xval_independent"]["xval_status"], "PASS")
        binding = next(item for item in result["upstream_content_bindings"] if item["logical_name"] == "xval_independent_check")
        self.assertEqual(binding["relative_path"], "output/validation/xval_independent_check.json")
        self.assertEqual(binding["sha256"], self._sha(artifact))
        self.assertTrue(binding["required_for_candidate_validation"])

    def test_current_run_summary_hash_must_match_xval_primary_binding(self) -> None:
        self._write_valid_xval()
        from validation.xval_independent import validate_xval_independent_check

        report = validate_xval_independent_check(self.case_dir, required=True, submission_tier="selection")
        staged = self.root / "staged_result_summary.json"
        staged.write_text("{\"different\": true}\n", encoding="utf-8")
        run_case_module._bind_xval_to_current_result_summary(
            case_dir=self.case_dir,
            current_result_summary_path=staged,
            report=report,
            required=True,
        )
        self.assertEqual(report["status"], "BLOCKED")
        self.assertIn("XVAL_PRIMARY_RESULT_STALE", report["blockers"])

    def test_blocked_manual_and_scalar_comparison_failure_block_selection(self) -> None:
        for label, overrides in (
            ("artifact_blocked", {"status": "BLOCKED"}),
            ("artifact_manual", {"status": "MANUAL_REVIEW_REQUIRED"}),
            ("comparison_failure", {"comparison_results": [{
                "comparison_result_id": "objective_compare", "target_id": "objective", "status": "BLOCKED",
                "primary_value": 1234.5, "independent_value": 1200.0, "tolerance": 0.0,
                "difference": 34.5, "blocker": "fixture mismatch",
            }]}),
        ):
            with self.subTest(label=label):
                self._write_valid_xval(**overrides)
                result, _ = self._run(label)
                self.assertEqual(result["risk_status"], "BLOCK")
                self.assertEqual(result["xval_status"], "BLOCKED")

    def test_stale_unsafe_role_and_rerun_failures_block_selection(self) -> None:
        cases = (
            ("stale_primary", {"primary_result_sha256": "0" * 64}, "XVAL_PRIMARY_RESULT_STALE"),
            ("unsafe_path", {"primary_result_path": "../unsafe.json"}, "XVAL_UNSAFE_PATH"),
            ("same_role", {"independent_provider_role_id": "primary_provider"}, "XVAL_ROLE_SEPARATION_FAILED"),
            ("rerun_only", {
                "primary_method_id": "same_method", "independent_method_id": "same_method",
                "independence_strength": "INDEPENDENT_RERUN",
            }, "XVAL_METHOD_RERUN_ONLY"),
        )
        for label, overrides, expected in cases:
            with self.subTest(label=label):
                self._write_valid_xval(**overrides)
                result, _ = self._run(label)
                self.assertEqual(result["risk_status"], "BLOCK")
                self.assertIn(expected, result["blockers"])

    def test_training_explicit_required_missing_xval_blocks(self) -> None:
        self._write_selection_prerequisites(tier="training", xval_independent_required=True)
        result, _ = self._run("training_required_missing")
        self.assertEqual(result["risk_status"], "BLOCK")
        self.assertTrue(result["xval_required"])
        self.assertIn("XVAL_MISSING", result["blockers"])

    def test_selection_explicit_disable_is_visible(self) -> None:
        self._write_selection_prerequisites(
            xval_independent_enabled=False,
            xval_independent_required=False,
        )
        result, run_dir = self._run("selection_explicit_disable")
        self.assertEqual(result["risk_status"], "PASS")
        self.assertEqual(result["xval_status"], "SKIPPED")
        risk = self._risk(run_dir)
        self.assertTrue(any("TIER_DEFAULT_EXPLICITLY_DISABLED: xval_independent" in str(item["message"]) for item in risk["risks"]))

    def test_isolated_xval_wiring_never_creates_candidate_or_final_submit(self) -> None:
        self._write_valid_xval()
        result, _ = self._run("no_candidate_or_final")
        self.assertFalse(result["candidate_package_created"])
        self.assertFalse(result["final_submit_created"])
        self.assertFalse((self.root / "candidate_packages").exists())
        self.assertFalse((self.root / "final_submit").exists())

    def test_freeze_tripwire_and_registered_ids_remain_unchanged(self) -> None:
        report = evaluate(run_case_module.ROOT)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["registered_ids_current"], report["registered_ids_baseline"])


if __name__ == "__main__":
    unittest.main()
