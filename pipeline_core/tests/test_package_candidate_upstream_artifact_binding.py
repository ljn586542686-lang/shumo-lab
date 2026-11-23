from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from package_candidate import (
    CandidatePromotionUpstreamBindingBlocked,
    package_candidate,
)


class UpstreamArtifactBindingTests(unittest.TestCase):
    """P6-S2B: hash equality alone must never be trusted as proof an
    upstream artifact is safe — this suite proves package_candidate also
    parses each applicable artifact's own status semantics, and that a
    fresh, byte-identical hash bound to a BLOCK/FAIL/fatal payload still
    blocks candidate creation before mkdir.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "root"
        self.root.mkdir()
        self.case_dir = self.root / "cases" / "case_s2b"
        self.case_dir.mkdir(parents=True)

        self._git("init", "-b", "main")
        self._git("config", "user.email", "s2b-fixture@example.com")
        self._git("config", "user.name", "S2B Fixture")
        (self.root / ".gitignore").write_text("work_tmp/\n", encoding="utf-8")
        self._git("add", ".gitignore")
        self._git("commit", "-m", "initial commit")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, text=True, check=True
        )

    def _head(self) -> str:
        return self._git("rev-parse", "--verify", "HEAD").stdout.strip()

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _base_source_provenance(self, *, commit: str, expected_paths: list[str]) -> dict[str, object]:
        return {
            "source_commit_before": commit,
            "source_commit_after": commit,
            "source_branch": "main",
            "source_detached_head": False,
            "source_worktree_clean_before": True,
            "source_worktree_clean_after_raw": True,
            "source_status_entries_before": [],
            "source_status_entries_after_raw": [],
            "source_status_records_before": [],
            "source_status_records_after_raw": [],
            "source_commit_stable": True,
            "source_provenance_capture_ok": True,
            "source_provenance_status": "CAPTURED_UNCLASSIFIED_OUTPUTS",
            "source_provenance_errors": [],
            "source_write_registry_scope": "ALL_PRE_SNAPSHOT_WRITES",
            "source_write_registry_authoritative": False,
            "source_write_registry_excluded_classes": [],
            "source_written_repo_paths": [],
            "source_external_written_paths": [],
            "source_registry_errors": [],
            "source_required_write_tokens": [],
            "source_completed_write_tokens": [],
            "source_write_registry_complete": True,
            "source_expected_post_snapshot_paths": expected_paths,
            "source_expected_post_snapshot_write_verified": False,
        }

    def _build_fixture(
        self,
        *,
        submission_tier: str = "training",
        run_result_extra: dict[str, object] | None = None,
        risk_summary_extra: dict[str, object] | None = None,
        result_summary_extra: dict[str, object] | None = None,
        route_review_payload: dict[str, object] | None = None,
        xval_payload: dict[str, object] | None = None,
        claim_check_required: bool = False,
        core_claims: list[dict[str, object]] | None = None,
    ) -> None:
        result_summary = self.case_dir / "output" / "result_summary.json"
        result_summary.parent.mkdir(parents=True, exist_ok=True)
        result_summary_payload = {"status": "ok", "schema_version": "x5_f0_result_summary_v1"}
        if result_summary_extra:
            result_summary_payload.update(result_summary_extra)
        self._write_json(result_summary, result_summary_payload)

        run_dir = self.root / "work_tmp" / "run_s2b_fixture"
        run_dir.mkdir(parents=True, exist_ok=True)
        risk_summary_path = run_dir / "risk_summary.json"
        run_result_path = run_dir / "run_result.json"
        upstream_run_result = run_dir / "upstream_run_result.json"
        upstream_risk_summary = run_dir / "upstream_risk_summary.json"

        config: dict[str, object] = {
            "submission_tier": submission_tier,
            "claim_check_required": claim_check_required,
            "core_claims": core_claims if core_claims is not None else [],
            "human_revision_required": False,
            "human_revision_mode": "off",
            "r3_required": False,
            "candidate_upstream_run_result_path": str(upstream_run_result.resolve()),
            "candidate_upstream_risk_summary_path": str(upstream_risk_summary.resolve()),
        }

        route_review_path: Path | None = None
        if route_review_payload is not None:
            route_review_path = run_dir / "route_review.json"
            self._write_json(route_review_path, route_review_payload)
            config["candidate_upstream_route_review_path"] = str(route_review_path.resolve())

        xval_path: Path | None = None
        if xval_payload is not None:
            xval_path = run_dir / "xval.json"
            self._write_json(xval_path, xval_payload)
            config["candidate_upstream_xval_path"] = str(xval_path.resolve())

        self._write_json(self.case_dir / "case_config.json", config)

        self._git("add", "-A")
        self._git("commit", "-m", "fixture: case_dir contents")
        real_commit = self._head()

        expected_paths = [
            str(risk_summary_path.resolve().relative_to(self.root.resolve()).as_posix()),
            str((run_dir / "risk_summary.md").resolve().relative_to(self.root.resolve()).as_posix()),
            str(run_result_path.resolve().relative_to(self.root.resolve()).as_posix()),
        ]
        provenance = self._base_source_provenance(commit=real_commit, expected_paths=expected_paths)

        run_result_payload = {"run_id": "run_s2b_fixture", "status": "ok", "source_provenance": provenance}
        if run_result_extra:
            run_result_payload.update(run_result_extra)
        risk_summary_payload = {
            "run_id": "run_s2b_fixture",
            "risks": [],
            "schema_version": "x5_f0_risk_summary_v1",
            "source_provenance": provenance,
        }
        if risk_summary_extra:
            risk_summary_payload.update(risk_summary_extra)

        risk_summary_path.write_text(json.dumps(risk_summary_payload), encoding="utf-8")
        (run_dir / "risk_summary.md").write_text("# risk summary\n", encoding="utf-8")
        run_result_path.write_text(json.dumps(run_result_payload), encoding="utf-8")

        upstream_run_result.write_text(
            json.dumps(
                {
                    **run_result_payload,
                    "risk_status": "PASS",
                    "fatal_count": 0,
                    "upstream_content_bindings": [
                        {
                            "logical_name": "result_summary",
                            "relative_path": "output/result_summary.json",
                            "sha256": hashlib.sha256(result_summary.read_bytes()).hexdigest(),
                            "required_for_candidate_validation": True,
                            "source_role": "result_summary",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        risk_summary_upstream_guard_fields = {
            "risk_status": "PASS",
            "fatal_count": 0,
            "upstream_content_bindings": [
                {
                    "logical_name": "result_summary",
                    "relative_path": "output/result_summary.json",
                    "sha256": hashlib.sha256(result_summary.read_bytes()).hexdigest(),
                    "required_for_candidate_validation": True,
                    "source_role": "result_summary",
                }
            ],
        }
        # risk_summary_extra (e.g. risk_status="BLOCK", risks=[...]) must
        # win over these upstream-guard-pass defaults, not be clobbered by
        # them — the guard fields only fill in what the test did not
        # already specify.
        upstream_risk_summary.write_text(
            json.dumps({**risk_summary_upstream_guard_fields, **risk_summary_payload}),
            encoding="utf-8",
        )
        self.route_review_path = route_review_path
        self.xval_path = xval_path

    def _rebind_source_commit_after_extra_commit(self) -> None:
        """Call after any additional `git commit` made post-_build_fixture
        (e.g. to add a case_dir-tracked file like a human-revision record)
        so source_commit_after still names the real, current HEAD — never
        a stale ancestor commit. The upstream JSON copies live under the
        gitignored work_tmp/, so rewriting them here does not itself dirty
        the worktree.
        """
        real_commit = self._head()
        run_dir = self.root / "work_tmp" / "run_s2b_fixture"
        for name in ("run_result.json", "risk_summary.json", "upstream_run_result.json", "upstream_risk_summary.json"):
            path = run_dir / name
            payload = json.loads(path.read_text(encoding="utf-8"))
            provenance = payload.get("source_provenance")
            if isinstance(provenance, dict):
                provenance["source_commit_before"] = real_commit
                provenance["source_commit_after"] = real_commit
            self._write_json(path, payload)

    def _run(self):
        return package_candidate(case_dir=self.case_dir, root=self.root)

    def _blocked(self) -> dict[str, object]:
        with self.assertRaises(CandidatePromotionUpstreamBindingBlocked) as caught:
            self._run()
        result = caught.exception.result
        self.assertEqual(result["code"], "CANDIDATE_PROMOTION_UPSTREAM_BINDING_INVALID")
        self.assertFalse(result["candidate_package_created"])
        self.assertFalse(result["final_submit_created"])
        self.assertFalse((self.root / "candidate_submit").exists())
        self.assertFalse((self.root / "candidate_emergency").exists())
        self.assertFalse((self.root / "final_submit").exists())
        return result

    # ---- All valid: passes the binding layer ----

    def test_all_applicable_artifacts_valid_passes_binding_layer(self) -> None:
        self._build_fixture(
            route_review_payload={"route_review_status": "PASS", "blockers": []},
            xval_payload={"xval_status": "PASS", "blockers": [], "fatal_count_contribution": 0},
        )
        result = self._run()
        self.assertEqual(result["manifest"]["authoritative_result_index"]["authority_scope_key"], ["question_id", "artifact_role"])
        self.assertTrue((Path(result["package_dir"]) / "authoritative_result_index.json").is_file())
        bindings = result["manifest"]["upstream_artifact_bindings"]
        for name in ("run_result", "risk_summary", "result_summary", "route_review", "xval"):
            self.assertTrue(bindings[name]["applicable"], name)
            self.assertTrue(bindings[name]["semantic_valid"], name)
            self.assertIsInstance(bindings[name]["sha256"], str)
            self.assertEqual(len(bindings[name]["sha256"]), 64)
        self.assertIn(bindings["run_result"]["semantic_status"], {"PASS"})
        self.assertIn(bindings["risk_summary"]["semantic_status"], {"PASS"})
        # No large embedded blobs — only the summary fields per artifact.
        for name in bindings:
            self.assertLessEqual(len(bindings[name].keys()), 6)

    # ---- Hash matches but risk_summary reports BLOCK ----

    def test_hash_matches_but_risk_summary_block_blocks(self) -> None:
        self._build_fixture(risk_summary_extra={"risk_status": "BLOCK"})
        result = self._blocked()
        self.assertIn("UPSTREAM_BINDING_RISK_SUMMARY_INVALID", result["binding_blockers"])
        bindings = result["upstream_artifact_bindings"]
        self.assertEqual(bindings["risk_summary"]["semantic_status"], "BLOCK")
        self.assertFalse(bindings["risk_summary"]["semantic_valid"])
        # Hash was still computed and matches the actual file bytes —
        # proving this is a semantic failure, not a hash failure.
        self.assertIsInstance(bindings["risk_summary"]["sha256"], str)

    # ---- run_result PASS, risk_summary BLOCK: no PASS anywhere overrides a BLOCK ----

    def test_run_result_pass_risk_summary_block_blocks(self) -> None:
        self._build_fixture(
            run_result_extra={"status": "ok"},
            risk_summary_extra={"risks": [{"fatal": True, "category": "test"}]},
        )
        result = self._blocked()
        bindings = result["upstream_artifact_bindings"]
        self.assertEqual(bindings["run_result"]["semantic_status"], "PASS")
        self.assertEqual(bindings["risk_summary"]["semantic_status"], "FATAL")
        self.assertIn("UPSTREAM_BINDING_RISK_SUMMARY_INVALID", result["binding_blockers"])

    # ---- route_review hash correct but status fails ----

    def test_route_review_hash_correct_but_status_failed_blocks(self) -> None:
        self._build_fixture(route_review_payload={"route_review_status": "BLOCKED", "blockers": ["ROUTE_X"]})
        result = self._blocked()
        bindings = result["upstream_artifact_bindings"]
        self.assertEqual(bindings["route_review"]["semantic_status"], "BLOCK")
        self.assertIn("UPSTREAM_BINDING_ROUTE_REVIEW_INVALID", result["binding_blockers"])
        self.assertIsInstance(bindings["route_review"]["sha256"], str)

    # ---- xval hash correct but fatal ----

    def test_xval_hash_correct_but_fatal_blocks(self) -> None:
        self._build_fixture(
            xval_payload={"xval_status": "PASS", "fatal_count_contribution": 1, "blockers": []}
        )
        result = self._blocked()
        bindings = result["upstream_artifact_bindings"]
        self.assertEqual(bindings["xval"]["semantic_status"], "FATAL")
        self.assertIn("UPSTREAM_BINDING_XVAL_INVALID", result["binding_blockers"])
        self.assertIsInstance(bindings["xval"]["sha256"], str)

    # ---- claim-check required and unresolved: blocks ----

    def test_claim_check_required_and_unresolved_blocks(self) -> None:
        self._build_fixture(claim_check_required=True, core_claims=[])
        # claim_check_required with no core_claims makes the real (non-
        # mocked) claim_check_preflight SKIP, which _enforce_claim_check_
        # candidate_gate itself already turns into a block — this proves
        # that earlier gate's failure surfaces before the binding layer,
        # and never gets masked into a generic binding blocker.
        from package_candidate import CandidatePromotionClaimCheckBlocked

        with self.assertRaises(CandidatePromotionClaimCheckBlocked) as caught:
            self._run()
        self.assertIn("CLAIM_CHECK_REQUIRED_BUT_NO_CORE_CLAIMS", caught.exception.result["underlying_codes"])

    def test_claim_check_required_and_blocked_status_recorded_in_binding_when_reached(self) -> None:
        # Directly exercise the binding function's own claim_check
        # recording logic (independent of the earlier gate's own
        # exception), proving the binding layer would also correctly mark
        # claim_check invalid if it were ever reached with a BLOCKED status.
        from package_candidate import _build_upstream_artifact_bindings, _effective_candidate_settings

        self._build_fixture(claim_check_required=True, core_claims=[{"claim_id": "C-01"}])
        effective_settings = _effective_candidate_settings(self.case_dir)
        config = json.loads((self.case_dir / "case_config.json").read_text(encoding="utf-8"))
        claim_check_result = {"status": "BLOCKED", "blockers": ["CLAIM_CHECK_UNRESOLVED_BLOCK"], "claims": []}
        human_revision_result = {"status": "SKIPPED", "human_revision": {"required": False}}
        bindings, blockers = _build_upstream_artifact_bindings(
            self.case_dir,
            config,
            effective_settings,
            claim_check_result=claim_check_result,
            human_revision_result=human_revision_result,
        )
        self.assertFalse(bindings["claim_check"]["semantic_valid"])
        self.assertIn("UPSTREAM_BINDING_CLAIM_CHECK_INVALID", blockers)

    # ---- human revision required and not satisfied: blocks ----

    def test_human_revision_required_and_unsatisfied_blocks(self) -> None:
        from package_candidate import CandidatePromotionHumanRevisionBlocked

        self._build_fixture()
        config = json.loads((self.case_dir / "case_config.json").read_text(encoding="utf-8"))
        config["human_revision_required"] = True
        config["human_revision_mode"] = "full"
        self._write_json(self.case_dir / "case_config.json", config)
        self._git("add", "-A")
        self._git("commit", "-m", "fixture: require human revision")

        with self.assertRaises(CandidatePromotionHumanRevisionBlocked):
            self._run()

    def test_human_revision_not_required_does_not_block(self) -> None:
        self._build_fixture()
        result = self._run()
        bindings = result["manifest"]["upstream_artifact_bindings"]
        self.assertTrue(bindings["human_revision"]["semantic_valid"])

    # ---- claim_check / human_revision genuine artifact-hash binding ----

    def test_claim_check_applicable_has_real_file_hash_and_manifest_summary(self) -> None:
        card_path = self.case_dir / "output" / "validation" / "claim_checks" / "C-01.json"
        card_payload = {
            "claim_id": "C-01",
            "status": "PASS",
            "human_decision": "APPROVED",
            "recommended_decision": "PASS",
            "allowed_claim_strength": "strong",
            "machine_red_flags": [],
        }
        card_path.parent.mkdir(parents=True, exist_ok=True)
        card_path.write_text(json.dumps(card_payload), encoding="utf-8")
        expected_hash_input = [
            {"relative_path": card_path.name, "sha256": hashlib.sha256(card_path.read_bytes()).hexdigest()}
        ]

        self._build_fixture(claim_check_required=True, core_claims=[{"claim_id": "C-01"}])
        preflight = {
            "status": "PASS",
            "blockers": [],
            "claims": [
                {"claim_id": "C-01", "claim_type": "model_selection", "status": "PASS", "claim_card_path": str(card_path)}
            ],
            "final_submit_created": False,
        }
        with mock.patch("package_candidate.check_claim_check_preflight", return_value=preflight):
            result = self._run()
        bindings = result["manifest"]["upstream_artifact_bindings"]
        claim_binding = bindings["claim_check"]
        self.assertTrue(claim_binding["applicable"])
        self.assertIsInstance(claim_binding["sha256"], str)
        self.assertEqual(len(claim_binding["sha256"]), 64)
        # The hash must be derived from the real card file's own bytes, not
        # a synthetic summary of the preflight result dict.
        from package_candidate import _pc_deterministic_sha256

        self.assertEqual(claim_binding["sha256"], _pc_deterministic_sha256(expected_hash_input))
        self.assertEqual(claim_binding["semantic_status"], "PASS")
        self.assertTrue(claim_binding["semantic_valid"])
        self.assertIn("sha256", claim_binding)
        self.assertIn("schema_version", claim_binding)
        self.assertIn("semantic_status", claim_binding)
        self.assertIn("semantic_valid", claim_binding)

    def test_claim_check_hash_matches_but_unrecognized_status_blocks(self) -> None:
        card_path = self.case_dir / "output" / "validation" / "claim_checks" / "C-01.json"
        card_path.parent.mkdir(parents=True, exist_ok=True)
        card_path.write_text(json.dumps({"claim_id": "C-01"}), encoding="utf-8")

        self._build_fixture(claim_check_required=True, core_claims=[{"claim_id": "C-01"}])
        # Preflight itself reports PASS at the top level (so the earlier,
        # independent _enforce_claim_check_candidate_gate lets it through),
        # but the individual claim's own status is an unrecognized value —
        # this must still be caught by the binding layer's own parsing,
        # not waved through just because the gate-level status was PASS.
        preflight = {
            "status": "PASS",
            "blockers": [],
            "claims": [
                {
                    "claim_id": "C-01",
                    "claim_type": "model_selection",
                    "status": "SOME_UNRECOGNIZED_STATUS",
                    "claim_card_path": str(card_path),
                }
            ],
            "final_submit_created": False,
        }
        with mock.patch("package_candidate.check_claim_check_preflight", return_value=preflight):
            result = self._blocked()
        bindings = result["upstream_artifact_bindings"]
        claim_binding = bindings["claim_check"]
        self.assertIsInstance(claim_binding["sha256"], str)
        self.assertFalse(claim_binding["semantic_valid"])
        self.assertIn("UPSTREAM_BINDING_CLAIM_CHECK_INVALID", result["binding_blockers"])

    def test_human_revision_applicable_has_real_file_hash_and_manifest_summary(self) -> None:
        self._build_fixture()
        config = json.loads((self.case_dir / "case_config.json").read_text(encoding="utf-8"))
        config["human_revision_required"] = True
        config["human_revision_mode"] = "full"
        self._write_json(self.case_dir / "case_config.json", config)
        record_path = self.case_dir / "human_review" / "human_revision_record.json"
        record_payload = {
            "status": "COMPLETED",
            "reviewer_name": "Human Reviewer",
            "manual_confirmation": "reviewed manually",
            "scope": ["candidate wording"],
            "source": "human",
        }
        self._write_json(record_path, record_payload)
        self._git("add", "-A")
        self._git("commit", "-m", "fixture: require and satisfy human revision")
        self._rebind_source_commit_after_extra_commit()

        result = self._run()
        bindings = result["manifest"]["upstream_artifact_bindings"]
        revision_binding = bindings["human_revision"]
        self.assertTrue(revision_binding["applicable"])
        self.assertIsInstance(revision_binding["sha256"], str)
        self.assertEqual(revision_binding["sha256"], hashlib.sha256(record_path.read_bytes()).hexdigest())
        self.assertEqual(revision_binding["semantic_status"], "PASS")
        self.assertTrue(revision_binding["semantic_valid"])
        self.assertIn("sha256", revision_binding)
        self.assertIn("schema_version", revision_binding)
        self.assertIn("semantic_status", revision_binding)
        self.assertIn("semantic_valid", revision_binding)

    def test_human_revision_hash_matches_but_unrecognized_status_blocks_when_required(self) -> None:
        from package_candidate import _build_upstream_artifact_bindings, _effective_candidate_settings

        self._build_fixture()
        config = json.loads((self.case_dir / "case_config.json").read_text(encoding="utf-8"))
        config["human_revision_required"] = True
        config["human_revision_mode"] = "full"
        self._write_json(self.case_dir / "case_config.json", config)
        record_path = self.case_dir / "human_review" / "human_revision_record.json"
        # A record whose "status" field is present but not the recognized
        # "COMPLETED" value — hash is real and computable, but the parsed
        # semantic status is neither PASS nor a known passing state.
        self._write_json(record_path, {"status": "PENDING_REVIEW", "source": "human"})

        effective_settings = _effective_candidate_settings(self.case_dir)
        human_revision_result = {
            "status": "MANUAL_REVIEW_REQUIRED",
            "human_revision": {"required": True, "record_path": "human_review/human_revision_record.json"},
        }
        bindings, blockers = _build_upstream_artifact_bindings(
            self.case_dir,
            config,
            effective_settings,
            claim_check_result={"status": "SKIPPED", "claims": []},
            human_revision_result=human_revision_result,
        )
        self.assertIsInstance(bindings["human_revision"]["sha256"], str)
        self.assertEqual(
            bindings["human_revision"]["sha256"], hashlib.sha256(record_path.read_bytes()).hexdigest()
        )
        self.assertFalse(bindings["human_revision"]["semantic_valid"])
        self.assertIn("UPSTREAM_BINDING_HUMAN_REVISION_INVALID", blockers)

    def test_claim_check_binding_failure_blocks_before_mkdir(self) -> None:
        card_path = self.case_dir / "output" / "validation" / "claim_checks" / "C-01.json"
        card_path.parent.mkdir(parents=True, exist_ok=True)
        card_path.write_text(json.dumps({"claim_id": "C-01"}), encoding="utf-8")
        self._build_fixture(claim_check_required=True, core_claims=[{"claim_id": "C-01"}])
        preflight = {
            "status": "PASS",
            "blockers": [],
            "claims": [
                {
                    "claim_id": "C-01",
                    "claim_type": "model_selection",
                    "status": "SOME_UNRECOGNIZED_STATUS",
                    "claim_card_path": str(card_path),
                }
            ],
            "final_submit_created": False,
        }
        with mock.patch("package_candidate.check_claim_check_preflight", return_value=preflight):
            with mock.patch("package_candidate.Path.mkdir") as mkdir:
                self._blocked()
        mkdir.assert_not_called()

    # ---- Unknown schema version blocks ----

    def test_unknown_schema_version_blocks(self) -> None:
        self._build_fixture(risk_summary_extra={"schema_version": "totally_unknown_v99"})
        result = self._blocked()
        bindings = result["upstream_artifact_bindings"]
        self.assertEqual(bindings["risk_summary"]["semantic_status"], "UNKNOWN")
        self.assertFalse(bindings["risk_summary"]["semantic_valid"])
        self.assertIn("UPSTREAM_BINDING_RISK_SUMMARY_INVALID", result["binding_blockers"])

    def test_non_string_schema_version_blocks(self) -> None:
        self._build_fixture(risk_summary_extra={"schema_version": 12345})
        result = self._blocked()
        bindings = result["upstream_artifact_bindings"]
        self.assertFalse(bindings["risk_summary"]["semantic_valid"])

    def test_missing_schema_version_without_mandatory_contract_is_allowed(self) -> None:
        # run_result has no schema_version contract anywhere in its real
        # producer (run_case.py never sets one) — S2B must not fabricate a
        # "field required" rule for it. Absence must be schema_version:
        # null, never a blocker.
        self._build_fixture()
        result = self._run()
        bindings = result["manifest"]["upstream_artifact_bindings"]
        self.assertIsNone(bindings["run_result"]["schema_version"])
        self.assertTrue(bindings["run_result"]["semantic_valid"])

    def test_declared_supported_schema_version_is_allowed(self) -> None:
        self._build_fixture(risk_summary_extra={"schema_version": "x5_f0_risk_summary_v1"})
        result = self._run()
        bindings = result["manifest"]["upstream_artifact_bindings"]
        self.assertEqual(bindings["risk_summary"]["schema_version"], "x5_f0_risk_summary_v1")
        self.assertTrue(bindings["risk_summary"]["semantic_valid"])

    # ---- Stale hash after artifact mutation ----

    def test_artifact_modified_after_fixture_produces_different_hash_and_blocks_on_semantics(self) -> None:
        self._build_fixture()
        # Directly mutate the upstream risk_summary copy in work_tmp/
        # (gitignored, so this does not need a new commit) to a BLOCK
        # status after the fixture was built — simulating a stale/tampered
        # artifact whose hash will differ from what a fresh, valid run
        # would have produced.
        run_dir = self.root / "work_tmp" / "run_s2b_fixture"
        upstream_risk_summary = run_dir / "upstream_risk_summary.json"
        before_hash = hashlib.sha256(upstream_risk_summary.read_bytes()).hexdigest()
        payload = json.loads(upstream_risk_summary.read_text(encoding="utf-8"))
        payload["risk_status"] = "BLOCK"
        upstream_risk_summary.write_text(json.dumps(payload), encoding="utf-8")
        after_hash = hashlib.sha256(upstream_risk_summary.read_bytes()).hexdigest()
        self.assertNotEqual(before_hash, after_hash)

        result = self._blocked()
        bindings = result["upstream_artifact_bindings"]
        self.assertEqual(bindings["risk_summary"]["sha256"], after_hash)
        self.assertFalse(bindings["risk_summary"]["semantic_valid"])

    # ---- Failure happens before candidate mkdir ----

    def test_binding_failure_blocks_before_candidate_mkdir(self) -> None:
        self._build_fixture(risk_summary_extra={"risk_status": "BLOCK"})
        with mock.patch("package_candidate.Path.mkdir") as mkdir:
            self._blocked()
        mkdir.assert_not_called()

    def test_no_repo_root_candidate_or_final_created(self) -> None:
        self._build_fixture(risk_summary_extra={"risk_status": "BLOCK"})
        self._blocked()
        self.assertFalse((self.root / "candidate_submit").exists())
        self.assertFalse((self.root / "candidate_emergency").exists())
        self.assertFalse((self.root / "final_submit").exists())

    def test_final_submit_fields_never_flipped_on_pass(self) -> None:
        self._build_fixture()
        result = self._run()
        manifest = result["manifest"]
        self.assertFalse(manifest["final_submit_created"])
        self.assertFalse(manifest["for_official_submission"])
        self.assertNotIn("final_submit_allowed", manifest)


if __name__ == "__main__":
    unittest.main()
