from __future__ import annotations

import unittest

from pipeline_core.paper_quality.writer_schema import WriterSchemaError, resolve_task_contract


class WriterSchemaTests(unittest.TestCase):
    def test_legacy_three_four_and_five_question_manifests_remain_compatible(self) -> None:
        for count in (3, 4, 5):
            with self.subTest(question_count=count):
                contract = resolve_task_contract({"question_count": count})
                self.assertEqual(contract["migration_mode"], "LEGACY_QUESTION_COUNT_FALLBACK")
                self.assertEqual(len(contract["tasks"]), count)
                self.assertEqual(contract["tasks"][0]["label"], "问题一")
                self.assertEqual(contract["tasks"][-1]["content_key"], f"question_content_q{count}")

    def test_named_and_non_numbered_tasks_are_explicit(self) -> None:
        contract = resolve_task_contract(
            {
                "task_contract": {
                    "tasks": [
                        {
                            "task_id": "layout",
                            "label": "空间配置",
                            "analysis_content_key": "problem_analysis_layout",
                            "content_key": "question_content_layout",
                        },
                        {
                            "task_id": "extension",
                            "label": "附加任务：稳健性分析",
                            "analysis_content_key": "problem_analysis_extension",
                            "content_key": "question_content_extension",
                        },
                    ]
                }
            }
        )
        self.assertEqual(contract["migration_mode"], "EXPLICIT_TASK_CONTRACT")
        self.assertEqual(contract["tasks"][1]["label"], "附加任务：稳健性分析")

    def test_duplicate_or_mismatched_contract_is_rejected(self) -> None:
        with self.assertRaisesRegex(WriterSchemaError, "TASK_CONTRACT_QUESTION_COUNT_MISMATCH"):
            resolve_task_contract(
                {
                    "question_count": 3,
                    "task_contract": {
                        "tasks": [
                            {
                                "task_id": "only",
                                "label": "唯一任务",
                                "analysis_content_key": "problem_analysis_only",
                                "content_key": "question_content_only",
                            }
                        ]
                    },
                }
            )


if __name__ == "__main__":
    unittest.main()
