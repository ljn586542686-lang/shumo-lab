"""Tests: F5.5c claim check enforcement gate (X5-F5.5C).

Verifies that enforce_claim_ready_for_writing() correctly blocks or passes
core conclusion writing attempts based on claim check settlement state.

These tests do NOT:
  - create final_submit/
  - modify cases/ or archive/
  - require network access
  - run MATLAB
  - generate or modify any paper PDF
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline_core.claim_check.enforce_claim_check import (
    HIGH_RISK_CLAIM_TYPES,
    BLOCK_MISSING,
    BLOCK_UNRESOLVED,
    BLOCK_REJECTED,
    BLOCK_WORDING,
    FAIL_SELF_FILLED,
    FAIL_PROVIDER_MISSING,
    PASS_READY,
    enforce_claim_ready_for_writing,
    requires_claim_check,
    validate_core_claim_classification,
    check_wording_compatible,
)


# ---------------------------------------------------------------------------
# Helper: minimal settled result dict
# ---------------------------------------------------------------------------

def _settled(
    human_decision: str = "A_ACCEPT_AS_CORE_CLAIM",
    allowed_claim_strength: str = "core_conclusion",
    red_flag_count: int = 0,
    red_flag_ids: list[str] | None = None,
) -> dict:
    return {
        "human_decision": human_decision,
        "allowed_claim_strength": allowed_claim_strength,
        "red_flag_count": red_flag_count,
        "red_flag_ids": red_flag_ids or [],
        "status": "AUTO_PASS" if red_flag_count == 0 else "RED_FLAG_REQUIRES_HUMAN_DECISION",
        "recommended_decision": "A_ACCEPT_AS_CORE_CLAIM" if red_flag_count == 0 else "B_DOWNGRADE_CLAIM",
    }


def _boundary_result(human_decision: str = "PENDING") -> dict:
    """Simulates the F5.5b dry-run result for synthetic boundary (9 RED flags, C_REJECT_AND_REWORK)."""
    return {
        "human_decision": human_decision,
        "allowed_claim_strength": "not_allowed",
        "red_flag_count": 9,
        "red_flag_ids": [
            "EDGE_BOUNDARY_FLAG", "MODEL_CONFLICT_FLAG", "MODEL_SELECTION_FLAG",
            "BASELINE_MISSING_FLAG", "MISSING_REQUIRED_EVIDENCE_FLAG",
            "UNCERTAINTY_MISSING_FLAG",
        ],
        "status": "RED_FLAG_REQUIRES_HUMAN_DECISION",
        "recommended_decision": "C_REJECT_AND_REWORK",
    }


# ---------------------------------------------------------------------------
# Test 1: requires_claim_check
# ---------------------------------------------------------------------------

class TestRequiresClaimCheck(unittest.TestCase):

    def test_high_risk_types_require_check(self):
        for ct in HIGH_RISK_CLAIM_TYPES:
            with self.subTest(claim_type=ct):
                self.assertTrue(requires_claim_check(ct, False))

    def test_is_core_conclusion_true_requires_check(self):
        self.assertTrue(requires_claim_check("model_explanation", True))

    def test_low_risk_non_core_does_not_require_check(self):
        self.assertFalse(requires_claim_check("model_explanation", False))

    def test_all_eight_high_risk_types_listed(self):
        expected = {
            "optimal_condition", "model_selection", "prediction_claim",
            "experiment_design", "sensitivity_claim", "stability_claim",
            "evaluation_claim", "classification_claim",
        }
        self.assertEqual(HIGH_RISK_CLAIM_TYPES, expected)


# ---------------------------------------------------------------------------
# Test 2: classification guard
# ---------------------------------------------------------------------------

class TestCoreClaimClassificationGuard(unittest.TestCase):

    def test_self_filled_high_risk_triggers_fail(self):
        result = validate_core_claim_classification(
            claim_type="optimal_condition",
            is_core_conclusion=True,
            claim_type_provider_role_id="generator_gpt4",
            generator_role_id="generator_gpt4",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.verdict, FAIL_SELF_FILLED)

    def test_provider_missing_triggers_fail(self):
        result = validate_core_claim_classification(
            claim_type="optimal_condition",
            is_core_conclusion=True,
            claim_type_provider_role_id=None,
            generator_role_id="generator_gpt4",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.verdict, FAIL_PROVIDER_MISSING)

    def test_different_provider_passes(self):
        result = validate_core_claim_classification(
            claim_type="optimal_condition",
            is_core_conclusion=True,
            claim_type_provider_role_id="red_team_claude",
            generator_role_id="generator_gpt4",
        )
        self.assertIsNone(result)

    def test_low_risk_non_core_skips_guard(self):
        result = validate_core_claim_classification(
            claim_type="model_explanation",
            is_core_conclusion=False,
            claim_type_provider_role_id=None,
            generator_role_id="generator_gpt4",
        )
        self.assertIsNone(result)

    def test_provider_missing_empty_string_triggers_fail(self):
        result = validate_core_claim_classification(
            claim_type="model_selection",
            is_core_conclusion=False,
            claim_type_provider_role_id="",
            generator_role_id="generator_gpt4",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.verdict, FAIL_PROVIDER_MISSING)


# ---------------------------------------------------------------------------
# Test 3: claim_check_result missing
# ---------------------------------------------------------------------------

class TestMissingClaimCheckResult(unittest.TestCase):

    def test_missing_result_blocks_high_risk(self):
        r = enforce_claim_ready_for_writing(
            claim_id="TEST-MISS-001",
            claim_type="optimal_condition",
            is_core_conclusion=True,
            claim_check_result=None,
        )
        self.assertEqual(r.verdict, BLOCK_MISSING)

    def test_missing_result_blocks_core_conclusion(self):
        r = enforce_claim_ready_for_writing(
            claim_id="TEST-MISS-002",
            claim_type="model_explanation",
            is_core_conclusion=True,
            claim_check_result=None,
        )
        self.assertEqual(r.verdict, BLOCK_MISSING)

    def test_non_core_low_risk_passes_without_result(self):
        r = enforce_claim_ready_for_writing(
            claim_id="TEST-MISS-003",
            claim_type="model_explanation",
            is_core_conclusion=False,
            claim_check_result=None,
        )
        self.assertEqual(r.verdict, PASS_READY)


# ---------------------------------------------------------------------------
# Test 4: RED + PENDING blocks writing
# ---------------------------------------------------------------------------

class TestRedPendingBlocks(unittest.TestCase):

    def test_red_and_pending_blocks(self):
        r = enforce_claim_ready_for_writing(
            claim_id="TEST-RED-001",
            claim_type="optimal_condition",
            is_core_conclusion=True,
            claim_check_result=_boundary_result(human_decision="PENDING"),
        )
        self.assertEqual(r.verdict, BLOCK_UNRESOLVED)

    def test_red_with_decision_does_not_unresolved_block(self):
        # RED flags present but human decided B_DOWNGRADE — not an unresolved block
        r = enforce_claim_ready_for_writing(
            claim_id="TEST-RED-002",
            claim_type="model_selection",
            is_core_conclusion=True,
            claim_check_result=_settled(
                human_decision="B_DOWNGRADE_CLAIM",
                allowed_claim_strength="model_specific_candidate",
                red_flag_count=1,
                red_flag_ids=["MODEL_SELECTION_FLAG"],
            ),
            proposed_wording="该模型为主线模型下的优先复核候选。",
        )
        self.assertNotEqual(r.verdict, BLOCK_UNRESOLVED)


# ---------------------------------------------------------------------------
# Test 5: C_REJECT_AND_REWORK / not_allowed blocks
# ---------------------------------------------------------------------------

class TestRejectedOrNotAllowed(unittest.TestCase):

    def test_c_reject_blocks(self):
        r = enforce_claim_ready_for_writing(
            claim_id="TEST-REJ-001",
            claim_type="optimal_condition",
            is_core_conclusion=True,
            claim_check_result=_boundary_result(human_decision="C_REJECT_AND_REWORK"),
        )
        self.assertEqual(r.verdict, BLOCK_REJECTED)

    def test_not_allowed_blocks(self):
        result = {**_settled(), "allowed_claim_strength": "not_allowed",
                  "human_decision": "A_ACCEPT_AS_CORE_CLAIM"}
        r = enforce_claim_ready_for_writing(
            claim_id="TEST-REJ-002",
            claim_type="optimal_condition",
            is_core_conclusion=True,
            claim_check_result=result,
            proposed_wording="任何措辞",
        )
        self.assertEqual(r.verdict, BLOCK_REJECTED)


# ---------------------------------------------------------------------------
# Test 6: B_DOWNGRADE_CLAIM — low strength wording passes, strong wording blocks
# ---------------------------------------------------------------------------

class TestDowngradeClaimWording(unittest.TestCase):

    def _model_specific_result(self) -> dict:
        return _settled(
            human_decision="B_DOWNGRADE_CLAIM",
            allowed_claim_strength="model_specific_candidate",
            red_flag_count=1,
            red_flag_ids=["MODEL_SELECTION_FLAG"],
        )

    def test_downgraded_wording_passes(self):
        r = enforce_claim_ready_for_writing(
            claim_id="TEST-DG-001",
            claim_type="optimal_condition",
            is_core_conclusion=True,
            claim_check_result=self._model_specific_result(),
            proposed_wording="合成序列的边界点是主线模型下的优先复核候选。",
        )
        self.assertEqual(r.verdict, PASS_READY)

    def test_strong_wording_blocked(self):
        r = enforce_claim_ready_for_writing(
            claim_id="TEST-DG-002",
            claim_type="optimal_condition",
            is_core_conclusion=True,
            claim_check_result=self._model_specific_result(),
            proposed_wording="合成序列的边界点是全局最优条件。",
        )
        self.assertEqual(r.verdict, BLOCK_WORDING)
        self.assertIn("全局最优", r.blocked_token)


# ---------------------------------------------------------------------------
# Test 7: model_specific_candidate wording rules
# ---------------------------------------------------------------------------

class TestModelSpecificCandidateWording(unittest.TestCase):

    def _result(self) -> dict:
        return _settled(
            human_decision="B_DOWNGRADE_CLAIM",
            allowed_claim_strength="model_specific_candidate",
        )

    def test_global_optimal_blocked(self):
        for wording in ("全局最优", "最终最优", "确定最优"):
            with self.subTest(wording=wording):
                r = check_wording_compatible(wording, "model_specific_candidate")
                self.assertIsNotNone(r)
                self.assertEqual(r.verdict, BLOCK_WORDING)

    def test_candidate_wording_passes(self):
        r = check_wording_compatible("主线模型下的优先复核候选", "model_specific_candidate")
        self.assertIsNone(r)


# ---------------------------------------------------------------------------
# Test 8: trend_evidence_only wording rules
# ---------------------------------------------------------------------------

class TestTrendEvidenceWording(unittest.TestCase):

    def test_prove_blocked(self):
        r = check_wording_compatible("该结果证明模型成立", "trend_evidence_only")
        self.assertIsNotNone(r)
        self.assertEqual(r.verdict, BLOCK_WORDING)

    def test_final_conclusion_blocked(self):
        r = check_wording_compatible("最终结论是条件A最优", "trend_evidence_only")
        self.assertIsNotNone(r)
        self.assertEqual(r.verdict, BLOCK_WORDING)

    def test_core_basis_blocked(self):
        r = check_wording_compatible("作为核心依据，条件A成立", "trend_evidence_only")
        self.assertIsNotNone(r)
        self.assertEqual(r.verdict, BLOCK_WORDING)

    def test_trend_wording_passes(self):
        r = check_wording_compatible("趋势提示：条件A在扰动下有下降倾向", "trend_evidence_only")
        self.assertIsNone(r)


# ---------------------------------------------------------------------------
# Test 9: no bureaucratic overhead — low-risk passes without claim check
# ---------------------------------------------------------------------------

class TestNoBureaucraticOverhead(unittest.TestCase):

    def test_low_risk_non_core_passes_immediately(self):
        r = enforce_claim_ready_for_writing(
            claim_id="TEST-NB-001",
            claim_type="model_explanation",
            is_core_conclusion=False,
            claim_check_result=None,
        )
        self.assertEqual(r.verdict, PASS_READY)

    def test_zero_red_flag_accepted_result_passes(self):
        r = enforce_claim_ready_for_writing(
            claim_id="TEST-NB-002",
            claim_type="sensitivity_claim",
            is_core_conclusion=True,
            claim_check_result=_settled(
                human_decision="A_ACCEPT_AS_CORE_CLAIM",
                allowed_claim_strength="core_conclusion",
                red_flag_count=0,
            ),
            proposed_wording="敏感性分析表明扰动影响在可接受范围内。",
        )
        self.assertEqual(r.verdict, PASS_READY)


# ---------------------------------------------------------------------------
# Test 10: synthetic boundary enforcement scenario
# ---------------------------------------------------------------------------

class TestBoundaryEnforcement(unittest.TestCase):
    """Full scenario tests using the canonical F5.5b synthetic boundary claim result."""

    def test_strong_wording_blocked_when_not_allowed(self):
        r = enforce_claim_ready_for_writing(
            claim_id="SYNTHETIC-BOUNDARY-01",
            claim_type="optimal_condition",
            is_core_conclusion=True,
            claim_check_result=_boundary_result("C_REJECT_AND_REWORK"),
            proposed_wording="合成序列的边界点是最优条件。",
        )
        self.assertEqual(r.verdict, BLOCK_REJECTED)

    def test_missing_result_blocks(self):
        r = enforce_claim_ready_for_writing(
            claim_id="SYNTHETIC-BOUNDARY-01",
            claim_type="optimal_condition",
            is_core_conclusion=True,
            claim_check_result=None,
            proposed_wording="合成序列的边界点是最优条件。",
        )
        self.assertEqual(r.verdict, BLOCK_MISSING)

    def test_red_pending_blocks(self):
        r = enforce_claim_ready_for_writing(
            claim_id="SYNTHETIC-BOUNDARY-01",
            claim_type="optimal_condition",
            is_core_conclusion=True,
            claim_check_result=_boundary_result("PENDING"),
        )
        self.assertEqual(r.verdict, BLOCK_UNRESOLVED)

    def test_downgraded_candidate_wording_passes(self):
        # Simulate: human decided B_DOWNGRADE, strength upgraded to model_specific_candidate
        downgraded_result = {
            "human_decision": "B_DOWNGRADE_CLAIM",
            "allowed_claim_strength": "model_specific_candidate",
            "red_flag_count": 2,
            "red_flag_ids": ["EDGE_BOUNDARY_FLAG", "MODEL_SELECTION_FLAG"],
            "status": "RED_FLAG_REQUIRES_HUMAN_DECISION",
            "recommended_decision": "B_DOWNGRADE_CLAIM",
        }
        r = enforce_claim_ready_for_writing(
            claim_id="SYNTHETIC-BOUNDARY-01",
            claim_type="optimal_condition",
            is_core_conclusion=True,
            claim_check_result=downgraded_result,
            proposed_wording="合成序列的边界点是主线模型下的优先复核候选。",
        )
        self.assertEqual(r.verdict, PASS_READY)

    def test_global_optimal_still_blocked_after_downgrade(self):
        downgraded_result = {
            "human_decision": "B_DOWNGRADE_CLAIM",
            "allowed_claim_strength": "model_specific_candidate",
            "red_flag_count": 2,
            "red_flag_ids": ["EDGE_BOUNDARY_FLAG", "MODEL_SELECTION_FLAG"],
            "status": "RED_FLAG_REQUIRES_HUMAN_DECISION",
            "recommended_decision": "B_DOWNGRADE_CLAIM",
        }
        r = enforce_claim_ready_for_writing(
            claim_id="SYNTHETIC-BOUNDARY-01",
            claim_type="optimal_condition",
            is_core_conclusion=True,
            claim_check_result=downgraded_result,
            proposed_wording="合成序列的边界点是全局最优条件。",
        )
        self.assertEqual(r.verdict, BLOCK_WORDING)


# ---------------------------------------------------------------------------
# Test 11: safety boundaries
# ---------------------------------------------------------------------------

class TestSafetyBoundaries(unittest.TestCase):

    def test_final_submit_not_created(self):
        final_submit = REPO_ROOT / "final_submit"
        self.assertFalse(final_submit.exists())

    def test_cases_dir_intact(self):
        cases = REPO_ROOT / "cases"
        if cases.exists():
            self.assertTrue(cases.is_dir())

    def test_archive_dir_intact(self):
        archive = REPO_ROOT / "archive"
        if archive.exists():
            self.assertTrue(archive.is_dir())


if __name__ == "__main__":
    unittest.main()
