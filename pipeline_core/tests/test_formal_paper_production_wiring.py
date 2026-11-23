from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipeline_core.paper_quality.track_c_paper_expression import run_track_c_pass
from pipeline_core.paper_template.paper_renderer import PaperRendererError, render_paper
from x5_surgical_utils import find_paper_pdf


class FormalPaperProductionWiringTests(unittest.TestCase):
    def _manifest(self, root: Path, *, legacy: bool = False, mixed: bool = False) -> Path:
        content_root = root / "writer_content"
        content_root.mkdir(parents=True)
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
        evaluation = {"strengths": "strengths", "weaknesses": "weaknesses", "improvements": "improvements"}
        (content_root / "model_evaluation.json").write_text(json.dumps(evaluation), encoding="utf-8")
        content["model_evaluation"] = "model_evaluation.json"
        for index in range(1, 4):
            filename = f"question_content_q{index}.json"
            (content_root / filename).write_text(
                json.dumps({"intro": "intro", "sections": [{"title": "具体模型标题", "content": "body"}]}),
                encoding="utf-8",
            )
            content[f"question_content_q{index}"] = filename
        if legacy or mixed:
            filename = "legacy.tex"
            (content_root / filename).write_text("legacy\n", encoding="utf-8")
            content["model_building_q1"] = filename
        payload = {
            "case_id": "TEST_CASE",
            "writer_schema_version": "WRITER_SCHEMA_V2",
            "manifest_mode": "LEGACY_COMPATIBILITY",
            "question_count": 3,
            "content_root": "writer_content",
            "content": content,
        }
        if legacy and not mixed:
            payload.pop("writer_schema_version")
            for key in list(content):
                if key not in {"model_building_q1"}:
                    del payload["content"][key]
            payload["content"]["title"] = "title.tex"
        manifest = root / "writer_manifest.json"
        manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return manifest

    def test_formal_renderer_requires_actual_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._manifest(root)
            with self.assertRaises(PaperRendererError) as context:
                render_paper(manifest, root / "paper", formal=True, expected_case_id="TEST_CASE")
            self.assertIn("TRACK_C_RECEIPT_INVALID", str(context.exception))

    def test_fake_manifest_pass_without_receipt_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._manifest(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["track_c_pass"] = {"status": "PASS"}
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(PaperRendererError) as context:
                render_paper(manifest, root / "paper", formal=True, expected_case_id="TEST_CASE")
            self.assertIn("TRACK_C_RECEIPT_INVALID", str(context.exception))

    def test_legacy_and_mixed_schema_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = self._manifest(root / "legacy", legacy=True)
            with self.assertRaises(PaperRendererError) as context:
                render_paper(legacy, root / "legacy_paper", formal=True, expected_case_id="TEST_CASE")
            self.assertIn("LEGACY_WRITER_SCHEMA_NOT_ALLOWED_FOR_FORMAL_BUILD", str(context.exception))

            mixed = self._manifest(root / "mixed", mixed=True)
            with self.assertRaises(PaperRendererError) as context:
                render_paper(mixed, root / "mixed_paper", formal=True, expected_case_id="TEST_CASE")
            self.assertIn("MIXED_WRITER_SCHEMA_FORBIDDEN", str(context.exception))

    def test_track_c_receipt_is_consumed_by_formal_renderer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._manifest(root)
            output_manifest = root / "track_c_output_manifest.json"
            receipt = root / "track_c_receipt.json"
            run_track_c_pass(manifest, output_manifest, root / "track_c_output", receipt, case_id="TEST_CASE")
            record = render_paper(
                output_manifest,
                root / "paper",
                formal=True,
                receipt_path=receipt,
                expected_case_id="TEST_CASE",
            )
            self.assertTrue(record["formal_mode"])
            self.assertTrue(record["track_c_runtime_consumed"])

    def test_final_candidate_is_not_a_canonical_pdf_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case_dir = Path(directory) / "case"
            (case_dir / "paper" / "final_candidate").mkdir(parents=True)
            (case_dir / "paper" / "final_candidate" / "main.pdf").write_bytes(b"%PDF-review")
            self.assertIsNone(find_paper_pdf(case_dir))
            (case_dir / "paper").mkdir(exist_ok=True)
            (case_dir / "paper" / "main.pdf").write_bytes(b"%PDF-canonical")
            self.assertEqual(find_paper_pdf(case_dir), case_dir / "paper" / "main.pdf")


if __name__ == "__main__":
    unittest.main()
