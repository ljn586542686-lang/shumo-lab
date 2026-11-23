"""不调用真实 LP backend 的入口与字典序编排测试。"""

from __future__ import annotations

import ast
import json
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch


CODE_DIR = Path(__file__).resolve().parents[1]
CASE_ROOT = CODE_DIR.parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import run_pipeline  # noqa: E402
from formal_q1_provider import FormalQ1Input  # noqa: E402
from model_contract import PhysicalInputs  # noqa: E402
from q1_model_solver import build_q1_model  # noqa: E402
from solver import BackendSolveEvidence, run_lexicographic_q1  # noqa: E402


def build_feasible_model():
    return build_q1_model((100.0,) * 144, (0.0,) * 144, (1.0,) * 144)


def feasible_solution(model):
    values = [0.0] * model.layout.size
    for k in range(model.grid.n_intervals):
        values[model.layout.x(k)] = 100.0
        values[model.layout.energy(k)] = 6_000.0
    return tuple(values)


class ScriptedBackend:
    name = "fake.mock_lp_backend"
    constraint_violation_tolerance = 1e-7

    def __init__(self, accepted=(True, True, True)):
        self.accepted = list(accepted)
        self.calls = []

    def solve(self, *, objective, equalities, bounds, run_id, case_dir):
        model = build_feasible_model()
        solution = feasible_solution(model)
        call = {
            "objective": tuple(objective),
            "equalities": tuple(equalities),
            "bounds": tuple(bounds),
            "run_id": run_id,
            "case_dir": case_dir,
        }
        self.calls.append(call)
        accepted = self.accepted[len(self.calls) - 1]
        objective_value = sum(a * b for a, b in zip(objective, solution)) if accepted else None
        return BackendSolveEvidence(
            accepted=accepted,
            status="solver_reported_optimum" if accepted else "infeasible",
            objective_value=objective_value,
            solution=solution if accepted else None,
            constraint_violation=0.0 if accepted else None,
            iterations=7 if accepted else 0,
            message="mock only",
            failure_reason=None if accepted else "infeasible",
        )


class RaisingBackend(ScriptedBackend):
    def solve(self, **kwargs):
        self.calls.append(kwargs)
        raise RuntimeError("mock backend failure")


class UnknownStatusBackend(ScriptedBackend):
    def solve(self, **kwargs):
        evidence = super().solve(**kwargs)
        return BackendSolveEvidence(
            accepted=True,
            status="unknown",
            objective_value=evidence.objective_value,
            solution=evidence.solution,
            constraint_violation=evidence.constraint_violation,
            iterations=evidence.iterations,
            message=evidence.message,
        )


class TestEntrypointContract(unittest.TestCase):
    def test_entrypoint_exists(self):
        config = json.loads((CASE_ROOT / "case_config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["entry_points"]["python"], "code/python/run_pipeline.py")
        self.assertTrue((CASE_ROOT / config["entry_points"]["python"]).is_file())

    def test_entrypoint_importable(self):
        self.assertTrue(callable(run_pipeline.main))
        self.assertTrue(callable(run_pipeline.dispatch_execution))

    def test_entrypoint_fail_closed(self):
        with self.assertRaises(SystemExit) as caught:
            run_pipeline.main([])
        self.assertEqual(caught.exception.code, 2)

    def test_entrypoint_call_graph(self):
        tree = ast.parse((CODE_DIR / "run_pipeline.py").read_text(encoding="utf-8"))
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("build_q1_model", calls)
        self.assertIn("run_lexicographic_q1", calls)

    def test_authorization_failure_stops_before_backend(self):
        with patch.object(
            run_pipeline,
            "verify_execution_authorization",
            return_value={"status": "FAIL", "reason": "TEST_AUTHORIZATION_REJECTED"},
        ), patch.object(run_pipeline, "MathCoreLinearProgramBackend") as backend_factory, redirect_stdout(StringIO()):
            code = run_pipeline.main(
                ["--mode", "synthetic", "--authorization", "missing.json", "--fixture", "TEST_A_FLAT_PRICE_NO_PV"]
            )
        self.assertEqual(code, 2)
        backend_factory.assert_not_called()

    def test_formal_call_graph_uses_provider_model_solver_validation_and_writer(self):
        backend = ScriptedBackend()
        formal = FormalQ1Input(
            physical_inputs=PhysicalInputs.from_sequences(
                (100.0,) * 144,
                (0.0,) * 144,
                (1.0,) * 144,
            ),
            source_sha256="test",
            sheet_name="Sheet1",
            data_positions=144,
            first_position_raw=("first",),
            last_position_raw=("last",),
        )
        model = build_feasible_model()
        solved = {
            "status": "PASS",
            "scientific_validation": {"status": "PASS"},
            "final_solution": {},
        }
        with patch.object(run_pipeline, "load_formal_q1_inputs", return_value=formal) as provider, patch.object(
            run_pipeline,
            "build_q1_model",
            return_value=model,
        ) as builder, patch.object(
            run_pipeline,
            "run_lexicographic_q1",
            return_value=solved,
        ) as solver_call, patch.object(
            run_pipeline,
            "write_result1_from_q1",
            return_value={"status": "PASS"},
        ) as writer:
            result = run_pipeline.dispatch_execution(mode="formal", fixture_name=None, backend=backend)
        self.assertEqual(result["status"], "PASS")
        provider.assert_called_once_with(run_pipeline.FORMAL_ATTACHMENT1)
        builder.assert_called_once()
        solver_call.assert_called_once()
        writer.assert_called_once()
        self.assertEqual(backend.calls, [])


class TestLexicographicOrchestration(unittest.TestCase):
    def test_lexicographic_order(self):
        model = build_feasible_model()
        backend = ScriptedBackend()
        result = run_lexicographic_q1(model, backend=backend)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(backend.calls), 3)
        self.assertEqual(len(backend.calls[0]["equalities"]), len(model.equalities))
        self.assertEqual(backend.calls[1]["equalities"][-1].name, "lock_level_1")
        self.assertEqual(
            [row.name for row in backend.calls[2]["equalities"][-2:]],
            ["lock_level_1", "lock_level_2"],
        )

    def test_no_epsilon_objective(self):
        model = build_feasible_model()
        backend = ScriptedBackend()
        run_lexicographic_q1(model, backend=backend)
        self.assertEqual(
            [call["objective"] for call in backend.calls],
            [level.coefficients for level in model.objective_levels],
        )

    def test_level1_failure_stops(self):
        backend = ScriptedBackend((False,))
        result = run_lexicographic_q1(build_feasible_model(), backend=backend)
        self.assertEqual(result["reason"], "LEVEL1_SOLVER_STATUS_NOT_ACCEPTED")
        self.assertEqual(len(backend.calls), 1)

    def test_level2_failure_stops(self):
        backend = ScriptedBackend((True, False))
        result = run_lexicographic_q1(build_feasible_model(), backend=backend)
        self.assertEqual(result["reason"], "LEVEL2_SOLVER_STATUS_NOT_ACCEPTED")
        self.assertEqual(len(backend.calls), 2)

    def test_level3_failure_stops(self):
        backend = ScriptedBackend((True, True, False))
        result = run_lexicographic_q1(build_feasible_model(), backend=backend)
        self.assertEqual(result["reason"], "LEVEL3_SOLVER_STATUS_NOT_ACCEPTED")
        self.assertEqual(len(backend.calls), 3)

    def test_backend_exception_stops(self):
        backend = RaisingBackend()
        result = run_lexicographic_q1(build_feasible_model(), backend=backend)
        self.assertEqual(result["reason"], "LEVEL1_BACKEND_ERROR")
        self.assertEqual(len(backend.calls), 1)

    def test_unknown_status_stops(self):
        backend = UnknownStatusBackend()
        result = run_lexicographic_q1(build_feasible_model(), backend=backend)
        self.assertEqual(result["reason"], "LEVEL1_SOLVER_STATUS_NOT_ACCEPTED")
        self.assertEqual(len(backend.calls), 1)

    def test_objective_lock_binding(self):
        model = build_feasible_model()
        backend = ScriptedBackend()
        result = run_lexicographic_q1(model, backend=backend)
        z1, z2, _ = result["objective_optima"]
        lock1 = backend.calls[1]["equalities"][-1]
        lock2 = backend.calls[2]["equalities"][-1]
        self.assertEqual(
            lock1.coefficients,
            tuple(
                (index, value)
                for index, value in enumerate(model.objective_levels[0].coefficients)
                if value != 0.0
            ),
        )
        self.assertEqual(lock1.rhs, z1)
        self.assertEqual(
            lock2.coefficients,
            tuple(
                (index, value)
                for index, value in enumerate(model.objective_levels[1].coefficients)
                if value != 0.0
            ),
        )
        self.assertEqual(lock2.rhs, z2)

    def test_final_primary_objective_preserved(self):
        result = run_lexicographic_q1(build_feasible_model(), backend=ScriptedBackend())
        self.assertEqual(result["status"], "PASS")
        self.assertLessEqual(result["final_primary_objective_delta"], 1e-7)
        self.assertEqual(result["scientific_validation"]["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
