from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from canonical_result_index import build_index
from pipeline_core.python_tools.final_freezer import check_freeze_ready
from pipeline_core.python_tools.r3_approval_interface import canonicalize_approved_constraint_payload
from pipeline_core.python_tools.r3_validator import ARTIFACT_SCHEMA_VERSION, prepare_review_constraints_artifact
from pipeline_core.python_tools.post_writer_reentry import (
    SCHEMA_VERSION,
    compute_contract_digest,
    validate_post_writer_reentry,
)
from pipeline_core.python_tools.structured_semantic_reentry import SCHEMA_VERSION as R4B_SCHEMA_VERSION


class PostWriterReentryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        self.case = self.root / "cases" / "case_r4a"
        (self.case / "output").mkdir(parents=True)
        (self.root / "reentry").mkdir(parents=True)
        self._write_case()
        self._write_reentry()
        self._write_r3_fixture()
        self._write_trusted_manifest()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_case(self) -> None:
        primary = self.case / "output" / "q4_primary.json"
        validation = self.case / "output" / "q4_validation.json"
        primary.write_text(json.dumps({"question_id": "Q4", "artifact_role": "primary_result", "status": "PASS"}), encoding="utf-8")
        validation.write_text(json.dumps({"question_id": "Q4", "artifact_role": "validation_evidence", "status": "PASS"}), encoding="utf-8")
        config = {
            "r3_required": True,
            "authoritative_result_artifacts": [
                {"question_id": "Q4", "artifact_role": "primary_result", "path": "output/q4_primary.json"},
                {"question_id": "Q4", "artifact_role": "validation_evidence", "path": "output/q4_validation.json"},
            ],
            "authoritative_required_scopes": [["Q4", "primary_result"], ["Q4", "validation_evidence"]],
        }
        (self.case / "case_config.json").write_text(json.dumps(config), encoding="utf-8")
        index = build_index(self.case, config, "run_r4a")
        self.index_path = self.root / "reentry" / "authoritative_result_index.json"
        self.index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")

    def _write_handoff(self, claims: list[dict[str, object]] | None = None) -> None:
        if claims is None:
            claims = [
                {
                    "claim_id": "C-01",
                    "claim_check_status": "PASS",
                    "approved_constraints": [
                        {
                            "constraint_type": "LOWER_BOUND",
                            "symbol": "eta",
                            "value": 0,
                            "comparison_tolerance": {"abs_tolerance": 0.0},
                        }
                    ],
                    "paper_span_reference": {"claim_id": "C-01"},
                    "traceability": {
                        "primary_claim_anchor": {
                            "anchor_id": "paper:results:C-01",
                            "claim_id": "C-01",
                            "role": "PRIMARY_CLAIM_ANCHOR",
                            "reference": "paper.tex#C-01",
                        },
                        "secondary_references": [],
                    },
                }
            ]
        handoff = {
            "schema_version": "s0_r2a_structured_claim_handoff_v1",
            "status": "PASS",
            "result_authority": "R1_CANONICAL_INDEX",
            "claims": claims,
        }
        self.handoff_path = self.root / "reentry" / "claim_handoff.json"
        self.handoff_path.write_text(json.dumps(handoff, indent=2), encoding="utf-8")

    def _write_reentry(self, **overrides: object) -> None:
        self.paper_path = self.root / "reentry" / "paper.tex"
        self.paper_path.write_text(
            "\\section{C-01}\\label{C-01}\n"
            "% CLAIM_START:C-01\n"
            "eta = 0.17\n"
            "% CLAIM_END:C-01\n",
            encoding="utf-8",
        )
        self._write_handoff()
        paper_hash = hashlib.sha256(self.paper_path.read_bytes()).hexdigest()
        payload: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "status": "READY",
            "paper_source": {"path": "paper.tex", "sha256": paper_hash},
            "result_authority": {
                "source": "R1_CANONICAL_INDEX",
                "index_path": "authoritative_result_index.json",
                "case_dir": "cases/case_r4a",
                "references": [{"question_id": "Q4", "artifact_role": "primary_result"}],
            },
            "claim_handoff_path": "claim_handoff.json",
            "required_claim_ids": ["C-01"],
        }
        payload.update(overrides)
        payload["contract_digest"] = compute_contract_digest(payload)
        self.reentry_path = self.root / "reentry" / "post_writer_reentry.json"
        self.reentry_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _write_r3_fixture(self) -> None:
        """Create the smallest explicit R3 approval/artifact pair for the
        finalizer positive control.

        This is test-only fixture wiring: it reuses the production R3
        preparation helper and does not create approval or trust inputs in
        production code.
        """
        identity = "cases/case_r4a"
        run_id = "run_r4a"
        items = [
            {
                "constraint_id": "R3-R4A-C1",
                "constraint_type": "CLAIM_REFERENCE",
                "authority_reference": {"authority_type": "R2A_CLAIM", "claim_id": "C-01"},
                "target_reference": {
                    "target_type": "CLAIM_ANCHOR",
                    "claim_id": "C-01",
                    "anchor_id": "paper:results:C-01",
                },
                "required_action": "PRESERVE_REQUIRED_INCLUSION",
                "review_mode": "DETERMINISTIC",
                "enforcement_reference": {
                    "enforcement_type": "R4A_CLAIM_ANCHOR",
                    "claim_id": "C-01",
                    "anchor_id": "paper:results:C-01",
                },
                "source_evidence_references": ["reentry/claim_handoff.json"],
            },
            {
                "constraint_id": "R3-R4B-C1",
                "constraint_type": "CLAIM_REFERENCE",
                "authority_reference": {
                    "authority_type": "R1_CANONICAL_INDEX",
                    "authority_scope_key": ["question_id", "artifact_role"],
                    "question_id": "Q4",
                    "artifact_role": "primary_result",
                    "run_id": run_id,
                    "result_schema_id": "x5_f0_canonical_result_schema_v1",
                },
                "target_reference": {
                    "target_type": "CLAIM_ANCHOR",
                    "claim_id": "C-01",
                    "anchor_id": "paper:results:C-01",
                },
                "required_action": "PRESERVE_AUTHORITY_REFERENCE",
                "review_mode": "DETERMINISTIC",
                "enforcement_reference": {
                    "enforcement_type": "R4B_STRUCTURED_CONSTRAINT",
                    "claim_id": "C-01",
                    "constraint_type": "LOWER_BOUND",
                    "symbol": "eta",
                },
                "source_evidence_references": ["reentry/authoritative_result_index.json"],
            },
        ]
        source = self.root / "reentry" / "r3_review_constraints_draft.json"
        source.write_text(
            json.dumps(
                {
                    "schema_version": "s0_r3_review_constraints_draft_v1",
                    "status": "DRAFT_NOT_AUTHORITY",
                    "case_identity": identity,
                    "run_id": run_id,
                    "items": items,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        canonical_hash = hashlib.sha256(canonicalize_approved_constraint_payload(items)).hexdigest()
        self.r3_approval_input_path = self.root / "reentry" / "r3_approval_input.json"
        self.r3_approval_input_path.write_text(
            json.dumps(
                {
                    "schema_version": "s0_r3_approval_input_v1",
                    "case_identity": identity,
                    "run_id": run_id,
                    "approved_constraint_set_sha256": canonical_hash,
                    "approval_reference": "test_only_explicit_r3_fixture",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        prepared = prepare_review_constraints_artifact(
            approved_source_path=source,
            approval_input_path=self.r3_approval_input_path,
            case_identity=identity,
            run_id=run_id,
        )
        self.review_constraints_path = self.root / "reentry" / "review_constraints.json"
        self.review_constraints_path.write_text(json.dumps(prepared["artifact"], indent=2), encoding="utf-8")
        structured = {
            "schema_version": R4B_SCHEMA_VERSION,
            "paper_source": {
                "path": "paper.tex",
                "sha256": hashlib.sha256(self.paper_path.read_bytes()).hexdigest(),
            },
            "claim_handoff_path": "claim_handoff.json",
            "result_authority": {
                "index_path": "authoritative_result_index.json",
                "case_dir": identity,
            },
        }
        self.structured_reentry_path = self.root / "reentry" / "structured_semantic_reentry.json"
        self.structured_reentry_path.write_text(json.dumps(structured, indent=2), encoding="utf-8")

    def _write_trusted_manifest(self) -> None:
        payload = {
            "schema_version": "x5_f0_candidate_manifest_v1",
            "status": "candidate",
            "package_id": "candidate_r4a",
            "case_dir": "cases/case_r4a",
            "case_identity": "cases/case_r4a",
            "run_id": "run_r4a",
            "authoritative_result_index": {
                "path": "authoritative_result_index.json",
                "sha256": hashlib.sha256(self.index_path.read_bytes()).hexdigest(),
            },
            "claim_handoff": {
                "path": "claim_handoff.json",
                "sha256": hashlib.sha256(self.handoff_path.read_bytes()).hexdigest(),
            },
            "review_constraints": {
                "path": "review_constraints.json",
                "sha256": hashlib.sha256(self.review_constraints_path.read_bytes()).hexdigest(),
                "schema_version": ARTIFACT_SCHEMA_VERSION,
            },
        }
        self.manifest_path = self.root / "reentry" / "manifest.json"
        self.manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load(self) -> dict[str, object]:
        return json.loads(self.reentry_path.read_text(encoding="utf-8"))

    def _save(self, payload: dict[str, object], *, resign: bool = True) -> None:
        if resign:
            payload["contract_digest"] = compute_contract_digest(payload)
        self.reentry_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def test_positive_control_consumed_by_final_freezer(self) -> None:
        result = validate_post_writer_reentry(self.reentry_path, repo_root=self.root)
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["fallback"]["FALLBACK_USED"])
        self.assertFalse(result["fallback"]["LATEST_FILE_SELECTED"])
        self.assertFalse(result["fallback"]["RAW_RESULT_SELECTED"])
        self.assertFalse(result["fallback"]["ANCHOR_GUESSED"])
        self.assertFalse(result["fallback"]["HASH_AUTO_UPDATED"])
        freeze = check_freeze_ready(
            self.case,
            repo_root=self.root,
            post_writer_reentry_path=self.reentry_path,
            candidate_manifest_path=self.manifest_path,
            expected_candidate_manifest_sha256=hashlib.sha256(self.manifest_path.read_bytes()).hexdigest(),
            expected_run_id="run_r4a",
            require_post_writer_reentry=True,
            structured_semantic_reentry_path=self.structured_reentry_path,
            require_structured_semantic_reentry=True,
            r3_approval_input_path=self.r3_approval_input_path,
        )
        self.assertTrue(freeze["post_writer_reentry_passed"])
        self.assertTrue(freeze["downstream_gate_reached"])
        self.assertFalse(freeze["final_freeze_ready"])

    def test_negative_a_paper_hash_blocks_without_fallback(self) -> None:
        self.paper_path.write_text("tampered paper", encoding="utf-8")
        result = validate_post_writer_reentry(self.reentry_path, repo_root=self.root)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("POST_WRITER_PAPER_HASH_MISMATCH", result["blockers"])
        self.assertFalse(result["fallback"]["DOWNSTREAM_FINALIZATION_ALLOWED"])

    def test_negative_b_missing_authority_blocks(self) -> None:
        payload = self._load()
        payload["result_authority"]["references"] = [{"question_id": "Q9", "artifact_role": "primary_result"}]
        self._save(payload)
        result = validate_post_writer_reentry(self.reentry_path, repo_root=self.root)
        self.assertIn("CANONICAL_AUTHORITY_MISSING:Q9:primary_result", result["blockers"])
        self.assertEqual(result["status"], "BLOCKED")

    def test_negative_c_wrong_role_blocks(self) -> None:
        payload = self._load()
        payload["result_authority"]["references"] = [{
            "question_id": "Q4", "artifact_role": "primary_result", "resolved_artifact_role": "validation_evidence"
        }]
        self._save(payload)
        result = validate_post_writer_reentry(self.reentry_path, repo_root=self.root)
        self.assertIn("CANONICAL_ROLE_MISMATCH:Q4:primary_result", result["blockers"])

    def test_negative_d_missing_primary_anchor_blocks(self) -> None:
        payload = self._load()
        handoff = json.loads(self.handoff_path.read_text(encoding="utf-8"))
        handoff["claims"][0]["traceability"].pop("primary_claim_anchor")
        self.handoff_path.write_text(json.dumps(handoff), encoding="utf-8")
        self._save(payload)
        result = validate_post_writer_reentry(self.reentry_path, repo_root=self.root)
        self.assertIn("POST_WRITER_PRIMARY_ANCHOR_MISSING:C-01", result["blockers"])

    def test_negative_e_anchor_mismatch_blocks(self) -> None:
        payload = self._load()
        handoff = json.loads(self.handoff_path.read_text(encoding="utf-8"))
        handoff["claims"][0]["traceability"]["primary_claim_anchor"]["claim_id"] = "C-02"
        self.handoff_path.write_text(json.dumps(handoff), encoding="utf-8")
        self._save(payload)
        result = validate_post_writer_reentry(self.reentry_path, repo_root=self.root)
        self.assertIn("POST_WRITER_CLAIM_ANCHOR_MISMATCH:C-01", result["blockers"])

    def test_negative_f_duplicate_primary_anchor_blocks(self) -> None:
        duplicate = {
            "claim_id": "C-02",
            "claim_check_status": "PASS",
            "traceability": {
                "primary_claim_anchor": {
                    "anchor_id": "paper:results:C-01",
                    "claim_id": "C-02",
                    "role": "PRIMARY_CLAIM_ANCHOR",
                    "reference": "paper.tex#C-01",
                },
                "secondary_references": [],
            },
        }
        self._write_handoff(json.loads(self.handoff_path.read_text())["claims"] + [duplicate])
        payload = self._load()
        self._save(payload)
        result = validate_post_writer_reentry(self.reentry_path, repo_root=self.root)
        self.assertIn("POST_WRITER_DUPLICATE_PRIMARY_ANCHOR:paper:results:C-01", result["blockers"])

    def test_positive_secondary_reference_is_allowed(self) -> None:
        handoff = json.loads(self.handoff_path.read_text(encoding="utf-8"))
        handoff["claims"][0]["traceability"]["secondary_references"] = [{"reference": "paper.tex#abstract-C-01"}]
        self.handoff_path.write_text(json.dumps(handoff), encoding="utf-8")
        result = validate_post_writer_reentry(self.reentry_path, repo_root=self.root)
        self.assertEqual(result["status"], "PASS")

    def test_tampered_sidecar_digest_blocks(self) -> None:
        payload = self._load()
        payload["status"] = "TAMPERED"
        self._save(payload, resign=False)
        result = validate_post_writer_reentry(self.reentry_path, repo_root=self.root)
        self.assertIn("POST_WRITER_REENTRY_STATUS_INVALID", result["blockers"])
        self.assertIn("POST_WRITER_REENTRY_DIGEST_MISMATCH", result["blockers"])

    def test_missing_sidecar_cannot_silently_bypass(self) -> None:
        result = check_freeze_ready(
            self.case,
            repo_root=self.root,
            candidate_manifest_path=self.manifest_path,
            expected_candidate_manifest_sha256=hashlib.sha256(self.manifest_path.read_bytes()).hexdigest(),
            expected_run_id="run_r4a",
            require_post_writer_reentry=True,
        )
        self.assertIn("POST_WRITER_REENTRY_REQUIRED", result["blockers"])
        self.assertFalse(result["post_writer_reentry_consumed"])
        self.assertFalse(result["final_freeze_ready"])


if __name__ == "__main__":
    unittest.main()
