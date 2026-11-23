"""Permanent regression fixture for DEFECT-CLAIM-EN-01.

The historical English overclaim must fail through the production
``package_candidate`` path, while a bounded English claim and an unrelated
use of ``prove`` remain valid.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from package_candidate import CandidatePromotionClaimCheckBlocked, package_candidate
from pipeline_core.claim_check.enforce_claim_check import BLOCK_WORDING, check_wording_compatible


class EnglishLexicalClaimRegressionTests(unittest.TestCase):
    DEFECT_ID = "DEFECT-CLAIM-EN-01"
    HISTORICAL_WORDING = "This proves the global optimum."

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        self.case = self.root / "cases" / "english_lexical_fixture"
        self.case.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_fixture(self, wording: str) -> None:
        (self.case / "paper").mkdir(parents=True, exist_ok=True)
        (self.case / "paper/claim.txt").write_text(wording, encoding="utf-8")
        card_dir = self.case / "output/validation/claim_checks"
        card_dir.mkdir(parents=True, exist_ok=True)
        (card_dir / "EN-01.json").write_text(
            json.dumps(
                {
                    "claim_id": "EN-01",
                    "status": "PASS",
                    "human_decision": "A_ACCEPT",
                    "recommended_decision": "A_ACCEPT",
                    "allowed_claim_strength": "model_specific_candidate",
                    "red_flag_count": 0,
                    "red_flag_ids": [],
                }
            ),
            encoding="utf-8",
        )
        (self.case / "case_config.json").write_text(
            json.dumps(
                {
                    "submission_tier": "training",
                    "claim_check_required": True,
                    "core_claims": [
                        {
                            "claim_id": "EN-01",
                            "claim_type": "evaluation_claim",
                            "is_core_conclusion": True,
                            "generator_role_id": "writer",
                            "adversarial_role_id": "reviewer",
                            "claim_type_provider_role_id": "reviewer",
                            "claim_card_path": "output/validation/claim_checks/EN-01.json",
                            "proposed_wording_path": "paper/claim.txt",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def test_historical_defect_blocks_through_production_package_gate(self) -> None:
        self._write_fixture(self.HISTORICAL_WORDING)
        with self.assertRaises(CandidatePromotionClaimCheckBlocked) as caught:
            package_candidate(case_dir=self.case, root=self.root, run_id="run_english_defect")
        result = caught.exception.result
        self.assertIn("CLAIM_WORDING_EXCEEDS_ALLOWED_STRENGTH", result["underlying_codes"])
        self.assertFalse(result["candidate_package_created"])
        self.assertFalse((self.root / "candidate_submit").exists())

    def test_lexical_variants_and_case_normalization_block(self) -> None:
        for wording in (
            "This proves global optimality.",
            "The solution is globally optimal.",
            "THIS PROVES THE GLOBAL OPTIMUM.",
        ):
            with self.subTest(wording=wording):
                result = check_wording_compatible(wording, "model_specific_candidate")
                self.assertIsNotNone(result)
                self.assertEqual(result.verdict, BLOCK_WORDING)
                self.assertTrue(result.blocked_token)

    def test_legal_english_and_false_positive_controls_pass(self) -> None:
        legal = check_wording_compatible(
            "This bounded model-specific candidate is supported by the recorded evidence.",
            "model_specific_candidate",
        )
        unrelated = check_wording_compatible(
            "The experiment proves no change in the observed range.",
            "model_specific_candidate",
        )
        self.assertIsNone(legal)
        self.assertIsNone(unrelated)


if __name__ == "__main__":
    unittest.main()
