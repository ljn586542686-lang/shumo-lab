from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from package_candidate import (
    CandidatePromotionClaimCheckBlocked,
    CandidatePromotionProvenanceBlocked,
    CandidatePromotionUpstreamStatusBlocked,
    package_candidate,
)


class ClaimCheckIndependentBlockerTests(unittest.TestCase):
    """P6-PC1B-CHECK: prove the Claim Check blocker fires on its own merits
    once every earlier gate (upstream status / content-binding freshness /
    P6-PV2A-B source provenance finalization) has already PASSed — it must
    never be masked by, or mistaken for, CANDIDATE_PROMOTION_UPSTREAM_STATUS_STALE
    or CANDIDATE_PROMOTION_PROVENANCE_BLOCKED.

    Fixture strategy: build a selection-tier case (the strictest tier, where
    every earlier gate is active) whose upstream status, content bindings,
    and source_provenance are all genuinely fresh/valid/passing — mirroring
    the same self-reference-safe git fixture pattern already established in
    test_package_candidate_provenance_finalization.py — then vary only the
    claim-check preflight outcome.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "root"
        self.root.mkdir()
        self.case_dir = self.root / "cases" / "case_claim_check"
        self.case_dir.mkdir(parents=True)

        self._git("init", "-b", "main")
        self._git("config", "user.email", "claim-check-fixture@example.com")
        self._git("config", "user.name", "Claim Check Fixture")
        # work_tmp/ gitignored, same self-reference-avoidance rationale as
        # test_package_candidate_provenance_finalization.py's setUp.
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

    def _build_positive_fixture(
        self,
        *,
        submission_tier: str = "selection",
        claim_check_required: bool = False,
        core_claims: list[dict[str, object]] | None = None,
    ) -> None:
        """Write a case_config.json + upstream pair that clears, in order:
        upstream status guard (fresh content bindings), source provenance
        finalization (P6-PV2A-B), leaving only claim-check as the first gate
        a test can still make fail. human-revision stays at its default
        (not required), so it never interferes either.
        """
        result_summary = self.case_dir / "output" / "result_summary.json"
        result_summary.parent.mkdir(parents=True, exist_ok=True)
        result_summary.write_text(json.dumps({"status": "ok"}), encoding="utf-8")

        run_dir = self.root / "work_tmp" / "run_claim_check_fixture"
        run_dir.mkdir(parents=True, exist_ok=True)
        risk_summary_path = run_dir / "risk_summary.json"
        run_result_path = run_dir / "run_result.json"
        upstream_run_result = run_dir / "upstream_run_result.json"
        upstream_risk_summary = run_dir / "upstream_risk_summary.json"

        commit_before_fixture = self._head()
        expected_paths = [
            str(risk_summary_path.resolve().relative_to(self.root.resolve()).as_posix()),
            str((run_dir / "risk_summary.md").resolve().relative_to(self.root.resolve()).as_posix()),
            str(run_result_path.resolve().relative_to(self.root.resolve()).as_posix()),
        ]
        # Fresh, matching content binding for the upstream status guard.
        guard_fields = {
            "risk_status": "PASS",
            "status": "ok",
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

        config = {
            "submission_tier": submission_tier,
            "claim_check_required": claim_check_required,
            "core_claims": core_claims if core_claims is not None else [],
            # This fixture exercises candidate packaging/claim/provenance
            # gates only; it does not represent an automatic finalization
            # path, so the explicit legacy/manual R3 state is intentional.
            "r3_required": False,
            "human_revision_required": False,
            "human_revision_mode": "off",
            "candidate_upstream_run_result_path": str(upstream_run_result.resolve()),
            "candidate_upstream_risk_summary_path": str(upstream_risk_summary.resolve()),
        }
        self._write_json(self.case_dir / "case_config.json", config)

        # Commit case_dir's tracked fixture (case_config.json,
        # output/result_summary.json) so the worktree is clean at packaging
        # time, then bind source_commit_after to the real resulting HEAD —
        # never to a commit that would need to contain its own hash.
        self._git("add", "-A")
        self._git("commit", "-m", "fixture: case_dir contents")
        real_commit = self._head()
        self.assertNotEqual(real_commit, commit_before_fixture)

        provenance = self._base_source_provenance(commit=real_commit, expected_paths=expected_paths)
        risk_summary_path.write_text(
            json.dumps({"run_id": "run_claim_check_fixture", "risks": [], "source_provenance": provenance}),
            encoding="utf-8",
        )
        (run_dir / "risk_summary.md").write_text("# risk summary\n", encoding="utf-8")
        run_result_path.write_text(
            json.dumps({"run_id": "run_claim_check_fixture", "status": "ok", "source_provenance": provenance}),
            encoding="utf-8",
        )
        upstream_run_result.write_text(
            json.dumps(
                {"run_id": "run_claim_check_fixture", "status": "ok", "source_provenance": provenance, **guard_fields}
            ),
            encoding="utf-8",
        )
        upstream_risk_summary.write_text(
            json.dumps(
                {"run_id": "run_claim_check_fixture", "risks": [], "source_provenance": provenance, **guard_fields}
            ),
            encoding="utf-8",
        )

    def _run(self):
        return package_candidate(case_dir=self.case_dir, root=self.root)

    def _assert_no_stale_or_provenance_masking(self, blockers_or_underlying: list[str]) -> None:
        self.assertNotIn("CANDIDATE_PROMOTION_UPSTREAM_STATUS_STALE", blockers_or_underlying)
        self.assertNotIn("UPSTREAM_STATUS_STALE", blockers_or_underlying)
        self.assertNotIn("CANDIDATE_PROMOTION_PROVENANCE_BLOCKED", blockers_or_underlying)

    # ---- Sanity: the positive fixture genuinely clears every earlier gate ----

    def test_positive_fixture_reaches_claim_check_gate_pass(self) -> None:
        self._build_positive_fixture()
        # claim_check_required stays False and core_claims stays [] here,
        # so the real (non-mocked) preflight legitimately SKIPs — proving
        # upstream status + provenance gates were both already cleared
        # without needing to touch claim-check at all.
        result = self._run()
        self.assertTrue((self.root / result["package_dir"]).is_dir())
        verification = result["manifest"]["source_provenance_verification"]
        self.assertTrue(verification["source_provenance_finalization_passed"])
        self.assertEqual(verification["blockers"], [])

    # ---- A. Claim Check required, but preflight explicitly reports failure/blocked ----

    def test_claim_check_explicit_failure_blocks_independently(self) -> None:
        self._build_positive_fixture(claim_check_required=True, core_claims=[])
        preflight = {
            "status": "BLOCKED",
            "blockers": ["CLAIM_CHECK_UNRESOLVED_BLOCK"],
            "final_submit_created": False,
        }
        with mock.patch("package_candidate.check_claim_check_preflight", return_value=preflight):
            with self.assertRaises(CandidatePromotionClaimCheckBlocked) as caught:
                self._run()
        result = caught.exception.result
        self.assertEqual(result["code"], "CANDIDATE_PROMOTION_CLAIM_CHECK_BLOCKED")
        self.assertIn("CLAIM_CHECK_UNRESOLVED_BLOCK", result["underlying_codes"])
        self._assert_no_stale_or_provenance_masking(result["underlying_codes"])
        self.assertNotEqual(result["code"], "CANDIDATE_PROMOTION_UPSTREAM_STATUS_STALE")
        self.assertFalse(result["candidate_package_created"])
        self.assertFalse((self.root / "candidate_submit").exists())
        self.assertFalse((self.root / "candidate_emergency").exists())
        self.assertFalse((self.root / "final_submit").exists())

    # ---- B. Claim Check required, but artifact (claim card) is missing ----

    def test_claim_check_missing_artifact_blocks_independently(self) -> None:
        claim = {
            "claim_id": "C-01",
            "claim_type": "model_selection",
            "proposed_wording_path": "paper/main.tex",
            "is_core_conclusion": True,
            "generator_role_id": "writer_role",
            "adversarial_role_id": "independent_reviewer",
            "claim_type_provider_role_id": "independent_reviewer",
            "claim_card_path": "output/validation/claim_checks/C-01.json",
        }
        self._build_positive_fixture(claim_check_required=True, core_claims=[claim])
        # Real (non-mocked) preflight: the claim card file was never
        # written, so this exercises production claim_check_preflight logic
        # directly, not a stand-in.
        with self.assertRaises(CandidatePromotionClaimCheckBlocked) as caught:
            self._run()
        result = caught.exception.result
        self.assertEqual(result["code"], "CANDIDATE_PROMOTION_CLAIM_CHECK_BLOCKED")
        self.assertIn("CLAIM_CARD_MISSING", result["underlying_codes"])
        self._assert_no_stale_or_provenance_masking(result["underlying_codes"])
        self.assertFalse(result["candidate_package_created"])
        self.assertFalse((self.root / "candidate_submit").exists())
        self.assertFalse((self.root / "candidate_emergency").exists())
        self.assertFalse((self.root / "final_submit").exists())

    def test_claim_check_required_but_no_core_claims_blocks_independently(self) -> None:
        self._build_positive_fixture(claim_check_required=True, core_claims=[])
        with self.assertRaises(CandidatePromotionClaimCheckBlocked) as caught:
            self._run()
        result = caught.exception.result
        self.assertIn("CLAIM_CHECK_REQUIRED_BUT_NO_CORE_CLAIMS", result["underlying_codes"])
        self._assert_no_stale_or_provenance_masking(result["underlying_codes"])
        self.assertFalse(result["candidate_package_created"])

    # ---- C. Same fixture, claim check flipped to a legitimate PASS ----

    def test_claim_check_pass_clears_gate_on_identical_fixture(self) -> None:
        self._build_positive_fixture(claim_check_required=True, core_claims=[])
        preflight = {"status": "PASS", "blockers": [], "final_submit_created": False}
        with mock.patch("package_candidate.check_claim_check_preflight", return_value=preflight):
            # Proving the flow proceeds past the claim-check gate is
            # sufficient; a full candidate creation is already covered by
            # the sanity test above and by test_claim_check_candidate_
            # promotion.py's own PASS-path tests.
            try:
                self._run()
            except CandidatePromotionClaimCheckBlocked:
                self.fail("claim-check gate must not block once preflight reports PASS")

    # ---- Ordering: the first substantive blocker is the claim-check one ----

    def test_claim_check_blocker_is_first_substantive_blocker_not_stale_or_provenance(self) -> None:
        self._build_positive_fixture(claim_check_required=True, core_claims=[])
        preflight = {
            "status": "BLOCKED",
            "blockers": ["CLAIM_CHECK_UNRESOLVED_BLOCK"],
            "final_submit_created": False,
        }
        with mock.patch("package_candidate.check_claim_check_preflight", return_value=preflight):
            with self.assertRaises(CandidatePromotionClaimCheckBlocked) as caught:
                self._run()
        result = caught.exception.result
        # The exception type itself proves the upstream-status guard and
        # the provenance-finalization guard already returned cleanly
        # (neither CandidatePromotionUpstreamStatusBlocked nor
        # CandidatePromotionProvenanceBlocked was raised first) — this is
        # the actual, structural proof of "claim-check blocker is first",
        # since package_candidate() raises synchronously at the first gate
        # that fails and control could not have reached this point
        # otherwise.
        self.assertIsInstance(caught.exception, CandidatePromotionClaimCheckBlocked)
        self.assertEqual(result["code"], "CANDIDATE_PROMOTION_CLAIM_CHECK_BLOCKED")
        self._assert_no_stale_or_provenance_masking(result["underlying_codes"])
        self._assert_no_stale_or_provenance_masking([result["code"]])

    def test_no_earlier_gate_raises_for_the_positive_fixture(self) -> None:
        # Negative-space check: confirm the positive fixture itself does
        # NOT raise either earlier gate's exception when claim-check is
        # left at its default (not required) — isolating that any
        # blocking seen in the A/B tests above is attributable solely to
        # claim-check, not to fixture staleness.
        self._build_positive_fixture()
        try:
            self._run()
        except (CandidatePromotionUpstreamStatusBlocked, CandidatePromotionProvenanceBlocked) as exc:
            self.fail(f"positive fixture unexpectedly failed an earlier gate: {exc.result}")


if __name__ == "__main__":
    unittest.main()
