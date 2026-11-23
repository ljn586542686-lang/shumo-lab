from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipeline_core.paper_quality.track_c_paper_expression import (
    TrackCPaperExpressionError,
    run_track_c_pass,
)


class TrackCPaperExpressionTests(unittest.TestCase):
    def _manifest(self, root: Path, heading: str = "连续日启动排程模型") -> Path:
        content_root = root / "writer_content"
        content_root.mkdir()
        content = {}
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
        ):
            filename = f"{key}.tex"
            (content_root / filename).write_text(f"{key} content\n", encoding="utf-8")
            content[key] = filename
        evaluation = {
            "strengths": "strengths",
            "weaknesses": "weaknesses",
            "improvements": "improvements",
        }
        (content_root / "model_evaluation.json").write_text(
            json.dumps(evaluation), encoding="utf-8"
        )
        content["model_evaluation"] = "model_evaluation.json"
        for index in range(1, 4):
            filename = f"question_content_q{index}.json"
            question = {
                "intro": f"Question {index} introduction.",
                "sections": [{"title": heading, "content": f"Question {index} content."}],
                "closing_optional": "",
            }
            (content_root / filename).write_text(
                json.dumps(question), encoding="utf-8"
            )
            content[f"question_content_q{index}"] = filename
        manifest = root / "writer_manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "question_count": 3,
                    "manifest_mode": "LEGACY_COMPATIBILITY",
                    "content_root": "writer_content",
                    "content": content,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return manifest

    def test_track_c_output_is_the_renderer_input_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_manifest = self._manifest(root)
            output_manifest = root / "track_c_output_manifest.json"
            receipt_path = root / "track_c_runtime_receipt.json"
            receipt = run_track_c_pass(
                input_manifest,
                output_manifest,
                root / "track_c_output",
                receipt_path,
            )
            self.assertEqual(receipt["status"], "PASS")
            self.assertTrue(receipt["track_c_consumed_by_final_writer_chain"])
            self.assertEqual(receipt["renderer_input"], str(output_manifest.resolve()))
            output = json.loads(output_manifest.read_text(encoding="utf-8"))
            self.assertEqual(output["track_c_pass"]["status"], "PASS")
            self.assertTrue(
                (root / "track_c_output" / "content" / "question_content_q1.json").is_file()
            )

    def test_generic_heading_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_manifest = self._manifest(root, heading="从集中配种到连续启动")
            with self.assertRaises(TrackCPaperExpressionError) as context:
                run_track_c_pass(
                    input_manifest,
                    root / "track_c_output_manifest.json",
                    root / "track_c_output",
                    root / "track_c_runtime_receipt.json",
                )
            self.assertIn("TRACK_C_GENERIC_SUBHEADING", str(context.exception))

    def test_track_c_accepts_explicit_named_task_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_manifest = self._manifest(root)
            payload = json.loads(input_manifest.read_text(encoding="utf-8"))
            content_root = root / "writer_content"
            named_question = {
                "intro": "空间配置的处理说明。",
                "sections": [{"title": "约束建模", "content": "给出可行域。"}],
                "closing_optional": "",
            }
            (content_root / "question_content_layout.json").write_text(
                json.dumps(named_question, ensure_ascii=False), encoding="utf-8"
            )
            (content_root / "problem_analysis_layout.tex").write_text("空间分析。", encoding="utf-8")
            for index in range(1, 4):
                payload["content"].pop(f"question_content_q{index}")
            payload["content"]["question_content_layout"] = "question_content_layout.json"
            payload["content"]["problem_analysis_layout"] = "problem_analysis_layout.tex"
            payload.pop("question_count")
            payload["task_contract"] = {
                "tasks": [
                    {
                        "task_id": "layout",
                        "label": "空间配置",
                        "analysis_content_key": "problem_analysis_layout",
                        "content_key": "question_content_layout",
                    }
                ]
            }
            input_manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            receipt = run_track_c_pass(
                input_manifest,
                root / "track_c_output_manifest.json",
                root / "track_c_output",
                root / "track_c_runtime_receipt.json",
            )
            self.assertEqual(receipt["status"], "PASS")
            self.assertEqual(receipt["review"]["writing_policy"]["mode"], "LEGACY_COMPATIBILITY_BYPASS")

    def test_authoritative_case_cannot_bypass_v4_with_legacy_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_manifest = self._manifest(root)
            (root / "case_config.json").write_text(
                json.dumps({"state_authority": "case_config.json"}), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                TrackCPaperExpressionError,
                "AUTHORITATIVE_CASE_CURRENT_FORMAL_MANIFEST_REQUIRED",
            ):
                run_track_c_pass(
                    input_manifest,
                    root / "track_c_output_manifest.json",
                    root / "track_c_output",
                    root / "track_c_receipt.json",
                    case_root=root,
                )

    def test_new_authoritative_case_requires_declared_v5_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_manifest = self._manifest(root)
            payload = json.loads(input_manifest.read_text(encoding="utf-8"))
            content_root = root / "writer_content"
            (content_root / "analysis_q1.tex").write_text("问题一分析。", encoding="utf-8")
            payload["manifest_mode"] = "CURRENT_FORMAL"
            payload["writer_schema_version"] = "WRITER_SCHEMA_V2"
            payload["content"]["problem_analysis_q1"] = "analysis_q1.tex"
            payload["task_contract"] = {"tasks": [{
                "task_id": "q1",
                "label": "问题一",
                "analysis_content_key": "problem_analysis_q1",
                "content_key": "question_content_q1",
            }]}
            payload["writing_policy"] = {"version": "controlled_writing_layer_v4"}
            input_manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            (root / "case_config.json").write_text(
                json.dumps({
                    "state_authority": "case_config.json",
                    "writing_contract_version": "controlled_writing_layer_v5",
                    "argument_blueprint_required": True,
                }),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                TrackCPaperExpressionError,
                "AUTHORITATIVE_CASE_WRITING_POLICY_REQUIRED:controlled_writing_layer_v5",
            ):
                run_track_c_pass(
                    input_manifest,
                    root / "track_c_output_manifest.json",
                    root / "track_c_output",
                    root / "track_c_receipt.json",
                    case_root=root,
                )


if __name__ == "__main__":
    unittest.main()
