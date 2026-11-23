"""Minimal opt-in competition model governance guard tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipeline_core.python_tools.competition_model_governance import (
    evaluate_model_execution_governance,
    evaluate_proxy_cost,
    evaluate_route_activation,
)


def contract(*, certification_status: str = "PASS") -> dict:
    return {
        "competition_governance": {
            "enabled": True,
            "numerical_certification": {
                "status": certification_status,
                "evidence_path": "output/validation/certification.json",
            },
        }
    }


def write_certification_evidence(case: Path) -> None:
    evidence = case / "output" / "validation" / "certification.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text("{}", encoding="utf-8")


class CompetitionModelGovernanceTests(unittest.TestCase):
    def test_a1_certification_pass_allows_formal_optimization(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = Path(temp)
            evidence = case / "output" / "validation" / "certification.json"
            evidence.parent.mkdir(parents=True)
            evidence.write_text("{}", encoding="utf-8")
            result = evaluate_model_execution_governance(
                case,
                contract(),
                {"formal_optimization": True, "canonical_result_trusted": True},
            )
            self.assertEqual(result["status"], "PASS")
            self.assertTrue(result["formal_optimization_allowed"])

    def test_a2_certification_pending_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = evaluate_model_execution_governance(
                Path(temp),
                contract(certification_status="PENDING"),
                {"formal_optimization": True},
            )
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(result["reason"], "NUMERICAL_CERTIFICATION_REQUIRED")
            self.assertFalse(result["canonical_result_trusted"])

    def test_a3_certification_pending_blocks_without_executor_intent_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = evaluate_model_execution_governance(
                Path(temp),
                contract(certification_status="PENDING"),
                {},
            )
            self.assertEqual(result["status"], "BLOCKED")
            self.assertFalse(result["formal_optimization_allowed"])
            self.assertFalse(result["canonical_result_trusted"])

    def test_b1_hard_feasibility_blocks_objective_without_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = contract()
            config["hard_feasibility_constraint"] = True
            config["optimization_order"] = "FEASIBILITY_THEN_OBJECTIVE"
            result = evaluate_model_execution_governance(
                Path(temp),
                config,
                {"OBJECTIVE_OPTIMIZATION_STATUS": "FINAL", "FEASIBILITY_STATUS": "INFEASIBLE", "FEASIBLE_BASELINE_FOUND": "NO"},
            )
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(result["reason"], "FEASIBLE_BASELINE_REQUIRED_BEFORE_OBJECTIVE")

    def test_b2_feasible_baseline_allows_objective(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = contract()
            config["hard_feasibility_constraint"] = {"enabled": True, "target": "case-defined"}
            config["optimization_order"] = "FEASIBILITY_THEN_OBJECTIVE"
            write_certification_evidence(Path(temp))
            result = evaluate_model_execution_governance(
                Path(temp),
                config,
                {"OBJECTIVE_OPTIMIZATION_STATUS": "RUNNING", "FEASIBILITY_STATUS": "FEASIBLE", "FEASIBLE_BASELINE_FOUND": "YES"},
            )
            self.assertEqual(result["status"], "PASS")

    def test_b3_unknown_objective_status_cannot_bypass_feasibility(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = contract()
            config["hard_feasibility_constraint"] = True
            config["optimization_order"] = "FEASIBILITY_THEN_OBJECTIVE"
            result = evaluate_model_execution_governance(
                Path(temp),
                config,
                {"OBJECTIVE_OPTIMIZATION_STATUS": "DONE"},
            )
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(result["reason"], "FEASIBLE_BASELINE_REQUIRED_BEFORE_OBJECTIVE")

    def test_b4_invalid_hard_constraint_declaration_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = contract()
            config["hard_feasibility_constraint"] = "a descriptive but unsupported declaration"
            result = evaluate_model_execution_governance(Path(temp), config, {})
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(result["reason"], "HARD_FEASIBILITY_DECLARATION_INVALID")

    def test_c1_cheap_proxy_allows_continuation(self) -> None:
        config = {"compute_budget": {"proxy_max_fraction_of_full": 0.10}}
        result = evaluate_proxy_cost(
            config,
            proxy_runtime_seconds=5,
            full_estimate_seconds=100,
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["PROXY_TOO_EXPENSIVE"], "NO")

    def test_c2_expensive_proxy_blocks_batch_continuation(self) -> None:
        config = {"compute_budget": {"proxy_max_fraction_of_full": 0.10}}
        result = evaluate_proxy_cost(
            config,
            proxy_runtime_seconds=11,
            full_estimate_seconds=100,
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "PROXY_TOO_EXPENSIVE")

    def test_d1_rejected_route_cannot_reactivate(self) -> None:
        result = evaluate_route_activation(
            {"route_id": "route-a", "status": "REJECTED_WITH_EVIDENCE", "evidence": "review.md"}
        )
        self.assertEqual(result["status"], "BLOCKED")

    def test_d2_executor_override_cannot_reactivate_rejected_route(self) -> None:
        result = evaluate_route_activation(
            {"route_id": "route-a", "status": "REJECTED_WITH_EVIDENCE", "evidence": "review.md"},
            explicit_override=True,
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "ROUTE_OVERRIDE_UNSUPPORTED")

    def test_d3_route_ledger_is_case_insensitive_and_rejects_unknown_route(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = contract()
            write_certification_evidence(Path(temp))
            config["competition_governance"]["model_routes"] = [
                {"route_id": "Route-A", "status": "ACTIVE"}
            ]
            allowed = evaluate_model_execution_governance(Path(temp), config, {"route_id": " route-a "})
            self.assertEqual(allowed["status"], "PASS")
            unknown = evaluate_model_execution_governance(Path(temp), config, {"route_id": "route-b"})
            self.assertEqual(unknown["status"], "BLOCKED")
            self.assertEqual(unknown["reason"], "ROUTE_NOT_IN_LEDGER")

    def test_d4_route_ledger_requires_registered_route_and_valid_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            missing = contract()
            missing["competition_governance"]["model_routes"] = [
                {"route_id": "route-a", "status": "ACTIVE"}
            ]
            result = evaluate_model_execution_governance(Path(temp), missing, {})
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(result["reason"], "ROUTE_ID_REQUIRED")
            malformed = contract()
            malformed["competition_governance"]["model_routes"] = "route-a"
            result = evaluate_model_execution_governance(Path(temp), malformed, {"route_id": "route-a"})
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(result["reason"], "MODEL_ROUTES_DECLARATION_INVALID")

    def test_d5_model_config_override_cannot_reopen_rejected_route(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = contract()
            write_certification_evidence(Path(temp))
            config["competition_governance"]["model_routes"] = [
                {"route_id": "route-a", "status": "REJECTED_WITH_EVIDENCE", "evidence": "review.md"}
            ]
            result = evaluate_model_execution_governance(
                Path(temp),
                config,
                {"route_id": "route-a", "route_override": True},
            )
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(result["reason"], "REJECTED_ROUTE_REACTIVATION_BLOCKED")

    def test_legacy_manifest_without_governance_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = Path(temp)
            (case / "case_config.json").write_text(json.dumps({"case_id": "legacy"}), encoding="utf-8")
            result = evaluate_model_execution_governance(case, {"case_id": "legacy"}, {})
            self.assertEqual(result["status"], "PASS")
            self.assertTrue(result["legacy_compatible"])

    def test_legacy_model_routes_metadata_does_not_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            case = Path(temp)
            config = {"case_id": "legacy", "model_routes": "generated"}
            result = evaluate_model_execution_governance(case, config, {})
            self.assertEqual(result["status"], "PASS")
            self.assertTrue(result["legacy_compatible"])


if __name__ == "__main__":
    unittest.main()
