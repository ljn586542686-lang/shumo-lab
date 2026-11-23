from __future__ import annotations

import tempfile
import unittest
import hashlib
from pathlib import Path

from pipeline_core.paper_quality.pdf_layout_checks import (
    PDFLayoutCheckError,
    build_layout_report,
    check_pdf_artifact,
    check_page_layout_manifest,
    check_xelatex_log,
    validate_manual_visual_review,
)


class PDFLayoutChecksTests(unittest.TestCase):
    def _write_real_pdf(self, path: Path) -> None:
        try:
            from pypdf import PdfWriter
        except ImportError:
            try:
                from PyPDF2 import PdfWriter
            except ImportError:
                objects = [
                    b"<< /Type /Catalog /Pages 2 0 R >>",
                    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << >> /Contents 4 0 R >>",
                    b"<< /Length 0 >>\nstream\n\nendstream",
                ]
                body = bytearray(b"%PDF-1.4\n")
                offsets = [0]
                for index, value in enumerate(objects, 1):
                    offsets.append(len(body))
                    body.extend(f"{index} 0 obj\n".encode("ascii"))
                    body.extend(value)
                    body.extend(b"\nendobj\n")
                xref = len(body)
                body.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
                body.extend(b"0000000000 65535 f \n")
                for offset in offsets[1:]:
                    body.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
                body.extend(
                    f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
                )
                path.write_bytes(bytes(body))
                return
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        with path.open("wb") as stream:
            writer.write(stream)

    def test_xelatex_log_and_page_manifest_are_advisory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "pass.log"
            log.write_text("This is XeTeX.\nOutput written on main.pdf.\n", encoding="utf-8")
            self.assertEqual(check_xelatex_log(log)["status"], "PASS")
            report = check_page_layout_manifest(
                {
                    "nonessential_blank_threshold": 0.30,
                    "pages": [
                        {"page": 1, "text_coverage_ratio": 0.72},
                        {"page": 2, "text_coverage_ratio": 0.55, "has_figure": True, "caption_present": True, "figure_source_bound": True},
                    ],
                }
            )
            self.assertEqual(report["status"], "PASS")

    def test_box_warning_and_manual_blank_page_are_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "warn.log"
            log.write_text("Overfull \\hbox (12.0pt too wide)\n", encoding="utf-8")
            self.assertEqual(check_xelatex_log(log)["status"], "WARNING")
            self.assertEqual(check_xelatex_log(log, strict_layout=True)["status"], "FAIL")
            report = check_page_layout_manifest({"pages": [{"page": 1, "text_coverage_ratio": 0.0}]})
            self.assertEqual(report["status"], "WARNING")

    def test_actual_pdf_is_read_and_reported_without_layout_claims(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "main.pdf"
            log = root / "main.log"
            self._write_real_pdf(pdf)
            log.write_text("Output written on main.pdf.\n", encoding="utf-8")
            artifact = check_pdf_artifact(pdf)
            self.assertEqual(artifact["page_count"], 1)
            self.assertIn(artifact["status"], {"PASS", "WARNING"})
            report = build_layout_report([log], pdf_path=pdf)
            self.assertIn(report["status"], {"PASS", "WARNING"})

    def test_missing_or_unreadable_pdf_is_hard_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(PDFLayoutCheckError, "PDF_ARTIFACT_MISSING"):
                check_pdf_artifact(root / "missing.pdf")
            broken = root / "broken.pdf"
            broken.write_text("not a PDF", encoding="utf-8")
            with self.assertRaisesRegex(PDFLayoutCheckError, "PDF_ARTIFACT_UNREADABLE"):
                check_pdf_artifact(broken)

    def test_visual_review_must_cover_exact_hash_and_every_page(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "main.pdf"
            self._write_real_pdf(pdf)
            review = {
                "schema_version": "manual_pdf_visual_review_v2",
                "status": "PASS",
                "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
                "page_count": 1,
                "reviewed_pages": [1],
                "checks": {
                    "black_background_absent": True,
                    "cropping_absent": True,
                    "garbled_text_absent": True,
                    "unexpected_blank_page_absent": True,
                    "abnormal_bottom_whitespace_absent": True,
                    "figures_and_tables_readable": True,
                    "figure_label_language_consistent": True,
                    "formula_overflow_absent": True,
                    "formula_numbering_and_reference_consistent": True,
                    "paragraph_indent_consistent": True,
                    "title_abstract_keywords_layout_consistent": True,
                    "citation_marker_consistent": True,
                    "internal_engineering_terms_absent": True,
                },
                "defects": [],
                "reviewer": "test-reviewer",
                "reviewed_at": "2026-08-24T00:00:00+00:00",
                "human_review_required_before_gate4": True,
            }
            self.assertEqual(validate_manual_visual_review(review, pdf)["status"], "PASS")
            review["reviewed_pages"] = []
            self.assertEqual(
                validate_manual_visual_review(review, pdf)["reason"],
                "PDF_VISUAL_REVIEW_INCOMPLETE_PAGE_COVERAGE",
            )
            review["reviewed_pages"] = [1]
            review["pdf_sha256"] = "0" * 64
            self.assertEqual(
                validate_manual_visual_review(review, pdf)["reason"],
                "PDF_VISUAL_REVIEW_HASH_MISMATCH",
            )


if __name__ == "__main__":
    unittest.main()
