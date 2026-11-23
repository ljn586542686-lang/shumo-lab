"""Regression tests for case-isolated routing and model-route stage wiring."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from pipeline_core.python_tools.run_pipeline import run_stage
from pipeline_core.python_tools.transition_case import (
    authorize_transition,
    build_completion_receipt,
    commit_transition,
    write_completion_receipt,
    write_transition_authorization,
)
from pipeline_core.router import problem_type_router as router_module
from pipeline_core.router.problem_type_router import (
    ProblemContractBlocked,
    build_output,
    collect_case_inputs,
    prepare_model_building_handoff_inputs,
    validate_problem_contract,
    validate_model_building_handoff,
    write_case_outputs,
)
from pipeline_core.model_route.model_route_stage import (
    _xlsx_text,
    run_model_route_stage,
    validate_model_route_outputs,
)
from pipeline_core.model_route.model_route_score_writer import (
    write_model_route_score_xlsx,
)
from pipeline_core.tests.test_human_gate_decision_semantics import (
    GATE_FILES,
    write_gate_case,
)


def make_geometry_case(root: Path, *, case_id: str = "geometry_case") -> Path:
    case_dir = write_gate_case(root / case_id, 1, 0)
    (case_dir / "problem").mkdir(parents=True, exist_ok=True)
    (case_dir / "data" / "raw").mkdir(parents=True, exist_ok=True)
    (case_dir / "data" / "raw" / "problem.pdf").write_bytes(b"%PDF-test-fixture")
    (case_dir / "problem" / "problem_contract.md").write_text(
        """# Problem Contract

纯方位无源定位：接收机使用两架发射机连线之间的方向夹角定位。
圆形编队需要位置调整，并检查未知编号发射机、镜像多解和退化构型。
""",
        encoding="utf-8",
    )
    (case_dir / "problem_analysis.md").write_text(
        """# 问题分析

本题属于空间几何、反问题和编队调整。观测为无向夹角，目标含圆周与共线等距约束。
""",
        encoding="utf-8",
    )
    config = json.loads((case_dir / "case_config.json").read_text(encoding="utf-8"))
    config.update(
        {
            "case_id": case_id,
            "problem_contract_path": "problem/problem_contract.md",
            "problem_statement_files": ["data/raw/problem.pdf"],
            "attachment_files": [],
            "official_submission_mode": "locked",
            "final_submit_allowed": False,
        }
    )
    (case_dir / "case_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return case_dir


def write_route_workbook(path: Path, route_names: list[str]) -> None:
    rows = "".join(
        f'<row r="{idx}"><c r="A{idx}" t="inlineStr"><is><t>{name}</t></is></c></row>'
        for idx, name in enumerate(route_names, start=1)
    )
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""
    root_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
    workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="路线评分" sheetId="1" r:id="rId1"/></sheets></workbook>"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
    sheet = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{rows}</sheetData></worksheet>"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)


class ProblemContractPathBindingTests(unittest.TestCase):
    ROOT_MARKER = "ROOT_CONTRACT_UNIQUE_MARKER optimization planning scheduling"
    DEFAULT_MARKER = "DEFAULT_CONTRACT_UNIQUE_MARKER classification clustering"
    PLACEHOLDER_MARKER = "Status: waiting for problem statement."

    def make_case(
        self,
        root: Path,
        *,
        declare_contract_path: bool,
        contract_path: str = "",
    ) -> Path:
        case_dir = root / "contract_binding_case"
        case_dir.mkdir(parents=True)
        config: dict[str, object] = {
            "case_id": "contract_binding_case",
            "problem_statement_files": [],
        }
        if declare_contract_path:
            config["problem_contract_path"] = contract_path
        (case_dir / "case_config.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (case_dir / "problem_analysis.md").write_text(
            self.PLACEHOLDER_MARKER,
            encoding="utf-8",
        )
        return case_dir

    @staticmethod
    def write_contract(case_dir: Path, relative_path: str, content: str) -> None:
        path = case_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def checked_paths(checked: list[dict[str, object]]) -> list[str]:
        return [str(item["path"]) for item in checked]

    @staticmethod
    def build_and_capture_text(case_dir: Path) -> tuple[dict[str, object], str]:
        captured: dict[str, str] = {}
        original_compute_scores = router_module.compute_scores

        def capture_scores(text: str, json_payloads: dict, registry: dict):
            captured["text"] = text
            return original_compute_scores(text, json_payloads, registry)

        with patch.object(router_module, "compute_scores", side_effect=capture_scores):
            result = build_output(case_dir)
        return result, captured["text"]

    def test_TEST_1_EXPLICIT_ROOT_CONTRACT(self) -> None:
        with tempfile.TemporaryDirectory(prefix="explicit_root_contract_") as temp:
            case_dir = self.make_case(
                Path(temp),
                declare_contract_path=True,
                contract_path="problem_contract.md",
            )
            self.write_contract(case_dir, "problem_contract.md", self.ROOT_MARKER)

            validation = validate_problem_contract(case_dir)
            checked, text, _ = collect_case_inputs(case_dir)
            result, router_text = self.build_and_capture_text(case_dir)

            self.assertEqual(validation["status"], "PASS")
            self.assertEqual(validation["path"], "problem_contract.md")
            self.assertIn("problem_contract.md", self.checked_paths(checked))
            self.assertNotIn("problem/problem_contract.md", self.checked_paths(checked))
            self.assertIn(self.ROOT_MARKER, text)
            self.assertIn(self.ROOT_MARKER, router_text)
            self.assertEqual(result["problem_contract_validation"]["path"], "problem_contract.md")

    def test_TEST_2_LEGACY_DEFAULT_CONTRACT(self) -> None:
        with tempfile.TemporaryDirectory(prefix="legacy_default_contract_") as temp:
            case_dir = self.make_case(Path(temp), declare_contract_path=False)
            self.write_contract(case_dir, "problem/problem_contract.md", self.DEFAULT_MARKER)

            validation = validate_problem_contract(case_dir)
            checked, text, _ = collect_case_inputs(case_dir)
            result, router_text = self.build_and_capture_text(case_dir)

            self.assertEqual(validation["status"], "PASS")
            self.assertEqual(validation["path"], "problem/problem_contract.md")
            self.assertIn("problem/problem_contract.md", self.checked_paths(checked))
            self.assertIn(self.DEFAULT_MARKER, text)
            self.assertIn(self.DEFAULT_MARKER, router_text)
            self.assertEqual(result["problem_contract_validation"]["path"], "problem/problem_contract.md")

    def test_TEST_3_EXPLICIT_PATH_WINS(self) -> None:
        with tempfile.TemporaryDirectory(prefix="explicit_path_wins_") as temp:
            case_dir = self.make_case(
                Path(temp),
                declare_contract_path=True,
                contract_path="problem_contract.md",
            )
            self.write_contract(case_dir, "problem_contract.md", self.ROOT_MARKER)
            self.write_contract(case_dir, "problem/problem_contract.md", self.DEFAULT_MARKER)

            checked, text, _ = collect_case_inputs(case_dir)
            result, router_text = self.build_and_capture_text(case_dir)

            self.assertIn(self.ROOT_MARKER, text)
            self.assertNotIn(self.DEFAULT_MARKER, text)
            self.assertIn(self.ROOT_MARKER, router_text)
            self.assertNotIn(self.DEFAULT_MARKER, router_text)
            self.assertNotIn("problem/problem_contract.md", self.checked_paths(checked))
            self.assertNotIn(
                "problem/problem_contract.md",
                self.checked_paths(result["input_files_checked"]),
            )

    def test_TEST_4_DECLARED_PATH_MISSING_FAIL_CLOSED(self) -> None:
        with tempfile.TemporaryDirectory(prefix="declared_missing_contract_") as temp:
            case_dir = self.make_case(
                Path(temp),
                declare_contract_path=True,
                contract_path="problem_contract.md",
            )
            self.write_contract(case_dir, "problem/problem_contract.md", self.DEFAULT_MARKER)

            validation = validate_problem_contract(case_dir)

            self.assertEqual(validation["status"], "FAIL")
            self.assertEqual(validation["code"], "PROBLEM_CONTRACT_MISSING")
            self.assertEqual(validation["path"], "problem_contract.md")
            with self.assertRaises(ProblemContractBlocked):
                collect_case_inputs(case_dir)
            with self.assertRaises(ProblemContractBlocked):
                build_output(case_dir)

    def test_TEST_5_PATH_ESCAPE_REJECTED(self) -> None:
        with tempfile.TemporaryDirectory(prefix="escaped_contract_") as temp:
            root = Path(temp)
            case_dir = self.make_case(
                root,
                declare_contract_path=True,
                contract_path="../outside.md",
            )
            (root / "outside.md").write_text(self.ROOT_MARKER, encoding="utf-8")

            validation = validate_problem_contract(case_dir)

            self.assertEqual(validation["status"], "FAIL")
            self.assertEqual(validation["code"], "PROBLEM_CONTRACT_UNSAFE_PATH")
            with self.assertRaises(ProblemContractBlocked):
                collect_case_inputs(case_dir)
            with self.assertRaises(ProblemContractBlocked):
                build_output(case_dir)

    def test_TEST_6_VALIDATOR_ROUTER_BINDING(self) -> None:
        with tempfile.TemporaryDirectory(prefix="validator_router_binding_") as temp:
            case_dir = self.make_case(
                Path(temp),
                declare_contract_path=True,
                contract_path="problem_contract.md",
            )
            self.write_contract(case_dir, "problem_contract.md", self.ROOT_MARKER)

            validation = validate_problem_contract(case_dir)
            result, _ = self.build_and_capture_text(case_dir)
            router_contract_path = result["formal_input_binding"]["problem_contract"]["source_path"]

            self.assertEqual(validation["path"], result["problem_contract_validation"]["path"])
            self.assertEqual(validation["path"], router_contract_path)
            self.assertIn(validation["path"], self.checked_paths(result["input_files_checked"]))

    def test_TEST_7_CONTENT_BINDING(self) -> None:
        with tempfile.TemporaryDirectory(prefix="contract_content_binding_") as temp:
            case_dir = self.make_case(
                Path(temp),
                declare_contract_path=True,
                contract_path="problem_contract.md",
            )
            full_contract = "# Formal Contract\n\n" + self.ROOT_MARKER + "\nFULL_CONTENT_END_MARKER\n"
            self.write_contract(case_dir, "problem_contract.md", full_contract)

            _, collected_text, _ = collect_case_inputs(case_dir)
            _, router_text = self.build_and_capture_text(case_dir)

            self.assertIn(full_contract.strip(), collected_text)
            self.assertIn(full_contract.strip(), router_text)
            self.assertIn("FULL_CONTENT_END_MARKER", router_text)
            self.assertNotIn(self.PLACEHOLDER_MARKER, collected_text)
            self.assertNotIn(self.PLACEHOLDER_MARKER, router_text)


class ModelRouteProductionWiringTests(unittest.TestCase):
    @staticmethod
    def score_inputs() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        criteria = [
            {"name": "Fit", "direction": "higher_is_better", "weight": 2},
            {"name": "Cost", "direction": "lower_is_better", "weight": 1},
        ]
        routes = [
            {
                "route_name": "A: Route A",
                "scores": {"Fit": 8.0, "Cost": 3.0},
                "comparison_reasoning": "Explicit comparison against the bounded baseline.",
            },
            {
                "route_name": "B: Route B",
                "scores": {"Fit": 7.0, "Cost": 4.0},
                "comparison_reasoning": "Lower fit score with a documented cost tradeoff.",
            },
        ]
        return criteria, routes

    def test_real_score_inputs_roundtrip_through_existing_validator(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_writer_real_") as temp:
            case_dir = make_geometry_case(Path(temp))
            criteria, routes = self.score_inputs()
            route_names = [route["route_name"] for route in routes]
            (case_dir / "model_routes.md").write_text(
                "# Model Routes\n\n"
                + "\n\n".join(
                    f"## Route {name}\n\nCandidate route."
                    for name in route_names
                )
                + "\n",
                encoding="utf-8",
            )
            output_path = case_dir / "model_route_score.xlsx"
            write_model_route_score_xlsx(output_path, criteria, routes)

            result = validate_model_route_outputs(case_dir)

            self.assertEqual(result["status"], "AWAITING_GATE2")
            self.assertEqual(result["reason"], "MODEL_ROUTES_VALID_GATE2_PENDING")
            self.assertTrue(output_path.is_file())

    def test_workbook_contains_explicit_scores_weights_directions_and_reasoning(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_writer_content_") as temp:
            output_path = Path(temp) / "model_route_score.xlsx"
            criteria, routes = self.score_inputs()
            write_model_route_score_xlsx(output_path, criteria, routes)

            workbook_text = _xlsx_text(output_path)
            for token in (
                "Route A",
                "Route B",
                "Fit",
                "Cost",
                "higher_is_better",
                "lower_is_better",
                "Comparison Reasoning",
            ):
                self.assertIn(token, workbook_text)
            with zipfile.ZipFile(output_path) as archive:
                self.assertIn("xl/worksheets/sheet2.xml", archive.namelist())
                sheet_xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
                self.assertIn("<f>SUM(B2:C2)</f>", sheet_xml)
                self.assertIn("<v>11</v>", sheet_xml)
                workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
                self.assertIn('name="Scoring Rules"', workbook_xml)

    def test_contribution_points_sum_without_second_weighting(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_writer_contribution_points_") as temp:
            output_path = Path(temp) / "model_route_score.xlsx"
            criteria = [
                {"name": "C1", "direction": "higher_is_better", "weight": 30},
                {"name": "C2", "direction": "higher_is_better", "weight": 20},
                {"name": "C3", "direction": "higher_is_better", "weight": 20},
                {"name": "C4", "direction": "higher_is_better", "weight": 15},
                {"name": "C5", "direction": "higher_is_better", "weight": 10},
                {"name": "C6", "direction": "higher_is_better", "weight": 5},
            ]
            routes = [
                {
                    "route_name": "A: Route A",
                    "scores": {"C1": 29, "C2": 19, "C3": 18, "C4": 9, "C5": 10, "C6": 5},
                    "reported_total": 90,
                    "comparison_reasoning": "Contribution points sum to the recorded total.",
                }
            ]
            write_model_route_score_xlsx(output_path, criteria, routes)
            with zipfile.ZipFile(output_path) as archive:
                sheet_xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
                self.assertIn("<f>SUM(B2:G2)</f>", sheet_xml)
                self.assertIn("<v>90</v>", sheet_xml)

    def test_lower_direction_does_not_reverse_contribution_points(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_writer_direction_") as temp:
            output_path = Path(temp) / "model_route_score.xlsx"
            criteria = [{"name": "Risk", "direction": "lower_is_better", "weight": 5}]
            routes = [
                {
                    "route_name": "A: Route A",
                    "scores": {"Risk": 5},
                    "comparison_reasoning": "Five points are supplied as the awarded contribution.",
                }
            ]
            write_model_route_score_xlsx(output_path, criteria, routes)
            with zipfile.ZipFile(output_path) as archive:
                sheet_xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
                self.assertIn("<v>5</v>", sheet_xml)

    def test_reported_total_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_writer_total_check_") as temp:
            output_path = Path(temp) / "model_route_score.xlsx"
            with self.assertRaisesRegex(ValueError, "MODEL_ROUTE_SCORE_REPORTED_TOTAL_MISMATCH"):
                write_model_route_score_xlsx(
                    output_path,
                    [{"name": "Fit", "direction": "higher_is_better", "weight": 1}],
                    [
                        {
                            "route_name": "A: Route A",
                            "scores": {"Fit": 9},
                            "reported_total": 10,
                            "comparison_reasoning": "The reported total is intentionally inconsistent.",
                        }
                    ],
                )
            self.assertFalse(output_path.exists())

    def test_route_score_writer_supports_unicode_route_names(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_writer_unicode_") as temp:
            output_path = Path(temp) / "model_route_score.xlsx"
            route_name = "路线 甲：高压油管动态压力控制"
            write_model_route_score_xlsx(
                output_path,
                [{"name": "匹配度", "direction": "分数越高越好", "weight": 1}],
                [
                    {
                        "route_name": route_name,
                        "scores": {"匹配度": 9},
                        "comparison_reasoning": "Unicode reasoning.",
                    }
                ],
            )
            self.assertIn(route_name, _xlsx_text(output_path))

    def test_route_score_writer_rejects_empty_inputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_writer_empty_") as temp:
            output_path = Path(temp) / "model_route_score.xlsx"
            with self.assertRaisesRegex(ValueError, "MODEL_ROUTE_SCORE_CRITERIA_REQUIRED"):
                write_model_route_score_xlsx(output_path, [], [])
            with self.assertRaisesRegex(ValueError, "MODEL_ROUTE_RECORDS_REQUIRED"):
                write_model_route_score_xlsx(
                    output_path,
                    [{"name": "Fit", "direction": "higher", "weight": 1}],
                    [],
                )
            self.assertFalse(output_path.exists())

    def test_route_score_writer_rejects_missing_direction_or_weight(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_writer_criteria_") as temp:
            output_path = Path(temp) / "model_route_score.xlsx"
            route = {
                "route_name": "A: Route A",
                "scores": {"Fit": 1},
                "comparison_reasoning": "x",
            }
            with self.assertRaisesRegex(ValueError, "MODEL_ROUTE_SCORE_CRITERION_DIRECTION_REQUIRED"):
                write_model_route_score_xlsx(output_path, [{"name": "Fit", "weight": 1}], [route])
            with self.assertRaisesRegex(ValueError, "MODEL_ROUTE_SCORE_CRITERION_WEIGHT_INVALID"):
                write_model_route_score_xlsx(
                    output_path,
                    [{"name": "Fit", "direction": "higher", "weight": float("nan")}],
                    [route],
                )
            self.assertFalse(output_path.exists())

    def test_route_score_writer_rejects_incomplete_scores_or_reasoning(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_writer_route_") as temp:
            output_path = Path(temp) / "model_route_score.xlsx"
            criteria = [{"name": "Fit", "direction": "higher", "weight": 1}]
            with self.assertRaisesRegex(ValueError, "MODEL_ROUTE_SCORE_DIMENSION_MISMATCH"):
                write_model_route_score_xlsx(
                    output_path,
                    criteria,
                    [{"route_name": "A: Route A", "scores": {}, "comparison_reasoning": "x"}],
                )
            with self.assertRaisesRegex(ValueError, "MODEL_ROUTE_SCORE_COMPARISON_REASONING_REQUIRED"):
                write_model_route_score_xlsx(
                    output_path,
                    criteria,
                    [{"route_name": "A: Route A", "scores": {"Fit": 1}, "comparison_reasoning": ""}],
                )
            self.assertFalse(output_path.exists())

    def test_route_score_writer_rejects_non_finite_scores_and_duplicate_names(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_writer_finite_") as temp:
            output_path = Path(temp) / "model_route_score.xlsx"
            criteria = [{"name": "Fit", "direction": "higher", "weight": 1}]
            with self.assertRaisesRegex(ValueError, "MODEL_ROUTE_SCORE_VALUE_INVALID"):
                write_model_route_score_xlsx(
                    output_path,
                    criteria,
                    [{"route_name": "A: Route A", "scores": {"Fit": float("inf")}, "comparison_reasoning": "x"}],
                )
            with self.assertRaisesRegex(ValueError, "MODEL_ROUTE_SCORE_CRITERION_DUPLICATE"):
                write_model_route_score_xlsx(
                    output_path,
                    [
                        {"name": "Fit", "direction": "higher", "weight": 1},
                        {"name": "Fit", "direction": "higher", "weight": 1},
                    ],
                    [{"route_name": "A: Route A", "scores": {"Fit": 1}, "comparison_reasoning": "x"}],
                )
            self.assertFalse(output_path.exists())

    def test_public_model_stage_does_not_generate_route_score(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_writer_integration_") as temp:
            case_dir = make_geometry_case(Path(temp))
            run_stage(case_dir, "model")
            (case_dir / "model_routes.md").write_text(
                "# Model Routes\n\n## Route R1: Explicit route\n\nCandidate route.\n",
                encoding="utf-8",
            )
            score_path = case_dir / "model_route_score.xlsx"
            result = run_stage(case_dir, "model")
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(
                result["model_route_stage"]["outputs"]["reason"],
                "MODEL_ROUTE_OUTPUT_SET_INCOMPLETE",
            )
            self.assertFalse(score_path.exists())

    def test_existing_executor_workbook_is_preserved_by_public_model_stage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_writer_preserve_") as temp:
            case_dir = make_geometry_case(Path(temp))
            criteria, routes = self.score_inputs()
            (case_dir / "model_routes.md").write_text(
                "# Model Routes\n\n"
                + "\n\n".join(
                    f"## Route {route['route_name']}\n\nCandidate route."
                    for route in routes
                )
                + "\n",
                encoding="utf-8",
            )
            score_path = case_dir / "model_route_score.xlsx"
            write_model_route_score_xlsx(score_path, criteria, routes)
            before = score_path.read_bytes()
            result = run_stage(case_dir, "model")
            self.assertEqual(result["status"], "AWAITING_GATE2")
            self.assertEqual(score_path.read_bytes(), before)

    def _legacy_route_score_writer_roundtrips_through_existing_validator(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_writer_") as temp:
            case_dir = make_geometry_case(Path(temp))
            route_names = ["A: Route A", "B: Route B"]
            (case_dir / "model_routes.md").write_text(
                "# Model Routes\n\n"
                + "\n\n".join(
                    f"## Route {name}\n\n候选路线。" for name in route_names
                )
                + "\n",
                encoding="utf-8",
            )
            output_path = case_dir / "model_route_score.xlsx"
            write_model_route_score_xlsx(output_path, route_names)

            result = validate_model_route_outputs(case_dir)

            self.assertEqual(result["status"], "AWAITING_GATE2")
            self.assertEqual(result["reason"], "MODEL_ROUTES_VALID_GATE2_PENDING")
            self.assertTrue(output_path.is_file())

    def _legacy_route_score_workbook_contains_complete_route_names(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_writer_text_") as temp:
            output_path = Path(temp) / "model_route_score.xlsx"
            route_names = ["Route A", "Route B"]
            write_model_route_score_xlsx(output_path, route_names)

            workbook_text = _xlsx_text(output_path)

            for route_name in route_names:
                self.assertIn(route_name, workbook_text)

    def _legacy_route_score_writer_supports_unicode_route_names(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_writer_unicode_靠") as temp:
            output_path = Path(temp) / "model_route_score.xlsx"
            route_name = "路线 甲：高压油管动态压力控制"

            write_model_route_score_xlsx(output_path, [route_name])

            self.assertIn(route_name, _xlsx_text(output_path))

    def _legacy_route_score_regeneration_removes_stale_route_names(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_writer_refresh_") as temp:
            output_path = Path(temp) / "model_route_score.xlsx"
            write_model_route_score_xlsx(output_path, ["Route A", "Route B"])
            write_model_route_score_xlsx(output_path, ["Route C"])

            workbook_text = _xlsx_text(output_path)

            self.assertIn("Route C", workbook_text)
            self.assertNotIn("Route A", workbook_text)
            self.assertNotIn("Route B", workbook_text)

    def _legacy_route_score_writer_rejects_empty_route_list(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_writer_empty_") as temp:
            output_path = Path(temp) / "model_route_score.xlsx"

            with self.assertRaisesRegex(ValueError, "MODEL_ROUTE_RECORDS_REQUIRED"):
                write_model_route_score_xlsx(output_path, [])
            self.assertFalse(output_path.exists())

    def _legacy_route_score_writer_does_not_invent_score_or_rank_fields(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_writer_no_fake_score_") as temp:
            output_path = Path(temp) / "model_route_score.xlsx"

            write_model_route_score_xlsx(output_path, ["Route A"])

            workbook_text = _xlsx_text(output_path)
            self.assertNotIn("score", workbook_text.casefold())
            self.assertNotIn("rank", workbook_text.casefold())

    def _legacy_public_model_stage_generates_route_score_without_preseed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_writer_integration_") as temp:
            case_dir = make_geometry_case(Path(temp))
            run_stage(case_dir, "model")
            route_name = "R1：解析几何"
            (case_dir / "model_routes.md").write_text(
                f"# 模型路线\n\n## 路线 {route_name}\n\n候选路线。\n",
                encoding="utf-8",
            )
            score_path = case_dir / "model_route_score.xlsx"
            self.assertFalse(score_path.exists())

            result = run_stage(case_dir, "model")

            self.assertEqual(result["status"], "AWAITING_GATE2")
            self.assertTrue(score_path.is_file())
            self.assertIn(route_name, _xlsx_text(score_path))

    def test_repeated_public_model_stage_is_hash_stable_for_unchanged_inputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_idempotent_") as temp:
            case_dir = make_geometry_case(Path(temp))

            first = run_stage(case_dir, "model")
            router_path = case_dir / "routing" / "problem_type_routing_result.json"
            handoff_path = case_dir / "handoff" / "MODEL_ROUTE_GENERATION_HANDOFF.md"
            first_router_bytes = router_path.read_bytes()
            first_handoff_bytes = handoff_path.read_bytes()

            second = run_stage(case_dir, "model")

            self.assertEqual(first["status"], "AWAITING_EXECUTOR")
            self.assertEqual(second["status"], "AWAITING_EXECUTOR")
            self.assertEqual(router_path.read_bytes(), first_router_bytes)
            self.assertEqual(handoff_path.read_bytes(), first_handoff_bytes)
            self.assertEqual(
                second["model_route_stage"]["handoff"]["sha256"],
                first["model_route_stage"]["handoff"]["sha256"],
            )

    def test_geometry_case_routes_without_historical_case_pollution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_isolation_") as temp:
            case_dir = make_geometry_case(Path(temp))
            (case_dir / "model").mkdir(parents=True, exist_ok=True)
            (case_dir / "model" / "model_formulation.md").write_text(
                "# Model Formulation\n\nReviewed formulation boundary.\n",
                encoding="utf-8",
            )
            (case_dir / "model_critique.md").write_text(
                "# Model Review\n\nMODEL_REVIEW_FINAL_VERDICT: PASS\n",
                encoding="utf-8",
            )
            result = run_stage(case_dir, "model")

            self.assertEqual(result["status"], "AWAITING_EXECUTOR")
            self.assertEqual(
                result["problem_type_routing"]["primary_type"],
                "geometry_inverse_localization",
            )
            problem_type = json.loads((case_dir / "problem_type.json").read_text(encoding="utf-8"))
            self.assertEqual(problem_type["primary_type"], "geometry_inverse_localization")
            self.assertIn("31_inverse_problem_and_parameter_identification", problem_type["recommended_knowledge_cards"])
            routing_result = json.loads(
                (case_dir / "routing" / "problem_type_routing_result.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIsNone(routing_result["final_model_route"])
            self.assertEqual(routing_result["route_source"], "NOT_SELECTED_GATE2_PENDING")
            generated_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (case_dir / "routing").glob("*.md")
            )
            self.assertNotIn("2017B", generated_text)
            self.assertNotIn("拍照赚钱", generated_text)
            self.assertTrue((case_dir / "handoff" / "MODEL_ROUTE_GENERATION_HANDOFF.md").is_file())
            handoff_text = (case_dir / "handoff" / "MODEL_ROUTE_GENERATION_HANDOFF.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("`PROBLEM_ANALYSIS_PATH`: `problem_analysis.md`", handoff_text)
            self.assertIn("`PROBLEM_ANALYSIS_SHA256`:", handoff_text)
            self.assertIn("`CASE_CONFIG_SHA256`:", handoff_text)
            self.assertIn("`ROUTER_RESULT_SHA256`:", handoff_text)
            self.assertIn("`DATA_AVAILABILITY`:", handoff_text)
            self.assertIn('"path": "model/model_formulation.md"', handoff_text)
            self.assertIn('"path": "model_critique.md"', handoff_text)
            self.assertIn("only add pending questions", handoff_text)
            self.assertFalse((case_dir / "handoff" / "MODEL_BUILDING_HANDOFF.md").exists())
            self.assertFalse((case_dir / "final_submit").exists())

    def _legacy_incomplete_route_outputs_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_incomplete_") as temp:
            case_dir = make_geometry_case(Path(temp))
            run_stage(case_dir, "model")
            (case_dir / "model_routes.md").write_text(
                "# 模型路线\n\n## 路线 R1：解析几何\n",
                encoding="utf-8",
            )

            result = run_stage(case_dir, "model")

            self.assertEqual(result["status"], "AWAITING_GATE2")
            self.assertEqual(
                result["model_route_stage"]["outputs"]["reason"],
                "MODEL_ROUTES_VALID_GATE2_PENDING",
            )
            self.assertTrue((case_dir / "model_route_score.xlsx").is_file())

    def test_route_heading_without_id_and_separator_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_heading_format_") as temp:
            case_dir = make_geometry_case(Path(temp))
            run_stage(case_dir, "model")
            malformed_name = "解析几何定位"
            (case_dir / "model_routes.md").write_text(
                f"# 模型路线\n\n## 路线 {malformed_name}\n\n候选路线，尚未运行。\n",
                encoding="utf-8",
            )
            write_route_workbook(case_dir / "model_route_score.xlsx", [malformed_name])

            result = run_stage(case_dir, "model")

            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(
                result["model_route_stage"]["outputs"]["reason"],
                "MODEL_ROUTE_HEADING_FORMAT_INVALID",
            )

    def test_missing_skill_input_blocks_before_executor_handoff(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_missing_input_") as temp:
            case_dir = make_geometry_case(Path(temp))
            (case_dir / "problem_analysis.md").unlink()

            result = run_stage(case_dir, "model")

            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(result["reason"], "MODEL_ROUTE_PRODUCTION_WIRING_FAILED")
            self.assertEqual(result["error"], "PROBLEM_ANALYSIS_INPUT_MISSING")
            self.assertFalse((case_dir / "handoff" / "MODEL_ROUTE_GENERATION_HANDOFF.md").exists())

    def test_template_route_placeholder_is_treated_as_not_generated(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_placeholder_") as temp:
            case_dir = make_geometry_case(Path(temp))
            (case_dir / "model_routes.md").write_text(
                "# Model Routes\n\nStatus: waiting for confirmed problem analysis.\n",
                encoding="utf-8",
            )

            result = run_stage(case_dir, "model")

            self.assertEqual(result["status"], "AWAITING_EXECUTOR")
            self.assertEqual(
                result["model_route_stage"]["outputs"]["reason"],
                "MODEL_ROUTE_OUTPUTS_NOT_GENERATED",
            )

    def test_valid_routes_wait_for_gate2_then_allow_model_implementation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_gate2_") as temp:
            base = Path(temp)
            case_dir = make_geometry_case(base)
            run_stage(case_dir, "model")
            route_names = ["R1：解析几何", "R2：直接角度反馈"]
            (case_dir / "model_routes.md").write_text(
                "# 模型路线\n\n"
                + "\n\n".join(f"## 路线 {name}\n\n候选路线，尚未运行。" for name in route_names),
                encoding="utf-8",
            )
            write_route_workbook(case_dir / "model_route_score.xlsx", route_names)

            pending = run_stage(case_dir, "model")
            self.assertEqual(pending["status"], "AWAITING_GATE2")
            self.assertFalse(pending["model_route_stage"]["solver_allowed"])
            self.assertFalse((case_dir / "handoff" / "MODEL_BUILDING_HANDOFF.md").exists())

            approved_fixture = write_gate_case(base / "gate2_approved", 2, 0)
            shutil.copy2(
                approved_fixture / "human_review" / GATE_FILES[2],
                case_dir / "human_review" / GATE_FILES[2],
            )
            gate2_path = case_dir / "human_review" / GATE_FILES[2]
            gate2_path.write_text(
                gate2_path.read_text(encoding="utf-8").replace(
                    "选择路线：", "选择路线：R1：解析几何"
                )
                + "\n## 批准后的正式建模/求解前强制 fail-closed 条件\n"
                + "- 仅允许在数据边界和人工批准的模型复杂度内实现。\n",
                encoding="utf-8",
            )
            approved = run_stage(case_dir, "model")
            self.assertEqual(approved["status"], "READY_FOR_MODEL_IMPLEMENTATION")
            self.assertTrue(approved["model_route_stage"]["solver_allowed"])
            self.assertEqual(
                approved["model_route_stage"]["outputs"]["resolved_selected_route"],
                "R1：解析几何",
            )
            self.assertEqual(approved["model_building_handoff"]["status"], "PASS")
            handoff_text = (case_dir / "handoff" / "MODEL_BUILDING_HANDOFF.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("`selected_model_route`: `R1：解析几何`", handoff_text)
            self.assertIn("`current_phase`: `MODEL_IMPLEMENTATION`", handoff_text)
            self.assertNotIn("ROUTER_ONLY / NO_MODEL_EXECUTION", handoff_text)
            self.assertIn("## Modeling Boundary", handoff_text)
            self.assertIn("## Knowledge Support", handoff_text)

    def test_gate2_handoff_survives_real_transition_commit_and_stable_sources_remain_bound(self) -> None:
        with tempfile.TemporaryDirectory(prefix="formal_handoff_lifecycle_") as temp:
            base = Path(temp)
            case_dir = make_geometry_case(base, case_id="formal_handoff_lifecycle")
            route_name = "R1：解析几何"
            (case_dir / "model_routes.md").write_text(
                f"# 模型路线\n\n## 路线 {route_name}\n\n候选路线，尚未运行。\n",
                encoding="utf-8",
            )
            write_route_workbook(case_dir / "model_route_score.xlsx", [route_name])
            approved_fixture = write_gate_case(base / "gate2_approved", 2, 0)
            gate2_path = case_dir / "human_review" / GATE_FILES[2]
            shutil.copy2(approved_fixture / "human_review" / GATE_FILES[2], gate2_path)
            gate2_path.write_text(
                gate2_path.read_text(encoding="utf-8").replace(
                    "选择路线：", f"选择路线：{route_name}"
                )
                + "\n## 批准后的正式建模/求解前强制 fail-closed 条件\n"
                + "- 仅允许在 Problem Contract 和人工批准路线内实现。\n",
                encoding="utf-8",
            )

            config_path = case_dir / "case_config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config.update(
                {
                    "current_stage": "MODEL_ROUTE_GENERATION",
                    "status": "MODEL_ROUTE_GENERATION",
                    "previous_stage": "PROBLEM_UNDERSTANDING",
                    "next_allowed_stage": "MODEL_IMPLEMENTATION",
                    "state_authority": "case_config.json",
                    "transition_authority": "transition_case_v1",
                    "state_revision": 2,
                    "current_handoff_path": "CURRENT_HANDOFF.md",
                    "last_transition_id": "route-stage-fixture",
                }
            )
            config_path.write_text(
                json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (case_dir / "CURRENT_HANDOFF.md").write_text(
                "# formal_handoff_lifecycle Current Handoff\n\n"
                "`CURRENT_HANDOFF_STATUS=STATUS_MIRROR_CASE_CONFIG`\n\n"
                "- `CASE_ID`: `formal_handoff_lifecycle`\n"
                "- `CURRENT_STAGE`: `MODEL_ROUTE_GENERATION`\n"
                "- `STATE_AUTHORITY`: `case_config.json`\n"
                "- `TRANSITION_AUTHORITY`: `transition_case_v1`\n"
                "- `STATE_REVISION`: `2`\n"
                "- `NEXT_ALLOWED_STAGE`: `MODEL_IMPLEMENTATION`\n"
                "- `LAST_TRANSITION_ID`: `route-stage-fixture`\n",
                encoding="utf-8",
            )

            authorization = authorize_transition(
                case_dir,
                requested_stage="MODEL_IMPLEMENTATION",
                executor="run_pipeline:model",
            )
            self.assertEqual(authorization["status"], "PASS", authorization)
            authorization_path = write_transition_authorization(case_dir, authorization)
            generated = run_stage(
                case_dir,
                "model",
                transition_authorization_path=str(authorization_path),
            )
            self.assertEqual(generated["status"], "AWAITING_TRANSITION", generated)
            self.assertEqual(generated["executor_status"], "READY_FOR_MODEL_IMPLEMENTATION")
            handoff_path = case_dir / "handoff" / "MODEL_BUILDING_HANDOFF.md"
            handoff_text = handoff_path.read_text(encoding="utf-8")
            self.assertIn("`selected_model_route`: `R1：解析几何`", handoff_text)
            self.assertIn("`current_phase`: `MODEL_IMPLEMENTATION`", handoff_text)
            self.assertIn("`INFORMATIONAL_NON_INVALIDATING`", handoff_text)

            receipt = build_completion_receipt(
                case_dir,
                authorization_path=authorization_path,
                artifacts=["handoff/MODEL_BUILDING_HANDOFF.md"],
                executor_status="PASS",
            )
            self.assertEqual(receipt["status"], "PASS", receipt)
            receipt_path = write_completion_receipt(case_dir, receipt)
            committed = commit_transition(
                case_dir,
                authorization_path=authorization_path,
                completion_receipt_path=receipt_path,
            )
            self.assertEqual(committed["status"], "PASS", committed)
            self.assertEqual(committed["current_stage"], "MODEL_IMPLEMENTATION")

            post_commit_result = build_output(case_dir)
            post_commit_outputs = validate_model_route_outputs(case_dir)
            prepare_model_building_handoff_inputs(
                case_dir,
                post_commit_result,
                post_commit_outputs,
                target_stage="MODEL_IMPLEMENTATION",
            )
            validated = validate_model_building_handoff(case_dir, post_commit_result)
            self.assertEqual(validated["status"], "PASS")

            stable_source = case_dir / "problem" / "problem_contract.md"
            stable_source.write_text(
                stable_source.read_text(encoding="utf-8") + "\nstable authority changed\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError,
                "MODEL_BUILDING_HANDOFF_SOURCE_CHANGED: problem/problem_contract.md",
            ):
                validate_model_building_handoff(case_dir, post_commit_result)

    def test_old_formal_handoff_is_rejected_after_bound_source_changes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="formal_handoff_hash_binding_") as temp:
            base = Path(temp)
            case_dir = make_geometry_case(base)
            route_name = "R1：解析几何"
            (case_dir / "model_routes.md").write_text(
                f"# 模型路线\n\n## 路线 {route_name}\n\n候选路线。\n",
                encoding="utf-8",
            )
            write_route_workbook(case_dir / "model_route_score.xlsx", [route_name])
            approved_fixture = write_gate_case(base / "gate2_approved", 2, 0)
            gate2_path = case_dir / "human_review" / GATE_FILES[2]
            shutil.copy2(approved_fixture / "human_review" / GATE_FILES[2], gate2_path)
            gate2_path.write_text(
                gate2_path.read_text(encoding="utf-8").replace(
                    "选择路线：", f"选择路线：{route_name}"
                )
                + "\n## 批准后的正式建模/求解前强制 fail-closed 条件\n"
                + "- 仅允许在数据边界和人工批准的模型复杂度内实现。\n",
                encoding="utf-8",
            )
            first = run_stage(case_dir, "model")
            self.assertEqual(first["status"], "READY_FOR_MODEL_IMPLEMENTATION")
            handoff_path = case_dir / "handoff" / "MODEL_BUILDING_HANDOFF.md"
            original_handoff = handoff_path.read_bytes()

            for relative, marker in (
                ("model_routes.md", "路线内容变更"),
                ("human_review/gate_2_model_choice.md", "备注：绑定来源变更"),
                ("problem/problem_contract.md", "输入契约内容变更"),
            ):
                handoff_path.write_bytes(original_handoff)
                source = case_dir / relative
                source.write_text(source.read_text(encoding="utf-8") + f"\n{marker}\n", encoding="utf-8")
                result = build_output(case_dir)
                write_case_outputs(case_dir, result)
                route_stage = run_model_route_stage(case_dir, result)
                self.assertEqual(route_stage["status"], "READY_FOR_MODEL_IMPLEMENTATION")
                prepare_model_building_handoff_inputs(case_dir, result, route_stage["outputs"])
                with self.assertRaisesRegex(ValueError, "MODEL_BUILDING_HANDOFF_SOURCE_HASH_MISMATCH"):
                    validate_model_building_handoff(case_dir, result)

    def test_non_geometry_gate_boundary_is_bound_without_geometry_terms(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_non_geometry_boundary_") as temp:
            base = Path(temp)
            case_dir = make_geometry_case(base, case_id="planning_case")
            (case_dir / "problem" / "problem_contract.md").write_text(
                "# Problem Contract\n\n线性资源配置，目标为成本最小，约束为容量与需求平衡。\n",
                encoding="utf-8",
            )
            (case_dir / "problem_analysis.md").write_text(
                "# 问题分析\n\n本题为线性规划，不涉及几何定位、Jacobian 或镜像分支。\n",
                encoding="utf-8",
            )
            route_name = "P1：资源配置基线"
            (case_dir / "model_routes.md").write_text(
                f"# 模型路线\n\n## 路线 {route_name}\n\n使用线性规划求解资源配置。\n",
                encoding="utf-8",
            )
            write_route_workbook(case_dir / "model_route_score.xlsx", [route_name])
            approved_fixture = write_gate_case(base / "planning_gate2", 2, 0)
            gate2_path = case_dir / "human_review" / GATE_FILES[2]
            shutil.copy2(approved_fixture / "human_review" / GATE_FILES[2], gate2_path)
            gate2_path.write_text(
                gate2_path.read_text(encoding="utf-8").replace(
                    "选择路线：", f"选择路线：{route_name}"
                )
                + "\n## 批准后的正式建模/求解前强制 fail-closed 条件\n"
                + "- 需求量和容量数据缺失时停止求解；不得虚构输入。\n",
                encoding="utf-8",
            )

            result = run_stage(case_dir, "model")

            self.assertEqual(result["status"], "READY_FOR_MODEL_IMPLEMENTATION", result)
            handoff_text = (case_dir / "handoff" / "MODEL_BUILDING_HANDOFF.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("需求量和容量数据缺失时停止求解", handoff_text)
            self.assertNotIn("rank=16", handoff_text)
            self.assertNotIn("累计观测独立性", handoff_text)

    def test_gate2_pass_without_matching_selected_route_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="route_gate2_selection_") as temp:
            base = Path(temp)
            case_dir = make_geometry_case(base)
            route_names = ["R1：解析几何", "R2：直接角度反馈"]
            (case_dir / "model_routes.md").write_text(
                "# 模型路线\n\n"
                + "\n\n".join(f"## 路线 {name}\n\n候选路线，尚未运行。" for name in route_names),
                encoding="utf-8",
            )
            write_route_workbook(case_dir / "model_route_score.xlsx", route_names)
            approved_fixture = write_gate_case(base / "gate2_approved", 2, 0)
            gate2_path = case_dir / "human_review" / GATE_FILES[2]
            shutil.copy2(approved_fixture / "human_review" / GATE_FILES[2], gate2_path)

            blank = run_stage(case_dir, "model")
            self.assertEqual(blank["status"], "FAIL")
            self.assertEqual(
                blank["model_route_stage"]["outputs"]["reason"],
                "GATE2_SELECTED_ROUTE_REQUIRED",
            )

            gate2_path.write_text(
                gate2_path.read_text(encoding="utf-8").replace(
                    "选择路线：", "选择路线：R9：不存在的路线"
                ),
                encoding="utf-8",
            )
            mismatch = run_stage(case_dir, "model")
            self.assertEqual(mismatch["status"], "FAIL")
            self.assertEqual(
                mismatch["model_route_stage"]["outputs"]["reason"],
                "GATE2_SELECTED_ROUTE_NOT_IN_CANDIDATES",
            )


if __name__ == "__main__":
    unittest.main()
