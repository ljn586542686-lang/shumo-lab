from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from pipeline_core.paper_quality.matlab_figure_pipeline import validate_figure_spec
from pipeline_core.paper_quality.visual_reasoning_contract import (
    validate_visual_reasoning_contract,
    validate_visual_reasoning_trace_report,
)


class VisualReasoningContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        question = {
            "intro": "先画出发射机、接收机与观测角的实际几何关系。定角圆弧适合把方向夹角转化为可求交的轨迹。",
            "derivation_blocks": [
                {
                    "title": "几何关系",
                    "steps": [
                        {
                            "formula_tex": "x^2+y^2=R^2",
                            "explanation": "由定角关系得到候选圆弧。",
                            "equation_label": "eq:arc",
                        }
                    ],
                }
            ],
            "sections": [
                {
                    "title": "候选交会与筛选",
                    "content": (
                        "由式中圆弧交会得到两个候选点。候选距离比较后确定最终位置。"
                        "图中的弦、圆周角与候选圆弧对应式中半径关系。"
                        "圆弧结构解释候选为何成对出现。枚举投影展示候选如何分离。"
                        "结果对比显示筛选规则的作用。残差诊断确认最终位置满足约束。"
                    ),
                }
            ],
            "closing_optional": "",
        }
        (self.root / "question.json").write_text(
            json.dumps(question, ensure_ascii=False), encoding="utf-8"
        )
        self.task_contract = {
            "tasks": [
                {
                    "task_id": "q1",
                    "label": "问题一",
                    "analysis_content_key": "analysis_q1",
                    "content_key": "question_q1",
                }
            ]
        }
        self.content = {"question_q1": "question.json"}
        self.figures = {
            "figures": [
                {
                    "figure_id": "q1_arc", "task_id": "q1", "reasoning_chain_id": "chain_q1",
                    "reasoning_role": "MODEL_ENTRY_EXPLANATION", "first_discussion": "候选交会与筛选",
                    "reader_question": "候选点为什么成对出现？", "public_takeaway": "圆弧结构解释候选为何成对出现",
                    "placement_slot": "TASK_MODEL_ENTRY",
                    "task_visual_family": "SPATIAL_GEOMETRY",
                    "explanatory_form": "GEOMETRIC_CONFIGURATION",
                    "object_bindings": ["A", "B", "X", "alpha"],
                    "matlab_figure_spec": {
                        "figure_type": "geometric_schematic",
                        "panels": [{
                            "objects": [
                                {"object_id": "A"},
                                {"object_id": "B"},
                                {"object_id": "X"},
                                {"object_id": "alpha"},
                            ]
                        }],
                    },
                    "entry_lead": "先画出发射机、接收机与观测角的实际几何关系",
                    "model_choice_reason": "定角圆弧适合把方向夹角转化为可求交的轨迹",
                    "relation_statement": "图中的弦、圆周角与候选圆弧对应式中半径关系",
                    "logic_links": {"model_ids": ["m_q1"], "equation_labels": ["eq:arc"], "intermediate_result_ids": ["q1_candidates"], "final_result_ids": []},
                },
                {
                    "figure_id": "q1_projection", "task_id": "q1", "reasoning_chain_id": "chain_q1",
                    "reasoning_role": "ENUMERATION_PROJECTION", "first_discussion": "候选交会与筛选",
                    "reader_question": "枚举后候选如何分离？", "public_takeaway": "枚举投影展示候选如何分离",
                    "logic_links": {"model_ids": [], "equation_labels": [], "intermediate_result_ids": ["q1_candidates"], "final_result_ids": ["q1_selected"]},
                },
                {
                    "figure_id": "q1_comparison", "task_id": "q1", "reasoning_chain_id": "chain_q1",
                    "reasoning_role": "COMPARISON", "first_discussion": "候选交会与筛选",
                    "reader_question": "筛选规则是否改变结论？", "public_takeaway": "结果对比显示筛选规则的作用",
                    "logic_links": {"model_ids": [], "equation_labels": [], "intermediate_result_ids": ["q1_candidates"], "final_result_ids": ["q1_selected"]},
                },
                {
                    "figure_id": "q1_residual", "task_id": "q1", "reasoning_chain_id": "chain_q1",
                    "reasoning_role": "RESIDUAL_VECTOR", "first_discussion": "候选交会与筛选",
                    "reader_question": "最终位置是否满足约束？", "public_takeaway": "残差诊断确认最终位置满足约束",
                    "logic_links": {"model_ids": ["m_q1"], "equation_labels": [], "intermediate_result_ids": [], "final_result_ids": ["q1_selected"]},
                },
            ]
        }
        self.results = {
            "result_cards": [
                {"result_id": "q1_candidates"},
                {"result_id": "q1_selected"},
            ]
        }
        self.evidence = {
            "model_blocks": [{"model_id": "m_q1", "task_id": "q1"}]
        }
        self.contract = {
            "version": "visual_reasoning_contract_v1",
            "display_profile": "STRONG_EVIDENCE_STORY",
            "task_chains": [
                {
                    "chain_id": "chain_q1",
                    "task_id": "q1",
                    "model_ids": ["m_q1"],
                    "equation_labels": ["eq:arc"],
                    "intermediate_result_ids": ["q1_candidates"],
                    "final_result_ids": ["q1_selected"],
                    "visual_ids": ["q1_arc", "q1_projection", "q1_comparison", "q1_residual"],
                    "model_to_intermediate_marker": "圆弧交会得到两个候选点",
                    "intermediate_to_result_marker": "候选距离比较后确定最终位置",
                }
            ],
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _validate(self) -> dict[str, object]:
        return validate_visual_reasoning_contract(
            self.contract,
            task_contract=self.task_contract,
            figure_placement=self.figures,
            result_acceptance_contract=self.results,
            paper_evidence_contract=self.evidence,
            content_root=self.root,
            content=self.content,
        )

    def test_complete_model_intermediate_result_visual_chain_passes(self) -> None:
        report = self._validate()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["task_count"], 1)
        self.assertEqual(report["formal_figure_count"], 4)
        self.assertEqual(
            set(report["role_families_covered"]),
            {"MODEL_STRUCTURE", "INTERMEDIATE_PROCESS", "RESULT_DIAGNOSTIC"},
        )

    def test_missing_intermediate_state_is_a_hard_failure(self) -> None:
        self.contract["task_chains"][0]["intermediate_result_ids"] = []
        report = self._validate()
        self.assertEqual(report["status"], "FAIL")
        self.assertIn(
            "VISUAL_REASONING_INTERMEDIATE_EVIDENCE_REQUIRED",
            {item["code"] for item in report["hard_failures"]},
        )

    def test_decorative_or_wrong_task_figure_cannot_satisfy_contract(self) -> None:
        self.figures["figures"][0].pop("reasoning_role")
        self.figures["figures"][0]["task_id"] = "q2"
        report = self._validate()
        codes = {item["code"] for item in report["hard_failures"]}
        self.assertIn("VISUAL_REASONING_ROLE_INVALID", codes)
        self.assertIn("VISUAL_REASONING_FIGURE_TASK_MISMATCH", codes)

    def test_formula_and_public_bridge_must_exist_in_the_task(self) -> None:
        self.contract["task_chains"][0]["equation_labels"] = ["eq:not_present"]
        self.contract["task_chains"][0]["intermediate_to_result_marker"] = "不存在的解释"
        report = self._validate()
        codes = {item["code"] for item in report["hard_failures"]}
        self.assertIn("VISUAL_REASONING_EQUATION_UNKNOWN", codes)
        self.assertIn("VISUAL_REASONING_INTERMEDIATE_TO_RESULT_NOT_VISIBLE", codes)

    def test_model_entry_figure_without_process_or_diagnostic_support_is_not_enough(self) -> None:
        self.figures["figures"] = self.figures["figures"][:1]
        self.contract["task_chains"][0]["visual_ids"] = ["q1_arc"]
        report = self._validate()
        self.assertEqual(report["status"], "FAIL")
        self.assertIn(
            "VISUAL_REASONING_STRONG_FIGURE_COUNT_NOT_MET",
            {item["code"] for item in report["hard_failures"]},
        )

    def test_all_three_visual_role_families_are_required(self) -> None:
        self.figures["figures"][1]["reasoning_role"] = "COMPARISON"
        report = self._validate()
        self.assertEqual(report["status"], "FAIL")
        self.assertIn(
            "VISUAL_REASONING_ROLE_FAMILY_MISSING",
            {item["code"] for item in report["hard_failures"]},
        )

    def test_generic_flowchart_cannot_replace_the_problem_relation_figure(self) -> None:
        self.figures["figures"][0]["explanatory_form"] = "GENERIC_FLOWCHART"
        report = self._validate()
        self.assertEqual(report["status"], "FAIL")
        self.assertIn(
            "VISUAL_REASONING_MODEL_ENTRY_FORM_INVALID",
            {item["code"] for item in report["hard_failures"]},
        )

    def test_scatter_plot_cannot_be_mislabeled_as_a_geometric_model_entry(self) -> None:
        self.figures["figures"][0]["matlab_figure_spec"]["figure_type"] = "scatter"
        report = self._validate()
        self.assertEqual(report["status"], "FAIL")
        self.assertIn(
            "VISUAL_REASONING_MODEL_ENTRY_FORM_FIGURE_MISMATCH",
            {item["code"] for item in report["hard_failures"]},
        )

    def test_model_entry_visual_family_is_required(self) -> None:
        self.figures["figures"][0].pop("task_visual_family")
        report = self._validate()
        self.assertEqual(report["status"], "FAIL")
        self.assertIn(
            "VISUAL_REASONING_MODEL_ENTRY_VISUAL_FAMILY_REQUIRED",
            {item["code"] for item in report["hard_failures"]},
        )

    def test_model_entry_visual_archetypes_transfer_across_problem_families(self) -> None:
        base = {
            "backend": "MATLAB",
            "purpose": "model_explanation",
            "title": "模型入口关系图",
            "x_label": "横轴变量",
            "y_label": "纵轴变量",
            "source_binding": {"source_id": "q1_selected", "source_type": "result_card", "sha256": "a" * 64},
            "dpi": 400,
        }

        def full_spec(figure_type: str, **fields: object) -> dict[str, object]:
            return {
                **base,
                "figure_id": f"migration_{figure_type}",
                "figure_type": figure_type,
                "output_file": f"figures/migration_{figure_type}.png",
                **fields,
            }

        geometry = full_spec(
            "geometric_schematic",
            panels=[{
                "title": "对象关系",
                "objects": [
                    {"object_id": "A", "label": "节点 A", "x": 0, "y": 0},
                    {"object_id": "X", "label": "节点 X", "x": 1, "y": 1},
                ],
                "relations": [{"kind": "segment", "from": "A", "to": "X", "label": "关系"}],
            }],
        )
        network = full_spec(
            "geometric_schematic",
            panels=[{
                "title": "网络拓扑",
                "objects": [
                    {"object_id": "source", "label": "源点", "x": 0, "y": 0},
                    {"object_id": "sink", "label": "汇点", "x": 2, "y": 0},
                ],
                "relations": [{"kind": "arrow", "from": "source", "to": "sink", "label": "流向"}],
            }],
        )
        cases = [
            (
                "SPATIAL_GEOMETRY", "GEOMETRIC_CONFIGURATION",
                geometry,
                ["A", "X"],
            ),
            (
                "NETWORK_STRUCTURE", "NETWORK_TOPOLOGY",
                network,
                ["source", "sink"],
            ),
            (
                "DYNAMIC_PROCESS", "TEMPORAL_DYNAMICS",
                full_spec("trajectory_2d", x_label="时间 / s", y_label="状态量", x=[0, 1, 2], y=[0, 0.5, 1]),
                ["时间 / s", "状态量"],
            ),
            (
                "DATA_PATTERN", "DATA_RELATION",
                full_spec("scatter", x_label="特征一", y_label="响应值", series=[{"name": "样本", "x": [1, 2], "y": [2, 3]}]),
                ["特征一", "响应值"],
            ),
            (
                "OPTIMIZATION_DECISION", "FEASIBLE_REGION",
                full_spec("contour", x_label="决策量一", y_label="决策量二", colorbar_label="目标函数", x=[0, 1], y=[0, 1], z=[[1, 2], [2, 4]]),
                ["决策量一", "目标函数"],
            ),
            (
                "SCHEDULING_RESOURCE", "RESOURCE_SCHEDULE",
                full_spec("interval_timeline", lanes=[
                    {"lane_id": "machine_a", "label": "设备 A", "intervals": [{"start": 0, "end": 2, "label": "任务一"}]},
                    {"lane_id": "machine_b", "label": "设备 B", "intervals": [{"start": 1, "end": 3, "label": "任务二"}]},
                ]),
                ["machine_a", "machine_b"],
            ),
            (
                "STATISTICAL_UNCERTAINTY", "UNCERTAINTY_STRUCTURE",
                full_spec("uncertainty_band", x_label="预测时刻", y_label="预测区间", x=[1, 2], center=[2, 3], lower=[1, 2], upper=[3, 4]),
                ["预测时刻", "预测区间"],
            ),
            (
                "EVALUATION_COMPARISON", "INDICATOR_RELATION",
                full_spec("grouped_bar", categories=["方案甲", "方案乙"], series=[{"name": "综合得分", "values": [8, 9]}]),
                ["方案甲", "综合得分"],
            ),
        ]
        for family, form, spec, bindings in cases:
            with self.subTest(family=family):
                self.assertEqual(validate_figure_spec(spec)["figure_type"], spec["figure_type"])
                entry = self.figures["figures"][0]
                entry["task_visual_family"] = family
                entry["explanatory_form"] = form
                entry["matlab_figure_spec"] = spec
                entry["object_bindings"] = bindings
                report = self._validate()
                self.assertEqual(report["status"], "PASS", report.get("hard_failures"))

    def test_family_type_mismatch_fails_closed(self) -> None:
        entry = self.figures["figures"][0]
        entry["task_visual_family"] = "SCHEDULING_RESOURCE"
        entry["explanatory_form"] = "RESOURCE_SCHEDULE"
        report = self._validate()
        self.assertEqual(report["status"], "FAIL")
        self.assertIn(
            "VISUAL_REASONING_MODEL_ENTRY_FIGURE_FAMILY_MISMATCH",
            {item["code"] for item in report["hard_failures"]},
        )

    def test_problem_relation_figure_must_be_placed_before_derivation(self) -> None:
        self.figures["figures"][0]["placement_slot"] = "SECTION_AFTER_DISCUSSION"
        report = self._validate()
        self.assertEqual(report["status"], "FAIL")
        self.assertIn(
            "VISUAL_REASONING_MODEL_ENTRY_PLACEMENT_REQUIRED",
            {item["code"] for item in report["hard_failures"]},
        )

    def test_renderer_trace_revalidates_main_tex_and_figure_hashes(self) -> None:
        paper = self.root / "paper"
        figure_root = paper / "writer_content" / "figures"
        figure_root.mkdir(parents=True)
        main = paper / "main.tex"
        main.write_text("paper", encoding="utf-8")
        figures = []
        for index in range(1, 5):
            figure = figure_root / f"q1_{index}.png"
            figure.write_bytes(f"figure-{index}".encode("utf-8"))
            figures.append(figure)
        payload = {
            "schema_version": "visual_reasoning_trace_report_v1",
            "generated_by": "paper_renderer",
            "status": "PASS",
            "policy_version": "controlled_writing_layer_v4",
            "manifest_mode": "CURRENT_FORMAL",
            "main_tex_sha256": hashlib.sha256(main.read_bytes()).hexdigest(),
            "visual_reasoning": {
                "status": "PASS",
                "display_profile": "STRONG_EVIDENCE_STORY",
                "task_count": 1,
                "chain_count": 1,
                "minimum_formal_figure_count": 2,
                "formal_figure_count": 4,
                "role_families_covered": [
                    "MODEL_STRUCTURE",
                    "INTERMEDIATE_PROCESS",
                    "RESULT_DIAGNOSTIC",
                ],
                "task_chains": [{
                    "task_id": "q1",
                    "visual_ids": ["q1_1", "q1_2", "q1_3", "q1_4"],
                }],
            },
            "figure_assets": [
                {
                    "figure_id": f"q1_{index}",
                    "path": f"writer_content/figures/q1_{index}.png",
                    "sha256": hashlib.sha256(figure.read_bytes()).hexdigest(),
                }
                for index, figure in enumerate(figures, 1)
            ],
        }
        self.assertEqual(validate_visual_reasoning_trace_report(payload, paper)["status"], "PASS")
        figures[0].write_bytes(b"tampered")
        report = validate_visual_reasoning_trace_report(payload, paper)
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("VISUAL_REASONING_ASSET_HASH_MISMATCH:q1_1", report["failures"])


if __name__ == "__main__":
    unittest.main()
