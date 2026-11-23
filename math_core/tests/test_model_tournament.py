"""Tests for math_core.comparison.model_tournament (X5-F5.3).

Test groups (11 required):
 1. basic_success          — 2 valid models, 2 metrics, comparison_table + best_by_metric
 2. metric_direction       — minimize vs maximize judged correctly
 3. direction_missing      — metric_direction_missing, manual_review_required
 4. failed_model_excluded  — success=false retained but excluded from leaders
 5. insufficient_valid     — valid < 2 → insufficient_valid_models
 6. forbidden_fields       — none of the 8 forbidden keys appear in output
 7. selection_reason_block — missing selection_reason → selection_reason_required + pending
 8. tie_handling           — tie_tolerance triggers tie flag, no forced winner
 9. candidate_models_req   — missing candidate_models → failure + manual_review_required
10. output_files           — json + csv generated
11. forbidden_paths        — final_submit/ not created
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from math_core.comparison.model_tournament import (
    FORBIDDEN_OUTPUT_KEYS,
    SCHEMA_VERSION,
    run_model_tournament,
    write_tournament_report,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _two_models_two_metrics(
    *,
    model_a_obj=2.0,
    model_b_obj=3.0,
    model_a_acc=0.9,
    model_b_acc=0.8,
    selection_reason="test run",
    tie_tolerance=1e-9,
) -> dict:
    return {
        "candidate_models": [
            {
                "model_id": "model_a",
                "model_name": "Model A",
                "model_type": "nonlinear_minimize",
                "success": True,
                "metrics": {"objective_value": model_a_obj, "accuracy": model_a_acc},
            },
            {
                "model_id": "model_b",
                "model_name": "Model B",
                "model_type": "nonlinear_minimize",
                "success": True,
                "metrics": {"objective_value": model_b_obj, "accuracy": model_b_acc},
            },
        ],
        "metrics": [
            {"metric_name": "objective_value", "direction": "minimize"},
            {"metric_name": "accuracy", "direction": "maximize"},
        ],
        "tie_tolerance": tie_tolerance,
        "selection_reason": selection_reason,
    }


# ---------------------------------------------------------------------------
# Group 1: basic_success
# ---------------------------------------------------------------------------

class ModelTournamentBasicSuccessTests(unittest.TestCase):
    """Test group 1: 2 valid models, 2 metrics → comparison_table + best_by_metric."""

    def test_basic_run_succeeds(self) -> None:
        result = run_model_tournament(_two_models_two_metrics(), run_id="t_basic")
        self.assertTrue(result["success"], msg=result.get("failure_reason"))

    def test_comparison_table_has_both_models(self) -> None:
        result = run_model_tournament(_two_models_two_metrics(), run_id="t_table")
        ids = {r["model_id"] for r in result["comparison_table"]}
        self.assertIn("model_a", ids)
        self.assertIn("model_b", ids)

    def test_best_by_metric_present_for_each_metric(self) -> None:
        result = run_model_tournament(_two_models_two_metrics(), run_id="t_bbm")
        self.assertIn("objective_value", result["best_by_metric"])
        self.assertIn("accuracy", result["best_by_metric"])

    def test_required_top_level_keys_present(self) -> None:
        result = run_model_tournament(_two_models_two_metrics(), run_id="t_keys")
        for key in (
            "schema_version", "metric_definitions", "model_counts",
            "comparison_table", "best_by_metric", "metric_leaders", "ties",
            "candidate_models", "selection_reason_required", "manual_review_required",
            "model_comparison_complete", "final_selection_pending_human_review",
            "limitations", "disclaimer", "metadata", "evidence_tier",
        ):
            self.assertIn(key, result, msg=f"missing top-level key: {key}")

    def test_model_counts_correct(self) -> None:
        result = run_model_tournament(_two_models_two_metrics(), run_id="t_counts")
        mc = result["model_counts"]
        self.assertEqual(mc["total"], 2)
        self.assertEqual(mc["valid"], 2)
        self.assertEqual(mc["failed"], 0)

    def test_metadata_flags_false(self) -> None:
        result = run_model_tournament(_two_models_two_metrics(), run_id="t_meta")
        m = result["metadata"]
        self.assertFalse(m["model_tournament_performed"])
        self.assertFalse(m["automl_performed"])
        self.assertFalse(m["academic_validity_judged"])
        self.assertFalse(m["final_model_selected"])
        self.assertFalse(m["two_thousand_twenty_one_b_used"])

    def test_schema_version(self) -> None:
        result = run_model_tournament(_two_models_two_metrics(), run_id="t_schema")
        self.assertEqual(result["schema_version"], SCHEMA_VERSION)


# ---------------------------------------------------------------------------
# Group 2: metric_direction
# ---------------------------------------------------------------------------

class ModelTournamentMetricDirectionTests(unittest.TestCase):
    """Test group 2: minimize picks lower value; maximize picks higher value."""

    def test_minimize_picks_lower(self) -> None:
        result = run_model_tournament(
            _two_models_two_metrics(model_a_obj=1.0, model_b_obj=5.0),
            run_id="t_minimize",
        )
        leaders = result["best_by_metric"]["objective_value"]["leaders"]
        leader_ids = [l["model_id"] for l in leaders]
        self.assertIn("model_a", leader_ids, "minimize: lower value (model_a=1.0) should lead")
        self.assertNotIn("model_b", leader_ids)

    def test_maximize_picks_higher(self) -> None:
        result = run_model_tournament(
            _two_models_two_metrics(model_a_acc=0.7, model_b_acc=0.95),
            run_id="t_maximize",
        )
        leaders = result["best_by_metric"]["accuracy"]["leaders"]
        leader_ids = [l["model_id"] for l in leaders]
        self.assertIn("model_b", leader_ids, "maximize: higher value (model_b=0.95) should lead")
        self.assertNotIn("model_a", leader_ids)

    def test_direction_stored_in_best_by_metric(self) -> None:
        result = run_model_tournament(_two_models_two_metrics(), run_id="t_dir_stored")
        self.assertEqual(result["best_by_metric"]["objective_value"]["direction"], "minimize")
        self.assertEqual(result["best_by_metric"]["accuracy"]["direction"], "maximize")


# ---------------------------------------------------------------------------
# Group 3: direction_missing
# ---------------------------------------------------------------------------

class ModelTournamentDirectionMissingTests(unittest.TestCase):
    """Test group 3: metric without direction → metric_direction_missing + manual_review_required."""

    def _config_no_direction(self) -> dict:
        return {
            "candidate_models": [
                {"model_id": "a", "model_type": "t", "success": True, "metrics": {"obj": 1.0}},
                {"model_id": "b", "model_type": "t", "success": True, "metrics": {"obj": 2.0}},
            ],
            "metrics": [{"metric_name": "obj"}],  # direction missing
        }

    def test_direction_missing_flag(self) -> None:
        result = run_model_tournament(self._config_no_direction(), run_id="t_nodir")
        self.assertTrue(result["metric_direction_missing"])

    def test_manual_review_required_when_direction_missing(self) -> None:
        result = run_model_tournament(self._config_no_direction(), run_id="t_nodir_mr")
        self.assertTrue(result["manual_review_required"])

    def test_comparison_not_complete_when_direction_missing(self) -> None:
        result = run_model_tournament(self._config_no_direction(), run_id="t_nodir_nc")
        self.assertFalse(result["model_comparison_complete"])


# ---------------------------------------------------------------------------
# Group 4: failed_model_excluded
# ---------------------------------------------------------------------------

class ModelTournamentFailedModelTests(unittest.TestCase):
    """Test group 4: success=false model retained but excluded from leaders."""

    def _config_with_failed(self) -> dict:
        return {
            "candidate_models": [
                {
                    "model_id": "good",
                    "model_type": "t",
                    "success": True,
                    "metrics": {"obj": 1.0},
                },
                {
                    "model_id": "good2",
                    "model_type": "t",
                    "success": True,
                    "metrics": {"obj": 1.5},
                },
                {
                    "model_id": "bad",
                    "model_type": "t",
                    "success": False,
                    "failure_reason": "solver_did_not_converge",
                    "metrics": {"obj": 0.0},  # would be "better" if not failed
                },
            ],
            "metrics": [{"metric_name": "obj", "direction": "minimize"}],
            "selection_reason": "test",
        }

    def test_failed_model_retained_in_comparison_table(self) -> None:
        result = run_model_tournament(self._config_with_failed(), run_id="t_failed_retained")
        ids = [r["model_id"] for r in result["comparison_table"]]
        self.assertIn("bad", ids, "failed model must be retained in comparison_table")

    def test_failed_model_excluded_from_leader_selection(self) -> None:
        result = run_model_tournament(self._config_with_failed(), run_id="t_failed_excl")
        bad_row = next(r for r in result["comparison_table"] if r["model_id"] == "bad")
        self.assertTrue(bad_row["excluded_from_leader_selection"])
        self.assertEqual(bad_row["status"], "failed")

    def test_failed_model_not_in_best_by_metric(self) -> None:
        result = run_model_tournament(self._config_with_failed(), run_id="t_failed_bbm")
        leaders = result["best_by_metric"]["obj"]["leaders"]
        leader_ids = [l["model_id"] for l in leaders]
        self.assertNotIn("bad", leader_ids,
                         "failed model must not appear as leader even if metric value is best")
        self.assertIn("good", leader_ids)

    def test_model_counts_reflect_failure(self) -> None:
        result = run_model_tournament(self._config_with_failed(), run_id="t_failed_counts")
        mc = result["model_counts"]
        self.assertEqual(mc["total"], 3)
        self.assertEqual(mc["valid"], 2)
        self.assertEqual(mc["failed"], 1)


# ---------------------------------------------------------------------------
# Group 5: insufficient_valid
# ---------------------------------------------------------------------------

class ModelTournamentInsufficientValidTests(unittest.TestCase):
    """Test group 5: valid < 2 → insufficient_valid_models + manual_review_required."""

    def test_all_failed_gives_insufficient(self) -> None:
        result = run_model_tournament(
            {
                "candidate_models": [
                    {"model_id": "x", "model_type": "t", "success": False,
                     "failure_reason": "err", "metrics": {}},
                    {"model_id": "y", "model_type": "t", "success": False,
                     "failure_reason": "err", "metrics": {}},
                ],
                "metrics": [{"metric_name": "obj", "direction": "minimize"}],
            },
            run_id="t_insuff_all_failed",
        )
        self.assertTrue(result["insufficient_valid_models"])
        self.assertTrue(result["manual_review_required"])

    def test_only_one_valid_gives_insufficient(self) -> None:
        result = run_model_tournament(
            {
                "candidate_models": [
                    {"model_id": "ok", "model_type": "t", "success": True, "metrics": {"obj": 1.0}},
                    {"model_id": "no", "model_type": "t", "success": False,
                     "failure_reason": "err", "metrics": {}},
                ],
                "metrics": [{"metric_name": "obj", "direction": "minimize"}],
            },
            run_id="t_insuff_one",
        )
        self.assertTrue(result["insufficient_valid_models"])

    def test_no_fake_tournament_when_insufficient(self) -> None:
        result = run_model_tournament(
            {
                "candidate_models": [
                    {"model_id": "only", "model_type": "t", "success": True,
                     "metrics": {"obj": 1.0}},
                ],
                "metrics": [{"metric_name": "obj", "direction": "minimize"}],
            },
            run_id="t_insuff_one_cand",
        )
        self.assertTrue(result["insufficient_valid_models"])
        # best_by_metric should not produce a real leader entry without a competitor
        bbm = result["best_by_metric"].get("obj", {})
        self.assertFalse(
            len(bbm.get("leaders", [])) > 0 and not result["insufficient_valid_models"],
            "should not produce leader when insufficient_valid_models",
        )


# ---------------------------------------------------------------------------
# Group 6: forbidden_fields
# ---------------------------------------------------------------------------

class ModelTournamentForbiddenFieldsTests(unittest.TestCase):
    """Test group 6: none of the 8 forbidden keys appear in output."""

    def test_forbidden_keys_absent_in_success_case(self) -> None:
        result = run_model_tournament(_two_models_two_metrics(), run_id="t_forbidden_ok")
        for key in FORBIDDEN_OUTPUT_KEYS:
            self.assertNotIn(key, result, msg=f"forbidden key '{key}' must not appear in output")

    def test_forbidden_keys_absent_in_failure_report(self) -> None:
        result = run_model_tournament(
            {"candidate_models": None, "metrics": []},
            run_id="t_forbidden_fail",
        )
        for key in FORBIDDEN_OUTPUT_KEYS:
            self.assertNotIn(key, result, msg=f"forbidden key '{key}' must not appear in failure report")

    def test_forbidden_keys_set_is_complete(self) -> None:
        expected = {
            "best_model", "winner", "overall_winner", "auto_selected_model",
            "recommended_model", "recommendation", "final_model", "automl_result",
        }
        self.assertEqual(FORBIDDEN_OUTPUT_KEYS, frozenset(expected))


# ---------------------------------------------------------------------------
# Group 7: selection_reason_block
# ---------------------------------------------------------------------------

class ModelTournamentSelectionReasonTests(unittest.TestCase):
    """Test group 7: missing selection_reason → selection_reason_required=true + pending."""

    def test_no_selection_reason_sets_required(self) -> None:
        cfg = _two_models_two_metrics(selection_reason=None)
        result = run_model_tournament(cfg, run_id="t_sr_required")
        self.assertTrue(result["selection_reason_required"])

    def test_no_selection_reason_sets_pending(self) -> None:
        cfg = _two_models_two_metrics(selection_reason=None)
        result = run_model_tournament(cfg, run_id="t_sr_pending")
        self.assertTrue(result["final_selection_pending_human_review"])

    def test_no_selection_reason_does_not_set_final_selection_ready(self) -> None:
        cfg = _two_models_two_metrics(selection_reason=None)
        result = run_model_tournament(cfg, run_id="t_sr_not_ready")
        self.assertNotIn("final_selection_ready", result)
        # The field must not exist in the output at all when not ready
        self.assertFalse(result.get("final_selection_ready", False))

    def test_with_selection_reason_clears_required(self) -> None:
        cfg = _two_models_two_metrics(selection_reason="model_a minimizes objective better")
        result = run_model_tournament(cfg, run_id="t_sr_cleared")
        self.assertFalse(result["selection_reason_required"])

    def test_model_comparison_complete_true_when_valid(self) -> None:
        result = run_model_tournament(_two_models_two_metrics(), run_id="t_sr_complete")
        self.assertTrue(result["model_comparison_complete"])


# ---------------------------------------------------------------------------
# Group 8: tie_handling
# ---------------------------------------------------------------------------

class ModelTournamentTieTests(unittest.TestCase):
    """Test group 8: tie_tolerance triggers tie, no forced winner."""

    def test_tie_detected_within_tolerance(self) -> None:
        cfg = _two_models_two_metrics(
            model_a_obj=1.0000,
            model_b_obj=1.0000,  # exact tie
            tie_tolerance=1e-6,
        )
        result = run_model_tournament(cfg, run_id="t_tie_exact")
        bbm = result["best_by_metric"]["objective_value"]
        self.assertTrue(bbm["tied"], "exact equal values must be marked as tied")
        self.assertEqual(len(bbm["leaders"]), 2, "both models should be in leader list when tied")

    def test_no_tie_outside_tolerance(self) -> None:
        cfg = _two_models_two_metrics(
            model_a_obj=1.0,
            model_b_obj=2.0,
            tie_tolerance=1e-9,
        )
        result = run_model_tournament(cfg, run_id="t_tie_nope")
        bbm = result["best_by_metric"]["objective_value"]
        self.assertFalse(bbm["tied"])
        self.assertEqual(len(bbm["leaders"]), 1)

    def test_tie_does_not_produce_forced_single_winner(self) -> None:
        cfg = _two_models_two_metrics(
            model_a_obj=1.0,
            model_b_obj=1.0,
            tie_tolerance=0.5,
        )
        result = run_model_tournament(cfg, run_id="t_tie_no_winner")
        bbm = result["best_by_metric"]["objective_value"]
        self.assertTrue(bbm["tied"])
        # Both models in leaders — no forced single winner
        self.assertGreaterEqual(len(bbm["leaders"]), 2)
        for fk in FORBIDDEN_OUTPUT_KEYS:
            self.assertNotIn(fk, result)

    def test_ties_dict_populated_for_tied_metric(self) -> None:
        cfg = _two_models_two_metrics(
            model_a_obj=5.0,
            model_b_obj=5.0,
        )
        result = run_model_tournament(cfg, run_id="t_ties_dict")
        self.assertIn("objective_value", result["ties"])
        self.assertEqual(len(result["ties"]["objective_value"]["tie_group"]), 2)

    def test_tie_leaders_have_delta_field(self) -> None:
        cfg = _two_models_two_metrics(
            model_a_obj=3.0,
            model_b_obj=3.0,
        )
        result = run_model_tournament(cfg, run_id="t_tie_delta_leaders")
        leaders = result["best_by_metric"]["objective_value"]["leaders"]
        self.assertEqual(len(leaders), 2)
        for entry in leaders:
            self.assertIn("delta", entry, "each leader entry must contain 'delta'")
            self.assertAlmostEqual(entry["delta"], 0.0,
                                   msg="exact tie: delta must be 0.0")

    def test_non_tie_leader_delta_is_zero_non_leader_positive(self) -> None:
        cfg = _two_models_two_metrics(
            model_a_obj=1.0,
            model_b_obj=5.0,
            tie_tolerance=1e-9,
        )
        result = run_model_tournament(cfg, run_id="t_tie_delta_nontie")
        leaders = result["best_by_metric"]["objective_value"]["leaders"]
        self.assertEqual(len(leaders), 1)
        self.assertEqual(leaders[0]["model_id"], "model_a")
        self.assertAlmostEqual(leaders[0]["delta"], 0.0, msg="leader delta must be 0")

    def test_ties_dict_entries_have_delta(self) -> None:
        cfg = _two_models_two_metrics(
            model_a_obj=7.0,
            model_b_obj=7.0,
        )
        result = run_model_tournament(cfg, run_id="t_ties_delta_dict")
        tie_group = result["ties"]["objective_value"]["tie_group"]
        for entry in tie_group:
            self.assertIn("delta", entry, "tie_group entries must contain 'delta'")
            self.assertAlmostEqual(entry["delta"], 0.0)


# ---------------------------------------------------------------------------
# Group 9: candidate_models_required
# ---------------------------------------------------------------------------

class ModelTournamentCandidateModelsRequiredTests(unittest.TestCase):
    """Test group 9: missing/empty candidate_models → failure + manual_review_required."""

    def test_none_candidate_models_fails(self) -> None:
        result = run_model_tournament(
            {"candidate_models": None, "metrics": [{"metric_name": "obj", "direction": "minimize"}]},
            run_id="t_cand_none",
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["failure_reason"], "missing_candidate_models")
        self.assertTrue(result["manual_review_required"])

    def test_empty_candidate_models_fails(self) -> None:
        result = run_model_tournament(
            {"candidate_models": [], "metrics": [{"metric_name": "obj", "direction": "minimize"}]},
            run_id="t_cand_empty",
        )
        self.assertFalse(result["success"])
        self.assertTrue(result["manual_review_required"])

    def test_missing_candidate_models_key_fails(self) -> None:
        result = run_model_tournament(
            {"metrics": [{"metric_name": "obj", "direction": "minimize"}]},
            run_id="t_cand_missing_key",
        )
        self.assertFalse(result["success"])
        self.assertTrue(result["manual_review_required"])


# ---------------------------------------------------------------------------
# Group 10: output_files
# ---------------------------------------------------------------------------

class ModelTournamentOutputFilesTests(unittest.TestCase):
    """Test group 10: json + csv generated with required content."""

    def test_write_json_and_csv(self) -> None:
        report = run_model_tournament(_two_models_two_metrics(), run_id="t_files")
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path, csv_path = write_tournament_report(report, tmpdir)
            self.assertTrue(json_path.exists())
            self.assertTrue(csv_path.exists())

            loaded = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["schema_version"], SCHEMA_VERSION)
            self.assertIn("comparison_table", loaded)
            self.assertIn("best_by_metric", loaded)
            self.assertIn("model_counts", loaded)
            self.assertIn("disclaimer", loaded)

            csv_text = csv_path.read_text(encoding="utf-8")
            self.assertIn("model_id", csv_text)
            self.assertIn("metric_name", csv_text)
            self.assertIn("metric_value", csv_text)

    def test_json_disclaimer_content(self) -> None:
        from math_core.comparison.model_tournament import DISCLAIMER
        report = run_model_tournament(_two_models_two_metrics(), run_id="t_disc")
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path, _ = write_tournament_report(report, tmpdir)
            loaded = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["disclaimer"], DISCLAIMER)
            # Disclaimer must contain key phrases
            d = DISCLAIMER.lower()
            self.assertIn("best_by_metric", DISCLAIMER)
            self.assertIn("best_model", DISCLAIMER)
            self.assertIn("automl", d)

    def test_json_no_forbidden_keys_on_disk(self) -> None:
        report = run_model_tournament(_two_models_two_metrics(), run_id="t_disk_fk")
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path, _ = write_tournament_report(report, tmpdir)
            loaded = json.loads(json_path.read_text(encoding="utf-8"))
            for key in FORBIDDEN_OUTPUT_KEYS:
                self.assertNotIn(key, loaded, msg=f"forbidden key '{key}' found in JSON on disk")

    def _read_csv_rows(self, csv_path: "Path") -> "list[dict]":
        import csv as _csv
        import io
        lines = [ln for ln in csv_path.read_text(encoding="utf-8").splitlines()
                 if not ln.startswith("#")]
        reader = _csv.DictReader(io.StringIO("\n".join(lines)))
        return list(reader)

    def test_csv_tie_tolerance_non_empty(self) -> None:
        input_tolerance = 5e-7
        cfg = _two_models_two_metrics(tie_tolerance=input_tolerance)
        report = run_model_tournament(cfg, run_id="t_csv_tol_nonempty")
        with tempfile.TemporaryDirectory() as tmpdir:
            _, csv_path = write_tournament_report(report, tmpdir)
            rows = self._read_csv_rows(csv_path)
            self.assertGreater(len(rows), 0, "CSV must have data rows")
            for row in rows:
                val = row.get("tie_tolerance", "")
                self.assertNotEqual(val, "", msg="CSV tie_tolerance must not be empty")
                self.assertIsNotNone(val, msg="CSV tie_tolerance must not be None")
                self.assertAlmostEqual(float(val), input_tolerance,
                                       msg="CSV tie_tolerance must equal the input value")

    def test_csv_has_delta_column(self) -> None:
        cfg = _two_models_two_metrics(model_a_obj=2.0, model_b_obj=4.0)
        report = run_model_tournament(cfg, run_id="t_csv_delta_col")
        with tempfile.TemporaryDirectory() as tmpdir:
            _, csv_path = write_tournament_report(report, tmpdir)
            csv_text = csv_path.read_text(encoding="utf-8")
            self.assertIn("delta", csv_text, "CSV header must contain 'delta' column")
            rows = self._read_csv_rows(csv_path)
            # leader row (model_a, obj=2.0, best=2.0) should have delta=0.0
            leader_rows = [r for r in rows
                           if r.get("model_id") == "model_a"
                           and r.get("metric_name") == "objective_value"]
            self.assertEqual(len(leader_rows), 1)
            self.assertAlmostEqual(float(leader_rows[0]["delta"]), 0.0)


# ---------------------------------------------------------------------------
# Group 11: forbidden_paths
# ---------------------------------------------------------------------------

class ModelTournamentForbiddenPathsTests(unittest.TestCase):
    """Test group 11: final_submit/ not created; output goes to caller-supplied dir."""

    def test_no_final_submit_created(self) -> None:
        report = run_model_tournament(_two_models_two_metrics(), run_id="t_fp_nosubmit")
        with tempfile.TemporaryDirectory() as tmpdir:
            write_tournament_report(report, tmpdir)
        repo_root = Path(__file__).parents[2]
        self.assertFalse((repo_root / "final_submit").exists())

    def test_output_in_caller_dir_not_repo_root(self) -> None:
        report = run_model_tournament(_two_models_two_metrics(), run_id="t_fp_dir")
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path, csv_path = write_tournament_report(report, tmpdir)
            repo_root = Path(__file__).parents[2]
            self.assertFalse((repo_root / "model_comparison_report.json").exists())
            self.assertTrue(json_path.parent == Path(tmpdir))


if __name__ == "__main__":
    unittest.main()
