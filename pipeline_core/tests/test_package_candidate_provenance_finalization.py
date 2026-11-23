from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from package_candidate import (
    CandidatePromotionProvenanceBlocked,
    CandidatePromotionUpstreamStatusBlocked,
    package_candidate,
)
from scripts.stabilization_freeze import evaluate


class PackageCandidateProvenanceFinalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "root"
        self.root.mkdir()
        self.case_dir = self.root / "cases" / "case_pv2a"
        self.case_dir.mkdir(parents=True)

        self._git("init", "-b", "main")
        self._git("config", "user.email", "pv2a-fixture@example.com")
        self._git("config", "user.name", "PV2A Fixture")
        # work_tmp/ is gitignored in the real repository (see .gitignore at
        # repo root) — run_case's own outputs (risk_summary.json,
        # run_result.json, etc.) live there and are never git-tracked in
        # production. Mirroring that here means fixture files under
        # work_tmp/ never appear in `git status` or `git add -A` at all,
        # which is exactly what lets source_commit_after legitimately name
        # a commit that predates (and never needs to contain) run_case's
        # own output — matching the self-reference-avoidance property
        # documented in the P6-PV1B2B-D0 audit.
        (self.root / ".gitignore").write_text("work_tmp/\n", encoding="utf-8")
        self._git("add", ".gitignore")
        self._git("commit", "-m", "initial commit")
        self.commit_a = self._head()

        (self.root / "README.md").write_text("fixture repo v2\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "second commit")
        self.commit_b = self._head()

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

    def _upstream_guard_pass_fields(self) -> dict[str, object]:
        """Extra fields so the pre-existing _enforce_upstream_status_candidate_guard
        also PASSes for selection/formal tier cases, letting execution reach
        the new provenance-finalization check under test.
        """
        summary = self.case_dir / "output" / "result_summary.json"
        summary.parent.mkdir(parents=True, exist_ok=True)
        if not summary.exists():
            summary.write_text(json.dumps({"status": "ok"}), encoding="utf-8")
        return {
            "risk_status": "PASS",
            "fatal_count": 0,
            "upstream_content_bindings": [
                {
                    "logical_name": "result_summary",
                    "relative_path": "output/result_summary.json",
                    "sha256": hashlib.sha256(summary.read_bytes()).hexdigest(),
                    "required_for_candidate_validation": True,
                    "source_role": "result_summary",
                }
            ],
        }

    def _write_upstream_pair(
        self,
        *,
        run_id: str = "run_pv2a_fixture",
        provenance_overrides: dict[str, object] | None = None,
        risk_provenance_overrides: dict[str, object] | None = None,
        run_dir_name: str = "run_pv2a_fixture",
        commit: str | None = None,
        write_expected_files: bool = True,
        extra_written_repo_paths: list[str] | None = None,
        with_provenance: bool = True,
        upstream_guard_pass: bool = False,
    ) -> dict[str, object]:
        # commit is a placeholder here (the fixture files this function
        # writes are not committed yet) — _config() commits everything and
        # rewrites source_commit_before/source_commit_after to the resulting
        # real commit, unless an explicit `commit` override is requested by
        # a test that specifically wants a stale/nonexistent commit hash.
        commit = commit or "PENDING_FIXTURE_COMMIT"
        run_dir = self.root / "work_tmp" / run_dir_name
        risk_summary_path = run_dir / "risk_summary.json"
        run_result_path = run_dir / "run_result.json"
        expected_paths = [
            self._rel(risk_summary_path),
            self._rel(run_dir / "risk_summary.md"),
            self._rel(run_result_path),
        ]
        provenance = self._base_source_provenance(commit=commit, expected_paths=expected_paths)
        if extra_written_repo_paths:
            provenance["source_written_repo_paths"] = list(extra_written_repo_paths)
        if provenance_overrides:
            provenance.update(provenance_overrides)

        risk_provenance = dict(provenance)
        if risk_provenance_overrides:
            risk_provenance.update(risk_provenance_overrides)

        self._write_json(
            run_result_path,
            {"run_id": run_id, "status": "ok", "source_provenance": provenance},
        )
        self._write_json(
            risk_summary_path,
            {"run_id": run_id, "risks": [], "source_provenance": risk_provenance},
        )
        if write_expected_files:
            risk_summary_path.write_text(risk_summary_path.read_text(encoding="utf-8"), encoding="utf-8")
            (run_dir / "risk_summary.md").write_text("# risk summary\n", encoding="utf-8")

        guard_fields = self._upstream_guard_pass_fields() if upstream_guard_pass else {}

        # Deliberately placed under work_tmp/ (gitignored, see setUp), not
        # case_dir/output/validation/ — these upstream copies embed
        # source_commit_after, so keeping them outside git tracking avoids
        # the same self-reference problem run_case's own writes avoid: a
        # committed file cannot correctly embed the hash of the commit that
        # would carry it.
        upstream_run_result = run_dir / "upstream_run_result.json"
        upstream_risk_summary = run_dir / "upstream_risk_summary.json"
        run_result_upstream_payload: dict[str, object] = {"run_id": run_id, "status": "ok", **guard_fields}
        risk_summary_upstream_payload: dict[str, object] = {"run_id": run_id, "risks": [], **guard_fields}
        if with_provenance:
            run_result_upstream_payload["source_provenance"] = provenance
            risk_summary_upstream_payload["source_provenance"] = risk_provenance
        self._write_json(upstream_run_result, run_result_upstream_payload)
        self._write_json(upstream_risk_summary, risk_summary_upstream_payload)
        return {
            "run_id": run_id,
            "upstream_run_result": upstream_run_result,
            "upstream_risk_summary": upstream_risk_summary,
            "provenance": provenance,
            "risk_provenance": risk_provenance,
            "commit_override": commit if commit != "PENDING_FIXTURE_COMMIT" else None,
            "with_provenance": with_provenance,
        }

    def _finalize_fixture_commit(self, upstream: dict[str, object]) -> None:
        """Stage and commit all fixture files, then bind source_commit_* to it.

        Real run_case output would already be part of a clean, committed
        worktree by the time package_candidate runs — this mirrors that by
        committing the case/work_tmp fixture files this test wrote, unless
        the test explicitly requested a stale/nonexistent commit override
        (test_head_drift_blocks / test_source_commit_missing_blocks), in
        which case the placeholder commit is left untouched and the
        resulting worktree is intentionally left uncommitted-dirty or
        pointed at a foreign commit, which is exactly what those tests
        verify blocks packaging.
        """
        if upstream.get("commit_override") is not None:
            return
        # A real run_case run captures its "after" git snapshot before the
        # commit that would carry these very files ever exists — it does
        # not (and structurally cannot) embed the hash of a commit that
        # contains its own output. This fixture mirrors that: commit once
        # with a placeholder commit hash still embedded in the upstream
        # JSON content, then treat that resulting commit as the fixed
        # "source_commit_after" going forward without re-committing (a
        # second commit would just shift HEAD again, reproducing the same
        # self-reference problem one level up).
        self._git("add", "-A")
        self._git("commit", "-m", "fixture: case + run_case outputs")
        real_commit = self._head()
        upstream["provenance"]["source_commit_before"] = real_commit
        upstream["provenance"]["source_commit_after"] = real_commit
        upstream["risk_provenance"]["source_commit_before"] = real_commit
        upstream["risk_provenance"]["source_commit_after"] = real_commit
        if not upstream.get("with_provenance", True):
            return
        for key in ("upstream_run_result", "upstream_risk_summary"):
            path: Path = upstream[key]
            payload = json.loads(path.read_text(encoding="utf-8"))
            if "source_provenance" not in payload:
                continue
            payload["source_provenance"]["source_commit_before"] = real_commit
            payload["source_provenance"]["source_commit_after"] = real_commit
            self._write_json(path, payload)

    def _rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root.resolve()).as_posix()

    def _config(self, upstream: dict[str, object], **extra: object) -> None:
        payload = {
            "submission_tier": "training",
            "claim_check_required": False,
            "core_claims": [],
            "human_revision_required": False,
            "human_revision_mode": "off",
            "r3_required": False,
            "candidate_upstream_run_result_path": str(Path(upstream["upstream_run_result"]).resolve()),
            "candidate_upstream_risk_summary_path": str(Path(upstream["upstream_risk_summary"]).resolve()),
        }
        payload.update(extra)
        self._write_json(self.case_dir / "case_config.json", payload)
        self._finalize_fixture_commit(upstream)

    def _rel_case(self, path: Path) -> str:
        return path.resolve().relative_to(self.case_dir.resolve()).as_posix()

    def _blocked(self) -> dict[str, object]:
        with self.assertRaises(CandidatePromotionProvenanceBlocked) as caught:
            package_candidate(case_dir=self.case_dir, root=self.root)
        result = caught.exception.result
        self.assertEqual(result["code"], "CANDIDATE_PROMOTION_PROVENANCE_BLOCKED")
        self.assertFalse(result["candidate_package_created"])
        self.assertFalse(result["final_submit_created"])
        self.assertFalse((self.root / "candidate_submit").exists())
        self.assertFalse((self.root / "candidate_emergency").exists())
        self.assertFalse((self.root / "final_submit").exists())
        return result

    def _blocked_by_any_candidate_gate(self) -> None:
        """For scenarios where an earlier, pre-existing gate (the upstream
        status guard) legitimately fires first — e.g. selection/formal with
        no upstream path configured at all is already rejected by
        _enforce_upstream_status_candidate_guard before the new provenance
        check ever runs. Both outcomes prove the same thing this suite
        cares about: no candidate directory gets created.
        """
        with self.assertRaises((CandidatePromotionProvenanceBlocked, CandidatePromotionUpstreamStatusBlocked)) as caught:
            package_candidate(case_dir=self.case_dir, root=self.root)
        result = caught.exception.result
        self.assertFalse(result["candidate_package_created"])
        self.assertFalse(result["final_submit_created"])
        self.assertFalse((self.root / "candidate_submit").exists())
        self.assertFalse((self.root / "candidate_emergency").exists())
        self.assertFalse((self.root / "final_submit").exists())

    def _assert_upstream_untouched(self, upstream: dict[str, object]) -> None:
        for key in ("upstream_run_result", "upstream_risk_summary"):
            path = upstream[key]
            self.assertTrue(path.is_file())

    # 1. Full positive contract passes and writes manifest summary/hash.
    def test_full_contract_passes_and_writes_manifest_summary(self) -> None:
        upstream = self._write_upstream_pair()
        self._config(upstream)
        before_bytes = {
            key: upstream[key].read_bytes() for key in ("upstream_run_result", "upstream_risk_summary")
        }
        result = package_candidate(case_dir=self.case_dir, root=self.root)
        manifest = result["manifest"]
        verification = manifest["source_provenance_verification"]
        self.assertTrue(verification["source_provenance_finalization_passed"])
        self.assertTrue(verification["post_snapshot_verification_passed"])
        self.assertEqual(verification["run_id"], upstream["run_id"])
        self.assertEqual(verification["source_commit"], self._head())
        self.assertTrue(verification["packaging_head_matches_source_commit"])
        self.assertFalse(verification["packaging_git_operation_in_progress"])
        self.assertEqual(verification["packaging_unexpected_status_entries"], [])
        self.assertEqual(verification["blockers"], [])
        self.assertIsInstance(verification["source_provenance_sha256"], str)
        self.assertEqual(len(verification["source_provenance_sha256"]), 64)
        self.assertNotIn("status_entries", verification)
        self.assertNotIn("status_records", verification)
        for key, original in before_bytes.items():
            self.assertEqual(upstream[key].read_bytes(), original)
        self.assertFalse((self.root / "final_submit").exists())
        self.assertFalse(manifest["final_submit_created"])
        self.assertFalse(manifest["for_official_submission"])

    # 2. run_result/risk_summary provenance mismatch blocks.
    def test_provenance_mismatch_blocks(self) -> None:
        upstream = self._write_upstream_pair(risk_provenance_overrides={"source_branch": "other-branch"})
        self._config(upstream)
        result = self._blocked()
        self.assertIn("PROVENANCE_MISMATCH", result["source_provenance_verification"]["blockers"])
        self._assert_upstream_untouched(upstream)

    # 3. run_id mismatch blocks.
    def test_run_id_mismatch_blocks(self) -> None:
        upstream = self._write_upstream_pair()
        self._write_json(
            upstream["upstream_risk_summary"],
            {"run_id": "different_run_id", "risks": [], "source_provenance": upstream["provenance"]},
        )
        self._config(upstream)
        result = self._blocked()
        self.assertIn("PROVENANCE_RUN_ID_MISMATCH", result["source_provenance_verification"]["blockers"])

    # 4. Missing expected file blocks.
    def test_missing_expected_file_blocks(self) -> None:
        upstream = self._write_upstream_pair()
        run_dir = self.root / "work_tmp" / "run_pv2a_fixture"
        (run_dir / "risk_summary.md").unlink()
        self._config(upstream)
        result = self._blocked()
        self.assertIn("PROVENANCE_POST_SNAPSHOT_MISSING", result["source_provenance_verification"]["blockers"])

    # 5a. Expected path is a directory blocks.
    def test_expected_path_is_directory_blocks(self) -> None:
        upstream = self._write_upstream_pair()
        run_dir = self.root / "work_tmp" / "run_pv2a_fixture"
        md_path = run_dir / "risk_summary.md"
        md_path.unlink()
        md_path.mkdir()
        self._config(upstream)
        result = self._blocked()
        self.assertIn("PROVENANCE_POST_SNAPSHOT_MISSING", result["source_provenance_verification"]["blockers"])

    # 5b. Expected path is a symlink blocks.
    def test_expected_path_is_symlink_blocks(self) -> None:
        upstream = self._write_upstream_pair()
        run_dir = self.root / "work_tmp" / "run_pv2a_fixture"
        md_path = run_dir / "risk_summary.md"
        real_target = run_dir / "risk_summary_real.md"
        md_path.unlink()
        real_target.write_text("# risk summary\n", encoding="utf-8")
        try:
            md_path.symlink_to(real_target)
        except OSError:
            self.skipTest("symlink creation not permitted in this environment")
        self._config(upstream)
        result = self._blocked()
        verification = result["source_provenance_verification"]
        self.assertFalse(verification["post_snapshot_verification_passed"])
        self.assertTrue(verification["blockers"])

    # 5c. Expected path escapes repo root (unsafe) blocks.
    def test_expected_path_unsafe_blocks(self) -> None:
        upstream = self._write_upstream_pair(
            provenance_overrides={"source_expected_post_snapshot_paths": ["../../etc/passwd"]},
            risk_provenance_overrides={"source_expected_post_snapshot_paths": ["../../etc/passwd"]},
        )
        self._config(upstream)
        result = self._blocked()
        self.assertIn("PROVENANCE_PATH_UNSAFE", result["source_provenance_verification"]["blockers"])

    # 6. source_worktree_clean_before=false blocks.
    def test_worktree_not_clean_before_blocks(self) -> None:
        upstream = self._write_upstream_pair(
            provenance_overrides={"source_worktree_clean_before": False},
            risk_provenance_overrides={"source_worktree_clean_before": False},
        )
        self._config(upstream)
        result = self._blocked()
        self.assertIn("PROVENANCE_RUNTIME_INCOMPLETE", result["source_provenance_verification"]["blockers"])

    # 7. source_commit_stable=false blocks.
    def test_commit_not_stable_blocks(self) -> None:
        upstream = self._write_upstream_pair(
            provenance_overrides={"source_commit_stable": False},
            risk_provenance_overrides={"source_commit_stable": False},
        )
        self._config(upstream)
        result = self._blocked()
        self.assertIn("PROVENANCE_RUNTIME_INCOMPLETE", result["source_provenance_verification"]["blockers"])

    # 8. write_registry_complete=false blocks.
    def test_write_registry_incomplete_blocks(self) -> None:
        upstream = self._write_upstream_pair(
            provenance_overrides={"source_write_registry_complete": False},
            risk_provenance_overrides={"source_write_registry_complete": False},
        )
        self._config(upstream)
        result = self._blocked()
        self.assertIn("PROVENANCE_RUNTIME_INCOMPLETE", result["source_provenance_verification"]["blockers"])

    # 9. excluded_classes non-empty blocks.
    def test_excluded_classes_non_empty_blocks(self) -> None:
        upstream = self._write_upstream_pair(
            provenance_overrides={"source_write_registry_excluded_classes": ["ASSET_MANIFEST_WRITES"]},
            risk_provenance_overrides={"source_write_registry_excluded_classes": ["ASSET_MANIFEST_WRITES"]},
        )
        self._config(upstream)
        result = self._blocked()
        self.assertIn("PROVENANCE_RUNTIME_INCOMPLETE", result["source_provenance_verification"]["blockers"])

    # 10. current HEAD differs from source_commit_after blocks.
    def test_head_drift_blocks(self) -> None:
        upstream = self._write_upstream_pair(commit=self.commit_a)
        self._config(upstream)
        result = self._blocked()
        verification = result["source_provenance_verification"]
        self.assertIn("PROVENANCE_HEAD_DRIFT", verification["blockers"])
        self.assertFalse(verification["packaging_head_matches_source_commit"])

    # 11. source commit object does not exist blocks.
    def test_source_commit_missing_blocks(self) -> None:
        fake_commit = "0" * 40
        upstream = self._write_upstream_pair(commit=fake_commit)
        self._config(upstream)
        result = self._blocked()
        self.assertIn("PROVENANCE_COMMIT_MISSING", result["source_provenance_verification"]["blockers"])

    # 12. Unrelated dirty/untracked file blocks.
    def test_unrelated_dirty_file_blocks(self) -> None:
        upstream = self._write_upstream_pair()
        self._config(upstream)
        (self.root / "unrelated_untracked.txt").write_text("surprise\n", encoding="utf-8")
        result = self._blocked()
        verification = result["source_provenance_verification"]
        self.assertIn("PROVENANCE_UNEXPECTED_WORKTREE_CHANGE", verification["blockers"])
        self.assertTrue(
            any(entry.get("path") == "unrelated_untracked.txt" for entry in verification["packaging_unexpected_status_entries"])
        )

    # 13. Only declared generated/expected paths change: not misflagged.
    def test_declared_written_and_expected_paths_not_misflagged(self) -> None:
        run_dir_rel = "work_tmp/run_pv2a_fixture"
        written_repo_paths = [
            f"{run_dir_rel}/risk_summary.json",
            f"{run_dir_rel}/run_result.json",
        ]
        upstream = self._write_upstream_pair(extra_written_repo_paths=written_repo_paths)
        self._config(upstream)
        # After _config()'s auto-commit, the worktree is clean. Simulate a
        # run_case run that touched exactly its own declared
        # source_written_repo_paths (an untracked rewrite of an already
        # fixture-committed file) — this must not be misflagged as an
        # unrelated/unexpected change precisely because it's declared.
        run_dir = self.root / "work_tmp" / "run_pv2a_fixture"
        (run_dir / "run_result.json").write_text(
            (run_dir / "run_result.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
        result = package_candidate(case_dir=self.case_dir, root=self.root)
        verification = result["manifest"]["source_provenance_verification"]
        self.assertTrue(verification["source_provenance_finalization_passed"])
        self.assertEqual(verification["packaging_unexpected_status_entries"], [])

    # 14. Rename/copy with either end undeclared blocks.
    def test_rename_with_undeclared_end_blocks(self) -> None:
        upstream = self._write_upstream_pair()
        self._config(upstream)
        tracked = self.root / "README.md"
        renamed = self.root / "README_renamed.md"
        tracked.rename(renamed)
        self._git("add", "-A")
        result = self._blocked()
        verification = result["source_provenance_verification"]
        self.assertIn("PROVENANCE_UNEXPECTED_WORKTREE_CHANGE", verification["blockers"])
        self.assertTrue(verification["packaging_unexpected_status_entries"])

    # 15. merge/rebase/cherry-pick in-progress markers block.
    def test_git_operation_in_progress_blocks(self) -> None:
        upstream = self._write_upstream_pair()
        self._config(upstream)
        (self.root / ".git" / "MERGE_HEAD").write_text(self.commit_a + "\n", encoding="utf-8")
        try:
            result = self._blocked()
            verification = result["source_provenance_verification"]
            self.assertIn("PROVENANCE_GIT_OPERATION_IN_PROGRESS", verification["blockers"])
            self.assertTrue(verification["packaging_git_operation_in_progress"])
        finally:
            marker = self.root / ".git" / "MERGE_HEAD"
            if marker.exists():
                marker.unlink()

    def test_rebase_merge_directory_blocks(self) -> None:
        upstream = self._write_upstream_pair()
        self._config(upstream)
        rebase_dir = self.root / ".git" / "rebase-merge"
        rebase_dir.mkdir()
        try:
            result = self._blocked()
            self.assertIn(
                "PROVENANCE_GIT_OPERATION_IN_PROGRESS", result["source_provenance_verification"]["blockers"]
            )
        finally:
            rebase_dir.rmdir()

    # 16. Malformed porcelain output fails closed.
    def test_malformed_porcelain_fails_closed(self) -> None:
        import unittest.mock as mock

        upstream = self._write_upstream_pair()
        self._config(upstream)

        real_run = subprocess.run

        def _fake_run(args, **kwargs):
            if isinstance(args, list) and "status" in args:
                completed = real_run(["git", "rev-parse", "--verify", "HEAD"], **{**kwargs, "text": True})
                completed.stdout = b"not a nul terminated porcelain stream"
                completed.returncode = 0
                return completed
            return real_run(args, **kwargs)

        with mock.patch("package_candidate.subprocess.run", side_effect=_fake_run):
            result = self._blocked()
        self.assertIn(
            "PROVENANCE_UNEXPECTED_WORKTREE_CHANGE", result["source_provenance_verification"]["blockers"]
        )

    # 17. Source run_result/risk_summary bytes not modified across a passing run.
    def test_source_files_not_modified_on_pass(self) -> None:
        upstream = self._write_upstream_pair()
        self._config(upstream)
        before = {key: upstream[key].read_bytes() for key in ("upstream_run_result", "upstream_risk_summary")}
        package_candidate(case_dir=self.case_dir, root=self.root)
        for key, original in before.items():
            self.assertEqual(upstream[key].read_bytes(), original)

    # 18. Failure scenarios block before candidate mkdir.
    def test_failure_blocks_before_candidate_mkdir(self) -> None:
        import unittest.mock as mock

        upstream = self._write_upstream_pair(commit=self.commit_a)
        self._config(upstream)
        with mock.patch("package_candidate.Path.mkdir") as mkdir:
            self._blocked()
        mkdir.assert_not_called()

    # 19. No repo-root candidate/final directories are ever created on failure.
    def test_no_repo_root_pollution_on_failure(self) -> None:
        upstream = self._write_upstream_pair(commit=self.commit_a)
        self._config(upstream)
        self._blocked()
        self.assertFalse((self.root / "candidate_submit").exists())
        self.assertFalse((self.root / "candidate_emergency").exists())
        self.assertFalse((self.root / "final_submit").exists())

    # 20. freeze and existing package_candidate tests do not regress.
    def test_freeze_tripwire_and_ids_remain_unchanged(self) -> None:
        report = evaluate(Path(__file__).resolve().parents[2])
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["registered_ids_current"], report["registered_ids_baseline"])

    def test_authoritative_field_never_forced_true(self) -> None:
        upstream = self._write_upstream_pair()
        self._config(upstream)
        result = package_candidate(case_dir=self.case_dir, root=self.root)
        verification = result["manifest"]["source_provenance_verification"]
        self.assertTrue(verification["source_provenance_finalization_passed"])
        stored_provenance = json.loads(upstream["upstream_run_result"].read_text(encoding="utf-8"))
        self.assertFalse(stored_provenance["source_provenance"]["source_write_registry_authoritative"])

    def test_not_applicable_when_no_upstream_paths_configured(self) -> None:
        self._write_json(self.case_dir / "case_config.json", {
            "submission_tier": "training",
            "claim_check_required": False,
            "core_claims": [],
            "human_revision_required": False,
            "human_revision_mode": "off",
            "r3_required": False,
        })
        result = package_candidate(case_dir=self.case_dir, root=self.root)
        verification = result["manifest"]["source_provenance_verification"]
        self.assertFalse(verification["source_provenance_required"])
        self.assertFalse(verification["source_provenance_applicable"])
        self.assertTrue(verification["source_provenance_legacy_compatibility"])
        # Legacy compatibility must never claim a finalization it never ran.
        self.assertFalse(verification["source_provenance_finalization_passed"])
        self.assertFalse(verification["post_snapshot_verification_passed"])
        self.assertEqual(verification["blockers"], [])
        self.assertTrue((self.root / result["package_dir"]).is_dir())

    # ---- P6-PV2A-B: applicability policy hardening ----

    # 1. selection/formal with no upstream paths configured at all: blocks
    # (via whichever gate fires — the pre-existing upstream-status guard
    # also requires configured paths for these tiers and fires first; the
    # important invariant is that no candidate is ever created).
    def test_selection_tier_no_upstream_paths_blocks(self) -> None:
        self._write_json(self.case_dir / "case_config.json", {
            "submission_tier": "selection",
            "claim_check_required": False,
            "core_claims": [],
            "human_revision_required": False,
            "human_revision_mode": "off",
            "r3_required": False,
        })
        self._blocked_by_any_candidate_gate()

    def test_formal_tier_no_upstream_paths_blocks(self) -> None:
        self._write_json(self.case_dir / "case_config.json", {
            "submission_tier": "formal",
            "claim_check_required": False,
            "core_claims": [],
            "human_revision_required": False,
            "human_revision_mode": "off",
            "r3_required": False,
        })
        self._blocked_by_any_candidate_gate()

    # 1b. selection/formal where run_result path is configured (so the
    # pre-existing upstream-status guard's own requirement is satisfied and
    # it PASSes) but risk_summary path is not — reaches the new provenance
    # check specifically, proving it independently requires both.
    def test_selection_tier_partial_upstream_paths_blocks_on_provenance_gate(self) -> None:
        upstream = self._write_upstream_pair(upstream_guard_pass=True)
        self._config(
            upstream,
            submission_tier="selection",
            candidate_upstream_risk_summary_path="",
        )
        result = self._blocked()
        verification = result["source_provenance_verification"]
        self.assertTrue(verification["source_provenance_required"])
        # run_result carries source_provenance, so this is applicable even
        # though the risk_summary side is unreachable — applicability is
        # never softened by a missing counterpart, it just fails to verify.
        self.assertTrue(verification["source_provenance_applicable"])
        self.assertIn("PROVENANCE_INPUT_MISSING", verification["blockers"])

    # 2. selection/formal with both upstream artifacts present but neither
    # carrying source_provenance (pre-feature legacy fixtures): blocks.
    def test_selection_tier_legacy_fixtures_without_provenance_blocks(self) -> None:
        upstream = self._write_upstream_pair(with_provenance=False, upstream_guard_pass=True)
        self._config(upstream, submission_tier="selection")
        result = self._blocked()
        verification = result["source_provenance_verification"]
        self.assertTrue(verification["source_provenance_required"])
        self.assertFalse(verification["source_provenance_applicable"])
        self.assertFalse(verification["source_provenance_legacy_compatibility"])
        self.assertIn("PROVENANCE_INPUT_MISSING", verification["blockers"])

    # 3. effective_upstream_status_required=true (selection/formal implies
    # this via _effective_candidate_settings) with missing provenance: blocks.
    def test_effective_upstream_status_required_with_missing_provenance_blocks(self) -> None:
        upstream = self._write_upstream_pair(with_provenance=False, upstream_guard_pass=True)
        self._config(upstream, submission_tier="formal")
        result = self._blocked()
        self.assertTrue(result["effective_upstream_status_required"])
        self.assertTrue(result["source_provenance_verification"]["source_provenance_required"])

    # 4. training tier, neither path configured: legacy non-applicable,
    # manifest reports false/false/true honestly, candidate still created.
    def test_training_tier_no_paths_configured_allows_legacy(self) -> None:
        self._write_json(self.case_dir / "case_config.json", {
            "submission_tier": "training",
            "claim_check_required": False,
            "core_claims": [],
            "human_revision_required": False,
            "human_revision_mode": "off",
            "r3_required": False,
        })
        result = package_candidate(case_dir=self.case_dir, root=self.root)
        verification = result["manifest"]["source_provenance_verification"]
        self.assertFalse(verification["source_provenance_required"])
        self.assertFalse(verification["source_provenance_applicable"])
        self.assertTrue(verification["source_provenance_legacy_compatibility"])
        self.assertFalse(verification["source_provenance_finalization_passed"])
        self.assertFalse(verification["post_snapshot_verification_passed"])
        self.assertTrue((self.root / result["package_dir"]).is_dir())

    # 5. training tier, legacy fixtures configured with neither side
    # carrying provenance: allowed, but legacy_compatibility must be true.
    def test_training_tier_legacy_fixtures_without_provenance_allowed(self) -> None:
        upstream = self._write_upstream_pair(with_provenance=False)
        self._config(upstream, submission_tier="training")
        result = package_candidate(case_dir=self.case_dir, root=self.root)
        verification = result["manifest"]["source_provenance_verification"]
        self.assertFalse(verification["source_provenance_required"])
        self.assertFalse(verification["source_provenance_applicable"])
        self.assertTrue(verification["source_provenance_legacy_compatibility"])
        self.assertFalse(verification["source_provenance_finalization_passed"])
        self.assertTrue((self.root / result["package_dir"]).is_dir())

    # 6. training tier, upstream artifacts DO carry provenance: applicability
    # is forced true and full strict validation runs (passing case).
    def test_training_tier_with_provenance_forces_strict_validation(self) -> None:
        upstream = self._write_upstream_pair()
        self._config(upstream, submission_tier="training")
        result = package_candidate(case_dir=self.case_dir, root=self.root)
        verification = result["manifest"]["source_provenance_verification"]
        self.assertFalse(verification["source_provenance_required"])
        self.assertTrue(verification["source_provenance_applicable"])
        self.assertFalse(verification["source_provenance_legacy_compatibility"])
        self.assertTrue(verification["source_provenance_finalization_passed"])
        self.assertTrue(verification["post_snapshot_verification_passed"])

    # training tier, provenance present but invalid (runtime predicate
    # fails): must still block, tier does not soften this.
    def test_training_tier_with_invalid_provenance_still_blocks(self) -> None:
        upstream = self._write_upstream_pair(
            provenance_overrides={"source_worktree_clean_before": False},
            risk_provenance_overrides={"source_worktree_clean_before": False},
        )
        self._config(upstream, submission_tier="training")
        result = self._blocked()
        verification = result["source_provenance_verification"]
        self.assertTrue(verification["source_provenance_applicable"])
        self.assertFalse(verification["source_provenance_required"])
        self.assertIn("PROVENANCE_RUNTIME_INCOMPLETE", verification["blockers"])

    # 7. One side has provenance, the other does not: blocks regardless of
    # tier (tested here at training tier, the most permissive one).
    def test_one_sided_provenance_blocks_even_at_training_tier(self) -> None:
        upstream = self._write_upstream_pair()
        payload = json.loads(upstream["upstream_risk_summary"].read_text(encoding="utf-8"))
        del payload["source_provenance"]
        self._write_json(upstream["upstream_risk_summary"], payload)
        self._config(upstream, submission_tier="training")
        result = self._blocked()
        verification = result["source_provenance_verification"]
        self.assertIn("PROVENANCE_MISMATCH", verification["blockers"])
        # One side carrying source_provenance makes this applicable — it
        # just fails, it does not silently fall back to legacy mode.
        self.assertTrue(verification["source_provenance_applicable"])
        self.assertFalse(verification["source_provenance_legacy_compatibility"])

    # 8. Provenance present but invalid at selection tier too: blocks.
    def test_provenance_present_but_invalid_blocks_at_selection_tier(self) -> None:
        upstream = self._write_upstream_pair(
            upstream_guard_pass=True,
            provenance_overrides={"source_commit_stable": False},
            risk_provenance_overrides={"source_commit_stable": False},
        )
        self._config(upstream, submission_tier="selection")
        result = self._blocked()
        verification = result["source_provenance_verification"]
        self.assertTrue(verification["source_provenance_applicable"])
        self.assertIn("PROVENANCE_RUNTIME_INCOMPLETE", verification["blockers"])

    # 9. required/applicable failures happen before candidate mkdir (both
    # a required-tier failure and an applicable-but-invalid failure).
    def test_required_failure_blocks_before_mkdir(self) -> None:
        import unittest.mock as mock

        upstream = self._write_upstream_pair(upstream_guard_pass=True)
        self._config(
            upstream,
            submission_tier="selection",
            candidate_upstream_risk_summary_path="",
        )
        with mock.patch("package_candidate.Path.mkdir") as mkdir:
            self._blocked()
        mkdir.assert_not_called()

    def test_applicable_failure_blocks_before_mkdir(self) -> None:
        import unittest.mock as mock

        upstream = self._write_upstream_pair(
            provenance_overrides={"source_write_registry_complete": False},
            risk_provenance_overrides={"source_write_registry_complete": False},
        )
        self._config(upstream, submission_tier="training")
        with mock.patch("package_candidate.Path.mkdir") as mkdir:
            self._blocked()
        mkdir.assert_not_called()

    # 11. Source files not modified across the hardened-policy scenarios.
    def test_source_files_unchanged_across_legacy_and_strict_paths(self) -> None:
        upstream = self._write_upstream_pair()
        self._config(upstream, submission_tier="training")
        before = {key: upstream[key].read_bytes() for key in ("upstream_run_result", "upstream_risk_summary")}
        package_candidate(case_dir=self.case_dir, root=self.root)
        for key, original in before.items():
            self.assertEqual(upstream[key].read_bytes(), original)

    # 12. No repo-root candidate/final pollution across a blocked
    # required-tier run.
    def test_no_repo_root_pollution_on_required_tier_block(self) -> None:
        upstream = self._write_upstream_pair(upstream_guard_pass=True)
        self._config(
            upstream,
            submission_tier="formal",
            candidate_upstream_risk_summary_path="",
        )
        self._blocked()
        self.assertFalse((self.root / "candidate_submit").exists())
        self.assertFalse((self.root / "candidate_emergency").exists())
        self.assertFalse((self.root / "final_submit").exists())


if __name__ == "__main__":
    unittest.main()
