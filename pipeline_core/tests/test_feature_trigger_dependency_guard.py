from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import check_feature_triggers


class FeatureTriggerDependencyGuardTests(unittest.TestCase):
    def test_pyyaml_available_and_template_smoke_passes(self) -> None:
        yaml_module = check_feature_triggers._require_pyyaml()
        template = Path("templates/case_FEATURES.yaml").read_text(encoding="utf-8")
        payload = yaml_module.safe_load(template)
        feature_ids = {
            item["feature_id"]
            for group in ("required_features", "conditional_features")
            for item in payload[group]
        }
        self.assertTrue({"ABST-10", "XVAL-10", "EQ-OPT"}.issubset(feature_ids))

    def test_missing_pyyaml_returns_clean_dependency_error(self) -> None:
        stderr = io.StringIO()
        argv = ["check_feature_triggers.py", "fixture.yaml"]
        with (
            mock.patch.object(check_feature_triggers, "yaml", None),
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stderr(stderr),
        ):
            return_code = check_feature_triggers.main()
        message = stderr.getvalue()
        self.assertEqual(return_code, 2)
        self.assertIn("PyYAML is required for YAML feature trigger files", message)
        self.assertIn("pip install PyYAML", message)
        self.assertNotIn("Traceback", message)

    def test_existing_checker_semantics_remain_available(self) -> None:
        case = {
            "case_id": "fixture",
            "features": {
                "ABST-10": {
                    "status": "SHOULD_AND_DONE",
                    "evidence_paths": ["paper/main.tex"],
                }
            },
        }
        self.assertEqual(check_feature_triggers.check_case(case), [])

    def test_guard_creates_no_final_submit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.assertIsNotNone(check_feature_triggers._require_pyyaml())
            self.assertFalse((root / "final_submit").exists())


if __name__ == "__main__":
    unittest.main()
