from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline_core.paper_quality.matlab_figure_pipeline import (
    DEFERRED_FIGURE_TYPES,
    MatlabFigureSpecError,
    compile_matlab_figure_script,
    discover_matlab_executable,
    execute_matlab_figure,
    matlab_batch_command,
    validate_figure_spec,
    canonical_record_sha256,
)
from pipeline_core.tests.test_figure_f2_b_handoff import _result_contract, _valid_png_bytes


def execution_authority() -> dict:
    return {
        "result_acceptance_contract": _result_contract(),
        "gate3_evaluation": {
            "gate_id": "GATE3",
            "gate_status": "PASS",
            "evidence_sha256": "c" * 64,
        },
    }


def base_spec(figure_type: str = "line") -> dict:
    return {
        "figure_id": f"figure_{figure_type}",
        "backend": "MATLAB",
        "figure_type": figure_type,
        "purpose": "comparison",
        "title": "Analytical figure",
        "x_label": "x",
        "y_label": "y",
        "source_binding": {
            "source_id": "result_figure",
            "source_type": "result_card",
            "sha256": canonical_record_sha256(_result_contract()["result_cards"][0]),
        },
        "output_file": f"figures/{figure_type}.png",
        "dpi": 400,
    }


def line_spec(figure_type: str = "line") -> dict:
    spec = base_spec(figure_type)
    spec["series"] = [{"name": "A", "x": [1, 2, 3], "y": [2.0, 3.0, 5.0]}]
    if figure_type == "multi_line":
        spec["series"].append({"name": "B", "x": [1, 2, 3], "y": [1.0, 4.0, 4.5]})
    return spec


def schematic_spec() -> dict:
    return {
        **base_spec("geometric_schematic"),
        "purpose": "model_explanation",
        "title": "几何关系示意图",
        "x_label": "横坐标 / m",
        "y_label": "纵坐标 / m",
        "panels": [
            {
                "title": "候选交会关系",
                "show_axes": False,
                "objects": [
                    {"object_id": "A", "label": "发射机 A", "x": 0, "y": 0, "marker_kind": "anchor", "label_offset": [0.2, 0.2]},
                    {"object_id": "B", "label": "发射机 B", "x": 4, "y": 0, "marker_kind": "anchor", "label_offset": [0.2, 0.2]},
                    {"object_id": "X", "label": "接收机 X", "x": 2, "y": 3, "marker_kind": "receiver", "label_offset": [0.2, 0.2]},
                    {"object_id": "O", "label": "圆心 O", "x": 2, "y": 0, "marker_kind": "generic", "label_offset": [0.2, -0.4]},
                ],
                "relations": [
                    {"kind": "segment", "from": "A", "to": "X", "label": "观测边 1"},
                    {"kind": "dashed_segment", "from": "B", "to": "X", "label": "观测边 2"},
                    {"kind": "arrow", "from": "O", "to": "X", "label": "半径 R"},
                    {"kind": "circle", "center": "O", "radius": 2, "label": "候选圆"},
                    {"kind": "arc", "center": "X", "radius": 0.7, "start_deg": 220, "end_deg": 320, "label": "夹角 alpha"},
                ],
            }
        ],
    }


def timeline_spec() -> dict:
    return {
        **base_spec("interval_timeline"),
        "purpose": "model_explanation",
        "title": "资源调度关系图",
        "x_label": "时间 / h",
        "y_label": "资源",
        "lanes": [
            {
                "lane_id": "machine_a",
                "label": "设备 A",
                "intervals": [
                    {"start": 0, "end": 2, "label": "任务 1", "category": "加工"},
                    {"start": 3, "end": 5, "label": "任务 3", "category": "加工"},
                ],
            },
            {
                "lane_id": "machine_b",
                "label": "设备 B",
                "intervals": [
                    {"start": 1, "end": 4, "label": "任务 2", "category": "加工"},
                ],
            },
        ],
    }


def typed_specs() -> dict[str, dict]:
    return {
        "line": line_spec("line"),
        "multi_line": line_spec("multi_line"),
        "scatter": line_spec("scatter"),
        "bar": {**base_spec("bar"), "categories": ["A", "B", "C"], "values": [2, 4, 3]},
        "grouped_bar": {
            **base_spec("grouped_bar"),
            "categories": ["A", "B", "C"],
            "series": [{"name": "A1", "values": [2, 4, 3]}, {"name": "A2", "values": [1, 3, 2]}],
        },
        "error_bar": {**base_spec("error_bar"), "x": [1, 2, 3], "y": [2, 4, 3], "error": [0.2, 0.4, 0.3]},
        "uncertainty_band": {
            **base_spec("uncertainty_band"),
            "x": [1, 2, 3],
            "center": [2, 4, 3],
            "lower": [1, 3, 2],
            "upper": [3, 5, 4],
        },
        "heatmap": {**base_spec("heatmap"), "x": [1, 2, 3], "y": [10, 20], "z": [[1, 2, 3], [4, 5, 6]]},
        "contour": {**base_spec("contour"), "x": [1, 2, 3], "y": [10, 20], "z": [[1, 2, 3], [4, 5, 6]]},
        "filled_contour": {**base_spec("filled_contour"), "x": [1, 2, 3], "y": [10, 20], "z": [[1, 2, 3], [4, 5, 6]]},
        "surface_3d": {
            **base_spec("surface_3d"),
            "x": [1, 2, 3],
            "y": [10, 20],
            "z": [[1, 2, 3], [4, 5, 6]],
            "z_label": "response",
            "purpose": "response_surface",
        },
        "trajectory_2d": {
            **base_spec("trajectory_2d"),
            "x": [0, 1, 2],
            "y": [0, 1, 1.5],
            "purpose": "spatial_trajectory",
        },
        "trajectory_3d": {
            **base_spec("trajectory_3d"),
            "x": [0, 1, 2],
            "y": [0, 1, 1.5],
            "z": [0, 2, 3],
            "z_label": "height",
            "purpose": "spatial_trajectory",
        },
        "geometric_schematic": schematic_spec(),
        "interval_timeline": timeline_spec(),
    }


class MatlabFigurePipelineTests(unittest.TestCase):
    def compile_text(self, spec: dict) -> str:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "figure.m"
            record = compile_matlab_figure_script(spec, target)
            self.assertEqual(record["backend"], "MATLAB")
            return target.read_text(encoding="utf-8")

    def test_legacy_line_spec_remains_backward_compatible(self) -> None:
        spec = {
            "figure_id": "legacy",
            "title": "Legacy",
            "x_label": "x",
            "y_label": "y",
            "source_binding": {
                "source_id": "legacy_result",
                "source_type": "result_card",
                "sha256": "b" * 64,
                "approved": True,
            },
            "output_file": "figures/legacy.png",
            "series": [{"name": "legacy", "x": [1, 2], "y": [3, 4]}],
        }
        normalized = validate_figure_spec(spec)
        self.assertEqual(normalized["figure_type"], "line")
        self.assertEqual(normalized["purpose"], "trend")

    def test_typed_declaration_requires_purpose(self) -> None:
        spec = line_spec()
        del spec["purpose"]
        with self.assertRaisesRegex(MatlabFigureSpecError, "MATLAB_FIGURE_REQUIRED_TEXT: purpose"):
            validate_figure_spec(spec)

    def test_source_backend_and_deferred_types_fail_closed(self) -> None:
        spec = line_spec()
        del spec["source_binding"]
        with self.assertRaisesRegex(MatlabFigureSpecError, "SOURCE_BINDING"):
            validate_figure_spec(spec)
        spec = line_spec()
        spec["backend"] = "PYTHON"
        with self.assertRaisesRegex(MatlabFigureSpecError, "FORMAL_FIGURE_BACKEND_MUST_BE_MATLAB"):
            validate_figure_spec(spec)
        for figure_type in DEFERRED_FIGURE_TYPES:
            spec = base_spec(figure_type)
            with self.assertRaisesRegex(MatlabFigureSpecError, "MATLAB_FIGURE_TYPE_UNSUPPORTED"):
                validate_figure_spec(spec)

    def test_source_binding_declares_identity_but_not_approval(self) -> None:
        spec = line_spec()
        spec["source_binding"] = "output/result.json#figure"
        with self.assertRaisesRegex(MatlabFigureSpecError, "STRUCTURED_REQUIRED"):
            validate_figure_spec(spec)
        spec = line_spec()
        del spec["source_binding"]["sha256"]
        with self.assertRaisesRegex(MatlabFigureSpecError, "sha256"):
            validate_figure_spec(spec)
        normalized = validate_figure_spec(line_spec())
        self.assertEqual(normalized["source_binding"]["source_type"], "result_card")
        self.assertNotIn("approved", normalized["source_binding"])

    def test_numeric_validation_rejects_empty_nan_inf_and_mismatched_lengths(self) -> None:
        spec = line_spec()
        spec["series"][0]["x"] = []
        with self.assertRaisesRegex(MatlabFigureSpecError, "NUMERIC_SERIES_REQUIRED"):
            validate_figure_spec(spec)
        spec = line_spec()
        spec["series"][0]["y"] = [2.0, float("nan"), 5.0]
        with self.assertRaisesRegex(MatlabFigureSpecError, "FINITE_VALUES_REQUIRED"):
            validate_figure_spec(spec)
        spec = line_spec()
        spec["series"][0]["y"] = [2.0, float("inf"), 5.0]
        with self.assertRaisesRegex(MatlabFigureSpecError, "FINITE_VALUES_REQUIRED"):
            validate_figure_spec(spec)
        spec = line_spec()
        spec["series"][0]["y"] = [2.0, 3.0]
        with self.assertRaisesRegex(MatlabFigureSpecError, "SERIES_LENGTH_MISMATCH"):
            validate_figure_spec(spec)

    def test_error_and_uncertainty_values_are_nonnegative(self) -> None:
        spec = typed_specs()["error_bar"]
        spec["error"] = [0.2, -0.4, 0.3]
        with self.assertRaisesRegex(MatlabFigureSpecError, "NONNEGATIVE_REQUIRED"):
            validate_figure_spec(spec)
        spec = typed_specs()["error_bar"]
        spec["lower_error"] = [0.2, -0.4, 0.3]
        del spec["error"]
        spec["upper_error"] = [0.2, 0.4, 0.3]
        with self.assertRaisesRegex(MatlabFigureSpecError, "NONNEGATIVE_REQUIRED"):
            validate_figure_spec(spec)
        spec = typed_specs()["uncertainty_band"]
        spec["lower"] = [1, 5, 2]
        with self.assertRaisesRegex(MatlabFigureSpecError, "UNCERTAINTY_ORDER_INVALID"):
            validate_figure_spec(spec)

    def test_3d_guard_rejects_constant_z_and_accepts_valid_variation(self) -> None:
        spec = typed_specs()["surface_3d"]
        spec["z"] = [[2, 2, 2], [2, 2, 2]]
        with self.assertRaisesRegex(MatlabFigureSpecError, "3D_VARIATION_REQUIRED"):
            validate_figure_spec(spec)
        spec = typed_specs()["trajectory_3d"]
        spec["z"] = [2, 2, 2]
        with self.assertRaisesRegex(MatlabFigureSpecError, "3D_VARIATION_REQUIRED"):
            validate_figure_spec(spec)
        self.assertEqual(validate_figure_spec(typed_specs()["surface_3d"])["figure_type"], "surface_3d")
        self.assertEqual(validate_figure_spec(typed_specs()["trajectory_3d"])["figure_type"], "trajectory_3d")

    def test_output_path_requires_supported_extension_and_no_traversal(self) -> None:
        spec = line_spec()
        spec["output_file"] = "figures/figure.svg"
        with self.assertRaisesRegex(MatlabFigureSpecError, "EXTENSION_UNSUPPORTED"):
            validate_figure_spec(spec)
        spec = line_spec()
        spec["output_file"] = "figures/../figure.png"
        with self.assertRaisesRegex(MatlabFigureSpecError, "PATH_TRAVERSAL"):
            validate_figure_spec(spec)
        spec = line_spec()
        spec["output_file"] = ""
        with self.assertRaisesRegex(MatlabFigureSpecError, "REQUIRED_TEXT: output_file"):
            validate_figure_spec(spec)
        spec = line_spec()
        spec["output_file"] = "C:/outside/figure.png"
        with self.assertRaisesRegex(MatlabFigureSpecError, "OUTPUT_PATH_ABSOLUTE"):
            validate_figure_spec(spec)

    def test_shape_and_uncertainty_validation_fail_closed(self) -> None:
        spec = typed_specs()["heatmap"]
        spec["z"] = [[1, 2], [3, 4]]
        with self.assertRaisesRegex(MatlabFigureSpecError, "MATRIX_DIMENSIONS_MISMATCH"):
            validate_figure_spec(spec)
        spec = typed_specs()["uncertainty_band"]
        spec["lower"] = [1, 5, 2]
        with self.assertRaisesRegex(MatlabFigureSpecError, "UNCERTAINTY_ORDER_INVALID"):
            validate_figure_spec(spec)
        spec = typed_specs()["trajectory_3d"]
        spec["z"] = [0, 1]
        with self.assertRaisesRegex(MatlabFigureSpecError, "SERIES_LENGTH_MISMATCH"):
            validate_figure_spec(spec)

    def test_geometric_schematic_requires_real_objects_and_relations(self) -> None:
        normalized = validate_figure_spec(schematic_spec())
        self.assertEqual(normalized["figure_type"], "geometric_schematic")
        self.assertEqual(len(normalized["panels"][0]["objects"]), 4)
        self.assertEqual(len(normalized["panels"][0]["relations"]), 5)

        spec = schematic_spec()
        spec["panels"][0]["relations"][0]["to"] = "MISSING"
        with self.assertRaisesRegex(MatlabFigureSpecError, "RELATION_ENDPOINT_INVALID"):
            validate_figure_spec(spec)

        spec = schematic_spec()
        spec["panels"][0]["relations"] = [{"kind": "flow_box", "from": "A", "to": "B"}]
        with self.assertRaisesRegex(MatlabFigureSpecError, "RELATION_KIND_INVALID"):
            validate_figure_spec(spec)

    def test_geometric_schematic_compiles_object_geometry_not_flowchart_boxes(self) -> None:
        text = self.compile_text(schematic_spec())
        self.assertIn("tiledlayout(fig", text)
        self.assertIn("quiver(ax", text)
        self.assertIn("linspace(", text)
        self.assertIn("axis(ax,'equal')", text)
        self.assertNotIn("annotation('rectangle'", text)

    def test_interval_timeline_requires_real_lanes_and_ordered_intervals(self) -> None:
        normalized = validate_figure_spec(timeline_spec())
        self.assertEqual(normalized["figure_type"], "interval_timeline")
        self.assertEqual(len(normalized["lanes"]), 2)

        spec = timeline_spec()
        spec["lanes"][0]["intervals"][0]["end"] = 0
        with self.assertRaisesRegex(MatlabFigureSpecError, "TIMELINE_INTERVAL_ORDER_INVALID"):
            validate_figure_spec(spec)

        spec = timeline_spec()
        spec["lanes"] = []
        with self.assertRaisesRegex(MatlabFigureSpecError, "TIMELINE_LANES_REQUIRED"):
            validate_figure_spec(spec)

    def test_all_f1_types_compile_to_type_specific_matlab(self) -> None:
        expected_tokens = {
            "line": "plot(ax",
            "multi_line": "plot(ax",
            "scatter": "scatter(ax",
            "bar": "bar(ax",
            "grouped_bar": "barHandles = bar(ax",
            "error_bar": "errorbar(ax",
            "uncertainty_band": "fill(ax",
            "heatmap": "imagesc(ax",
            "contour": "contour(ax",
            "filled_contour": "contourf(ax",
            "surface_3d": "surf(ax",
            "trajectory_2d": "plot(ax",
            "trajectory_3d": "plot3(ax",
            "geometric_schematic": "tiledlayout(fig",
            "interval_timeline": "rectangle(ax",
        }
        for figure_type, spec in typed_specs().items():
            with self.subTest(figure_type=figure_type):
                text = self.compile_text(spec)
                self.assertIn(expected_tokens[figure_type], text)
                self.assertIn("exportgraphics", text)
                self.assertIn("fileparts(mfilename('fullpath'))", text)
                self.assertIn("mkdir(outputFolder)", text)
                self.assertIn("Publication style: COMPETITION_PUBLICATION_STYLE_V1", text)

    def test_publication_style_avoids_default_visual_noise(self) -> None:
        text = self.compile_text(line_spec("line"))
        self.assertNotIn("'-o'", text)
        self.assertNotIn("title(ax", text)
        self.assertNotIn("legend(ax", text)
        self.assertIn("grid(ax,'on')", text)

        multi = line_spec("multi_line")
        multi["show_marker"] = True
        text = self.compile_text(multi)
        self.assertIn("'-o'", text)
        self.assertIn("legend(ax", text)

        bar = self.compile_text(typed_specs()["bar"])
        self.assertIn("grid(ax,'off')", bar)
        self.assertNotIn("title(ax", bar)

    def test_3d_labels_colorbar_and_no_decorative_3d_bar(self) -> None:
        surface_spec = typed_specs()["surface_3d"]
        surface_spec["colorbar_label"] = "response scale"
        surface = self.compile_text(surface_spec)
        self.assertIn("zlabel(ax,'response'", surface)
        self.assertIn("colorbar(ax)", surface)
        self.assertIn("ylabel(cb,'response scale'", surface)
        self.assertIn("view(ax", surface)
        with self.assertRaisesRegex(MatlabFigureSpecError, "MATLAB_FIGURE_TYPE_UNSUPPORTED"):
            validate_figure_spec({**base_spec("bar3"), "figure_type": "bar3"})

    def test_pdf_and_png_export_discipline_and_data_preservation(self) -> None:
        spec = line_spec()
        spec["output_file"] = "figures/figure.pdf"
        text = self.compile_text(spec)
        self.assertIn("ContentType','vector'", text)
        self.assertNotIn("'Resolution',400", text)
        spec = line_spec()
        text = self.compile_text(spec)
        self.assertIn("'Resolution',400", text)
        self.assertIn("[1 2 3]", text)
        self.assertIn("[2 3 5]", text)
        self.assertNotIn("smooth", text.lower())
        self.assertNotIn("interp", text.lower())
        self.assertNotIn("resamp", text.lower())

    def test_size_profiles_are_finite(self) -> None:
        spec = line_spec()
        spec["size_profile"] = "wide"
        self.assertIn("Position',[2 2 18 9.5]", self.compile_text(spec))
        spec["size_profile"] = "arbitrary"
        with self.assertRaisesRegex(MatlabFigureSpecError, "SIZE_PROFILE_UNSUPPORTED"):
            validate_figure_spec(spec)

    def test_matlab_batch_command_remains_a_non_runtime_helper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "render.m"
            target.write_text("close all;", encoding="utf-8")
            self.assertEqual(matlab_batch_command(target)[:2], ["matlab", "-batch"])

    def test_matlab_executable_discovery_found_and_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "matlab.exe"
            executable.write_text("mock", encoding="utf-8")
            self.assertEqual(
                discover_matlab_executable(executable),
                executable.resolve(),
            )
            self.assertEqual(
                discover_matlab_executable(
                    environment={"MATLAB_EXECUTABLE": str(executable)}
                ),
                executable.resolve(),
            )
            self.assertIsNone(discover_matlab_executable(Path(directory) / "missing.exe"))

    def test_matlab_return_code_failure_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "matlab.exe"
            executable.write_text("mock", encoding="utf-8")
            with patch(
                "pipeline_core.paper_quality.matlab_figure_pipeline.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    [str(executable), "-batch"], 7, "MATLAB stdout", "MATLAB stderr"
                ),
            ):
                receipt = execute_matlab_figure(line_spec(), root / "render.m", executable=executable, **execution_authority())
            self.assertEqual(receipt["status"], "FAIL")
            self.assertEqual(receipt["code"], "FIGURE_GENERATION_FAILED")
            self.assertEqual(receipt["return_code"], 7)
            self.assertEqual(receipt["generated_files"], [])

    def test_matlab_sandbox_filesystem_failure_is_runtime_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "matlab.exe"
            executable.write_text("mock", encoding="utf-8")
            with patch(
                "pipeline_core.paper_quality.matlab_figure_pipeline.subprocess.run",
                return_value=subprocess.CompletedProcess([], 1, "", "System Error: File system inconsistency"),
            ):
                receipt = execute_matlab_figure(
                    line_spec(), root / "render.m", executable=executable, **execution_authority()
                )
            self.assertEqual(receipt["status"], "BLOCKED")
            self.assertEqual(receipt["code"], "FIGURE_RUNTIME_FILESYSTEM_INCONSISTENCY")

    def test_matlab_success_validates_generated_asset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "matlab.exe"
            executable.write_text("mock", encoding="utf-8")
            spec = line_spec()
            spec["output_file"] = "figures/figure.png"

            def successful_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
                output = Path(kwargs["cwd"]) / "figures" / "figure.png"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(_valid_png_bytes())
                return subprocess.CompletedProcess(command, 0, "ok", "")

            with patch(
                "pipeline_core.paper_quality.matlab_figure_pipeline.subprocess.run",
                side_effect=successful_run,
            ) as run:
                receipt = execute_matlab_figure(spec, root / "render.m", executable=executable, **execution_authority())
            self.assertEqual(receipt["status"], "PASS")
            self.assertEqual(receipt["generation_status"], "GENERATED")
            self.assertEqual(receipt["asset_format"], "png")
            self.assertGreater(receipt["asset_size_bytes"], 0)
            self.assertEqual(receipt["result_binding"]["source_contract"], "result_acceptance_contract")
            self.assertTrue(receipt["result_binding"]["approved"])
            self.assertEqual(len(receipt["result_binding"]["result_sha256"]), 64)
            self.assertEqual(len(receipt["asset_binding"]["asset_sha256"]), 64)
            self.assertEqual(run.call_args.args[0][0], str(executable.resolve()))
            self.assertEqual(run.call_args.args[0][1], "-batch")

    def test_successful_matlab_without_asset_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "matlab.exe"
            executable.write_text("mock", encoding="utf-8")
            with patch(
                "pipeline_core.paper_quality.matlab_figure_pipeline.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "ok", ""),
            ):
                receipt = execute_matlab_figure(line_spec(), root / "render.m", executable=executable, **execution_authority())
            self.assertEqual(receipt["status"], "FAIL")
            self.assertEqual(receipt["code"], "FIGURE_ASSET_MISSING")
            self.assertEqual(receipt["generated_files"], [])

    def test_missing_matlab_is_fail_closed_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch(
                "pipeline_core.paper_quality.matlab_figure_pipeline.subprocess.run"
            ) as run:
                receipt = execute_matlab_figure(
                    line_spec(),
                    root / "render.m",
                    executable=root / "missing_matlab.exe",
                    **execution_authority(),
                )
            self.assertEqual(receipt["status"], "BLOCKED")
            self.assertEqual(receipt["code"], "FIGURE_RUNTIME_UNAVAILABLE")
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
