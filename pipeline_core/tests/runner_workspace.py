"""Run integration tests against the real runner in a temporary project."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class RunnerWorkspaceTestCase(unittest.TestCase):
    root: Path

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.root = Path(cls.enterClassContext(tempfile.TemporaryDirectory(prefix="model_runner_")))
        source = Path(__file__).resolve().parents[2]
        for script in source.glob("*.py"):
            shutil.copy2(script, cls.root / script.name)
        for package in ("math_core", "pipeline_core", "quality_gates", "review", "templates", "validation"):
            shutil.copytree(
                source / package,
                cls.root / package,
                ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "work_tmp"),
            )
        for folder in ("archive", "cases"):
            directory = cls.root / folder
            directory.mkdir()
            (directory / "preserve.txt").write_text("fixture\n", encoding="utf-8")

    def run_case(self, stage: str, case_dir: Path, timeout_seconds: int) -> dict:
        result = subprocess.run(
            [
                sys.executable, "run_case.py",
                "--stage", stage,
                "--case-dir", str(case_dir),
                "--timeout-seconds", str(timeout_seconds),
            ],
            cwd=self.root,
            env={**os.environ, "PYTHONUTF8": "1"},
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertIn(result.returncode, (0, 2), result.stderr or result.stdout)
        self.assertTrue(result.stdout.strip(), result.stderr)
        return json.loads(result.stdout)
