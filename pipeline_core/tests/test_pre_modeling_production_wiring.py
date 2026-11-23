"""Focused regression tests for the formal pre-modeling production wiring."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run_case as run_case_module
from pipeline_core.literature.support_chain.support_chain_loader import build_support_chain_context
from pipeline_core.python_tools.human_gate import authorize_stage_transition
from pipeline_core.router.problem_type_router import (
    ProblemContractBlocked,
    build_output,
    render_model_building_handoff,
    validate_model_building_handoff,
    write_model_building_handoff,
    write_case_outputs,
)
from pipeline_core.tests.test_human_gate_decision_semantics import GATE_FILES, write_gate_case
from run_case import (
    _run_pre_modeling_production_wiring,
    _verify_competition_solver_authorization,
    _write_math_core_summary,
)


def make_case(root: Path, *, case_id: str = "pre_modeling_test") -> Path:
    case_dir = write_gate_case(root / case_id, 1, 0)
    (case_dir / "problem").mkdir(parents=True, exist_ok=True)
    (case_dir / "data" / "raw").mkdir(parents=True, exist_ok=True)
    (case_dir / "data" / "raw" / "problem.pdf").write_bytes(b"%PDF-test-fixture")
    (case_dir / "case_config.json").write_text(
        json.dumps(
            {
                "case_id": case_id,
                "problem_statement_files": ["data/raw/problem.pdf"],
                "problem_analysis_path": "problem_analysis.md",
                "problem_contract_path": "problem/problem_contract.md",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (case_dir / "problem" / "problem_contract.md").write_text(
        "# Problem Contract\n\nScope: bounded test input.\n",
        encoding="utf-8",
    )
    (case_dir / "problem_analysis.md").write_text(
        "# Problem Analysis\n\nBounded test analysis and data availability statement.\n",
        encoding="utf-8",
    )
    return case_dir


class PreModelingProductionWiringTests(unittest.TestCase):
    @staticmethod
    def _copy_runtime_root(source: Path, target: Path) -> None:
        def ignore(_directory: str, names: list[str]) -> set[str]:
            return {
                name
                for name in names
                if name in {".git", ".pytest_cache", "__pycache__", "work_tmp", "final_submit"}
            }

        shutil.copytree(source, target, ignore=ignore)

    @staticmethod
    def _make_solver_case(
        root: Path, case_id: str, *, gate2_pending: bool, governance: dict | None = None
    ) -> Path:
        case_dir = write_gate_case(root / "cases" / case_id, 2, 0)
        if gate2_pending:
            template = run_case_module.ROOT / "cases" / "case_template" / "human_review" / GATE_FILES[2]
            shutil.copy2(template, case_dir / "human_review" / GATE_FILES[2])
        (case_dir / "data" / "raw").mkdir(parents=True, exist_ok=True)
        (case_dir / "data" / "processed").mkdir(parents=True, exist_ok=True)
        (case_dir / "data" / "raw" / "sample.csv").write_text("ok\n1\n", encoding="utf-8")
        (case_dir / "data" / "processed" / "regression.csv").write_text(
            "x1,x2,y\n1,2,6\n2,1,7\n3,4,14\n4,3,15\n5,5,20\n",
            encoding="utf-8",
        )
        config = {
            "model_type": "statistics.linear_regression",
            "input_path": "data/processed/regression.csv",
            "target": "y",
            "features": ["x1", "x2"],
        }
        (case_dir / "model_config.json").write_text(json.dumps(config), encoding="utf-8")
        if governance is not None:
            case_config_path = case_dir / "case_config.json"
            case_config = json.loads(case_config_path.read_text(encoding="utf-8"))
            case_config["competition_governance"] = governance
            case_config_path.write_text(json.dumps(case_config), encoding="utf-8")
        return case_dir

    def test_real_run_case_gate2_boundary_blocks_and_allows_solver(self) -> None:
        with tempfile.TemporaryDirectory(prefix="run_case_gate2_boundary_") as temp:
            base = Path(temp)
            runtime_root = base / "runtime"
            self._copy_runtime_root(run_case_module.ROOT, runtime_root)
            original_root = run_case_module.ROOT
            run_case_module.ROOT = runtime_root
            try:
                pending = self._make_solver_case(runtime_root, "gate2_pending", gate2_pending=True)
                with patch("math_core.registry.run_model") as solver:
                    blocked = run_case_module.run_case(
                        "run",
                        pending,
                        30,
                        require_pre_modeling_wiring=False,
                        competition_mode=True,
                        competition_current_stage="MODEL_IMPLEMENTATION",
                        competition_requested_stage="SOLVER_EXECUTION",
                    )
                self.assertEqual(solver.call_count, 0)
                self.assertEqual(blocked["competition_solver_authorization"]["status"], "FAIL")
                self.assertEqual(blocked["competition_solver_authorization"]["reason"], "COMPETITION_SOLVER_AUTHORIZATION_REQUIRED")

                approved = self._make_solver_case(runtime_root, "gate2_approved", gate2_pending=False)
                authorization = authorize_stage_transition(
                    approved,
                    current_stage="MODEL_IMPLEMENTATION",
                    requested_stage="SOLVER_EXECUTION",
                    mode="competition",
                )
                authorization_path = approved / "authorization.json"
                authorization_path.write_text(json.dumps(authorization), encoding="utf-8")
                with patch(
                    "math_core.registry.run_model",
                    return_value={
                        "model_id": "test.model",
                        "model_family": "test",
                        "success": True,
                        "status": "ok",
                        "fatal_count": 0,
                        "warning_count": 0,
                        "primary_results": {"ok": True},
                        "risk_items": [],
                    },
                ) as solver:
                    allowed = run_case_module.run_case(
                        "run",
                        approved,
                        30,
                        require_pre_modeling_wiring=False,
                        competition_mode=True,
                        competition_authorization_path=authorization_path,
                        competition_current_stage="MODEL_IMPLEMENTATION",
                        competition_requested_stage="SOLVER_EXECUTION",
                    )
                self.assertEqual(solver.call_count, 1)
                self.assertEqual(allowed["competition_solver_authorization"]["status"], "PASS")
            finally:
                run_case_module.ROOT = original_root

    def test_strict_governance_blocks_formal_run_before_solver_and_never_trusts_canonical(self) -> None:
        with tempfile.TemporaryDirectory(prefix="run_case_governance_boundary_") as temp:
            base = Path(temp)
            runtime_root = base / "runtime"
            self._copy_runtime_root(run_case_module.ROOT, runtime_root)
            original_root = run_case_module.ROOT
            run_case_module.ROOT = runtime_root
            try:
                case_dir = self._make_solver_case(
                    runtime_root,
                    "governance_pending",
                    gate2_pending=False,
                    governance={
                        "enabled": True,
                        "numerical_certification": {
                            "status": "PENDING",
                            "evidence_path": "output/validation/certification.json",
                        },
                    },
                )
                authorization = authorize_stage_transition(
                    case_dir,
                    current_stage="MODEL_IMPLEMENTATION",
                    requested_stage="SOLVER_EXECUTION",
                    mode="competition",
                )
                authorization_path = case_dir / "authorization.json"
                authorization_path.write_text(json.dumps(authorization), encoding="utf-8")
                with patch("math_core.registry.run_model") as solver:
                    result = run_case_module.run_case(
                        "run",
                        case_dir,
                        30,
                        require_pre_modeling_wiring=False,
                        competition_mode=True,
                        competition_authorization_path=authorization_path,
                        competition_current_stage="MODEL_IMPLEMENTATION",
                        competition_requested_stage="SOLVER_EXECUTION",
                    )
                self.assertEqual(solver.call_count, 0)
                self.assertEqual(result["status"], "fatal")
                self.assertEqual(result["model_governance"]["reason"], "NUMERICAL_CERTIFICATION_REQUIRED")
                self.assertFalse(result["model_governance"]["canonical_result_trusted"])
                run_result = (case_dir / result["run_dir"] / "run_result.json").read_text(encoding="utf-8")
                self.assertNotIn('"canonical_result_trusted": true', run_result.lower())
            finally:
                run_case_module.ROOT = original_root

    def test_case_template_enables_strict_governance_by_default(self) -> None:
        template_config = json.loads(
            (run_case_module.ROOT / "cases" / "case_template" / "case_config.json").read_text(
                encoding="utf-8"
            )
        )
        governance = template_config["competition_governance"]
        self.assertIs(governance["enabled"], True)
        self.assertEqual(governance["numerical_certification"]["status"], "PENDING")
        self.assertNotIn("hard_feasibility_constraint", governance)
        self.assertNotIn("model_routes", governance)
        self.assertNotIn("compute_budget", governance)

    def test_template_governance_blocks_pending_and_allows_certified_execution(self) -> None:
        template_config = json.loads(
            (run_case_module.ROOT / "cases" / "case_template" / "case_config.json").read_text(
                encoding="utf-8"
            )
        )
        template_governance = template_config["competition_governance"]
        with tempfile.TemporaryDirectory(prefix="template_governance_activation_") as temp:
            base = Path(temp)
            runtime_root = base / "runtime"
            self._copy_runtime_root(run_case_module.ROOT, runtime_root)
            original_root = run_case_module.ROOT
            run_case_module.ROOT = runtime_root
            try:
                pending = self._make_solver_case(
                    runtime_root,
                    "template_pending",
                    gate2_pending=False,
                    governance=template_governance,
                )
                authorization = authorize_stage_transition(
                    pending,
                    current_stage="MODEL_IMPLEMENTATION",
                    requested_stage="SOLVER_EXECUTION",
                    mode="competition",
                )
                authorization_path = pending / "authorization.json"
                authorization_path.write_text(json.dumps(authorization), encoding="utf-8")
                with patch("math_core.registry.run_model") as solver:
                    blocked = run_case_module.run_case(
                        "run",
                        pending,
                        30,
                        require_pre_modeling_wiring=False,
                        competition_mode=True,
                        competition_authorization_path=authorization_path,
                        competition_current_stage="MODEL_IMPLEMENTATION",
                        competition_requested_stage="SOLVER_EXECUTION",
                    )
                self.assertEqual(solver.call_count, 0)
                self.assertEqual(blocked["status"], "fatal")
                self.assertEqual(blocked["model_governance"]["reason"], "NUMERICAL_CERTIFICATION_REQUIRED")
                self.assertFalse(blocked["model_governance"]["canonical_result_trusted"])

                certified_governance = {
                    "enabled": True,
                    "numerical_certification": {
                        "status": "PASS",
                        "evidence_path": "output/validation/certification.json",
                    },
                }
                certified = self._make_solver_case(
                    runtime_root,
                    "template_certified",
                    gate2_pending=False,
                    governance=certified_governance,
                )
                evidence = certified / "output" / "validation" / "certification.json"
                evidence.parent.mkdir(parents=True, exist_ok=True)
                evidence.write_text("{}", encoding="utf-8")
                certified_authorization = authorize_stage_transition(
                    certified,
                    current_stage="MODEL_IMPLEMENTATION",
                    requested_stage="SOLVER_EXECUTION",
                    mode="competition",
                )
                certified_auth_path = certified / "authorization.json"
                certified_auth_path.write_text(json.dumps(certified_authorization), encoding="utf-8")
                with patch(
                    "math_core.registry.run_model",
                    return_value={
                        "model_id": "template.test",
                        "model_family": "test",
                        "success": True,
                        "status": "ok",
                        "fatal_count": 0,
                        "warning_count": 0,
                        "primary_results": {"ok": True},
                        "risk_items": [],
                    },
                ) as solver:
                    allowed = run_case_module.run_case(
                        "run",
                        certified,
                        30,
                        require_pre_modeling_wiring=False,
                        competition_mode=True,
                        competition_authorization_path=certified_auth_path,
                        competition_current_stage="MODEL_IMPLEMENTATION",
                        competition_requested_stage="SOLVER_EXECUTION",
                    )
                self.assertEqual(solver.call_count, 1)
                self.assertEqual(allowed["model_governance"]["status"], "PASS")
                self.assertTrue(allowed["model_governance"]["formal_optimization_allowed"])
                self.assertTrue(allowed["model_governance"]["canonical_result_trusted"])
            finally:
                run_case_module.ROOT = original_root

    def test_invalid_contract_fails_closed_before_router_outputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pre_modeling_contract_") as temp:
            case_dir = Path(temp) / "invalid"
            case_dir.mkdir()
            with self.assertRaises(ProblemContractBlocked):
                build_output(case_dir)
            self.assertFalse((case_dir / "routing").exists())
            self.assertFalse((case_dir / "handoff").exists())

    def test_existing_chain_stops_at_route_executor_before_gate2(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pre_modeling_handoff_") as temp:
            base = Path(temp)
            case_dir = make_case(base)
            run_dir = base / "run"
            run_dir.mkdir()
            trace, handoff = _run_pre_modeling_production_wiring(case_dir, run_dir)
            route_handoff_text = (case_dir / "handoff" / "MODEL_ROUTE_GENERATION_HANDOFF.md").read_text(
                encoding="utf-8"
            )
            self.assertEqual(trace["status"], "AWAITING_EXECUTOR")
            self.assertTrue(trace["stop_before_downstream"])
            self.assertIsNone(handoff)
            self.assertIn("`EXECUTOR`: `VSCODE_MODEL_ROUTE_EXECUTOR`", route_handoff_text)
            self.assertIn("`HUMAN_GATE_2`: `PENDING_MANUAL_REVIEW`", route_handoff_text)
            self.assertFalse((case_dir / "handoff" / "MODEL_BUILDING_HANDOFF.md").exists())

    def test_existing_contract_with_pending_gate1_stops_before_router(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pre_modeling_pending_gate1_") as temp:
            base = Path(temp)
            case_dir = make_case(base, case_id="pending_gate1")
            template = run_case_module.ROOT / "cases" / "case_template" / "human_review" / GATE_FILES[1]
            shutil.copy2(template, case_dir / "human_review" / GATE_FILES[1])
            run_dir = base / "run"
            run_dir.mkdir()

            trace, handoff = _run_pre_modeling_production_wiring(case_dir, run_dir)

            self.assertEqual(trace["status"], "WAITING_FOR_GATE1")
            self.assertTrue(trace["stop_before_downstream"])
            self.assertEqual(trace["problem_contract_candidate_ref"], "problem/problem_contract.md")
            self.assertEqual(trace["router_runtime"], "NOT_RUN")
            self.assertIsNone(handoff)
            self.assertFalse((case_dir / "routing").exists())
            self.assertFalse((case_dir / "handoff").exists())

    def test_selected_card_body_is_in_handoff_and_solver_context(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pre_modeling_card_") as temp:
            base = Path(temp)
            case_dir = make_case(base, case_id="card_case")
            result = build_output(case_dir)
            context = build_support_chain_context(
                problem_type="optimization_planning",
                model_route="optimization_planning",
            )
            cards = context["model_building_context"]["cards"]
            self.assertTrue(cards)
            result["support_chain_context"] = context
            result["formal_input_binding"]["knowledge_support"] = {
                "status": "CONSUMED",
                "support_chain_source": context["support_chain_source"],
                "selected_card_paths": context["textbook_concept_support"],
                "loaded_card_ids": context["consumption_evidence"]["loaded_card_ids"],
            }
            write_case_outputs(case_dir, result)
            write_model_building_handoff(case_dir, result)
            handoff = validate_model_building_handoff(case_dir, result)
            self.assertEqual(handoff["knowledge_card_status"], "CONSUMED")
            self.assertEqual(len(handoff["relevant_card_content"]), len(cards))

            captured: dict[str, object] = {}

            def fake_run_model(config: dict, **_: object) -> dict:
                captured.update(config)
                return {
                    "model_id": "test.model",
                    "model_family": "test",
                    "success": True,
                    "status": "PASS",
                    "fatal_count": 0,
                    "warning_count": 0,
                    "primary_results": {"ok": True},
                    "risk_items": [],
                }

            with patch("math_core.registry.run_model", side_effect=fake_run_model):
                _write_math_core_summary(base / "solver_run", case_dir, {}, handoff)
            self.assertEqual(captured["model_building_handoff"], handoff)
            self.assertTrue(captured["model_building_handoff"]["relevant_card_content"])

    def test_knowledge_card_body_loading_error_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pre_modeling_card_error_") as temp:
            case_dir = make_case(Path(temp), case_id="card_error_case")
            result = build_output(case_dir)
            result["support_chain_context"] = {
                "model_building_context": {"status": "KNOWLEDGE_CARD_BODY_UNREADABLE", "cards": []},
                "consumption_evidence": {
                    "status": "KNOWLEDGE_CARD_BODY_UNREADABLE",
                    "retrieved_card_ids": ["missing_card"],
                    "loaded_card_ids": [],
                    "missing_cards": [],
                    "unreadable_cards": [
                        {
                            "card_id": "missing_card",
                            "path": "pipeline_core/knowledge_cards/missing_card.md",
                            "status": "KNOWLEDGE_CARD_BODY_UNREADABLE",
                        }
                    ],
                },
            }
            result["formal_input_binding"]["knowledge_support"] = {
                "status": "KNOWLEDGE_CARD_BODY_UNREADABLE",
                "selected_card_paths": [
                    "pipeline_core/knowledge_cards/missing_card.md"
                ],
                "loaded_card_ids": [],
            }
            write_case_outputs(case_dir, result)
            write_model_building_handoff(case_dir, result)
            with self.assertRaisesRegex(ValueError, "KNOWLEDGE_CARD_STATUS_INVALID"):
                validate_model_building_handoff(case_dir, result)

    def test_direct_solver_authorization_boundary_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="direct_solver_auth_") as temp:
            case_dir = Path(temp) / "case"
            case_dir.mkdir()
            missing = _verify_competition_solver_authorization(
                case_dir,
                competition_mode=True,
                authorization_path=None,
                current_stage="MODEL_IMPLEMENTATION",
                requested_stage="SOLVER_EXECUTION",
            )
            self.assertEqual(missing["status"], "FAIL")
            self.assertFalse(missing["authorization_verified"])

            authorization = case_dir / "authorization.json"
            authorization.write_text("{}", encoding="utf-8")
            with patch(
                "run_case.verify_authorized_stage_transition_for_case",
                return_value={"status": "FAIL", "reason": "AUTHORIZATION_INVALID"},
            ) as verifier:
                invalid = _verify_competition_solver_authorization(
                    case_dir,
                    competition_mode=True,
                    authorization_path=authorization,
                    current_stage="MODEL_IMPLEMENTATION",
                    requested_stage="SOLVER_EXECUTION",
                )
            self.assertEqual(invalid["status"], "FAIL")
            self.assertFalse(invalid["authorization_verified"])
            verifier.assert_called_once()

            with patch(
                "run_case.verify_authorized_stage_transition_for_case",
                return_value={"status": "PASS", "reason": "AUTHORIZED_STAGE_TRANSITION_VALID"},
            ) as verifier:
                valid = _verify_competition_solver_authorization(
                    case_dir,
                    competition_mode=True,
                    authorization_path=authorization,
                    current_stage="MODEL_IMPLEMENTATION",
                    requested_stage="SOLVER_EXECUTION",
                )
            self.assertEqual(valid["status"], "PASS")
            self.assertTrue(valid["authorization_verified"])
            verifier.assert_called_once()

            non_competition = _verify_competition_solver_authorization(
                case_dir,
                competition_mode=False,
                authorization_path=None,
                current_stage=None,
                requested_stage=None,
            )
            self.assertEqual(non_competition["status"], "NOT_REQUIRED")


if __name__ == "__main__":
    unittest.main()
