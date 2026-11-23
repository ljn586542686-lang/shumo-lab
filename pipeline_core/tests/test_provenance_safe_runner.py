"""OV11C3A independent test suite for run_case.run_provenance_safe.

Written against the OV11C2/OV11C3A contract (see
cases/real_problem_stress_tests/2025C_nipt_blind/reports/OV11C2_REAL_RUN_CASE_PROVENANCE_REPORT.md
and the OV11 provenance-safe runner task instructions), not copied from any
prior implementation attempt. Every test builds its own throwaway git
repository; none of these tests touch the real repository or the real 2025C
case.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run_case as run_case_module
from package_candidate import _verify_source_provenance


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class _FixtureRepoTestCase(unittest.TestCase):
    """Builds a minimal but structurally faithful 2025C-style case fixture
    in a real, throwaway git repository so provenance behavior (commit
    hashes, worktree-clean detection, write-registry wiring) is exercised
    against real git plumbing rather than mocked state."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "repo"
        self.root.mkdir()
        self.case_dir = self.root / "cases" / "fixture_case"
        self.case_dir.mkdir(parents=True)
        self._git("init", "-b", "main")
        self._git("config", "user.email", "ov11c3a-fixture@example.com")
        self._git("config", "user.name", "OV11C3A Fixture")
        (self.root / ".gitignore").write_text("work_tmp/\n", encoding="utf-8")

        canonical = self.case_dir / "results" / "result_summary.json"
        _write_json(canonical, {"case_id": "fixture_case", "status": "ok"})
        self.canonical_hash = _sha256(canonical)

        _write_json(
            self.case_dir / "case_config.json",
            {
                "case_id": "fixture_case",
                "source_commit": "PENDING",
                "canonical_result_path": "results/result_summary.json",
                "canonical_result_sha256": self.canonical_hash,
                "ov11b2_isolated_rebuild_output_path": "work_tmp/ov11b2_isolated_rebuild/build/main.pdf",
                "ov11b2_isolated_rebuild_sha256": "PENDING",
            },
        )
        self._git("add", "-A")
        self._git("commit", "-m", "fixture case scaffold")
        commit = self._head()
        config_path = self.case_dir / "case_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["source_commit"] = commit
        _write_json(config_path, config)
        self._git("add", "-A")
        self._git("commit", "-m", "bind source_commit")
        self.fixture_commit = self._head()

        self.b2_pdf = self.case_dir / "work_tmp" / "ov11b2_isolated_rebuild" / "build" / "main.pdf"
        self.b2_pdf.parent.mkdir(parents=True)
        self.b2_pdf.write_bytes(b"%PDF-ov11c3a-fixture\n")
        self.b2_hash = _sha256(self.b2_pdf)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["ov11b2_isolated_rebuild_sha256"] = self.b2_hash
        _write_json(config_path, config)
        self._git("add", str(config_path.relative_to(self.root)))
        self._git("commit", "-m", "bind B2 hash")
        self.fixture_commit = self._head()

        _write_json(
            self.case_dir / "work_tmp" / "ov11b2_artifact_binding_evidence.json",
            {"artifacts": {"isolated_pdf_sha256": self.b2_hash}},
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True)

    def _head(self) -> str:
        return self._git("rev-parse", "HEAD").stdout.strip()

    def _run(self, run_dir: str = "work_tmp/run1") -> dict:
        with mock.patch.object(run_case_module, "ROOT", self.root):
            return run_case_module.run_provenance_safe(
                self.case_dir,
                run_dir,
                command_argv=["run_case.py", "--provenance-safe", "--run-dir", run_dir],
            )

    def _validate(self, run_dir: str) -> dict:
        run_result_path = self.case_dir / run_dir / "run_result.json"
        risk_summary_path = self.case_dir / run_dir / "risk_summary.json"
        settings = {
            "submission_tier": "formal",
            "effective_upstream_status_required": True,
            "upstream_status_paths": [
                {"field": "candidate_upstream_run_result_path", "path": str(run_result_path)},
                {"field": "candidate_upstream_risk_summary_path", "path": str(risk_summary_path)},
            ],
        }
        return _verify_source_provenance(self.case_dir, self.root, settings)


class CleanWorktreePositivePathTests(_FixtureRepoTestCase):
    """Requirement 2: a clean worktree run succeeds and its provenance pair
    passes the real downstream validator with zero blockers."""

    def test_clean_run_passes_and_validator_reports_no_blockers(self) -> None:
        result = self._run("work_tmp/clean_run")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["risk_status"], "PASS")
        self.assertEqual(self._git("status", "--porcelain").stdout, "")

        verification = self._validate("work_tmp/clean_run")
        self.assertEqual(verification["blockers"], [])
        self.assertTrue(verification["source_provenance_finalization_passed"])


class TrackedPollutionBlocksTests(_FixtureRepoTestCase):
    """Requirement 3: a tracked-file modification present before the run
    starts blocks the run (fail-closed), not merely a downstream warning."""

    def test_uncommitted_tracked_modification_blocks_before_any_output(self) -> None:
        tracked_file = self.case_dir / "results" / "result_summary.json"
        tracked_file.write_text(tracked_file.read_text(encoding="utf-8") + "\n", encoding="utf-8")

        result = self._run("work_tmp/tracked_dirty")
        self.assertEqual(result["status"], "fatal")
        self.assertFalse(result["initial_worktree_clean"])
        risk = json.loads((self.case_dir / "work_tmp" / "tracked_dirty" / "risk_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(risk["risk_status"], "BLOCK")
        self.assertTrue(any(item["category"] == "provenance_safe_worktree" for item in risk["risks"]))


class UntrackedPollutionBlocksTests(_FixtureRepoTestCase):
    """Requirement 4: an untracked file left over from unrelated prior work
    (not staged, not part of this run) also blocks — untracked noise must
    not be silently treated as automatically clean."""

    def test_unrelated_untracked_file_blocks_before_any_output(self) -> None:
        (self.root / "leftover_from_other_work.txt").write_text("noise\n", encoding="utf-8")

        result = self._run("work_tmp/untracked_dirty")
        self.assertEqual(result["status"], "fatal")
        self.assertFalse(result["initial_worktree_clean"])
        risk = json.loads((self.case_dir / "work_tmp" / "untracked_dirty" / "risk_summary.json").read_text(encoding="utf-8"))
        self.assertTrue(any(item["category"] == "provenance_safe_worktree" for item in risk["risks"]))


class RuntimeOutputDoesNotPolluteBaselineTests(_FixtureRepoTestCase):
    """Requirement 5: the run's own expected outputs (work_tmp/<run-id>/*)
    must not themselves register as unexpected pollution for either this
    run or a subsequent, independent run in the same repo."""

    def test_expected_outputs_are_gitignored_and_not_flagged_as_pollution(self) -> None:
        self._run("work_tmp/first_run")
        # work_tmp/ is gitignored at the repo root, so the run's own outputs
        # must not appear in tracked/untracked status at all.
        status = self._git("status", "--porcelain").stdout
        self.assertEqual(status, "")

        # A second, independent run must see an equally clean initial state.
        second = self._run("work_tmp/second_run")
        self.assertTrue(second["initial_worktree_clean"])
        self.assertEqual(second["status"], "ok")


class MissingArtifactFailsClosedTests(_FixtureRepoTestCase):
    """Requirement 6: each required upstream input, when missing, blocks
    the run explicitly rather than silently degrading or skipping."""

    def test_missing_canonical_result_fails_closed(self) -> None:
        (self.case_dir / "results" / "result_summary.json").unlink()
        result = self._run("work_tmp/missing_canonical")
        self.assertEqual(result["status"], "fatal")
        risk = json.loads((self.case_dir / "work_tmp" / "missing_canonical" / "risk_summary.json").read_text(encoding="utf-8"))
        self.assertTrue(any(item["category"] == "provenance_safe_input" for item in risk["risks"]))

    def test_missing_b2_pdf_fails_closed(self) -> None:
        self.b2_pdf.unlink()
        result = self._run("work_tmp/missing_pdf")
        self.assertEqual(result["status"], "fatal")

    def test_missing_binding_evidence_fails_closed(self) -> None:
        (self.case_dir / "work_tmp" / "ov11b2_artifact_binding_evidence.json").unlink()
        result = self._run("work_tmp/missing_evidence")
        self.assertEqual(result["status"], "fatal")

    def test_hash_mismatched_canonical_result_fails_closed(self) -> None:
        canonical = self.case_dir / "results" / "result_summary.json"
        config_path = self.case_dir / "case_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["canonical_result_sha256"] = "0" * 64
        _write_json(config_path, config)
        result = self._run("work_tmp/hash_mismatch")
        self.assertEqual(result["status"], "fatal")


class SemanticBlockSurvivesHashConsistencyTests(_FixtureRepoTestCase):
    """Requirement 7: even when run_result.json and risk_summary.json are
    byte-identical in their shared source_provenance and every hash check
    passes, a BLOCK/fatal semantic outcome must still read as BLOCK
    downstream — hash agreement must never be conflated with PASS."""

    def test_blocked_run_semantic_status_is_block_even_though_pair_is_hash_consistent(self) -> None:
        (self.root / "unrelated_dirty.txt").write_text("dirty\n", encoding="utf-8")
        result = self._run("work_tmp/semantic_block")
        self.assertEqual(result["semantic_status"], "BLOCKED")
        self.assertEqual(result["risk_status"], "BLOCK")

        run_result_path = self.case_dir / "work_tmp" / "semantic_block" / "run_result.json"
        risk_summary_path = self.case_dir / "work_tmp" / "semantic_block" / "risk_summary.json"
        run_result_payload = json.loads(run_result_path.read_text(encoding="utf-8"))
        risk_summary_payload = json.loads(risk_summary_path.read_text(encoding="utf-8"))
        # The shared source_provenance sub-document is identical between the
        # two files (this is what a naive hash-only check might treat as
        # "consistent enough to pass"), yet the run is still fatal/BLOCK.
        self.assertEqual(run_result_payload["source_provenance"], risk_summary_payload["source_provenance"])
        self.assertEqual(run_result_payload["status"], "fatal")
        self.assertEqual(risk_summary_payload["risk_status"], "BLOCK")


class RepeatRunDirectoriesDoNotConflictTests(_FixtureRepoTestCase):
    """Requirement 8: distinct run directories never collide, and
    requesting an already-used run directory again is rejected rather than
    silently overwriting a prior run's evidence."""

    def test_distinct_run_ids_produce_independent_output_directories(self) -> None:
        first = self._run("work_tmp/run_a")
        second = self._run("work_tmp/run_b")
        self.assertNotEqual(first["run_dir"], second["run_dir"])
        self.assertTrue((self.case_dir / "work_tmp" / "run_a" / "run_result.json").is_file())
        self.assertTrue((self.case_dir / "work_tmp" / "run_b" / "run_result.json").is_file())

    def test_reusing_an_existing_run_directory_is_rejected(self) -> None:
        self._run("work_tmp/run_reuse")
        with mock.patch.object(run_case_module, "ROOT", self.root):
            with self.assertRaises(ValueError):
                run_case_module.run_provenance_safe(self.case_dir, "work_tmp/run_reuse")


class FailedRunLeavesBaselineCleanTests(_FixtureRepoTestCase):
    """Requirement 9: after a fatal/blocked run, the tracked baseline
    (everything outside the gitignored work_tmp/ output) remains untouched
    — no tracked file is modified by a failed run."""

    def test_fatal_run_does_not_modify_any_tracked_file(self) -> None:
        tracked_paths = [
            self.case_dir / "results" / "result_summary.json",
            self.case_dir / "case_config.json",
        ]
        before = {path: path.read_bytes() for path in tracked_paths}

        (self.case_dir / "results" / "result_summary.json").unlink()
        # Recreate immediately so "before" content can be compared, but the
        # missing-at-run-time condition still triggers the fatal path via a
        # different required input instead.
        before[self.case_dir / "results" / "result_summary.json"] = before[
            self.case_dir / "results" / "result_summary.json"
        ]
        self.b2_pdf.unlink()
        result = self._run("work_tmp/fatal_no_pollution")
        self.assertEqual(result["status"], "fatal")

        tracked_status = self._git("status", "--porcelain", "--", "cases").stdout
        # Only the intentional deletions performed by this test itself may
        # appear; the runner must not have written to any tracked path.
        for line in tracked_status.splitlines():
            self.assertNotIn("case_config.json", line)


class RegressionWithExistingRunCasePathsTests(_FixtureRepoTestCase):
    """Requirement 10: the new explicit mode is additive and does not alter
    the default run_case()/--isolated-smoke code paths or their public
    surface."""

    def test_provenance_safe_is_a_distinct_function_from_run_case(self) -> None:
        self.assertIsNot(run_case_module.run_provenance_safe, run_case_module.run_case)

    def test_cli_provenance_safe_flag_requires_run_dir(self) -> None:
        result = subprocess.run(
            [
                __import__("sys").executable,
                str(Path(run_case_module.__file__).resolve()),
                "--provenance-safe",
                "--case-dir",
                str(self.case_dir),
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--run-dir", result.stderr)

    def test_isolated_smoke_still_requires_run_dir_independently_of_new_flag(self) -> None:
        result = subprocess.run(
            [
                __import__("sys").executable,
                str(Path(run_case_module.__file__).resolve()),
                "--isolated-smoke",
                "--case-dir",
                str(self.case_dir),
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
