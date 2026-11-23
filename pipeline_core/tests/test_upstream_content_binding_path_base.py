"""Regression tests for the ``upstream_content_bindings[].relative_path``
path-base contract between ``run_case.py::_provenance_safe_input_binding()``
(producer) and ``package_candidate.py::_content_binding_mismatches()``
(consumer).

Both functions were first introduced together in commit 0bbe086 ("P4-S2G
bind upstream status to content hashes"), where the founding producer
(``run_case.py::_validated_content_bindings()``/``_case_relative_file()``)
always emitted case_dir-relative paths (e.g. ``"output/result_summary.json"``,
with no ``cases/<case>/`` prefix), and the consumer has always resolved
``relative_path`` as ``case_dir / relative_path``. A later producer,
``run_case.py::_provenance_safe_input_binding()`` (added in the OV11C3A
provenance-safe runner), incorrectly resolved paths against the worktree
root instead of ``case_dir`` -- producing a doubled
``cases/<case>/cases/<case>/...`` path when the consumer resolved it,
which the consumer's fail-closed "missing" branch reported as
``UPSTREAM_STATUS_STALE`` instead of the artifact's real semantic status.

This suite fixes and locks the case_dir-relative contract for good.
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
from package_candidate import _content_binding_mismatches, package_candidate


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class ProvenanceSafeInputBindingProducerTests(unittest.TestCase):
    """Requirement 1: producer output conforms to the case_dir-relative
    contract, not the worktree root."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "repo"
        self.case_dir = self.root / "cases" / "case_x"
        self.case_dir.mkdir(parents=True)
        self.nested = self.case_dir / "output" / "result_summary.json"
        self.nested.parent.mkdir(parents=True)
        self.nested.write_text('{"status": "ok"}', encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_relative_path_is_case_dir_relative_not_worktree_root_relative(self) -> None:
        binding = run_case_module._provenance_safe_input_binding(
            self.case_dir, self.nested, "canonical_result", "result_summary"
        )
        self.assertEqual(binding["relative_path"], "output/result_summary.json")
        # The pre-fix bug produced a worktree-root-relative path with the
        # case's own subpath doubled in as a prefix, e.g.
        # "cases/case_x/output/result_summary.json" -- explicitly assert
        # against that regression shape too.
        self.assertNotEqual(binding["relative_path"], "cases/case_x/output/result_summary.json")

    def test_resolving_producer_path_against_case_dir_hits_the_real_file(self) -> None:
        binding = run_case_module._provenance_safe_input_binding(
            self.case_dir, self.nested, "canonical_result", "result_summary"
        )
        resolved = (self.case_dir / binding["relative_path"]).resolve()
        self.assertEqual(resolved, self.nested.resolve())
        self.assertTrue(resolved.is_file())

    def test_no_case_directory_name_duplication_in_resolved_path(self) -> None:
        binding = run_case_module._provenance_safe_input_binding(
            self.case_dir, self.nested, "canonical_result", "result_summary"
        )
        resolved_str = str((self.case_dir / binding["relative_path"]).resolve())
        case_marker = str(Path("cases") / "case_x")
        self.assertEqual(resolved_str.count(case_marker), 1)


class ContentBindingMismatchesConsumerRoundTripTests(unittest.TestCase):
    """Requirements 2-8: the consumer, fed a producer-shaped payload,
    resolves the same real file -- including hash-match/mismatch, path
    escape rejection, and Windows separator/drive-letter normalization."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "repo"
        self.case_dir = self.root / "cases" / "case_y"
        (self.case_dir / "output").mkdir(parents=True)
        self.target = self.case_dir / "output" / "result_summary.json"
        self.target.write_bytes(b'{"status": "ok"}')
        self.original_bytes = self.target.read_bytes()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _binding_payload(self, relative_path: str, sha256: str) -> dict[str, object]:
        return {
            "risk_status": "PASS",
            "status": "ok",
            "fatal_count": 0,
            "upstream_content_bindings": [
                {
                    "logical_name": "result_summary",
                    "relative_path": relative_path,
                    "sha256": sha256,
                    "required_for_candidate_validation": True,
                    "source_role": "result_summary",
                }
            ],
        }

    def test_producer_shaped_binding_resolves_to_the_real_file_no_mismatch(self) -> None:
        binding = run_case_module._provenance_safe_input_binding(
            self.case_dir, self.target, "canonical_result", "result_summary"
        )
        payload = self._binding_payload(binding["relative_path"], binding["sha256"])
        mismatches = _content_binding_mismatches(self.case_dir, payload, "upstream_status.json")
        self.assertEqual(mismatches, [])

    def test_no_doubled_case_path_produced_by_consumer_resolution(self) -> None:
        binding = run_case_module._provenance_safe_input_binding(
            self.case_dir, self.target, "canonical_result", "result_summary"
        )
        raw_path = Path(binding["relative_path"])
        resolved = (self.case_dir / raw_path).resolve()
        self.assertTrue(resolved.is_file())
        self.assertNotIn("cases" + "\\cases", str(resolved))
        self.assertNotIn("cases/cases", str(resolved).replace("\\", "/"))

    def test_unmodified_bytes_hash_matches(self) -> None:
        expected = _sha256_bytes(self.original_bytes)
        payload = self._binding_payload("output/result_summary.json", expected)
        mismatches = _content_binding_mismatches(self.case_dir, payload, "upstream_status.json")
        self.assertEqual(mismatches, [])

    def test_single_byte_modification_hits_hash_mismatch(self) -> None:
        expected = _sha256_bytes(self.original_bytes)
        self.target.write_bytes(self.original_bytes + b"\n")
        payload = self._binding_payload("output/result_summary.json", expected)
        mismatches = _content_binding_mismatches(self.case_dir, payload, "upstream_status.json")
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0]["reason"], "sha256_mismatch")

    def test_absolute_path_rejected(self) -> None:
        expected = _sha256_bytes(self.original_bytes)
        absolute = str(self.target.resolve())
        payload = self._binding_payload(absolute, expected)
        mismatches = _content_binding_mismatches(self.case_dir, payload, "upstream_status.json")
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0]["reason"], "unsafe_path")

    def test_parent_traversal_path_rejected(self) -> None:
        expected = _sha256_bytes(self.original_bytes)
        payload = self._binding_payload("../case_y/output/result_summary.json", expected)
        mismatches = _content_binding_mismatches(self.case_dir, payload, "upstream_status.json")
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0]["reason"], "unsafe_path")

    def test_windows_backslash_separator_normalizes_to_the_same_file(self) -> None:
        expected = _sha256_bytes(self.original_bytes)
        payload = self._binding_payload("output\\result_summary.json", expected)
        mismatches = _content_binding_mismatches(self.case_dir, payload, "upstream_status.json")
        self.assertEqual(mismatches, [])


class ProvenanceSafeRealRunProducesConsumableBindingsTests(unittest.TestCase):
    """Requirement 9: a real ``run_provenance_safe()`` output -- not a
    hand-written JSON payload -- is handed directly to
    ``_content_binding_mismatches`` and resolves cleanly."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "repo"
        self.root.mkdir()
        self.case_dir = self.root / "cases" / "fixture_case"
        self.case_dir.mkdir(parents=True)
        self._git("init", "-b", "main")
        self._git("config", "user.email", "binding-fixture@example.com")
        self._git("config", "user.name", "Binding Fixture")
        (self.root / ".gitignore").write_text("work_tmp/\n", encoding="utf-8")

        canonical = self.case_dir / "results" / "result_summary.json"
        _write_json(canonical, {"case_id": "fixture_case", "status": "ok"})
        canonical_hash = _sha256_bytes(canonical.read_bytes())

        _write_json(
            self.case_dir / "case_config.json",
            {
                "case_id": "fixture_case",
                "source_commit": "PENDING",
                "canonical_result_path": "results/result_summary.json",
                "canonical_result_sha256": canonical_hash,
                "r3_required": False,
                "ov11b2_isolated_rebuild_output_path": "work_tmp/ov11b2_isolated_rebuild/build/main.pdf",
                "ov11b2_isolated_rebuild_sha256": "PENDING",
            },
        )
        self._git("add", "-A")
        self._git("commit", "-m", "fixture case scaffold")
        config_path = self.case_dir / "case_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["source_commit"] = self._head()
        _write_json(config_path, config)
        self._git("add", "-A")
        self._git("commit", "-m", "bind source_commit")

        self.b2_pdf = self.case_dir / "work_tmp" / "ov11b2_isolated_rebuild" / "build" / "main.pdf"
        self.b2_pdf.parent.mkdir(parents=True)
        self.b2_pdf.write_bytes(b"%PDF-fixture\n")
        b2_hash = _sha256_bytes(self.b2_pdf.read_bytes())
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["ov11b2_isolated_rebuild_sha256"] = b2_hash
        _write_json(config_path, config)
        self._git("add", str(config_path.relative_to(self.root)))
        self._git("commit", "-m", "bind B2 hash")

        _write_json(
            self.case_dir / "work_tmp" / "ov11b2_artifact_binding_evidence.json",
            {"artifacts": {"isolated_pdf_sha256": b2_hash}},
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True)

    def _head(self) -> str:
        return self._git("rev-parse", "HEAD").stdout.strip()

    def test_real_run_result_bindings_are_directly_consumable_by_package_candidate(self) -> None:
        with mock.patch.object(run_case_module, "ROOT", self.root):
            result = run_case_module.run_provenance_safe(
                self.case_dir,
                "work_tmp/real_run",
                command_argv=["run_case.py", "--provenance-safe", "--run-dir", "work_tmp/real_run"],
            )
        self.assertEqual(result["status"], "ok")
        bindings = result["upstream_content_bindings"]
        self.assertTrue(bindings)

        # This is the exact payload shape package_candidate() consumes: no
        # manual rewriting of the real run_result.json.
        mismatches = _content_binding_mismatches(self.case_dir, result, "run_result.json")
        self.assertEqual(mismatches, [])

        # None of the produced relative_path values may carry a doubled
        # case-directory prefix (the pre-fix UPSTREAM_STATUS_STALE bug
        # shape).
        for binding in bindings:
            raw = binding["relative_path"]
            self.assertFalse(raw.startswith("cases/"), raw)
            resolved = (self.case_dir / raw).resolve()
            self.assertTrue(resolved.is_file(), raw)


class LegacyRootRelativeRecordFailsClosedTests(unittest.TestCase):
    """Requirement 10: an old, root-relative-shaped record (the pre-fix
    producer's output, or any other non-conforming record) must be
    structurally blocked -- reported as a real mismatch -- not silently
    reinterpreted or auto-corrected by guessing an alternate base."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "repo"
        self.case_dir = self.root / "cases" / "case_legacy"
        (self.case_dir / "output").mkdir(parents=True)
        self.target = self.case_dir / "output" / "result_summary.json"
        self.target.write_bytes(b'{"status": "ok"}')

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_worktree_root_relative_legacy_path_is_reported_missing_not_silently_fixed(self) -> None:
        expected = _sha256_bytes(self.target.read_bytes())
        legacy_relative_path = "cases/case_legacy/output/result_summary.json"
        payload = {
            "risk_status": "PASS",
            "status": "ok",
            "fatal_count": 0,
            "upstream_content_bindings": [
                {
                    "logical_name": "result_summary",
                    "relative_path": legacy_relative_path,
                    "sha256": expected,
                    "required_for_candidate_validation": True,
                    "source_role": "result_summary",
                }
            ],
        }
        mismatches = _content_binding_mismatches(self.case_dir, payload, "upstream_status.json")
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0]["reason"], "missing_file")


class RealChainProvenanceSafeToPackageCandidateTests(unittest.TestCase):
    """Requirement 9 (integration form): real
    ``run_provenance_safe()`` output feeds ``package_candidate()``'s main
    entry (via its ``candidate_upstream_run_result_path``/
    ``candidate_upstream_risk_summary_path`` configuration), in-process but
    against a real, separate case directory -- proving the fix all the way
    through the actual upstream-status guard, not just the bindings
    helper in isolation. This does not exercise final_freezer and does not
    create final_submit/.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "repo"
        self.root.mkdir()
        self.case_dir = self.root / "cases" / "chain_case"
        self.case_dir.mkdir(parents=True)
        self._git("init", "-b", "main")
        self._git("config", "user.email", "chain-fixture@example.com")
        self._git("config", "user.name", "Chain Fixture")
        (self.root / ".gitignore").write_text("work_tmp/\ncandidate_submit/\n", encoding="utf-8")

        result_summary = self.case_dir / "output" / "result_summary.json"
        _write_json(result_summary, {"status": "ok", "schema_version": "x5_f0_result_summary_v1"})
        canonical_hash = _sha256_bytes(result_summary.read_bytes())

        run_result_path = self.case_dir / "work_tmp" / "chain_run" / "run_result.json"
        risk_summary_path = self.case_dir / "work_tmp" / "chain_run" / "risk_summary.json"
        _write_json(
            self.case_dir / "case_config.json",
            {
                "case_id": "chain_case",
                "source_commit": "PENDING",
                "canonical_result_path": "output/result_summary.json",
                "canonical_result_sha256": canonical_hash,
                "ov11b2_isolated_rebuild_output_path": "work_tmp/ov11b2_isolated_rebuild/build/main.pdf",
                "ov11b2_isolated_rebuild_sha256": "PENDING",
                "submission_tier": "selection",
                "claim_check_required": False,
                "core_claims": [],
                "human_revision_required": False,
                "human_revision_mode": "off",
                "r3_required": False,
                # Bound to the run's own future output paths up front, so
                # case_config.json's committed hash never changes again
                # after run_provenance_safe() records it as one of the
                # bindings -- otherwise a later edit to case_config.json
                # would produce a real (and separate) staleness mismatch
                # unrelated to the path-base bug this suite targets.
                "candidate_upstream_run_result_path": str(run_result_path.resolve()),
                "candidate_upstream_risk_summary_path": str(risk_summary_path.resolve()),
            },
        )
        self._git("add", "-A")
        self._git("commit", "-m", "fixture scaffold")
        config_path = self.case_dir / "case_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["source_commit"] = self._head()
        _write_json(config_path, config)
        self._git("add", "-A")
        self._git("commit", "-m", "bind source_commit")

        self.b2_pdf = self.case_dir / "work_tmp" / "ov11b2_isolated_rebuild" / "build" / "main.pdf"
        self.b2_pdf.parent.mkdir(parents=True)
        self.b2_pdf.write_bytes(b"%PDF-chain-fixture\n")
        b2_hash = _sha256_bytes(self.b2_pdf.read_bytes())
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["ov11b2_isolated_rebuild_sha256"] = b2_hash
        _write_json(config_path, config)
        self._git("add", str(config_path.relative_to(self.root)))
        self._git("commit", "-m", "bind B2 hash")

        _write_json(
            self.case_dir / "work_tmp" / "ov11b2_artifact_binding_evidence.json",
            {"artifacts": {"isolated_pdf_sha256": b2_hash}},
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True)

    def _head(self) -> str:
        return self._git("rev-parse", "HEAD").stdout.strip()

    def test_real_provenance_safe_output_no_longer_stales_package_candidate_on_path_duplication(self) -> None:
        with mock.patch.object(run_case_module, "ROOT", self.root):
            provenance_result = run_case_module.run_provenance_safe(
                self.case_dir,
                "work_tmp/chain_run",
                command_argv=["run_case.py", "--provenance-safe", "--run-dir", "work_tmp/chain_run"],
            )
        self.assertEqual(provenance_result["status"], "ok")

        run_result_path = self.case_dir / "work_tmp" / "chain_run" / "run_result.json"
        risk_summary_path = self.case_dir / "work_tmp" / "chain_run" / "risk_summary.json"
        self.assertTrue(run_result_path.is_file())
        self.assertTrue(risk_summary_path.is_file())

        from package_candidate import CandidatePromotionUpstreamStatusBlocked

        try:
            package_candidate(case_dir=self.case_dir, root=self.root)
        except CandidatePromotionUpstreamStatusBlocked as exc:
            self.assertNotEqual(exc.result.get("code"), "CANDIDATE_PROMOTION_UPSTREAM_STATUS_STALE")
            self.assertNotEqual(exc.result.get("upstream_blocker"), "UPSTREAM_STATUS_STALE")
        # Whether package_candidate() proceeds past the upstream-status
        # guard (raising a later, unrelated known-blocked exception such as
        # claim-check/human-revision/provenance) or returns is immaterial
        # here -- this test only proves the path-duplication-driven
        # UPSTREAM_STATUS_STALE regression is gone. No assertion depends on
        # candidate_submit/ having been created.
        self.assertFalse((self.root / "final_submit").exists())


if __name__ == "__main__":
    unittest.main()
