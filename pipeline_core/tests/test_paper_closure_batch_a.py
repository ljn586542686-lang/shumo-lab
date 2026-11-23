from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipeline_core.paper_quality.writing_policy import WritingPolicyError, validate_writing_policy
from pipeline_core.quality_gates.result_acceptance_contract import validate_result_acceptance_contract
from pipeline_core.paper_quality.writer_schema import LEGACY_COMPATIBILITY_MODE, resolve_manifest_mode


def _answer(task_id: str = "q1", *, answer_type: str = "strategy", marker: str = "综上得到方案") -> dict:
    return {
        "task_id": task_id,
        "question_requirement": "给出方案",
        "paper_output": marker,
        "representative_value": "方案",
        "paper_location": "question_content_q1",
        "answer_marker": marker,
        "answer_type": answer_type,
        "result_card_ids": [],
        "source_bindings": ["evidence/q1"],
    }


def _result_contract(answers: list[dict], results: list[dict] | None = None) -> dict:
    return {
        "result_cards": results or [],
        "answer_cards": answers,
        "limitation_impacts": [],
    }


def _formal_fixture(text: str = "综上得到方案") -> tuple[Path, dict[str, str], dict]:
    root = Path(tempfile.mkdtemp())
    content_root = root / "content"
    content_root.mkdir()
    values = {
        "title": "标题",
        "authors": "作者",
        "abstract": "目标。方法。结果。",
        "keywords": "关键词",
        "problem_restatement": "研究对象与任务。问题一。",
        "problem_analysis_q1": "分析。",
        "model_assumptions": "假设。",
        "model_assumptions_note": "说明。",
        "notation": "符号。",
        "references": "",
        "appendix": "",
    }
    content: dict[str, str] = {}
    for key, value in values.items():
        path = content_root / f"{key}.tex"
        path.write_text(value, encoding="utf-8")
        content[key] = path.name
    question_path = content_root / "question_content_q1.json"
    question_path.write_text(
        json.dumps({"intro": text, "sections": [], "closing_optional": ""}, ensure_ascii=False),
        encoding="utf-8",
    )
    content["question_content_q1"] = question_path.name
    evaluation_path = content_root / "model_evaluation.json"
    evaluation_path.write_text(
        json.dumps({"strengths": "优点。", "weaknesses": "缺点。", "improvements": "改进。"}, ensure_ascii=False),
        encoding="utf-8",
    )
    content["model_evaluation"] = evaluation_path.name
    task_contract = {
        "tasks": [{
            "task_id": "q1",
            "label": "问题一",
            "analysis_content_key": "problem_analysis_q1",
            "content_key": "question_content_q1",
        }]
    }
    payload = {
        "manifest_mode": "CURRENT_FORMAL",
        "writer_schema_version": "WRITER_SCHEMA_V2",
        "task_contract": task_contract,
        "content": content,
        "writing_policy": {
            "version": "controlled_writing_layer_v2",
            "limitation_policy": {},
            "result_acceptance_contract": _result_contract([_answer()]),
            "appendix_code_mode": "PENDING_USER_POLICY",
        },
    }
    return content_root, content, payload


class PaperClosureBatchATests(unittest.TestCase):
    @staticmethod
    def _task(task_id: str) -> dict[str, str]:
        return {
            "task_id": task_id,
            "content_key": "question_content_q1",
        }

    def test_current_formal_missing_policy_fails_closed(self) -> None:
        with self.assertRaisesRegex(WritingPolicyError, "CURRENT_FORMAL_POLICY_REQUIRED"):
            validate_writing_policy(
                {"manifest_mode": "CURRENT_FORMAL", "task_contract": {"tasks": []}, "content": {}},
                Path("."),
                {},
            )

    def test_current_formal_policy_requires_task_contract(self) -> None:
        with self.assertRaisesRegex(WritingPolicyError, "CURRENT_FORMAL_TASK_CONTRACT_REQUIRED"):
            validate_writing_policy(
                {
                    "manifest_mode": "CURRENT_FORMAL",
                    "content": {},
                    "writing_policy": {"version": "controlled_writing_layer_v2"},
                },
                Path("."),
                {},
            )

    def test_explicit_legacy_missing_policy_remains_compatible(self) -> None:
        report = validate_writing_policy(
            {"manifest_mode": LEGACY_COMPATIBILITY_MODE, "content": {}},
            Path("."),
            {},
        )
        self.assertEqual(report["mode"], "LEGACY_COMPATIBILITY_BYPASS")

    def test_unclassified_missing_fields_do_not_become_legacy(self) -> None:
        with self.assertRaisesRegex(WritingPolicyError, "MANIFEST_MODE_REQUIRED"):
            validate_writing_policy(
                {"writer_schema_version": "WRITER_SCHEMA_V2", "question_count": 1, "content": {"question_content_q1": "q.json"}},
                Path("."),
                {"question_content_q1": "q.json"},
            )

    def test_legacy_fixed_shape_is_positive_legacy_evidence(self) -> None:
        keys = {
            "title", "authors", "abstract", "keywords", "problem_restatement",
            "model_assumptions", "model_assumptions_note", "notation", "model_evaluation",
            "references", "appendix", "problem_analysis_q1", "question_content_q1",
        }
        self.assertEqual(
            resolve_manifest_mode({"question_count": 1}, content_keys=keys),
            LEGACY_COMPATIBILITY_MODE,
        )

    def test_four_tasks_with_three_answers_fails(self) -> None:
        answers = [_answer(f"q{index}") for index in range(1, 4)]
        contract = validate_result_acceptance_contract(
            _result_contract(answers),
            task_contract={"tasks": [self._task(f"q{index}") for index in range(1, 5)]},
        )
        self.assertIn("ANSWER_CARD_MISSING_FOR_TASK", {item["code"] for item in contract["hard_failures"]})

    def test_answer_card_unknown_task_fails(self) -> None:
        contract = validate_result_acceptance_contract(
            _result_contract([_answer("unknown")]),
            task_contract={"tasks": [self._task("q1")]},
        )
        self.assertIn("ANSWER_CARD_UNKNOWN_TASK", {item["code"] for item in contract["hard_failures"]})

    def test_quantitative_answer_without_approved_result_fails(self) -> None:
        quantitative = _answer(answer_type="quantitative", marker="结果为 80 N")
        quantitative["result_card_ids"] = ["r1"]
        quantitative["source_bindings"] = []
        result = {
            "result_id": "r1",
            "display_value": "80",
            "unit": "N",
            "source_binding": "output/r.json#r1",
            "paper_location": "question_content_q1",
            "approved": False,
            "scenario": {"result_source": "output/r.json"},
            "applicable_scenario_fields": ["result_source"],
        }
        report = validate_result_acceptance_contract(
            _result_contract([quantitative], [result]),
            task_contract={"tasks": [self._task("q1")]},
        )
        codes = {item["code"] for item in report["hard_failures"]}
        self.assertIn("RESULT_CARD_NOT_APPROVED", codes)
        self.assertIn("ANSWER_RESULT_CARD_NOT_APPROVED", codes)

    def test_qualitative_strategy_without_numeric_result_can_pass(self) -> None:
        report = validate_result_acceptance_contract(
            _result_contract([_answer(answer_type="strategy")]),
            task_contract={"tasks": [self._task("q1")]},
        )
        self.assertEqual(report["status"], "PASS")

    def test_answer_must_precede_public_limitation(self) -> None:
        content_root, content, payload = _formal_fixture("适用范围：理想条件。综上得到方案")
        payload["writing_policy"]["limitation_policy"] = {
            "limitations": [{
                "limitation_id": "public_condition",
                "visibility": "public",
                "content_keys": ["question_content_q1"],
                "markers": ["适用范围：理想条件"],
            }]
        }
        with self.assertRaisesRegex(WritingPolicyError, "ANSWER_BEFORE_PUBLIC_LIMITATION_REQUIRED"):
            validate_writing_policy(payload, content_root, content)

    def test_internal_limitation_is_not_copied_to_public_validation(self) -> None:
        content_root, content, payload = _formal_fixture()
        payload["writing_policy"]["limitation_policy"] = {
            "limitations": [{
                "limitation_id": "internal_boundary",
                "visibility": "internal",
                "content_keys": ["question_content_q1"],
                "markers": ["INTERNAL_ONLY_BOUNDARY"],
            }]
        }
        report = validate_writing_policy(payload, content_root, content)
        self.assertEqual(report["limitations"]["public_limitation_ids"], [])
        self.assertEqual(report["limitations"]["internal_limitation_ids"], ["internal_boundary"])

    def test_public_limitation_after_answer_can_pass(self) -> None:
        content_root, content, payload = _formal_fixture("综上得到方案。适用范围：理想条件")
        payload["writing_policy"]["limitation_policy"] = {
            "limitations": [{
                "limitation_id": "public_condition",
                "visibility": "public",
                "content_keys": ["question_content_q1"],
                "markers": ["适用范围：理想条件"],
            }]
        }
        report = validate_writing_policy(payload, content_root, content)
        self.assertEqual(report["status"], "PASS")

    def test_internal_production_terms_are_blocked_in_public_text(self) -> None:
        content_root, content, payload = _formal_fixture("综上得到方案。CLAIM_BOUNDARY")
        with self.assertRaisesRegex(WritingPolicyError, "INTERNAL_STATE_TERM_LEAKAGE"):
            validate_writing_policy(payload, content_root, content)


if __name__ == "__main__":
    unittest.main()
