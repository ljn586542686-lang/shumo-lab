from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline_core.paper_quality.writing_policy import (
    WritingPolicyError,
    validate_abstract_structure,
    validate_figure_placement,
    validate_formula_derivation_chains,
    validate_keywords,
    validate_problem_restatement,
    validate_public_language,
    validate_references,
    validate_symbol_policy,
    validate_writing_policy,
)


class WritingPolicyTests(unittest.TestCase):
    def test_v3_current_formal_rejects_empty_quality_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content_root = root / "content"
            content_root.mkdir()
            (content_root / "analysis.tex").write_text("分析依据。\n\n约束来源。", encoding="utf-8")
            (content_root / "question.json").write_text(
                json.dumps({"intro": "模型。", "sections": [{"title": "推导", "content": "结果与验证。"}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            content = {"problem_analysis_task": "analysis.tex", "question_content_task": "question.json"}
            payload = {
                "manifest_mode": "CURRENT_FORMAL",
                "writer_schema_version": "WRITER_SCHEMA_V2",
                "task_contract": {"tasks": [{
                    "task_id": "task", "label": "任务", "analysis_content_key": "problem_analysis_task",
                    "content_key": "question_content_task",
                }]},
                "content": content,
                "writing_policy": {
                    "version": "controlled_writing_layer_v3",
                    "formula_derivation": {"chains": []},
                    "symbol_policy": {"semantic_subscript_decision": "REQUIRED", "semantic_subscript_mapping": {}},
                    "paragraph_policy": {"records": []},
                    "task_closure_contract": {"records": []},
                    "result_acceptance_contract": {
                        "result_cards": [],
                        "answer_cards": [{
                            "task_id": "task", "question_requirement": "给出方案", "paper_output": "方案",
                            "representative_value": "方案", "paper_location": "question_content_task",
                            "answer_marker": "结果", "answer_type": "strategy", "source_bindings": ["evidence/task"],
                            "result_card_ids": [],
                        }],
                        "limitation_impacts": [],
                    },
                    "paper_evidence_contract": {
                        "model_blocks": [], "assumption_ledger": [], "claim_evidence_cards": [],
                        "optimization_comparisons": [], "optimization_task_ids": [],
                    },
                    "appendix_code_mode": "NOT_REQUIRED",
                    "appendix_code_reason": "该测试只验证正文质量契约，不要求附录代码。",
                },
            }
            with self.assertRaises(WritingPolicyError) as context:
                validate_writing_policy(payload, content_root, content)
            message = str(context.exception)
            self.assertIn("FORMULA_DERIVATION_CHAIN_MISSING_FOR_TASK", message)
            self.assertIn("TASK_CLOSURE_RECORD_MISSING", message)
            self.assertIn("MODEL_EVIDENCE_MISSING_FOR_TASK", message)
            self.assertIn("METHOD_OUTPUT_LINK_MISSING_FOR_TASK", message)
            payload["writing_policy"]["version"] = "controlled_writing_layer_v4"
            with self.assertRaisesRegex(WritingPolicyError, "VISUAL_REASONING_CONTRACT_REQUIRED"):
                validate_writing_policy(payload, content_root, content)
            payload["writing_policy"]["version"] = "controlled_writing_layer_v5"
            with patch(
                "pipeline_core.paper_quality.writing_policy.validate_visual_reasoning_contract",
                return_value={"status": "PASS", "hard_failures": [], "warnings": []},
            ):
                with self.assertRaisesRegex(WritingPolicyError, "ARGUMENT_BLUEPRINT_CONTRACT_REQUIRED"):
                    validate_writing_policy(payload, content_root, content)

    def test_style_guidance_becomes_warning_not_blocker(self) -> None:
        abstract = validate_abstract_structure("简短摘要。", {"minimum_paragraphs": 3})
        self.assertEqual(abstract["status"], "WARNING")
        chain = validate_formula_derivation_chains(
            {"question_content_alpha": "只有结果。"},
            {"chains": [{"question": "方案设计", "steps": ["结果"], "content_keys": ["question_content_alpha"], "markers": ["缺失"]}]},
        )
        self.assertEqual(chain["status"], "WARNING")

    def test_current_formal_abstract_requires_total_part_total_structure(self) -> None:
        task_contract = {"tasks": [
            {"task_id": "q1"},
            {"task_id": "q2"},
        ]}
        text = "本文研究定位问题。\n\n问题一采用几何法并得到结果。\n\n问题二完成辨识并得到结论。\n\n综上，方法在给定条件下有效。"
        spec = {
            "minimum_paragraphs": 4,
            "required_concepts": {"result": ["得到结果"]},
            "total_part_total_structure": {
                "opening_markers": ["本文研究"],
                "task_paragraphs": [
                    {"task_id": "q1", "markers": ["问题一", "几何法"]},
                    {"task_id": "q2", "markers": ["问题二", "辨识"]},
                ],
                "closing_markers": ["综上"],
            },
        }
        self.assertEqual(
            validate_abstract_structure(text, spec, task_contract=task_contract, strict=True)["status"],
            "PASS",
        )
        del spec["total_part_total_structure"]
        report = validate_abstract_structure(text, spec, task_contract=task_contract, strict=True)
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("ABSTRACT_TOTAL_PART_TOTAL_STRUCTURE_REQUIRED", {item["code"] for item in report["hard_failures"]})

    def test_current_formal_restatement_and_keywords_fail_closed(self) -> None:
        tasks = [{"task_id": "q1", "label": "问题一"}, {"task_id": "q2", "label": "问题二"}]
        report = validate_problem_restatement(
            "研究背景。问题一说明任务。",
            {
                "background_markers": ["研究背景"],
                "questions": [
                    {"label": "问题一", "markers": ["说明任务"]},
                    {"label": "问题二", "markers": ["给出方案"]},
                ],
                "forbidden_method_terms": ["遗传算法"],
            },
            tasks,
            strict=True,
        )
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("PROBLEM_RESTATEMENT_TASK_LABEL_NOT_EVIDENT", {item["code"] for item in report["hard_failures"]})
        self.assertEqual(validate_keywords("定位；编队", {"minimum_keywords": 3}, strict=True)["status"], "FAIL")
        self.assertEqual(validate_keywords("定位；编队；定角圆弧", {"minimum_keywords": 3}, strict=True)["status"], "PASS")

    def test_current_formal_figures_require_chinese_visible_labels(self) -> None:
        base = {
            "figure_id": "figure_q1",
            "backend": "MATLAB",
            "figure_type": "line",
            "purpose": "comparison",
            "title": "定位误差",
            "x_label": "扰动幅度 / m",
            "y_label": "位置误差 / m",
            "source_binding": {"source_id": "r1", "source_type": "result_card", "sha256": "a" * 64},
            "output_file": "figures/q1.png",
            "dpi": 400,
            "series": [{"name": "位置误差", "x": [0, 1], "y": [0, 1]}],
            "language_policy": "ZH_PRIMARY",
        }
        plan = {"formal_figure_backend": "MATLAB", "figures": [{
            "figure_id": "figure_q1", "public_title": "定位误差", "first_discussion": "分析误差变化。",
            "source_binding": "output/result_summary.json", "formal": True, "render_backend": "MATLAB",
            "matlab_script_path": "output/figures/q1.m", "matlab_figure_spec": base,
        }]}
        self.assertEqual(validate_figure_placement(plan, strict=True)["status"], "PASS")
        plan["figures"][0]["matlab_figure_spec"]["x_label"] = "initial bias / m"
        report = validate_figure_placement(plan, strict=True)
        self.assertIn("FORMAL_FIGURE_CHINESE_LABEL_REQUIRED", {item["code"] for item in report["hard_failures"]})

    def test_public_language_and_reference_mismatch_remain_hard(self) -> None:
        with self.assertRaisesRegex(WritingPolicyError, "PUBLIC_PROBLEM_LABEL_FORBIDDEN"):
            validate_public_language("Q1 result", strict=True)
        refs = validate_references(
            {"problem_restatement": r"题目见\cite{official}。", "references": r"\bibitem{unused} Author. Title."},
            {},
        )
        self.assertEqual(refs["status"], "FAIL")
        self.assertEqual(refs["hard_failures"][0]["code"], "REFERENCE_BIDIRECTIONAL_MISMATCH")

    def test_symbol_mapping_is_case_declared_and_never_rewrites_math(self) -> None:
        self.assertEqual(validate_symbol_policy(r"v_{ball}^{+}, T_i, \Delta t", {})["status"], "PASS")
        report = validate_symbol_policy(
            r"v_{ball}^{+}, T_i, \Delta t",
            {"semantic_subscript_mapping": {"ball": "球"}},
        )
        self.assertEqual(report["status"], "WARNING")
        self.assertIn("+", report["protected_math_symbols"])

    def test_explicit_policy_supports_named_tasks_and_warning_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content_root = root / "content"
            content_root.mkdir()
            text_files = {
                "abstract": "研究目标。\n\n采用方法。\n\n得到结果。",
                "problem_restatement": "背景与研究对象。空间配置任务。",
                "problem_analysis_layout": r"分析。\cite{official}",
                "references": r"\bibitem{official} 官方题面。",
                "title": "标题",
                "authors": "",
                "keywords": "关键词",
                "model_assumptions": "假设",
                "model_assumptions_note": "说明",
                "notation": "符号",
                "appendix": "附录",
            }
            content: dict[str, str] = {}
            for key, value in text_files.items():
                path = content_root / f"{key}.tex"
                path.write_text(value, encoding="utf-8")
                content[key] = path.name
            question = {"intro": "变量定义。", "sections": [{"title": "配置关系", "content": "由约束得到方案。综上得到方案。"}]}
            (content_root / "question_content_layout.json").write_text(json.dumps(question, ensure_ascii=False), encoding="utf-8")
            content["question_content_layout"] = "question_content_layout.json"
            evaluation = {"strengths": "优点", "weaknesses": "限制", "improvements": "改进"}
            (content_root / "model_evaluation.json").write_text(json.dumps(evaluation, ensure_ascii=False), encoding="utf-8")
            content["model_evaluation"] = "model_evaluation.json"
            payload = {
                "content": content,
                "task_contract": {"tasks": [{"task_id": "layout", "label": "空间配置", "analysis_content_key": "problem_analysis_layout", "content_key": "question_content_layout"}]},
                "writing_policy": {
                    "version": "controlled_writing_layer_v2",
                    "abstract": {"minimum_paragraphs": 4},
                    "problem_restatement": {},
                    "formula_derivation": {},
                    "symbol_policy": {},
                    "figure_placement": {},
                    "reference_policy": {},
                    "limitation_policy": {},
                    "result_acceptance_contract": {
                        "result_cards": [],
                        "answer_cards": [{
                            "task_id": "layout",
                            "question_requirement": "给出配置方案",
                            "paper_output": "综上得到方案",
                            "representative_value": "配置方案",
                            "paper_location": "question_content_layout",
                            "answer_marker": "综上得到方案",
                            "answer_type": "strategy",
                            "source_bindings": ["evidence/layout"],
                            "result_card_ids": [],
                        }],
                    },
                    "appendix_code_mode": "PENDING_USER_POLICY",
                },
            }
            report = validate_writing_policy(payload, content_root, content)
            self.assertEqual(report["status"], "WARNING")
            self.assertEqual(report["task_contract"]["tasks"][0]["label"], "空间配置")
            self.assertEqual(report["appendix_code_policy"], "PENDING_USER_POLICY")

    def test_required_limitation_is_a_hard_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content_root = root / "content"
            content_root.mkdir()
            for name in ("abstract", "problem_restatement", "problem_analysis_q1", "references", "title", "authors", "keywords", "model_assumptions", "model_assumptions_note", "notation", "appendix"):
                (content_root / f"{name}.tex").write_text(r"题目见\cite{official}。" if name == "problem_restatement" else (r"\bibitem{official} 官方题面。" if name == "references" else "正文。"), encoding="utf-8")
            (content_root / "question_content_q1.json").write_text(json.dumps({"intro": "正文", "sections": [{"title": "标题", "content": "正文"}]}), encoding="utf-8")
            (content_root / "model_evaluation.json").write_text(json.dumps({"strengths": "正文", "weaknesses": "正文", "improvements": "正文"}), encoding="utf-8")
            content = {path.stem: path.name for path in content_root.iterdir()}
            payload = {
                "question_count": 1,
                "manifest_mode": "LEGACY_COMPATIBILITY",
                "content": content,
                "writing_policy": {
                    "version": "controlled_writing_layer_v2",
                    "limitation_policy": {"limitations": [{"limitation_id": "human_limit", "content_keys": ["model_evaluation"], "markers": ["人体可执行性"], "required": True}]},
                    "appendix_code_mode": "PENDING_USER_POLICY",
                },
            }
            with self.assertRaisesRegex(WritingPolicyError, "LIMITATION_PROPAGATION_MISSING"):
                validate_writing_policy(payload, content_root, content)

    def test_result_card_must_be_visible_with_value_and_unit_in_declared_paper_location(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content_root = root / "content"
            content_root.mkdir()
            (content_root / "question_content_q1.json").write_text(
                json.dumps({"intro": "结果为 80 N。", "sections": []}, ensure_ascii=False), encoding="utf-8"
            )
            content = {"question_content_q1": "question_content_q1.json"}
            payload = {
                "question_count": 1,
                "manifest_mode": "CURRENT_FORMAL",
                "task_contract": {"tasks": [{
                    "task_id": "q1",
                    "label": "问题一",
                    "analysis_content_key": "question_content_q1",
                    "content_key": "question_content_q1",
                }]},
                "content": content,
                "writing_policy": {
                    "version": "controlled_writing_layer_v2",
                    "result_acceptance_contract": {
                        "result_cards": [{
                            "result_id": "r1", "display_value": "80", "unit": "N", "source_binding": "output/x.json#r1",
                            "paper_location": "question_content_q1", "approved": True, "scenario": {
                                "result_source": "output/x.json", "e": "0.8", "duration": "0.10 s", "scenario_id": "s1",
                                "player_combination": "1,2", "direction_angle": "12 deg", "mirror": "1",
                            },
                        }],
                        "answer_cards": [{
                            "task_id": "q1", "question_requirement": "给出力度", "paper_output": "80N", "representative_value": "80 N",
                            "paper_location": "question_content_q1", "answer_marker": "结果为 80 N",
                            "answer_type": "quantitative", "result_card_ids": ["r1"],
                        }],
                        "limitation_impacts": [{
                            "limitation_id": "l1", "assumption_or_boundary": "条件", "affected_quantities": "力", "conditional_results": "条件结果",
                        }],
                    },
                    "appendix_code_mode": "PENDING_USER_POLICY",
                },
            }
            self.assertEqual(validate_writing_policy(payload, content_root, content)["result_acceptance"]["status"], "PASS")
            payload["writing_policy"]["result_acceptance_contract"]["result_cards"][0]["unit"] = "kN"
            with self.assertRaisesRegex(WritingPolicyError, "RESULT_PAPER_BINDING_MISSING"):
                validate_writing_policy(payload, content_root, content)


if __name__ == "__main__":
    unittest.main()
