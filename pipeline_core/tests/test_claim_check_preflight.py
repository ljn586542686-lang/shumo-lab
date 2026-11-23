from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipeline_core.python_tools.claim_check_preflight import (
    check_claim_check_preflight,
    write_claim_check_preflight_report,
)


class ClaimCheckPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.case_dir = Path(self.temp_dir.name) / "case"
        self.case_dir.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_config(self, **overrides: object) -> None:
        config = {"claim_check_required": False, "core_claims": []}
        config.update(overrides)
        (self.case_dir / "case_config.json").write_text(json.dumps(config), encoding="utf-8")

    def _claim(self, **overrides: object) -> dict[str, object]:
        claim: dict[str, object] = {
            "claim_id": "C-01",
            "claim_type": "model_selection",
            "source_section": "paper/results",
            "proposed_wording_path": "paper/main.tex",
            "is_core_conclusion": True,
            "generator_role_id": "writer_role",
            "adversarial_role_id": "independent_reviewer",
            "claim_type_provider_role_id": "independent_reviewer",
            "claim_card_path": "output/validation/claim_checks/C-01.json",
        }
        claim.update(overrides)
        return claim

    def _write_wording(self, text: str = "A candidate conclusion is supported by the reviewed record.") -> None:
        path = self.case_dir / "paper" / "main.tex"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _write_card(self, **overrides: object) -> None:
        payload = {
            "status": "AUTO_PASS",
            "human_decision": "A_ACCEPT_AS_CORE_CLAIM",
            "recommended_decision": "A_ACCEPT_AS_CORE_CLAIM",
            "allowed_claim_strength": "core_conclusion",
            "red_flag_count": 0,
            "red_flag_ids": [],
        }
        payload.update(overrides)
        path = self.case_dir / "output" / "validation" / "claim_checks" / "C-01.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_default_config_skips_without_execution(self) -> None:
        self._write_config()
        result = check_claim_check_preflight(self.case_dir)
        self.assertEqual(result["status"], "SKIPPED")
        self.assertEqual(result["blockers"], [])
        self.assertFalse(result["final_submit_created"])

    def test_required_without_core_claims_blocks(self) -> None:
        self._write_config(claim_check_required=True)
        result = check_claim_check_preflight(self.case_dir)
        self.assertIn("CLAIM_CHECK_REQUIRED_BUT_NO_CORE_CLAIMS", result["blockers"])

    def test_declared_claim_missing_card_blocks(self) -> None:
        self._write_config(core_claims=[self._claim()])
        result = check_claim_check_preflight(self.case_dir)
        self.assertIn("CLAIM_CARD_MISSING", result["blockers"])

    def test_card_path_traversal_blocks(self) -> None:
        self._write_config(core_claims=[self._claim(claim_card_path="../outside.json")])
        result = check_claim_check_preflight(self.case_dir)
        self.assertIn("CLAIM_CARD_UNREADABLE", result["blockers"])

    def test_generator_equal_adversarial_blocks(self) -> None:
        self._write_config(core_claims=[self._claim(adversarial_role_id="writer_role")])
        result = check_claim_check_preflight(self.case_dir)
        self.assertIn("CORE_CLAIM_CLASSIFICATION_SELF_FILLED", result["blockers"])

    def test_generator_equal_provider_blocks(self) -> None:
        self._write_config(core_claims=[self._claim(claim_type_provider_role_id="writer_role")])
        result = check_claim_check_preflight(self.case_dir)
        self.assertIn("CORE_CLAIM_CLASSIFICATION_SELF_FILLED", result["blockers"])

    def test_missing_provider_blocks(self) -> None:
        self._write_config(core_claims=[self._claim(claim_type_provider_role_id="")])
        result = check_claim_check_preflight(self.case_dir)
        self.assertIn("CORE_CLAIM_CLASSIFICATION_PROVIDER_MISSING", result["blockers"])

    def test_malformed_card_blocks_schema(self) -> None:
        self._write_wording()
        path = self.case_dir / "output" / "validation" / "claim_checks" / "C-01.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{broken", encoding="utf-8")
        self._write_config(core_claims=[self._claim()])
        result = check_claim_check_preflight(self.case_dir)
        self.assertIn("CLAIM_CARD_SCHEMA_ERROR", result["blockers"])

    def test_existing_enforcement_unresolved_blocks(self) -> None:
        self._write_wording()
        self._write_card(
            status="RED_FLAG_REQUIRES_HUMAN_DECISION",
            human_decision="PENDING",
            allowed_claim_strength="model_specific_candidate",
            red_flag_count=1,
            red_flag_ids=["FIXTURE_RED"],
        )
        self._write_config(core_claims=[self._claim()])
        result = check_claim_check_preflight(self.case_dir)
        self.assertIn("CLAIM_CHECK_UNRESOLVED_BLOCK", result["blockers"])

    def test_existing_enforcement_passes_settled_card(self) -> None:
        self._write_wording()
        self._write_card()
        self._write_config(core_claims=[self._claim()])
        result = check_claim_check_preflight(self.case_dir)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["claims"][0]["status"], "PASS")
        self.assertFalse(result["final_submit_created"])

    def test_unreadable_wording_path_blocks(self) -> None:
        self._write_card()
        self._write_config(core_claims=[self._claim(proposed_wording_path="paper/missing.tex")])
        result = check_claim_check_preflight(self.case_dir)
        self.assertIn("CLAIM_EVIDENCE_PATH_UNREADABLE", result["blockers"])

    def test_report_writer_is_explicit(self) -> None:
        self._write_config()
        result = check_claim_check_preflight(self.case_dir)
        validation_dir = self.case_dir / "output" / "validation"
        self.assertFalse(validation_dir.exists())
        json_path, markdown_path = write_claim_check_preflight_report(result, self.case_dir)
        self.assertTrue(json_path.is_file())
        self.assertTrue(markdown_path.is_file())
        self.assertFalse(result["final_submit_created"])


if __name__ == "__main__":
    unittest.main()
