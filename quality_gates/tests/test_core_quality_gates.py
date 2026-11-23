from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from quality_gates import run_quality_gates
from pipeline_core.tests.runner_workspace import RunnerWorkspaceTestCase


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _copy_fixture(root: Path, name: str, target_name: str) -> Path:
    src = FIXTURES / name
    dst = root / "work_tmp" / target_name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return dst


def _snapshot_files(path: Path) -> dict[str, tuple[int, int]]:
    if not path.exists():
        return {}
    return {str(item.relative_to(path)): (item.stat().st_size, item.stat().st_mtime_ns) for item in path.rglob("*") if item.is_file()}


def _rule(report: dict, rule_id: str) -> dict:
    for item in report["results"]:
        if item["rule_id"] == rule_id:
            return item
    raise AssertionError(f"rule not found: {rule_id}")


class CoreQualityGateTests(RunnerWorkspaceTestCase):
    def test_qg_pass_case_rules_pass(self) -> None:
        case_dir = _copy_fixture(self.root, "qg_pass_case", "qg_pass_case_runtime")
        run_dir = self.root / "work_tmp" / "qg_pass_case_run"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        report = run_quality_gates(root=self.root, case_dir=case_dir, run_dir=run_dir, run_id="qg_pass_run")
        self.assertTrue(report["promotion_allowed"])
        self.assertEqual(report["hard_block_count"], 0)
        self.assertEqual(_rule(report, "QG-DATA-01")["status"], "PASS")
        self.assertEqual(_rule(report, "QG-EXTRAP-02")["status"], "PASS")
        self.assertEqual(_rule(report, "QG-SENS-03")["status"], "PASS")
        self.assertEqual(_rule(report, "QG-ERROR-04")["status"], "PASS")
        self.assertEqual(_rule(report, "QG-MODEL-05")["status"], "MANUAL_REVIEW_REQUIRED")
        self.assertEqual(_rule(report, "QG-SYM-06")["status"], "PASS")
        self.assertEqual(_rule(report, "QG-PREP-07")["status"], "PASS")
        self.assertEqual(_rule(report, "QG-FIG-08")["status"], "PASS")
        self.assertTrue(_rule(report, "QG-FIG-08")["manual_review_required"])
        self.assertEqual(_rule(report, "QG-REF-09")["status"], "PASS")
        self.assertTrue(_rule(report, "QG-REF-09")["manual_review_required"])
        self.assertEqual(_rule(report, "QG-ABST-10")["status"], "PASS")
        self.assertTrue(_rule(report, "QG-ABST-10")["manual_review_required"])
        self.assertTrue((run_dir / "output" / "quality" / "qg_report.json").exists())
        self.assertTrue((run_dir / "output" / "quality" / "qg_report.md").exists())
        self.assertTrue((run_dir / "output" / "quality" / "manual_review_todo.json").exists())

    def test_qg_fail_case_triggers_data_and_extrap_hard_blocks(self) -> None:
        case_dir = _copy_fixture(self.root, "qg_fail_case", "qg_fail_case_runtime")
        run_dir = self.root / "work_tmp" / "qg_fail_case_run"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        report = run_quality_gates(root=self.root, case_dir=case_dir, run_dir=run_dir, run_id="qg_fail_run")
        self.assertFalse(report["promotion_allowed"])
        self.assertGreaterEqual(report["hard_block_count"], 2)
        self.assertEqual(_rule(report, "QG-DATA-01")["status"], "FAIL")
        self.assertEqual(_rule(report, "QG-EXTRAP-02")["status"], "FAIL")
        self.assertEqual(_rule(report, "QG-MODEL-05")["status"], "FAIL")
        self.assertEqual(_rule(report, "QG-SYM-06")["status"], "FAIL")
        self.assertEqual(_rule(report, "QG-PREP-07")["status"], "WARNING")
        self.assertEqual(_rule(report, "QG-FIG-08")["status"], "WARNING")
        self.assertIn("series_count", _rule(report, "QG-FIG-08")["message"])
        self.assertEqual(_rule(report, "QG-REF-09")["status"], "FAIL")
        self.assertEqual(_rule(report, "QG-REF-09")["severity"], "SOFT_BLOCK")
        self.assertEqual(_rule(report, "QG-ABST-10")["status"], "FAIL")
        self.assertEqual(_rule(report, "QG-ABST-10")["severity"], "SOFT_BLOCK")
        self.assertTrue(_rule(report, "QG-ABST-10")["manual_review_required"])

    def test_sensitivity_missing_triggers_soft_block(self) -> None:
        case_dir = _copy_fixture(self.root, "qg_pass_case", "qg_sens_missing_case")
        shutil.rmtree(case_dir / "output")
        run_dir = self.root / "work_tmp" / "qg_sens_missing_run"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        report = run_quality_gates(root=self.root, case_dir=case_dir, run_dir=run_dir, run_id="qg_sens_missing")
        sens = _rule(report, "QG-SENS-03")
        self.assertEqual(sens["severity"], "SOFT_BLOCK")
        self.assertEqual(sens["status"], "FAIL")
        self.assertTrue(report["promotion_allowed"])

    def test_composite_target_missing_error_propagation_triggers_soft_block(self) -> None:
        case_dir = _copy_fixture(self.root, "qg_pass_case", "qg_error_missing_case")
        config_path = case_dir / "quality_gates.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["composite_target"] = {"formula": "Y=f(X,S)"}
        config["candidate_points"] = [{"id": "p1"}]
        config_path.write_text(json.dumps(config), encoding="utf-8")
        run_dir = self.root / "work_tmp" / "qg_error_missing_run"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        report = run_quality_gates(root=self.root, case_dir=case_dir, run_dir=run_dir, run_id="qg_error_missing")
        err = _rule(report, "QG-ERROR-04")
        self.assertEqual(err["severity"], "SOFT_BLOCK")
        self.assertEqual(err["status"], "FAIL")
        self.assertTrue(report["promotion_allowed"])

    def test_missing_fields_are_skipped_or_warning_without_crash(self) -> None:
        case_dir = self.root / "work_tmp" / "qg_empty_case"
        if case_dir.exists():
            shutil.rmtree(case_dir)
        (case_dir / "data" / "raw").mkdir(parents=True)
        (case_dir / "case_config.json").write_text(json.dumps({"case_id": "qg_empty_case"}), encoding="utf-8")
        run_dir = self.root / "work_tmp" / "qg_empty_run"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        report = run_quality_gates(root=self.root, case_dir=case_dir, run_dir=run_dir, run_id="qg_empty")
        self.assertTrue(report["promotion_allowed"])
        self.assertEqual(_rule(report, "QG-DATA-01")["status"], "WARNING")
        self.assertEqual(_rule(report, "QG-EXTRAP-02")["status"], "SKIPPED")
        self.assertEqual(_rule(report, "QG-SENS-03")["status"], "SKIPPED")
        self.assertEqual(_rule(report, "QG-ERROR-04")["status"], "SKIPPED")
        self.assertEqual(_rule(report, "QG-MODEL-05")["status"], "SKIPPED")
        self.assertEqual(_rule(report, "QG-SYM-06")["status"], "SKIPPED")
        self.assertEqual(_rule(report, "QG-PREP-07")["status"], "WARNING")
        self.assertEqual(_rule(report, "QG-FIG-08")["status"], "SKIPPED")
        self.assertEqual(_rule(report, "QG-REF-09")["status"], "FAIL")
        self.assertEqual(_rule(report, "QG-ABST-10")["status"], "WARNING")

    def test_model_comparison_missing_skips(self) -> None:
        case_dir = _copy_fixture(self.root, "qg_pass_case", "qg_model_missing_case")
        config_path = case_dir / "quality_gates.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.pop("model_comparison", None)
        config_path.write_text(json.dumps(config), encoding="utf-8")
        run_dir = self.root / "work_tmp" / "qg_model_missing_run"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        report = run_quality_gates(root=self.root, case_dir=case_dir, run_dir=run_dir, run_id="qg_model_missing")
        self.assertEqual(_rule(report, "QG-MODEL-05")["status"], "SKIPPED")

    def test_symbol_table_missing_skips(self) -> None:
        case_dir = _copy_fixture(self.root, "qg_pass_case", "qg_symbol_missing_case")
        config_path = case_dir / "quality_gates.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.pop("symbols_defined", None)
        config_path.write_text(json.dumps(config), encoding="utf-8")
        run_dir = self.root / "work_tmp" / "qg_symbol_missing_run"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        report = run_quality_gates(root=self.root, case_dir=case_dir, run_dir=run_dir, run_id="qg_symbol_missing")
        self.assertIn(_rule(report, "QG-SYM-06")["status"], {"SKIPPED", "WARNING"})

    def test_preprocessing_missing_warns_not_hard_block(self) -> None:
        case_dir = _copy_fixture(self.root, "qg_pass_case", "qg_prep_missing_case")
        config_path = case_dir / "quality_gates.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.pop("preprocessing_log", None)
        config_path.write_text(json.dumps(config), encoding="utf-8")
        run_dir = self.root / "work_tmp" / "qg_prep_missing_run"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        report = run_quality_gates(root=self.root, case_dir=case_dir, run_dir=run_dir, run_id="qg_prep_missing")
        prep = _rule(report, "QG-PREP-07")
        self.assertEqual(prep["severity"], "WARNING")
        self.assertEqual(prep["status"], "WARNING")
        self.assertTrue(report["promotion_allowed"])

    def test_figure_manifest_missing_skips_without_crash(self) -> None:
        case_dir = _copy_fixture(self.root, "qg_pass_case", "qg_fig_missing_case")
        config_path = case_dir / "quality_gates.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.pop("figure_manifest", None)
        config_path.write_text(json.dumps(config), encoding="utf-8")
        run_dir = self.root / "work_tmp" / "qg_fig_missing_run"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        report = run_quality_gates(root=self.root, case_dir=case_dir, run_dir=run_dir, run_id="qg_fig_missing")
        self.assertIn(_rule(report, "QG-FIG-08")["status"], {"SKIPPED", "WARNING"})
        self.assertTrue(report["promotion_allowed"])

    def test_citation_matrix_missing_soft_blocks_without_faking_citations(self) -> None:
        case_dir = _copy_fixture(self.root, "qg_pass_case", "qg_ref_missing_case")
        config_path = case_dir / "quality_gates.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.pop("citation_matrix", None)
        config_path.write_text(json.dumps(config), encoding="utf-8")
        run_dir = self.root / "work_tmp" / "qg_ref_missing_run"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        report = run_quality_gates(root=self.root, case_dir=case_dir, run_dir=run_dir, run_id="qg_ref_missing")
        ref = _rule(report, "QG-REF-09")
        self.assertEqual(ref["severity"], "SOFT_BLOCK")
        self.assertEqual(ref["status"], "FAIL")
        self.assertTrue(ref["manual_review_required"])
        self.assertTrue(report["promotion_allowed"])

    def test_abstract_missing_warns_without_crash(self) -> None:
        case_dir = self.root / "work_tmp" / "qg_abstract_missing_case"
        if case_dir.exists():
            shutil.rmtree(case_dir)
        (case_dir / "data" / "raw").mkdir(parents=True)
        (case_dir / "case_config.json").write_text(json.dumps({"case_id": "qg_abstract_missing_case"}), encoding="utf-8")
        (case_dir / "quality_gates.json").write_text(json.dumps({"citation_matrix": {"citations": []}}), encoding="utf-8")
        run_dir = self.root / "work_tmp" / "qg_abstract_missing_run"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        report = run_quality_gates(root=self.root, case_dir=case_dir, run_dir=run_dir, run_id="qg_abstract_missing")
        abst = _rule(report, "QG-ABST-10")
        self.assertEqual(abst["severity"], "WARNING")
        self.assertEqual(abst["status"], "WARNING")
        self.assertTrue(report["promotion_allowed"])

    def test_diagnostic_figure_missing_interpretation_soft_blocks(self) -> None:
        case_dir = _copy_fixture(self.root, "qg_pass_case", "qg_diag_fig_case")
        config_path = case_dir / "quality_gates.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["figure_manifest"] = {
            "figures": [
                {
                    "figure_id": "fig_residual",
                    "path": "output/figures/fig_residual.png",
                    "figure_type": "residual_plot",
                    "series_count": 2,
                    "caption": "Residual plot",
                    "diagnostic_figure": True,
                }
            ]
        }
        config_path.write_text(json.dumps(config), encoding="utf-8")
        run_dir = self.root / "work_tmp" / "qg_diag_fig_run"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        report = run_quality_gates(root=self.root, case_dir=case_dir, run_dir=run_dir, run_id="qg_diag_fig")
        fig = _rule(report, "QG-FIG-08")
        self.assertEqual(fig["severity"], "SOFT_BLOCK")
        self.assertEqual(fig["status"], "FAIL")
        self.assertTrue(fig["manual_review_required"])
        self.assertTrue(report["promotion_allowed"])

    def test_hard_block_run_case_does_not_promote(self) -> None:
        case_dir = _copy_fixture(self.root, "qg_fail_case", "qg_fail_runner_case")
        (case_dir / "output").mkdir(exist_ok=True)
        (case_dir / "output" / "result_summary.json").write_text(
            json.dumps(
                {
                    "schema_version": "x5_f0_result_summary_v1",
                    "generated_at": "2026-01-01T00:00:00+00:00",
                    "run_id": "previous_good",
                    "case_id": "qg_fail_runner_case",
                    "data_health_status": "ok",
                    "final_submit_created": False,
                }
            ),
            encoding="utf-8",
        )
        result = self.run_case("run", case_dir, 30)
        self.assertEqual(result["status"], "fatal")
        promoted = json.loads((case_dir / "output" / "result_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(promoted["run_id"], "previous_good")
        run_dir = self.root / result["run_dir"]
        self.assertTrue((run_dir / "output" / "quality" / "qg_report.json").exists())
        self.assertFalse((case_dir / "output" / "quality" / "qg_report.json").exists())

    def test_no_final_submit_archive_or_history_cases_modified(self) -> None:
        archive_before = _snapshot_files(self.root / "archive")
        cases_before = _snapshot_files(self.root / "cases")
        case_dir = _copy_fixture(self.root, "qg_pass_case", "qg_safety_case")
        result = self.run_case("run", case_dir, 30)
        self.assertIn(result["status"], {"ok", "fatal"})
        self.assertFalse((self.root / "final_submit").exists())
        self.assertEqual(archive_before, _snapshot_files(self.root / "archive"))
        self.assertEqual(cases_before, _snapshot_files(self.root / "cases"))


if __name__ == "__main__":
    unittest.main()
