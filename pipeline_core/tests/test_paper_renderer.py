from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline_core.paper_quality.argument_blueprint_contract import validate_argument_blueprint_trace_report

from pipeline_core.paper_template.paper_renderer import (
    TEMPLATE_PATH,
    PaperRendererError,
    _structure_body,
    render_paper,
    validate_authoritative_template_contract,
)


class PaperRendererTests(unittest.TestCase):
    def test_v5_render_emits_hash_bound_argument_blueprint_trace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._manifest(root)
            blueprint_report = {
                "status": "PASS",
                "contract_version": "argument_blueprint_contract_v1",
                "task_count": 1,
                "hard_failures": [],
                "warnings": [],
            }
            with patch(
                "pipeline_core.paper_template.paper_renderer.validate_writing_policy",
                return_value={
                    "status": "PASS",
                    "policy_version": "controlled_writing_layer_v5",
                    "manifest_mode": "CURRENT_FORMAL",
                    "argument_blueprint": blueprint_report,
                    "visual_reasoning": {"status": "PASS"},
                    "appendix_code_policy": "NOT_REQUIRED",
                },
            ):
                record = render_paper(manifest, root / "paper")
            trace_path = root / "paper" / "argument_blueprint_trace_report.json"
            self.assertTrue(trace_path.is_file())
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            self.assertEqual(validate_argument_blueprint_trace_report(trace, root / "paper")["status"], "PASS")
            self.assertEqual(record["argument_blueprint_trace_report"], "argument_blueprint_trace_report.json")

    def test_model_entry_figure_is_rendered_before_the_first_derivation(self) -> None:
        tasks = [{
            "task_id": "q1",
            "label": "问题一",
            "analysis_content_key": "problem_analysis_q1",
            "content_key": "question_content_q1",
        }]
        structured = {
            "question_content_q1": {
                "intro": "先说明几何关系。",
                "derivation_blocks": [{
                    "title": "定角关系",
                    "method_selection_basis": "",
                    "steps": [{"equation_label": "eq:arc"}],
                    "physical_explanation": "",
                    "assumptions_and_boundary": "",
                }],
                "sections": [{"title": "候选筛选", "content": "说明。"}],
                "closing_optional": "",
            }
        }
        content = {"problem_analysis_q1": "analysis.tex"}
        figures = [{
            "figure_id": "q1_geometry",
            "task_id": "q1",
            "placement_slot": "TASK_MODEL_ENTRY",
            "first_discussion": "候选筛选",
            "asset_path": "figures/q1_geometry.png",
            "caption": "问题一几何关系",
        }]
        body = _structure_body(tasks, structured, content, figures)
        self.assertLess(
            body.index(r"\PaperContent{question_content_q1_intro}"),
            body.index(r"\PaperFigure{writer_content/figures/q1_geometry.png}"),
        )
        self.assertLess(
            body.index(r"\PaperFigure{writer_content/figures/q1_geometry.png}"),
            body.index(r"\subsubsection{定角关系}"),
        )

    def test_current_formal_manifest_requires_supported_formal_writing_layer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._manifest(root, question_count=1)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["manifest_mode"] = "CURRENT_FORMAL"
            payload["writer_schema_version"] = "WRITER_SCHEMA_V2"
            payload["task_contract"] = {"tasks": [{
                "task_id": "q1", "label": "问题一", "analysis_content_key": "problem_analysis_q1",
                "content_key": "question_content_q1",
            }]}
            manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(PaperRendererError, "FORMAL_WRITING_POLICY_VERSION_REQUIRED"):
                render_paper(manifest, root / "paper", formal=True)

    def _manifest(self, root: Path, question_count: int = 3) -> Path:
        content_root = root / "content"
        content_root.mkdir()
        keys = [
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
        ]
        content = {}
        for key in keys:
            filename = f"{key}.tex"
            (content_root / filename).write_text(f"内容 {key}。\n", encoding="utf-8")
            content[key] = filename
        evaluation = {"strengths": "优点。", "weaknesses": "缺点。", "improvements": "改进。"}
        (content_root / "model_evaluation.json").write_text(json.dumps(evaluation, ensure_ascii=False), encoding="utf-8")
        content["model_evaluation"] = "model_evaluation.json"
        for index in range(1, question_count + 1):
            key = f"question_content_q{index}"
            question = {
                "intro": f"问题{index}的处理说明。",
                "sections": [
                    {"title": f"真实小标题{index}", "content": f"问题{index}的模型内容。"},
                ],
                "closing_optional": "",
            }
            filename = f"{key}.json"
            (content_root / filename).write_text(json.dumps(question, ensure_ascii=False), encoding="utf-8")
            content[key] = filename
        manifest = root / "writer_manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "question_count": question_count,
                    "manifest_mode": "LEGACY_COMPATIBILITY",
                    "content_root": "content",
                    "content": content,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return manifest

    def test_renderer_owns_contract_and_question_sections_are_flexible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = render_paper(self._manifest(root), root / "paper")
            main = (root / "paper" / "main.tex").read_text(encoding="utf-8")
            self.assertFalse(record["gpt_format_reminder_required"])
            self.assertFalse(record["mandatory_question_summary"])
            self.assertFalse(record["fixed_four_block_schema"])
            self.assertIn(r"number=\chinese{section}", main)
            self.assertIn(r"\section{模型的建立与求解}", main)
            self.assertIn(r"\subsection{问题一}", main)
            self.assertIn(r"\subsection{问题三}", main)
            self.assertIn(r"\subsubsection{真实小标题1}", main)
            template = TEMPLATE_PATH.read_text(encoding="utf-8")
            self.assertIn(r"\titleformat{\subsubsection}" + "\n" + r"  {\zihao{-4}\heiti}", template)
            self.assertIn(r"\section{模型的评价、改进与推广}", main)
            self.assertIn(r"\subsection{模型优点}", main)
            self.assertIn(r"\subsection{模型缺点}", main)
            self.assertIn(r"\subsection{模型的改进}", main)
            self.assertIn(r"\section{参考文献}", main)
            self.assertIn(r"\begin{PaperReferences}{9}", main)
            self.assertIn(r"\clearpage", main)
            self.assertIn(r"\section*{附录}", main)
            self.assertNotIn("model_assumptions_note", main)
            self.assertNotIn(r"\subsection{问题一的模型建立与求解}", main)
            self.assertNotIn(r"\subsubsection{本问小结}", main)
            self.assertNotIn(r"\tableofcontents", main)

    def test_assumption_explanatory_note_is_not_required_or_rendered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._manifest(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["content"].pop("model_assumptions_note")
            manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            render_paper(manifest, root / "paper")
            main = (root / "paper" / "main.tex").read_text(encoding="utf-8")
            self.assertNotIn("model_assumptions_note", main)

    def test_writer_cannot_inject_structure_or_typography(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._manifest(root)
            bad = root / "content" / "problem_restatement.tex"
            bad.write_text(r"\section{结果与分析}\zihao{5}", encoding="utf-8")
            with self.assertRaises(PaperRendererError) as context:
                render_paper(manifest, root / "paper")
            self.assertIn("WRITER_TOP_LEVEL_SECTION_FORBIDDEN", str(context.exception))

    def test_writer_cannot_include_arbitrary_tex_or_define_macros(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._manifest(root)
            bad = root / "content" / "problem_restatement.tex"
            bad.write_text(r"\input{arbitrary.tex}", encoding="utf-8")
            with self.assertRaises(PaperRendererError) as context:
                render_paper(manifest, root / "paper")
            self.assertIn("WRITER_INPUT_PATH_FORBIDDEN", str(context.exception))

    def test_writer_cannot_override_assumption_numbering_or_page_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._manifest(root)
            bad = root / "content" / "model_assumptions.tex"
            bad.write_text(r"\begin{enumerate}[label=(\arabic*)]", encoding="utf-8")
            with self.assertRaises(PaperRendererError) as context:
                render_paper(manifest, root / "paper")
            self.assertIn("WRITER_STRUCTURE_ENVIRONMENT_FORBIDDEN", str(context.exception))

            bad.write_text(r"\FloatBarrier", encoding="utf-8")
            with self.assertRaises(PaperRendererError) as context:
                render_paper(manifest, root / "paper")
            self.assertIn("WRITER_PAGE_CONTROL_FORBIDDEN", str(context.exception))

    def test_empty_question_heading_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._manifest(root)
            bad = root / "content" / "question_content_q2.json"
            bad.write_text(json.dumps({"intro": "", "sections": [{"title": "", "content": ""}]}), encoding="utf-8")
            with self.assertRaises(PaperRendererError) as context:
                render_paper(manifest, root / "paper")
            self.assertIn("EMPTY_HEADING_FORBIDDEN", str(context.exception))

    def test_explicit_named_task_contract_is_rendered_without_q_labels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._manifest(root, question_count=1)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            content_root = root / "content"
            question = {
                "intro": "空间配置的处理说明。",
                "sections": [{"title": "约束表达", "content": "以约束给出可行区域。"}],
                "closing_optional": "",
            }
            (content_root / "question_content_layout.json").write_text(
                json.dumps(question, ensure_ascii=False), encoding="utf-8"
            )
            (content_root / "problem_analysis_layout.tex").write_text("空间约束分析。", encoding="utf-8")
            payload["content"].pop("question_content_q1")
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
            manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            record = render_paper(manifest, root / "paper")
            main = (root / "paper" / "main.tex").read_text(encoding="utf-8")
            self.assertIn(r"\subsection{空间配置}", main)
            self.assertNotIn(r"\subsection{问题一}", main)
            self.assertEqual(record["task_contract"]["migration_mode"], "EXPLICIT_TASK_CONTRACT")

    def test_structured_derivation_block_explains_step_before_formula_without_variable_dump(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._manifest(root, question_count=1)
            question_path = root / "content" / "question_content_q1.json"
            question = json.loads(question_path.read_text(encoding="utf-8"))
            question["derivation_blocks"] = [{
                "title": "平衡关系推导", "model_goal": "建立受力平衡。",
                "variables": [{"symbol": r"$F$", "definition": "作用力大小。"}],
                "steps": [{"formula_tex": r"F=ma", "explanation": "由牛顿第二定律得到。"}],
                "physical_explanation": "该式把合力与加速度联系起来。",
                "assumptions_and_boundary": "仅在给定近似条件下使用。",
            }]
            question_path.write_text(json.dumps(question, ensure_ascii=False), encoding="utf-8")
            render_paper(manifest, root / "paper")
            main = (root / "paper" / "main.tex").read_text(encoding="utf-8")
            self.assertIn(r"\subsubsection{平衡关系推导}", main)
            self.assertNotIn("question_content_q1_derivation_1_variable_1_symbol", main)
            self.assertLess(main.index("question_content_q1_derivation_1_step_1_explanation"), main.index("question_content_q1_derivation_1_step_1_formula"))
            self.assertTrue((root / "paper" / "writer_content" / "question_content_q1_derivation_1_step_1_formula.tex").is_file())

    def _set_derivation_steps(self, root: Path, steps: list[dict[str, str]]) -> Path:
        manifest = self._manifest(root, question_count=1)
        question_path = root / "content" / "question_content_q1.json"
        question = json.loads(question_path.read_text(encoding="utf-8"))
        question["derivation_blocks"] = [{
            "title": "Core derivation",
            "model_goal": "Establish the core relation.",
            "variables": [{"symbol": "F", "definition": "Force magnitude."}],
            "steps": steps,
            "physical_explanation": "The relation has a direct physical meaning.",
            "assumptions_and_boundary": "Use only under the declared boundary.",
        }]
        question_path.write_text(json.dumps(question, ensure_ascii=False), encoding="utf-8")
        return manifest

    def test_display_only_formula_remains_unnumbered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._set_derivation_steps(root, [{"formula_tex": "F=ma", "explanation": "Display only."}])
            render_paper(manifest, root / "paper")
            main = (root / "paper" / "main.tex").read_text(encoding="utf-8")
            self.assertIn(r"\[", main)
            self.assertNotIn(r"\begin{equation}", main)
            self.assertNotIn(r"\label{eq:", main)

    def test_labeled_formula_uses_numbered_equation_and_latex_label(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._set_derivation_steps(root, [{
                "formula_tex": "F=ma",
                "explanation": "Core equation.",
                "equation_label": "eq:q1-collision",
            }])
            render_paper(manifest, root / "paper")
            main = (root / "paper" / "main.tex").read_text(encoding="utf-8")
            self.assertIn(r"\begin{equation}", main)
            self.assertIn(r"\label{eq:q1-collision}", main)
            self.assertIn(r"\end{equation}", main)

    def test_eqref_is_preserved_as_writer_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._set_derivation_steps(root, [{
                "formula_tex": "F=ma",
                "explanation": r"See \eqref{eq:q1-collision}.",
                "equation_label": "eq:q1-collision",
            }])
            render_paper(manifest, root / "paper")
            explanation = root / "paper" / "writer_content" / "question_content_q1_derivation_1_step_1_explanation.tex"
            self.assertIn(r"\eqref{eq:q1-collision}", explanation.read_text(encoding="utf-8"))

    def test_two_core_equations_receive_independent_latex_labels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._set_derivation_steps(root, [
                {"formula_tex": "F=ma", "explanation": "First core equation.", "equation_label": "eq:q1-force"},
                {"formula_tex": "J=mr^2", "explanation": "Second core equation.", "equation_label": "eq:q1-inertia"},
            ])
            render_paper(manifest, root / "paper")
            main = (root / "paper" / "main.tex").read_text(encoding="utf-8")
            self.assertEqual(main.count(r"\begin{equation}"), 2)
            self.assertIn(r"\label{eq:q1-force}", main)
            self.assertIn(r"\label{eq:q1-inertia}", main)

    def test_mixed_core_and_display_only_formulas_keep_distinct_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._set_derivation_steps(root, [
                {"formula_tex": "F=ma", "explanation": "Core equation.", "equation_label": "eq:q1-force"},
                {"formula_tex": "2+2=4", "explanation": "Display only."},
            ])
            render_paper(manifest, root / "paper")
            main = (root / "paper" / "main.tex").read_text(encoding="utf-8")
            self.assertEqual(main.count(r"\begin{equation}"), 1)
            self.assertIn(r"\label{eq:q1-force}", main)
            self.assertIn(r"\[", main)

    def test_duplicate_equation_label_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._set_derivation_steps(root, [
                {"formula_tex": "F=ma", "explanation": "First.", "equation_label": "eq:q1-force"},
                {"formula_tex": "J=mr^2", "explanation": "Duplicate.", "equation_label": "eq:q1-force"},
            ])
            with self.assertRaisesRegex(PaperRendererError, "DUPLICATE_EQUATION_LABEL"):
                render_paper(manifest, root / "paper")

    def test_equation_label_must_be_latex_safe_and_equation_specific(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._set_derivation_steps(root, [{
                "formula_tex": "F=ma", "explanation": "Invalid label.", "equation_label": "not safe label"
            }])
            with self.assertRaisesRegex(PaperRendererError, "EQUATION_LABEL_INVALID"):
                render_paper(manifest, root / "paper")

    def test_renderer_does_not_insert_python_equation_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._set_derivation_steps(root, [{
                "formula_tex": "F=ma", "explanation": "Core equation.", "equation_label": "eq:q1-force"
            }])
            render_paper(manifest, root / "paper")
            main = (root / "paper" / "main.tex").read_text(encoding="utf-8")
            self.assertNotIn(r"\tag{", main)
            self.assertNotIn(r"\number", main)

    def test_template_uses_sorted_compressed_superscript_bracket_citations(self) -> None:
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
        self.assertIn(r"\usepackage{cite}", template)
        self.assertIn(r"\renewcommand{\cite}[1]{\textsuperscript{\PaperOriginalCite{#1}}}", template)
        self.assertNotIn(r"\usepackage[superscript,sort&compress]{cite}", template)
        self.assertNotIn(r"\renewcommand{\@citess}", template)
        self.assertNotIn(r"\renewcommand{\@cite}", template)

    def test_template_binds_fonts_without_editor_specific_aliases(self) -> None:
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
        self.assertIn("fontset=none", template)
        self.assertIn("C:/Windows/Fonts/times.ttf", template)
        self.assertIn("C:/Windows/Fonts/simsun.ttc", template)
        self.assertIn(r"\setmainfont{TeX Gyre Termes}", template)
        self.assertIn("FandolSong-Regular", template)
        self.assertIn(r"\renewcommand{\heiti}{\CJKfamily{paperhei}}", template)
        self.assertNotIn("fontset=windows", template)
        self.assertNotIn(r"\setmainfont{Times New Roman}", template)
        self.assertEqual(validate_authoritative_template_contract(template)["status"], "PASS")
        invalid = template.replace(r"\setstretch{1}", r"\setstretch{1.2}")
        invalid = invalid.replace(r"\noindent\begin{minipage}{\textwidth}", r"\begin{center}\begin{minipage}{0.91\textwidth}")
        with self.assertRaisesRegex(PaperRendererError, "AUTHORITATIVE_TEMPLATE_CONTRACT_VIOLATION"):
            validate_authoritative_template_contract(invalid)

    def test_citation_semantics_and_bibliography_order_remain_writer_owned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._manifest(root, question_count=1)
            content_root = root / "content"
            (content_root / "problem_restatement.tex").write_text(
                r"First claim \cite{a,b,c}; repeated claim \cite{a}.", encoding="utf-8"
            )
            (content_root / "references.tex").write_text(
                "\\bibitem{b} B.\n\\bibitem{a} A.\n\\bibitem{c} C.", encoding="utf-8"
            )
            render_paper(manifest, root / "paper")
            body = (root / "paper" / "writer_content" / "problem_restatement.tex").read_text(encoding="utf-8")
            references = (root / "paper" / "writer_content" / "references.tex").read_text(encoding="utf-8")
            self.assertIn(r"\cite{a,b,c}", body)
            self.assertIn(r"\cite{a}", body)
            self.assertLess(references.index(r"\bibitem{b}"), references.index(r"\bibitem{a}"))
            self.assertIn(r"\bibitem{c}", references)

    def test_batch_b_smoke_fixture_contains_equations_eqref_and_citations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._set_derivation_steps(root, [
                {"formula_tex": "F=ma", "explanation": r"See \eqref{eq:q1-force}.", "equation_label": "eq:q1-force"},
                {"formula_tex": "J=mr^2", "explanation": "Second core equation.", "equation_label": "eq:q1-inertia"},
                {"formula_tex": "2+2=4", "explanation": "Display-only formula."},
            ])
            content_root = root / "content"
            (content_root / "problem_restatement.tex").write_text(
                r"The method follows prior work \cite{a,b,c}; see \cite{a}.", encoding="utf-8"
            )
            (content_root / "references.tex").write_text(
                "\\bibitem{a} A.\n\\bibitem{b} B.\n\\bibitem{c} C.\n", encoding="utf-8"
            )
            render_paper(manifest, root / "paper")
            main = (root / "paper" / "main.tex").read_text(encoding="utf-8")
            body = (root / "paper" / "writer_content" / "problem_restatement.tex").read_text(encoding="utf-8")
            self.assertEqual(main.count(r"\begin{equation}"), 2)
            self.assertIn(r"\[", main)
            self.assertIn(r"\eqref{eq:q1-force}", (root / "paper" / "writer_content" / "question_content_q1_derivation_1_step_1_explanation.tex").read_text(encoding="utf-8"))
            self.assertIn(r"\cite{a,b,c}", body)
            self.assertIn(r"\begin{PaperReferences}{9}", main)
            self.assertEqual(body.count(r"\cite"), 2)

    def test_writer_sources_remain_immutable_when_content_root_overlaps_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_manifest = self._manifest(root)
            paper = root / "paper"
            paper.mkdir()
            shutil.move(str(root / "content"), str(paper / "writer_content"))
            payload = json.loads(source_manifest.read_text(encoding="utf-8"))
            payload["content_root"] = "writer_content"
            manifest = paper / "writer_manifest.json"
            manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            source_evaluation = paper / "writer_content" / "model_evaluation.json"
            source_bytes = source_evaluation.read_bytes()

            render_paper(manifest, paper)

            self.assertEqual(source_evaluation.read_bytes(), source_bytes)
            self.assertIsInstance(json.loads(source_evaluation.read_text(encoding="utf-8")), dict)
            self.assertTrue((paper / "rendered_writer_content" / "model_evaluation_strengths.tex").is_file())

    def test_writer_cannot_emit_internal_runtime_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._manifest(root)
            bad = root / "content" / "question_content_q1.json"
            bad.write_text(json.dumps({"intro": "format-only preview", "sections": []}), encoding="utf-8")
            with self.assertRaises(PaperRendererError) as context:
                render_paper(manifest, root / "paper")
            self.assertIn("WRITER_INTERNAL_TEXT_FORBIDDEN", str(context.exception))


if __name__ == "__main__":
    unittest.main()
