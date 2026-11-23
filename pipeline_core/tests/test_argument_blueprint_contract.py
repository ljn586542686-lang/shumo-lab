from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pipeline_core.paper_quality.argument_blueprint_contract import (
    ArgumentBlueprintContractError,
    TRACE_VERSION,
    validate_argument_blueprint_contract,
    validate_argument_blueprint_trace_report,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ArgumentBlueprintContractTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[dict, dict, dict, dict, dict[str, str]]:
        content_root = root / "content"
        content_root.mkdir()
        (content_root / "analysis_q1.tex").write_text("研究发射点 A 与接收点 B 的定位关系。", encoding="utf-8")
        (content_root / "analysis_q2.tex").write_text("研究候选方案 C。", encoding="utf-8")
        q1 = {
            "intro": "定位矛盾在于只观测夹角而未知位置。先建立 A 与 B 的距离关系。",
            "sections": [{"title": "求解", "content": "由上一关系计算，位置结果为 10 m。若夹角退化则该关系失效。"}],
            "derivation_blocks": [{
                "title": "定位模型",
                "method_selection_basis": "利用 A 与 B 的几何关系。",
                "physical_explanation": "距离连接两个研究对象。",
                "assumptions_and_boundary": "假设观测角有效。",
                "variables": [{"symbol": "x", "definition": "接收点位置"}],
                "steps": [{"equation_label": "eq:q1_distance", "formula_tex": "x=10", "explanation": "式中 x 表示接收点位置。"}],
            }],
        }
        q2 = {
            "intro": "沿用问题一的位置结果为 10 m，对候选方案 C 进行筛选。",
            "sections": [{"title": "结论", "content": "最终方案采用 A。"}],
            "derivation_blocks": [],
        }
        for name, payload in (("q1.json", q1), ("q2.json", q2)):
            (content_root / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        abstract = (
            "本文研究定位与方案筛选问题。\n\n"
            "问题一利用 A 与 B 的距离关系，位置结果为 10 m。\n\n"
            "问题二沿用位置结果筛选候选方案 C，最终方案采用 A。\n\n"
            "综上，两项任务形成从定位到方案选择的闭环。"
        )
        (content_root / "abstract.tex").write_text(abstract, encoding="utf-8")
        content = {
            "abstract": "abstract.tex",
            "problem_analysis_q1": "analysis_q1.tex",
            "problem_analysis_q2": "analysis_q2.tex",
            "question_content_q1": "q1.json",
            "question_content_q2": "q2.json",
        }
        task_contract = {"tasks": [
            {"task_id": "q1", "label": "问题一", "analysis_content_key": "problem_analysis_q1", "content_key": "question_content_q1"},
            {"task_id": "q2", "label": "问题二", "analysis_content_key": "problem_analysis_q2", "content_key": "question_content_q2"},
        ]}
        results = {
            "result_cards": [
                {"result_id": "r1"},
                {"result_id": "r2"},
            ],
            "answer_cards": [
                {"task_id": "q1", "question_requirement": "确定位置", "answer_marker": "位置结果为 10 m", "result_card_ids": ["r1"], "source_bindings": ["output/q1.json#r1"]},
                {"task_id": "q2", "question_requirement": "给出方案", "answer_marker": "最终方案采用 A", "result_card_ids": ["r2"], "source_bindings": ["output/q2.json#r2"]},
            ],
        }
        evidence = {
            "model_blocks": [
                {"model_id": "m1", "task_id": "q1", "assumption_ids": ["a1"]},
                {"model_id": "m2", "task_id": "q2", "assumption_ids": []},
            ],
            "assumption_ledger": [{"assumption_id": "a1"}],
        }
        blueprint = {
            "version": "argument_blueprint_contract_v1",
            "repair_policy": {"max_automatic_retries": 2, "on_exhaustion": "HUMAN_REVIEW_REQUIRED", "surface_rewrite_forbidden": True},
            "tasks": [
                {
                    "task_id": "q1",
                    "complexity_mode": "FULL",
                    "research_objects": [
                        {"object_id": "obj_a", "public_name": "发射点 A", "public_markers": ["发射点 A"]},
                        {"object_id": "obj_b", "public_name": "接收点 B", "public_markers": ["接收点 B"]},
                    ],
                    "model_ids": ["m1"],
                    "key_relations": [{"relation_id": "rel_distance", "object_ids": ["obj_a", "obj_b"], "equation_labels": ["eq:q1_distance"], "public_marker": "A 与 B 的距离关系"}],
                    "equation_meanings": [{"equation_label": "eq:q1_distance", "object_ids": ["obj_b"], "meaning_marker": "x 表示接收点位置"}],
                    "assumption_status": "LINKED",
                    "assumption_links": [{"assumption_id": "a1", "equation_labels": ["eq:q1_distance"], "relation_ids": [], "public_marker": "假设观测角有效"}],
                    "result_answer": {"question_requirement_quote": "确定位置", "answer_marker": "位置结果为 10 m", "result_card_ids": ["r1"], "source_bindings": ["output/q1.json#r1"]},
                    "task_conflict": {"question_requirement_quote": "确定位置", "public_marker": "只观测夹角而未知位置"},
                    "upstream_dependency": {"status": "NONE", "records": []},
                    "failure_modes": [{"failure_id": "f1", "assumption_ids": ["a1"], "equation_labels": ["eq:q1_distance"], "relation_ids": [], "public_marker": "夹角退化则该关系失效"}],
                    "reasoning_path": {"mode": "CUSTOM", "steps": [
                        {"step_id": "s1", "public_marker": "先建立 A 与 B 的距离关系", "links": {"object_ids": ["obj_a", "obj_b"], "relation_ids": ["rel_distance"], "equation_labels": ["eq:q1_distance"], "result_card_ids": [], "model_ids": ["m1"]}},
                        {"step_id": "s2", "public_marker": "位置结果为 10 m", "links": {"object_ids": ["obj_b"], "relation_ids": [], "equation_labels": [], "result_card_ids": ["r1"], "model_ids": []}},
                    ]},
                },
                {
                    "task_id": "q2",
                    "complexity_mode": "SIMPLE",
                    "simple_mode_reason": "该问只需利用上游结果完成有限方案选择。",
                    "research_objects": [{"object_id": "obj_c", "public_name": "候选方案 C", "public_markers": ["候选方案 C"]}],
                    "model_ids": ["m2"],
                    "key_relations": [],
                    "equation_meanings": [],
                    "equation_not_applicable_reason": "该问不新增数学公式。",
                    "assumption_status": "NONE_DECLARED",
                    "assumption_links": [],
                    "result_answer": {"question_requirement_quote": "给出方案", "answer_marker": "最终方案采用 A", "result_card_ids": ["r2"], "source_bindings": ["output/q2.json#r2"]},
                    "upstream_dependency": {"status": "DECLARED", "records": [{"from_task_id": "q1", "result_card_ids": ["r1"], "public_marker": "沿用问题一的位置结果为 10 m"}]},
                    "failure_modes": [],
                    "failure_mode_not_applicable_reason": "算例只执行确定性筛选。",
                    "reasoning_path": {"mode": "MIXED", "steps": [{"step_id": "s1", "public_marker": "对候选方案 C 进行筛选", "links": {"object_ids": ["obj_c"], "relation_ids": [], "equation_labels": [], "result_card_ids": ["r1", "r2"], "model_ids": ["m2"]}}]},
                },
            ],
            "symbol_consistency_review": {"status": "DECLARED_CONSISTENT", "symbols": ["x"]},
            "abstract_task_bindings": [
                {"task_id": "q1", "problem_marker": "问题一", "method_marker": "A 与 B 的距离关系", "result_marker": "位置结果为 10 m", "result_card_ids": ["r1"]},
                {"task_id": "q2", "problem_marker": "问题二", "method_marker": "筛选候选方案 C", "result_marker": "最终方案采用 A", "result_card_ids": ["r2"]},
            ],
            "abstract_synthesis_marker": "形成从定位到方案选择的闭环",
        }
        return blueprint, task_contract, results, evidence, content

    def _validate(self, root: Path, blueprint: dict, task_contract: dict, results: dict, evidence: dict, content: dict[str, str]) -> dict:
        return validate_argument_blueprint_contract(
            blueprint,
            task_contract=task_contract,
            result_acceptance_contract=results,
            paper_evidence_contract=evidence,
            content_root=root / "content",
            content=content,
        )

    def test_full_custom_and_simple_upstream_blueprints_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = self._fixture(root)
            report = self._validate(root, *fixture)
            self.assertEqual(report["status"], "PASS")
            self.assertFalse(report["semantic_quality_proof_claimed"])
            self.assertFalse(report["automatic_naturalness_judgment_claimed"])

    def test_marker_declared_only_in_blueprint_cannot_bypass_visibility(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            blueprint, task_contract, results, evidence, content = self._fixture(root)
            blueprint["tasks"][0]["task_conflict"]["public_marker"] = "正文不存在的冲突标记"
            report = self._validate(root, blueprint, task_contract, results, evidence, content)
            self.assertIn("ARGUMENT_BLUEPRINT_TASK_CONFLICT_NOT_VISIBLE", {item["code"] for item in report["hard_failures"]})

    def test_missing_assumption_link_and_false_upstream_are_hard_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            blueprint, task_contract, results, evidence, content = self._fixture(root)
            blueprint["tasks"][0]["assumption_links"] = []
            blueprint["tasks"][1]["upstream_dependency"]["records"][0]["from_task_id"] = "q2"
            report = self._validate(root, blueprint, task_contract, results, evidence, content)
            codes = {item["code"] for item in report["hard_failures"]}
            self.assertIn("ARGUMENT_BLUEPRINT_ASSUMPTION_COVERAGE_INCOMPLETE", codes)
            self.assertIn("ARGUMENT_BLUEPRINT_UPSTREAM_TASK_INVALID", codes)

    def test_equation_mapping_and_result_question_binding_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            blueprint, task_contract, results, evidence, content = self._fixture(root)
            blueprint["tasks"][0]["equation_meanings"] = []
            blueprint["tasks"][0]["result_answer"]["question_requirement_quote"] = "通用定位任务"
            report = self._validate(root, blueprint, task_contract, results, evidence, content)
            codes = {item["code"] for item in report["hard_failures"]}
            self.assertIn("ARGUMENT_BLUEPRINT_EQUATION_MAPPING_INCOMPLETE", codes)
            self.assertIn("ARGUMENT_BLUEPRINT_QUESTION_REQUIREMENT_MISMATCH", codes)

    def test_repair_loop_is_bounded_and_requires_human_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            blueprint, task_contract, results, evidence, content = self._fixture(root)
            blueprint["repair_policy"]["max_automatic_retries"] = 3
            with self.assertRaisesRegex(ArgumentBlueprintContractError, "ARGUMENT_BLUEPRINT_RETRY_LIMIT_INVALID"):
                self._validate(root, blueprint, task_contract, results, evidence, content)

    def test_trace_binds_main_tex_and_source_manifest_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case_root = Path(directory)
            paper_root = case_root / "paper"
            paper_root.mkdir()
            main = paper_root / "main.tex"
            manifest = paper_root / "writer_manifest.json"
            main.write_text("paper", encoding="utf-8")
            manifest.write_text("{}", encoding="utf-8")
            trace = {
                "schema_version": TRACE_VERSION,
                "generated_by": "paper_renderer",
                "status": "PASS",
                "policy_version": "controlled_writing_layer_v5",
                "manifest_mode": "CURRENT_FORMAL",
                "argument_blueprint": {"status": "PASS", "contract_version": "argument_blueprint_contract_v1", "task_count": 2},
                "main_tex_sha256": _sha256(main),
                "source_manifest": {"path": "paper/writer_manifest.json", "sha256": _sha256(manifest)},
            }
            self.assertEqual(validate_argument_blueprint_trace_report(trace, paper_root)["status"], "PASS")
            main.write_text("tampered", encoding="utf-8")
            report = validate_argument_blueprint_trace_report(trace, paper_root)
            self.assertEqual(report["status"], "FAIL")
            self.assertIn("ARGUMENT_BLUEPRINT_TRACE_MAIN_TEX_HASH_MISMATCH", report["failures"])


if __name__ == "__main__":
    unittest.main()
