from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.stabilization_freeze import evaluate
from validation.xval_independent import validate_xval_independent_check


class XvalIndependentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.case_dir = self.root / "case"
        self.case_dir.mkdir()
        self._write_json("output/result_summary.json", {"objective": 10.0})
        self._write_text("data/input/sample.json", "{\"input\": 1}\n")
        self._write_text("code/primary.py", "# primary fixture\n")
        self._write_text("code/verifier/xval_verify.py", "# verifier fixture\n")
        self._write_json("output/validation/independent_output.json", {"objective": 10.005})

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _path(self, relative: str) -> Path:
        return self.case_dir / relative

    def _write_text(self, relative: str, content: str) -> None:
        path = self._path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _write_json(self, relative: str, payload: object) -> None:
        self._write_text(relative, json.dumps(payload, ensure_ascii=False, indent=2))

    def _sha(self, relative: str) -> str:
        return hashlib.sha256(self._path(relative).read_bytes()).hexdigest()

    def _artifact(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "version": "1.0",
            "case_id": "xval_fixture",
            "submission_tier": "selection",
            "status": "PASS",
            "generated_at": "2026-07-12T00:00:00+00:00",
            "primary_result_path": "output/result_summary.json",
            "primary_result_sha256": self._sha("output/result_summary.json"),
            "primary_method_id": "primary_method",
            "primary_provider_role_id": "primary_provider",
            "independent_method_id": "independent_method",
            "independent_provider_role_id": "independent_provider",
            "independence_claim": "A separate method and provider produced the check output.",
            "independence_strength": "EXACT_SECOND_METHOD",
            "checked_targets": [{
                "target_id": "objective", "target_type": "scalar_objective",
                "primary_value": 10.0, "independent_value": 10.005,
                "tolerance_id": "objective_abs", "comparison_result_id": "objective_compare",
            }],
            "comparison_tolerances": {"objective_abs": 0.01},
            "independent_outputs": [{
                "path": "output/validation/independent_output.json",
                "sha256": self._sha("output/validation/independent_output.json"),
            }],
            "comparison_results": [{
                "comparison_result_id": "objective_compare", "target_id": "objective", "status": "PASS",
                "primary_value": 10.0, "independent_value": 10.005,
                "tolerance_id": "objective_abs", "difference": 0.005,
            }],
            "input_bindings": [{
                "relative_path": "data/input/sample.json", "sha256": self._sha("data/input/sample.json"),
                "role": "primary_input", "required_for_xval_validation": True,
            }],
            "verifier_code_path": "code/verifier/xval_verify.py",
            "verifier_code_sha256": self._sha("code/verifier/xval_verify.py"),
            "blockers": [],
            "warnings": [],
        }
        payload.update(overrides)
        return payload

    def _write_artifact(self, payload: dict[str, object] | None = None) -> None:
        self._write_json("output/validation/xval_independent_check.json", payload or self._artifact())

    def _artifact_with_primary_code(self, **overrides: object) -> dict[str, object]:
        return self._artifact(
            primary_code_path="code/primary.py",
            primary_code_sha256=self._sha("code/primary.py"),
            **overrides,
        )

    def _validate(self, tier: str = "selection", **kwargs: object) -> dict[str, object]:
        return validate_xval_independent_check(self.case_dir, submission_tier=tier, **kwargs)

    def _blocked(self, code: str, **kwargs: object) -> dict[str, object]:
        result = self._validate(**kwargs)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn(code, result["blockers"])
        return result

    def test_selection_and_formal_missing_artifact_block(self) -> None:
        self._blocked("XVAL_MISSING")
        self._blocked("XVAL_MISSING", tier="formal")

    def test_training_missing_artifact_is_non_blocking(self) -> None:
        result = self._validate(tier="training")
        self.assertEqual(result["status"], "MANUAL_REVIEW_REQUIRED")
        self.assertFalse(result["blockers"])
        self.assertIn("XVAL_MISSING", result["warnings"])

    def test_malformed_json_and_statuses_block_selection(self) -> None:
        self._write_text("output/validation/xval_independent_check.json", "{")
        self._blocked("XVAL_MALFORMED")
        self._write_artifact(self._artifact(status="BLOCKED"))
        self._blocked("XVAL_STATUS_BLOCKED")
        self._write_artifact(self._artifact(status="MANUAL_REVIEW_REQUIRED"))
        self._blocked("XVAL_STATUS_MANUAL_REVIEW_REQUIRED")

    def test_valid_pass_artifact_passes(self) -> None:
        self._write_artifact()
        result = self._validate()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["checked_targets_count"], 1)
        self.assertEqual(result["comparison_results_count"], 1)

    def test_same_role_blocks(self) -> None:
        self._write_artifact(self._artifact(independent_provider_role_id="primary_provider"))
        self._blocked("XVAL_ROLE_SEPARATION_FAILED")

    def test_same_method_blocks_and_rerun_is_manual_block_for_selection(self) -> None:
        self._write_artifact(self._artifact(independent_method_id="primary_method"))
        self._blocked("XVAL_METHOD_NOT_INDEPENDENT")
        self._write_artifact(self._artifact(independent_method_id="primary_method", independence_strength="INDEPENDENT_RERUN"))
        result = self._validate()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("XVAL_METHOD_RERUN_ONLY", result["warnings"])
        self.assertIn("XVAL_METHOD_RERUN_ONLY", result["blockers"])
        self.assertTrue(result["rerun_only_manual_required"])
        result = self._validate(tier="training")
        self.assertEqual(result["status"], "PASS")
        self.assertIn("XVAL_METHOD_RERUN_ONLY", result["warnings"])

    def test_manual_review_only_strength_does_not_auto_pass(self) -> None:
        self._write_artifact(self._artifact(independence_strength="MANUAL_REVIEW_ONLY"))
        self._blocked("XVAL_INDEPENDENCE_STRENGTH_INSUFFICIENT")

    def test_missing_targets_or_comparison_for_target_blocks(self) -> None:
        self._write_artifact(self._artifact(checked_targets=[]))
        self._blocked("XVAL_COMPARISON_MISSING")
        payload = self._artifact()
        payload["comparison_results"] = []
        self._write_artifact(payload)
        self._blocked("XVAL_COMPARISON_MISSING")

    def test_scalar_mismatch_and_match_within_tolerance(self) -> None:
        payload = self._artifact()
        payload["checked_targets"][0]["independent_value"] = 10.5
        payload["comparison_results"][0]["independent_value"] = 10.5
        payload["comparison_results"][0]["difference"] = 0.5
        self._write_artifact(payload)
        self._blocked("XVAL_COMPARISON_FAILED")
        self._write_artifact()
        self.assertEqual(self._validate()["status"], "PASS")

    def test_primary_input_verifier_and_independent_output_staleness_block(self) -> None:
        self._write_artifact()
        self._write_json("output/result_summary.json", {"objective": 11.0})
        self._blocked("XVAL_PRIMARY_RESULT_STALE")
        self.setUp_files_after_primary()
        self._write_artifact()
        self._write_text("data/input/sample.json", "changed\n")
        self._blocked("XVAL_INPUT_BINDING_STALE")
        self.setUp_files_after_primary()
        self._write_artifact()
        self._write_text("code/verifier/xval_verify.py", "changed\n")
        self._blocked("XVAL_VERIFIER_CODE_STALE")
        self.setUp_files_after_primary()
        self._write_artifact()
        self._write_json("output/validation/independent_output.json", {"objective": 11.0})
        self._blocked("XVAL_INDEPENDENT_OUTPUT_STALE")

    def setUp_files_after_primary(self) -> None:
        self._write_json("output/result_summary.json", {"objective": 10.0})
        self._write_text("data/input/sample.json", "{\"input\": 1}\n")
        self._write_text("code/primary.py", "# primary fixture\n")
        self._write_text("code/verifier/xval_verify.py", "# verifier fixture\n")
        self._write_json("output/validation/independent_output.json", {"objective": 10.005})

    def test_primary_code_hash_independence_and_staleness(self) -> None:
        valid = self._artifact_with_primary_code()
        self._write_artifact(valid)
        self.assertEqual(self._validate()["status"], "PASS")
        self.assertTrue(self._validate()["hash_independence_checked"])
        self._write_text("code/primary.py", "# verifier fixture\n")
        same_code = self._artifact_with_primary_code()
        self._write_artifact(same_code)
        result = self._blocked("XVAL_VERIFIER_CODE_NOT_INDEPENDENT")
        self.assertTrue(result["verifier_code_not_independent"])
        self.setUp_files_after_primary()
        self._write_artifact(self._artifact_with_primary_code())
        self._write_text("code/primary.py", "changed primary\n")
        self._blocked("XVAL_PRIMARY_CODE_STALE")

    def test_missing_primary_code_hash_warns_without_breaking_existing_artifact(self) -> None:
        self._write_artifact()
        result = self._validate()
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["primary_code_hash_missing"])
        self.assertIn("XVAL_PRIMARY_CODE_HASH_MISSING", result["warnings"])

    def test_independent_output_matching_primary_path_or_hash_blocks(self) -> None:
        same_path = self._artifact()
        same_path["independent_outputs"] = [{
            "path": "output/result_summary.json",
            "sha256": self._sha("output/result_summary.json"),
        }]
        self._write_artifact(same_path)
        result = self._blocked("XVAL_INDEPENDENT_OUTPUT_NOT_INDEPENDENT")
        self.assertTrue(result["independent_output_not_independent"])
        self._write_json("output/validation/independent_output.json", {"objective": 10.0})
        same_hash = self._artifact()
        same_hash["independent_outputs"] = [{
            "path": "output/validation/independent_output.json",
            "sha256": self._sha("output/result_summary.json"),
        }]
        self._write_artifact(same_hash)
        self._blocked("XVAL_INDEPENDENT_OUTPUT_NOT_INDEPENDENT")

    def test_comparison_status_tolerance_target_and_mapping_branches_block(self) -> None:
        blocked = self._artifact()
        blocked["comparison_results"][0]["status"] = "BLOCKED"
        blocked["comparison_results"][0]["blocker"] = "fixture comparison blocked"
        self._write_artifact(blocked)
        self._blocked("XVAL_COMPARISON_FAILED")
        manual = self._artifact()
        manual["comparison_results"][0]["status"] = "MANUAL_REVIEW_REQUIRED"
        self._write_artifact(manual)
        self._blocked("XVAL_COMPARISON_FAILED")
        missing_tolerance = self._artifact()
        missing_tolerance["checked_targets"][0].pop("tolerance_id")
        self._write_artifact(missing_tolerance)
        self._blocked("XVAL_TOLERANCE_MISSING")
        unsupported = self._artifact()
        unsupported["checked_targets"][0]["target_type"] = "unsupported_fixture_type"
        self._write_artifact(unsupported)
        self._blocked("XVAL_UNSUPPORTED_TARGET_TYPE")
        missing_mapping = self._artifact()
        missing_mapping["checked_targets"][0]["comparison_result_id"] = "absent_mapping"
        self._write_artifact(missing_mapping)
        self._blocked("XVAL_COMPARISON_MISSING")

    def test_missing_required_bound_files_block(self) -> None:
        self._write_artifact()
        self._path("data/input/sample.json").unlink()
        self._blocked("XVAL_INPUT_BINDING_STALE")
        self.setUp_files_after_primary()
        self._write_artifact()
        self._path("code/verifier/xval_verify.py").unlink()
        self._blocked("XVAL_VERIFIER_CODE_STALE")
        self.setUp_files_after_primary()
        self._write_artifact()
        self._path("output/validation/independent_output.json").unlink()
        self._blocked("XVAL_INDEPENDENT_OUTPUT_STALE")
        self.setUp_files_after_primary()
        self._write_artifact()
        self._path("output/result_summary.json").unlink()
        self._blocked("XVAL_PRIMARY_RESULT_STALE")

    def test_unsafe_and_malformed_content_binding_block(self) -> None:
        self._write_artifact(self._artifact(primary_result_path="../outside.json"))
        self._blocked("XVAL_UNSAFE_PATH")
        payload = self._artifact()
        payload["input_bindings"] = [{"relative_path": "data/input/sample.json", "sha256": self._sha("data/input/sample.json")}]
        self._write_artifact(payload)
        self._blocked("XVAL_CONTENT_BINDING_MALFORMED")

    def test_explicit_required_makes_training_missing_block_and_disable_is_visible(self) -> None:
        self._blocked("XVAL_MISSING", tier="training", required=True)
        result = self._validate(tier="selection", required=False)
        self.assertEqual(result["status"], "MANUAL_REVIEW_REQUIRED")
        self.assertIn("XVAL_TIER_DEFAULT_EXPLICITLY_DISABLED", result["warnings"])

    def test_no_candidate_or_final_submit_and_freeze_ids_unchanged(self) -> None:
        self._write_artifact()
        self._validate()
        self.assertFalse((self.root / "candidate_submit").exists())
        self.assertFalse((self.root / "final_submit").exists())
        report = evaluate(Path(__file__).resolve().parents[2])
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["registered_ids_current"], report["registered_ids_baseline"])


if __name__ == "__main__":
    unittest.main()
