from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline_core.python_tools.final_freezer import (
    _calculate_candidate_package_digest,
    check_freeze_ready,
)
from pipeline_core.python_tools.post_writer_reentry import (
    R1_AUTHORITY,
    SCHEMA_VERSION,
    compute_contract_digest,
    validate_post_writer_reentry,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TrustedPackageRootTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.case = self.root / "cases" / "case_trusted"
        self.package = Path(self.tmp.name) / "candidate" / "candidate_001"
        self.external = Path(self.tmp.name) / "external"
        self.case.mkdir(parents=True)
        self.package.mkdir(parents=True)
        self.external.mkdir()
        (self.case / "output").mkdir()
        self._write_case_artifacts()
        self._write_package()
        self._write_sidecar()
        self._write_authorization()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_case_artifacts(self) -> None:
        primary = self.case / "output" / "q1_primary.json"
        primary.write_text(json.dumps({"question_id": "Q1", "artifact_role": "primary_result", "status": "PASS"}), encoding="utf-8")
        self.primary = primary
        self.case_config = {"authoritative_result_artifacts": [{"question_id": "Q1", "artifact_role": "primary_result", "path": "output/q1_primary.json"}]}

    def _write_package(self) -> None:
        index = {
            "schema_version": "x5_f0_canonical_result_index_v1",
            "status": "PASS",
            "authority_scope_key": ["question_id", "artifact_role"],
            "result_authority": "primary_result",
            "entries": [{"question_id": "Q1", "artifact_role": "primary_result", "artifact_path": "output/q1_primary.json", "sha256": _sha(self.primary)}],
        }
        index_path = self.package / "authoritative_result_index.json"
        index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")
        handoff = {
            "schema_version": "s0_r2a_structured_claim_handoff_v1",
            "status": "PASS",
            "result_authority": R1_AUTHORITY,
            "claims": [{
                "claim_id": "C-01",
                "claim_check_status": "PASS",
                "traceability": {"primary_claim_anchor": {"anchor_id": "paper:results:C-01", "claim_id": "C-01", "role": "PRIMARY_CLAIM_ANCHOR", "reference": "paper.tex#C-01"}, "secondary_references": []},
            }],
        }
        handoff_path = self.package / "claim_handoff.json"
        handoff_path.write_text(json.dumps(handoff, indent=2), encoding="utf-8")
        provenance = {
            "source_provenance_required": True,
            "source_provenance_applicable": True,
            "source_provenance_legacy_compatibility": False,
            "source_provenance_finalization_passed": True,
            "post_snapshot_verification_passed": True,
            "packaging_head_matches_source_commit": True,
            "packaging_git_operation_in_progress": False,
            "packaging_unexpected_status_entries": [],
            "blockers": [],
            "source_provenance_sha256": "9" * 64,
            "source_commit": "c" * 40,
        }
        names = ("run_result", "risk_summary", "result_summary", "route_review", "xval", "claim_check", "human_revision")
        bindings = {name: {"applicable": False, "semantic_valid": True, "semantic_status": "NOT_APPLICABLE"} for name in names}
        manifest = {
            "schema_version": "x5_f0_candidate_manifest_v1",
            "status": "candidate",
            "package_id": "candidate_001",
            "case_dir": "cases/case_trusted",
            "case_identity": "cases/case_trusted",
            "run_id": "run-001",
            "source_provenance_verification": provenance,
            "upstream_artifact_bindings": bindings,
            "authoritative_result_index": {"path": "authoritative_result_index.json", "sha256": _sha(index_path)},
            "claim_handoff": {"path": "claim_handoff.json", "sha256": _sha(handoff_path), "schema_version": handoff["schema_version"], "status": "PASS", "result_authority": R1_AUTHORITY},
        }
        (self.package / "payload.txt").write_text("payload", encoding="utf-8")
        self.manifest_path = self.package / "manifest.json"
        self.manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        self.original_manifest_sha256 = _sha(self.manifest_path)

    def _write_sidecar(self) -> None:
        self.paper = self.external / "paper.tex"
        self.paper.write_text("\\section{C-01}\\label{C-01} deterministic result", encoding="utf-8")
        payload = {
            "schema_version": SCHEMA_VERSION,
            "status": "READY",
            "paper_source": {"path": "paper.tex", "sha256": _sha(self.paper)},
            "result_authority": {"source": R1_AUTHORITY, "index_path": str(self.package / "authoritative_result_index.json"), "case_dir": str(self.case), "references": [{"question_id": "Q1", "artifact_role": "primary_result"}]},
            "claim_handoff_path": str(self.package / "claim_handoff.json"),
            "required_claim_ids": ["C-01"],
        }
        payload["contract_digest"] = compute_contract_digest(payload)
        self.sidecar = self.external / "post_writer_reentry.json"
        self.sidecar.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _write_authorization(self) -> None:
        record = {
            "schema_version": "p6_ff_external_authorization_v1",
            "authorization_mode": "production",
            "authorization_status": "APPROVED",
            "authorized_by_display_name": "test reviewer",
            "authorization_record_ref": "fixture",
            "authorized_at": "2026-08-09T10:00:00+00:00",
            "candidate_manifest_path": str(self.manifest_path),
            "candidate_manifest_sha256": self.original_manifest_sha256,
            "candidate_package_digest": _calculate_candidate_package_digest(self.package),
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
            "not_automatic_submission_statement": "This authorization does not automatically submit or create final_submit.",
            "revocation_or_supersession_ref": "none",
        }
        self.authorization = self.external / "authorization.json"
        self.authorization.write_text(json.dumps(record, indent=2), encoding="utf-8")

    def _freeze(self, **overrides: object) -> dict:
        params = {
            "authorization_path": self.authorization,
            "candidate_manifest_path": self.manifest_path,
            "expected_candidate_manifest_sha256": self.original_manifest_sha256,
            "expected_run_id": "run-001",
            "post_writer_reentry_path": self.sidecar,
            "require_post_writer_reentry": True,
            "repo_root": self.root,
            "requested_mode": "production",
        }
        params.update(overrides)
        return check_freeze_ready(self.case, **params)

    def _assert_trust_failure_does_not_consume_reentry(self, result: dict) -> None:
        self.assertFalse(result["post_writer_reentry_consumed"])
        self.assertFalse(result["post_writer_reentry_passed"])
        self.assertFalse(result["downstream_gate_reached"])
        self.assertEqual(result["post_writer_reentry_status"], "NOT_EVALUATED_TRUST_PREREQUISITE_FAILED")

    def test_positive_control_binds_identity_and_sidecar(self) -> None:
        result = self._freeze()
        self.assertNotIn("CANDIDATE_MANIFEST_HASH_MISMATCH", result["blockers"])
        self.assertNotIn("CANDIDATE_CASE_IDENTITY_MISMATCH", result["blockers"])
        self.assertNotIn("CANDIDATE_RUN_ID_MISMATCH", result["blockers"])
        self.assertEqual(result["post_writer_reentry"]["status"], "PASS")

    def test_missing_trusted_root_blocks(self) -> None:
        with patch("pipeline_core.python_tools.final_freezer.validate_post_writer_reentry") as checker:
            result = self._freeze(candidate_manifest_path=None, expected_candidate_manifest_sha256=None)
        checker.assert_not_called()
        self.assertIn("TRUSTED_CANDIDATE_ROOT_REQUIRED", result["blockers"])
        self._assert_trust_failure_does_not_consume_reentry(result)

    def test_manifest_tamper_blocks(self) -> None:
        self.manifest_path.write_text(self.manifest_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with patch("pipeline_core.python_tools.final_freezer.validate_post_writer_reentry") as checker:
            result = self._freeze()
        checker.assert_not_called()
        self.assertIn("CANDIDATE_MANIFEST_HASH_MISMATCH", result["blockers"])
        self._assert_trust_failure_does_not_consume_reentry(result)

    def test_wrong_case_self_consistent_manifest_blocks(self) -> None:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["case_identity"] = "cases/other_case"
        manifest["case_dir"] = "cases/other_case"
        self.manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        with patch("pipeline_core.python_tools.final_freezer.validate_post_writer_reentry") as checker:
            result = self._freeze(expected_candidate_manifest_sha256=_sha(self.manifest_path))
        checker.assert_not_called()
        self.assertIn("CANDIDATE_CASE_IDENTITY_MISMATCH", result["blockers"])
        self._assert_trust_failure_does_not_consume_reentry(result)

    def test_wrong_run_blocks(self) -> None:
        with patch("pipeline_core.python_tools.final_freezer.validate_post_writer_reentry") as checker:
            result = self._freeze(expected_run_id="run-other")
        checker.assert_not_called()
        self.assertIn("CANDIDATE_RUN_ID_MISMATCH", result["blockers"])
        self._assert_trust_failure_does_not_consume_reentry(result)

    def test_forged_r1_sidecar_blocks(self) -> None:
        payload = json.loads(self.sidecar.read_text(encoding="utf-8"))
        forged = self.external / "forged_index.json"
        forged.write_text((self.package / "authoritative_result_index.json").read_text(encoding="utf-8"), encoding="utf-8")
        payload["result_authority"]["index_path"] = str(forged)
        payload["contract_digest"] = compute_contract_digest(payload)
        self.sidecar.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        result = self._freeze()
        self.assertIn("POST_WRITER_RESULT_AUTHORITY_PROVENANCE_MISMATCH", result["blockers"])

    def test_forged_r2a_sidecar_blocks(self) -> None:
        payload = json.loads(self.sidecar.read_text(encoding="utf-8"))
        forged = self.external / "forged_handoff.json"
        forged.write_text((self.package / "claim_handoff.json").read_text(encoding="utf-8"), encoding="utf-8")
        payload["claim_handoff_path"] = str(forged)
        payload["contract_digest"] = compute_contract_digest(payload)
        self.sidecar.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        result = self._freeze()
        self.assertIn("POST_WRITER_CLAIM_HANDOFF_PROVENANCE_MISMATCH", result["blockers"])

    def test_post_package_r1_mutation_blocks(self) -> None:
        path = self.package / "authoritative_result_index.json"
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with patch("pipeline_core.python_tools.final_freezer.validate_post_writer_reentry") as checker:
            result = self._freeze()
        checker.assert_not_called()
        self.assertIn("R1_INDEX_PROVENANCE_MISMATCH", result["blockers"])
        self._assert_trust_failure_does_not_consume_reentry(result)

    def test_post_package_r2a_mutation_blocks(self) -> None:
        path = self.package / "claim_handoff.json"
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with patch("pipeline_core.python_tools.final_freezer.validate_post_writer_reentry") as checker:
            result = self._freeze()
        checker.assert_not_called()
        self.assertIn("R2A_HANDOFF_PROVENANCE_MISMATCH", result["blockers"])
        self._assert_trust_failure_does_not_consume_reentry(result)

    def test_actual_anchor_removed_blocks_even_with_updated_paper_hash(self) -> None:
        self.paper.write_text("no anchor", encoding="utf-8")
        payload = json.loads(self.sidecar.read_text(encoding="utf-8"))
        payload["paper_source"]["sha256"] = _sha(self.paper)
        payload["contract_digest"] = compute_contract_digest(payload)
        self.sidecar.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        result = self._freeze()
        self.assertIn("POST_WRITER_PAPER_ANCHOR_NOT_FOUND:C-01", result["blockers"])

    def test_missing_reentry_blocks(self) -> None:
        result = self._freeze(post_writer_reentry_path=None)
        self.assertIn("POST_WRITER_REENTRY_REQUIRED", result["blockers"])

    def test_same_claim_duplicate_primary_blocks(self) -> None:
        payload = json.loads(self.sidecar.read_text(encoding="utf-8"))
        handoff = json.loads((self.package / "claim_handoff.json").read_text(encoding="utf-8"))
        duplicate = json.loads(json.dumps(handoff["claims"][0]))
        duplicate["traceability"]["primary_claim_anchor"]["anchor_id"] = "paper:results:C-02"
        handoff["claims"].append(duplicate)
        handoff_path = self.external / "duplicate_handoff.json"
        handoff_path.write_text(json.dumps(handoff, indent=2), encoding="utf-8")
        payload["claim_handoff_path"] = str(handoff_path)
        payload["contract_digest"] = compute_contract_digest(payload)
        self.sidecar.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        result = validate_post_writer_reentry(
            self.sidecar,
            repo_root=self.root,
            trusted_index_path=self.package / "authoritative_result_index.json",
            trusted_index_sha256=_sha(self.package / "authoritative_result_index.json"),
            trusted_handoff_path=handoff_path,
            trusted_handoff_sha256=_sha(handoff_path),
        )
        self.assertIn("POST_WRITER_DUPLICATE_CLAIM_ID", result["blockers"])


if __name__ == "__main__":
    unittest.main()
