from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pipeline_core.python_tools.final_freezer import (
    REQUIRED,
    _calculate_candidate_package_digest,
    _sha256_file,
    check_freeze_ready,
)


class FinalFreezerMockAuthorizationDryRunTests(unittest.TestCase):
    """P6-FF2: prove the full chain — mock external authorization, candidate
    package digest, source provenance (P6-PV2A/-B), and S2B
    upstream_artifact_bindings — can all be independently verified through
    check_freeze_ready() without ever setting final_freeze_ready or
    final_submit_allowed to true, and without creating final_submit/. This
    module never runs production code that creates final_submit; it only
    exercises the existing read-only predicate against fully-mocked,
    temp-directory-only fixtures.
    """

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
            "r3_required": False,
        }
        config.update(overrides)
        self._write_json(self.case_dir / "case_config.json", config)

    def _write_visual_reasoning_trace(self) -> None:
        main_tex = self.case_dir / "paper" / "main.tex"
        figure_root = self.case_dir / "paper" / "writer_content" / "figures"
        main_tex.parent.mkdir(parents=True, exist_ok=True)
        figure_root.mkdir(parents=True, exist_ok=True)
        main_tex.write_text("formal paper", encoding="utf-8")
        figures = []
        for index in range(1, 5):
            figure = figure_root / f"fig{index}.png"
            figure.write_bytes(f"figure-{index}".encode("utf-8"))
            figures.append(figure)
        self._write_json(
            self.case_dir / "paper" / "visual_reasoning_trace_report.json",
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

    def _valid_provenance_verification(self, *, source_commit: str = "c" * 40, **overrides: object) -> dict:
        verification = {
            "source_provenance_required": True,
            "source_provenance_applicable": True,
            "source_provenance_legacy_compatibility": False,
            "source_provenance_sha256": "9" * 64,
            "source_provenance_finalization_passed": True,
            "post_snapshot_verification_passed": True,
            "run_id": "run_ff2_fixture",
            "source_commit": source_commit,
            "packaging_head_matches_source_commit": True,
            "packaging_git_operation_in_progress": False,
            "packaging_unexpected_status_entries": [],
            "blockers": [],
        }
        verification.update(overrides)
        return verification

    def _valid_upstream_artifact_bindings(self, **overrides: object) -> dict:
        applicable_pass = {
            "applicable": True,
            "sha256": "7" * 64,
            "schema_version": None,
            "semantic_status": "PASS",
            "semantic_valid": True,
        }
        not_applicable = {
            "applicable": False,
            "sha256": None,
            "schema_version": None,
            "semantic_status": "NOT_APPLICABLE",
            "semantic_valid": True,
        }
        bindings = {
            "run_result": dict(applicable_pass),
            "risk_summary": dict(applicable_pass),
            "result_summary": dict(applicable_pass),
            "route_review": dict(not_applicable),
            "xval": dict(not_applicable),
            "claim_check": dict(applicable_pass),
            "human_revision": dict(not_applicable),
        }
        for key, value in overrides.items():
            bindings[key] = value
        return bindings

    def _mock_candidate_package(
        self,
        name: str = "candidate_ff2",
        *,
        files: tuple[tuple[str, str], ...] = (("payload.txt", "payload"),),
        source_commit: str = "c" * 40,
        provenance_overrides: dict | None = None,
        bindings_overrides: dict | None = None,
        omit_bindings: bool = False,
        omit_provenance: bool = False,
    ) -> Path:
        candidate_dir = self.external_dir / name
        candidate_dir.mkdir(parents=True)
        index_path = candidate_dir / "authoritative_result_index.json"
        handoff_path = candidate_dir / "claim_handoff.json"
        self._write_json(index_path, {"schema_version": "fixture_index_v1", "entries": []})
        self._write_json(handoff_path, {"schema_version": "fixture_handoff_v1", "claims": []})
        manifest: dict[str, object] = {
            "schema_version": "x5_f0_candidate_manifest_v1",
            "status": "candidate",
            "final_submit_created": False,
            "for_official_submission": False,
            "case_identity": "case",
            "case_dir": "case",
            "run_id": "run_ff2_fixture",
            "authoritative_result_index": {
                "path": "authoritative_result_index.json",
                "sha256": _sha256_file(index_path),
            },
            "claim_handoff": {
                "path": "claim_handoff.json",
                "sha256": _sha256_file(handoff_path),
            },
        }
        if not omit_provenance:
            manifest["source_provenance_verification"] = self._valid_provenance_verification(
                source_commit=source_commit, **(provenance_overrides or {})
            )
        if not omit_bindings:
            manifest["upstream_artifact_bindings"] = self._valid_upstream_artifact_bindings(
                **(bindings_overrides or {})
            )
        self._write_json(candidate_dir / "manifest.json", manifest)
        for relative_path, content in files:
            path = candidate_dir / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return candidate_dir

    def _mock_authorization(self, candidate_dir: Path, **overrides: object) -> Path:
        manifest_path = candidate_dir / "manifest.json"
        overrides.setdefault("candidate_manifest_path", str(manifest_path))
        overrides.setdefault("candidate_manifest_sha256", _sha256_file(manifest_path))
        overrides.setdefault("candidate_package_digest", _calculate_candidate_package_digest(candidate_dir))
        record = self._contract(**overrides)
        path = self.external_dir / "mock_authorization.json"
        self._write_json(path, record)
        return path

    def _candidate_package_authorization(self, candidate_dir: Path, relative_path: str, **overrides: object) -> Path:
        manifest_path = candidate_dir / "manifest.json"
        overrides.setdefault("candidate_manifest_path", str(manifest_path))
        overrides.setdefault("candidate_manifest_sha256", _sha256_file(manifest_path))
        overrides.setdefault("candidate_package_digest", _calculate_candidate_package_digest(candidate_dir))
        path = candidate_dir / relative_path
        self._write_json(path, self._contract(**overrides))
        return path

    def _check(self, authorization_path: Path | None, *, mode: str = "mock_test", dry_run: bool = True) -> dict:
        candidate_manifest_path: Path | None = None
        expected_manifest_sha256: str | None = None
        if authorization_path is not None and authorization_path.is_file():
            try:
                authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                authorization = None
            if isinstance(authorization, dict):
                raw_manifest_path = authorization.get("candidate_manifest_path")
                if isinstance(raw_manifest_path, str):
                    candidate_manifest_path = Path(raw_manifest_path)
                    if candidate_manifest_path.is_file():
                        expected_manifest_sha256 = _sha256_file(candidate_manifest_path)
        return check_freeze_ready(
            self.case_dir,
            authorization_path=authorization_path,
            candidate_manifest_path=candidate_manifest_path,
            expected_candidate_manifest_sha256=expected_manifest_sha256,
            expected_run_id="run_ff2_fixture",
            requested_mode=mode,
            dry_run=dry_run,
            repo_root=self.repo_root,
            require_post_writer_reentry=False,
        )

    def _assert_never_creates_final_submit_or_repo_candidate(self, result: dict) -> None:
        self.assertFalse(result["final_submit_created"])
        self.assertFalse((self.repo_root / "final_submit").exists())
        self.assertFalse((self.repo_root / "candidate_submit").exists())

    # ---- Positive: full chain reaches the honest fail-closed end state ----

    def test_full_mock_chain_reaches_fail_closed_end_state(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._mock_candidate_package()
        authorization_path = self._mock_authorization(candidate_dir, source_commit="c" * 40)
        result = self._check(authorization_path)

        # 1. mock authorization contract accepted (not rejected as invalid).
        self.assertTrue(result["authorization_external"])
        self.assertTrue(result["authorization_contract_valid"])
        self.assertEqual(result["authorization_mode"], "mock_test")
        self.assertNotIn("FINAL_AUTH_INVALID", result["blockers"])
        self.assertNotIn("FINAL_AUTH_MOCK_NOT_ALLOWED", result["blockers"])

        # 2. candidate digest bound and matching.
        self.assertEqual(result["calculated_candidate_manifest_sha256"], _sha256_file(candidate_dir / "manifest.json"))
        self.assertEqual(result["calculated_candidate_package_digest"], _calculate_candidate_package_digest(candidate_dir))
        self.assertNotIn("FINAL_AUTH_PACKAGE_MISMATCH", result["blockers"])

        # 3. source provenance verified.
        self.assertTrue(result["candidate_provenance_valid"])
        self.assertIsNotNone(result["candidate_provenance_verification"])
        self.assertNotIn("FINAL_CANDIDATE_PROVENANCE_INVALID", result["blockers"])

        # 4. S2B upstream_artifact_bindings verified.
        self.assertTrue(result["candidate_upstream_bindings_valid"])
        self.assertIsNotNone(result["candidate_upstream_bindings"])
        self.assertNotIn("FINAL_CANDIDATE_UPSTREAM_BINDING_INVALID", result["blockers"])

        # 5. Explicit distinction: mock accepted, production NOT available
        # here (production authorization was never supplied in this test),
        # and the terminal fields remain exactly as the safety policy
        # requires regardless of how far the chain got.
        self.assertIn("FINAL_AUTH_BINDINGS_NOT_VALIDATED", result["blockers"])
        self.assertIn("FINAL_AUTH_BINDINGS_NOT_VALIDATED", result["warnings"])
        self.assertFalse(result["final_freeze_ready"])
        self.assertFalse(result["final_submit_allowed"])
        self.assertFalse(result["final_freeze_dry_run_passed"])
        self.assertEqual(result["status"], "BLOCKED")
        self._assert_never_creates_final_submit_or_repo_candidate(result)

    # ---- Negative: authorization located inside the candidate package ----

    def test_authorization_inside_candidate_package_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._mock_candidate_package()
        authorization_path = self._candidate_package_authorization(
            candidate_dir, "mock_authorization.json", source_commit="c" * 40
        )
        result = self._check(authorization_path)
        self.assertIn("FINAL_AUTH_INSIDE_CANDIDATE_PACKAGE", result["blockers"])
        self._assert_never_creates_final_submit_or_repo_candidate(result)
        self.assertFalse(result["final_freeze_ready"])

    # ---- Negative: candidate digest mismatch ----

    def test_candidate_digest_mismatch_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._mock_candidate_package()
        authorization_path = self._mock_authorization(
            candidate_dir, source_commit="c" * 40, candidate_package_digest="0" * 64
        )
        result = self._check(authorization_path)
        self.assertIn("FINAL_AUTH_PACKAGE_MISMATCH", result["blockers"])
        self.assertFalse(result["candidate_provenance_valid"])
        self.assertFalse(result["candidate_upstream_bindings_valid"])
        self._assert_never_creates_final_submit_or_repo_candidate(result)

    # ---- Negative: source_commit mismatch between authorization and manifest ----

    def test_source_commit_mismatch_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._mock_candidate_package(source_commit="c" * 40)
        authorization_path = self._mock_authorization(candidate_dir, source_commit="d" * 40)
        result = self._check(authorization_path)
        self.assertIn("FINAL_CANDIDATE_PROVENANCE_INVALID", result["blockers"])
        self.assertIn("FINAL_CANDIDATE_PROVENANCE_COMMIT_MISMATCH", result["blockers"])
        self.assertFalse(result["candidate_provenance_valid"])
        self._assert_never_creates_final_submit_or_repo_candidate(result)

    # ---- Negative: provenance finalization incomplete ----

    def test_provenance_finalization_incomplete_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._mock_candidate_package(
            source_commit="c" * 40,
            provenance_overrides={"source_provenance_finalization_passed": False},
        )
        authorization_path = self._mock_authorization(candidate_dir, source_commit="c" * 40)
        result = self._check(authorization_path)
        self.assertIn("FINAL_CANDIDATE_PROVENANCE_INVALID", result["blockers"])
        self.assertIn("FINAL_CANDIDATE_PROVENANCE_FINALIZATION_FAILED", result["blockers"])
        self.assertFalse(result["candidate_provenance_valid"])
        self._assert_never_creates_final_submit_or_repo_candidate(result)

    # ---- Negative: S2B upstream binding missing entirely ----

    def test_upstream_bindings_missing_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._mock_candidate_package(source_commit="c" * 40, omit_bindings=True)
        authorization_path = self._mock_authorization(candidate_dir, source_commit="c" * 40)
        result = self._check(authorization_path)
        self.assertIn("FINAL_CANDIDATE_UPSTREAM_BINDING_INVALID", result["blockers"])
        self.assertIn("FINAL_CANDIDATE_UPSTREAM_BINDING_MISSING", result["blockers"])
        self.assertFalse(result["candidate_upstream_bindings_valid"])
        self._assert_never_creates_final_submit_or_repo_candidate(result)

    # ---- Negative: S2B upstream binding semantic_valid=false ----

    def test_upstream_bindings_semantic_invalid_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._mock_candidate_package(
            source_commit="c" * 40,
            bindings_overrides={
                "risk_summary": {
                    "applicable": True,
                    "sha256": "7" * 64,
                    "schema_version": None,
                    "semantic_status": "BLOCK",
                    "semantic_valid": False,
                }
            },
        )
        authorization_path = self._mock_authorization(candidate_dir, source_commit="c" * 40)
        result = self._check(authorization_path)
        self.assertIn("FINAL_CANDIDATE_UPSTREAM_BINDING_INVALID", result["blockers"])
        self.assertIn("FINAL_CANDIDATE_UPSTREAM_BINDING_RISK_SUMMARY_INVALID", result["blockers"])
        self.assertFalse(result["candidate_upstream_bindings_valid"])
        self._assert_never_creates_final_submit_or_repo_candidate(result)

    # ---- Defense in depth: semantic_status is independently checked,      ----
    # ---- never overridden by a semantic_valid=true boolean alone         ----

    def test_semantic_valid_true_but_status_block_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._mock_candidate_package(
            source_commit="c" * 40,
            bindings_overrides={
                "risk_summary": {
                    "applicable": True,
                    "sha256": "7" * 64,
                    "schema_version": None,
                    "semantic_status": "BLOCK",
                    "semantic_valid": True,
                }
            },
        )
        authorization_path = self._mock_authorization(candidate_dir, source_commit="c" * 40)
        result = self._check(authorization_path)
        self.assertIn("FINAL_CANDIDATE_UPSTREAM_BINDING_INVALID", result["blockers"])
        self.assertIn("FINAL_CANDIDATE_UPSTREAM_BINDING_RISK_SUMMARY_SEMANTIC_STATUS_INVALID", result["blockers"])
        self.assertFalse(result["candidate_upstream_bindings_valid"])
        self._assert_never_creates_final_submit_or_repo_candidate(result)

    def test_semantic_valid_true_but_status_unknown_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._mock_candidate_package(
            source_commit="c" * 40,
            bindings_overrides={
                "risk_summary": {
                    "applicable": True,
                    "sha256": "7" * 64,
                    "schema_version": None,
                    "semantic_status": "UNKNOWN",
                    "semantic_valid": True,
                }
            },
        )
        authorization_path = self._mock_authorization(candidate_dir, source_commit="c" * 40)
        result = self._check(authorization_path)
        self.assertIn("FINAL_CANDIDATE_UPSTREAM_BINDING_RISK_SUMMARY_SEMANTIC_STATUS_INVALID", result["blockers"])
        self.assertFalse(result["candidate_upstream_bindings_valid"])
        self._assert_never_creates_final_submit_or_repo_candidate(result)

    def test_semantic_valid_true_but_status_unrecognized_string_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._mock_candidate_package(
            source_commit="c" * 40,
            bindings_overrides={
                "risk_summary": {
                    "applicable": True,
                    "sha256": "7" * 64,
                    "schema_version": None,
                    "semantic_status": "TOTALLY_MADE_UP_STATUS",
                    "semantic_valid": True,
                }
            },
        )
        authorization_path = self._mock_authorization(candidate_dir, source_commit="c" * 40)
        result = self._check(authorization_path)
        self.assertIn("FINAL_CANDIDATE_UPSTREAM_BINDING_RISK_SUMMARY_SEMANTIC_STATUS_INVALID", result["blockers"])
        self.assertFalse(result["candidate_upstream_bindings_valid"])
        self._assert_never_creates_final_submit_or_repo_candidate(result)

    def test_semantic_valid_true_but_status_missing_or_non_string_blocks(self) -> None:
        self._make_case_ready_for_intake()
        for bad_status in (None, 12345, [], {}):
            with self.subTest(bad_status=bad_status):
                candidate_dir = self._mock_candidate_package(
                    name=f"candidate_ff2_status_{id(bad_status)}",
                    source_commit="c" * 40,
                    bindings_overrides={
                        "risk_summary": {
                            "applicable": True,
                            "sha256": "7" * 64,
                            "schema_version": None,
                            "semantic_status": bad_status,
                            "semantic_valid": True,
                        }
                    },
                )
                authorization_path = self._mock_authorization(candidate_dir, source_commit="c" * 40)
                result = self._check(authorization_path)
                self.assertIn(
                    "FINAL_CANDIDATE_UPSTREAM_BINDING_RISK_SUMMARY_SEMANTIC_STATUS_INVALID", result["blockers"]
                )
                self.assertFalse(result["candidate_upstream_bindings_valid"])
                self._assert_never_creates_final_submit_or_repo_candidate(result)

    def test_required_artifact_skipped_or_not_applicable_status_blocks(self) -> None:
        self._make_case_ready_for_intake()
        for skip_like_status in ("SKIPPED", "NOT_APPLICABLE"):
            with self.subTest(status=skip_like_status):
                candidate_dir = self._mock_candidate_package(
                    name=f"candidate_ff2_skip_{skip_like_status.lower()}",
                    source_commit="c" * 40,
                    bindings_overrides={
                        "claim_check": {
                            # applicable=True (this case genuinely required
                            # claim-check) but the status is a skip-like
                            # value instead of an affirmative PASS — this
                            # must never be accepted as satisfying a
                            # required binding, even with semantic_valid
                            # forged to true.
                            "applicable": True,
                            "sha256": "7" * 64,
                            "schema_version": None,
                            "semantic_status": skip_like_status,
                            "semantic_valid": True,
                        }
                    },
                )
                authorization_path = self._mock_authorization(candidate_dir, source_commit="c" * 40)
                result = self._check(authorization_path)
                self.assertIn(
                    "FINAL_CANDIDATE_UPSTREAM_BINDING_CLAIM_CHECK_SEMANTIC_STATUS_INVALID", result["blockers"]
                )
                self.assertFalse(result["candidate_upstream_bindings_valid"])
                self._assert_never_creates_final_submit_or_repo_candidate(result)

    def test_legitimate_pass_status_still_passes_after_whitelist_patch(self) -> None:
        # Regression guard for the defense-in-depth patch itself: a fully
        # legitimate manifest (applicable=PASS, not-applicable=NOT_
        # APPLICABLE) must still clear the binding layer cleanly.
        self._make_case_ready_for_intake()
        candidate_dir = self._mock_candidate_package(source_commit="c" * 40)
        authorization_path = self._mock_authorization(candidate_dir, source_commit="c" * 40)
        result = self._check(authorization_path)
        self.assertTrue(result["candidate_upstream_bindings_valid"])
        for name, entry in result["candidate_upstream_bindings"].items():
            self.assertNotIn(f"FINAL_CANDIDATE_UPSTREAM_BINDING_{name.upper()}_SEMANTIC_STATUS_INVALID", result["blockers"])
        self._assert_never_creates_final_submit_or_repo_candidate(result)

    # ---- Negative: authorization disguised as production without external human confirmation ----

    def test_production_disguise_without_external_confirmation_blocks(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._mock_candidate_package(source_commit="c" * 40)
        authorization_path = self._mock_authorization(
            candidate_dir, source_commit="c" * 40, mode="production"
        )
        result = self._check(authorization_path, mode="production", dry_run=False)
        # The contract itself may validate (production mode requested with
        # a production-mode record), but production still cannot proceed
        # without a real external human confirmation loop — the function
        # always demands EXTERNAL_MANUAL_CONFIRMATION_REQUIRED once machine
        # preconditions pass, and never sets final_freeze_ready/
        # final_submit_allowed regardless.
        self.assertEqual(result["authorization_mode"], "production")
        if result["authorization_contract_valid"]:
            self.assertIn("EXTERNAL_MANUAL_CONFIRMATION_REQUIRED", result["blockers"])
        self.assertFalse(result["final_freeze_ready"])
        self.assertFalse(result["final_submit_allowed"])
        self._assert_never_creates_final_submit_or_repo_candidate(result)

    def test_mock_mode_rejected_under_production_request(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._mock_candidate_package(source_commit="c" * 40)
        # A mock_test-mode record cannot masquerade as production simply by
        # requesting requested_mode="production" — the contract itself
        # must be rejected.
        authorization_path = self._mock_authorization(candidate_dir, source_commit="c" * 40, mode="mock_test")
        result = self._check(authorization_path, mode="production", dry_run=False)
        self.assertIn("FINAL_AUTH_MOCK_NOT_ALLOWED", result["blockers"])
        self.assertFalse(result["authorization_contract_valid"])
        self._assert_never_creates_final_submit_or_repo_candidate(result)

    # ---- Cross-cutting: every failure path stays free of repo pollution ----

    def test_all_negative_paths_never_create_final_submit_or_candidate(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._mock_candidate_package(source_commit="c" * 40)
        scenarios = [
            self._candidate_package_authorization(candidate_dir, "inside.json", source_commit="c" * 40),
            self._mock_authorization(candidate_dir, source_commit="c" * 40, candidate_package_digest="0" * 64),
            self._mock_authorization(candidate_dir, source_commit="d" * 40),
        ]
        for authorization_path in scenarios:
            with self.subTest(authorization_path=str(authorization_path)):
                result = self._check(authorization_path)
                self._assert_never_creates_final_submit_or_repo_candidate(result)
                self.assertFalse(result["final_freeze_ready"])
                self.assertFalse(result["final_submit_allowed"])

    def test_source_files_not_modified(self) -> None:
        self._make_case_ready_for_intake()
        candidate_dir = self._mock_candidate_package(source_commit="c" * 40)
        authorization_path = self._mock_authorization(candidate_dir, source_commit="c" * 40)
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self._check(authorization_path)
        after = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
