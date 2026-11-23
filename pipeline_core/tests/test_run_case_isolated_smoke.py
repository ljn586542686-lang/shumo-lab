from __future__ import annotations

import json
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run_case as run_case_module
from scripts.stabilization_freeze import evaluate


class RunCaseIsolatedSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.case_dir = self.root / "case"
        self.case_dir.mkdir()
        self.run_dir = self.root / "isolated_run"
        self._write_json(
            self.case_dir / "case_config.json",
            {
                "case_id": "isolated_fixture",
                "result_consistency_enabled": False,
                "result_consistency_required": False,
                "sensitivity_required": False,
                "tournament_required": False,
                "sensitivity_hook": {"enabled": False},
                "tournament_hook": {"enabled": False},
            },
        )
        self.summary_path = self.case_dir / "output" / "result_summary.json"
        self.summary_payload = {
            "schema_version": "isolated_fixture_v1",
            "generated_at": "2026-07-11T00:00:00+00:00",
            "run_id": "trusted_fixture_run",
            "case_id": "isolated_fixture",
            "status": "ok",
            "metrics": {"objective_value": 1234.5},
            "final_submit_created": False,
        }
        self._write_json(self.summary_path, self.summary_payload)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _run(self, *, stage: str = "run", run_dir: Path | None = None) -> dict[str, object]:
        return run_case_module.run_case(
            stage,
            self.case_dir,
            5,
            isolated_smoke=True,
            run_dir=run_dir or self.run_dir,
        )

    @staticmethod
    def _snapshot(
        *,
        commit: str = "a" * 40,
        branch: str | None = "main",
        status_entries: list[str] | None = None,
        capture_ok: bool = True,
        capture_error: str | None = None,
    ) -> dict[str, object]:
        entries = status_entries or []
        return {
            "commit": commit if capture_ok else None,
            "branch": branch,
            "detached_head": branch is None and capture_ok,
            "status_entries": entries,
            "raw_worktree_clean": len(entries) == 0 if capture_ok else None,
            "capture_ok": capture_ok,
            "capture_error": capture_error,
        }

    def _write_legacy_summary(self) -> bytes:
        legacy = dict(self.summary_payload)
        for field in ("schema_version", "generated_at", "run_id"):
            legacy.pop(field)
        self._write_json(self.summary_path, legacy)
        return self.summary_path.read_bytes()

    def _enable_required_result_consistency(self, rendered_value: str = "1234.5") -> None:
        config = json.loads((self.case_dir / "case_config.json").read_text(encoding="utf-8"))
        config["result_consistency_enabled"] = True
        config["result_consistency_required"] = True
        config["result_consistency_manifest_path"] = "output/validation/result_consistency_manifest.json"
        self._write_json(self.case_dir / "case_config.json", config)
        self._write_json(
            self.case_dir / "output" / "validation" / "result_consistency_manifest.json",
            {
                "version": "1.0",
                "case_id": "isolated_fixture",
                "reported_values": [
                    {
                        "value_id": "objective_value",
                        "json_pointer": "/metrics/objective_value",
                        "unit": "",
                        "display_precision": 1,
                    }
                ],
                "text_sources": [
                    {"source_id": "smoke_excerpt", "path": "paper/smoke_excerpt.md", "kind": "md"}
                ],
                "anchors": [
                    {
                        "anchor_id": "objective_value_in_excerpt",
                        "source_id": "smoke_excerpt",
                        "value_id": "objective_value",
                        "required": True,
                    }
                ],
                "producer_scripts": [],
                "code_output_paths": ["output/result_summary.json"],
                "appendix_code_paths": [],
                "uniqueness_claims": [],
            },
        )
        excerpt = self.case_dir / "paper" / "smoke_excerpt.md"
        excerpt.parent.mkdir(parents=True, exist_ok=True)
        excerpt.write_text(f"# Fixture Result\n\nThe objective value is {rendered_value}.\n", encoding="utf-8")

    def _enable_paper_check(self, body: str) -> None:
        excerpt = self.case_dir / "paper" / "smoke_excerpt.md"
        excerpt.parent.mkdir(parents=True, exist_ok=True)
        excerpt.write_text(body, encoding="utf-8")
        self._write_json(
            self.case_dir / "paper" / "paper_preflight_config.json",
            {
                "version": "1.0",
                "enabled": True,
                "submission_tier": "selection",
                "submitted_text_sources": [{"path": "paper/smoke_excerpt.md", "kind": "md"}],
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

    def test_isolated_smoke_reuses_existing_result_summary(self) -> None:
        result = self._run()
        staged = json.loads((self.run_dir / "output" / "result_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["risk_status"], "PASS")
        self.assertTrue(result["existing_summary_reused"])
        self.assertEqual(staged, self.summary_payload)

    def test_isolated_smoke_writes_result_summary_content_binding(self) -> None:
        result = self._run()
        binding = result["upstream_content_bindings"][0]
        self.assertEqual(binding["relative_path"], "output/result_summary.json")
        self.assertEqual(binding["sha256"], hashlib.sha256(self.summary_path.read_bytes()).hexdigest())
        risk = json.loads((self.run_dir / "risk_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(risk["upstream_content_bindings"], result["upstream_content_bindings"])

    def test_isolated_smoke_captures_before_before_writes_and_persists_one_provenance_object(self) -> None:
        before = self._snapshot()
        after = self._snapshot(status_entries=["?? generated_during_run.txt"])
        observations: list[tuple[bool, bool, bool]] = []

        def capture(_: Path) -> dict[str, object]:
            observations.append(
                (
                    self.run_dir.exists(),
                    (self.run_dir / "risk_summary.json").exists(),
                    (self.run_dir / "run_result.json").exists(),
                )
            )
            return (before, after)[len(observations) - 1]

        with mock.patch.object(run_case_module, "_git_provenance_snapshot", side_effect=capture):
            result = self._run()
        risk = json.loads((self.run_dir / "risk_summary.json").read_text(encoding="utf-8"))
        persisted_result = json.loads((self.run_dir / "run_result.json").read_text(encoding="utf-8"))
        self.assertEqual(observations, [(False, False, False), (True, False, False)])
        self.assertEqual(result["source_provenance"], risk["source_provenance"])
        self.assertEqual(persisted_result["source_provenance"], risk["source_provenance"])
        self.assertTrue(result["source_provenance"]["source_commit_stable"])
        self.assertEqual(result["source_provenance"]["source_provenance_status"], "CAPTURED_UNCLASSIFIED_OUTPUTS")
        self.assertFalse(result["source_provenance"]["source_worktree_clean_after_raw"])
        self.assertEqual(risk["fatal_count"], 0)

    def test_isolated_smoke_marks_commit_change_diagnostic_only(self) -> None:
        before = self._snapshot(commit="a" * 40)
        after = self._snapshot(commit="b" * 40)
        with mock.patch.object(run_case_module, "_git_provenance_snapshot", side_effect=[before, after]):
            result = self._run()
        provenance = result["source_provenance"]
        self.assertFalse(provenance["source_commit_stable"])
        self.assertEqual(provenance["source_provenance_status"], "COMMIT_CHANGED_DURING_RUN")
        self.assertEqual(result["risk_status"], "PASS")

    def test_isolated_smoke_unhandled_exception_still_persists_structured_provenance(self) -> None:
        before = self._snapshot()
        after = self._snapshot()
        with mock.patch.object(run_case_module, "_git_provenance_snapshot", side_effect=[before, after]), mock.patch.object(
            run_case_module,
            "_validated_content_bindings",
            side_effect=RuntimeError("fixture provenance exception"),
        ):
            result = self._run()
        risk = json.loads((self.run_dir / "risk_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(result["risk_status"], "BLOCK")
        self.assertEqual(result["source_provenance"], risk["source_provenance"])
        self.assertEqual(result["source_provenance"]["source_provenance_status"], "CAPTURED_UNCLASSIFIED_OUTPUTS")

    def test_isolated_smoke_writes_manifest_and_paper_content_bindings_when_used(self) -> None:
        self._enable_required_result_consistency()
        self._enable_paper_check("# Fixture Result\n\nThe objective value is 1234.5.\n")
        result = self._run()
        paths = {item["relative_path"] for item in result["upstream_content_bindings"]}
        self.assertTrue({
            "output/result_summary.json",
            "output/validation/result_consistency_manifest.json",
            "paper/paper_preflight_config.json",
            "paper/smoke_excerpt.md",
        }.issubset(paths))

    def test_isolated_smoke_accepts_bound_legacy_summary_missing_metadata(self) -> None:
        before = self._write_legacy_summary()
        self._enable_required_result_consistency()
        result = self._run()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["risk_status"], "PASS")
        self.assertEqual(self.summary_path.read_bytes(), before)
        report = json.loads(
            (self.run_dir / "output" / "validation" / "result_consistency_report.json").read_text(encoding="utf-8")
        )
        self.assertEqual(report["status"], "PASS")

    def test_isolated_smoke_reports_legacy_metadata_compatibility_warning(self) -> None:
        self._write_legacy_summary()
        self._enable_required_result_consistency()
        self._run()
        risk = json.loads((self.run_dir / "risk_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(risk["fatal_count"], 0)
        self.assertEqual(risk["warning_count"], 1)
        self.assertIn(
            "LEGACY_RESULT_SUMMARY_METADATA_COMPATIBLE_IN_ISOLATED_SMOKE",
            risk["risks"][0]["message"],
        )

    def test_legacy_metadata_without_required_result_consistency_still_blocks(self) -> None:
        self._write_legacy_summary()
        result = self._run()
        self.assertEqual(result["risk_status"], "BLOCK")
        risk = json.loads((self.run_dir / "risk_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(risk["fatal_count"], 3)
        self.assertIn("result_consistency_required=true and PASS", risk["risks"][0]["message"])

    def test_non_isolated_validation_remains_strict_for_missing_metadata(self) -> None:
        self._write_legacy_summary()
        findings = run_case_module.validate_result_summary_file(self.summary_path)
        missing_paths = {
            finding["json_path"]
            for finding in findings
            if finding["issue"] == "required_field_missing"
        }
        self.assertEqual(missing_paths, {"$.schema_version", "$.generated_at", "$.run_id"})

    def test_legacy_summary_result_mismatch_still_blocks(self) -> None:
        self._write_legacy_summary()
        self._enable_required_result_consistency(rendered_value="999.0")
        result = self._run()
        self.assertEqual(result["risk_status"], "BLOCK")
        report = json.loads(
            (self.run_dir / "output" / "validation" / "result_consistency_report.json").read_text(encoding="utf-8")
        )
        self.assertEqual(report["status"], "BLOCKED")
        self.assertIn("RESULT_VALUE_MISMATCH", report["blockers"])

    def test_legacy_summary_paper_hard_error_still_blocks(self) -> None:
        self._write_legacy_summary()
        self._enable_required_result_consistency()
        self._enable_paper_check("# Fixture Result\n\nThe objective value is 1234.5.\n\nCodex\n")
        result = self._run()
        self.assertEqual(result["risk_status"], "BLOCK")
        consistency = json.loads(
            (self.run_dir / "output" / "validation" / "result_consistency_report.json").read_text(encoding="utf-8")
        )
        paper = json.loads(
            (self.run_dir / "output" / "validation" / "paper_hard_error_report.json").read_text(encoding="utf-8")
        )
        self.assertEqual(consistency["status"], "PASS")
        self.assertEqual(paper["status"], "BLOCKED")
        self.assertIn("INTERNAL_WORKFLOW_TERM_FOUND", paper["blockers"])

    def test_isolated_smoke_does_not_overwrite_result_summary(self) -> None:
        before_bytes = self.summary_path.read_bytes()
        before_mtime = self.summary_path.stat().st_mtime_ns
        self._run()
        self.assertEqual(self.summary_path.read_bytes(), before_bytes)
        self.assertEqual(self.summary_path.stat().st_mtime_ns, before_mtime)

    def test_isolated_smoke_works_without_model_config(self) -> None:
        self.assertFalse((self.case_dir / "model_config.json").exists())
        with mock.patch.object(run_case_module, "_write_math_core_summary") as math_run, mock.patch.object(
            run_case_module, "_write_result_summary"
        ) as generated_summary:
            result = self._run()
        self.assertEqual(result["status"], "ok")
        math_run.assert_not_called()
        generated_summary.assert_not_called()

    def test_isolated_smoke_writes_only_to_run_dir(self) -> None:
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self._run()
        after_outside_run = {
            path.relative_to(self.root): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file() and self.run_dir not in path.parents
        }
        self.assertEqual(after_outside_run, before)
        self.assertTrue((self.run_dir / "risk_summary.json").is_file())
        self.assertTrue((self.run_dir / "run_result.json").is_file())

    def test_isolated_smoke_does_not_update_global_compliance_queue(self) -> None:
        with mock.patch.object(run_case_module, "_update_compliance_queue") as update_queue, mock.patch.object(
            run_case_module, "_ensure_static_templates"
        ) as static_templates:
            result = self._run()
        update_queue.assert_not_called()
        static_templates.assert_not_called()
        self.assertTrue(result["global_writes_blocked"])
        self.assertFalse((self.run_dir / "compliance_todo_queue_snapshot.json").exists())

    def test_isolated_smoke_does_not_update_global_evidence_manifest(self) -> None:
        with mock.patch.object(run_case_module, "_write_f2_1_evidence") as write_evidence:
            self._run()
        write_evidence.assert_not_called()
        self.assertFalse((self.run_dir / "evidence_map.json").exists())

    def test_isolated_smoke_does_not_create_candidate_package(self) -> None:
        with mock.patch.object(run_case_module, "package_candidate") as package:
            result = self._run()
        package.assert_not_called()
        self.assertFalse(result["candidate_package_created"])
        self.assertFalse((self.root / "candidate_packages").exists())

    def test_isolated_smoke_does_not_create_final_submit(self) -> None:
        result = self._run()
        self.assertFalse(result["final_submit_created"])
        self.assertFalse((self.root / "final_submit").exists())
        self.assertFalse((self.case_dir / "final_submit").exists())

    def test_missing_result_summary_fails_cleanly(self) -> None:
        self.summary_path.unlink()
        result = self._run()
        self.assertEqual(result["status"], "fatal")
        self.assertEqual(result["risk_status"], "BLOCK")
        self.assertFalse(result["existing_summary_reused"])
        risk = json.loads((self.run_dir / "risk_summary.json").read_text(encoding="utf-8"))
        self.assertIn("result_summary.json is missing", risk["risks"][0]["message"])

    def test_malformed_result_summary_fails_cleanly(self) -> None:
        self.summary_path.write_text("{not-json", encoding="utf-8")
        result = self._run()
        self.assertEqual(result["status"], "fatal")
        self.assertEqual(result["risk_status"], "BLOCK")
        self.assertNotIn("exception", result)
        self.assertTrue((self.run_dir / "error.log").is_file())

    def test_isolated_smoke_triggers_result_consistency_when_enabled(self) -> None:
        config = json.loads((self.case_dir / "case_config.json").read_text(encoding="utf-8"))
        config["result_consistency_enabled"] = True
        self._write_json(self.case_dir / "case_config.json", config)
        with mock.patch.object(
            run_case_module,
            "_run_result_consistency_if_configured",
            wraps=run_case_module._run_result_consistency_if_configured,
        ) as consistency:
            self._run()
        consistency.assert_called_once()

    def test_isolated_smoke_triggers_paper_preflight_when_enabled(self) -> None:
        paper_config = self.case_dir / "paper" / "paper_preflight_config.json"
        self._write_json(paper_config, {"version": "1.0", "enabled": True, "submitted_text_sources": []})
        with mock.patch.object(
            run_case_module,
            "_run_paper_preflight_if_enabled",
            wraps=run_case_module._run_paper_preflight_if_enabled,
        ) as paper_check:
            self._run()
        paper_check.assert_called_once()
        self.assertTrue((self.run_dir / "output" / "validation" / "paper_hard_error_report.json").is_file())

    def test_optional_evidence_remains_disabled_by_default(self) -> None:
        with mock.patch(
            "pipeline_core.python_tools.optional_evidence_hooks.check_optional_evidence_hooks"
        ) as optional_check:
            self._run()
        optional_check.assert_not_called()
        self.assertFalse((self.run_dir / "output" / "validation" / "optional_evidence_hooks_report.json").exists())

    def test_isolated_smoke_disables_promotion_and_package_stages(self) -> None:
        with mock.patch.object(run_case_module, "safe_promote") as promote, mock.patch.object(
            run_case_module, "register_asset"
        ) as register:
            result = self._run(stage="package")
        self.assertEqual(result["risk_status"], "BLOCK")
        self.assertTrue(result["promotion_disabled"])
        promote.assert_not_called()
        register.assert_not_called()

    def test_freeze_tripwire_and_registered_ids_remain_unchanged(self) -> None:
        report = evaluate(run_case_module.ROOT)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["registered_ids_current"], report["registered_ids_baseline"])

    _FORBIDDEN_PROVENANCE_FIELDS = (
        "source_worktree_clean_after",
        "source_provenance_valid",
        "safe_promotion_allowed",
        "authorization_bindings_valid",
        "final_freeze_ready",
        "final_submit_allowed",
    )

    @staticmethod
    def _expected_final_paths_rel(run_dir: Path) -> set[str]:
        return {
            run_case_module.rel_path(run_dir / "risk_summary.json"),
            run_case_module.rel_path(run_dir / "risk_summary.md"),
            run_case_module.rel_path(run_dir / "run_result.json"),
        }

    def test_isolated_smoke_success_registers_expected_writes_outside_repo(self) -> None:
        result = self._run()
        risk = json.loads((self.run_dir / "risk_summary.json").read_text(encoding="utf-8"))
        persisted_result = json.loads((self.run_dir / "run_result.json").read_text(encoding="utf-8"))

        result_provenance = result["source_provenance"]
        risk_provenance = risk["source_provenance"]
        persisted_provenance = persisted_result["source_provenance"]
        self.assertEqual(result_provenance, risk_provenance)
        self.assertEqual(result_provenance, persisted_provenance)

        self.assertEqual(result_provenance["source_write_registry_scope"], "PRE_SNAPSHOT_OBSERVED_WRITES")
        # error.log is the only pre-snapshot observed required write on the
        # success path (no summary reused in this fixture run), so
        # completeness is achievable and true.
        self.assertTrue(result_provenance["source_write_registry_complete"])
        self.assertEqual(result_provenance["source_registry_errors"], [])

        # self.run_dir lives under a TemporaryDirectory, outside the repo
        # root, so every observed write lands in the external bucket, never
        # in written_repo_paths.
        self.assertEqual(result_provenance["source_written_repo_paths"], [])
        external = set(result_provenance["source_external_written_paths"])
        run_dir_resolved = self.run_dir.resolve()
        self.assertIn((run_dir_resolved / "error.log").as_posix(), external)
        # risk_summary.json/.md and run_result.json can never be observed —
        # the shared snapshot is captured before any of them exist — so they
        # never appear as written/external paths, only as expected paths.
        self.assertNotIn((run_dir_resolved / "risk_summary.json").as_posix(), external)
        self.assertNotIn((run_dir_resolved / "risk_summary.md").as_posix(), external)
        self.assertNotIn((run_dir_resolved / "run_result.json").as_posix(), external)

        self.assertEqual(
            set(result_provenance["source_expected_post_snapshot_paths"]),
            self._expected_final_paths_rel(self.run_dir),
        )
        self.assertFalse(result_provenance["source_expected_post_snapshot_write_verified"])

        # These fields must never be set by WriteRegistry wiring specifically
        # (source_provenance is where it writes). safe_promotion_allowed
        # legitimately appears at the result's top level already, sourced
        # from the pre-existing, unrelated XVAL machine record — this
        # assertion only guards the registry's own output surface.
        for field in self._FORBIDDEN_PROVENANCE_FIELDS:
            self.assertNotIn(field, result_provenance)

    def test_isolated_smoke_success_with_reused_summary_registers_staged_copy(self) -> None:
        result = self._run()
        self.assertTrue(result["existing_summary_reused"])
        provenance = result["source_provenance"]
        staged_summary = (self.run_dir / "output" / "result_summary.json").resolve()
        external = set(provenance["source_external_written_paths"])
        self.assertIn(staged_summary.as_posix(), external)
        # Both error.log and the staged copy are pre-snapshot observed
        # writes here, so completeness still holds.
        self.assertTrue(provenance["source_write_registry_complete"])
        self.assertIn(
            run_case_module.ISOLATED_TOKEN_STAGED_SUMMARY_COPY, provenance["source_completed_write_tokens"]
        )

    def test_isolated_smoke_missing_summary_does_not_register_staged_copy(self) -> None:
        self.summary_path.unlink()
        result = self._run()
        self.assertFalse(result["existing_summary_reused"])
        provenance = result["source_provenance"]
        staged_summary = (self.run_dir / "output" / "result_summary.json").resolve()
        external = set(provenance["source_external_written_paths"])
        self.assertNotIn(staged_summary.as_posix(), external)
        # error.log is unconditional and still completes before the shared
        # snapshot is taken, even though the summary copy never ran; the
        # staged-copy token is not part of required_tokens in this branch
        # (existing_summary_reused is False), so completeness still holds.
        self.assertIn((self.run_dir.resolve() / "error.log").as_posix(), external)
        self.assertNotIn(
            run_case_module.ISOLATED_TOKEN_STAGED_SUMMARY_COPY, provenance["source_required_write_tokens"]
        )
        self.assertTrue(provenance["source_write_registry_complete"])

    def test_isolated_smoke_exception_registers_only_completed_writes(self) -> None:
        before = self._snapshot()
        after = self._snapshot()
        with mock.patch.object(
            run_case_module, "_git_provenance_snapshot", side_effect=[before, after]
        ), mock.patch.object(
            run_case_module,
            "_validated_content_bindings",
            side_effect=RuntimeError("fixture provenance exception"),
        ):
            result = self._run()
        risk = json.loads((self.run_dir / "risk_summary.json").read_text(encoding="utf-8"))
        persisted_result = json.loads((self.run_dir / "run_result.json").read_text(encoding="utf-8"))

        provenance = result["source_provenance"]
        self.assertEqual(provenance, risk["source_provenance"])
        self.assertEqual(provenance, persisted_result["source_provenance"])

        # error.log is written before the snapshot in the exception branch
        # too, so it is observed and completeness holds.
        self.assertTrue(provenance["source_write_registry_complete"])
        external = set(provenance["source_external_written_paths"])
        run_dir_resolved = self.run_dir.resolve()
        self.assertIn((run_dir_resolved / "error.log").as_posix(), external)
        self.assertNotIn((run_dir_resolved / "risk_summary.json").as_posix(), external)
        self.assertNotIn((run_dir_resolved / "run_result.json").as_posix(), external)
        self.assertEqual(
            set(provenance["source_expected_post_snapshot_paths"]),
            self._expected_final_paths_rel(self.run_dir),
        )
        self.assertFalse(provenance["source_expected_post_snapshot_write_verified"])
        for field in self._FORBIDDEN_PROVENANCE_FIELDS:
            self.assertNotIn(field, provenance)

    def test_isolated_smoke_failed_write_produces_registry_error_not_silent_success(self) -> None:
        def _fake_write_error_log(run_dir, risks, exc=None):
            # Simulate a helper that reports success without leaving a real
            # file behind at the path record_write will check next. error.log
            # is now the sole required token on both the success and
            # exception paths, so faking it is sufficient to exercise a
            # failed observed write on the success path.
            return None

        with mock.patch.object(
            run_case_module, "_write_error_log", side_effect=_fake_write_error_log
        ):
            result = self._run()
        self.assertFalse(result["source_provenance"]["source_write_registry_complete"])
        self.assertTrue(result["source_provenance"]["source_registry_errors"])
        completed = set(result["source_provenance"]["source_completed_write_tokens"])
        self.assertNotIn(run_case_module.ISOLATED_TOKEN_ERROR_LOG, completed)

    def test_non_isolated_exception_registers_compliance_queue_and_error_log_as_observed(self) -> None:
        real_root = run_case_module.ROOT
        real_queue_path = real_root / "compliance" / "compliance_todo_queue.json"
        before_bytes = real_queue_path.read_bytes()
        before_git_status = subprocess.run(
            ["git", "status", "--porcelain", "--", "compliance/compliance_todo_queue.json"],
            cwd=real_root,
            capture_output=True,
            text=True,
            check=False,
        ).stdout

        captured_registries: list[run_case_module.WriteRegistry] = []
        real_registry_cls = run_case_module.WriteRegistry

        def _spy_registry(*args: object, **kwargs: object) -> run_case_module.WriteRegistry:
            instance = real_registry_cls(*args, **kwargs)  # type: ignore[arg-type]
            captured_registries.append(instance)
            return instance

        with tempfile.TemporaryDirectory() as fake_root_dir:
            fake_root = Path(fake_root_dir)
            # _update_compliance_queue(root, ...) writes to
            # root / "compliance" / "compliance_todo_queue.json" using
            # whatever `root` it is called with. run_case() binds its local
            # `root` from the module-level ROOT at call time, so patching
            # run_case_module.ROOT redirects every write this branch makes
            # (work_tmp, compliance, manifests) into this temp directory —
            # the real repo's compliance file is never opened for writing.
            with mock.patch.object(run_case_module, "ROOT", new=fake_root), mock.patch.object(
                run_case_module, "load_case_config", side_effect=RuntimeError("fixture load failure")
            ), mock.patch.object(run_case_module, "WriteRegistry", side_effect=_spy_registry):
                result = run_case_module.run_case("run", self.case_dir, 5)
            run_dir = fake_root / result["run_dir"]

            self.assertEqual(result["status"], "fatal")
            self.assertIn("source_provenance", result)
            provenance = result["source_provenance"]

            risk = json.loads((run_dir / "risk_summary.json").read_text(encoding="utf-8"))
            persisted_result = json.loads((run_dir / "run_result.json").read_text(encoding="utf-8"))
            self.assertEqual(provenance, risk["source_provenance"])
            self.assertEqual(provenance, persisted_result["source_provenance"])

            # The queue file was actually written under the temp root — not
            # faked, not pre-created — verified by reading real bytes back.
            fake_queue_path = fake_root / "compliance" / "compliance_todo_queue.json"
            self.assertTrue(fake_queue_path.is_file())
            written_queue_payload = json.loads(fake_queue_path.read_text(encoding="utf-8"))
            self.assertEqual(written_queue_payload["last_run_id"], result["run_id"])

            # compliance_todo_queue.json and error.log both complete before
            # the after-snapshot in this branch, so they are genuine
            # pre-snapshot observed writes reflected directly in the
            # embedded snapshot (unlike the isolated branches' final files).
            # WriteRegistry computes written_repo_paths using its own root
            # field (the patched fake_root passed to its constructor), not
            # the module-level rel_path() helper — so the expected values
            # here are computed relative to fake_root directly, matching
            # what the registry itself does internally.
            written = set(provenance["source_written_repo_paths"])
            expected_queue = fake_queue_path.resolve().relative_to(fake_root.resolve()).as_posix()
            expected_error_log = (run_dir / "error.log").resolve().relative_to(fake_root.resolve()).as_posix()
            self.assertIn(expected_queue, written)
            self.assertIn(expected_error_log, written)
            self.assertEqual(provenance["source_external_written_paths"], [])
            self.assertTrue(provenance["source_write_registry_complete"])
            self.assertEqual(provenance["source_registry_errors"], [])

            self.assertEqual(
                set(provenance["source_expected_post_snapshot_paths"]),
                self._expected_final_paths_rel(run_dir),
            )
            self.assertFalse(provenance["source_expected_post_snapshot_write_verified"])
            for field in self._FORBIDDEN_PROVENANCE_FIELDS:
                self.assertNotIn(field, provenance)

            # run_case()'s normal-success-path WriteRegistry is now
            # constructed only after every conditional branch in the main
            # try block has run (P6-PV1B2A3A2), i.e. much later than
            # load_case_config(). Since this fixture forces
            # load_case_config to raise immediately, the success-path
            # registry is never constructed at all — only the
            # except-Exception tail's run_case_exception_registry is.
            self.assertEqual(len(captured_registries), 1)
            registry = captured_registries[0]
            self.assertEqual(registry.required_tokens, run_case_module.RUN_CASE_EXCEPTION_REQUIRED_TOKENS)
            self.assertEqual(registry.written_repo_paths, frozenset({expected_queue, expected_error_log}))
            self.assertTrue(registry.diagnostic_completeness())

        # The real repo's compliance file and git status were never touched
        # at any point during this test — no finally-block restore needed
        # because nothing ever wrote to it.
        self.assertEqual(real_queue_path.read_bytes(), before_bytes)
        after_git_status = subprocess.run(
            ["git", "status", "--porcelain", "--", "compliance/compliance_todo_queue.json"],
            cwd=real_root,
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        self.assertEqual(before_git_status, after_git_status)


class RunCaseNormalSuccessRegistryTests(unittest.TestCase):
    """P6-PV1B2A3A1: the four core deterministic normal-success writes only."""

    _FORBIDDEN_PROVENANCE_FIELDS = RunCaseIsolatedSmokeTests._FORBIDDEN_PROVENANCE_FIELDS

    @property
    def _DEFAULT_FIXTURE_COMPLETED_TOKENS(self) -> set[str]:
        return {
            run_case_module.RUN_CASE_SUCCESS_TOKEN_RUN_CONFIG,
            run_case_module.RUN_CASE_SUCCESS_TOKEN_COMPLIANCE_QUEUE,
            run_case_module.RUN_CASE_SUCCESS_TOKEN_COMPLIANCE_QUEUE_SNAPSHOT,
            run_case_module.RUN_CASE_SUCCESS_TOKEN_ERROR_LOG,
            run_case_module.RUN_CASE_SUCCESS_TOKEN_RESULT_SUMMARY_JSON,
            run_case_module.RUN_CASE_SUCCESS_TOKEN_RESULT_SUMMARY_TXT,
            run_case_module.RUN_CASE_SUCCESS_TOKEN_VALIDATION_SUMMARY_JSON,
            run_case_module.RUN_CASE_SUCCESS_TOKEN_VALIDATION_SUMMARY_MD,
            run_case_module.RUN_CASE_SUCCESS_TOKEN_DATA_HEALTH_LOG,
            run_case_module.RUN_CASE_SUCCESS_TOKEN_DATA_HEALTH_JSON,
            run_case_module.RUN_CASE_SUCCESS_TOKEN_DATA_HEALTH_MD,
            run_case_module.RUN_CASE_SUCCESS_TOKEN_PDF_CHECKLIST_LOG,
            run_case_module.RUN_CASE_SUCCESS_TOKEN_PDF_CHECKLIST_JSON,
            run_case_module.RUN_CASE_SUCCESS_TOKEN_PDF_CHECKLIST_MD,
            run_case_module.RUN_CASE_SUCCESS_TOKEN_QG_REPORT_JSON,
            run_case_module.RUN_CASE_SUCCESS_TOKEN_QG_MANUAL_REVIEW_TODO,
            run_case_module.RUN_CASE_SUCCESS_TOKEN_QG_REPORT_MD,
            run_case_module.RUN_CASE_SUCCESS_TOKEN_SAFE_PROMOTE_DIFF_COMPLETE,
            run_case_module.RUN_CASE_SUCCESS_TOKEN_ASSETS_MANIFEST_WRITE,
        }

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.fake_root = Path(self.temp_dir.name) / "fake_root"
        self.fake_root.mkdir()
        self.case_dir = Path(self.temp_dir.name) / "case"
        self.case_dir.mkdir()
        self._write_json(
            self.case_dir / "case_config.json",
            {
                "case_id": "normal_success_fixture",
                "result_consistency_enabled": False,
                "result_consistency_required": False,
                "sensitivity_required": False,
                "tournament_required": False,
                "sensitivity_hook": {"enabled": False},
                "tournament_hook": {"enabled": False},
            },
        )
        # No model_config.json, so run_case() takes the _write_result_summary
        # branch rather than _write_math_core_summary — the simplest normal
        # in-process path with no additional fixture files required.
        # data/raw must contain at least one real file: since P6-PV1B2A3B
        # makes _run() call the real run_data_health_check (not a no-op), a
        # genuinely missing data/raw now correctly surfaces as a fatal risk
        # (as it should), which would otherwise block result_summary
        # generation for every "default success" test in this class.
        raw_dir = self.case_dir / "data" / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "sample.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _fake_subprocess_result(
        args: list[str], timeout_seconds: int, cwd: Path, log_path: Path
    ) -> dict[str, object]:
        # Stands in for subprocess.Popen, but — unlike a bare no-op — really
        # calls the same underlying function the real script would (in
        # process, no child process spawned), so it produces the real
        # target files data_health_check.py / pdf_checklist.py would. This
        # is required for DATA_HEALTH_*/PDF_CHECKLIST_* tokens to ever
        # legitimately complete: record_write independently verifies file
        # existence and would otherwise correctly flag every run here as
        # incomplete.
        script_name = Path(args[1]).name
        case_dir = Path(args[args.index("--case-dir") + 1])
        run_dir = Path(args[args.index("--run-dir") + 1])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if script_name == "data_health_check.py":
            import data_health_check

            data_health_check.run_data_health_check(case_dir, run_dir)
        elif script_name == "pdf_checklist.py":
            from x5_surgical_utils import generate_pdf_checklist

            generate_pdf_checklist(case_dir, run_dir, strict="--strict" in args)
        log_path.write_text("fake subprocess log\n", encoding="utf-8")
        return {"command": args, "returncode": 0, "timed_out": False, "log_path": str(log_path)}

    @staticmethod
    def _fake_write_validation_summary(run_dir, case_dir, risks, result_summary, pdf_check, *, strict_pdf=False):
        # Stands in for the real _write_validation_summary (excluded from
        # this slice's own write-tracking scope, same as pdf_checklist/
        # data_health), but — unlike a bare no-op — actually writes the two
        # files real callers would see, so tests default to the "this
        # conditional branch succeeded" shape unless a test deliberately
        # wants to exercise the failure path.
        validation_dir = run_dir / "output" / "validation"
        validation_dir.mkdir(parents=True, exist_ok=True)
        (validation_dir / "validation_summary.json").write_text("{}", encoding="utf-8")
        (validation_dir / "validation_summary.md").write_text("# stub\n", encoding="utf-8")

    def _fake_run_core_quality_gates(self, run_dir, case_dir, run_id, risks):
        # Stands in for _run_core_quality_gates, but really calls
        # run_quality_gates (in process, no rule engine behavior changed)
        # with root=self.fake_root passed explicitly — run_quality_gates
        # accepts root as an explicit keyword argument, so this stays
        # confined to the temp fixture regardless of the ROOT patch state.
        # Unlike a no-op, this produces the real qg_report.json/
        # manual_review_todo.json/qg_report.md files a genuine call would.
        from quality_gates import run_quality_gates

        return run_quality_gates(case_dir=case_dir, run_dir=run_dir, run_id=run_id, root=self.fake_root)

    def _run(self, *, stage: str = "run") -> dict[str, object]:
        with mock.patch.object(run_case_module, "ROOT", new=self.fake_root), mock.patch.object(
            run_case_module, "run_subprocess_with_timeout", side_effect=self._fake_subprocess_result
        ), mock.patch.object(
            run_case_module, "_write_validation_summary", side_effect=self._fake_write_validation_summary
        ), mock.patch.object(
            run_case_module, "_run_core_quality_gates", side_effect=self._fake_run_core_quality_gates
        ), mock.patch.object(
            # evidence_map writing goes through validation.evidence_writer,
            # whose write_run_evidence_map/update_global_evidence_map default
            # root=x5_surgical_utils.ROOT independently of run_case_module.ROOT
            # — patching run_case_module.ROOT does not redirect them. Since
            # evidence_map.json is explicitly out of scope for this slice
            # (only its expected path is asserted, never its actual content),
            # this call is mocked out entirely to avoid writing the real
            # repo's manifests/evidence_map.json.
            run_case_module,
            "_write_f2_1_evidence",
        ):
            # register_asset (x5_surgical_utils.py) now receives an explicit
            # root=root from run_case.py (P6-PV1B2A4B), so it is no longer
            # mocked here — it genuinely writes into self.fake_root's own
            # manifests/assets_manifest.json, never the real repo's.
            return run_case_module.run_case(stage, self.case_dir, 5)

    def test_normal_success_registers_four_core_deterministic_writes(self) -> None:
        result = self._run()
        run_dir = self.fake_root / result["run_dir"]
        provenance = result["source_provenance"]

        self.assertTrue((run_dir / "run_config.json").is_file())
        self.assertTrue((self.fake_root / "compliance" / "compliance_todo_queue.json").is_file())
        self.assertTrue((run_dir / "compliance_todo_queue_snapshot.json").is_file())
        self.assertTrue((run_dir / "error.log").is_file())

        completed = set(provenance["source_completed_write_tokens"])
        # The default fixture has no model_config.json and no fatal risk
        # before result_summary generation, so _write_result_summary runs
        # and RESULT_SUMMARY_JSON/TXT are confirmed alongside the four
        # always-required tokens. No other conditional branch (paper
        # preflight, result consistency, release check, validation summary
        # needs an existing output/validation dir which this fixture never
        # creates) is active for this default fixture.
        self.assertEqual(completed, self._DEFAULT_FIXTURE_COMPLETED_TOKENS)
        self.assertTrue(provenance["source_write_registry_complete"])
        self.assertEqual(provenance["source_registry_errors"], [])
        self.assertEqual(provenance["source_write_registry_scope"], run_case_module.RUN_CASE_SUCCESS_SCOPE)
        self.assertFalse(provenance["source_write_registry_authoritative"])
        self.assertEqual(
            provenance["source_write_registry_excluded_classes"],
            run_case_module.RUN_CASE_SUCCESS_EXCLUDED_CLASSES,
        )

    def test_normal_success_failed_observed_write_produces_registry_error(self) -> None:
        def _fake_write_error_log(run_dir, risks, exc=None):
            return None  # reports success without leaving a real file behind

        with mock.patch.object(run_case_module, "ROOT", new=self.fake_root), mock.patch.object(
            run_case_module, "run_subprocess_with_timeout", side_effect=self._fake_subprocess_result
        ), mock.patch.object(run_case_module, "_write_validation_summary"), mock.patch.object(
            run_case_module, "_run_core_quality_gates"
        ), mock.patch.object(
            run_case_module, "_write_f2_1_evidence"
        ), mock.patch.object(
            run_case_module, "_write_error_log", side_effect=_fake_write_error_log
        ):
            result = run_case_module.run_case("run", self.case_dir, 5)

        provenance = result["source_provenance"]
        self.assertFalse(provenance["source_write_registry_complete"])
        self.assertTrue(provenance["source_registry_errors"])
        completed = set(provenance["source_completed_write_tokens"])
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_ERROR_LOG, completed)
        # The other three still completed fine; only the faked write failed.
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_RUN_CONFIG, completed)
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_COMPLIANCE_QUEUE, completed)
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_COMPLIANCE_QUEUE_SNAPSHOT, completed)

    def test_normal_success_promotion_outputs_excluded_from_completed_tokens(self) -> None:
        result = self._run()
        provenance = result["source_provenance"]
        completed = set(provenance["source_completed_write_tokens"])
        self.assertEqual(completed, self._DEFAULT_FIXTURE_COMPLETED_TOKENS)
        # No path fragment associated with safe_promote's backup directory
        # appears anywhere in the registry's observed state.
        # data_health/pdf_checklist/qg_report/manual_review_todo/
        # assets_manifest ARE expected here (P6-PV1B2A3B tracks the former
        # as conditional writes, P6-PV1B2A4B tracks the manifest write for
        # this default fixture), so they are deliberately not forbidden.
        written = set(provenance["source_written_repo_paths"]) | set(
            provenance["source_external_written_paths"]
        )
        forbidden_fragments = ("backup_before_promotion",)
        for path in written:
            for fragment in forbidden_fragments:
                self.assertNotIn(fragment, path)

    def test_normal_success_final_files_only_in_expected_paths(self) -> None:
        result = self._run()
        run_dir = self.fake_root / result["run_dir"]
        provenance = result["source_provenance"]

        expected = set(provenance["source_expected_post_snapshot_paths"])
        risk_json = run_case_module.rel_path(run_dir / "risk_summary.json")
        risk_md = run_case_module.rel_path(run_dir / "risk_summary.md")
        run_result = run_case_module.rel_path(run_dir / "run_result.json")
        evidence_map = run_case_module.rel_path(run_dir / "evidence_map.json")
        manifests_evidence_map = run_case_module.rel_path(self.fake_root / "manifests" / "evidence_map.json")

        self.assertIn(risk_json, expected)
        self.assertIn(risk_md, expected)
        self.assertIn(run_result, expected)
        self.assertIn(evidence_map, expected)
        if result["status"] == "ok":
            self.assertIn(manifests_evidence_map, expected)
        self.assertFalse(provenance["source_expected_post_snapshot_write_verified"])

        written_and_external = set(provenance["source_written_repo_paths"]) | set(
            provenance["source_external_written_paths"]
        )
        for final_path in (risk_json, risk_md, run_result, evidence_map):
            self.assertNotIn(final_path, written_and_external)
        completed = set(provenance["source_completed_write_tokens"])
        self.assertEqual(completed, self._DEFAULT_FIXTURE_COMPLETED_TOKENS)

    def test_normal_success_risk_summary_and_run_result_provenance_match(self) -> None:
        result = self._run()
        run_dir = self.fake_root / result["run_dir"]
        risk = json.loads((run_dir / "risk_summary.json").read_text(encoding="utf-8"))
        persisted_result = json.loads((run_dir / "run_result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["source_provenance"], risk["source_provenance"])
        self.assertEqual(result["source_provenance"], persisted_result["source_provenance"])
        for field in self._FORBIDDEN_PROVENANCE_FIELDS:
            self.assertNotIn(field, result["source_provenance"])

    def test_normal_success_does_not_mutate_real_repo_files(self) -> None:
        real_root = run_case_module.ROOT
        tracked_paths = [
            real_root / "compliance" / "compliance_todo_queue.json",
            real_root / "manifests" / "evidence_map.json",
            real_root / "manifests" / "assets_manifest.json",
            real_root / "release_authorization.json",
        ]
        before_bytes = {path: path.read_bytes() for path in tracked_paths if path.is_file()}
        before_git_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=real_root,
            capture_output=True,
            text=True,
            check=False,
        ).stdout

        self._run(stage="release-with-risk")

        after_git_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=real_root,
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        self.assertEqual(before_git_status, after_git_status)
        for path, original in before_bytes.items():
            self.assertEqual(path.read_bytes(), original)

    # ---- P6-PV1B2A3A2: conditional direct writes ----

    def test_normal_success_result_summary_not_registered_when_fatal_before_generation(self) -> None:
        # scan_structured_result_files flags an existing NaN in case output,
        # which becomes a fatal risk before result_summary generation is
        # ever attempted — so RESULT_SUMMARY_JSON/TXT must never be
        # confirmed, and must not be demanded as required either.
        bad_result = self.case_dir / "output" / "results" / "existing.json"
        bad_result.parent.mkdir(parents=True, exist_ok=True)
        bad_result.write_text(json.dumps({"value": float("nan")}), encoding="utf-8")
        # json.dumps can't emit NaN by default in strict mode, but Python's
        # default allow_nan=True does — this produces literal `NaN` in the
        # file, which is exactly what scan_structured_result_files flags.
        result = self._run()
        provenance = result["source_provenance"]
        completed = set(provenance["source_completed_write_tokens"])
        required = set(provenance["source_required_write_tokens"])
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_RESULT_SUMMARY_JSON, completed)
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_RESULT_SUMMARY_TXT, completed)
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_RESULT_SUMMARY_JSON, required)
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_RESULT_SUMMARY_TXT, required)
        # The four always-required tokens still complete regardless.
        self.assertTrue(provenance["source_write_registry_complete"])

    def _enable_paper_preflight(self) -> None:
        paper_dir = self.case_dir / "paper"
        paper_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(
            paper_dir / "paper_preflight_config.json",
            {
                "version": "1.0",
                "enabled": True,
                "submission_tier": "training",
                "submitted_text_sources": [],
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

    def test_normal_success_paper_preflight_writes_registered_when_enabled(self) -> None:
        self._enable_paper_preflight()
        result = self._run()
        run_dir = self.fake_root / result["run_dir"]
        provenance = result["source_provenance"]

        self.assertTrue((run_dir / "effective_paper_config.json").is_file())
        self.assertTrue((run_dir / "output" / "validation" / "paper_hard_error_report.json").is_file())
        completed = set(provenance["source_completed_write_tokens"])
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_EFFECTIVE_PAPER_CONFIG, completed)
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_PAPER_PREFLIGHT_REPORT, completed)
        self.assertTrue(provenance["source_write_registry_complete"])

    def test_normal_success_paper_preflight_not_registered_when_disabled(self) -> None:
        result = self._run()
        provenance = result["source_provenance"]
        completed = set(provenance["source_completed_write_tokens"])
        required = set(provenance["source_required_write_tokens"])
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_EFFECTIVE_PAPER_CONFIG, completed)
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_PAPER_PREFLIGHT_REPORT, completed)
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_EFFECTIVE_PAPER_CONFIG, required)
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_PAPER_PREFLIGHT_REPORT, required)

    def _enable_result_consistency(self) -> None:
        config = json.loads((self.case_dir / "case_config.json").read_text(encoding="utf-8"))
        config["result_consistency_enabled"] = True
        self._write_json(self.case_dir / "case_config.json", config)

    def test_normal_success_result_consistency_report_registered_when_enabled(self) -> None:
        self._enable_result_consistency()
        result = self._run()
        run_dir = self.fake_root / result["run_dir"]
        provenance = result["source_provenance"]

        self.assertTrue((run_dir / "output" / "validation" / "result_consistency_report.json").is_file())
        completed = set(provenance["source_completed_write_tokens"])
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_RESULT_CONSISTENCY_REPORT, completed)
        self.assertTrue(provenance["source_write_registry_complete"])

    def test_normal_success_result_consistency_report_not_registered_when_disabled(self) -> None:
        result = self._run()
        provenance = result["source_provenance"]
        completed = set(provenance["source_completed_write_tokens"])
        required = set(provenance["source_required_write_tokens"])
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_RESULT_CONSISTENCY_REPORT, completed)
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_RESULT_CONSISTENCY_REPORT, required)

    def test_normal_success_xval_never_registered(self) -> None:
        # Even with xval_independent enabled, no XVAL token exists in this
        # slice at all — validate_xval_independent_check only consumes an
        # existing artifact, it never writes a new report file, so this
        # slice deliberately defines no token for it (see plan item 5).
        config = json.loads((self.case_dir / "case_config.json").read_text(encoding="utf-8"))
        config["xval_independent_enabled"] = True
        self._write_json(self.case_dir / "case_config.json", config)
        result = self._run()
        provenance = result["source_provenance"]
        completed = set(provenance["source_completed_write_tokens"])
        for token in completed:
            self.assertNotIn("XVAL", token)

    def test_normal_success_release_check_registered_only_for_release_stage(self) -> None:
        result = self._run(stage="release-with-risk")
        run_dir = self.fake_root / result["run_dir"]
        provenance = result["source_provenance"]

        self.assertTrue((run_dir / "release_authorization_check.json").is_file())
        self.assertTrue((run_dir / "release_authorization_check.md").is_file())
        completed = set(provenance["source_completed_write_tokens"])
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_RELEASE_CHECK_JSON, completed)
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_RELEASE_CHECK_MD, completed)
        self.assertTrue(provenance["source_write_registry_complete"])

    def test_normal_success_release_check_not_registered_for_default_stage(self) -> None:
        result = self._run()
        provenance = result["source_provenance"]
        completed = set(provenance["source_completed_write_tokens"])
        required = set(provenance["source_required_write_tokens"])
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_RELEASE_CHECK_JSON, completed)
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_RELEASE_CHECK_MD, completed)
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_RELEASE_CHECK_JSON, required)
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_RELEASE_CHECK_MD, required)

    def test_normal_success_validation_summary_registered_when_result_summary_present(self) -> None:
        result = self._run()
        provenance = result["source_provenance"]
        completed = set(provenance["source_completed_write_tokens"])
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_VALIDATION_SUMMARY_JSON, completed)
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_VALIDATION_SUMMARY_MD, completed)
        # The top-level run_dir/pdf_checklist.json is legitimately tracked
        # (PDF_CHECKLIST_JSON_WRITE) as of P6-PV1B2A3B — only the copy
        # _write_validation_summary makes into output/validation/ remains
        # unregistered, which is what this assertion actually guards.
        written = set(provenance["source_written_repo_paths"]) | set(
            provenance["source_external_written_paths"]
        )
        for path in written:
            self.assertNotIn("output/validation/pdf_checklist", path)

    def test_normal_success_failed_conditional_write_produces_registry_error(self) -> None:
        # _run_paper_preflight_if_enabled returns a non-None report (the
        # branch's own success signal) but the fake never actually writes
        # effective_paper_config.json to disk — record_write must catch
        # this and mark the token incomplete, not silently trust the
        # return-value signal alone.
        self._enable_paper_preflight()

        def _fake_paper_preflight(*, case_dir, run_dir, risks, effective_settings=None):
            return {"status": "PASS"}

        with mock.patch.object(run_case_module, "ROOT", new=self.fake_root), mock.patch.object(
            run_case_module, "run_subprocess_with_timeout", side_effect=self._fake_subprocess_result
        ), mock.patch.object(
            run_case_module, "_write_validation_summary", side_effect=self._fake_write_validation_summary
        ), mock.patch.object(
            run_case_module, "_run_core_quality_gates"
        ), mock.patch.object(
            run_case_module, "_write_f2_1_evidence"
        ), mock.patch.object(
            run_case_module, "_run_paper_preflight_if_enabled", side_effect=_fake_paper_preflight
        ):
            result = run_case_module.run_case("run", self.case_dir, 5)

        provenance = result["source_provenance"]
        self.assertFalse(provenance["source_write_registry_complete"])
        self.assertTrue(provenance["source_registry_errors"])
        completed = set(provenance["source_completed_write_tokens"])
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_EFFECTIVE_PAPER_CONFIG, completed)
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_PAPER_PREFLIGHT_REPORT, completed)
        # The four always-required tokens are unaffected by this failure.
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_RUN_CONFIG, completed)
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_ERROR_LOG, completed)

    def test_normal_success_scope_is_all_pre_snapshot_writes(self) -> None:
        result = self._run()
        provenance = result["source_provenance"]
        self.assertEqual(provenance["source_write_registry_excluded_classes"], [])
        self.assertEqual(
            provenance["source_write_registry_scope"],
            "ALL_PRE_SNAPSHOT_WRITES",
        )
        self.assertFalse(provenance["source_write_registry_authoritative"])

    def test_normal_success_conditional_writes_do_not_break_provenance_consistency(self) -> None:
        self._enable_paper_preflight()
        self._enable_result_consistency()
        result = self._run(stage="release-with-risk")
        run_dir = self.fake_root / result["run_dir"]
        risk = json.loads((run_dir / "risk_summary.json").read_text(encoding="utf-8"))
        persisted_result = json.loads((run_dir / "run_result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["source_provenance"], risk["source_provenance"])
        self.assertEqual(result["source_provenance"], persisted_result["source_provenance"])
        # A richer set of conditional tokens should be present here than in
        # the default fixture, confirming these branches really did stack.
        completed = set(result["source_provenance"]["source_completed_write_tokens"])
        self.assertTrue(
            {
                run_case_module.RUN_CASE_SUCCESS_TOKEN_EFFECTIVE_PAPER_CONFIG,
                run_case_module.RUN_CASE_SUCCESS_TOKEN_PAPER_PREFLIGHT_REPORT,
                run_case_module.RUN_CASE_SUCCESS_TOKEN_RESULT_CONSISTENCY_REPORT,
                run_case_module.RUN_CASE_SUCCESS_TOKEN_RELEASE_CHECK_JSON,
                run_case_module.RUN_CASE_SUCCESS_TOKEN_RELEASE_CHECK_MD,
            }.issubset(completed)
        )

    # ---- P6-PV1B2A3B: Data Health / PDF Checklist / Quality Gates ----

    def test_normal_success_data_health_writes_registered(self) -> None:
        result = self._run()
        run_dir = self.fake_root / result["run_dir"]
        provenance = result["source_provenance"]

        self.assertTrue((run_dir / "subprocess_logs" / "data_health_check.log").is_file())
        self.assertTrue((run_dir / "data_health_report.json").is_file())
        self.assertTrue((run_dir / "data_health_report.md").is_file())
        completed = set(provenance["source_completed_write_tokens"])
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_DATA_HEALTH_LOG, completed)
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_DATA_HEALTH_JSON, completed)
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_DATA_HEALTH_MD, completed)
        self.assertTrue(provenance["source_write_registry_complete"])

    def test_normal_success_pdf_checklist_writes_registered(self) -> None:
        result = self._run()
        run_dir = self.fake_root / result["run_dir"]
        provenance = result["source_provenance"]

        self.assertTrue((run_dir / "subprocess_logs" / "pdf_checklist.log").is_file())
        self.assertTrue((run_dir / "pdf_checklist.json").is_file())
        self.assertTrue((run_dir / "pdf_checklist.md").is_file())
        completed = set(provenance["source_completed_write_tokens"])
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_PDF_CHECKLIST_LOG, completed)
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_PDF_CHECKLIST_JSON, completed)
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_PDF_CHECKLIST_MD, completed)
        self.assertTrue(provenance["source_write_registry_complete"])
        written = set(provenance["source_written_repo_paths"]) | set(
            provenance["source_external_written_paths"]
        )
        for path in written:
            self.assertNotIn("output/validation/pdf_checklist", path)

    def test_normal_success_quality_gate_writes_registered(self) -> None:
        result = self._run()
        run_dir = self.fake_root / result["run_dir"]
        provenance = result["source_provenance"]

        quality_dir = run_dir / "output" / "quality"
        self.assertTrue((quality_dir / "qg_report.json").is_file())
        self.assertTrue((quality_dir / "manual_review_todo.json").is_file())
        self.assertTrue((quality_dir / "qg_report.md").is_file())
        completed = set(provenance["source_completed_write_tokens"])
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_QG_REPORT_JSON, completed)
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_QG_MANUAL_REVIEW_TODO, completed)
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_QG_REPORT_MD, completed)
        self.assertTrue(provenance["source_write_registry_complete"])

    def test_normal_success_data_health_missing_report_produces_registry_error(self) -> None:
        # Simulates a subprocess that exits cleanly (log written, returncode
        # 0) but never produces its target report files — proving the
        # registry does not trust the subprocess result alone.
        def _fake_subprocess_log_only(args, timeout_seconds, cwd, log_path):
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("fake log, no report produced\n", encoding="utf-8")
            return {"command": args, "returncode": 0, "timed_out": False, "log_path": str(log_path)}

        with mock.patch.object(run_case_module, "ROOT", new=self.fake_root), mock.patch.object(
            run_case_module, "run_subprocess_with_timeout", side_effect=_fake_subprocess_log_only
        ), mock.patch.object(
            run_case_module, "_write_validation_summary", side_effect=self._fake_write_validation_summary
        ), mock.patch.object(
            run_case_module, "_run_core_quality_gates", side_effect=self._fake_run_core_quality_gates
        ), mock.patch.object(
            run_case_module, "_write_f2_1_evidence"
        ):
            result = run_case_module.run_case("run", self.case_dir, 5)

        provenance = result["source_provenance"]
        completed = set(provenance["source_completed_write_tokens"])
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_DATA_HEALTH_LOG, completed)
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_DATA_HEALTH_JSON, completed)
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_DATA_HEALTH_MD, completed)
        self.assertFalse(provenance["source_write_registry_complete"])
        self.assertTrue(provenance["source_registry_errors"])

    def test_normal_success_pdf_checklist_missing_output_produces_registry_error(self) -> None:
        # PDF Checklist's subprocess result looks normal, but the fake never
        # calls generate_pdf_checklist, so the JSON/MD targets are missing —
        # fail-closed, same pattern as the Data Health case above.
        def _fake_subprocess_log_only(args, timeout_seconds, cwd, log_path):
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("fake log, no report produced\n", encoding="utf-8")
            return {"command": args, "returncode": 0, "timed_out": False, "log_path": str(log_path)}

        def _fake_subprocess_data_health_only(args, timeout_seconds, cwd, log_path):
            script_name = Path(args[1]).name
            log_path.parent.mkdir(parents=True, exist_ok=True)
            if script_name == "data_health_check.py":
                case_dir = Path(args[args.index("--case-dir") + 1])
                run_dir = Path(args[args.index("--run-dir") + 1])
                import data_health_check

                data_health_check.run_data_health_check(case_dir, run_dir)
            log_path.write_text("fake log\n", encoding="utf-8")
            return {"command": args, "returncode": 0, "timed_out": False, "log_path": str(log_path)}

        with mock.patch.object(run_case_module, "ROOT", new=self.fake_root), mock.patch.object(
            run_case_module, "run_subprocess_with_timeout", side_effect=_fake_subprocess_data_health_only
        ), mock.patch.object(
            run_case_module, "_write_validation_summary", side_effect=self._fake_write_validation_summary
        ), mock.patch.object(
            run_case_module, "_run_core_quality_gates", side_effect=self._fake_run_core_quality_gates
        ), mock.patch.object(
            run_case_module, "_write_f2_1_evidence"
        ):
            result = run_case_module.run_case("run", self.case_dir, 5)

        provenance = result["source_provenance"]
        completed = set(provenance["source_completed_write_tokens"])
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_PDF_CHECKLIST_LOG, completed)
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_PDF_CHECKLIST_JSON, completed)
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_PDF_CHECKLIST_MD, completed)
        self.assertFalse(provenance["source_write_registry_complete"])
        self.assertTrue(provenance["source_registry_errors"])

    def test_normal_success_quality_gate_missing_file_produces_registry_error(self) -> None:
        # Calls the real run_quality_gates (so two of the three files are
        # genuinely written), then deletes one report file to simulate
        # "the function returned a normal payload but one output is
        # missing" — the registry must not trust the return value alone.
        def _fake_run_core_quality_gates_missing_md(run_dir, case_dir, run_id, risks):
            from quality_gates import run_quality_gates

            report = run_quality_gates(case_dir=case_dir, run_dir=run_dir, run_id=run_id, root=self.fake_root)
            (run_dir / "output" / "quality" / "qg_report.md").unlink()
            return report

        with mock.patch.object(run_case_module, "ROOT", new=self.fake_root), mock.patch.object(
            run_case_module, "run_subprocess_with_timeout", side_effect=self._fake_subprocess_result
        ), mock.patch.object(
            run_case_module, "_write_validation_summary", side_effect=self._fake_write_validation_summary
        ), mock.patch.object(
            run_case_module, "_run_core_quality_gates", side_effect=_fake_run_core_quality_gates_missing_md
        ), mock.patch.object(
            run_case_module, "_write_f2_1_evidence"
        ):
            result = run_case_module.run_case("run", self.case_dir, 5)

        provenance = result["source_provenance"]
        completed = set(provenance["source_completed_write_tokens"])
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_QG_REPORT_JSON, completed)
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_QG_MANUAL_REVIEW_TODO, completed)
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_QG_REPORT_MD, completed)
        self.assertFalse(provenance["source_write_registry_complete"])
        self.assertTrue(provenance["source_registry_errors"])

    # ---- P6-PV1B2A4A: safe_promote target/backup diff registry ----

    def test_normal_success_safe_promote_created_target_registered(self) -> None:
        # Default fixture: case_dir/output does not exist before this run,
        # so result_summary.json/.txt are both first-time creates.
        result = self._run()
        run_dir = self.fake_root / result["run_dir"]
        provenance = result["source_provenance"]

        source_json = (run_dir / "output" / "result_summary.json").read_bytes()
        target_json = (self.case_dir / "output" / "result_summary.json").read_bytes()
        self.assertEqual(source_json, target_json)
        source_txt = (run_dir / "output" / "result_summary.txt").read_bytes()
        target_txt = (self.case_dir / "output" / "result_summary.txt").read_bytes()
        self.assertEqual(source_txt, target_txt)

        backup_dir = run_dir / "backup_before_promotion"
        self.assertFalse(backup_dir.exists() and any(backup_dir.rglob("*")))

        completed = set(provenance["source_completed_write_tokens"])
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_SAFE_PROMOTE_DIFF_COMPLETE, completed)
        self.assertTrue(provenance["source_write_registry_complete"])

    def test_normal_success_safe_promote_overwritten_target_registered(self) -> None:
        # Must be a *valid* result_summary.json (scan_structured_result_files
        # validates this specific filename against RESULT_SUMMARY_REQUIRED_FIELDS
        # and would otherwise raise a fatal risk before safe_promote ever runs),
        # just with different content than what this run will generate.
        old_content = json.dumps(
            {
                "schema_version": "old_fixture_v1",
                "generated_at": "2020-01-01T00:00:00+00:00",
                "run_id": "old_run",
                "case_id": "normal_success_fixture",
                "final_submit_created": False,
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        target = self.case_dir / "output" / "result_summary.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(old_content)

        result = self._run()
        run_dir = self.fake_root / result["run_dir"]
        provenance = result["source_provenance"]

        new_source = (run_dir / "output" / "result_summary.json").read_bytes()
        new_target = target.read_bytes()
        self.assertEqual(new_source, new_target)
        self.assertNotEqual(old_content, new_target)

        backup_file = run_dir / "backup_before_promotion" / "result_summary.json"
        self.assertTrue(backup_file.is_file())
        self.assertEqual(backup_file.read_bytes(), old_content)

        completed = set(provenance["source_completed_write_tokens"])
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_SAFE_PROMOTE_DIFF_COMPLETE, completed)
        self.assertTrue(provenance["source_write_registry_complete"])

    def test_normal_success_safe_promote_excludes_unrelated_case_output_files(self) -> None:
        unrelated = self.case_dir / "output" / "unrelated.txt"
        unrelated.parent.mkdir(parents=True, exist_ok=True)
        unrelated.write_text("not touched by this run", encoding="utf-8")

        result = self._run()
        provenance = result["source_provenance"]

        # The unrelated file survives untouched.
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "not touched by this run")
        written = set(provenance["source_written_repo_paths"]) | set(
            provenance["source_external_written_paths"]
        )
        for path in written:
            self.assertNotIn("unrelated.txt", path)
        self.assertTrue(provenance["source_write_registry_complete"])

    def test_normal_success_safe_promote_missing_promoted_entry_incomplete(self) -> None:
        real_safe_promote = run_case_module.safe_promote

        def _fake_safe_promote_missing_entry(run_output_dir, case_output_dir, backup_dir):
            promoted = real_safe_promote(run_output_dir, case_output_dir, backup_dir)
            return promoted[1:] if len(promoted) > 1 else []

        with mock.patch.object(run_case_module, "ROOT", new=self.fake_root), mock.patch.object(
            run_case_module, "run_subprocess_with_timeout", side_effect=self._fake_subprocess_result
        ), mock.patch.object(
            run_case_module, "_write_validation_summary", side_effect=self._fake_write_validation_summary
        ), mock.patch.object(
            run_case_module, "_run_core_quality_gates", side_effect=self._fake_run_core_quality_gates
        ), mock.patch.object(
            run_case_module, "_write_f2_1_evidence"
        ), mock.patch.object(
            run_case_module, "safe_promote", side_effect=_fake_safe_promote_missing_entry
        ):
            result = run_case_module.run_case("run", self.case_dir, 5)

        provenance = result["source_provenance"]
        completed = set(provenance["source_completed_write_tokens"])
        required = set(provenance["source_required_write_tokens"])
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_SAFE_PROMOTE_DIFF_COMPLETE, required)
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_SAFE_PROMOTE_DIFF_COMPLETE, completed)
        self.assertFalse(provenance["source_write_registry_complete"])

    def test_normal_success_safe_promote_extra_promoted_entry_incomplete(self) -> None:
        real_safe_promote = run_case_module.safe_promote

        def _fake_safe_promote_extra_entry(run_output_dir, case_output_dir, backup_dir):
            promoted = real_safe_promote(run_output_dir, case_output_dir, backup_dir)
            return promoted + ["never_actually_promoted.json"]

        with mock.patch.object(run_case_module, "ROOT", new=self.fake_root), mock.patch.object(
            run_case_module, "run_subprocess_with_timeout", side_effect=self._fake_subprocess_result
        ), mock.patch.object(
            run_case_module, "_write_validation_summary", side_effect=self._fake_write_validation_summary
        ), mock.patch.object(
            run_case_module, "_run_core_quality_gates", side_effect=self._fake_run_core_quality_gates
        ), mock.patch.object(
            run_case_module, "_write_f2_1_evidence"
        ), mock.patch.object(
            run_case_module, "safe_promote", side_effect=_fake_safe_promote_extra_entry
        ):
            result = run_case_module.run_case("run", self.case_dir, 5)

        provenance = result["source_provenance"]
        completed = set(provenance["source_completed_write_tokens"])
        required = set(provenance["source_required_write_tokens"])
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_SAFE_PROMOTE_DIFF_COMPLETE, required)
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_SAFE_PROMOTE_DIFF_COMPLETE, completed)
        self.assertFalse(provenance["source_write_registry_complete"])

    def test_normal_success_safe_promote_target_hash_mismatch_incomplete(self) -> None:
        real_safe_promote = run_case_module.safe_promote

        def _fake_safe_promote_corrupt_target(run_output_dir, case_output_dir, backup_dir):
            promoted = real_safe_promote(run_output_dir, case_output_dir, backup_dir)
            target = case_output_dir / "result_summary.json"
            if target.is_file():
                target.write_text('{"tampered": true}', encoding="utf-8")
            return promoted

        with mock.patch.object(run_case_module, "ROOT", new=self.fake_root), mock.patch.object(
            run_case_module, "run_subprocess_with_timeout", side_effect=self._fake_subprocess_result
        ), mock.patch.object(
            run_case_module, "_write_validation_summary", side_effect=self._fake_write_validation_summary
        ), mock.patch.object(
            run_case_module, "_run_core_quality_gates", side_effect=self._fake_run_core_quality_gates
        ), mock.patch.object(
            run_case_module, "_write_f2_1_evidence"
        ), mock.patch.object(
            run_case_module, "safe_promote", side_effect=_fake_safe_promote_corrupt_target
        ):
            result = run_case_module.run_case("run", self.case_dir, 5)

        provenance = result["source_provenance"]
        completed = set(provenance["source_completed_write_tokens"])
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_SAFE_PROMOTE_DIFF_COMPLETE, completed)
        self.assertFalse(provenance["source_write_registry_complete"])

    def test_normal_success_safe_promote_backup_missing_incomplete(self) -> None:
        # Must be a *valid* result_summary.json — see the overwritten-target
        # test above for why an arbitrary placeholder would trip a fatal
        # risk before safe_promote ever runs.
        old_content = json.dumps(
            {
                "schema_version": "old_fixture_v1",
                "generated_at": "2020-01-01T00:00:00+00:00",
                "run_id": "old_run",
                "case_id": "normal_success_fixture",
                "final_submit_created": False,
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        target = self.case_dir / "output" / "result_summary.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(old_content)

        real_safe_promote = run_case_module.safe_promote

        def _fake_safe_promote_delete_backup(run_output_dir, case_output_dir, backup_dir):
            promoted = real_safe_promote(run_output_dir, case_output_dir, backup_dir)
            backup_file = backup_dir / "result_summary.json"
            if backup_file.is_file():
                backup_file.unlink()
            return promoted

        with mock.patch.object(run_case_module, "ROOT", new=self.fake_root), mock.patch.object(
            run_case_module, "run_subprocess_with_timeout", side_effect=self._fake_subprocess_result
        ), mock.patch.object(
            run_case_module, "_write_validation_summary", side_effect=self._fake_write_validation_summary
        ), mock.patch.object(
            run_case_module, "_run_core_quality_gates", side_effect=self._fake_run_core_quality_gates
        ), mock.patch.object(
            run_case_module, "_write_f2_1_evidence"
        ), mock.patch.object(
            run_case_module, "safe_promote", side_effect=_fake_safe_promote_delete_backup
        ):
            result = run_case_module.run_case("run", self.case_dir, 5)

        provenance = result["source_provenance"]
        completed = set(provenance["source_completed_write_tokens"])
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_SAFE_PROMOTE_DIFF_COMPLETE, completed)
        self.assertFalse(provenance["source_write_registry_complete"])

    def test_normal_success_safe_promote_exception_does_not_complete_token(self) -> None:
        def _fake_safe_promote_raises(run_output_dir, case_output_dir, backup_dir):
            raise RuntimeError("fixture safe_promote failure")

        with mock.patch.object(run_case_module, "ROOT", new=self.fake_root), mock.patch.object(
            run_case_module, "run_subprocess_with_timeout", side_effect=self._fake_subprocess_result
        ), mock.patch.object(
            run_case_module, "_write_validation_summary", side_effect=self._fake_write_validation_summary
        ), mock.patch.object(
            run_case_module, "_run_core_quality_gates", side_effect=self._fake_run_core_quality_gates
        ), mock.patch.object(
            run_case_module, "_write_f2_1_evidence"
        ), mock.patch.object(
            run_case_module, "safe_promote", side_effect=_fake_safe_promote_raises
        ):
            result = run_case_module.run_case("run", self.case_dir, 5)

        # The exception is caught by run_case()'s own except-Exception tail
        # (a different registry entirely, RUN_CASE_EXCEPTION_REQUIRED_TOKENS)
        # — SAFE_PROMOTE_DIFF_COMPLETE (a RUN_CASE_SUCCESS_* token) must not
        # appear anywhere in this result.
        self.assertEqual(result["status"], "fatal")
        provenance = result["source_provenance"]
        completed = set(provenance["source_completed_write_tokens"])
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_SAFE_PROMOTE_DIFF_COMPLETE, completed)

    def test_normal_success_safe_promote_empty_run_output_is_noop(self) -> None:
        # run_output_dir (run_dir/output) is safe_promote's *entire* source
        # tree, recursively — not just result_summary.json. To genuinely
        # empty it, every writer that touches run_dir/output/** must be a
        # true no-op: _write_result_summary (result_summary.json/.txt),
        # _write_validation_summary (output/validation/*), and
        # _run_core_quality_gates (output/quality/*).
        def _fake_write_result_summary_noop(run_dir, case_dir, case_config, data_health):
            return {"schema_version": "noop", "status": "ok", "final_submit_created": False}

        def _fake_write_validation_summary_noop(run_dir, case_dir, risks, result_summary, pdf_check, *, strict_pdf=False):
            return None

        def _fake_run_core_quality_gates_noop(run_dir, case_dir, run_id, risks):
            return {"promotion_allowed": True, "results": []}

        with mock.patch.object(run_case_module, "ROOT", new=self.fake_root), mock.patch.object(
            run_case_module, "run_subprocess_with_timeout", side_effect=self._fake_subprocess_result
        ), mock.patch.object(
            run_case_module, "_write_validation_summary", side_effect=_fake_write_validation_summary_noop
        ), mock.patch.object(
            run_case_module, "_run_core_quality_gates", side_effect=_fake_run_core_quality_gates_noop
        ), mock.patch.object(
            run_case_module, "_write_f2_1_evidence"
        ), mock.patch.object(
            run_case_module, "_write_result_summary", side_effect=_fake_write_result_summary_noop
        ), mock.patch.object(
            run_case_module, "validate_result_summary_file", return_value=[]
        ):
            result = run_case_module.run_case("run", self.case_dir, 5)

        run_dir = self.fake_root / result["run_dir"]
        self.assertEqual(list((run_dir / "output").rglob("*")) if (run_dir / "output").exists() else [], [])
        provenance = result["source_provenance"]
        completed = set(provenance["source_completed_write_tokens"])
        required = set(provenance["source_required_write_tokens"])
        # The point of this test is specifically that an empty
        # run_output_dir never demands SAFE_PROMOTE_DIFF_COMPLETE — the
        # no-op mocks above intentionally break other conditional tokens'
        # completion too (they claim success via return value without
        # writing files), which is orthogonal to what this test verifies.
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_SAFE_PROMOTE_DIFF_COMPLETE, required)
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_SAFE_PROMOTE_DIFF_COMPLETE, completed)

    # ---- P6-PV1B2A4B: register_asset root binding / manifest integrity ----

    def test_normal_success_register_asset_writes_land_in_temp_root(self) -> None:
        # Default fixture: safe_promote promotes result_summary.json and
        # result_summary.txt, both matching name.startswith("result_summary"),
        # so register_asset fires twice with asset_type="result".
        result = self._run()
        run_id = result["run_id"]
        manifest_path = self.fake_root / "manifests" / "assets_manifest.json"
        self.assertTrue(manifest_path.is_file())
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = [item for item in payload["assets"] if item.get("run_id") == run_id]
        self.assertEqual(len(entries), 2)
        expected_paths = {
            run_case_module.rel_path(self.case_dir / "output" / "result_summary.json", self.fake_root),
            run_case_module.rel_path(self.case_dir / "output" / "result_summary.txt", self.fake_root),
        }
        for entry in entries:
            self.assertEqual(entry["type"], "result")
            self.assertEqual(entry["status"], "promoted")
            self.assertIn(entry["path"], expected_paths)

        provenance = result["source_provenance"]
        completed = set(provenance["source_completed_write_tokens"])
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_ASSETS_MANIFEST_WRITE, completed)
        self.assertTrue(provenance["source_write_registry_complete"])

    def test_normal_success_register_asset_figures_tables_pdf_entries_correct(self) -> None:
        figures_dir = self.case_dir / "output" / "figures"
        figures_dir.mkdir(parents=True, exist_ok=True)
        (figures_dir / "fig1.png").write_bytes(b"fake-png")
        tables_dir = self.case_dir / "output" / "tables"
        tables_dir.mkdir(parents=True, exist_ok=True)
        (tables_dir / "table1.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        paper_dir = self.case_dir / "paper"
        paper_dir.mkdir(parents=True, exist_ok=True)
        (paper_dir / "main.pdf").write_bytes(b"%PDF-fake")

        result = self._run()
        run_id = result["run_id"]
        manifest_path = self.fake_root / "manifests" / "assets_manifest.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = {
            (item["type"], item["path"], item["run_id"], item["status"])
            for item in payload["assets"]
            if item.get("run_id") == run_id
        }
        figure_path = run_case_module.rel_path(figures_dir / "fig1.png", self.fake_root)
        table_path = run_case_module.rel_path(tables_dir / "table1.csv", self.fake_root)
        pdf_path = run_case_module.rel_path(paper_dir / "main.pdf", self.fake_root)
        self.assertIn(("figure", figure_path, run_id, "existing_promoted"), entries)
        self.assertIn(("table", table_path, run_id, "existing_promoted"), entries)
        self.assertIn(("paper_pdf", pdf_path, run_id, "existing_promoted"), entries)

        provenance = result["source_provenance"]
        completed = set(provenance["source_completed_write_tokens"])
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_ASSETS_MANIFEST_WRITE, completed)

    def test_normal_success_register_asset_historical_entries_preserved(self) -> None:
        manifest_path = self.fake_root / "manifests" / "assets_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        historical_entry = {
            "asset_id": "historical-fixture-id",
            "type": "result",
            "path": "case/output/result_summary.json",
            "source_script": "run_case.py",
            "source_data": [],
            "run_id": "some_other_historical_run_id",
            "sha256": "0" * 64,
            "created_at": "2020-01-01T00:00:00+00:00",
            "status": "promoted",
        }
        self._write_json(
            manifest_path,
            {"schema_version": "assets_v1", "updated_at": "2020-01-01T00:00:00+00:00", "assets": [historical_entry]},
        )

        result = self._run()
        run_id = result["run_id"]
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertIn(historical_entry, payload["assets"])
        current_entries = [item for item in payload["assets"] if item.get("run_id") == run_id]
        self.assertEqual(len(current_entries), 2)

        provenance = result["source_provenance"]
        completed = set(provenance["source_completed_write_tokens"])
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_ASSETS_MANIFEST_WRITE, completed)

    def test_normal_success_register_asset_duplicate_expected_uses_multiset(self) -> None:
        # Two distinct figure files produce two distinct expected entries —
        # exercising Counter's multiset semantics (cardinality, not just
        # set membership) rather than a literal duplicate-path case.
        figures_dir = self.case_dir / "output" / "figures"
        figures_dir.mkdir(parents=True, exist_ok=True)
        (figures_dir / "fig1.png").write_bytes(b"fake-png-1")
        (figures_dir / "fig2.png").write_bytes(b"fake-png-2")

        result = self._run()
        run_id = result["run_id"]
        manifest_path = self.fake_root / "manifests" / "assets_manifest.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        figure_entries = [
            item for item in payload["assets"] if item.get("run_id") == run_id and item.get("type") == "figure"
        ]
        self.assertEqual(len(figure_entries), 2)

        provenance = result["source_provenance"]
        completed = set(provenance["source_completed_write_tokens"])
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_ASSETS_MANIFEST_WRITE, completed)

    def test_normal_success_register_asset_missing_entry_incomplete(self) -> None:
        # A figure exists on disk (so it's expected), but register_asset is
        # patched to skip figure registrations entirely — the manifest ends
        # up missing that entry, so the multiset comparison must fail.
        real_register_asset = run_case_module.register_asset

        def _fake_register_asset_skip_figure(*, asset_type, **kwargs):
            if asset_type == "figure":
                return {}
            return real_register_asset(asset_type=asset_type, **kwargs)

        figures_dir = self.case_dir / "output" / "figures"
        figures_dir.mkdir(parents=True, exist_ok=True)
        (figures_dir / "fig1.png").write_bytes(b"fake-png")

        with mock.patch.object(run_case_module, "ROOT", new=self.fake_root), mock.patch.object(
            run_case_module, "run_subprocess_with_timeout", side_effect=self._fake_subprocess_result
        ), mock.patch.object(
            run_case_module, "_write_validation_summary", side_effect=self._fake_write_validation_summary
        ), mock.patch.object(
            run_case_module, "_run_core_quality_gates", side_effect=self._fake_run_core_quality_gates
        ), mock.patch.object(
            run_case_module, "_write_f2_1_evidence"
        ), mock.patch.object(
            run_case_module, "register_asset", side_effect=_fake_register_asset_skip_figure
        ):
            result = run_case_module.run_case("run", self.case_dir, 5)

        provenance = result["source_provenance"]
        required = set(provenance["source_required_write_tokens"])
        completed = set(provenance["source_completed_write_tokens"])
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_ASSETS_MANIFEST_WRITE, required)
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_ASSETS_MANIFEST_WRITE, completed)
        self.assertFalse(provenance["source_write_registry_complete"])

    def test_normal_success_register_asset_extra_entry_incomplete(self) -> None:
        # register_asset is patched to register one extra, unexpected entry
        # alongside every genuine call — the manifest ends up with more
        # current-run entries than expected, so the comparison must fail.
        real_register_asset = run_case_module.register_asset
        call_count = {"n": 0}

        def _fake_register_asset_with_extra(*, root, asset_type, path, source_script, source_data, run_id, status):
            call_count["n"] += 1
            entry = real_register_asset(
                root=root,
                asset_type=asset_type,
                path=path,
                source_script=source_script,
                source_data=source_data,
                run_id=run_id,
                status=status,
            )
            if call_count["n"] == 1:
                real_register_asset(
                    root=root,
                    asset_type=asset_type,
                    path=path,
                    source_script=source_script,
                    source_data=source_data,
                    run_id=run_id,
                    status="unexpected_extra_status",
                )
            return entry

        with mock.patch.object(run_case_module, "ROOT", new=self.fake_root), mock.patch.object(
            run_case_module, "run_subprocess_with_timeout", side_effect=self._fake_subprocess_result
        ), mock.patch.object(
            run_case_module, "_write_validation_summary", side_effect=self._fake_write_validation_summary
        ), mock.patch.object(
            run_case_module, "_run_core_quality_gates", side_effect=self._fake_run_core_quality_gates
        ), mock.patch.object(
            run_case_module, "_write_f2_1_evidence"
        ), mock.patch.object(
            run_case_module, "register_asset", side_effect=_fake_register_asset_with_extra
        ):
            result = run_case_module.run_case("run", self.case_dir, 5)

        provenance = result["source_provenance"]
        required = set(provenance["source_required_write_tokens"])
        completed = set(provenance["source_completed_write_tokens"])
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_ASSETS_MANIFEST_WRITE, required)
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_ASSETS_MANIFEST_WRITE, completed)
        self.assertFalse(provenance["source_write_registry_complete"])

    def test_normal_success_register_asset_field_mismatch_incomplete(self) -> None:
        # register_asset is patched to write a tampered status for the first
        # call — the manifest entry no longer matches the expected tuple, so
        # the comparison must fail even though the call count matches.
        real_register_asset = run_case_module.register_asset
        call_count = {"n": 0}

        def _fake_register_asset_tamper_status(*, root, asset_type, path, source_script, source_data, run_id, status):
            call_count["n"] += 1
            if call_count["n"] == 1:
                status = "tampered_status"
            return real_register_asset(
                root=root,
                asset_type=asset_type,
                path=path,
                source_script=source_script,
                source_data=source_data,
                run_id=run_id,
                status=status,
            )

        with mock.patch.object(run_case_module, "ROOT", new=self.fake_root), mock.patch.object(
            run_case_module, "run_subprocess_with_timeout", side_effect=self._fake_subprocess_result
        ), mock.patch.object(
            run_case_module, "_write_validation_summary", side_effect=self._fake_write_validation_summary
        ), mock.patch.object(
            run_case_module, "_run_core_quality_gates", side_effect=self._fake_run_core_quality_gates
        ), mock.patch.object(
            run_case_module, "_write_f2_1_evidence"
        ), mock.patch.object(
            run_case_module, "register_asset", side_effect=_fake_register_asset_tamper_status
        ):
            result = run_case_module.run_case("run", self.case_dir, 5)

        provenance = result["source_provenance"]
        required = set(provenance["source_required_write_tokens"])
        completed = set(provenance["source_completed_write_tokens"])
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_ASSETS_MANIFEST_WRITE, required)
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_ASSETS_MANIFEST_WRITE, completed)
        self.assertFalse(provenance["source_write_registry_complete"])

    def test_normal_success_register_asset_empty_expected_is_noop(self) -> None:
        # No figures/tables/paper_pdf, and safe_promote's promoted list is
        # forced empty, so expected_asset_entries stays empty — the token
        # must be neither required nor completed, and register_asset must
        # never be called.
        real_safe_promote = run_case_module.safe_promote

        def _fake_safe_promote_no_promotions(run_output_dir, case_output_dir, backup_dir):
            real_safe_promote(run_output_dir, case_output_dir, backup_dir)
            return []

        with mock.patch.object(run_case_module, "ROOT", new=self.fake_root), mock.patch.object(
            run_case_module, "run_subprocess_with_timeout", side_effect=self._fake_subprocess_result
        ), mock.patch.object(
            run_case_module, "_write_validation_summary", side_effect=self._fake_write_validation_summary
        ), mock.patch.object(
            run_case_module, "_run_core_quality_gates", side_effect=self._fake_run_core_quality_gates
        ), mock.patch.object(
            run_case_module, "_write_f2_1_evidence"
        ), mock.patch.object(
            run_case_module, "safe_promote", side_effect=_fake_safe_promote_no_promotions
        ), mock.patch.object(
            run_case_module, "register_asset", side_effect=AssertionError("register_asset must not be called")
        ):
            result = run_case_module.run_case("run", self.case_dir, 5)

        provenance = result["source_provenance"]
        required = set(provenance["source_required_write_tokens"])
        completed = set(provenance["source_completed_write_tokens"])
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_ASSETS_MANIFEST_WRITE, required)
        self.assertNotIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_ASSETS_MANIFEST_WRITE, completed)

    def test_normal_success_register_asset_provenance_consistency(self) -> None:
        figures_dir = self.case_dir / "output" / "figures"
        figures_dir.mkdir(parents=True, exist_ok=True)
        (figures_dir / "fig1.png").write_bytes(b"fake-png")

        result = self._run()
        run_dir = self.fake_root / result["run_dir"]
        risk = json.loads((run_dir / "risk_summary.json").read_text(encoding="utf-8"))
        persisted_result = json.loads((run_dir / "run_result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["source_provenance"], risk["source_provenance"])
        self.assertEqual(result["source_provenance"], persisted_result["source_provenance"])
        completed = set(result["source_provenance"]["source_completed_write_tokens"])
        self.assertIn(run_case_module.RUN_CASE_SUCCESS_TOKEN_ASSETS_MANIFEST_WRITE, completed)


if __name__ == "__main__":
    unittest.main()
