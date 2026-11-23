"""Regression tests for post-Gate3 model-version authorization binding."""

from __future__ import annotations

import json
import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from pipeline_core.python_tools.model_authority import (
    compute_model_authority,
    verify_model_authority,
)
from pipeline_core.python_tools.human_gate import authorize_stage_transition
import run_case as run_case_module
from run_case import run_case


class PostGate3ModelAuthorityBindingTests(unittest.TestCase):
    def make_case(self, root: Path) -> Path:
        case = root / "case_authority"
        (case / "human_review").mkdir(parents=True)
        (case / "handoff").mkdir()
        (case / "code" / "python").mkdir(parents=True)
        (case / "data" / "raw").mkdir(parents=True)
        (case / "data" / "processed").mkdir(parents=True)
        (case / "case_config.json").write_text(
            json.dumps(
                {
                    "case_id": "case_authority",
                    "submission_tier": "training",
                    "citation_ready": False,
                    "formal_reference_allowed": False,
                }
            ),
            encoding="utf-8",
        )
        (case / "human_review" / "gate_2_model_choice.md").write_text(
            "- [x] model route reviewed\n"
            "## Decision\n"
            "- [x] APPROVE\n"
            "confirmed_by: Human Reviewer\n"
            "confirmed_at: 2026-08-15T00:00:00+08:00\n",
            encoding="utf-8",
        )
        (case / "handoff" / "MODEL_BUILDING_HANDOFF.md").write_text(
            "selected_model_route: mechanism_fitting\n", encoding="utf-8"
        )
        (case / "code" / "MODEL_IMPLEMENTATION_SPEC.md").write_text(
            "model specification v1\n", encoding="utf-8"
        )
        (case / "code" / "python" / "model_contract.py").write_text(
            'APPROVED_ROUTE_ID = "mechanism_fitting"\n'
            'APPROVED_MODEL_FAMILY = "minimum_sufficient_mechanics"\n',
            encoding="utf-8",
        )
        (case / "code" / "python" / "solver.py").write_text(
            "def solve():\n    return 1\n", encoding="utf-8"
        )
        (case / "model_config.json").write_text(
            json.dumps(
                {
                    "model_type": "statistics.linear_regression",
                    "input_path": "data/processed/regression.csv",
                    "target": "y",
                    "features": ["x1"],
                }
            ),
            encoding="utf-8",
        )
        (case / "data" / "raw" / "sample.csv").write_text("ok\n", encoding="utf-8")
        (case / "data" / "processed" / "regression.csv").write_text(
            "x1,y\n1,2\n2,4\n3,6\n", encoding="utf-8"
        )
        return case

    def approve_current_hash(self, case: Path, approval_id: str = "HUMAN-TEST-1") -> str:
        current = compute_model_authority(case)
        review = (
            "MODEL_AUTHORIZATION_STATUS: APPROVED\n"
            f"CASE_ID: {current['case_id']}\n"
            f"MODEL_ROUTE: {current['model_route']}\n"
            f"MODEL_FAMILY: {current['model_family']}\n"
            f"MODEL_AUTHORITY_HASH: {current['model_authority_hash']}\n"
            "MODEL_ACTIVE_ENTRYPOINT: code/python/solver.py\n"
            f"HUMAN_APPROVAL_ID: {approval_id}\n"
            "APPROVAL_TIME: 2026-08-15T00:00:00+08:00\n"
        )
        (case / "human_review" / "MODEL_REVISION_REVIEW.md").write_text(review, encoding="utf-8")
        return current["model_authority_hash"]

    def test_readme_change_does_not_invalidate_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = self.make_case(Path(temp))
            self.approve_current_hash(case)
            (case / "README.md").write_text("documentation only\n", encoding="utf-8")
            self.assertEqual(verify_model_authority(case)["status"], "PASS")

    def test_workflow_state_change_does_not_invalidate_model_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = self.make_case(Path(temp))
            self.approve_current_hash(case)
            config_path = case / "case_config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config.update(
                {
                    "status": "SOLVER_EXECUTION",
                    "current_stage": "SOLVER_EXECUTION",
                    "previous_stage": "MODEL_IMPLEMENTATION",
                    "next_allowed_stage": "RESULT_VALIDATION",
                    "state_revision": 3,
                    "last_transition_id": "0" * 64,
                    "last_transition_at": "2026-08-23T00:00:00+00:00",
                }
            )
            config_path.write_text(
                json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(verify_model_authority(case)["status"], "PASS")

    def test_literature_grounding_state_change_does_not_invalidate_model_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = self.make_case(Path(temp))
            approved_hash = self.approve_current_hash(case)
            config_path = case / "case_config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config.update(
                {
                    "literature_status": "VERIFIED",
                    "formal_literature_search_status": "EXTERNAL_METHODS_VERIFIED",
                    "citation_ready": True,
                    "formal_reference_allowed": True,
                }
            )
            config_path.write_text(
                json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            computed = compute_model_authority(case)
            self.assertEqual(computed["model_authority_hash"], approved_hash)
            self.assertEqual(verify_model_authority(case)["status"], "PASS")

    def test_downstream_workflow_status_changes_do_not_invalidate_model_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = self.make_case(Path(temp))
            approved_hash = self.approve_current_hash(case)
            config_path = case / "case_config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            downstream_updates = {
                "track_b_status": "PASS_WITH_LIMITATIONS",
                "track_c_status": "PASS",
                "paper_status": "RENDERED",
                "post_writer_reentry_status": "PASS",
                "final_review_status": "PASS_WITH_LIMITATIONS",
            }

            for field, value in downstream_updates.items():
                with self.subTest(field=field):
                    config[field] = value
                    config_path.write_text(
                        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    computed = compute_model_authority(case)
                    self.assertEqual(computed["model_authority_hash"], approved_hash)
                    self.assertEqual(verify_model_authority(case)["status"], "PASS")

    def test_unrecognized_case_config_change_still_invalidates_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = self.make_case(Path(temp))
            self.approve_current_hash(case)
            config_path = case / "case_config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["model_parameter_profile"] = "changed-after-approval"
            config_path.write_text(
                json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            result = verify_model_authority(case)
            self.assertEqual(result["reason"], "MODEL_AUTHORIZATION_INVALIDATED")

    def test_model_spec_change_invalidates_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = self.make_case(Path(temp))
            self.approve_current_hash(case)
            (case / "code" / "MODEL_IMPLEMENTATION_SPEC.md").write_text(
                "model specification v2\n", encoding="utf-8"
            )
            result = verify_model_authority(case)
            self.assertEqual(result["reason"], "MODEL_AUTHORIZATION_INVALIDATED")

    def test_core_solver_change_invalidates_even_when_route_and_family_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = self.make_case(Path(temp))
            self.approve_current_hash(case)
            (case / "code" / "python" / "solver.py").write_text(
                "def solve():\n    return 2\n", encoding="utf-8"
            )
            result = verify_model_authority(case)
            self.assertEqual(result["reason"], "MODEL_AUTHORIZATION_INVALIDATED")

    def test_new_human_approved_hash_restores_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = self.make_case(Path(temp))
            self.approve_current_hash(case, approval_id="HUMAN-OLD")
            (case / "code" / "MODEL_IMPLEMENTATION_SPEC.md").write_text(
                "model specification v2\n", encoding="utf-8"
            )
            self.assertNotEqual(verify_model_authority(case)["status"], "PASS")
            new_hash = self.approve_current_hash(case, approval_id="HUMAN-NEW")
            verified = verify_model_authority(case)
            self.assertEqual(verified["status"], "PASS")
            self.assertEqual(verified["model_authority_hash"], new_hash)

    def test_stage_artifact_hash_must_match_human_approved_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = self.make_case(Path(temp))
            current_hash = self.approve_current_hash(case)
            authorization = case / "authorization.json"
            authorization.write_text(
                json.dumps(
                    {
                        "model_authority_hash": "stale-hash",
                        "model_authority_approval_id": "HUMAN-TEST-1",
                        "model_active_entrypoint": "code/python/solver.py",
                    }
                ),
                encoding="utf-8",
            )
            invalid = verify_model_authority(case, authorization_path=authorization)
            self.assertEqual(invalid["reason"], "MODEL_AUTHORIZATION_INVALIDATED")
            authorization.write_text(
                json.dumps(
                    {
                        "model_authority_hash": current_hash,
                        "model_authority_approval_id": "HUMAN-TEST-1",
                        "model_active_entrypoint": "code/python/solver.py",
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                verify_model_authority(case, authorization_path=authorization)["status"],
                "PASS",
            )

    def test_stage_authorization_blocks_without_model_revision_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = self.make_case(Path(temp))
            result = authorize_stage_transition(
                case,
                current_stage="MODEL_IMPLEMENTATION",
                requested_stage="SOLVER_EXECUTION",
                mode="competition",
            )
            self.assertEqual(result["control_decision"], "BLOCKED")
            self.assertEqual(result["reason"], "MODEL_REVIEW_REQUIRED")

    def test_approved_model_without_active_entrypoint_remains_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = self.make_case(Path(temp))
            current = compute_model_authority(case)
            (case / "human_review" / "MODEL_REVISION_REVIEW.md").write_text(
                "MODEL_AUTHORIZATION_STATUS: APPROVED\n"
                f"CASE_ID: {current['case_id']}\n"
                f"MODEL_ROUTE: {current['model_route']}\n"
                f"MODEL_FAMILY: {current['model_family']}\n"
                f"MODEL_AUTHORITY_HASH: {current['model_authority_hash']}\n"
                "MODEL_ACTIVE_ENTRYPOINT: PENDING_USER_SELECTION\n"
                "HUMAN_APPROVAL_ID: HUMAN-TEST-1\n"
                "APPROVAL_TIME: 2026-08-15T00:00:00+08:00\n",
                encoding="utf-8",
            )
            result = verify_model_authority(case)
            self.assertEqual(result["reason"], "MODEL_ACTIVE_ENTRYPOINT_REQUIRED")

    def test_approved_model_entrypoint_must_match_direct_solver(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = self.make_case(Path(temp))
            self.approve_current_hash(case)
            result = verify_model_authority(case, entrypoint="code/python/other_solver.py")
            self.assertEqual(result["reason"], "MODEL_ACTIVE_ENTRYPOINT_MISMATCH")

    def test_direct_run_case_with_require_false_blocks_before_run_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = self.make_case(Path(temp))
            with patch("math_core.registry.run_model") as run_model, patch(
                "run_case._update_compliance_queue", return_value={}
            ):
                result = run_case(
                    "run",
                    case,
                    30,
                    require_pre_modeling_wiring=False,
                )
            run_model.assert_not_called()
            self.assertEqual(result["status"], "fatal")
            self.assertEqual(result["exception"], "MODEL_REVIEW_REQUIRED")
            shutil.rmtree(Path(result["run_dir"]), ignore_errors=True)

    def test_developer_cli_path_blocks_before_run_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = self.make_case(Path(temp))
            argv = ["run_case.py", "--mode", "developer", "--case-dir", str(case)]
            work_tmp = run_case_module.ROOT / "work_tmp"
            before = set(work_tmp.glob("run_*")) if work_tmp.is_dir() else set()
            with patch.object(sys, "argv", argv), patch(
                "math_core.registry.run_model"
            ) as run_model, patch("run_case._update_compliance_queue", return_value={}):
                with redirect_stdout(io.StringIO()):
                    exit_code = run_case_module.main()
            for created in set(work_tmp.glob("run_*")) - before:
                shutil.rmtree(created, ignore_errors=True)
            run_model.assert_not_called()
            self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
