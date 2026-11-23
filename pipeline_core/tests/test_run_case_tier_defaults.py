from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import run_case as run_case_module


class RunCaseTierDefaultsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.case_dir = self.root / "case"
        self.case_dir.mkdir()
        self.risks: list[dict[str, object]] = []

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _effective(self, config: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
        return run_case_module._effective_run_gate_settings(self.case_dir, config, self.risks)

    def _write_result_fixture(self, *, rendered_value: str = "1234.5") -> None:
        self._write_json(
            self.case_dir / "output" / "result_summary.json",
            {
                "schema_version": "tier_fixture_v1",
                "generated_at": "2026-07-11T00:00:00+00:00",
                "run_id": "trusted_fixture_run",
                "case_id": "tier_fixture",
                "status": "ok",
                "metrics": {"objective_value": 1234.5},
                "final_submit_created": False,
            },
        )
        self._write_json(
            self.case_dir / "output" / "validation" / "result_consistency_manifest.json",
            {
                "version": "1.0",
                "case_id": "tier_fixture",
                "reported_values": [{
                    "value_id": "objective_value",
                    "json_pointer": "/metrics/objective_value",
                    "unit": "",
                    "display_precision": 1,
                }],
                "text_sources": [{"source_id": "paper", "path": "paper/main.md", "kind": "md"}],
                "anchors": [{
                    "anchor_id": "objective",
                    "source_id": "paper",
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
        paper.write_text(f"The objective value is {rendered_value}.\n", encoding="utf-8")

    def _write_paper_config(self, **overrides: object) -> None:
        config: dict[str, object] = {
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
        }
        config.update(overrides)
        self._write_json(self.case_dir / "paper" / "paper_preflight_config.json", config)

    def test_training_absent_flags_keeps_existing_gates_off(self) -> None:
        effective, settings = self._effective({"submission_tier": "training"})
        self.assertFalse(run_case_module._result_consistency_requested(effective))
        self.assertFalse(settings["effective_paper_preflight_enabled"])
        self.assertEqual(settings["tier_default_applied"], [])

    def test_selection_absent_result_flags_defaults_required_and_enabled(self) -> None:
        self._write_paper_config(enabled=False)
        effective, settings = self._effective({"submission_tier": "selection"})
        self.assertTrue(effective["result_consistency_required"])
        self.assertTrue(effective["result_consistency_enabled"])
        self.assertEqual(
            settings["effective_result_consistency_manifest_path"],
            "output/validation/result_consistency_manifest.json",
        )

    def test_selection_missing_manifest_blocks_cleanly(self) -> None:
        self._write_paper_config(enabled=False)
        effective, _ = self._effective({"submission_tier": "selection"})
        run_dir = self.root / "run_missing_manifest"
        run_dir.mkdir()
        self._write_json(run_dir / "output" / "result_summary.json", {"case_id": "tier_fixture"})
        report = run_case_module._run_result_consistency_if_configured(
            case_dir=self.case_dir, run_dir=run_dir, case_config=effective, risks=self.risks
        )
        self.assertEqual(report["status"], "BLOCKED")
        self.assertIn("RESULT_CONTRACT_MANIFEST_MISSING", report["blockers"])
        self.assertTrue(any(risk.get("fatal") for risk in self.risks))

    def test_selection_present_manifest_and_matching_result_passes(self) -> None:
        self._write_paper_config(enabled=False)
        self._write_result_fixture()
        effective, _ = self._effective({"submission_tier": "selection"})
        run_dir = self.root / "run_pass"
        run_dir.mkdir()
        self._write_json(
            run_dir / "output" / "result_summary.json",
            json.loads((self.case_dir / "output" / "result_summary.json").read_text(encoding="utf-8")),
        )
        report = run_case_module._run_result_consistency_if_configured(
            case_dir=self.case_dir, run_dir=run_dir, case_config=effective, risks=[]
        )
        self.assertEqual(report["status"], "PASS")

    def test_formal_paper_enabled_defaults_true_when_field_absent(self) -> None:
        self._write_result_fixture()
        self._write_paper_config()
        _, settings = self._effective({"submission_tier": "formal"})
        run_dir = self.root / "paper_run"
        run_dir.mkdir()
        report = run_case_module._run_paper_preflight_if_enabled(
            case_dir=self.case_dir, run_dir=run_dir, risks=self.risks, effective_settings=settings
        )
        self.assertTrue(settings["effective_paper_preflight_enabled"])
        self.assertEqual(report["status"], "PASS")

    def test_formal_missing_paper_config_blocks_cleanly(self) -> None:
        _, settings = self._effective({"submission_tier": "formal"})
        self.assertTrue(settings["effective_paper_preflight_enabled"])
        self.assertTrue(any("PAPER_PREFLIGHT_CONFIG_MISSING" in str(item["message"]) for item in self.risks))
        self.assertTrue(any(item.get("fatal") for item in self.risks))

    def test_formal_explicit_paper_disable_is_respected_and_visible(self) -> None:
        self._write_paper_config(enabled=False)
        _, settings = self._effective({"submission_tier": "formal"})
        self.assertFalse(settings["effective_paper_preflight_enabled"])
        self.assertIn("paper_preflight", settings["tier_default_explicitly_disabled"])
        self.assertTrue(any("TIER_DEFAULT_EXPLICITLY_DISABLED: paper_preflight" in str(r["message"]) for r in self.risks))

    def test_optional_evidence_and_human_revision_are_not_enabled_by_tier(self) -> None:
        self._write_paper_config(enabled=False)
        effective, _ = self._effective({"submission_tier": "formal"})
        self.assertFalse(run_case_module._optional_evidence_requested(effective))
        self.assertIsNot(effective.get("human_revision_required"), True)

    def test_git_snapshot_records_detached_head_and_untracked_porcelain_entries(self) -> None:
        completed = (
            SimpleNamespace(returncode=0, stdout="a" * 40 + "\n", stderr=""),
            SimpleNamespace(returncode=1, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout=b"?? scratch.txt\0 M tracked.txt\0", stderr=b""),
        )
        with mock.patch.object(run_case_module.subprocess, "run", side_effect=completed):
            snapshot = run_case_module._git_provenance_snapshot(self.root)
        self.assertTrue(snapshot["capture_ok"])
        self.assertTrue(snapshot["detached_head"])
        self.assertIsNone(snapshot["branch"])
        self.assertFalse(snapshot["raw_worktree_clean"])
        self.assertEqual(snapshot["status_entries"], ["?? scratch.txt", " M tracked.txt"])
        self.assertEqual(
            snapshot["status_records"],
            [
                {"xy": "??", "path": "scratch.txt", "original_path": None},
                {"xy": " M", "path": "tracked.txt", "original_path": None},
            ],
        )

    def test_git_snapshot_failure_never_defaults_to_clean(self) -> None:
        with mock.patch.object(run_case_module.subprocess, "run", side_effect=OSError("git unavailable")):
            snapshot = run_case_module._git_provenance_snapshot(self.root)
        self.assertFalse(snapshot["capture_ok"])
        self.assertIsNone(snapshot["raw_worktree_clean"])
        self.assertTrue(snapshot["capture_error"])
        provenance = run_case_module._runtime_source_provenance(snapshot, snapshot)
        self.assertEqual(provenance["source_provenance_status"], "CAPTURE_UNAVAILABLE")
        self.assertFalse(provenance["source_commit_stable"])

    def test_porcelain_z_parser_handles_standard_statuses_and_unicode_paths(self) -> None:
        stream = (
            b" M modified.txt\0"
            b"M  staged.txt\0"
            b"MM both.txt\0"
            b" D deleted.txt\0"
            b"?? space \" quote \xe4\xb8\xad.txt\0"
        )
        records, parse_error = run_case_module._parse_porcelain_v1_z(stream)
        self.assertIsNone(parse_error)
        self.assertEqual(
            records,
            [
                {"xy": " M", "path": "modified.txt", "original_path": None},
                {"xy": "M ", "path": "staged.txt", "original_path": None},
                {"xy": "MM", "path": "both.txt", "original_path": None},
                {"xy": " D", "path": "deleted.txt", "original_path": None},
                {"xy": "??", "path": "space \" quote 中.txt", "original_path": None},
            ],
        )

    def test_porcelain_z_parser_keeps_rename_paths_and_rejects_malformed_stream(self) -> None:
        records, parse_error = run_case_module._parse_porcelain_v1_z(
            b"R  new name.txt\0old name.txt\0C  copied.txt\0source.txt\0"
        )
        self.assertIsNone(parse_error)
        self.assertEqual(records[0], {"xy": "R ", "path": "new name.txt", "original_path": "old name.txt"})
        self.assertEqual(records[1], {"xy": "C ", "path": "copied.txt", "original_path": "source.txt"})
        malformed_records, malformed_error = run_case_module._parse_porcelain_v1_z(b"?? missing-terminator.txt")
        self.assertEqual(malformed_records, [])
        self.assertTrue(malformed_error)

    def test_malformed_porcelain_snapshot_is_not_clean(self) -> None:
        completed = (
            SimpleNamespace(returncode=0, stdout="a" * 40 + "\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="main\n", stderr=""),
            SimpleNamespace(returncode=0, stdout=b"?? malformed.txt", stderr=b""),
        )
        with mock.patch.object(run_case_module.subprocess, "run", side_effect=completed):
            snapshot = run_case_module._git_provenance_snapshot(self.root)
        self.assertFalse(snapshot["capture_ok"])
        self.assertIsNone(snapshot["raw_worktree_clean"])
        self.assertTrue(snapshot["capture_error"])

    def test_status_classification_uses_exact_paths_and_status_transitions(self) -> None:
        before = [{"xy": " M", "path": "already_dirty.txt", "original_path": None}]
        after = [
            {"xy": "MM", "path": "already_dirty.txt", "original_path": None},
            {"xy": "??", "path": "generated.json", "original_path": None},
            {"xy": "??", "path": "output/new.json", "original_path": None},
        ]
        classification = run_case_module._classify_git_status_records(
            before,
            after,
            {"generated.json", "output/**"},
            {"run_result.json"},
        )
        self.assertEqual(classification["preexisting_status_entries"], before)
        self.assertEqual(classification["generated_status_entries_after"], [after[1]])
        self.assertEqual(classification["unexpected_status_entries_after"], [after[0], after[2]])
        self.assertEqual(classification["expected_post_snapshot_entries"], [{"path": "run_result.json"}])
        self.assertTrue(classification["classification_errors"])

    def test_status_classification_requires_both_rename_paths_to_be_exactly_registered(self) -> None:
        rename = {"xy": "R ", "path": "new.txt", "original_path": "old.txt"}
        unexpected = run_case_module._classify_git_status_records([], [rename], {"new.txt"}, set())
        generated = run_case_module._classify_git_status_records([], [rename], {"new.txt", "old.txt"}, set())
        self.assertEqual(unexpected["unexpected_status_entries_after"], [rename])
        self.assertEqual(generated["generated_status_entries_after"], [rename])

    def test_isolated_formal_defaults_trigger_both_existing_checks(self) -> None:
        self._write_result_fixture()
        self._write_paper_config()
        self._write_json(self.case_dir / "case_config.json", {"case_id": "tier_fixture", "submission_tier": "formal"})
        run_dir = self.root / "isolated_good"
        with mock.patch.object(
            run_case_module,
            "_run_result_consistency_if_configured",
            wraps=run_case_module._run_result_consistency_if_configured,
        ) as consistency, mock.patch.object(
            run_case_module,
            "_run_paper_preflight_if_enabled",
            wraps=run_case_module._run_paper_preflight_if_enabled,
        ) as paper, mock.patch(
            "validation.xval_independent.validate_xval_independent_check",
            return_value={
                "status": "PASS",
                "artifact_path": "output/validation/xval_independent_check.json",
                "blockers": [],
                "warnings": [],
                "checked_targets_count": 0,
                "comparison_results_count": 0,
                "stale_bindings": [],
            },
        ) as xval:
            result = run_case_module.run_case("run", self.case_dir, 5, isolated_smoke=True, run_dir=run_dir)
        self.assertEqual(result["risk_status"], "PASS")
        consistency.assert_called_once()
        paper.assert_called_once()
        xval.assert_called_once()
        self.assertIn("result_consistency_required", result["tier_default_applied"])
        self.assertIn("paper_preflight", result["tier_default_applied"])
        self.assertFalse(result["candidate_package_created"])
        self.assertFalse(result["final_submit_created"])

    def test_isolated_formal_result_mismatch_still_blocks(self) -> None:
        self._write_result_fixture(rendered_value="999.0")
        self._write_paper_config()
        self._write_json(self.case_dir / "case_config.json", {"case_id": "tier_fixture", "submission_tier": "formal"})
        run_dir = self.root / "isolated_bad_result"
        result = run_case_module.run_case("run", self.case_dir, 5, isolated_smoke=True, run_dir=run_dir)
        report = json.loads((run_dir / "output" / "validation" / "result_consistency_report.json").read_text(encoding="utf-8"))
        self.assertEqual(result["risk_status"], "BLOCK")
        self.assertIn("RESULT_VALUE_MISMATCH", report["blockers"])

    def test_isolated_formal_paper_hard_error_still_blocks(self) -> None:
        self._write_result_fixture()
        (self.case_dir / "paper" / "main.md").write_text("The objective value is 1234.5.\nCodex\n", encoding="utf-8")
        self._write_paper_config()
        self._write_json(self.case_dir / "case_config.json", {"case_id": "tier_fixture", "submission_tier": "formal"})
        run_dir = self.root / "isolated_bad_paper"
        result = run_case_module.run_case("run", self.case_dir, 5, isolated_smoke=True, run_dir=run_dir)
        report = json.loads((run_dir / "output" / "validation" / "paper_hard_error_report.json").read_text(encoding="utf-8"))
        self.assertEqual(result["risk_status"], "BLOCK")
        self.assertIn("INTERNAL_WORKFLOW_TERM_FOUND", report["blockers"])
        self.assertFalse((self.root / "final_submit").exists())


if __name__ == "__main__":
    unittest.main()
