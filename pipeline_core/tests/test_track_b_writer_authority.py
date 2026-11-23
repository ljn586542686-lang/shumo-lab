from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pipeline_core.paper_quality.model_to_paper_contract import (
    ModelToPaperContractBlocked,
    _validate_literature_stage,
    _validate_track_b_writer_authority,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TrackBWriterAuthorityTests(unittest.TestCase):
    def test_selection_writer_requires_bound_track_b_review_and_authorization(self) -> None:
        with tempfile.TemporaryDirectory(prefix="track_b_writer_") as temp:
            case_dir = Path(temp)
            review = case_dir / "paper" / "draft_v1" / "review" / "00_TRACK_B_REVIEW.md"
            authorization = case_dir / "handoff" / "TRACK_B_TO_TRACK_C.md"
            review.parent.mkdir(parents=True)
            authorization.parent.mkdir(parents=True)
            review.write_text("TRACK_B_REVIEW_VERDICT = PASS_WITH_LIMITATIONS\n", encoding="utf-8")
            authorization.write_text(
                "TRACK_C_STATUS = AUTHORIZED\nTRACK_C_EXECUTION_AUTHORIZATION = YES\n",
                encoding="utf-8",
            )
            manifest = case_dir / "paper" / "writer_manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "supporting_source_bindings": [
                            {
                                "path": review.relative_to(case_dir).as_posix(),
                                "sha256": sha256(review),
                            },
                            {
                                "path": authorization.relative_to(case_dir).as_posix(),
                                "sha256": sha256(authorization),
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            _validate_track_b_writer_authority(
                case_dir, manifest, {"submission_tier": "selection"}
            )

            manifest.write_text(json.dumps({"supporting_source_bindings": []}), encoding="utf-8")
            with self.assertRaises(ModelToPaperContractBlocked) as context:
                _validate_track_b_writer_authority(
                    case_dir, manifest, {"submission_tier": "selection"}
                )
            self.assertEqual(context.exception.result["code"], "TRACK_B_SUPPORTING_BINDING_MISMATCH")

    def test_selection_writer_cannot_silently_skip_literature(self) -> None:
        with self.assertRaises(ModelToPaperContractBlocked) as context:
            _validate_literature_stage(
                {"submission_tier": "selection", "literature_status": "NOT_RUN"}
            )
        self.assertEqual(context.exception.result["code"], "LITERATURE_STAGE_NOT_RUN")

        with self.assertRaises(ModelToPaperContractBlocked) as context:
            _validate_literature_stage(
                {"submission_tier": "selection", "literature_status": "GAP_OPEN"}
            )
        self.assertEqual(context.exception.result["code"], "LITERATURE_GAP_OPEN")

        _validate_literature_stage(
            {"submission_tier": "selection", "literature_status": "VERIFIED"}
        )
        _validate_literature_stage(
            {"submission_tier": "selection", "literature_status": "NOT_REQUIRED"}
        )


if __name__ == "__main__":
    unittest.main()
