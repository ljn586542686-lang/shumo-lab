from __future__ import annotations

import tempfile
import unittest
import json
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from pipeline_core.paper_quality.matlab_figure_pipeline import (
    FigureAssetQualityError,
    validate_figure_asset,
)
from pipeline_core.paper_quality.track_c_paper_expression import (
    TrackCPaperExpressionError,
    run_track_c_pass,
)
from pipeline_core.paper_template.paper_renderer import PaperRendererError, render_paper
from pipeline_core.tests.test_figure_f2_b_handoff import _execution_receipt, _manifest, _valid_png_bytes


def _valid_pdf_bytes() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Page /MediaBox [0 0 612 792] >> endobj\n"
        b"%%EOF\n"
    )


class FigureF3AProvenanceTests(unittest.TestCase):
    def _prepare(
        self,
        root: Path,
        receipt: dict,
        asset_bytes: bytes | None = None,
        figure_overrides: dict | None = None,
    ) -> tuple[Path, Path]:
        relative = Path(receipt["asset_path"])
        asset = root / relative
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(asset_bytes if asset_bytes is not None else _valid_png_bytes())
        return _manifest(root, receipt, figure_overrides=figure_overrides), asset

    def test_normal_png_passes_visual_qa(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "figure.png"
            asset.write_bytes(_valid_png_bytes())
            qa = validate_figure_asset(asset)
            self.assertEqual(qa["status"], "PASS")
            self.assertGreaterEqual(qa["dpi_x"], 300)

    def test_corrupted_png_fails_visual_qa(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "figure.png"
            asset.write_bytes(b"\x89PNG\r\n\x1a\ncorrupted")
            with self.assertRaisesRegex(FigureAssetQualityError, "FIGURE_ASSET_UNREADABLE|FIGURE_ASSET_FORMAT_INVALID"):
                validate_figure_asset(asset)

    def test_empty_file_fails_visual_qa(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "figure.png"
            asset.write_bytes(b"")
            with self.assertRaisesRegex(FigureAssetQualityError, "FIGURE_ASSET_MISSING"):
                validate_figure_asset(asset)

    def test_low_dpi_png_fails_visual_qa(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "figure.png"
            asset.write_bytes(_valid_png_bytes(dpi=72))
            with self.assertRaisesRegex(FigureAssetQualityError, "FIGURE_ASSET_DPI_TOO_LOW"):
                validate_figure_asset(asset)

    def test_abnormal_dimensions_fail_visual_qa(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "figure.png"
            asset.write_bytes(_valid_png_bytes(width=20, height=120))
            with self.assertRaisesRegex(FigureAssetQualityError, "FIGURE_ASSET_DIMENSIONS_INVALID"):
                validate_figure_asset(asset)

    def test_fully_transparent_rgba_fails_visual_qa(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "figure.png"
            asset.write_bytes(_valid_png_bytes(fully_transparent_rgba=True))
            with self.assertRaisesRegex(FigureAssetQualityError, "FIGURE_ASSET_EMPTY"):
                validate_figure_asset(asset)

    def test_pdf_asset_passes_visual_qa(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "figure.pdf"
            asset.write_bytes(_valid_pdf_bytes())
            def render(command: list[str], **_kwargs: object) -> SimpleNamespace:
                Path(command[-1]).with_suffix(".png").write_bytes(_valid_png_bytes(dpi=72))
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with patch("pipeline_core.paper_quality.matlab_figure_pipeline.shutil.which", return_value="pdftoppm"), patch(
                "pipeline_core.paper_quality.matlab_figure_pipeline.subprocess.run", side_effect=render
            ):
                qa = validate_figure_asset(asset)
            self.assertEqual(qa["status"], "PASS")
            self.assertEqual(qa["format"], "pdf")

    def test_pdf_without_real_renderer_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "figure.pdf"
            asset.write_bytes(_valid_pdf_bytes())
            with patch("pipeline_core.paper_quality.matlab_figure_pipeline.shutil.which", return_value=None):
                with self.assertRaisesRegex(FigureAssetQualityError, "FIGURE_PDF_RENDERER_UNAVAILABLE"):
                    validate_figure_asset(asset)

    def test_missing_public_caption_fails_track_c_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, _ = self._prepare(
                root,
                _execution_receipt("figure_1", "figures/figure_1.png"),
                figure_overrides={"public_title": ""},
            )
            with self.assertRaisesRegex(TrackCPaperExpressionError, "TRACK_C_FIGURE_CAPTION_REQUIRED"):
                run_track_c_pass(manifest, root / "out.json", root / "out", root / "receipt.json")

    def test_caption_with_latex_special_character_is_escaped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, _ = self._prepare(
                root,
                _execution_receipt("figure_1", "figures/figure_1.png"),
                figure_overrides={"public_title": "figure_%&#_caption"},
            )
            output_manifest = root / "track_c_output_manifest.json"
            run_track_c_pass(manifest, output_manifest, root / "track_c_output", root / "track_c_receipt.json", case_id="F2_B_TEST_CASE")
            render_paper(output_manifest, root / "paper")
            main = (root / "paper" / "main.tex").read_text(encoding="utf-8")
            expected = r"\PaperFigure{writer_content/figures/figure_1.png}{figure\_\%\&\#\_caption}{fig:figure_1}"
            self.assertIn(expected, main)
            self.assertNotIn(r"\caption{figure\_\%\&\#\_caption}", main)

    def test_missing_figure_purpose_fails_track_c_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, _ = self._prepare(
                root,
                _execution_receipt("figure_1", "figures/figure_1.png"),
                figure_overrides={"figure_purpose": None},
            )
            with self.assertRaisesRegex(TrackCPaperExpressionError, "FIGURE_PURPOSE_(REQUIRED|INVALID)"):
                run_track_c_pass(manifest, root / "out.json", root / "out", root / "receipt.json")

    def test_missing_paper_binding_section_fails_track_c_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, _ = self._prepare(
                root,
                _execution_receipt("figure_1", "figures/figure_1.png"),
                figure_overrides={
                    "paper_binding": {
                        "supported_claim": "The declared model structure explains the task result.",
                        "conclusion_reference": "task.model_explanation",
                    }
                },
            )
            with self.assertRaisesRegex(TrackCPaperExpressionError, "FIGURE_PAPER_BINDING_SECTION_REQUIRED"):
                run_track_c_pass(manifest, root / "out.json", root / "out", root / "receipt.json")

    def test_missing_supported_claim_fails_track_c_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, _ = self._prepare(
                root,
                _execution_receipt("figure_1", "figures/figure_1.png"),
                figure_overrides={
                    "paper_binding": {
                        "section": "Task model",
                        "conclusion_reference": "task.model_explanation",
                    }
                },
            )
            with self.assertRaisesRegex(TrackCPaperExpressionError, "FIGURE_PAPER_BINDING_SUPPORTED_CLAIM_REQUIRED"):
                run_track_c_pass(manifest, root / "out.json", root / "out", root / "receipt.json")

    def test_valid_approved_result_and_asset_hash_reach_renderer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, _ = self._prepare(root, _execution_receipt("figure_1", "figures/figure_1.png"))
            output_manifest = root / "track_c_output_manifest.json"
            run_track_c_pass(manifest, output_manifest, root / "track_c_output", root / "track_c_receipt.json", case_id="F2_B_TEST_CASE")
            payload = json.loads(output_manifest.read_text(encoding="utf-8"))
            figure = payload["track_c_pass"]["figure_assets"][0]
            self.assertTrue(figure["result_binding"]["approved"])
            self.assertEqual(len(figure["asset_binding"]["asset_sha256"]), 64)
            self.assertEqual(figure["figure_purpose"], "model_explanation")
            self.assertEqual(figure["paper_binding"]["section"], "Task model")
            render_paper(output_manifest, root / "paper")

    def test_missing_result_binding_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = _execution_receipt("figure_1", "figures/figure_1.png")
            receipt.pop("result_binding")
            manifest, _ = self._prepare(root, receipt)
            with self.assertRaisesRegex(TrackCPaperExpressionError, "TRACK_C_RESULT_BINDING_MISSING"):
                run_track_c_pass(manifest, root / "out.json", root / "out", root / "receipt.json")

    def test_unapproved_result_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = _execution_receipt("figure_1", "figures/figure_1.png")
            receipt["result_binding"]["approved"] = False
            manifest, _ = self._prepare(root, receipt)
            with self.assertRaisesRegex(TrackCPaperExpressionError, "TRACK_C_RESULT_NOT_APPROVED"):
                run_track_c_pass(manifest, root / "out.json", root / "out", root / "receipt.json")

    def test_wrong_result_sha_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = _execution_receipt("figure_1", "figures/figure_1.png")
            receipt["result_binding"]["result_sha256"] = "b" * 64
            manifest, _ = self._prepare(root, receipt)
            with self.assertRaisesRegex(TrackCPaperExpressionError, "TRACK_C_RESULT_CARD_HASH_MISMATCH"):
                run_track_c_pass(manifest, root / "out.json", root / "out", root / "receipt.json")

    def test_asset_modified_after_generation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = _execution_receipt("figure_1", "figures/figure_1.png")
            manifest, asset = self._prepare(root, receipt)
            asset.write_bytes(b"modified-after-generation")
            with self.assertRaisesRegex(TrackCPaperExpressionError, "FIGURE_ASSET_HASH_MISMATCH"):
                run_track_c_pass(manifest, root / "out.json", root / "out", root / "receipt.json")

    def test_renderer_rejects_asset_modified_after_track_c(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, _ = self._prepare(root, _execution_receipt("figure_1", "figures/figure_1.png"))
            output_manifest = root / "track_c_output_manifest.json"
            run_track_c_pass(manifest, output_manifest, root / "track_c_output", root / "track_c_receipt.json", case_id="F2_B_TEST_CASE")
            payload = json.loads(output_manifest.read_text(encoding="utf-8"))
            content_root = output_manifest.parent / payload["content_root"]
            (content_root / "figures" / "figure_1.png").write_bytes(b"tampered-after-track-c")
            with self.assertRaisesRegex(PaperRendererError, "FIGURE_ASSET_HASH_MISMATCH"):
                render_paper(output_manifest, root / "paper")


if __name__ == "__main__":
    unittest.main()
