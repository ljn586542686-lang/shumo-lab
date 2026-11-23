from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from pipeline_core.paper_quality.pdf_layout_checks import check_pdf_artifact, check_xelatex_log


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "pipeline_core" / "freeze" / "formal_contest_freeze" / "final_paper_template.tex"


def _xelatex() -> Path | None:
    discovered = shutil.which("xelatex")
    if discovered:
        return Path(discovered)
    fixed = Path(r"D:\Tex\texlive\2025\bin\windows\xelatex.exe")
    return fixed if fixed.is_file() else None


class FormalTemplateCompileTests(unittest.TestCase):
    def test_authoritative_template_compiles_cleanly_with_fonts_equations_and_citations(self) -> None:
        executable = _xelatex()
        if executable is None:
            self.skipTest("XeLaTeX runtime unavailable")
        with tempfile.TemporaryDirectory(prefix="formal_template_compile_") as directory:
            root = Path(directory)
            content = root / "writer_content"
            content.mkdir()
            (content / "title.tex").write_text("正式论文模板编译回归", encoding="utf-8")
            (content / "abstract.tex").write_text(
                "本文建立可复核的数学模型。\n\n针对问题一，给出完整推导与结果。\n\n综上，模型在给定条件下有效。",
                encoding="utf-8",
            )
            (content / "keywords.tex").write_text("数学建模；数值验证；排版检查", encoding="utf-8")
            template = TEMPLATE.read_text(encoding="utf-8")
            body = r"""
\section{问题重述}
\par 本文验证首段缩进、中文标题、英文数字字体与引用标注\cite{ref1}。
\section{模型建立与求解}
由定义得到关系式
\begin{equation}
  y=x^2,
  \label{eq:smoke}
\end{equation}
由式\eqref{eq:smoke}可知，公式编号和交叉引用保持一致。
\section{参考文献}
\begin{PaperReferences}{9}
\bibitem{ref1} 测试作者. 数学建模论文排版方法[J]. 测试期刊, 2026, 1(1): 1--2.
\end{PaperReferences}
"""
            (root / "main.tex").write_text(
                template.replace("% PAPER_RENDERER:STRUCTURE_BODY", body), encoding="utf-8"
            )
            for _ in range(2):
                completed = subprocess.run(
                    [str(executable), "-interaction=nonstopmode", "-halt-on-error", "main.tex"],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=120,
                )
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            log = root / "main.log"
            self.assertEqual(check_xelatex_log(log, strict_layout=True)["status"], "PASS")
            self.assertEqual(check_pdf_artifact(root / "main.pdf")["status"], "PASS")
            log_text = log.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn("Package fontspec Error", log_text)
            self.assertNotIn("Font TU/SimHei", log_text)


if __name__ == "__main__":
    unittest.main()
