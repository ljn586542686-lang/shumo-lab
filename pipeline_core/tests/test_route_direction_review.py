from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from pipeline_core.model_route.route_direction_review import validate_route_direction_review
from pipeline_core.model_route.route_priority import resolve_model_route
from scripts.stabilization_freeze import evaluate


class RouteDirectionReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.case_dir = self.root / "case"
        self.case_dir.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_json(self, relative_path: str, payload: object) -> None:
        path = self.case_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _config(self, tier: str = "selection", **extra: object) -> None:
        payload = {"case_id": "route_fixture", "submission_tier": tier}
        payload.update(extra)
        self._write_json("case_config.json", payload)

    def _artifact(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "version": "1.0",
            "case_id": "route_fixture",
            "submission_tier": "selection",
            "status": "PASS",
            "selected_direction": "forecast supplier demand",
            "selected_route_id": "prediction_timeseries",
            "selected_model_family": "time_series",
            "evidence_inputs": [
                {"path": "problem_type.json", "role": "problem_type"},
                {"path": "model_routes.md", "role": "route_options"},
            ],
            "rejected_alternatives": [
                {"route_id": "optimization_planning", "model_family": "optimization", "rejection_reason": "not primary task"},
                {"route_id": "graph_network_spatial_coverage", "model_family": "network", "rejection_reason": "no spatial target"},
            ],
            "red_team_challenge": "Could optimization be the primary route?",
            "red_team_response": "The selected route follows the stated prediction target.",
            "provider_role_id": "route_provider",
            "reviewer_role_id": "route_reviewer",
            "review_evidence": {
                "review_record_ref": "external_chat://route-review-fixture",
                "review_record_type": "external_chat",
                "reviewer_evidence_ref": "human_review/gate_2_model_choice.md",
            },
            "created_at": "2026-07-11T09:00:00+00:00",
            "immutable_after_result_generation": True,
            "result_generation_state_at_review": "not_started",
        }
        payload.update(overrides)
        return payload

    def _write_valid_fixture(self, tier: str = "selection", **overrides: object) -> None:
        self._config(tier)
        self._write_json("problem_type.json", {"primary_problem_type": "prediction_timeseries"})
        (self.case_dir / "model_routes.md").write_text("# Routes\n", encoding="utf-8")
        artifact = self._artifact(submission_tier=tier, **overrides)
        self._write_json("route/route_direction_review.json", artifact)

    def test_selection_missing_artifact_blocks(self) -> None:
        self._config("selection")
        result = validate_route_direction_review(self.case_dir)
        self.assertEqual(result["route_review_status"], "BLOCKED")
        self.assertIn("ROUTE_REVIEW_MISSING", result["blockers"])

    def test_formal_missing_artifact_blocks(self) -> None:
        self._config("formal")
        self.assertEqual(validate_route_direction_review(self.case_dir)["route_review_status"], "BLOCKED")

    def test_training_missing_artifact_is_non_blocking_and_preserves_priority(self) -> None:
        self._config("training")
        result = validate_route_direction_review(self.case_dir)
        resolved = resolve_model_route(self.case_dir, router_suggestion="prediction_timeseries")
        self.assertEqual(result["route_review_status"], "MANUAL_REVIEW_REQUIRED")
        self.assertFalse(result["blockers"])
        self.assertEqual(resolved["final_model_route"], "prediction_timeseries")

    def test_valid_selection_artifact_passes_and_is_authoritative(self) -> None:
        self._write_valid_fixture()
        review = validate_route_direction_review(self.case_dir)
        resolved = resolve_model_route(self.case_dir, router_suggestion="optimization_planning")
        self.assertEqual(review["route_review_status"], "PASS")
        self.assertEqual(resolved["final_model_route"], "prediction_timeseries")
        self.assertEqual(resolved["route_source"], "P0_ROUTE_REVIEW / route_direction_review")

    def test_selection_requires_structural_review_evidence(self) -> None:
        self._write_valid_fixture(review_evidence=None)
        result = validate_route_direction_review(self.case_dir)
        self.assertEqual(result["route_review_status"], "BLOCKED")
        self.assertIn("ROUTE_REVIEW_STRUCTURAL_EVIDENCE_MISSING", result["blockers"])
        self._write_valid_fixture(review_evidence={"review_record_ref": "", "review_record_type": "external_chat"})
        self.assertIn("ROUTE_REVIEW_STRUCTURAL_EVIDENCE_MISSING", validate_route_direction_review(self.case_dir)["blockers"])

    def test_provider_reviewer_strings_alone_are_insufficient_for_selection(self) -> None:
        self._write_valid_fixture(review_evidence=None)
        result = validate_route_direction_review(self.case_dir)
        self.assertEqual(result["provider_role_id"], "route_provider")
        self.assertEqual(result["reviewer_role_id"], "route_reviewer")
        self.assertIn("ROUTE_REVIEW_STRUCTURAL_EVIDENCE_MISSING", result["blockers"])

    def test_empty_or_invalid_review_record_type_blocks_selection(self) -> None:
        for review_record_type in ("", "not_a_valid_type"):
            with self.subTest(review_record_type=review_record_type):
                self._write_valid_fixture(
                    review_evidence={
                        "review_record_ref": "external_chat://route-review-fixture",
                        "review_record_type": review_record_type,
                    }
                )
                result = validate_route_direction_review(self.case_dir)
                self.assertEqual(result["route_review_status"], "BLOCKED")
                self.assertIn("ROUTE_REVIEW_STRUCTURAL_EVIDENCE_MISSING", result["blockers"])

    def test_missing_structural_evidence_disables_selection_route_fallback(self) -> None:
        self._write_valid_fixture(review_evidence=None)
        review = validate_route_direction_review(self.case_dir)
        resolved = resolve_model_route(self.case_dir, router_suggestion="optimization_planning")
        self.assertEqual(review["route_review_status"], "BLOCKED")
        self.assertIn("ROUTE_REVIEW_STRUCTURAL_EVIDENCE_MISSING", review["blockers"])
        self.assertIsNone(resolved["final_model_route"])
        self.assertEqual(resolved["route_source"], "ROUTE_REVIEW_BLOCKED")
        self.assertIn("ROUTE_REVIEW_STRUCTURAL_EVIDENCE_MISSING", resolved["blockers"])

    def test_insufficient_alternatives_blocks(self) -> None:
        self._write_valid_fixture(rejected_alternatives=[self._artifact()["rejected_alternatives"][0]])
        self.assertIn("ROUTE_REVIEW_INSUFFICIENT_ALTERNATIVES", validate_route_direction_review(self.case_dir)["blockers"])

    def test_empty_rejection_reason_blocks(self) -> None:
        alternatives = self._artifact()["rejected_alternatives"]
        alternatives[0]["rejection_reason"] = ""
        self._write_valid_fixture(rejected_alternatives=alternatives)
        self.assertIn("ROUTE_REVIEW_EMPTY_REJECTION_REASON", validate_route_direction_review(self.case_dir)["blockers"])

    def test_selected_route_rejected_blocks(self) -> None:
        alternatives = self._artifact()["rejected_alternatives"]
        alternatives[0]["route_id"] = "prediction_timeseries"
        self._write_valid_fixture(rejected_alternatives=alternatives)
        self.assertIn("ROUTE_REVIEW_INVALID_SCHEMA", validate_route_direction_review(self.case_dir)["blockers"])

    def test_self_review_and_missing_reviewer_block(self) -> None:
        self._write_valid_fixture(reviewer_role_id="route_provider")
        self.assertIn("ROUTE_REVIEW_ROLE_SEPARATION_FAILED", validate_route_direction_review(self.case_dir)["blockers"])
        self._write_valid_fixture(reviewer_role_id="")
        self.assertIn("ROUTE_REVIEW_ROLE_SEPARATION_FAILED", validate_route_direction_review(self.case_dir)["blockers"])

    def test_blocked_and_manual_statuses_block_selection(self) -> None:
        self._write_valid_fixture(status="BLOCKED")
        self.assertIn("ROUTE_REVIEW_STATUS_BLOCKED", validate_route_direction_review(self.case_dir)["blockers"])
        self._write_valid_fixture(status="MANUAL_REVIEW_REQUIRED")
        self.assertIn("ROUTE_REVIEW_MANUAL_REVIEW_REQUIRED", validate_route_direction_review(self.case_dir)["blockers"])

    def test_unsafe_or_missing_evidence_blocks(self) -> None:
        self._write_valid_fixture(evidence_inputs=[{"path": "../outside.md", "role": "bad"}])
        self.assertIn("ROUTE_REVIEW_EVIDENCE_MISSING", validate_route_direction_review(self.case_dir)["blockers"])
        self._write_valid_fixture(evidence_inputs=[{"path": "missing.md", "role": "missing"}])
        self.assertIn("ROUTE_REVIEW_EVIDENCE_MISSING", validate_route_direction_review(self.case_dir)["blockers"])

    def test_post_hoc_timestamp_and_result_state_block(self) -> None:
        self._write_valid_fixture(created_at="2026-07-12T09:00:00+00:00")
        self._write_json("output/result_summary.json", {"generated_at": "2026-07-11T09:00:00+00:00"})
        result = validate_route_direction_review(self.case_dir)
        self.assertIn("ROUTE_REVIEW_POST_HOC_SUSPECTED", result["blockers"])
        self.assertTrue(result["post_hoc_suspected"])
        self._write_valid_fixture(result_generation_state_at_review="results_available")
        self.assertIn("ROUTE_REVIEW_POST_HOC_SUSPECTED", validate_route_direction_review(self.case_dir)["blockers"])

    def test_immutable_after_result_generation_is_required(self) -> None:
        self._write_valid_fixture(immutable_after_result_generation=False)
        self.assertIn("ROUTE_REVIEW_INVALID_SCHEMA", validate_route_direction_review(self.case_dir)["blockers"])

    def test_p0_p1_route_conflict_blocks_selection(self) -> None:
        self._write_valid_fixture()
        self._config("selection", model_route="optimization_planning")
        result = resolve_model_route(self.case_dir)
        self.assertEqual(result["route_review_status"], "BLOCKED")
        self.assertIn("ROUTE_REVIEW_ROUTE_CONFLICT", result["blockers"])
        self.assertIsNone(result["final_model_route"])

    def test_training_invalid_artifact_warns_without_hard_block(self) -> None:
        self._write_valid_fixture("training", reviewer_role_id="route_provider")
        review = validate_route_direction_review(self.case_dir)
        resolved = resolve_model_route(self.case_dir, router_suggestion="prediction_timeseries")
        self.assertEqual(review["route_review_status"], "PASS")
        self.assertEqual(resolved["final_model_route"], "prediction_timeseries")
        self._write_valid_fixture("training", evidence_inputs=[{"path": "../unsafe", "role": "bad"}])
        review = validate_route_direction_review(self.case_dir)
        self.assertEqual(review["route_review_status"], "MANUAL_REVIEW_REQUIRED")
        self.assertFalse(review["blockers"])

    def test_training_missing_review_evidence_is_lightweight_warning(self) -> None:
        self._write_valid_fixture("training", review_evidence=None)
        review = validate_route_direction_review(self.case_dir)
        self.assertEqual(review["route_review_status"], "PASS")
        self.assertIn("ROUTE_REVIEW_STRUCTURAL_EVIDENCE_MISSING", review["warnings"])

    def test_low_confidence_or_overridden_router_requires_general_incident_record(self) -> None:
        incident = {
            "observed_suggestion": "classification_clustering",
            "selected_route_id": "prediction_timeseries",
            "root_cause_hypothesis": "题面特征不足以区分机制与分类任务",
            "action": "REVIEW_ONLY",
        }
        self._write_valid_fixture(router_suggestion="classification_clustering", router_confidence="LOW")
        review = validate_route_direction_review(self.case_dir)
        self.assertIn("ROUTING_INCIDENT_MISSING", review["blockers"])
        self._write_valid_fixture(
            router_suggestion="classification_clustering", router_confidence="LOW", routing_incident=incident
        )
        review = validate_route_direction_review(self.case_dir)
        self.assertEqual(review["routing_incident_status"], "PASS")
        self.assertEqual(review["route_review_status"], "PASS")

    def _git(self, *args: str, date: str | None = None) -> None:
        env = dict(os.environ)
        if date is not None:
            env["GIT_AUTHOR_DATE"] = date
            env["GIT_COMMITTER_DATE"] = date
        completed = subprocess.run(
            ["git", "-c", "core.excludesfile=", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
            env=env,
        )
        if completed.returncode != 0:
            self.fail(f"temporary git command failed: {args}; stderr={completed.stderr.strip()}")

    def _init_git_fixture(self, *, result_before_route: bool) -> None:
        self._git("init")
        self._git("config", "user.email", "route-fixture@example.test")
        self._git("config", "user.name", "Route Fixture")
        self._config("selection")
        self._write_json("problem_type.json", {"primary_problem_type": "prediction_timeseries"})
        (self.case_dir / "model_routes.md").write_text("# Routes\n", encoding="utf-8")
        self._write_json("output/result_summary.json", {"generated_at": "2026-07-12T09:00:00+00:00"})
        case_prefix = self.case_dir.relative_to(self.root).as_posix()
        if result_before_route:
            self._git("add", f"{case_prefix}/output/result_summary.json")
            self._git("commit", "-m", "result first", date="2026-07-12T09:00:00+00:00")
            self._write_json("route/route_direction_review.json", self._artifact())
            self._git("add", f"{case_prefix}/route/route_direction_review.json")
            self._git("commit", "-m", "route after result", date="2026-07-13T09:00:00+00:00")
        else:
            self._write_json("route/route_direction_review.json", self._artifact())
            self._git("add", f"{case_prefix}/route/route_direction_review.json")
            self._git("commit", "-m", "route before result", date="2026-07-11T09:00:00+00:00")
            self._git("add", f"{case_prefix}/output/result_summary.json")
            self._git("commit", "-m", "result after route", date="2026-07-12T09:00:00+00:00")

    def test_git_timing_anchor_passes_for_review_committed_before_result(self) -> None:
        self._init_git_fixture(result_before_route=False)
        review = validate_route_direction_review(self.case_dir)
        self.assertEqual(review["route_review_status"], "PASS")
        self.assertEqual(review["git_timing_anchor_status"], "PASS")
        self.assertNotIn("ROUTE_REVIEW_GIT_TIMING_ANCHOR_UNAVAILABLE", review["warnings"])

    def test_git_timing_anchor_blocks_review_committed_after_result(self) -> None:
        self._init_git_fixture(result_before_route=True)
        review = validate_route_direction_review(self.case_dir)
        self.assertEqual(review["route_review_status"], "BLOCKED")
        self.assertEqual(review["git_timing_anchor_status"], "FAILED")
        self.assertIn("ROUTE_REVIEW_POST_HOC_GIT_ANCHOR_FAILED", review["blockers"])

    def test_untracked_git_timing_anchor_is_never_claimed_as_pass(self) -> None:
        self._write_valid_fixture()
        review = validate_route_direction_review(self.case_dir)
        self.assertEqual(review["route_review_status"], "PASS")
        self.assertEqual(review["git_timing_anchor_status"], "UNAVAILABLE")
        self.assertIn("ROUTE_REVIEW_GIT_TIMING_ANCHOR_UNAVAILABLE", review["warnings"])

    def test_no_candidate_or_final_submit_and_freeze_ids_unchanged(self) -> None:
        self._write_valid_fixture()
        resolve_model_route(self.case_dir)
        self.assertFalse((self.root / "candidate_submit").exists())
        self.assertFalse((self.root / "final_submit").exists())
        report = evaluate(Path(__file__).resolve().parents[2])
        # This checkout can already contain unrelated protected-file drift;
        # this regression only proves route work creates neither submission
        # directories nor unregistered feature/gate identifiers.
        self.assertFalse(report["final_submit_created"])
        self.assertEqual(report["registered_ids_current"], report["registered_ids_baseline"])
        implementation = (Path(__file__).resolve().parents[1] / "model_route" / "route_direction_review.py").read_text(encoding="utf-8")
        self.assertNotIn("XVAL-10", implementation)


if __name__ == "__main__":
    unittest.main()
