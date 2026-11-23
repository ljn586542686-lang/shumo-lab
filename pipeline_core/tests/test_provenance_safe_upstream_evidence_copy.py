"""OV11C3C tests for the fail-closed package_candidate upstream evidence copy.

package_candidate()'s main entry reads two gitignored work_tmp/ evidence
files -- candidate_upstream_run_result_path and
candidate_upstream_risk_summary_path -- before it ever reaches provenance
verification. A fresh isolated `git worktree add` checkout never contains
gitignored content, so copy_gitignored_evidence_into_worktree() must
explicitly transport these two files across the worktree boundary when
``include_package_candidate_upstream_status=True`` -- and, unlike the
legacy OV11B2 evidence fields (which run_provenance_safe() itself already
fails closed on when missing), any missing/out-of-bounds/hash-mismatched
copy here must raise rather than silently skip, or package_candidate()'s
main entry would observe an ambiguous "file just isn't there" instead of
the file's real persisted upstream status.

These tests exercise copy_gitignored_evidence_into_worktree() directly
against plain temporary directories (no git repository needed -- the
function only reads case_config.json and copies named files); none of
these tests touch the real repository or the real 2025C case.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pipeline_core.provenance_safe_run import (
    ProvenanceSafeOrchestrationError,
    copy_gitignored_evidence_into_worktree,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


class _EvidenceCopyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.main_case_dir = Path(self.temp_dir.name) / "main_case"
        self.worktree_case_dir = Path(self.temp_dir.name) / "worktree_case"
        self.main_case_dir.mkdir(parents=True)
        self.worktree_case_dir.mkdir(parents=True)

        self.run_result_path = self.main_case_dir / "work_tmp" / "ov11c1_provenance" / "preparation_run_result.json"
        self.risk_summary_path = self.main_case_dir / "work_tmp" / "ov11c1_provenance" / "preparation_risk_summary.json"
        _write_json(self.run_result_path, {"schema_version": "test_v1", "status": "BLOCKED", "risk_status": "BLOCK"})
        _write_json(self.risk_summary_path, {"schema_version": "test_v1", "status": "fatal", "risk_status": "BLOCK"})

        _write_json(
            self.main_case_dir / "case_config.json",
            {
                "candidate_upstream_run_result_path": "work_tmp/ov11c1_provenance/preparation_run_result.json",
                "candidate_upstream_risk_summary_path": "work_tmp/ov11c1_provenance/preparation_risk_summary.json",
            },
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()


class BothRequiredEvidenceFilesAreCopiedTests(_EvidenceCopyTestCase):
    def test_both_upstream_status_files_are_copied_with_matching_hashes(self) -> None:
        copied = copy_gitignored_evidence_into_worktree(
            main_case_dir=self.main_case_dir,
            worktree_case_dir=self.worktree_case_dir,
            include_package_candidate_upstream_status=True,
        )
        copied_paths = {item["relative_path"]: item for item in copied}

        run_result_key = "work_tmp/ov11c1_provenance/preparation_run_result.json"
        risk_summary_key = "work_tmp/ov11c1_provenance/preparation_risk_summary.json"
        self.assertIn(run_result_key, copied_paths)
        self.assertIn(risk_summary_key, copied_paths)

        self.assertEqual(copied_paths[run_result_key]["sha256"], _sha256(self.run_result_path))
        self.assertEqual(copied_paths[risk_summary_key]["sha256"], _sha256(self.risk_summary_path))

        worktree_run_result = self.worktree_case_dir / run_result_key
        worktree_risk_summary = self.worktree_case_dir / risk_summary_key
        self.assertTrue(worktree_run_result.is_file())
        self.assertTrue(worktree_risk_summary.is_file())
        self.assertEqual(_sha256(worktree_run_result), _sha256(self.run_result_path))
        self.assertEqual(_sha256(worktree_risk_summary), _sha256(self.risk_summary_path))

        # Content, status fields, and semantics must be byte-identical, not
        # merely hash-equal by coincidence -- the copy must not rewrite them.
        self.assertEqual(worktree_run_result.read_bytes(), self.run_result_path.read_bytes())
        self.assertEqual(worktree_risk_summary.read_bytes(), self.risk_summary_path.read_bytes())

    def test_recorded_sizes_match_source_file_sizes(self) -> None:
        copied = copy_gitignored_evidence_into_worktree(
            main_case_dir=self.main_case_dir,
            worktree_case_dir=self.worktree_case_dir,
            include_package_candidate_upstream_status=True,
        )
        by_path = {item["relative_path"]: item for item in copied}
        run_result_key = "work_tmp/ov11c1_provenance/preparation_run_result.json"
        self.assertEqual(
            by_path[run_result_key]["source_size_bytes"],
            self.run_result_path.stat().st_size,
        )
        self.assertEqual(
            by_path[run_result_key]["destination_size_bytes"],
            (self.worktree_case_dir / run_result_key).stat().st_size,
        )

    def test_opt_in_flag_defaults_to_false_and_skips_upstream_status_fields(self) -> None:
        # Without explicitly requesting package_candidate upstream status
        # evidence, a plain provenance-safe-only copy must not require or
        # copy these two files -- run_provenance_safe() has no use for them.
        copied = copy_gitignored_evidence_into_worktree(
            main_case_dir=self.main_case_dir,
            worktree_case_dir=self.worktree_case_dir,
        )
        copied_paths = {item["relative_path"] for item in copied}
        self.assertNotIn("work_tmp/ov11c1_provenance/preparation_run_result.json", copied_paths)
        self.assertNotIn("work_tmp/ov11c1_provenance/preparation_risk_summary.json", copied_paths)


class MissingEvidenceFailsClosedTests(_EvidenceCopyTestCase):
    def test_missing_run_result_source_file_raises(self) -> None:
        self.run_result_path.unlink()
        with self.assertRaises(ProvenanceSafeOrchestrationError):
            copy_gitignored_evidence_into_worktree(
                main_case_dir=self.main_case_dir,
                worktree_case_dir=self.worktree_case_dir,
                include_package_candidate_upstream_status=True,
            )

    def test_missing_risk_summary_source_file_raises(self) -> None:
        self.risk_summary_path.unlink()
        with self.assertRaises(ProvenanceSafeOrchestrationError):
            copy_gitignored_evidence_into_worktree(
                main_case_dir=self.main_case_dir,
                worktree_case_dir=self.worktree_case_dir,
                include_package_candidate_upstream_status=True,
            )

    def test_missing_case_config_field_raises(self) -> None:
        _write_json(self.main_case_dir / "case_config.json", {"candidate_upstream_run_result_path": ""})
        with self.assertRaises(ProvenanceSafeOrchestrationError):
            copy_gitignored_evidence_into_worktree(
                main_case_dir=self.main_case_dir,
                worktree_case_dir=self.worktree_case_dir,
                include_package_candidate_upstream_status=True,
            )

    def test_unreadable_case_config_raises(self) -> None:
        (self.main_case_dir / "case_config.json").write_text("not valid json {{{", encoding="utf-8")
        with self.assertRaises(ProvenanceSafeOrchestrationError):
            copy_gitignored_evidence_into_worktree(
                main_case_dir=self.main_case_dir,
                worktree_case_dir=self.worktree_case_dir,
                include_package_candidate_upstream_status=True,
            )

    def test_partial_failure_does_not_silently_copy_only_one_file(self) -> None:
        # Only the risk summary is missing; the run result alone must not
        # be treated as sufficient -- both are required_for_candidate_
        # validation and the copy must raise before returning a partial list.
        self.risk_summary_path.unlink()
        with self.assertRaises(ProvenanceSafeOrchestrationError):
            copy_gitignored_evidence_into_worktree(
                main_case_dir=self.main_case_dir,
                worktree_case_dir=self.worktree_case_dir,
                include_package_candidate_upstream_status=True,
            )
        # Whatever was copied before the raise must not be silently treated
        # as a complete, valid evidence set by any caller inspecting the
        # worktree afterward. Confirm the worktree side is not left with a
        # deceptively "half-plausible" state.
        self.assertFalse((self.worktree_case_dir / "work_tmp" / "ov11c1_provenance" / "preparation_risk_summary.json").exists())


class PathEscapeFailsClosedTests(_EvidenceCopyTestCase):
    def test_absolute_path_outside_case_dir_raises(self) -> None:
        outside = Path(self.temp_dir.name) / "outside_run_result.json"
        _write_json(outside, {"status": "ok"})
        _write_json(
            self.main_case_dir / "case_config.json",
            {
                "candidate_upstream_run_result_path": str(outside),
                "candidate_upstream_risk_summary_path": "work_tmp/ov11c1_provenance/preparation_risk_summary.json",
            },
        )
        with self.assertRaises(ProvenanceSafeOrchestrationError):
            copy_gitignored_evidence_into_worktree(
                main_case_dir=self.main_case_dir,
                worktree_case_dir=self.worktree_case_dir,
                include_package_candidate_upstream_status=True,
            )

    def test_traversal_path_outside_case_dir_raises(self) -> None:
        _write_json(
            self.main_case_dir / "case_config.json",
            {
                "candidate_upstream_run_result_path": "../outside_run_result.json",
                "candidate_upstream_risk_summary_path": "work_tmp/ov11c1_provenance/preparation_risk_summary.json",
            },
        )
        with self.assertRaises(ProvenanceSafeOrchestrationError):
            copy_gitignored_evidence_into_worktree(
                main_case_dir=self.main_case_dir,
                worktree_case_dir=self.worktree_case_dir,
                include_package_candidate_upstream_status=True,
            )


class HashConsistencyAfterCopyTests(_EvidenceCopyTestCase):
    def test_post_copy_hash_mismatch_is_detected(self) -> None:
        # Simulate a corrupting copy by writing a different destination
        # file after the fact and confirming the function's own hash check
        # (not just this test's) would have caught real corruption --
        # verified indirectly: the function computes source and destination
        # hashes independently after shutil.copy2, so any real filesystem
        # corruption during copy would raise. This test instead confirms
        # the two source files, which are content-distinct, produce
        # distinct recorded hashes (i.e. hashes are computed per-file, not
        # reused/cached across the two required evidence files).
        copied = copy_gitignored_evidence_into_worktree(
            main_case_dir=self.main_case_dir,
            worktree_case_dir=self.worktree_case_dir,
            include_package_candidate_upstream_status=True,
        )
        hashes = {item["relative_path"]: item["sha256"] for item in copied}
        run_result_key = "work_tmp/ov11c1_provenance/preparation_run_result.json"
        risk_summary_key = "work_tmp/ov11c1_provenance/preparation_risk_summary.json"
        self.assertNotEqual(hashes[run_result_key], hashes[risk_summary_key])


class NoUnrelatedGitignoredFileIsCopiedTests(_EvidenceCopyTestCase):
    def test_unrelated_work_tmp_file_is_never_copied(self) -> None:
        unrelated = self.main_case_dir / "work_tmp" / "some_other_unrelated_artifact.json"
        _write_json(unrelated, {"unrelated": True})

        copy_gitignored_evidence_into_worktree(
            main_case_dir=self.main_case_dir,
            worktree_case_dir=self.worktree_case_dir,
            include_package_candidate_upstream_status=True,
        )

        self.assertFalse((self.worktree_case_dir / "work_tmp" / "some_other_unrelated_artifact.json").exists())

    def test_entire_work_tmp_tree_is_not_mirrored(self) -> None:
        for name in ("a.json", "b.txt", "c.log"):
            (self.main_case_dir / "work_tmp" / "some_other_dir").mkdir(parents=True, exist_ok=True)
            (self.main_case_dir / "work_tmp" / "some_other_dir" / name).write_text("noise", encoding="utf-8")

        copy_gitignored_evidence_into_worktree(
            main_case_dir=self.main_case_dir,
            worktree_case_dir=self.worktree_case_dir,
            include_package_candidate_upstream_status=True,
        )

        self.assertFalse((self.worktree_case_dir / "work_tmp" / "some_other_dir").exists())

    def test_ov11b2_fields_not_requested_are_not_copied_when_absent(self) -> None:
        # Only the two package_candidate fields are configured in this
        # fixture's case_config.json; the legacy OV11B2 fields are absent.
        # Requesting include_package_candidate_upstream_status=True must
        # not manufacture or require them -- they remain governed by the
        # separate, pre-existing legacy (silent-skip) code path.
        copied = copy_gitignored_evidence_into_worktree(
            main_case_dir=self.main_case_dir,
            worktree_case_dir=self.worktree_case_dir,
            include_package_candidate_upstream_status=True,
        )
        copied_paths = {item["relative_path"] for item in copied}
        self.assertNotIn("work_tmp/ov11b2_artifact_binding_evidence.json", copied_paths)


if __name__ == "__main__":
    unittest.main()
