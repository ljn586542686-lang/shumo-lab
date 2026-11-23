from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pipeline_core.python_tools.human_gate import evaluate_human_gate


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class GateAuthorityTests(unittest.TestCase):
    def _case(self, root: Path) -> Path:
        case = root / "case"
        review = case / "human_review"
        review.mkdir(parents=True)
        gate = review / "gate_3_result_acceptance.md"
        gate.write_text("# Gate 3 evidence\n", encoding="utf-8")
        (case / "case_config.json").write_text(
            json.dumps({"case_id": "authority_fixture", "gate_authority_required": True}), encoding="utf-8"
        )
        contract = {
            "result_cards": [{
                "result_id": "r1", "display_value": "1", "unit": "m", "source_binding": "output/a.json#x",
                "paper_location": "question_content_q1", "scenario": {
                    "result_source": "output/a.json", "e": "0.8", "duration": "0.1 s", "scenario_id": "s1",
                    "player_combination": "1,2", "direction_angle": "0 deg", "mirror": "0",
                },
            }],
            "answer_cards": [{
                "task_id": "q1", "question_requirement": "输出结果", "paper_output": "结果为1m",
                "representative_value": "1 m", "result_card_ids": ["r1"],
            }],
            "limitation_impacts": [{
                "limitation_id": "l1", "assumption_or_boundary": "理想条件", "affected_quantities": "速度",
                "conditional_results": "仅作为条件结果",
            }],
        }
        authority = {
            "schema_version": "human_gate_authority_v1",
            "case_id": "authority_fixture",
            "gates": {"GATE3": {
                "status": "APPROVED", "confirmed_by": "Human", "confirmed_at": "2026-08-17T10:00:00+08:00",
                "evidence_path": "human_review/gate_3_result_acceptance.md", "evidence_sha256": sha256(gate),
                "semantic_acceptance_contract": contract,
            }},
        }
        (review / "gate_authority.json").write_text(json.dumps(authority), encoding="utf-8")
        return case

    def test_authority_is_single_gate_source_and_requires_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self._case(Path(directory))
            result = evaluate_human_gate(case, "GATE3")
            self.assertEqual(result["gate_status"], "PASS")
            self.assertEqual(result["decision"], "APPROVED_BY_GATE_AUTHORITY")

    def test_changed_evidence_or_missing_answer_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = self._case(Path(directory))
            gate = case / "human_review" / "gate_3_result_acceptance.md"
            gate.write_text("changed\n", encoding="utf-8")
            self.assertIn("GATE_AUTHORITY_EVIDENCE_HASH_MISMATCH", evaluate_human_gate(case, "GATE3")["reasons"])


if __name__ == "__main__":
    unittest.main()
