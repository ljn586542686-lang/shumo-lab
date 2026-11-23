from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from package_candidate import CandidatePromotionClaimCheckBlocked, package_candidate


class StructuredClaimHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        self.case = self.root / "cases" / "case_r2a"
        self.case.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_claim(self, claim_id: str = "C-01", *, anchor_id: str = "paper:results:C-01", **overrides: object) -> None:
        claim = {
            "claim_id": claim_id,
            "claim_type": "model_selection",
            "is_core_conclusion": True,
            "generator_role_id": "writer",
            "adversarial_role_id": "reviewer",
            "claim_type_provider_role_id": "reviewer",
            "claim_card_path": f"output/validation/claim_checks/{claim_id}.json",
            "proposed_wording_path": f"paper/{claim_id}.txt",
            "approved_constraints": [],
            "primary_claim_anchor": {
                "anchor_id": anchor_id,
                "claim_id": claim_id,
                "role": "PRIMARY_CLAIM_ANCHOR",
                "reference": f"paper/results#{claim_id}",
            },
        }
        claim.update(overrides)
        cfg = {
            "submission_tier": "training",
            "claim_check_required": True,
            "structured_claim_handoff_required": True,
            "r3_required": False,
            "core_claims": [claim],
        }
        (self.case / "case_config.json").write_text(json.dumps(cfg), encoding="utf-8")
        card_path = self.case / claim["claim_card_path"]
        card_path.parent.mkdir(parents=True, exist_ok=True)
        card = {
            "claim_id": claim_id,
            "status": "PASS",
            "human_decision": "A_ACCEPT",
            "recommended_decision": "A_ACCEPT",
            "allowed_claim_strength": "model_specific_candidate",
            "approved_strength": "model_specific_candidate",
            "approved_wording": "The selected model is supported by the recorded evidence.",
            "evidence_scope": "executed candidate comparison scope",
            "limitations": ["not a global optimum claim"],
            "source_evidence_references": ["output/result_summary.json"],
            "red_flag_count": 0,
            "red_flag_ids": [],
        }
        card_path.write_text(json.dumps(card), encoding="utf-8")
        (self.case / claim["proposed_wording_path"]).parent.mkdir(parents=True, exist_ok=True)
        (self.case / claim["proposed_wording_path"]).write_text(card["approved_wording"], encoding="utf-8")
        output = self.case / "output"
        output.mkdir(exist_ok=True)
        (output / "result_summary.json").write_text(json.dumps({"question_id": "Q1", "status": "PASS"}), encoding="utf-8")

    def test_positive_control_creates_handoff_artifact(self) -> None:
        self._write_claim()
        result = package_candidate(case_dir=self.case, root=self.root, run_id="run_r2a_positive")
        package = self.root / result["package_dir"]
        handoff = json.loads((package / "claim_handoff.json").read_text(encoding="utf-8"))
        self.assertEqual(handoff["status"], "PASS")
        self.assertTrue(handoff["lock_claims_not_prose"])
        self.assertEqual(handoff["result_authority"], "R1_CANONICAL_INDEX")
        self.assertEqual(len(handoff["claims"]), 1)
        self.assertEqual(result["manifest"]["claim_handoff"]["path"], "claim_handoff.json")

    def test_legacy_mode_is_explicitly_marked(self) -> None:
        self._write_claim()
        cfg = json.loads((self.case / "case_config.json").read_text(encoding="utf-8"))
        cfg.pop("structured_claim_handoff_required")
        (self.case / "case_config.json").write_text(json.dumps(cfg), encoding="utf-8")
        result = package_candidate(case_dir=self.case, root=self.root, run_id="run_r2a_legacy")
        marker = result["manifest"]["claim_handoff"]
        self.assertIsNone(marker["path"])
        self.assertEqual(marker["status"], "LEGACY_CLAIM_HANDOFF_NOT_REQUIRED")
        self.assertTrue(marker["legacy_claim_handoff"])

    def _assert_blocked(self, mutate_card=None, mutate_config=None) -> dict[str, object]:
        self._write_claim()
        card_path = self.case / "output/validation/claim_checks/C-01.json"
        if mutate_card:
            card = json.loads(card_path.read_text(encoding="utf-8"))
            mutate_card(card)
            card_path.write_text(json.dumps(card), encoding="utf-8")
        if mutate_config:
            cfg = json.loads((self.case / "case_config.json").read_text(encoding="utf-8"))
            mutate_config(cfg)
            (self.case / "case_config.json").write_text(json.dumps(cfg), encoding="utf-8")
        with self.assertRaises(CandidatePromotionClaimCheckBlocked) as caught:
            package_candidate(case_dir=self.case, root=self.root, run_id="run_r2a_negative")
        self.assertFalse((self.root / "candidate_submit").exists())
        return caught.exception.result

    def test_negative_missing_required_field_blocks(self) -> None:
        result = self._assert_blocked(mutate_card=lambda card: card.pop("evidence_scope"))
        self.assertIn("CLAIM_HANDOFF_REQUIRED_FIELD_MISSING", result["underlying_codes"])

    def test_negative_unapproved_wording_blocks(self) -> None:
        self._write_claim()
        (self.case / "paper/C-01.txt").write_text("A different approved claim wording.", encoding="utf-8")
        # Re-run the gate on the same case after the wording substitution.
        with self.assertRaises(CandidatePromotionClaimCheckBlocked) as caught:
            package_candidate(case_dir=self.case, root=self.root, run_id="run_r2a_wording")
        self.assertIn("CLAIM_HANDOFF_APPROVED_WORDING_MISMATCH", caught.exception.result["underlying_codes"])

    def test_negative_claim_anchor_mismatch_blocks(self) -> None:
        result = self._assert_blocked(mutate_config=lambda cfg: cfg["core_claims"][0]["primary_claim_anchor"].update({"claim_id": "OTHER"}))
        self.assertIn("CLAIM_HANDOFF_CLAIM_ANCHOR_MISMATCH", result["underlying_codes"])

    def test_negative_duplicate_anchor_blocks(self) -> None:
        self._write_claim()
        cfg = json.loads((self.case / "case_config.json").read_text(encoding="utf-8"))
        second = dict(cfg["core_claims"][0])
        second["claim_id"] = "C-02"
        second["claim_card_path"] = "output/validation/claim_checks/C-02.json"
        second["proposed_wording_path"] = "paper/C-02.txt"
        second["primary_claim_anchor"] = dict(second["primary_claim_anchor"])
        second["primary_claim_anchor"]["claim_id"] = "C-02"
        cfg["core_claims"].append(second)
        (self.case / "case_config.json").write_text(json.dumps(cfg), encoding="utf-8")
        card = json.loads((self.case / "output/validation/claim_checks/C-01.json").read_text(encoding="utf-8"))
        card["claim_id"] = "C-02"
        (self.case / "output/validation/claim_checks/C-02.json").write_text(json.dumps(card), encoding="utf-8")
        (self.case / "paper/C-02.txt").write_text(card["approved_wording"], encoding="utf-8")
        with self.assertRaises(CandidatePromotionClaimCheckBlocked) as caught:
            package_candidate(case_dir=self.case, root=self.root, run_id="run_r2a_duplicate")
        self.assertIn("CLAIM_HANDOFF_DUPLICATE_PRIMARY_ANCHOR", caught.exception.result["underlying_codes"])


if __name__ == "__main__":
    unittest.main()
