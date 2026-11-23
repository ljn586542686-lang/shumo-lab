from __future__ import annotations

import unittest

from pipeline_core.paper_quality.paper_evidence_contract import validate_paper_evidence_contract


def contract() -> dict:
    return {
        "optimization_task_ids": ["task_2"],
        "model_blocks": [{
            "model_id": "m1", "task_id": "task_2", "paper_location": "question_content_task_2",
            "model_goal": "降低损失", "method_selection_basis": "目标和约束均可量化",
            "variables": [{"symbol": "x", "definition": "决策变量"}],
            "derivation_steps": [{"formula_tex": "f(x)", "explanation": "构造目标函数"}],
            "physical_meaning": "目标表示损失", "boundary_marker": "仅在给定条件下成立",
            "result_card_ids": ["baseline", "optimized"], "assumption_ids": ["a1"],
        }],
        "assumption_ledger": [{
            "assumption_id": "a1", "statement": "忽略高阶项", "basis": "量级较小", "simplification": "一阶近似",
            "valid_domain": "小扰动", "limitation_id": "l1", "affected_result_card_ids": ["baseline", "optimized"],
        }],
        "optimization_comparisons": [{
            "task_id": "task_2", "paper_location": "question_content_task_2", "metric": "损失", "unit": "m",
            "direction": "minimize", "baseline_value": "8", "optimized_value": "7", "comparison_marker": "由8降至7",
            "baseline_result_card_id": "baseline", "optimized_result_card_id": "optimized",
            "baseline_scenario": {"case": "A"}, "optimized_scenario": {"case": "A"},
        }],
        "claim_evidence_cards": [{
            "claim_id": "c1", "claim": "在给定条件下损失降低", "paper_location": "question_content_task_2",
            "claim_marker": "损失降低", "result_card_ids": ["baseline", "optimized"], "model_ids": ["m1"], "assumption_ids": ["a1"],
        }],
    }


class PaperEvidenceContractTests(unittest.TestCase):
    def test_complete_evidence_contract_passes(self) -> None:
        self.assertEqual(validate_paper_evidence_contract(contract(), result_card_ids={"baseline", "optimized"})["status"], "PASS")

    def test_optimization_requires_same_scenario_and_bound_results(self) -> None:
        payload = contract()
        payload["optimization_comparisons"][0]["optimized_scenario"] = {"case": "B"}
        payload["optimization_comparisons"][0]["optimized_result_card_id"] = "unknown"
        report = validate_paper_evidence_contract(payload, result_card_ids={"baseline", "optimized"})
        codes = {item["code"] for item in report["hard_failures"]}
        self.assertIn("OPTIMIZATION_SCENARIO_NOT_COMPARABLE", codes)
        self.assertIn("OPTIMIZATION_RESULT_REFERENCE_INVALID", codes)

    def test_unqualified_claim_is_rejected(self) -> None:
        payload = contract()
        payload["claim_evidence_cards"][0]["claim"] = "这是全局最优方案"
        report = validate_paper_evidence_contract(payload, result_card_ids={"baseline", "optimized"})
        self.assertIn("UNQUALIFIED_CLAIM_FORBIDDEN", {item["code"] for item in report["hard_failures"]})


if __name__ == "__main__":
    unittest.main()
