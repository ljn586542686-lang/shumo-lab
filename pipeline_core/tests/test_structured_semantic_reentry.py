from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from canonical_result_index import build_index
from pipeline_core.python_tools.structured_semantic_reentry import (
    SCHEMA_VERSION,
    validate_structured_semantic_reentry,
)


class StructuredSemanticReentryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.case = self.root / "cases" / "demo"
        (self.case / "output").mkdir(parents=True)
        self.paper = self.root / "paper.tex"
        self._write_artifact(0.25)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_artifact(self, value: float) -> None:
        (self.case / "output" / "result.json").write_text(json.dumps({
            "result_schema_id": "x5_f0_canonical_result_schema_v1",
            "question_id": "Q1", "artifact_role": "primary_result",
            "run_id": "run-1", "status": "PASS", "metrics": {"eta": value},
        }), encoding="utf-8")

    def _write(self, body: str, constraints: list[dict], *, schema: str = "s0_r2a_structured_claim_handoff_v2", claim_extra: dict | None = None) -> dict:
        self.paper.write_text(f"% CLAIM_START:C-1\n{body}\n% CLAIM_END:C-1\n", encoding="utf-8")
        config = {"authoritative_result_artifacts": [{"question_id": "Q1", "artifact_role": "primary_result", "path": "output/result.json"}], "authoritative_required_scopes": [["Q1", "primary_result"]]}
        index = build_index(self.case, config, "run-1")
        index_path = self.root / "index.json"
        index_path.write_text(json.dumps(index), encoding="utf-8")
        claim = {"claim_id": "C-1", "claim_check_status": "PASS", "approved_constraints": constraints, "paper_span_reference": {"claim_id": "C-1"}, "traceability": {"primary_claim_anchor": {"claim_id": "C-1", "role": "PRIMARY_CLAIM_ANCHOR", "anchor_id": "a1"}, "secondary_references": []}}
        if claim_extra:
            claim.update(claim_extra)
        handoff = self.root / "handoff.json"
        handoff.write_text(json.dumps({"schema_version": schema, "status": "PASS", "result_authority": "R1_CANONICAL_INDEX", "claims": [claim]}), encoding="utf-8")
        sidecar = self.root / "reentry.json"
        payload = {"schema_version": SCHEMA_VERSION, "paper_source": {"path": "paper.tex", "sha256": hashlib.sha256(self.paper.read_bytes()).hexdigest()}, "claim_handoff_path": "handoff.json", "result_authority": {"index_path": "index.json", "case_dir": "cases/demo"}}
        sidecar.write_text(json.dumps(payload), encoding="utf-8")
        return validate_structured_semantic_reentry(sidecar, repo_root=self.root, trusted_index_path=index_path, trusted_index_sha256=hashlib.sha256(index_path.read_bytes()).hexdigest(), trusted_handoff_path=handoff, trusted_handoff_sha256=hashlib.sha256(handoff.read_bytes()).hexdigest())

    def test_lower_bound_violation(self):
        self.assertEqual(self._write("eta = -0.17", [{"constraint_type": "LOWER_BOUND", "symbol": "eta", "value": 0}])["verdict"], "SEMANTIC_BLOCK")

    def test_lower_bound_pass_and_zero(self):
        self.assertEqual(self._write("eta = 0.17", [{"constraint_type": "LOWER_BOUND", "symbol": "eta", "value": 0}])["verdict"], "SEMANTIC_PASS")
        self.assertEqual(self._write("eta = 0", [{"constraint_type": "LOWER_BOUND", "symbol": "eta", "value": 0}])["verdict"], "SEMANTIC_PASS")

    def test_negative_epsilon_blocks(self):
        self.assertEqual(self._write("eta = -1e-12", [{"constraint_type": "LOWER_BOUND", "symbol": "eta", "value": 0}])["verdict"], "SEMANTIC_BLOCK")

    def test_upper_and_interval(self):
        self.assertEqual(self._write("eta = 1.2", [{"constraint_type": "UPPER_BOUND", "symbol": "eta", "value": 1}])["verdict"], "SEMANTIC_BLOCK")
        self.assertEqual(self._write("0 <= eta <= 1", [{"constraint_type": "INTERVAL", "symbol": "eta", "lower": 0, "upper": 1}])["verdict"], "SEMANTIC_PASS")

    def test_frozen_uses_canonical_value(self):
        c = {"constraint_type": "FROZEN_VALUE", "symbol": "eta", "canonical_value_reference": {"question_id": "Q1", "artifact_role": "primary_result", "field_reference": "/metrics/eta"}, "comparison_tolerance": {"abs_tolerance": 1e-6}}
        self.assertEqual(self._write("eta = 0.25", [c])["verdict"], "SEMANTIC_PASS")
        self.assertEqual(self._write("eta = 0.26", [c])["verdict"], "SEMANTIC_BLOCK")

    def test_schema_mismatch_blocks(self):
        c = {"constraint_type": "FROZEN_VALUE", "symbol": "eta", "canonical_value_reference": {"question_id": "Q1", "artifact_role": "primary_result", "field_reference": "/metrics/eta", "result_schema_id": "wrong"}, "comparison_tolerance": {"abs_tolerance": 1e-6}}
        self.assertIn("R4B_RESULT_SCHEMA_ID_MISMATCH", self._write("eta = 0.25", [c])["blockers"])

    def test_v2_empty_not_applicable(self):
        self.assertEqual(self._write("free text", [])["verdict"], "NOT_APPLICABLE")

    def test_v1_missing_constraints_review(self):
        self.assertEqual(self._write("free text", [], schema="s0_r2a_structured_claim_handoff_v1", claim_extra={"approved_constraints": None})["verdict"], "REVIEW_REQUIRED")

    def test_marker_and_unparseable_fail_closed(self):
        self.assertEqual(self._write("eta is nonnegative", [{"constraint_type": "LOWER_BOUND", "symbol": "eta", "value": 0}])["verdict"], "REVIEW_REQUIRED")
        self.paper.write_text("no markers", encoding="utf-8")
        result = self._write("eta = -0.17", [{"constraint_type": "LOWER_BOUND", "symbol": "eta", "value": 0}])
        self.assertEqual(result["verdict"], "SEMANTIC_BLOCK")


if __name__ == "__main__":
    unittest.main()
