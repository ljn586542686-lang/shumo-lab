from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.stabilization_freeze import evaluate


ALLOWLIST = [
    "scripts/stabilization_freeze.py",
    "pipeline_core/tests/test_stabilization_freeze.py",
    "docs/status/STABILIZATION_FREEZE_POLICY.json",
    "docs/status/STABILIZATION_FREEZE_BASELINE.json",
]
RULE_IDS = ["QG-DATA-01", "QG-EXTRAP-02"]
FEATURE_IDS = ["DATA-01", "EXTRAP-02"]


class StabilizationFreezeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self._write("scripts/stabilization_freeze.py", "# implementation fixture\n")
        self._write("pipeline_core/tests/test_stabilization_freeze.py", "# test fixture\n")
        self._write("run_case.py", "ORIGINAL_RUN_CASE = True\n")
        self._write_core_rules(RULE_IDS)
        self._write_features(FEATURE_IDS)
        self._write_policy()
        self._write_baseline()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def _write_json(self, relative: str, payload: object) -> Path:
        return self._write(relative, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def _write_core_rules(self, ids: list[str]) -> None:
        calls = "\n".join(f'make_result(rule_id="{rule_id}")' for rule_id in ids)
        self._write("quality_gates/core_rules.py", calls + "\n")

    def _write_features(self, ids: list[str]) -> None:
        body = "required_features:\n" + "".join(f'  - feature_id: "{feature_id}"\n' for feature_id in ids)
        self._write("templates/case_FEATURES.yaml", body)

    def _policy_payload(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "version": "1.0",
            "freeze_active": True,
            "phase": "P4-Z0",
            "known_p4_fx_status": "NO_SUITABLE_FIXTURE",
            "known_future_candidate": "cases/case_08_pipeline_integrated_regression_test",
            "known_future_blocker": "runner isolation pending",
            "allowlist": ALLOWLIST,
        }
        payload.update(overrides)
        return payload

    def _write_policy(self, **overrides: object) -> None:
        self._write_json("docs/status/STABILIZATION_FREEZE_POLICY.json", self._policy_payload(**overrides))

    def _baseline_payload(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "version": "1.0",
            "source_commit": "fixture",
            "protected_file_name_patterns": ["*preflight*", "*gate*", "*checker*", "*wrapper*"],
            "allowlist": ALLOWLIST,
            "protected_files_baseline": [],
            "registries": {
                "quality_gate_rule_ids": {
                    "source_path": "quality_gates/core_rules.py",
                    "extractor": "python_ast_rule_id_keywords",
                    "ids": RULE_IDS,
                },
                "feature_namespace_ids": {
                    "source_path": "templates/case_FEATURES.yaml",
                    "extractor": "literal_yaml_feature_ids",
                    "ids": FEATURE_IDS,
                },
            },
            "limitations": ["Fixture baseline."],
        }
        payload.update(overrides)
        return payload

    def _write_baseline(self, **overrides: object) -> None:
        self._write_json("docs/status/STABILIZATION_FREEZE_BASELINE.json", self._baseline_payload(**overrides))

    def test_clean_baseline_passes(self) -> None:
        result = evaluate(self.root)
        self.assertEqual(result["status"], "PASS")

    def test_missing_policy_fails_closed(self) -> None:
        (self.root / "docs/status/STABILIZATION_FREEZE_POLICY.json").unlink()
        result = evaluate(self.root)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(result["freeze_active"])

    def test_malformed_policy_fails_closed(self) -> None:
        self._write("docs/status/STABILIZATION_FREEZE_POLICY.json", "{")
        self.assertEqual(evaluate(self.root)["status"], "FAIL")

    def test_missing_baseline_fails(self) -> None:
        (self.root / "docs/status/STABILIZATION_FREEZE_BASELINE.json").unlink()
        self.assertEqual(evaluate(self.root)["status"], "FAIL")

    def test_malformed_baseline_fails(self) -> None:
        self._write("docs/status/STABILIZATION_FREEZE_BASELINE.json", "[]")
        self.assertEqual(evaluate(self.root)["status"], "FAIL")

    def test_unknown_freeze_active_fails_closed(self) -> None:
        self._write_policy(freeze_active="yes")
        result = evaluate(self.root)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(result["freeze_active"])

    def test_new_dummy_gate_fails(self) -> None:
        self._write("pipeline_core/dummy_gate.py", "# red team\n")
        result = evaluate(self.root)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("pipeline_core/dummy_gate.py", result["protected_file_violations"])

    def test_new_dummy_checker_fails(self) -> None:
        self._write("scratch/dummy_checker.py", "# red team\n")
        result = evaluate(self.root)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("scratch/dummy_checker.py", result["protected_file_violations"])

    def test_case_and_runtime_gate_files_do_not_pollute_production_freeze(self) -> None:
        self._write("cases/new_case/human_review/gate_1_problem_understanding.md", "pending\n")
        self._write("work_tmp/run_1/dummy_gate.py", "runtime\n")
        result = evaluate(self.root)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["protected_file_violations"], [])

    def test_implementation_files_are_allowed(self) -> None:
        result = evaluate(self.root)
        self.assertEqual(result["allowlist"], sorted(ALLOWLIST))
        self.assertEqual(result["allowlist_paths_present"], sorted(ALLOWLIST))

    def test_added_registered_rule_id_fails(self) -> None:
        self._write_core_rules(RULE_IDS + ["QG-FAKE-99"])
        result = evaluate(self.root)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["baseline_diff"]["registries"]["quality_gate_rule_ids"]["added"], ["QG-FAKE-99"])

    def test_removed_registered_rule_id_fails(self) -> None:
        self._write_core_rules(RULE_IDS[:-1])
        result = evaluate(self.root)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["baseline_diff"]["registries"]["quality_gate_rule_ids"]["removed"], [RULE_IDS[-1]])

    def test_allowlist_contents_are_reported(self) -> None:
        result = evaluate(self.root)
        self.assertEqual(result["allowlist"], sorted(ALLOWLIST))
        self.assertEqual(result["allowlist_entries_not_in_baseline"], [])
        self.assertEqual(result["baseline_allowlist_entries_not_in_policy"], [])

    def test_final_submit_created_remains_false(self) -> None:
        result = evaluate(self.root)
        self.assertFalse(result["final_submit_created"])
        self.assertFalse((self.root / "final_submit").exists())

    def test_p4_fx_context_does_not_change_enforcement(self) -> None:
        self._write_policy(
            known_p4_fx_status="NO_SUITABLE_FIXTURE",
            known_future_candidate="cases/case_08_pipeline_integrated_regression_test",
            known_future_blocker="run_case isolation/existing-summary smoke mode not yet implemented",
        )
        self.assertEqual(evaluate(self.root)["status"], "PASS")

    def test_run_case_is_not_required_or_modified(self) -> None:
        run_case = self.root / "run_case.py"
        before = run_case.read_bytes()
        run_case.unlink()
        self.assertEqual(evaluate(self.root)["status"], "PASS")
        self.assertFalse(run_case.exists())
        run_case.write_bytes(before)
        self.assertEqual(run_case.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
