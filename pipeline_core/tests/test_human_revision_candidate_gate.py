from __future__ import annotations

import json
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from package_candidate import (
    CandidatePromotionClaimCheckBlocked,
    CandidatePromotionHumanRevisionBlocked,
    package_candidate,
)


class HumanRevisionCandidateGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "root"
        self.case_dir = self.root / "cases" / "case_test"
        self.case_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, text=True, check=True
        )

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

    def _write_config(self, **overrides: object) -> None:
        config = {
            "claim_check_required": False,
            "core_claims": [],
            "submission_tier": "training",
            "human_revision_mode": "off",
            "human_revision_required": False,
            "human_revision_record_path": "human_review/human_revision_record.json",
            "r3_required": False,
        }
        config.update(overrides)
        if config.get("submission_tier") in {"selection", "formal"}:
            summary = self.case_dir / "output" / "result_summary.json"
            summary.parent.mkdir(parents=True, exist_ok=True)
            summary.write_text(json.dumps({"status": "ok"}), encoding="utf-8")

            # P6-PV2A-B: selection/formal is always source_provenance_required
            # now. Self-reference avoidance mirrors test_candidate_tier_
            # defaults.py: the upstream run_result/risk_summary files live
            # under work_tmp/ (gitignored), so source_commit_after can name
            # the commit that carries case_dir's tracked fixture files
            # without those upstream JSON files needing to be part of that
            # same commit.
            self._git("init", "-b", "main")
            self._git("config", "user.email", "human-revision-fixture@example.com")
            self._git("config", "user.name", "Human Revision Fixture")
            (self.root / ".gitignore").write_text("work_tmp/\n", encoding="utf-8")
            self._git("add", ".gitignore")
            self._git("commit", "-m", "initial commit")

            run_dir = self.root / "work_tmp" / "run_human_revision_fixture"
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
            config["candidate_upstream_run_result_path"] = str(upstream_run_result.resolve())
            config["candidate_upstream_risk_summary_path"] = str(upstream_risk_summary.resolve())
            (self.case_dir / "case_config.json").write_text(json.dumps(config), encoding="utf-8")

            self._git("add", "-A")
            self._git("commit", "-m", "fixture: case_dir contents")
            real_commit = self._git("rev-parse", "--verify", "HEAD").stdout.strip()

            expected_paths = [
                str(risk_summary_path.resolve().relative_to(self.root.resolve()).as_posix()),
                str((run_dir / "risk_summary.md").resolve().relative_to(self.root.resolve()).as_posix()),
                str(run_result_path.resolve().relative_to(self.root.resolve()).as_posix()),
            ]
            provenance = self._base_source_provenance(commit=real_commit, expected_paths=expected_paths)
            risk_summary_path.write_text(json.dumps({"run_id": "run_human_revision_fixture", "risks": [], "source_provenance": provenance}), encoding="utf-8")
            (run_dir / "risk_summary.md").write_text("# risk summary\n", encoding="utf-8")
            run_result_path.write_text(json.dumps({"run_id": "run_human_revision_fixture", "status": "ok", "source_provenance": provenance}), encoding="utf-8")
            upstream_run_result.write_text(
                json.dumps({"run_id": "run_human_revision_fixture", "status": "ok", "source_provenance": provenance, **guard_fields}),
                encoding="utf-8",
            )
            upstream_risk_summary.write_text(
                json.dumps({"run_id": "run_human_revision_fixture", "risks": [], "source_provenance": provenance, **guard_fields}),
                encoding="utf-8",
            )
            return
        (self.case_dir / "case_config.json").write_text(json.dumps(config), encoding="utf-8")

    def _write_record(self, **overrides: object) -> None:
        record = {
            "status": "COMPLETED",
            "reviewer_name": "Human Reviewer",
            "reviewer_role": "manual reviewer",
            "reviewed_at": "2026-07-11T12:00:00+08:00",
            "scope": ["candidate wording"],
            "manual_confirmation": "I manually reviewed the candidate wording.",
            "source": "human",
        }
        record.update(overrides)
        path = self.case_dir / "human_review" / "human_revision_record.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record), encoding="utf-8")

    def _assert_human_blocked(self, expected_code: str) -> dict[str, object]:
        with self.assertRaises(CandidatePromotionHumanRevisionBlocked) as caught:
            package_candidate(case_dir=self.case_dir, root=self.root)
        result = caught.exception.result
        self.assertEqual(result["code"], "CANDIDATE_PROMOTION_HUMAN_REVISION_BLOCKED")
        self.assertIn(expected_code, result["underlying_codes"])
        self.assertFalse(result["candidate_package_created"])
        self.assertFalse(result["final_submit_created"])
        self.assertFalse((self.root / "candidate_submit").exists())
        return result

    def test_default_training_allows_candidate_export(self) -> None:
        self._write_config()
        result = package_candidate(case_dir=self.case_dir, root=self.root)
        self.assertTrue((self.root / result["package_dir"]).is_dir())

    def test_selection_recommendation_does_not_block_when_not_required(self) -> None:
        self._write_config(
            submission_tier="selection",
            human_revision_mode="full",
            human_revision_required=False,
        )
        result = package_candidate(case_dir=self.case_dir, root=self.root)
        self.assertTrue((self.root / result["package_dir"]).is_dir())

    def test_required_missing_record_blocks_without_candidate_directory(self) -> None:
        self._write_config(human_revision_required=True, human_revision_mode="full")
        self._assert_human_blocked("HUMAN_REVISION_RECORD_MISSING")

    def test_required_non_human_record_blocks(self) -> None:
        self._write_config(human_revision_required=True, human_revision_mode="full")
        self._write_record(source="ai")
        self._assert_human_blocked("HUMAN_REVISION_RECORD_NOT_HUMAN")

    def test_required_pending_record_blocks(self) -> None:
        self._write_config(human_revision_required=True, human_revision_mode="full")
        self._write_record(status="PENDING")
        self._assert_human_blocked("HUMAN_REVISION_RECORD_INCOMPLETE")

    def test_required_valid_human_record_allows_candidate_export(self) -> None:
        self._write_config(human_revision_required=True, human_revision_mode="full")
        self._write_record()
        result = package_candidate(case_dir=self.case_dir, root=self.root)
        self.assertTrue((self.root / result["package_dir"]).is_dir())

    def test_claim_then_human_gates_run_before_candidate_path_preparation(self) -> None:
        self._write_config()
        order: list[str] = []
        claim = {"status": "SKIPPED", "blockers": [], "final_submit_created": False}
        revision = {
            "status": "SKIPPED",
            "blockers": [],
            "warnings": [],
            "human_revision": {"required": False},
            "final_submit_created": False,
        }

        def claim_check(_case_dir: Path) -> dict[str, object]:
            order.append("claim")
            return claim

        def human_check(_case_dir: Path) -> dict[str, object]:
            order.append("human")
            return revision

        def latest(_base_dir: Path, _prefix: str) -> None:
            order.append("path_preparation")
            return None

        with (
            mock.patch("package_candidate.check_claim_check_preflight", side_effect=claim_check),
            mock.patch("package_candidate.check_human_revision", side_effect=human_check),
            mock.patch("package_candidate._latest_package", side_effect=latest),
        ):
            package_candidate(case_dir=self.case_dir, root=self.root)
        self.assertEqual(order[:3], ["claim", "human", "path_preparation"])

    def test_claim_block_prevents_human_gate_and_candidate_creation(self) -> None:
        self._write_config(claim_check_required=True)
        with mock.patch("package_candidate.check_human_revision") as human_check:
            with self.assertRaises(CandidatePromotionClaimCheckBlocked):
                package_candidate(case_dir=self.case_dir, root=self.root)
        human_check.assert_not_called()
        self.assertFalse((self.root / "candidate_submit").exists())

    def test_blocked_human_gate_does_not_call_path_preparation(self) -> None:
        self._write_config(human_revision_required=True, human_revision_mode="full")
        with mock.patch("package_candidate._latest_package") as latest:
            self._assert_human_blocked("HUMAN_REVISION_RECORD_MISSING")
        latest.assert_not_called()

    def test_candidate_export_never_creates_final_submit(self) -> None:
        self._write_config()
        result = package_candidate(case_dir=self.case_dir, root=self.root)
        self.assertFalse((self.root / "final_submit").exists())
        self.assertFalse(result["manifest"]["final_submit_created"])


if __name__ == "__main__":
    unittest.main()
