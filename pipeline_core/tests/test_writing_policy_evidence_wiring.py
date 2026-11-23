from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipeline_core.paper_quality.writing_policy import WritingPolicyError, validate_writing_policy


def _content(root: Path) -> dict[str, str]:
    root.mkdir()
    (root / "question_content_task.json").write_text(
        json.dumps({"intro": "目标和约束均可量化。由8降至7，损失降低。仅在给定条件下成立。strategy conclusion。\\cite{zh_method}", "sections": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "references.tex").write_text("\\bibitem{zh_method} 中文方法文献。", encoding="utf-8")
    (root / "problem_analysis_task.tex").write_text("分析。", encoding="utf-8")
    (root / "figure.m").write_text("% MATLAB", encoding="utf-8")
    (root / "figure.png").write_bytes(b"not-a-real-image")
    return {"question_content_task": "question_content_task.json", "problem_analysis_task": "problem_analysis_task.tex", "references": "references.tex"}


class WritingPolicyEvidenceWiringTests(unittest.TestCase):
    def test_formal_matlab_figure_requires_script_asset_and_backend(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "content"
            content = _content(root)
            payload = {"manifest_mode": "CURRENT_FORMAL", "task_contract": {"tasks": [{"task_id": "task", "label": "任务", "analysis_content_key": "problem_analysis_task", "content_key": "question_content_task"}]}, "content": content,
                "writing_policy": {"version": "controlled_writing_layer_v2", "result_acceptance_contract": {"result_cards": [], "answer_cards": [{
                    "task_id": "task", "question_requirement": "给出策略", "paper_output": "strategy conclusion", "representative_value": "strategy conclusion",
                    "paper_location": "question_content_task", "answer_marker": "strategy conclusion", "answer_type": "strategy", "source_bindings": ["evidence/task"], "result_card_ids": [],
                }], "limitation_impacts": []}, "figure_placement": {"formal_figure_backend": "MATLAB", "figures": [{
                    "figure_id": "f1", "formal": True, "render_backend": "PYTHON", "matlab_script_path": "figure.m", "asset_path": "figure.png",
                    "public_title": "比较", "source_binding": "output/x", "first_discussion": "正文",
                    "matlab_figure_spec": {
                        "figure_id": "f1", "backend": "MATLAB", "figure_type": "line", "purpose": "comparison",
                        "title": "比较", "x_label": "x", "y_label": "y", "output_file": "figure.png",
                        "source_binding": {"source_id": "r1", "source_type": "result_card", "sha256": "a" * 64},
                        "series": [{"name": "s", "x": [1, 2], "y": [2, 3]}],
                    },
                }]}, "appendix_code_mode": "PENDING_USER_POLICY"}}
            with self.assertRaisesRegex(WritingPolicyError, "FORMAL_FIGURE_BACKEND_MISMATCH"):
                validate_writing_policy(payload, root, content)
            payload["writing_policy"]["figure_placement"]["figures"][0]["render_backend"] = "MATLAB"
            self.assertEqual(validate_writing_policy(payload, root, content)["figure_placement"]["status"], "PASS")

    def test_model_evidence_and_literature_are_bound_to_public_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "content"
            content = _content(root)
            results = {"result_cards": [{"result_id": "r1", "display_value": "7", "unit": "m", "source_binding": "out#r1", "paper_location": "question_content_task", "approved": True, "scenario": {"result_source": "out", "e": "na", "duration": "na", "scenario_id": "A", "player_combination": "na", "direction_angle": "na", "mirror": "na"}}], "answer_cards": [{"task_id": "task", "question_requirement": "最小化", "paper_output": "7m", "representative_value": "7m", "paper_location": "question_content_task", "answer_marker": "7", "answer_type": "quantitative", "result_card_ids": ["r1"]}], "limitation_impacts": [{"limitation_id": "l1", "assumption_or_boundary": "条件", "affected_quantities": "损失", "conditional_results": "条件结果"}]}
            evidence = {"model_blocks": [{"model_id": "m1", "task_id": "task", "paper_location": "question_content_task", "model_goal": "最小化", "method_selection_basis": "目标和约束均可量化", "variables": [{"symbol": "x", "definition": "变量"}], "derivation_steps": [{"formula_tex": "f(x)", "explanation": "目标"}], "physical_meaning": "损失", "boundary_marker": "仅在给定条件下成立", "result_card_ids": ["r1"], "assumption_ids": ["a1"]}], "assumption_ledger": [{"assumption_id": "a1", "statement": "近似", "basis": "量级", "simplification": "一阶", "valid_domain": "小扰动", "limitation_id": "l1", "affected_result_card_ids": ["r1"]}], "claim_evidence_cards": [{"claim_id": "c1", "claim": "损失降低", "paper_location": "question_content_task", "claim_marker": "损失降低", "result_card_ids": ["r1"], "model_ids": ["m1"], "assumption_ids": ["a1"]}]}
            literature = {"external_evidence_required": True, "records": [{"record_id": "method", "citation_key": "zh_method", "source_type": "method", "language": "zh", "knowledge_gap": "方法", "decision_influenced": "验证", "citation_reason": "误差依据", "competition_suitability": "可简洁解释", "paper_location": "question_content_task", "human_review_status": "APPROVED"}]}
            payload = {"task_contract": {"tasks": [{"task_id": "task", "label": "任务", "analysis_content_key": "problem_analysis_task", "content_key": "question_content_task"}]}, "content": content, "writing_policy": {"version": "controlled_writing_layer_v2", "result_acceptance_contract": results, "paper_evidence_contract": evidence, "literature_usage_contract": literature, "appendix_code_mode": "PENDING_USER_POLICY"}}
            report = validate_writing_policy(payload, root, content)
            self.assertNotEqual(report["status"], "FAIL")
            self.assertEqual(report["paper_evidence"]["status"], "PASS")
            self.assertEqual(report["literature_usage"]["status"], "WARNING")


if __name__ == "__main__":
    unittest.main()
