"""Check LaTeX build artifacts without compiling."""

from __future__ import annotations

from pathlib import Path


def check_latex_outputs(case_dir: str | Path) -> dict:
    paper = Path(case_dir) / "paper"
    pdf = paper / "main.pdf"
    log = paper / "main.log"
    report = {"pdf_exists": pdf.exists(), "log_exists": log.exists(), "status": "warning"}
    if pdf.exists() and log.exists():
        text = log.read_text(encoding="utf-8", errors="ignore")
        bad = ["LaTeX Error", "Undefined references", "File `", "Fatal"]
        report["blocking_patterns"] = [p for p in bad if p in text]
        report["status"] = "ok" if not report["blocking_patterns"] else "fail"
    return report
