from __future__ import annotations

import hashlib
import json
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pipeline_core.python_tools.final_freezer import (
    REQUIRED,
    _base_machine_blockers,
    _calculate_candidate_package_digest,
    _sha256_file,
    check_freeze_ready,
)


class FinalFreezerAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.repo_root = self.root / "repo"
        self.case_dir = self.repo_root / "case"
        self.external_dir = self.root / "external"
        self.case_dir.mkdir(parents=True)
        self.external_dir.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _write_config(self, **overrides: object) -> None:
        config = {
            "official_submission_mode": "enabled",
            "final_submit_allowed": True,
            "citation_ready": True,
            "formal_reference_allowed": True,
            "human_gate_status": "APPROVED",
            "final_submit_authorization_path": "human_review/final_submit_authorization.json",
            "required_final_artifacts": [],
        }
        config.update(overrides)
        self._write_json(self.case_dir / "case_config.json", config)

    def _write_visual_reasoning_trace(self, case_dir: Path | None = None) -> None:
        case_dir = case_dir or self.case_dir
        main_tex = case_dir / "paper" / "main.tex"
        figure_root = case_dir / "paper" / "writer_content" / "figures"
        main_tex.parent.mkdir(parents=True, exist_ok=True)
        figure_root.mkdir(parents=True, exist_ok=True)
        main_tex.write_text("formal paper", encoding="utf-8")
        figures = []
        for index in range(1, 5):
            figure = figure_root / f"fig{index}.png"
            figure.write_bytes(f"figure-{index}".encode("utf-8"))
            figures.append(figure)
        self._write_json(
            case_dir / "paper" / "visual_reasoning_trace_report.json",
            {
                "schema_version": "visual_reasoning_trace_report_v1",
                "generated_by": "paper_renderer",
                "status": "PASS",
                "policy_version": "controlled_writing_layer_v4",
                "manifest_mode": "CURRENT_FORMAL",
                "main_tex_sha256": hashlib.sha256(main_tex.read_bytes()).hexdigest(),
                "visual_reasoning": {
                    "status": "PASS",
                    "display_profile": "STRONG_EVIDENCE_STORY",
                    "task_count": 1,
                    "chain_count": 1,
                    "minimum_formal_figure_count": 2,
                    "formal_figure_count": 4,
                    "role_families_covered": [
                        "MODEL_STRUCTURE",
                        "INTERMEDIATE_PROCESS",
                        "RESULT_DIAGNOSTIC",
                    ],
                    "task_chains": [{
                        "task_id": "q1",
                        "visual_ids": ["fig1", "fig2", "fig3", "fig4"],
                    }],
                },
                "figure_assets": [
                    {
                        "figure_id": f"fig{index}",
                        "path": f"writer_content/figures/fig{index}.png",
                        "sha256": hashlib.sha256(figure.read_bytes()).hexdigest(),
                    }
                    for index, figure in enumerate(figures, 1)
                ],
            },
        )

    def _write_required_artifacts(self) -> None:
        for relative_path in REQUIRED:
            path = self.case_dir / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative_path != "paper/visual_reasoning_trace_report.json":
                path.write_text("present", encoding="utf-8")
        self._write_visual_reasoning_trace()
        (self.case_dir / "human_review" / "gate_4_final_submission.md").write_text(
            "HUMAN_GATE_STATUS: APPROVED\nAPPROVED_BY: test reviewer\nMANUAL_CONFIRMATION: reviewed manually\n",
            encoding="utf-8",
        )

    def _make_case_ready_for_intake(self) -> None:
        self._write_config()
        self._write_required_artifacts()

    def _contract(self, *, mode: str = "mock_test", status: str = "APPROVED", **overrides: object) -> dict:
        record = {
            "schema_version": "p6_ff_external_authorization_v1",
            "authorization_mode": mode,
            "authorization_status": status,
            "authorized_by_display_name": "test reviewer",
            "authorization_record_ref": "user-conversation://approval/fixture",
            "authorized_at": "2026-07-13T10:00:00+00:00",
            "candidate_manifest_path": "/external/candidate/manifest.json",
            "candidate_manifest_sha256": "a" * 64,
            "candidate_package_digest": "b" * 64,
            "source_commit": "c" * 40,
            "run_result_sha256": "d" * 64,
            "risk_summary_sha256": "e" * 64,
            "result_summary_sha256": "f" * 64,
            "paper_source_or_config_sha256": "1" * 64,
            "route_review_sha256": "2" * 64,
            "xval_artifact_sha256": "3" * 64,
            "claim_check_artifact_sha256": "4" * 64,
            "human_revision_artifact_sha256": "5" * 64,
            "authorization_scope": "final_freeze_readiness",
            "not_automatic_submission_statement": (
                "This authorization does not automatically submit or create final_submit."
            ),
            "revocation_or_supersession_ref": "none",
        }
        record.update(overrides)
        return record

    def _external_authorization(self, **overrides: object) -> Path:
        path = self.external_dir / "authorization.json"
        self._write_json(path, self._contract(**overrides))
        return path

    def _create_candidate_package(
        self,
        name: str = "candidate",
        files: tuple[tuple[str, str], ...] = (),
        manifest_extra: dict | None = None,
    ) -> Path:
        candidate_dir = self.external_dir / name
        candidate_dir.mkdir(parents=True)
        manifest = {
            "status": "candidate",
            "case_dir": "case",
            "case_identity": "case",
            "run_id": "fixture_run",
        }
        if manifest_extra:
            manifest.update(manifest_extra)
        self._write_json(candidate_dir / "manifest.json", manifest)
        for relative_path, content in files:
            path = candidate_dir / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return candidate_dir

    def _valid_provenance_verification(self, *, source_commit: str = "c" * 40, **overrides: object) -> dict:
        verification = {
            "source_provenance_required": True,
            "source_provenance_applicable": True,
            "source_provenance_legacy_compatibility": False,
            "source_provenance_sha256": "9" * 64,
            "source_provenance_finalization_passed": True,
            "post_snapshot_verification_passed": True,
            "run_id": "run_pv3_fixture",
            "source_commit": source_commit,
            "packaging_head_matches_source_commit": True,
            "packaging_git_operation_in_progress": False,
            "packaging_unexpected_status_entries": [],
            "blockers": [],
        }
        verification.update(overrides)
        return verification

    def _provenance_candidate_package(
        self,
        name: str = "candidate_provenance",
        *,
        files: tuple[tuple[str, str], ...] = (("payload.txt", "payload"),),
        verification_overrides: dict | None = None,
        source_commit: str = "c" * 40,
    ) -> Path:
        verification = self._valid_provenance_verification(source_commit=source_commit, **(verification_overrides or {}))
        return self._create_candidate_package(
            name, files=files, manifest_extra={"source_provenance_verification": verification}
        )

    def _external_candidate_authorization(self, candidate_dir: Path | None = None, **overrides: object) -> Path:
        candidate_dir = candidate_dir or self._create_candidate_package(files=(("payload.txt", "payload"),))
        manifest_path = candidate_dir / "manifest.json"
        overrides.setdefault("candidate_manifest_path", str(manifest_path))
        overrides.setdefault("candidate_manifest_sha256", _sha256_file(manifest_path))
        overrides.setdefault("candidate_package_digest", _calculate_candidate_package_digest(candidate_dir))
        record = self._contract(**overrides)
        path = self.external_dir / "authorization.json"
        self._write_json(path, record)
        return path

    def _candidate_package_authorization(
        self,
        candidate_dir: Path,
        relative_path: str,
        **overrides: object,
    ) -> Path:
        manifest_path = candidate_dir / "manifest.json"
        overrides.setdefault("candidate_manifest_path", str(manifest_path))
        overrides.setdefault("candidate_manifest_sha256", _sha256_file(manifest_path))
        overrides.setdefault("candidate_package_digest", _calculate_candidate_package_digest(candidate_dir))
        path = candidate_dir / relative_path
        self._write_json(path, self._contract(**overrides))
        return path

    def _check(self, authorization_path: Path | None = None, *, mode: str = "mock_test", dry_run: bool = True) -> dict:
        kwargs: dict[str, object] = {}
        if authorization_path is not None and authorization_path.is_file():
            record = json.loads(authorization_path.read_text(encoding="utf-8"))
            candidate_text = record.get("candidate_manifest_path")
            if isinstance(candidate_text, str) and Path(candidate_text).is_file():
                kwargs["candidate_manifest_path"] = candidate_text
                kwargs["expected_candidate_manifest_sha256"] = _sha256_file(Path(candidate_text))
        return check_freeze_ready(
            self.case_dir,
            authorization_path=authorization_path,
            requested_mode=mode,
            dry_run=dry_run,
            repo_root=self.repo_root,
            **kwargs,
        )

    def _write_argument_blueprint_trace(self) -> None:
        main_tex = self.case_dir / "paper" / "main.tex"
        manifest = self.case_dir / "paper" / "writer_manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        self._write_json(
            self.case_dir / "paper" / "argument_blueprint_trace_report.json",
            {
                "schema_version": "argument_blueprint_trace_report_v1",
                "generated_by": "paper_renderer",
                "status": "PASS",
                "policy_version": "controlled_writing_layer_v5",
                "manifest_mode": "CURRENT_FORMAL",
                "argument_blueprint": {
                    "status": "PASS",
                    "contract_version": "argument_blueprint_contract_v1",
                    "task_count": 1,
                },
                "main_tex_sha256": hashlib.sha256(main_tex.read_bytes()).hexdigest(),
                "source_manifest": {
                    "path": "paper/writer_manifest.json",
                    "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                },
            },
        )

    def _assert_final_state_locked(self, result: dict) -> None:
        self.assertFalse(result["candidate_ready"])
        self.assertFalse(result["final_freeze_ready"])
        self.assertFalse(result["final_freeze_dry_run_passed"])
        self.assertFalse(result["final_submit_allowed"])
        self.assertFalse(result["final_submit_created"])
        self.assertFalse(result["official_submission_completed"])

    def _literal_case_with_missing_required_file(self, name: str, missing: str | None = None) -> Path:
        case_dir = self.repo_root / name
        case_dir.mkdir(parents=True)
        self._write_json(
            case_dir / "case_config.json",
            {
                "official_submission_mode": "enabled",
                "final_submit_allowed": True,
                "citation_ready": True,
                "formal_reference_allowed": True,
                "human_gate_status": "APPROVED",
                "required_final_artifacts": [],
            },
        )
        for relative_path in (
            "output/validation/output_contract_report.md",
            "output/validation/evidence_trace_report.md",
            "output/validation/citation_audit_report.md",
            "ai_disclosure/AI工具使用详情.md",
            "human_review/gate_4_final_submission.md",
            "paper/main.pdf",
            "paper/visual_reasoning_trace_report.json",
        ):
            if relative_path == missing:
                continue
            path = case_dir / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            content = (
                "HUMAN_GATE_STATUS: APPROVED\nAPPROVED_BY: test reviewer\nMANUAL_CONFIRMATION: reviewed manually\n"
                if relative_path == "human_review/gate_4_final_submission.md"
                else "present"
            )
            if relative_path != "paper/visual_reasoning_trace_report.json":
                path.write_text(content, encoding="utf-8")
        if missing != "paper/visual_reasoning_trace_report.json":
            self._write_visual_reasoning_trace(case_dir)
        return case_dir

    def _independent_digest_fixture(self, name: str, order: tuple[str, ...] = ("a.txt", "nested/b.bin", "manifest.json")) -> Path:
        candidate_dir = self.external_dir / name
        candidate_dir.mkdir(parents=True)
        contents = {
            "a.txt": b"alpha\n",
            "nested/b.bin": b"\x00\xffbinary\n",
            "manifest.json": b'{"status":"candidate","version":1}\n',
        }
        for relative_path in order:
            path = candidate_dir / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents[relative_path])
        return candidate_dir

    def _independent_digest(self, candidate_dir: Path, *, include_manifest: bool = True) -> str:
        rows: list[dict[str, str]] = []
        for path in candidate_dir.rglob("*"):
            if not path.is_file() or (not include_manifest and path.name == "manifest.json"):
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            rows.append({"relative_path": path.relative_to(candidate_dir).as_posix(), "sha256": digest})
        rows.sort(key=lambda item: item["relative_path"])
        canonical = json.dumps(rows, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def test_missing_authorization_path_blocks(self) -> None:
        self._make_case_ready_for_intake()
        result = self._check(None)
        self.assertIn("FINAL_AUTH_MISSING", result["blockers"])
        self._assert_final_state_locked(result)

    def test_repo_local_production_authorization_blocks(self) -> None:
        self._make_case_ready_for_intake()
        local_path = self.case_dir / "human_review" / "final_submit_authorization.json"
        self._write_json(local_path, self._contract(mode="production"))
        result = self._check(local_path, mode="production", dry_run=False)
        self.assertIn("FINAL_AUTH_NOT_EXTERNAL", result["blockers"])
        self.assertFalse(result["authorization_external"])

    def test_legacy_repository_record_remains_non_authoritative(self) -> None:
        self._make_case_ready_for_intake()
        self._write_json(
            self.case_dir / "human_review" / "final_submit_authorization.json",
            {
                "authorization_status": "AUTHORIZED", "source": "human", "scope": "final_submit",
                "authorized_by": "test reviewer", "authorized_at": "2026-07-13T10:00:00+00:00",
                "manual_confirmation": "manual review",
            },
        )
        result = self._check(None)
        self.assertTrue(result["repo_authorization_record_valid"])
        self.assertIn("FINAL_AUTH_MISSING", result["blockers"])
        self.assertFalse(result["authorization_contract_valid"])

    def test_config_final_submit_allowed_is_not_authorization(self) -> None:
        self._make_case_ready_for_intake()
        result = self._check(None)
        self.assertNotIn("FINAL_SUBMIT_NOT_ALLOWED", result["blockers"])
        self.assertIn("FINAL_AUTH_MISSING", result["blockers"])
        self._assert_final_state_locked(result)

    def test_chinese_disclosure_path_requires_literal_file_not_mojibake_substitute(self) -> None:
        disclosure_path = "ai_disclosure/AI工具使用详情.md"
        mojibake_path = "ai_disclosure/AI\u7039\u641e\u5131\u9359\u630e\u62c5\u74ba\u3126\u668f\u9420\u56f7\u9644\u934e?md"
        self.assertEqual(
            [path for path in REQUIRED if path.startswith("ai_disclosure/")],
            [disclosure_path],
        )
        self._write_config()
        for relative_path in (
            "output/validation/output_contract_report.md",
            "output/validation/evidence_trace_report.md",
            "output/validation/citation_audit_report.md",
            "human_review/gate_4_final_submission.md",
            "paper/main.pdf",
            "paper/visual_reasoning_trace_report.json",
        ):
            path = self.case_dir / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative_path != "paper/visual_reasoning_trace_report.json":
                path.write_text("present", encoding="utf-8")
        self._write_visual_reasoning_trace()
        (self.case_dir / "human_review" / "gate_4_final_submission.md").write_text(
            "HUMAN_GATE_STATUS: APPROVED\nAPPROVED_BY: test reviewer\nMANUAL_CONFIRMATION: reviewed manually\n",
            encoding="utf-8",
        )
        result = self._check(None)
        self.assertNotIn(mojibake_path, result["required_artifacts"])
        self.assertIn(disclosure_path, result["missing_artifacts"])
        expected_path = self.case_dir / disclosure_path
        expected_path.parent.mkdir(parents=True, exist_ok=True)
        expected_path.write_text("present", encoding="utf-8")
        result = self._check(None)
        self.assertNotIn(disclosure_path, result["missing_artifacts"])
        self.assertEqual(result["missing_artifacts"], [])

    def test_literal_required_artifact_golden_blockers(self) -> None:
        required_cases = (
            ("output_contract", "output/validation/output_contract_report.md", "MISSING_REQUIRED_ARTIFACTS"),
            ("evidence_trace", "output/validation/evidence_trace_report.md", "MISSING_REQUIRED_ARTIFACTS"),
            ("citation_audit", "output/validation/citation_audit_report.md", "MISSING_REQUIRED_ARTIFACTS"),
            ("ai_disclosure", "ai_disclosure/AI工具使用详情.md", "MISSING_REQUIRED_ARTIFACTS"),
            ("human_gate", "human_review/gate_4_final_submission.md", "MISSING_HUMAN_GATE"),
            ("paper", "paper/main.pdf", "MISSING_REQUIRED_ARTIFACTS"),
            ("visual_reasoning", "paper/visual_reasoning_trace_report.json", "MISSING_REQUIRED_ARTIFACTS"),
        )
        for name, missing, expected_blocker in required_cases:
            with self.subTest(missing=missing):
                case_dir = self._literal_case_with_missing_required_file(name, missing)
                result = check_freeze_ready(case_dir, repo_root=self.repo_root)
                self.assertIn(expected_blocker, result["blockers"])
                self.assertIn(missing, result["missing_artifacts"])
                self.assertEqual(result["machine_preconditions_status"], "FAIL")

    def test_visual_reasoning_report_must_be_semantic_pass_not_just_present(self) -> None:
        case_dir = self._literal_case_with_missing_required_file("bad_visual_reasoning")
        self._write_json(
            case_dir / "paper" / "visual_reasoning_trace_report.json",
            {"schema_version": "visual_reasoning_trace_report_v1", "status": "FAIL"},
        )
        result = check_freeze_ready(case_dir, repo_root=self.repo_root)
        self.assertIn("VISUAL_REASONING_TRACE_NOT_PASSED", result["blockers"])
        self.assertEqual(result["machine_preconditions_status"], "FAIL")

    def test_new_case_argument_blueprint_trace_is_a_real_freeze_blocker(self) -> None:
        self._write_config(argument_blueprint_required=True)
        self._write_required_artifacts()
        blockers, _, _ = _base_machine_blockers(
            self.case_dir,
            json.loads((self.case_dir / "case_config.json").read_text(encoding="utf-8")),
        )
        self.assertIn("ARGUMENT_BLUEPRINT_TRACE_NOT_PASSED", blockers)
        self._write_argument_blueprint_trace()
        blockers, _, _ = _base_machine_blockers(
            self.case_dir,
            json.loads((self.case_dir / "case_config.json").read_text(encoding="utf-8")),
        )
        self.assertNotIn("ARGUMENT_BLUEPRINT_TRACE_NOT_PASSED", blockers)

    def test_literal_complete_case_preserves_return_and_fail_closed_fields(self) -> None:
        case_dir = self._literal_case_with_missing_required_file("complete")
        result = check_freeze_ready(case_dir, repo_root=self.repo_root)
        for field in (
            "machine_preconditions_status",
            "authorization_external",
            "authorization_contract_valid",
            "authorization_bindings_valid",
            "candidate_ready",
            "final_freeze_ready",
            "final_freeze_dry_run_passed",
            "final_submit_allowed",
            "final_submit_created",
            "official_submission_completed",
        ):
            self.assertIn(field, result)
        self.assertEqual(result["machine_preconditions_status"], "PASS")
        self.assertIn("FINAL_AUTH_MISSING", result["blockers"])
        self._assert_final_state_locked(result)

    def test_independent_digest_oracle_has_hard_coded_value_and_includes_manifest(self) -> None:
        candidate_dir = self._independent_digest_fixture("oracle")
        expected_digest = "eb7d338a0c88bda7c30524db4e12b7e248e0b7096ce5f116f483de1e52982770"
        independent_digest = self._independent_digest(candidate_dir)
        self.assertEqual(independent_digest, expected_digest)
        self.assertEqual(_calculate_candidate_package_digest(candidate_dir), expected_digest)
        self.assertNotEqual(independent_digest, self._independent_digest(candidate_dir, include_manifest=False))

    def test_manifest_mutation_changes_package_digest(self) -> None:
        candidate_dir = self._independent_digest_fixture("manifest_mutation")
        before = self._independent_digest(candidate_dir)
        production_before = _calculate_candidate_package_digest(candidate_dir)
        (candidate_dir / "manifest.json").write_bytes(b'{"status":"candidate","version":2}\n')
        after = self._independent_digest(candidate_dir)
        self.assertNotEqual(before, after)
        self.assertNotEqual(production_before, _calculate_candidate_package_digest(candidate_dir))
        self.assertEqual(after, _calculate_candidate_package_digest(candidate_dir))

    def test_independent_digest_is_order_independent_and_raw_byte_sensitive(self) -> None:
        first = self._independent_digest_fixture("first")
        second = self._independent_digest_fixture("second", ("manifest.json", "nested/b.bin", "a.txt"))
        self.assertEqual(self._independent_digest(first), self._independent_digest(second))
        self.assertEqual(_calculate_candidate_package_digest(first), _calculate_candidate_package_digest(second))
        self.assertEqual(self._independent_digest(first), _calculate_candidate_package_digest(first))
        lf_dir = self._independent_digest_fixture("lf")
        crlf_dir = self._independent_digest_fixture("crlf")
        (crlf_dir / "a.txt").write_bytes(b"alpha\r\n")
        self.assertNotEqual(self._independent_digest(lf_dir), self._independent_digest(crlf_dir))
        self.assertNotEqual(_calculate_candidate_package_digest(lf_dir), _calculate_candidate_package_digest(crlf_dir))
        before = self._independent_digest(first)
        production_before = _calculate_candidate_package_digest(first)
        (first / "nested" / "b.bin").write_bytes(b"\x00\xffchanged\n")
        self.assertNotEqual(before, self._independent_digest(first))
        self.assertNotEqual(production_before, _calculate_candidate_package_digest(first))
        self.assertEqual(self._independent_digest(first), _calculate_candidate_package_digest(first))

    def test_non_regular_candidate_file_fails_closed(self) -> None:
        candidate_dir = self._independent_digest_fixture("non_regular")
        special_path = candidate_dir / "a.txt"
        original_lstat = Path.lstat

        def non_regular(path: Path):
            if path == special_path:
                return SimpleNamespace(st_mode=stat.S_IFIFO)
            return original_lstat(path)

        with patch.object(Path, "lstat", new=non_regular):
            self.assertIsNone(_calculate_candidate_package_digest(candidate_dir))

    def test_candidate_symlink_fails_closed_or_reports_skip_reason(self) -> None:
        candidate_dir = self._independent_digest_fixture("symlink")
        link_path = candidate_dir / "linked.txt"
        try:
            link_path.symlink_to(candidate_dir / "a.txt")
        except OSError as exc:
            self.skipTest(f"symlink unavailable in this environment: {exc}")
        self.assertIsNone(_calculate_candidate_package_digest(candidate_dir))

    def test_mock_without_dry_run_blocks(self) -> None:
        self._make_case_ready_for_intake()
        result = self._check(self._external_authorization(), dry_run=False)
        self.assertIn("FINAL_AUTH_INVALID", result["blockers"])

    def test_mock_in_production_blocks(self) -> None:
        self._make_case_ready_for_intake()
        result = self._check(self._external_authorization(), mode="production", dry_run=False)
        self.assertIn("FINAL_AUTH_MOCK_NOT_ALLOWED", result["blockers"])

    def test_missing_required_schema_field_blocks(self) -> None:
        self._make_case_ready_for_intake()
        record = self._contract()
        del record["route_review_sha256"]
        path = self.external_dir / "authorization.json"
        self._write_json(path, record)
        self.assertIn("FINAL_AUTH_INVALID", self._check(path)["blockers"])

    def test_invalid_mode_and_status_block(self) -> None:
        self._make_case_ready_for_intake()
        self.assertIn("FINAL_AUTH_INVALID", self._check(self._external_authorization(mode="invalid"))["blockers"])
        self.assertIn("FINAL_AUTH_INVALID", self._check(self._external_authorization(status="invalid"))["blockers"])

    def test_revoked_and_rejected_block(self) -> None:
        self._make_case_ready_for_intake()
        self.assertIn("FINAL_AUTH_REVOKED", self._check(self._external_authorization(status="REVOKED"))["blockers"])
        self.assertIn("FINAL_AUTH_REJECTED", self._check(self._external_authorization(status="REJECTED"))["blockers"])

    def test_scope_and_manual_statement_must_be_valid(self) -> None:
        self._make_case_ready_for_intake()
        self.assertIn(
            "FINAL_AUTH_SCOPE_MISMATCH",
            self._check(self._external_authorization(authorization_scope="final_submit"))["blockers"],
        )
        self.assertIn(
            "FINAL_AUTH_INVALID",
            self._check(self._external_authorization(not_automatic_submission_statement="approved"))["blockers"],
        )

    def test_canonical_digest_is_independent_of_file_creation_order(self) -> None:
        first = self._create_candidate_package(
            "first",
            (("nested/a.txt", "alpha"), ("b.txt", "beta")),
        )
        second = self._create_candidate_package(
            "second",
            (("b.txt", "beta"), ("nested/a.txt", "alpha")),
        )
        self.assertEqual(_calculate_candidate_package_digest(first), _calculate_candidate_package_digest(second))

    def test_canonical_digest_changes_when_candidate_content_changes(self) -> None:
        candidate_dir = self._create_candidate_package(files=(("payload.txt", "before"),))
        before = _calculate_candidate_package_digest(candidate_dir)
        (candidate_dir / "payload.txt").write_text("after", encoding="utf-8")
        self.assertNotEqual(before, _calculate_candidate_package_digest(candidate_dir))

    def test_matching_candidate_manifest_and_digest_are_revalidated_but_stay_fail_closed(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._create_candidate_package(files=(("payload.txt", "payload"),))
        result = self._check(self._external_candidate_authorization(candidate_dir))
        self.assertNotIn("FINAL_AUTH_PACKAGE_MISMATCH", result["blockers"])
        self.assertEqual(result["calculated_candidate_manifest_sha256"], _sha256_file(candidate_dir / "manifest.json"))
        self.assertEqual(result["calculated_candidate_package_digest"], _calculate_candidate_package_digest(candidate_dir))
        self.assertFalse(result["authorization_bindings_valid"])
        self._assert_final_state_locked(result)
        self.assertFalse((self.repo_root / "candidate_submit").exists())
        self.assertFalse((self.repo_root / "final_submit").exists())

    def test_manifest_hash_mismatch_blocks(self) -> None:
        self._make_case_ready_for_intake()
        result = self._check(self._external_candidate_authorization(candidate_manifest_sha256="0" * 64))
        self.assertIn("FINAL_AUTH_PACKAGE_MISMATCH", result["blockers"])

    def test_package_digest_mismatch_blocks(self) -> None:
        self._make_case_ready_for_intake()
        result = self._check(self._external_candidate_authorization(candidate_package_digest="0" * 64))
        self.assertIn("FINAL_AUTH_PACKAGE_MISMATCH", result["blockers"])

    def test_manifest_path_escape_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._create_candidate_package(files=(("payload.txt", "payload"),))
        escaped_path = str(candidate_dir / "nested" / ".." / "manifest.json")
        result = self._check(self._external_candidate_authorization(candidate_dir, candidate_manifest_path=escaped_path))
        self.assertIn("FINAL_AUTH_PACKAGE_MISMATCH", result["blockers"])
        self.assertIsNone(result["calculated_candidate_manifest_sha256"])
        self.assertIsNone(result["calculated_candidate_package_digest"])

    def test_missing_candidate_file_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._create_candidate_package(files=(("payload.txt", "payload"),))
        authorization_path = self._external_candidate_authorization(candidate_dir)
        (candidate_dir / "payload.txt").unlink()
        result = self._check(authorization_path)
        self.assertIn("FINAL_AUTH_PACKAGE_MISMATCH", result["blockers"])

    def test_unreadable_candidate_file_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._create_candidate_package(files=(("payload.txt", "payload"),))
        authorization_path = self._external_candidate_authorization(candidate_dir)
        payload_path = candidate_dir / "payload.txt"

        def unreadable(path: Path) -> str:
            if path == payload_path:
                raise OSError("fixture read denied")
            return _sha256_file(path)

        with patch("pipeline_core.python_tools.final_freezer._sha256_file", side_effect=unreadable):
            result = self._check(authorization_path)
        self.assertIn("FINAL_AUTH_PACKAGE_MISMATCH", result["blockers"])

    def test_valid_external_mock_contract_stays_fail_closed(self) -> None:
        self._make_case_ready_for_intake()
        result = self._check(self._external_candidate_authorization())
        self.assertTrue(result["authorization_contract_valid"])
        self.assertTrue(result["authorization_external"])
        self.assertFalse(result["authorization_bindings_valid"])
        self.assertIn("FINAL_AUTH_BINDINGS_NOT_VALIDATED", result["blockers"])
        self.assertIn("FINAL_AUTH_BINDINGS_NOT_VALIDATED", result["warnings"])
        self._assert_final_state_locked(result)

    def test_valid_external_production_contract_preserves_manual_boundary(self) -> None:
        self._make_case_ready_for_intake()
        result = self._check(
            self._external_candidate_authorization(mode="production"),
            mode="production",
            dry_run=False,
        )
        self.assertTrue(result["authorization_contract_valid"])
        self.assertTrue(result["authorization_external"])
        self.assertIn("EXTERNAL_MANUAL_CONFIRMATION_REQUIRED", result["blockers"])
        self._assert_final_state_locked(result)
        self.assertFalse((self.repo_root / "final_submit").exists())

    def test_authorization_at_candidate_root_is_rejected_before_record_read(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._create_candidate_package(files=(("payload.txt", "payload"),))
        authorization_path = self._candidate_package_authorization(
            candidate_dir,
            "final_authorization.json",
            mode="production",
        )
        from pipeline_core.python_tools import final_freezer

        original_read_json = final_freezer._read_json
        read_paths: list[Path] = []

        def tracking_read_json(path: Path):
            read_paths.append(path.resolve(strict=False))
            return original_read_json(path)

        with patch("pipeline_core.python_tools.final_freezer._read_json", side_effect=tracking_read_json):
            result = self._check(authorization_path, mode="production", dry_run=False)
        self.assertIn("FINAL_AUTH_INSIDE_CANDIDATE_PACKAGE", result["blockers"])
        self.assertNotIn(authorization_path.resolve(), read_paths)
        self.assertFalse(result["authorization_outside_candidate_package"])
        self.assertEqual(result["calculated_candidate_package_root"], str(candidate_dir.resolve()))
        self.assertEqual(result["authorization_resolved_path"], str(authorization_path.resolve()))
        self._assert_final_state_locked(result)

    def test_authorization_nested_in_candidate_package_is_rejected(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._create_candidate_package(files=(("payload.txt", "payload"),))
        authorization_path = self._candidate_package_authorization(
            candidate_dir,
            "nested/final_authorization.json",
            mode="production",
        )
        result = self._check(authorization_path, mode="production", dry_run=False)
        self.assertIn("FINAL_AUTH_INSIDE_CANDIDATE_PACKAGE", result["blockers"])
        self.assertFalse(result["authorization_outside_candidate_package"])
        self.assertEqual(result["calculated_candidate_package_root"], str(candidate_dir.resolve()))
        self._assert_final_state_locked(result)

    def test_candidate_package_root_cannot_be_an_authorization_path(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._create_candidate_package(files=(("payload.txt", "payload"),))
        result = self._check(candidate_dir, mode="production", dry_run=False)
        self.assertIn("FINAL_AUTH_INSIDE_CANDIDATE_PACKAGE", result["blockers"])
        self.assertFalse(result["authorization_outside_candidate_package"])
        self.assertEqual(result["calculated_candidate_package_root"], str(candidate_dir.resolve()))
        self.assertEqual(result["authorization_resolved_path"], str(candidate_dir.resolve()))
        self._assert_final_state_locked(result)

    def test_external_sibling_authorization_is_outside_candidate_package_but_stays_fail_closed(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._create_candidate_package(files=(("payload.txt", "payload"),))
        authorization_path = self._external_candidate_authorization(candidate_dir, mode="production")
        result = self._check(authorization_path, mode="production", dry_run=False)
        self.assertNotIn("FINAL_AUTH_INSIDE_CANDIDATE_PACKAGE", result["blockers"])
        self.assertTrue(result["authorization_outside_candidate_package"])
        self.assertEqual(result["calculated_candidate_package_root"], str(candidate_dir.resolve()))
        self.assertEqual(result["authorization_resolved_path"], str(authorization_path.resolve()))
        self.assertIn("FINAL_AUTH_BINDINGS_NOT_VALIDATED", result["blockers"])
        self._assert_final_state_locked(result)

    def test_repo_local_authorization_outside_candidate_package_keeps_repo_blocker(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._create_candidate_package(files=(("payload.txt", "payload"),))
        authorization_path = self.repo_root / "authorization" / "final_authorization.json"
        self._write_json(
            authorization_path,
            self._contract(
                mode="production",
                candidate_manifest_path=str(candidate_dir / "manifest.json"),
                candidate_manifest_sha256=_sha256_file(candidate_dir / "manifest.json"),
                candidate_package_digest=_calculate_candidate_package_digest(candidate_dir),
            ),
        )
        result = self._check(authorization_path, mode="production", dry_run=False)
        self.assertIn("FINAL_AUTH_NOT_EXTERNAL", result["blockers"])
        self.assertNotIn("FINAL_AUTH_INSIDE_CANDIDATE_PACKAGE", result["blockers"])
        self._assert_final_state_locked(result)

    def test_mock_authorization_inside_candidate_package_is_rejected(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._create_candidate_package(files=(("payload.txt", "payload"),))
        authorization_path = self._candidate_package_authorization(candidate_dir, "mock_authorization.json")
        result = self._check(authorization_path)
        self.assertIn("FINAL_AUTH_INSIDE_CANDIDATE_PACKAGE", result["blockers"])
        self.assertFalse(result["authorization_outside_candidate_package"])
        self._assert_final_state_locked(result)

    def test_symlinked_authorization_resolved_inside_candidate_package_is_rejected_or_skipped(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._create_candidate_package(files=(("payload.txt", "payload"),))
        inside_authorization = self._candidate_package_authorization(
            candidate_dir,
            "inside_authorization.json",
            mode="production",
        )
        authorization_path = self.external_dir / "linked_authorization.json"
        try:
            authorization_path.symlink_to(inside_authorization)
        except OSError as exc:
            self.skipTest(f"symlink unavailable in this Windows environment: {exc}")
        result = self._check(authorization_path, mode="production", dry_run=False)
        self.assertIn("FINAL_AUTH_INSIDE_CANDIDATE_PACKAGE", result["blockers"])
        self.assertFalse(result["authorization_outside_candidate_package"])
        self.assertEqual(result["authorization_resolved_path"], str(inside_authorization.resolve()))
        self._assert_final_state_locked(result)

    def test_candidate_only_package_remains_blocked(self) -> None:
        candidate_dir = self.repo_root / "candidate_submit" / "candidate_001"
        candidate_dir.mkdir(parents=True)
        self._write_json(candidate_dir / "manifest.json", {"status": "candidate", "for_official_submission": False})
        result = check_freeze_ready(candidate_dir, repo_root=self.repo_root)
        self.assertIn("CANDIDATE_PACKAGE_ONLY", result["blockers"])
        self.assertFalse(result["final_freeze_ready"])

    def test_validation_does_not_create_directories_or_modify_fixture_files(self) -> None:
        self._make_case_ready_for_intake()
        authorization_path = self._external_authorization()
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        result = self._check(authorization_path)
        after = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse((self.repo_root / "final_submit").exists())
        self.assertFalse((self.repo_root / "candidate_submit").exists())

    # ---- P6-PV3: bind final_freezer to candidate provenance ----

    def test_complete_provenance_binds_and_is_reported_valid(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._provenance_candidate_package()
        authorization_path = self._external_candidate_authorization(candidate_dir, source_commit="c" * 40)
        result = self._check(authorization_path)
        self.assertTrue(result["candidate_provenance_valid"])
        self.assertIsNotNone(result["candidate_provenance_verification"])
        self.assertNotIn("FINAL_CANDIDATE_PROVENANCE_INVALID", result["blockers"])
        # Still fail-closed in this slice: FF2 mock-authorization dry-run
        # is a separate, later slice.
        self._assert_final_state_locked(result)

    def test_finalization_passed_false_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._provenance_candidate_package(
            verification_overrides={"source_provenance_finalization_passed": False}
        )
        authorization_path = self._external_candidate_authorization(candidate_dir, source_commit="c" * 40)
        result = self._check(authorization_path)
        self.assertFalse(result["candidate_provenance_valid"])
        self.assertIn("FINAL_CANDIDATE_PROVENANCE_INVALID", result["blockers"])
        self.assertIn("FINAL_CANDIDATE_PROVENANCE_FINALIZATION_FAILED", result["blockers"])

    def test_post_snapshot_verification_false_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._provenance_candidate_package(
            verification_overrides={"post_snapshot_verification_passed": False}
        )
        authorization_path = self._external_candidate_authorization(candidate_dir, source_commit="c" * 40)
        result = self._check(authorization_path)
        self.assertFalse(result["candidate_provenance_valid"])
        self.assertIn("FINAL_CANDIDATE_PROVENANCE_POST_SNAPSHOT_FAILED", result["blockers"])

    def test_required_false_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._provenance_candidate_package(
            verification_overrides={"source_provenance_required": False}
        )
        authorization_path = self._external_candidate_authorization(candidate_dir, source_commit="c" * 40)
        result = self._check(authorization_path)
        self.assertFalse(result["candidate_provenance_valid"])
        self.assertIn("FINAL_CANDIDATE_PROVENANCE_NOT_REQUIRED", result["blockers"])

    def test_applicable_false_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._provenance_candidate_package(
            verification_overrides={"source_provenance_applicable": False}
        )
        authorization_path = self._external_candidate_authorization(candidate_dir, source_commit="c" * 40)
        result = self._check(authorization_path)
        self.assertFalse(result["candidate_provenance_valid"])
        self.assertIn("FINAL_CANDIDATE_PROVENANCE_NOT_APPLICABLE", result["blockers"])

    def test_legacy_compatibility_true_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._provenance_candidate_package(
            verification_overrides={
                "source_provenance_required": False,
                "source_provenance_applicable": False,
                "source_provenance_legacy_compatibility": True,
                "source_provenance_finalization_passed": False,
                "post_snapshot_verification_passed": False,
            }
        )
        authorization_path = self._external_candidate_authorization(candidate_dir, source_commit="c" * 40)
        result = self._check(authorization_path)
        self.assertFalse(result["candidate_provenance_valid"])
        self.assertIn("FINAL_CANDIDATE_PROVENANCE_LEGACY_NOT_ALLOWED", result["blockers"])
        self._assert_final_state_locked(result)

    def test_source_commit_mismatch_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._provenance_candidate_package(source_commit="c" * 40)
        authorization_path = self._external_candidate_authorization(candidate_dir, source_commit="d" * 40)
        result = self._check(authorization_path)
        self.assertFalse(result["candidate_provenance_valid"])
        self.assertIn("FINAL_CANDIDATE_PROVENANCE_COMMIT_MISMATCH", result["blockers"])

    def test_provenance_hash_format_invalid_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._provenance_candidate_package(
            verification_overrides={"source_provenance_sha256": "not-a-valid-hash"}
        )
        authorization_path = self._external_candidate_authorization(candidate_dir, source_commit="c" * 40)
        result = self._check(authorization_path)
        self.assertFalse(result["candidate_provenance_valid"])
        self.assertIn("FINAL_CANDIDATE_PROVENANCE_HASH_INVALID", result["blockers"])

    def test_manifest_commit_format_invalid_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._provenance_candidate_package(source_commit="not-a-commit")
        authorization_path = self._external_candidate_authorization(candidate_dir, source_commit="c" * 40)
        result = self._check(authorization_path)
        self.assertFalse(result["candidate_provenance_valid"])
        self.assertIn("FINAL_CANDIDATE_PROVENANCE_COMMIT_INVALID", result["blockers"])

    def test_manifest_blockers_present_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._provenance_candidate_package(
            verification_overrides={"blockers": ["PROVENANCE_RUNTIME_INCOMPLETE"]}
        )
        authorization_path = self._external_candidate_authorization(candidate_dir, source_commit="c" * 40)
        result = self._check(authorization_path)
        self.assertFalse(result["candidate_provenance_valid"])
        self.assertIn("FINAL_CANDIDATE_PROVENANCE_BLOCKERS_PRESENT", result["blockers"])

    def test_unexpected_status_entries_present_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._provenance_candidate_package(
            verification_overrides={
                "packaging_unexpected_status_entries": [{"xy": "??", "path": "unrelated.txt", "original_path": None}]
            }
        )
        authorization_path = self._external_candidate_authorization(candidate_dir, source_commit="c" * 40)
        result = self._check(authorization_path)
        self.assertFalse(result["candidate_provenance_valid"])
        self.assertIn("FINAL_CANDIDATE_PROVENANCE_UNEXPECTED_WORKTREE_CHANGE", result["blockers"])

    def test_git_operation_in_progress_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._provenance_candidate_package(
            verification_overrides={"packaging_git_operation_in_progress": True}
        )
        authorization_path = self._external_candidate_authorization(candidate_dir, source_commit="c" * 40)
        result = self._check(authorization_path)
        self.assertFalse(result["candidate_provenance_valid"])
        self.assertIn("FINAL_CANDIDATE_PROVENANCE_GIT_OPERATION_IN_PROGRESS", result["blockers"])

    def test_head_mismatch_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._provenance_candidate_package(
            verification_overrides={"packaging_head_matches_source_commit": False}
        )
        authorization_path = self._external_candidate_authorization(candidate_dir, source_commit="c" * 40)
        result = self._check(authorization_path)
        self.assertFalse(result["candidate_provenance_valid"])
        self.assertIn("FINAL_CANDIDATE_PROVENANCE_HEAD_MISMATCH", result["blockers"])

    def test_missing_verification_field_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._create_candidate_package(files=(("payload.txt", "payload"),))
        authorization_path = self._external_candidate_authorization(candidate_dir, source_commit="c" * 40)
        result = self._check(authorization_path)
        self.assertFalse(result["candidate_provenance_valid"])
        self.assertIn("FINAL_CANDIDATE_PROVENANCE_VERIFICATION_MISSING", result["blockers"])
        self.assertIsNone(result["candidate_provenance_verification"])

    def test_no_live_git_head_is_ever_invoked(self) -> None:
        # This module must bind exclusively to the manifest's own recorded
        # snapshot — never shell out to git at check time.
        self._make_case_ready_for_intake()
        candidate_dir = self._provenance_candidate_package()
        authorization_path = self._external_candidate_authorization(candidate_dir, source_commit="c" * 40)
        with patch("subprocess.run") as run:
            result = self._check(authorization_path)
        run.assert_not_called()
        self.assertTrue(result["candidate_provenance_valid"])

    def test_source_files_not_modified_across_provenance_scenarios(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._provenance_candidate_package()
        authorization_path = self._external_candidate_authorization(candidate_dir, source_commit="c" * 40)
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self._check(authorization_path)
        after = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_no_candidate_or_final_created_across_provenance_scenarios(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._provenance_candidate_package()
        authorization_path = self._external_candidate_authorization(candidate_dir, source_commit="c" * 40)
        result = self._check(authorization_path)
        self.assertFalse(result["final_submit_allowed"])
        self.assertFalse(result["final_submit_created"])
        self.assertFalse(result["final_freeze_ready"])
        self.assertFalse((self.repo_root / "final_submit").exists())
        self.assertFalse((self.repo_root / "candidate_submit").exists())
        self.assertFalse((self.repo_root / "candidate_emergency").exists())

    def test_valid_provenance_still_stays_fail_closed_in_production_mode(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._provenance_candidate_package()
        authorization_path = self._external_candidate_authorization(
            candidate_dir, source_commit="c" * 40, mode="production"
        )
        result = self._check(authorization_path, mode="production", dry_run=False)
        self.assertTrue(result["candidate_provenance_valid"])
        self._assert_final_state_locked(result)
        self.assertFalse((self.repo_root / "final_submit").exists())


if __name__ == "__main__":
    unittest.main()
