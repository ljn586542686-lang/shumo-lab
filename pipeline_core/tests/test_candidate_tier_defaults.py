from __future__ import annotations

import json
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from package_candidate import CandidatePromotionClaimCheckBlocked, package_candidate


class CandidateTierDefaultsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "root"
        self.case_dir = self.root / "cases" / "case_tier_fixture"
        self.case_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, text=True, check=True
        )

    def _init_git_repo_with_commit(self) -> str:
        # P6-PV2A-B hardened the applicability policy: selection/formal tier
        # is now always source_provenance_required, so these fixtures must
        # carry a real, verifiable git commit for source_commit_after to
        # bind against — not just an upstream-status stub.
        self._git("init", "-b", "main")
        self._git("config", "user.email", "tier-defaults-fixture@example.com")
        self._git("config", "user.name", "Tier Defaults Fixture")
        (self.root / ".gitignore").write_text("work_tmp/\n", encoding="utf-8")
        self._git("add", ".gitignore")
        self._git("commit", "-m", "initial commit")
        return self._git("rev-parse", "--verify", "HEAD").stdout.strip()

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

    def _write_config(self, payload: dict[str, object]) -> None:
        payload = {"r3_required": False, **payload}
        if payload.get("submission_tier") in {"selection", "formal"}:
            summary = self.case_dir / "output" / "result_summary.json"
            summary.parent.mkdir(parents=True, exist_ok=True)
            summary.write_text(json.dumps({"status": "ok"}), encoding="utf-8")

            # P6-PV2A-B: selection/formal is always source_provenance_required
            # now, so the upstream pair must carry real, mutually-consistent
            # source_provenance, and root must be a real git repo whose HEAD
            # matches source_commit_after — otherwise the new provenance
            # finalization gate blocks before candidate mkdir, which is
            # exactly the fail-closed behavior this hardening intends for
            # selection/formal (it must never fall back to legacy mode).
            #
            # Self-reference avoidance: the upstream run_result/risk_summary
            # files (which embed source_commit_after) live under work_tmp/,
            # gitignored by the .gitignore written below — exactly like a
            # real run_case run, whose own outputs are never git-tracked
            # either (see P6-PV1B2B-D0). This means source_commit_after can
            # legitimately name the commit that contains case_dir's tracked
            # fixture files (case_config.json, output/result_summary.json)
            # without those upstream JSON files ever needing to be part of
            # that same commit — no file has to embed the hash of a commit
            # that would carry itself.
            self._init_git_repo_with_commit()
            run_dir = self.root / "work_tmp" / "run_tier_fixture"
            run_dir.mkdir(parents=True, exist_ok=True)
            risk_summary_path = run_dir / "risk_summary.json"
            run_result_path = run_dir / "run_result.json"
            upstream_run_result = run_dir / "upstream_run_result.json"
            upstream_risk_summary = run_dir / "upstream_risk_summary.json"

            guard_fields = {
                "risk_status": "PASS", "status": "ok", "fatal_count": 0,
                "upstream_content_bindings": [{
                    "logical_name": "result_summary",
                    "relative_path": "output/result_summary.json",
                    "sha256": hashlib.sha256(summary.read_bytes()).hexdigest(),
                    "required_for_candidate_validation": True,
                    "source_role": "result_summary",
                }],
            }
            payload = {
                **payload,
                "candidate_upstream_run_result_path": str(upstream_run_result.resolve()),
                "candidate_upstream_risk_summary_path": str(upstream_risk_summary.resolve()),
            }
            (self.case_dir / "case_config.json").write_text(json.dumps(payload), encoding="utf-8")

            # Commit case_dir's tracked fixture files (case_config.json,
            # output/result_summary.json) now, then read the real resulting
            # HEAD — this is the commit source_commit_after will name.
            self._git("add", "-A")
            self._git("commit", "-m", "fixture: case_dir contents")
            real_commit = self._git("rev-parse", "--verify", "HEAD").stdout.strip()

            expected_paths = [
                str(risk_summary_path.resolve().relative_to(self.root.resolve()).as_posix()),
                str((run_dir / "risk_summary.md").resolve().relative_to(self.root.resolve()).as_posix()),
                str(run_result_path.resolve().relative_to(self.root.resolve()).as_posix()),
            ]
            provenance = self._base_source_provenance(commit=real_commit, expected_paths=expected_paths)
            risk_summary_path.write_text(json.dumps({"run_id": "run_tier_fixture", "risks": [], "source_provenance": provenance}), encoding="utf-8")
            (run_dir / "risk_summary.md").write_text("# risk summary\n", encoding="utf-8")
            run_result_path.write_text(json.dumps({"run_id": "run_tier_fixture", "status": "ok", "source_provenance": provenance}), encoding="utf-8")
            upstream_run_result.write_text(
                json.dumps({"run_id": "run_tier_fixture", "status": "ok", "source_provenance": provenance, **guard_fields}),
                encoding="utf-8",
            )
            upstream_risk_summary.write_text(
                json.dumps({"run_id": "run_tier_fixture", "risks": [], "source_provenance": provenance, **guard_fields}),
                encoding="utf-8",
            )
            return
        (self.case_dir / "case_config.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_training_absent_claim_flag_remains_not_required(self) -> None:
        self._write_config({"submission_tier": "training", "core_claims": []})
        result = package_candidate(case_dir=self.case_dir, root=self.root)
        self.assertFalse(result["effective_claim_check_required"])
        self.assertEqual(result["tier_default_applied"], [])

    def test_selection_absent_claim_flag_defaults_required_and_blocks_before_creation(self) -> None:
        self._write_config({"submission_tier": "selection", "core_claims": []})
        with self.assertRaises(CandidatePromotionClaimCheckBlocked) as caught:
            package_candidate(case_dir=self.case_dir, root=self.root)
        result = caught.exception.result
        self.assertTrue(result["effective_claim_check_required"])
        self.assertIn("claim_check", result["tier_default_applied"])
        self.assertIn("CLAIM_CHECK_REQUIRED_BUT_NO_CORE_CLAIMS", result["underlying_codes"])
        self.assertFalse(result["candidate_package_created"])
        self.assertFalse((self.root / "candidate_submit").exists())

    def test_selection_missing_claim_material_blocks_before_path_preparation(self) -> None:
        claim = {
            "claim_id": "C-01",
            "claim_type": "model_selection",
            "proposed_wording_path": "paper/main.tex",
            "is_core_conclusion": True,
            "generator_role_id": "writer_role",
            "adversarial_role_id": "reviewer_role",
            "claim_type_provider_role_id": "reviewer_role",
            "claim_card_path": "output/validation/claim_checks/C-01.json",
        }
        self._write_config({"submission_tier": "selection", "core_claims": [claim]})
        with mock.patch("package_candidate._latest_package") as latest:
            with self.assertRaises(CandidatePromotionClaimCheckBlocked) as caught:
                package_candidate(case_dir=self.case_dir, root=self.root)
        latest.assert_not_called()
        self.assertIn("CLAIM_CARD_MISSING", caught.exception.result["underlying_codes"])
        self.assertFalse((self.root / "candidate_submit").exists())

    def test_selection_explicit_disable_is_respected_and_reported(self) -> None:
        self._write_config({
            "submission_tier": "selection",
            "claim_check_required": False,
            "core_claims": [],
            "human_revision_required": False,
        })
        result = package_candidate(case_dir=self.case_dir, root=self.root)
        self.assertFalse(result["effective_claim_check_required"])
        self.assertIn("claim_check", result["tier_default_explicitly_disabled"])
        self.assertIn("TIER_DEFAULT_EXPLICITLY_DISABLED: claim_check", result["warnings"])

    def test_formal_tier_does_not_default_human_revision_to_required(self) -> None:
        self._write_config({
            "submission_tier": "formal",
            "claim_check_required": False,
            "core_claims": [],
        })
        result = package_candidate(case_dir=self.case_dir, root=self.root)
        self.assertEqual(result["manifest"]["status"], "candidate")
        self.assertFalse((self.root / "final_submit").exists())


if __name__ == "__main__":
    unittest.main()
