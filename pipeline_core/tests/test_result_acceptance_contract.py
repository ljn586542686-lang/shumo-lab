from __future__ import annotations

import unittest

from pipeline_core.quality_gates.result_acceptance_contract import validate_result_acceptance_contract


def valid_contract() -> dict:
    return {
        "version": "result_acceptance_contract_v1",
        "result_cards": [
            {
                "result_id": "q4_representative_force",
                "display_value": "80",
                "unit": "N",
                "source_binding": "results/q4.json#row-12",
                "paper_location": "question_content_q4",
                "approved": True,
                "applicable_scenario_fields": [
                    "result_source", "e", "duration", "scenario_id", "player_combination", "direction_angle", "mirror"
                ],
                "scenario": {
                    "result_source": "results/q4.json",
                    "e": "0.8",
                    "duration": "0.10 s",
                    "scenario_id": "representative",
                    "player_combination": "1,2",
                    "direction_angle": "12 deg",
                    "mirror": "1",
                },
                "display_precision_decimals": 0,
                "validated_precision_decimals": 1,
            }
        ],
        "answer_cards": [
            {
                "task_id": "q4",
                "question_requirement": "给出纠偏方案",
                "paper_output": "相邻队员的代表方案",
                "representative_value": "80 N",
                "paper_location": "question_content_q4",
                "answer_marker": "相邻队员的代表方案",
                "answer_type": "quantitative",
                "result_card_ids": ["q4_representative_force"],
            }
        ],
        "limitation_impacts": [
            {
                "limitation_id": "periodic_reset",
                "assumption_or_boundary": "周期复位近似",
                "affected_quantities": "碰撞时机与共同张力",
                "conditional_results": "该代表方案只在近似成立时使用",
            }
        ],
    }


class ResultAcceptanceContractTests(unittest.TestCase):
    def test_complete_card_contract_passes(self) -> None:
        self.assertEqual(validate_result_acceptance_contract(valid_contract())["status"], "PASS")

    def test_missing_scenario_or_answer_output_fails(self) -> None:
        contract = valid_contract()
        del contract["result_cards"][0]["scenario"]["mirror"]
        contract["answer_cards"][0]["representative_value"] = ""
        report = validate_result_acceptance_contract(contract)
        self.assertEqual(report["status"], "FAIL")
        codes = {item["code"] for item in report["hard_failures"]}
        self.assertIn("RESULT_SCENARIO_FIELD_MISSING", codes)
        self.assertIn("ANSWER_OUTPUT_MISSING", codes)

    def test_precision_cannot_exceed_validation_precision(self) -> None:
        contract = valid_contract()
        contract["result_cards"][0]["display_precision_decimals"] = 3
        contract["result_cards"][0]["validated_precision_decimals"] = 1
        report = validate_result_acceptance_contract(contract)
        self.assertIn("RESULT_PRECISION_UNSUPPORTED", {item["code"] for item in report["hard_failures"]})

    def test_visual_and_method_links_must_point_to_bound_results(self) -> None:
        contract = valid_contract()
        contract["input_conditions"] = [{"condition_id": "mass", "paper_location": "problem_restatement", "marker": "0.270 kg"}]
        contract["visual_bindings"] = [{
            "visual_id": "fig_tilt", "kind": "figure", "paper_location": "question_content_q2",
            "caption_marker": "倾角", "result_card_ids": ["unknown"],
        }]
        contract["method_output_links"] = [{
            "method_id": "solve", "paper_location": "question_content_q4", "method_marker": "求解",
            "output_marker": "80 N", "result_card_ids": ["q4_representative_force"],
        }]
        report = validate_result_acceptance_contract(contract)
        self.assertIn("VISUAL_RESULT_CARD_UNKNOWN", {item["code"] for item in report["hard_failures"]})


if __name__ == "__main__":
    unittest.main()
