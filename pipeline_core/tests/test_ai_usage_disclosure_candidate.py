from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipeline_core.python_tools.ai_usage_logger import build_minimum_disclosure_candidate


class AIUsageDisclosureCandidateTests(unittest.TestCase):
    def test_log_becomes_review_only_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "ai_disclosure"
            folder.mkdir()
            (folder / "ai_usage_log.json").write_text(
                json.dumps({"entries": [{"tool": "assistant", "purpose": "整理表达", "scope": "论文草稿"}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            output = build_minimum_disclosure_candidate(root)
            text = output.read_text(encoding="utf-8")
            self.assertIn("候选材料", text)
            self.assertIn("整理表达", text)
            self.assertIn("人工核对", text)


if __name__ == "__main__":
    unittest.main()
