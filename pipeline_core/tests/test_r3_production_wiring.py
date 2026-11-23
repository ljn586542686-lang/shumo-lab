from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline_core.python_tools.r3_approval_interface import canonicalize_approved_constraint_payload
from pipeline_core.python_tools.r3_validator import (
    ARTIFACT_SCHEMA_VERSION,
    prepare_review_constraints_artifact,
    validate_review_constraints_reentry,
)


ROOT = Path(__file__).parents[2]


def _draft_fixture() -> dict:
    """Return the minimal self-contained R3 draft used by this test module."""

    return {
        "schema_version": "s0_r3_review_constraints_draft_v1",
        "status": "DRAFT_NOT_AUTHORITY",
        "case_identity": "replaced-by-seed",
        "run_id": "run_r3_fixture",
        "items": [
            {
                "constraint_id": "R3-R4A-C1",
                "constraint_type": "CLAIM_REFERENCE",
                "authority_reference": {
                    "authority_type": "R2A_CLAIM",
                    "claim_id": "C-1",
                },
                "target_reference": {
                    "claim_id": "C-1",
                    "anchor_id": "paper:claim-c1",
                },
                "required_action": "PRESERVE_REQUIRED_INCLUSION",
                "review_mode": "DETERMINISTIC",
                "enforcement_reference": {
                    "enforcement_type": "R4A_CLAIM_ANCHOR",
                    "claim_id": "C-1",
                },
                "source_evidence_references": ["fixture:r4a"],
            },
            {
                "constraint_id": "R3-R4B-C1",
                "constraint_type": "RESULT_REFERENCE",
                "authority_reference": {
                    "authority_type": "R1_CANONICAL_INDEX",
                    "question_id": "Q1",
                    "artifact_role": "result_summary",
                    "run_id": "run_r3_fixture",
                    "result_schema_id": "fixture_result_v1",
                },
                "target_reference": {
                    "claim_id": "C-1",
                    "anchor_id": "paper:result-c1",
                },
                "required_action": "PRESERVE_AUTHORITY_REFERENCE",
                "review_mode": "DETERMINISTIC",
                "enforcement_reference": {
                    "enforcement_type": "R4B_STRUCTURED_CONSTRAINT",
                    "claim_id": "C-1",
                    "constraint_type": "LOWER_BOUND",
                    "symbol": "eta",
                },
                "source_evidence_references": ["fixture:r4b"],
            },
        ],
    }


def _seed(root: Path, *, r3_required: object = True) -> tuple[Path, Path, dict]:
    case = root / "cases/system_functional_proof/s0_r4b10_mandatory_wiring_remediation_2026-08-09/production_fixture/case"
    case.mkdir(parents=True)
    draft = _draft_fixture()
    identity = case.relative_to(root).as_posix()
    draft["case_identity"] = identity
    draft_path = root / "draft.json"
    draft_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    payload_hash = hashlib.sha256(canonicalize_approved_constraint_payload(draft["items"])).hexdigest()
    approval = {
        "schema_version": "s0_r3_approval_input_v1",
        "case_identity": identity,
        "run_id": draft["run_id"],
        "approved_constraint_set_sha256": payload_hash,
        "approval_reference": "human_test_approval",
    }
    approval_path = root / "approval.json"
    approval_path.write_text(json.dumps(approval, indent=2), encoding="utf-8")
    (case / "case_config.json").write_text(json.dumps({"r3_required": r3_required}), encoding="utf-8")
    return case, draft_path, {"approval": approval, "identity": identity, "run_id": draft["run_id"], "items": draft["items"]}


def _write_package(case: Path, root: Path, meta: dict, *, mutate: dict | None = None) -> Path:
    package = root / "candidate"; package.mkdir()
    artifact = {"schema_version": ARTIFACT_SCHEMA_VERSION, "case_identity": meta["identity"], "run_id": meta["run_id"], "items": json.loads(json.dumps(meta["items"]))}
    if mutate:
        mutate(artifact)
    artifact_path = package / "review_constraints.json"
    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "schema_version": "x5_f0_candidate_manifest_v1",
        "case_dir": meta["identity"], "case_identity": meta["identity"], "run_id": meta["run_id"],
        "review_constraints": {"path": "review_constraints.json", "sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(), "schema_version": ARTIFACT_SCHEMA_VERSION},
    }
    manifest_path = package / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


class R3ProductionWiringTests(unittest.TestCase):
    def _validate(self, root: Path, case: Path, meta: dict, manifest: Path, approval: Path | None = None, *, r4b: dict | None = None, handoff: dict | None = None) -> dict:
        return validate_review_constraints_reentry(
            case_dir=case,
            manifest_path=manifest,
            approval_input_path=approval or (root / "approval.json"),
            current_r4a_verdict={"status": "PASS", "required_claim_ids": ["C-1"]},
            current_r4b_verdict=r4b or {"status": "SEMANTIC_PASS", "evidence": [{"claim_id": "C-1", "constraint": "LOWER_BOUND"}]},
            current_claim_handoff=handoff or {"claims": [{"claim_id": "C-1", "approved_constraints": [{"constraint_type": "LOWER_BOUND", "symbol": "eta", "value": 0}]}]},
            repo_root=root,
        )

    def test_positive_read_once_and_current_verdicts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); case, draft, meta = _seed(root)
            prepared = prepare_review_constraints_artifact(approved_source_path=draft, approval_input_path=root / "approval.json", case_identity=meta["identity"], run_id=meta["run_id"])
            self.assertEqual(prepared["source_read_count"], 1)
            self.assertEqual(prepared["approved_input_read_count"], 1)
            manifest = _write_package(case, root, meta)
            result = validate_review_constraints_reentry(case_dir=case, manifest_path=manifest, approval_input_path=root / "approval.json", current_r4a_verdict={"status": "PASS", "required_claim_ids": ["C-1"]}, current_r4b_verdict={"status": "SEMANTIC_PASS", "evidence": [{"claim_id": "C-1", "constraint": "LOWER_BOUND"}]}, current_claim_handoff={"claims": [{"claim_id": "C-1", "approved_constraints": [{"constraint_type": "LOWER_BOUND", "symbol": "eta", "value": 0}]}]}, repo_root=root)
            self.assertEqual(result["status"], "R3_PASS")
            self.assertTrue(result["approved_input_read_once"])
            self.assertTrue(result["current_verdict_consumed"])
            self.assertFalse(result["historical_verdict_fallback"])

    def test_missing_hash_case_and_manifest_mutations_block(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); case, draft, meta = _seed(root)
            manifest = _write_package(case, root, meta, mutate=lambda a: a["items"][0]["target_reference"].update({"anchor_id": "paper:changed"}))
            result = validate_review_constraints_reentry(case_dir=case, manifest_path=manifest, approval_input_path=root / "approval.json", current_r4a_verdict={"status": "PASS", "required_claim_ids": ["C-1"]}, current_r4b_verdict={"status": "SEMANTIC_PASS", "evidence": [{"claim_id": "C-1", "constraint": "LOWER_BOUND"}]}, current_claim_handoff={"claims": [{"claim_id": "C-1"}]}, repo_root=root)
            self.assertEqual(result["status"], "R3_BLOCK")
            self.assertIn("R3_APPROVAL_HASH_MISMATCH", result["blockers"])

    def test_manual_review_is_review_required(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); case, draft, meta = _seed(root)
            meta["items"][0]["review_mode"] = "MANUAL_REVIEW_REQUIRED"
            approval = meta["approval"]
            approval["approved_constraint_set_sha256"] = hashlib.sha256(canonicalize_approved_constraint_payload(meta["items"])).hexdigest()
            (root / "approval.json").write_text(json.dumps(approval, indent=2), encoding="utf-8")
            manifest = _write_package(case, root, meta)
            result = validate_review_constraints_reentry(case_dir=case, manifest_path=manifest, approval_input_path=root / "approval.json", current_r4a_verdict={"status": "PASS"}, current_r4b_verdict={"status": "SEMANTIC_PASS", "evidence": []}, repo_root=root)
            self.assertEqual(result["status"], "REVIEW_REQUIRED")

    def test_missing_or_invalid_required_flag_blocks_explicit_validator(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); case, _draft, _meta = _seed(root, r3_required="true")
            result = validate_review_constraints_reentry(case_dir=case, manifest_path=None, approval_input_path=None, current_r4a_verdict=None, current_r4b_verdict=None, repo_root=root)
            self.assertEqual(result["status"], "R3_BLOCK")
            self.assertIn("R3_REQUIRED_FLAG_INVALID", result["blockers"])

    def test_f01_missing_artifact_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); case, _draft, meta = _seed(root)
            package = root / "candidate"; package.mkdir()
            manifest = {"case_dir": meta["identity"], "case_identity": meta["identity"], "run_id": meta["run_id"], "review_constraints": {"path": "review_constraints.json", "sha256": "0" * 64, "schema_version": ARTIFACT_SCHEMA_VERSION}}
            path = package / "manifest.json"; path.write_text(json.dumps(manifest), encoding="utf-8")
            result = self._validate(root, case, meta, path)
            self.assertEqual(result["status"], "R3_BLOCK")
            self.assertIn("R3_ARTIFACT_HASH_MISMATCH", result["blockers"])

    def test_f02_manifest_hash_mismatch_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); case, _draft, meta = _seed(root); manifest = _write_package(case, root, meta)
            data = json.loads(manifest.read_text()); data["review_constraints"]["sha256"] = "f" * 64; manifest.write_text(json.dumps(data), encoding="utf-8")
            result = self._validate(root, case, meta, manifest)
            self.assertIn("R3_ARTIFACT_HASH_MISMATCH", result["blockers"])

    def test_f03_wrong_case_or_run_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); case, _draft, meta = _seed(root); manifest = _write_package(case, root, meta)
            data = json.loads(manifest.read_text()); data["run_id"] = "other-run"; manifest.write_text(json.dumps(data), encoding="utf-8")
            result = self._validate(root, case, meta, manifest)
            self.assertIn("R3_ARTIFACT_CASE_RUN_MISMATCH", result["blockers"])

    def test_f04_approval_hash_mismatch_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); case, _draft, meta = _seed(root); manifest = _write_package(case, root, meta)
            approval = json.loads((root / "approval.json").read_text()); approval["approved_constraint_set_sha256"] = "a" * 64; approval_path = root / "bad_approval.json"; approval_path.write_text(json.dumps(approval), encoding="utf-8")
            result = self._validate(root, case, meta, manifest, approval_path)
            self.assertIn("R3_APPROVAL_HASH_MISMATCH", result["blockers"])

    def test_f05_authority_cannot_be_enforcement(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); case, _draft, meta = _seed(root); meta["items"][0]["enforcement_reference"]["enforcement_type"] = "R1_CANONICAL_INDEX"
            manifest = _write_package(case, root, meta); result = self._validate(root, case, meta, manifest)
            self.assertTrue(any("R3_ENFORCEMENT_TYPE_INVALID" in x or "R3_AUTHORITY_USED_AS_ENFORCEMENT" in x for x in result["blockers"]))

    def test_f06_fake_r4b_identity_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); case, _draft, meta = _seed(root); manifest = _write_package(case, root, meta)
            result = self._validate(root, case, meta, manifest, r4b={"status": "SEMANTIC_PASS", "evidence": [{"claim_id": "fake", "constraint": "LOWER_BOUND"}]})
            self.assertIn("R3_R4B_CONSTRAINT_ID_NOT_CURRENT:R3-R4B-C1", result["blockers"])

    def test_f07_cross_claim_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); case, _draft, meta = _seed(root); meta["items"][1]["enforcement_reference"]["claim_id"] = "C-2"
            approval = meta["approval"]; approval["approved_constraint_set_sha256"] = hashlib.sha256(canonicalize_approved_constraint_payload(meta["items"])).hexdigest(); (root / "approval.json").write_text(json.dumps(approval), encoding="utf-8")
            manifest = _write_package(case, root, meta); result = self._validate(root, case, meta, manifest)
            self.assertIn("R3_R4B_CONSTRAINT_IDENTITY_NOT_CURRENT:R3-R4B-C1", result["blockers"])

    def test_f08_unknown_action_and_f09_unknown_mode_block(self) -> None:
        for field, value, token in (("required_action", "UNKNOWN_ACTION", "R3_REQUIRED_ACTION_INVALID"), ("review_mode", "UNKNOWN_MODE", "R3_REVIEW_MODE_INVALID")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as td:
                root = Path(td); case, _draft, meta = _seed(root); meta["items"][0][field] = value
                manifest = _write_package(case, root, meta); result = self._validate(root, case, meta, manifest)
                self.assertTrue(any(token in x for x in result["blockers"]))

    def test_f11_manifest_replacement_without_pointer_update_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); case, _draft, meta = _seed(root); manifest = _write_package(case, root, meta)
            artifact = manifest.parent / "review_constraints.json"; data = json.loads(artifact.read_text()); data["items"][0]["target_reference"]["anchor_id"] = "paper:replacement"; artifact.write_text(json.dumps(data), encoding="utf-8")
            result = self._validate(root, case, meta, manifest)
            self.assertIn("R3_ARTIFACT_HASH_MISMATCH", result["blockers"])

    def test_f12_post_approval_draft_mutation_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); case, draft, meta = _seed(root)
            changed = json.loads(draft.read_text()); changed["items"][0]["required_action"] = "PRESERVE_AUTHORITY_REFERENCE"; draft.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(Exception, "R3_APPROVAL_HASH_MISMATCH"):
                prepare_review_constraints_artifact(approved_source_path=draft, approval_input_path=root / "approval.json", case_identity=meta["identity"], run_id=meta["run_id"])

    def test_f13_missing_approval_path_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); case, _draft, meta = _seed(root); manifest = _write_package(case, root, meta)
            result = validate_review_constraints_reentry(case_dir=case, manifest_path=manifest, approval_input_path=None, current_r4a_verdict={"status": "PASS"}, current_r4b_verdict={"status": "SEMANTIC_PASS"}, repo_root=root)
            self.assertIn("R3_APPROVAL_INPUT_REQUIRED", result["blockers"])

    def test_f14_cross_symbol_constraint_identity_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); case, _draft, meta = _seed(root); manifest = _write_package(case, root, meta)
            result = self._validate(root, case, meta, manifest, handoff={"claims": [{"claim_id": "C-1", "approved_constraints": [{"constraint_type": "LOWER_BOUND", "symbol": "theta", "value": 0}]}]})
            self.assertEqual(result["status"], "R3_BLOCK")
            self.assertIn("R3_R4B_CONSTRAINT_IDENTITY_NOT_CURRENT:R3-R4B-C1", result["blockers"])

    def test_f15_same_symbol_constraint_identity_passes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); case, _draft, meta = _seed(root); manifest = _write_package(case, root, meta)
            result = self._validate(root, case, meta, manifest)
            self.assertEqual(result["status"], "R3_PASS")

    def test_f16_ambiguous_constraint_identity_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); case, _draft, meta = _seed(root); manifest = _write_package(case, root, meta)
            handoff = {"claims": [{"claim_id": "C-1", "approved_constraints": [{"constraint_type": "LOWER_BOUND", "symbol": "eta", "value": 0}, {"constraint_type": "LOWER_BOUND", "symbol": "eta", "value": 0}]}]}
            result = self._validate(root, case, meta, manifest, handoff=handoff)
            self.assertNotEqual(result["status"], "R3_PASS")
            self.assertIn("R3_R4B_CONSTRAINT_IDENTITY_AMBIGUOUS:R3-R4B-C1", result["blockers"])

    def test_f17_cross_symbol_causal_pair(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); case, _draft, meta = _seed(root); manifest = _write_package(case, root, meta)
            positive = self._validate(root, case, meta, manifest, handoff={"claims": [{"claim_id": "C-1", "approved_constraints": [{"constraint_type": "LOWER_BOUND", "symbol": "eta", "value": 0}]}]})
            negative = self._validate(root, case, meta, manifest, handoff={"claims": [{"claim_id": "C-1", "approved_constraints": [{"constraint_type": "LOWER_BOUND", "symbol": "theta", "value": 0}]}]})
            self.assertEqual(positive["status"], "R3_PASS")
            self.assertEqual(negative["status"], "R3_BLOCK")

    def test_f18_missing_formal_r4b_symbol_blocks_closed_schema(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); case, _draft, meta = _seed(root)
            meta["items"][1]["enforcement_reference"].pop("symbol", None)
            meta["approval"]["approved_constraint_set_sha256"] = hashlib.sha256(canonicalize_approved_constraint_payload(meta["items"])).hexdigest()
            (root / "approval.json").write_text(json.dumps(meta["approval"], indent=2), encoding="utf-8")
            manifest = _write_package(case, root, meta)
            result = self._validate(root, case, meta, manifest)
            self.assertIn("R3_R4B_CONSTRAINT_SYMBOL_MISSING", result["blockers"])

    def test_package_candidate_writes_manifest_bound_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); case, draft, meta = _seed(root)
            (case / "output").mkdir()
            from package_candidate import package_candidate
            with patch("package_candidate._effective_candidate_settings", return_value={}), \
                 patch("package_candidate._enforce_upstream_status_candidate_guard"), \
                 patch("package_candidate._enforce_claim_check_candidate_gate", return_value={"structured_claim_handoff": None}), \
                 patch("package_candidate._enforce_human_revision_candidate_gate", return_value={}), \
                 patch("package_candidate._build_upstream_artifact_bindings", return_value=({}, [])), \
                 patch("package_candidate._verify_source_provenance", return_value={"source_provenance_required": False, "source_provenance_applicable": False, "source_provenance_finalization_passed": True}), \
                 patch("package_candidate._write_supporting_docs"), \
                 patch("package_candidate.register_asset"), \
                 patch("package_candidate.inventory_source_data", return_value={}):
                result = package_candidate(case_dir=case, root=root, run_id=meta["run_id"], r3_approved_source_path=draft, r3_approval_input_path=root / "approval.json")
            package = root / result["package_dir"]
            self.assertTrue((package / "review_constraints.json").exists())
            self.assertEqual(result["manifest"]["review_constraints"]["schema_version"], ARTIFACT_SCHEMA_VERSION)
            self.assertEqual(result["manifest"]["review_constraints"]["sha256"], hashlib.sha256((package / "review_constraints.json").read_bytes()).hexdigest())

    def test_finalizer_consumes_r3_after_r4_when_required(self) -> None:
        from pipeline_core.python_tools.final_freezer import check_freeze_ready
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); case = root / "cases" / "case"; case.mkdir(parents=True)
            (case / "case_config.json").write_text(json.dumps({"r3_required": True, "required_final_artifacts": []}), encoding="utf-8")
            result = check_freeze_ready(case, repo_root=root, candidate_manifest_path=None, r3_approval_input_path=None)
            self.assertEqual(result["r3_status"], "R3_BLOCK")
            self.assertIn("R3_MANIFEST_REQUIRED", result["r3_validation"]["blockers"])
            self.assertFalse(result["downstream_gate_reached"])

    def test_f01_package_missing_flag_blocks_before_package_creation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); case, _draft, meta = _seed(root)
            (case / "case_config.json").write_text(json.dumps({}), encoding="utf-8")
            from package_candidate import CandidatePromotionUpstreamBindingBlocked, package_candidate
            with patch("package_candidate._effective_candidate_settings", return_value={}), \
                 patch("package_candidate._enforce_upstream_status_candidate_guard"), \
                 patch("package_candidate._enforce_claim_check_candidate_gate", return_value={"structured_claim_handoff": None}), \
                 patch("package_candidate._enforce_human_revision_candidate_gate", return_value={}), \
                 patch("package_candidate._build_upstream_artifact_bindings", return_value=({}, [])), \
                 patch("package_candidate._verify_source_provenance", return_value={"source_provenance_required": False, "source_provenance_applicable": False, "source_provenance_finalization_passed": True}):
                with self.assertRaises(CandidatePromotionUpstreamBindingBlocked) as caught:
                    package_candidate(case_dir=case, root=root, run_id=meta["run_id"])
            self.assertEqual(caught.exception.result["code"], "R3_REQUIRED_FLAG_MISSING")
            self.assertFalse(caught.exception.result["candidate_package_created"])
            self.assertFalse((root / "candidate_submit").exists())

    def test_f03_finalizer_false_flag_is_legacy_manual_block(self) -> None:
        from pipeline_core.python_tools.final_freezer import check_freeze_ready
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); case, _draft, _meta = _seed(root, r3_required=False)
            result = check_freeze_ready(case, repo_root=root)
            self.assertEqual(result["r3_status"], "LEGACY_MANUAL_REVIEW_REQUIRED")
            self.assertFalse(result["downstream_gate_reached"])


if __name__ == "__main__":
    unittest.main()
