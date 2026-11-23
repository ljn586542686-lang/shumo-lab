from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from package_candidate import CandidatePromotionUpstreamStatusBlocked, package_candidate
from scripts.stabilization_freeze import evaluate


class CandidateUpstreamBlockGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "root"
        self.case_dir = self.root / "case"
        self.case_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _config(self, **extra: object) -> None:
        payload = {"submission_tier": "formal", "core_claims": [], "human_revision_required": False}
        payload.update(extra)
        (self.case_dir / "case_config.json").write_text(json.dumps(payload), encoding="utf-8")

    def _upstream(self, name: str, payload: object) -> str:
        path = self.case_dir / "output" / "validation" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path.relative_to(self.case_dir).as_posix()

    def _pass_payload(self, *bindings: dict[str, object]) -> dict[str, object]:
        return {"risk_status": "PASS", "status": "ok", "fatal_count": 0, "upstream_content_bindings": list(bindings)}

    def _binding(self, relative_path: str, role: str = "result_summary") -> dict[str, object]:
        path = self.case_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("fixture", encoding="utf-8")
        return {
            "logical_name": role,
            "relative_path": relative_path,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "required_for_candidate_validation": True,
            "source_role": role,
        }

    def _blocked(self) -> dict[str, object]:
        with self.assertRaises(CandidatePromotionUpstreamStatusBlocked) as caught:
            package_candidate(case_dir=self.case_dir, root=self.root)
        result = caught.exception.result
        self.assertFalse(result["candidate_package_created"])
        self.assertFalse(result["final_submit_created"])
        self.assertFalse((self.root / "candidate_submit").exists())
        return result

    def test_formal_missing_status_blocks_before_mkdir(self) -> None:
        self._config()
        result = self._blocked()
        self.assertEqual(result["code"], "CANDIDATE_PROMOTION_UPSTREAM_STATUS_MISSING")
        self.assertTrue(result["effective_upstream_status_required"])

    def test_selection_missing_status_blocks_before_mkdir(self) -> None:
        self._config(submission_tier="selection")
        self.assertEqual(self._blocked()["code"], "CANDIDATE_PROMOTION_UPSTREAM_STATUS_MISSING")

    def test_training_missing_status_reaches_existing_claim_check(self) -> None:
        self._config(submission_tier="training")
        claim = {"status": "MANUAL_REVIEW_REQUIRED", "blockers": [], "warnings": [], "final_submit_created": False}
        with mock.patch("package_candidate.check_claim_check_preflight", return_value=claim):
            with self.assertRaises(Exception) as caught:
                package_candidate(case_dir=self.case_dir, root=self.root)
        self.assertNotEqual(type(caught.exception).__name__, "CandidatePromotionUpstreamStatusBlocked")
        self.assertFalse((self.root / "candidate_submit").exists())

    def test_run_result_block_blocks_before_mkdir(self) -> None:
        self._config(candidate_upstream_run_result_path=self._upstream("run_result.json", {"risk_status": "BLOCK"}))
        result = self._blocked()
        self.assertEqual(result["code"], "CANDIDATE_PROMOTION_UPSTREAM_BLOCKED")
        self.assertEqual(result["upstream_blocker"], "UPSTREAM_STATUS_BLOCK")

    def test_run_result_fatal_blocks(self) -> None:
        self._config(candidate_upstream_run_result_path=self._upstream("run_result.json", {"status": "fatal"}))
        self.assertEqual(self._blocked()["upstream_blocker"], "UPSTREAM_STATUS_BLOCK")

    def test_risk_summary_fatal_count_blocks(self) -> None:
        self._config(candidate_upstream_risk_summary_path=self._upstream("risk_summary.json", {"fatal_count": 1, "risks": []}))
        self.assertEqual(self._blocked()["upstream_blocker"], "UPSTREAM_FATAL_COUNT")

    def test_validation_continue_false_blocks(self) -> None:
        self._config(candidate_upstream_run_result_path=self._upstream("run_result.json", {"risk_status": "PASS", "validation_continue_allowed": False}))
        self.assertEqual(self._blocked()["upstream_blocker"], "UPSTREAM_VALIDATION_CONTINUE_DISABLED")

    def test_malformed_json_blocks_cleanly(self) -> None:
        path = self.case_dir / "output" / "validation" / "run_result.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{", encoding="utf-8")
        self._config(candidate_upstream_run_result_path=path.relative_to(self.case_dir).as_posix())
        self.assertEqual(self._blocked()["upstream_blocker"], "UPSTREAM_STATUS_MALFORMED")

    def test_pass_status_flows_to_existing_claim_check_before_mkdir(self) -> None:
        self._config(candidate_upstream_run_result_path=self._upstream("run_result.json", self._pass_payload(self._binding("output/result_summary.json"))))
        claim = {"status": "MANUAL_REVIEW_REQUIRED", "blockers": [], "warnings": [], "final_submit_created": False}
        with mock.patch("package_candidate.check_claim_check_preflight", return_value=claim):
            with self.assertRaises(Exception) as caught:
                package_candidate(case_dir=self.case_dir, root=self.root)
        self.assertNotEqual(type(caught.exception).__name__, "CandidatePromotionUpstreamStatusBlocked")
        self.assertFalse((self.root / "candidate_submit").exists())

    def test_pass_status_modified_result_summary_blocks_before_mkdir(self) -> None:
        binding = self._binding("output/result_summary.json")
        self._config(candidate_upstream_run_result_path=self._upstream("run_result.json", self._pass_payload(binding)))
        (self.case_dir / "output" / "result_summary.json").write_text("modified", encoding="utf-8")
        result = self._blocked()
        self.assertEqual(result["code"], "CANDIDATE_PROMOTION_UPSTREAM_STATUS_STALE")
        self.assertTrue(result["stale_upstream_status"])
        self.assertEqual(result["content_binding_mismatches"][0]["reason"], "sha256_mismatch")

    def test_pass_status_modified_paper_source_blocks_before_mkdir(self) -> None:
        binding = self._binding("paper/excerpt.md", "paper_source")
        self._config(candidate_upstream_run_result_path=self._upstream("run_result.json", self._pass_payload(binding)))
        (self.case_dir / "paper" / "excerpt.md").write_text("modified paper", encoding="utf-8")
        self.assertEqual(self._blocked()["code"], "CANDIDATE_PROMOTION_UPSTREAM_STATUS_STALE")

    def test_pass_status_without_content_bindings_blocks_before_mkdir(self) -> None:
        self._config(candidate_upstream_run_result_path=self._upstream("run_result.json", {"risk_status": "PASS", "status": "ok"}))
        self.assertEqual(self._blocked()["code"], "CANDIDATE_PROMOTION_UPSTREAM_STATUS_STALE")

    def test_empty_content_bindings_block_before_mkdir(self) -> None:
        self._config(candidate_upstream_run_result_path=self._upstream("run_result.json", self._pass_payload()))
        self.assertEqual(self._blocked()["code"], "CANDIDATE_PROMOTION_UPSTREAM_STATUS_STALE")

    def test_malformed_content_bindings_block_before_mkdir(self) -> None:
        self._config(candidate_upstream_run_result_path=self._upstream("run_result.json", self._pass_payload({"relative_path": "output/result_summary.json"})))
        self.assertEqual(self._blocked()["code"], "CANDIDATE_PROMOTION_UPSTREAM_STATUS_STALE")

    def test_upstream_guard_precedes_claim_check_and_mkdir(self) -> None:
        self._config(candidate_upstream_run_result_path=self._upstream("run_result.json", {"risk_status": "BLOCK"}))
        with mock.patch("package_candidate.check_claim_check_preflight") as claim, mock.patch.object(Path, "mkdir") as mkdir:
            self._blocked()
        claim.assert_not_called()
        mkdir.assert_not_called()

    def test_freeze_tripwire_and_ids_remain_unchanged(self) -> None:
        report = evaluate(Path(__file__).resolve().parents[2])
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["registered_ids_current"], report["registered_ids_baseline"])


if __name__ == "__main__":
    unittest.main()
