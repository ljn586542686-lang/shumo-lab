"""OV11C3A tests for the isolated-worktree orchestration layer.

Covers the piece run_case.run_provenance_safe deliberately does not own:
creating a genuinely clean git worktree for the real run to execute in, and
re-verifying its result from inside that same isolated worktree. Every test
builds its own throwaway git repository with real `git worktree` commands;
none of these tests touch the real repository or the real 2025C case.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from pipeline_core.provenance_safe_run import (
    ProvenanceSafeOrchestrationError,
    create_isolated_worktree,
    run_isolated_provenance_safe,
)
from x5_surgical_utils import ROOT


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RealRepositoryGitignoreCoversByteCompilationTests(unittest.TestCase):
    """A missing __pycache__/ ignore rule would make importing run_case.py
    (or any dependency it pulls in) register as worktree pollution the
    moment it is byte-compiled inside a fresh isolated worktree -- this was
    reproduced directly while building this test suite's own fixture.
    Assert the real repository's .gitignore actually covers this so the
    isolated orchestration in production does not silently regress."""

    def test_real_gitignore_excludes_pycache(self) -> None:
        gitignore_path = ROOT / ".gitignore"
        self.assertTrue(gitignore_path.is_file())
        patterns = gitignore_path.read_text(encoding="utf-8").splitlines()
        self.assertIn("**/__pycache__/", patterns)


def _write_json(path: Path, payload: object) -> None:
    # newline="\n" pins the on-disk bytes to LF regardless of platform, so
    # this fixture's own pre-commit hash matches what a Windows core.
    # autocrlf=false worktree checkout of the same commit will contain --
    # otherwise Python's default text-mode newline translation on Windows
    # writes CRLF, git's clean filter normalizes it back to LF at commit
    # time, and the fixture's "canonical hash" (taken before that commit)
    # would silently disagree with every later checkout of the same blob.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


class _IsolatedOrchestrationTestCase(unittest.TestCase):
    """Builds a throwaway repository that mirrors the parts of the real
    repository the orchestrator depends on: a case fixture, run_case.py and
    package_candidate.py importable from the worktree root, and the shared
    x5_surgical_utils module."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "repo"
        self.root.mkdir()
        self._git_root("init", "-b", "main")
        self._git_root("config", "user.email", "ov11c3a-orchestration@example.com")
        self._git_root("config", "user.name", "OV11C3A Orchestration Fixture")
        # __pycache__/ must be ignored, matching the real repository's
        # .gitignore: importing any copied module writes a .pyc file, and an
        # incomplete ignore list would make that byte-compilation itself
        # register as worktree pollution before the run even starts.
        (self.root / ".gitignore").write_text("work_tmp/\n__pycache__/\n", encoding="utf-8")

        # Copy the real modules the isolated subprocess must import so
        # `python run_case.py --provenance-safe` behaves identically inside
        # the fresh worktree. This keeps the test hermetic (no dependency on
        # cwd being the real repository) while still exercising real code.
        # package_candidate.py transitively imports two pipeline_core
        # submodules (and pipeline_core has no __init__.py -- it is an
        # implicit namespace package, so no package marker file is needed).
        import package_candidate as package_candidate_module
        import run_case as run_case_module
        import x5_surgical_utils as x5_surgical_utils_module
        import canonical_result_index as canonical_result_index_module
        from pipeline_core.claim_check import enforce_claim_check as enforce_claim_check_module
        from pipeline_core.quality_gates import result_acceptance_contract as result_acceptance_contract_module
        from pipeline_core.python_tools import claim_check_preflight as claim_check_preflight_module
        from pipeline_core.python_tools import competition_model_governance as competition_model_governance_module
        from pipeline_core.python_tools import gate_authority as gate_authority_module
        from pipeline_core.python_tools import human_gate as human_gate_module
        from pipeline_core.python_tools import human_revision_check as human_revision_check_module
        from pipeline_core.python_tools import model_authority as model_authority_module
        from pipeline_core.python_tools import r3_approval_interface as r3_approval_interface_module
        from pipeline_core.python_tools import r3_validator as r3_validator_module
        from pipeline_core.python_tools import structured_claim_handoff as structured_claim_handoff_module
        from pipeline_core.python_tools import transition_case as transition_case_module
        from pipeline_core.python_tools import workflow_state as workflow_state_module

        for module in (
            run_case_module,
            package_candidate_module,
            x5_surgical_utils_module,
            canonical_result_index_module,
        ):
            source_path = Path(module.__file__).resolve()
            (self.root / source_path.name).write_bytes(source_path.read_bytes())

        repo_root = Path(run_case_module.__file__).resolve().parent
        for module in (
            claim_check_preflight_module,
            competition_model_governance_module,
            gate_authority_module,
            human_gate_module,
            human_revision_check_module,
            model_authority_module,
            r3_approval_interface_module,
            r3_validator_module,
            structured_claim_handoff_module,
            transition_case_module,
            workflow_state_module,
            enforce_claim_check_module,
            result_acceptance_contract_module,
        ):
            source_path = Path(module.__file__).resolve()
            relative = source_path.relative_to(repo_root)
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source_path.read_bytes())

        self.case_dir = self.root / "cases" / "fixture_case"
        self.case_dir.mkdir(parents=True)
        canonical = self.case_dir / "results" / "result_summary.json"
        _write_json(canonical, {"case_id": "fixture_case", "status": "ok"})
        canonical_hash = _sha256(canonical)
        _write_json(
            self.case_dir / "case_config.json",
            {
                "case_id": "fixture_case",
                "source_commit": "PENDING",
                "canonical_result_path": "results/result_summary.json",
                "canonical_result_sha256": canonical_hash,
                "ov11b2_isolated_rebuild_output_path": "work_tmp/ov11b2_isolated_rebuild/build/main.pdf",
                "ov11b2_isolated_rebuild_sha256": "PENDING",
            },
        )
        self._git_root("add", "-A")
        self._git_root("commit", "-m", "orchestration fixture scaffold")
        commit = self._head_root()
        config_path = self.case_dir / "case_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["source_commit"] = commit
        _write_json(config_path, config)
        self._git_root("add", "-A")
        self._git_root("commit", "-m", "bind source_commit")

        b2_pdf = self.case_dir / "work_tmp" / "ov11b2_isolated_rebuild" / "build" / "main.pdf"
        b2_pdf.parent.mkdir(parents=True)
        b2_pdf.write_bytes(b"%PDF-ov11c3a-orchestration-fixture\n")
        b2_hash = _sha256(b2_pdf)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["ov11b2_isolated_rebuild_sha256"] = b2_hash
        _write_json(config_path, config)
        self._git_root("add", str(config_path.relative_to(self.root)))
        self._git_root("commit", "-m", "bind B2 hash")
        self.baseline_commit = self._head_root()

        _write_json(
            self.case_dir / "work_tmp" / "ov11b2_artifact_binding_evidence.json",
            {"artifacts": {"isolated_pdf_sha256": b2_hash}},
        )

        self.worktree_path = Path(self.temp_dir.name) / "isolated_worktree"

    def tearDown(self) -> None:
        # Worktrees registered against a repository that is about to be
        # deleted must be pruned first, or a stale administrative entry can
        # leak into the surrounding environment's git metadata.
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(self.worktree_path)],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.temp_dir.cleanup()

    def _git_root(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True)

    def _head_root(self) -> str:
        return self._git_root("rev-parse", "HEAD").stdout.strip()


class IsolatedWorktreeCreationTests(_IsolatedOrchestrationTestCase):
    def test_creates_detached_worktree_pinned_to_baseline_commit(self) -> None:
        resolved = create_isolated_worktree(self.root, self.worktree_path, self.baseline_commit)
        self.assertEqual(resolved, self.baseline_commit)
        self.assertTrue(self.worktree_path.is_dir())
        head_in_worktree = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.worktree_path, capture_output=True, text=True, check=True
        ).stdout.strip()
        self.assertEqual(head_in_worktree, self.baseline_commit)
        status_in_worktree = subprocess.run(
            ["git", "status", "--porcelain"], cwd=self.worktree_path, capture_output=True, text=True, check=True
        ).stdout
        self.assertEqual(status_in_worktree, "")

    def test_unresolvable_baseline_commit_is_rejected(self) -> None:
        with self.assertRaises(ProvenanceSafeOrchestrationError):
            create_isolated_worktree(self.root, self.worktree_path, "0" * 40)
        self.assertFalse(self.worktree_path.exists())

    def test_existing_target_path_is_rejected(self) -> None:
        self.worktree_path.mkdir(parents=True)
        with self.assertRaises(ProvenanceSafeOrchestrationError):
            create_isolated_worktree(self.root, self.worktree_path, self.baseline_commit)

    def test_main_repo_untracked_noise_does_not_block_worktree_creation_or_leak_in(self) -> None:
        # The main repo carrying unrelated untracked noise (mirroring the
        # real repository's long-lived work_tmp/ and other stray files)
        # must not prevent creating a worktree, and that noise must not
        # appear inside the freshly created worktree.
        (self.root / "unrelated_noise.txt").write_text("noise\n", encoding="utf-8")
        create_isolated_worktree(self.root, self.worktree_path, self.baseline_commit)
        self.assertFalse((self.worktree_path / "unrelated_noise.txt").exists())


class IsolatedRunFullFlowTests(_IsolatedOrchestrationTestCase):
    def test_isolated_run_succeeds_and_post_run_verification_reports_no_blockers(self) -> None:
        record = run_isolated_provenance_safe(
            case_relative_dir="cases/fixture_case",
            run_dir_relative_to_case="work_tmp/isolated_run",
            worktree_path=self.worktree_path,
            baseline_commit=self.baseline_commit,
            root=self.root,
        )
        self.assertEqual(record["run_exit_code"], 0)
        self.assertEqual(record["run_result"]["status"], "ok")
        self.assertIsNotNone(record["post_run_verification"])
        self.assertEqual(record["post_run_verification"]["blockers"], [])
        self.assertTrue(record["post_run_verification"]["source_provenance_finalization_passed"])
        self.assertFalse(record["candidate_package_authorized"])
        self.assertFalse(record["final_freeze_authorized"])
        self.assertFalse(record["final_submit_creation_authorized"])

    def test_isolated_run_does_not_touch_main_repository_worktree(self) -> None:
        before_status = self._git_root("status", "--porcelain").stdout
        run_isolated_provenance_safe(
            case_relative_dir="cases/fixture_case",
            run_dir_relative_to_case="work_tmp/isolated_run_2",
            worktree_path=self.worktree_path,
            baseline_commit=self.baseline_commit,
            root=self.root,
        )
        after_status = self._git_root("status", "--porcelain").stdout
        self.assertEqual(before_status, after_status)
        # The isolated worktree's own outputs must not exist under the main
        # repo's case directory.
        self.assertFalse((self.case_dir / "work_tmp" / "isolated_run_2").exists())

    def test_isolated_run_survives_main_repo_being_dirty(self) -> None:
        # This is the central OV11C2 regression: the main repository
        # worktree being non-clean (as it routinely is, from unrelated
        # long-lived untracked artifacts) must not block an isolated run,
        # because the isolated run executes in its own clean worktree.
        (self.root / "unrelated_dirty_from_other_work.txt").write_text("noise\n", encoding="utf-8")
        record = run_isolated_provenance_safe(
            case_relative_dir="cases/fixture_case",
            run_dir_relative_to_case="work_tmp/isolated_run_dirty_main",
            worktree_path=self.worktree_path,
            baseline_commit=self.baseline_commit,
            root=self.root,
        )
        self.assertEqual(record["run_exit_code"], 0)
        self.assertEqual(record["run_result"]["status"], "ok")
        self.assertTrue(record["run_result"]["initial_worktree_clean"])


if __name__ == "__main__":
    unittest.main()
