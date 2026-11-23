"""Regression coverage for the public model-stage Gate 1 boundary."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pipeline_core.python_tools.run_pipeline import render_cli_json, run_stage
from pipeline_core.tests.test_human_gate_decision_semantics import (
    GATE_FILES,
    TEMPLATE_ROOT,
    write_gate_case,
)


ROOT = Path(__file__).resolve().parents[2]
RUN_PIPELINE = ROOT / "pipeline_core" / "python_tools" / "run_pipeline.py"


class RunPipelineModelGateTests(unittest.TestCase):
    def test_cli_json_is_codepage_independent_and_round_trips_chinese(self) -> None:
        payload = {"status": "AWAITING_GATE2", "route_names": ["路线 A：解析几何"]}

        rendered = render_cli_json(payload)

        self.assertTrue(rendered.isascii())
        self.assertEqual(json.loads(rendered), payload)

    def test_model_stage_blocks_pending_gate_and_allows_manual_handoff_after_pass(self) -> None:
        with tempfile.TemporaryDirectory(prefix="run_pipeline_model_gate_") as temp:
            base = Path(temp)
            pending = write_gate_case(base / "pending", 1, 0)
            shutil.copy2(TEMPLATE_ROOT / GATE_FILES[1], pending / "human_review" / GATE_FILES[1])
            blocked = run_stage(pending, "model")
            self.assertEqual(blocked["status"], "BLOCKED")
            self.assertEqual(blocked["reason"], "REQUIRED_HUMAN_GATE_NOT_PASS")

            approved = write_gate_case(base / "approved", 1, 0)
            (approved / "problem").mkdir(parents=True, exist_ok=True)
            (approved / "problem" / "problem_contract.md").write_text(
                "# Problem Contract\n\nScope: bounded geometry test.\n",
                encoding="utf-8",
            )
            (approved / "problem_analysis.md").write_text(
                "# 问题分析\n\n纯方位无源定位，依据方向夹角调整圆形编队。\n",
                encoding="utf-8",
            )
            config = json.loads((approved / "case_config.json").read_text(encoding="utf-8"))
            config["problem_contract_path"] = "problem/problem_contract.md"
            (approved / "case_config.json").write_text(
                json.dumps(config, ensure_ascii=False),
                encoding="utf-8",
            )
            allowed = run_stage(approved, "model")
            self.assertEqual(allowed["status"], "AWAITING_EXECUTOR")
            self.assertEqual(allowed["gate1_status"], "PASS")
            self.assertEqual(
                allowed["model_route_stage"]["handoff"]["path"],
                "handoff/MODEL_ROUTE_GENERATION_HANDOFF.md",
            )

    def test_direct_cli_runs_from_repo_root_and_returns_blocking_exit_code(self) -> None:
        with tempfile.TemporaryDirectory(prefix="run_pipeline_cli_gate_") as temp:
            case = write_gate_case(Path(temp) / "pending", 1, 0)
            shutil.copy2(TEMPLATE_ROOT / GATE_FILES[1], case / "human_review" / GATE_FILES[1])
            completed = subprocess.run(
                [sys.executable, str(RUN_PIPELINE), "--case", str(case), "--stage", "model"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(payload["status"], "BLOCKED")
            self.assertEqual(payload["required_gate"], "GATE1")

    def test_unwired_stage_is_not_reported_as_completed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="run_pipeline_unwired_stage_") as temp:
            case = write_gate_case(Path(temp) / "case", 1, 0)
            result = run_stage(case, "literature")
            self.assertEqual(result["status"], "AWAITING_EXECUTOR")
            self.assertEqual(result["reason"], "STAGE_EXECUTOR_NOT_WIRED")
            self.assertFalse(result["stage_completed"])
            self.assertTrue(result["stop_before_downstream"])

            completed = subprocess.run(
                [sys.executable, str(RUN_PIPELINE), "--case", str(case), "--stage", "literature"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)
            self.assertEqual(completed.returncode, 3)
            self.assertFalse(payload["stage_completed"])


if __name__ == "__main__":
    unittest.main()
