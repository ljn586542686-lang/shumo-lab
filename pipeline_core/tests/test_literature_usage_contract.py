from __future__ import annotations

import unittest

from pipeline_core.paper_quality.literature_usage_contract import (
    build_literature_review_packet,
    validate_literature_usage_contract,
)


def contract() -> dict:
    return {
        "external_evidence_required": True,
        "records": [{
            "record_id": "method_1", "citation_key": "zh_method", "source_type": "method", "language": "zh",
            "knowledge_gap": "求解器误差控制", "decision_influenced": "选用步长收敛验证",
            "citation_reason": "给出可复现的误差判据", "competition_suitability": "方法直接、可在本科竞赛篇幅内说明",
            "paper_location": "question_content_q2", "human_review_status": "APPROVED",
        }],
    }


class LiteratureUsageContractTests(unittest.TestCase):
    def test_approved_chinese_source_passes_with_thin_support_warning(self) -> None:
        report = validate_literature_usage_contract(contract(), cited_keys={"zh_method"})
        self.assertEqual(report["status"], "WARNING")
        self.assertIn("LITERATURE_SUPPORT_THIN", {item["code"] for item in report["warnings"]})

    def test_empty_formal_literature_plan_is_blocked(self) -> None:
        report = validate_literature_usage_contract({"records": []}, cited_keys=set())
        self.assertIn("LITERATURE_USAGE_REQUIRED", {item["code"] for item in report["hard_failures"]})

    def test_unapproved_or_unexplained_non_chinese_source_fails(self) -> None:
        payload = contract()
        payload["records"][0]["human_review_status"] = "PENDING_HUMAN_REVIEW"
        payload["records"][0]["language"] = "en"
        report = validate_literature_usage_contract(payload, cited_keys={"zh_method"})
        codes = {item["code"] for item in report["hard_failures"]}
        self.assertIn("LITERATURE_HUMAN_REVIEW_REQUIRED", codes)
        self.assertIn("NON_CHINESE_LITERATURE_EXCEPTION_REQUIRED", codes)

    def test_review_packet_explains_reason_and_competition_suitability(self) -> None:
        packet = build_literature_review_packet(contract())
        self.assertIn("引用原因", packet)
        self.assertIn("本科数模论文的适合性", packet)
        self.assertIn("用户决定", packet)


if __name__ == "__main__":
    unittest.main()
