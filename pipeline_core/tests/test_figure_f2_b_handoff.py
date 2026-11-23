from __future__ import annotations

import json
import hashlib
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from pipeline_core.paper_quality.matlab_figure_pipeline import (
    FIGURE_EXECUTION_RECEIPT_VERSION,
    FIGURE_SPEC_VERSION,
    bind_gate_evidence,
    canonical_record_sha256,
    discover_matlab_executable,
    execute_matlab_figure,
    generate_declared_matlab_figures,
    validate_figure_spec,
)
from pipeline_core.paper_quality.track_c_paper_expression import (
    TrackCPaperExpressionError,
    run_track_c_pass,
)
from pipeline_core.paper_template.paper_renderer import render_paper
from pipeline_core.python_tools.human_gate import evaluate_human_gate


GATE3_TEXT = """# Gate 3\n\n- [x] Result summary checked.\n\n## 决策\n\n- [x] 通过，允许进入验证、证据链和论文材料阶段。\n- [ ] 退回模型实现或结果验证阶段。\n\n确认人：Fixture Reviewer\n\n时间：2026-08-21T00:00:00+00:00\n"""


def _result_contract() -> dict:
    return {
        "version": "result_acceptance_contract_v1",
        "default_scenario_fields": ["scenario_id"],
        "result_cards": [
            {
                "result_id": "result_figure",
                "display_value": "1.0",
                "unit": "unit",
                "source_binding": "output/result_summary.json#result_figure",
                "paper_location": "question_content_task",
                "approved": True,
                "scenario": {"scenario_id": "fixture"},
                "applicable_scenario_fields": ["scenario_id"],
            }
        ],
        "answer_cards": [
            {
                "task_id": "task",
                "question_requirement": "Return the bounded fixture result.",
                "paper_output": "The bounded fixture answer is shown.",
                "representative_value": "1.0 unit",
                "paper_location": "question_content_task",
                "answer_marker": "answer-marker",
                "answer_type": "quantitative",
                "result_card_ids": ["result_figure"],
                "source_bindings": [],
            }
        ],
        "limitation_impacts": [],
        "input_conditions": [],
        "visual_bindings": [],
        "method_output_links": [],
    }


def _figure_spec(figure_id: str = "figure_1", output_file: str = "figures/figure_1.png") -> dict:
    card = _result_contract()["result_cards"][0]
    return {
        "figure_id": figure_id,
        "backend": "MATLAB",
        "figure_type": "line",
        "purpose": "model_explanation",
        "title": "Generated figure",
        "x_label": "x",
        "y_label": "y",
        "source_binding": {
            "source_id": "result_figure",
            "source_type": "result_card",
            "sha256": canonical_record_sha256(card),
        },
        "output_file": output_file,
        "dpi": 400,
        "series": [{"name": "A", "x": [1, 2, 3], "y": [1, 3, 2]}],
    }


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _valid_png_bytes(
    width: int = 120,
    height: int = 120,
    dpi: float = 400.0,
    *,
    fully_transparent_rgba: bool = False,
) -> bytes:
    rows = []
    for y in range(height):
        pixels = bytearray()
        for x in range(width):
            rgb = (35, 90, 160) if x == y % width else (245, 245, 245)
            pixels.extend((*rgb, 0) if fully_transparent_rgba else rgb)
        rows.append(b"\0" + bytes(pixels))
    ppm = round(dpi / 0.0254)
    return b"\x89PNG\r\n\x1a\n" + b"".join(
        (
            _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6 if fully_transparent_rgba else 2, 0, 0, 0)),
            _png_chunk(b"pHYs", struct.pack(">IIB", ppm, ppm, 1)),
            _png_chunk(b"IDAT", zlib.compress(b"".join(rows))),
            _png_chunk(b"IEND", b""),
        )
    )


def _execution_receipt(figure_id: str, asset_path: str, *, status: str = "PASS") -> dict:
    if not asset_path.startswith("writer_content/"):
        asset_path = f"writer_content/{asset_path}"
    card = _result_contract()["result_cards"][0]
    contract_sha = canonical_record_sha256(_result_contract())
    spec_sha = canonical_record_sha256(validate_figure_spec(_figure_spec(figure_id, asset_path.removeprefix("writer_content/").replace("figure_1", figure_id))))
    script_sha = hashlib.sha256(b"% generated fixture script\n").hexdigest()
    gate_evaluation = {
        "gate_id": "GATE3",
        "gate_status": "PASS",
        "decision": "\u901a\u8fc7\uff0c\u5141\u8bb8\u8fdb\u5165\u9a8c\u8bc1\u3001\u8bc1\u636e\u94fe\u548c\u8bba\u6587\u6750\u6599\u9636\u6bb5\u3002",
        "decision_class": "APPROVE",
        "confirmed_by": "Fixture Reviewer",
        "confirmed_at": "2026-08-21T00:00:00+00:00",
        "reasons": [],
        "path": "human_review/gate_3_result_acceptance.md",
    }
    gate_file_sha = hashlib.sha256(GATE3_TEXT.encode("utf-8")).hexdigest()
    gate_material = {**gate_evaluation, "gate_file_sha256": gate_file_sha}
    gate_sha = canonical_record_sha256(gate_material)
    asset_sha256 = hashlib.sha256(_valid_png_bytes()).hexdigest()
    return {
        "schema_version": FIGURE_EXECUTION_RECEIPT_VERSION,
        "runtime_origin": "execute_matlab_figure",
        "created_by_actual_matlab_figure_runtime": True,
        "status": status,
        "code": None if status == "PASS" else "FIGURE_GENERATION_FAILED",
        "figure_id": figure_id,
        "asset_path": asset_path,
        "asset_format": "png",
        "generation_status": "GENERATED" if status == "PASS" else "NOT_GENERATED",
        "execution_status": "SUCCEEDED" if status == "PASS" else "FAILED",
        "spec_sha256": spec_sha,
        "script_sha256": script_sha,
        "result_acceptance_contract_sha256": contract_sha,
        "gate3_evidence_sha256": gate_sha,
        "source_binding": {
            "source_id": "result_figure",
            "source_type": "result_card",
            "sha256": canonical_record_sha256(card),
            "approved": True,
        },
        "result_binding": {
            "result_id": "result_figure",
            "result_type": "result_card",
            "result_sha256": canonical_record_sha256(card),
            "approved": True,
            "source_contract": "result_acceptance_contract",
            "result_acceptance_contract_sha256": contract_sha,
            "gate3_evidence_sha256": gate_sha,
        },
        "asset_binding": {
            "asset_sha256": asset_sha256,
            "generated_at": "2026-08-21T00:00:00+00:00",
            "generator_version": FIGURE_SPEC_VERSION,
            "spec_sha256": spec_sha,
            "script_sha256": script_sha,
        },
    }


def _manifest(
    root: Path,
    receipt: dict | None = None,
    *,
    asset_path: str = "figures/figure_1.png",
    figure_overrides: dict | None = None,
) -> Path:
    content_root = root / "writer_content"
    content_root.mkdir(parents=True, exist_ok=True)
    gate_path = root / "human_review" / "gate_3_result_acceptance.md"
    gate_path.parent.mkdir(parents=True, exist_ok=True)
    gate_path.write_text(GATE3_TEXT, encoding="utf-8")
    if receipt is not None:
        live_gate = bind_gate_evidence(root, evaluate_human_gate(root, "GATE3"))
        receipt["gate3_evidence_sha256"] = live_gate["evidence_sha256"]
        if isinstance(receipt.get("result_binding"), dict):
            receipt["result_binding"]["gate3_evidence_sha256"] = live_gate["evidence_sha256"]
    content: dict[str, str] = {}
    for key in (
        "title",
        "authors",
        "abstract",
        "keywords",
        "problem_restatement",
        "model_assumptions",
        "model_assumptions_note",
        "notation",
        "references",
        "appendix",
        "problem_analysis_task",
    ):
        filename = f"{key}.tex"
        text = "A bounded competition-paper fixture.\n"
        if key == "model_assumptions":
            text = "\\item The fixture uses a bounded assumption.\n"
        if key == "references":
            text = "\\bibitem{fixture} Fixture reference.\n"
        (content_root / filename).write_text(text, encoding="utf-8")
        content[key] = filename
    (content_root / "question_content_task.json").write_text(
        json.dumps(
            {
                "intro": "The task is stated with an explicit result boundary.\\cite{fixture}",
                "sections": [{"title": "Task model", "content": "The model content is bounded: 1.0 unit; answer-marker."}],
                "closing_optional": "",
            }
        ),
        encoding="utf-8",
    )
    content["question_content_task"] = "question_content_task.json"
    (content_root / "model_evaluation.json").write_text(
        json.dumps({"strengths": "strength", "weaknesses": "weakness", "improvements": "improvement"}),
        encoding="utf-8",
    )
    content["model_evaluation"] = "model_evaluation.json"
    figure = {
        "figure_id": "figure_1",
        "formal": True,
        "render_backend": "MATLAB",
        "matlab_script_path": "writer_content/figure_1.m",
        "asset_path": receipt.get("asset_path") if receipt is not None else asset_path,
        "public_title": "Generated figure",
        "source_binding": _figure_spec()["source_binding"],
        "matlab_figure_spec": _figure_spec(),
        "first_discussion": "Task model",
        "figure_purpose": "model_explanation",
        "paper_binding": {
            "section": "Task model",
            "supported_claim": "The declared model structure explains the task result.",
            "conclusion_reference": "task.model_explanation",
        },
    }
    if figure_overrides:
        figure.update(figure_overrides)
    if receipt is not None:
        figure["execution_receipt"] = receipt
    payload = {
        "case_id": "F2_B_TEST_CASE",
        "writer_schema_version": "WRITER_SCHEMA_V2",
        "manifest_mode": "LEGACY_COMPATIBILITY",
        "task_contract": {
            "tasks": [
                {
                    "task_id": "task",
                    "label": "Task",
                    "analysis_content_key": "problem_analysis_task",
                    "content_key": "question_content_task",
                }
            ]
        },
        "content_root": "writer_content",
        "content": content,
        "writing_policy": {
            "version": "controlled_writing_layer_v2",
            "appendix_code_mode": "PENDING_USER_POLICY",
            "figure_placement": {
                "formal_figure_backend": "MATLAB",
                "figures": [figure] if receipt is not None else [],
            },
            "result_acceptance_contract": _result_contract(),
        },
    }
    manifest = root / "writer_manifest.json"
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (content_root / "figure_1.m").write_text("% generated fixture script\n", encoding="utf-8")
    if receipt is not None:
        actual_script_sha = hashlib.sha256((content_root / "figure_1.m").read_bytes()).hexdigest()
        receipt["script_path"] = str((content_root / "figure_1.m").resolve())
        receipt["script_sha256"] = actual_script_sha
        if isinstance(receipt.get("asset_binding"), dict):
            receipt["asset_binding"]["script_sha256"] = actual_script_sha
        receipt_path = content_root / "figure_1_receipt.json"
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        figure["execution_receipt"] = "writer_content/figure_1_receipt.json"
    content["figure_1_script"] = "figure_1.m"
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


class FigureF2BHandoffTests(unittest.TestCase):
    def test_valid_receipt_passes_track_c_and_reaches_renderer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "writer_content" / "figures" / "figure_1.png"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(_valid_png_bytes())
            manifest = _manifest(root, _execution_receipt("figure_1", "figures/figure_1.png"))
            output_manifest = root / "track_c_output_manifest.json"
            track_receipt = root / "track_c_receipt.json"
            result = run_track_c_pass(manifest, output_manifest, root / "track_c_output", track_receipt, case_id="F2_B_TEST_CASE")
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["figure_assets"][0]["figure_id"], "figure_1")
            render_record = render_paper(output_manifest, root / "paper")
            self.assertEqual(render_record["figure_count"], 1)
            main = (root / "paper" / "main.tex").read_text(encoding="utf-8")
            self.assertIn(r"\PaperFigure{writer_content/figures/figure_1.png}", main)
            self.assertIn(r"\includegraphics", main)
            self.assertTrue((root / "paper" / "writer_content" / "figures" / "figure_1.png").is_file())

    def test_missing_asset_fails_track_c_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _manifest(root, _execution_receipt("figure_1", "figures/missing.png"))
            with self.assertRaisesRegex(TrackCPaperExpressionError, "FIGURE_ASSET_(MISSING|NOT_FOUND)"):
                run_track_c_pass(manifest, root / "track_c_output_manifest.json", root / "track_c_output", root / "track_c_receipt.json")

    def test_failed_matlab_execution_is_blocked_before_renderer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "writer_content" / "figures" / "figure_1.png"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"stale-asset")
            manifest = _manifest(root, _execution_receipt("figure_1", "figures/figure_1.png", status="FAIL"))
            with self.assertRaisesRegex(TrackCPaperExpressionError, "FIGURE_GENERATION_FAILED"):
                run_track_c_pass(manifest, root / "track_c_output_manifest.json", root / "track_c_output", root / "track_c_receipt.json")

    def test_inline_receipt_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "writer_content" / "figures" / "figure_1.png"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(_valid_png_bytes())
            receipt = _execution_receipt("figure_1", "figures/figure_1.png")
            manifest = _manifest(root, receipt)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["writing_policy"]["figure_placement"]["figures"][0]["execution_receipt"] = receipt
            manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            with self.assertRaisesRegex(TrackCPaperExpressionError, "TRACK_C_FIGURE_RECEIPT_REQUIRED"):
                run_track_c_pass(manifest, root / "out.json", root / "out", root / "track.json", case_root=root)

    def test_asset_outside_case_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as external_directory:
            root = Path(directory)
            external = Path(external_directory) / "figure.png"
            external.write_bytes(_valid_png_bytes())
            receipt = _execution_receipt("figure_1", "figures/figure_1.png")
            receipt["asset_path"] = str(external)
            manifest = _manifest(root, receipt)
            with self.assertRaisesRegex(TrackCPaperExpressionError, "TRACK_C_FIGURE_ASSET_ESCAPES_CASE"):
                run_track_c_pass(manifest, root / "out.json", root / "out", root / "track.json", case_root=root)

    def test_figure_is_inserted_at_first_discussion_not_after_all_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "writer_content" / "figures" / "figure_1.png"
            asset.parent.mkdir(parents=True)
            asset.write_bytes(_valid_png_bytes())
            manifest = _manifest(root, _execution_receipt("figure_1", "figures/figure_1.png"))
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            content_root = root / "writer_content"
            (content_root / "problem_analysis_task2.tex").write_text("Second task analysis.", encoding="utf-8")
            (content_root / "question_content_task2.json").write_text(
                json.dumps(
                    {
                        "intro": "Second task intro.",
                        "sections": [{"title": "Second task model", "content": "Second task content."}],
                        "closing_optional": "",
                    }
                ),
                encoding="utf-8",
            )
            payload["content"]["problem_analysis_task2"] = "problem_analysis_task2.tex"
            payload["content"]["question_content_task2"] = "question_content_task2.json"
            payload["task_contract"]["tasks"].append(
                {
                    "task_id": "task2",
                    "label": "Task 2",
                    "analysis_content_key": "problem_analysis_task2",
                    "content_key": "question_content_task2",
                }
            )
            payload["writing_policy"]["result_acceptance_contract"]["answer_cards"].append(
                {
                    "task_id": "task2",
                    "question_requirement": "Return a bounded second answer.",
                    "paper_output": "Second task content.",
                    "representative_value": "Second task content.",
                    "paper_location": "question_content_task2",
                    "answer_marker": "Second task content.",
                    "answer_type": "qualitative",
                    "result_card_ids": [],
                    "source_bindings": ["fixture"],
                }
            )
            updated_contract_sha = canonical_record_sha256(payload["writing_policy"]["result_acceptance_contract"])
            receipt_path = root / "writer_content" / "figure_1_receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["result_acceptance_contract_sha256"] = updated_contract_sha
            receipt["result_binding"]["result_acceptance_contract_sha256"] = updated_contract_sha
            receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
            manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            output_manifest = root / "track_c_output_manifest.json"
            run_track_c_pass(manifest, output_manifest, root / "track_c_output", root / "track_c_receipt.json", case_root=root)
            render_paper(
                output_manifest,
                root / "paper",
                formal=True,
                receipt_path=root / "track_c_receipt.json",
                expected_case_id="F2_B_TEST_CASE",
            )
            main = (root / "paper" / "main.tex").read_text(encoding="utf-8")
            self.assertLess(main.index(r"\PaperFigure"), main.index(r"\subsection{Task 2}"))

    def test_declared_generation_stage_emits_case_local_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _manifest(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            figure = {
                "figure_id": "figure_1",
                "formal": True,
                "render_backend": "MATLAB",
                "matlab_script_path": "output/figures/figure_1.m",
                "public_title": "Generated figure",
                "source_binding": _figure_spec()["source_binding"],
                "matlab_figure_spec": _figure_spec(output_file="figure_1.png"),
                "first_discussion": "Task model",
                "figure_purpose": "model_explanation",
                "paper_binding": {
                    "section": "Task model",
                    "supported_claim": "The declared model structure explains the task result.",
                    "conclusion_reference": "task.model_explanation",
                },
            }
            payload["writing_policy"]["figure_placement"]["figures"] = [figure]
            manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            executable = root / "matlab.exe"
            executable.write_text("mock", encoding="utf-8")

            def successful_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
                output = Path(str(kwargs["cwd"])) / "figure_1.png"
                output.write_bytes(_valid_png_bytes())
                return subprocess.CompletedProcess(command, 0, "ok", "")

            with patch(
                "pipeline_core.paper_quality.matlab_figure_pipeline.subprocess.run", side_effect=successful_run
            ):
                generation = generate_declared_matlab_figures(
                    manifest,
                    root,
                    executable=executable,
                    derived_manifest=root / "writer_manifest_with_figures.json",
                )
            self.assertEqual(generation["status"], "PASS")
            derived = Path(generation["writer_manifest"])
            derived_payload = json.loads(derived.read_text(encoding="utf-8"))
            declared = derived_payload["writing_policy"]["figure_placement"]["figures"][0]
            self.assertEqual(declared["execution_receipt"], "output/figures/receipts/figure_1.json")
            self.assertEqual(declared["asset_path"], "output/figures/figure_1.png")
            output_manifest = root / "track_c_output_manifest.json"
            run_track_c_pass(
                derived,
                output_manifest,
                root / "track_c_output",
                root / "track_c_receipt.json",
                case_root=root,
            )
            render_paper(
                output_manifest,
                root / "paper",
                formal=True,
                receipt_path=root / "track_c_receipt.json",
                expected_case_id="F2_B_TEST_CASE",
            )

    def test_real_matlab_asset_reaches_real_xelatex_pdf(self) -> None:
        if os.environ.get("RUN_REAL_MATLAB_FIGURE_E2E") != "1":
            self.skipTest("set RUN_REAL_MATLAB_FIGURE_E2E=1 for the controlled runtime smoke")
        matlab = discover_matlab_executable(r"D:\Program Files\MATLAB\R2025b\bin\matlab.exe")
        xelatex = Path(shutil.which('xelatex') or 'xelatex.exe')
        try:
            xelatex_available = xelatex.is_file()
        except OSError:
            xelatex_available = True
        if matlab is None or not xelatex_available:
            self.skipTest("real MATLAB or XeLaTeX executable unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = _manifest(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["writing_policy"]["figure_placement"]["figures"] = [
                {
                    "figure_id": "figure_1",
                    "formal": True,
                    "render_backend": "MATLAB",
                    "matlab_script_path": "output/figures/figure_1.m",
                    "public_title": "Generated figure",
                    "source_binding": _figure_spec()["source_binding"],
                    "matlab_figure_spec": _figure_spec(output_file="figure_1.png"),
                    "first_discussion": "Task model",
                    "figure_purpose": "model_explanation",
                    "paper_binding": {
                        "section": "Task model",
                        "supported_claim": "The declared model structure explains the task result.",
                        "conclusion_reference": "task.model_explanation",
                    },
                }
            ]
            manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            generation = generate_declared_matlab_figures(
                manifest,
                root,
                executable=matlab,
                timeout_seconds=120,
                derived_manifest=root / "writer_manifest_with_figures.json",
            )
            self.assertEqual(generation["status"], "PASS", generation)
            derived_manifest = Path(generation["writer_manifest"])
            output_manifest = root / "track_c_output_manifest.json"
            track_receipt = root / "track_c_receipt.json"
            run_track_c_pass(
                derived_manifest,
                output_manifest,
                root / "track_c_output",
                track_receipt,
                case_id="F2_B_TEST_CASE",
                case_root=root,
            )
            paper = root / "paper"
            render_paper(output_manifest, paper, formal=True, receipt_path=track_receipt, expected_case_id="F2_B_TEST_CASE")
            env = os.environ.copy()
            fontconfig = xelatex.parent.parent.parent / "tlpkg" / "tlpostcode" / "xetex" / "conf" / "fonts.conf"
            if fontconfig.is_file():
                env["FONTCONFIG_FILE"] = str(fontconfig)
            for _ in range(2):
                completed = subprocess.run(
                    [str(xelatex), "-interaction=nonstopmode", "-halt-on-error", "main.tex"],
                    cwd=paper,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertTrue((paper / "main.pdf").is_file())
            self.assertGreater((paper / "main.pdf").stat().st_size, 0)
            artifact_directory = os.environ.get("REAL_MATLAB_FIGURE_E2E_ARTIFACT_DIR")
            if artifact_directory:
                artifact_root = Path(artifact_directory).resolve()
                artifact_root.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / generation["generated_assets"][0], artifact_root / "figure_1.png")
                shutil.copy2(paper / "main.pdf", artifact_root / "main.pdf")
                shutil.copy2(paper / "main.tex", artifact_root / "main.tex")


if __name__ == "__main__":
    unittest.main()
