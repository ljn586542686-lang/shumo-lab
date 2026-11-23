from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from package_candidate import (
    CandidatePromotionClaimCheckBlocked,
    package_candidate,
)


class ClaimCheckCandidatePromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "root"
        self.case_dir = self.root / "cases" / "case_test"
        self.case_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_config(self, **overrides: object) -> None:
        config = {"claim_check_required": False, "core_claims": [], "r3_required": False}
        config.update(overrides)
        (self.case_dir / "case_config.json").write_text(json.dumps(config), encoding="utf-8")

    @staticmethod
    def _claim(**overrides: object) -> dict[str, object]:
        claim: dict[str, object] = {
            "claim_id": "C-01",
            "claim_type": "model_selection",
            "proposed_wording_path": "paper/main.tex",
            "is_core_conclusion": True,
            "generator_role_id": "writer_role",
            "adversarial_role_id": "independent_reviewer",
            "claim_type_provider_role_id": "independent_reviewer",
            "claim_card_path": "output/validation/claim_checks/C-01.json",
        }
        claim.update(overrides)
        return claim

    def _assert_blocked(self, expected_code: str) -> dict[str, object]:
        with self.assertRaises(CandidatePromotionClaimCheckBlocked) as caught:
            package_candidate(case_dir=self.case_dir, root=self.root)
        result = caught.exception.result
        self.assertEqual(result["code"], "CANDIDATE_PROMOTION_CLAIM_CHECK_BLOCKED")
        self.assertIn(expected_code, result["underlying_codes"])
        self.assertFalse(result["candidate_package_created"])
        self.assertFalse(result["final_submit_created"])
        self.assertFalse((self.root / "candidate_submit").exists())
        return result

    def test_skipped_allows_ordinary_candidate_export(self) -> None:
        self._write_config()
        result = package_candidate(case_dir=self.case_dir, root=self.root)
        self.assertEqual(result["manifest"]["status"], "candidate")
        self.assertTrue((self.root / result["package_dir"]).is_dir())
        self.assertFalse(result["manifest"]["final_submit_created"])

    def test_required_without_core_claims_blocks_before_output_creation(self) -> None:
        self._write_config(claim_check_required=True)
        self._assert_blocked("CLAIM_CHECK_REQUIRED_BUT_NO_CORE_CLAIMS")

    def test_missing_core_claim_card_blocks(self) -> None:
        self._write_config(core_claims=[self._claim()])
        self._assert_blocked("CLAIM_CARD_MISSING")

    def test_generator_role_self_fill_blocks(self) -> None:
        self._write_config(core_claims=[self._claim(claim_type_provider_role_id="writer_role")])
        self._assert_blocked("CORE_CLAIM_CLASSIFICATION_SELF_FILLED")

    def test_manual_review_required_blocks_without_override(self) -> None:
        self._write_config()
        preflight = {
            "status": "MANUAL_REVIEW_REQUIRED",
            "blockers": [],
            "warnings": ["CLAIM_CHECK_MANUAL_REVIEW_REQUIRED"],
            "manual_review_items": ["C-01: pending"],
            "final_submit_created": False,
        }
        with mock.patch("package_candidate.check_claim_check_preflight", return_value=preflight):
            self._assert_blocked("CLAIM_CHECK_MANUAL_REVIEW_REQUIRED")

    def test_pass_allows_candidate_export(self) -> None:
        self._write_config()
        preflight = {"status": "PASS", "blockers": [], "final_submit_created": False}
        with mock.patch("package_candidate.check_claim_check_preflight", return_value=preflight):
            result = package_candidate(case_dir=self.case_dir, root=self.root)
        self.assertTrue((self.root / result["package_dir"]).is_dir())
        self.assertFalse(result["manifest"]["final_submit_created"])

    def test_skipped_preflight_result_allows_candidate_export(self) -> None:
        self._write_config()
        preflight = {"status": "SKIPPED", "blockers": [], "final_submit_created": False}
        with mock.patch("package_candidate.check_claim_check_preflight", return_value=preflight):
            result = package_candidate(case_dir=self.case_dir, root=self.root)
        self.assertTrue((self.root / result["package_dir"]).is_dir())

    def test_blocked_preflight_does_not_call_directory_creation(self) -> None:
        self._write_config(claim_check_required=True)
        with mock.patch.object(Path, "mkdir") as mkdir:
            self._assert_blocked("CLAIM_CHECK_REQUIRED_BUT_NO_CORE_CLAIMS")
        mkdir.assert_not_called()

    def test_candidate_export_never_creates_final_submit(self) -> None:
        self._write_config()
        result = package_candidate(case_dir=self.case_dir, root=self.root)
        self.assertFalse((self.root / "final_submit").exists())
        self.assertFalse(result["manifest"]["final_submit_created"])


if __name__ == "__main__":
    unittest.main()
