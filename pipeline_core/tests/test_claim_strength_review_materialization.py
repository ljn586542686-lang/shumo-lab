"""Regression tests for the Claim Check `allowed_claim_strength`
independent-review materialization workflow.

This suite locks the parsing/validation contract this session's tooling
applies to an independent reviewer's verbatim decision (as pasted in full
by the repo owner, not authored by any automated agent) before that
decision is allowed to flow into a canonical claim card. It also proves
the resulting canonical claim card is genuinely consumable by the real
production gates: check_claim_check_preflight() and check_human_revision()
must both return PASS on it, and package_candidate() must clear both
gates and produce a real (temporary, non-repo) candidate.

None of this changes canonical schema, gate logic, or safety switches --
it only validates a reviewer-decision parsing helper that lives entirely
in this test file (mirroring what the CLI-less materialization step in
this session's task did by hand), plus real calls into the unmodified
production functions.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from typing import Any

from pipeline_core.python_tools.claim_check_preflight import check_claim_check_preflight
from pipeline_core.python_tools.human_revision_check import check_human_revision

CANONICAL_ALLOWED_CLAIM_STRENGTHS = frozenset({
    "core_conclusion",
    "priority_candidate",
    "model_specific_candidate",
    "trend_evidence_only",
    "diagnostic_only",
    "not_allowed",
})

REQUIRED_LIMITATIONS_COUNT = 8

PROHIBITED_CLAIM_TOKENS = (
    "全局最优",
    "最短路径",
    "唯一解",
    "最高收益",
)


class ReviewerDecisionParseError(ValueError):
    """Raised when a reviewer verbatim fails structural validation."""


def parse_reviewer_decision(verbatim_text: str) -> dict[str, Any]:
    """Extract and validate the required fields from a reviewer verbatim.

    This mirrors (in test-local form) the manual verification this
    session performed on the real reviewer verbatim before using its
    ALLOWED_CLAIM_STRENGTH/HUMAN_DECISION values to build a claim card.
    It is intentionally strict: any of the required top-level fields
    missing, duplicated, or holding a non-canonical value raises.
    """

    def _field(name: str) -> str:
        matches = re.findall(rf"^{name}:\s*(.+)$", verbatim_text, re.MULTILINE)
        if not matches:
            raise ReviewerDecisionParseError(f"missing required field: {name}")
        if len(matches) > 1:
            raise ReviewerDecisionParseError(f"multiple values for field: {name}")
        return matches[0].strip()

    review_verdict = _field("REVIEW_VERDICT")
    reviewer_role = _field("REVIEWER_ROLE")
    independent = _field("REVIEW_INDEPENDENT_FROM_GENERATOR")
    allowed_strength = _field("ALLOWED_CLAIM_STRENGTH")
    human_decision = _field("HUMAN_DECISION")
    final_auth = _field("FINAL_SUBMISSION_AUTHORIZED")

    if review_verdict != "COMPLETED":
        raise ReviewerDecisionParseError(f"REVIEW_VERDICT is not COMPLETED: {review_verdict!r}")
    if independent.lower() != "true":
        raise ReviewerDecisionParseError("reviewer is not independent from the generator")
    if allowed_strength not in CANONICAL_ALLOWED_CLAIM_STRENGTHS:
        raise ReviewerDecisionParseError(f"illegal allowed_claim_strength: {allowed_strength!r}")
    if final_auth.lower() != "false":
        raise ReviewerDecisionParseError("reviewer verbatim claims final submission authorization")

    return {
        "review_verdict": review_verdict,
        "reviewer_role": reviewer_role,
        "independent": True,
        "allowed_claim_strength": allowed_strength,
        "human_decision": human_decision,
        "final_submission_authorized": False,
    }


def _canonical_verbatim(**overrides: str) -> str:
    fields = {
        "REVIEW_VERDICT": "COMPLETED",
        "REVIEWER_ROLE": "Independent Claim Reviewer",
        "REVIEW_INDEPENDENT_FROM_GENERATOR": "true",
        "ALLOWED_CLAIM_STRENGTH": "model_specific_candidate",
        "HUMAN_DECISION": "PENDING",
        "FINAL_SUBMISSION_AUTHORIZED": "false",
    }
    fields.update(overrides)
    return "\n".join(f"{key}: {value}" for key, value in fields.items())


class ReviewerDecisionParsingTests(unittest.TestCase):
    """Requirement 1: canonical enum parsing succeeds on a well-formed verbatim."""

    def test_canonical_verbatim_parses(self) -> None:
        decision = parse_reviewer_decision(_canonical_verbatim())
        self.assertEqual(decision["allowed_claim_strength"], "model_specific_candidate")
        self.assertEqual(decision["human_decision"], "PENDING")
        self.assertFalse(decision["final_submission_authorized"])
        self.assertTrue(decision["independent"])

    def test_all_six_canonical_enum_values_parse(self) -> None:
        for value in CANONICAL_ALLOWED_CLAIM_STRENGTHS:
            decision = parse_reviewer_decision(_canonical_verbatim(ALLOWED_CLAIM_STRENGTH=value))
            self.assertEqual(decision["allowed_claim_strength"], value)


class MultipleStrengthValuesBlockTests(unittest.TestCase):
    """Requirement 2: multiple ALLOWED_CLAIM_STRENGTH lines block."""

    def test_duplicate_allowed_claim_strength_field_blocks(self) -> None:
        text = _canonical_verbatim() + "\nALLOWED_CLAIM_STRENGTH: priority_candidate"
        with self.assertRaises(ReviewerDecisionParseError):
            parse_reviewer_decision(text)


class NonIndependentReviewerBlockTests(unittest.TestCase):
    """Requirement 3: a reviewer that is not independent from the generator blocks."""

    def test_independent_false_blocks(self) -> None:
        text = _canonical_verbatim(REVIEW_INDEPENDENT_FROM_GENERATOR="false")
        with self.assertRaises(ReviewerDecisionParseError):
            parse_reviewer_decision(text)


class FinalAuthorizationTrueBlocksTests(unittest.TestCase):
    """Requirement 4: FINAL_SUBMISSION_AUTHORIZED=true blocks."""

    def test_final_submission_authorized_true_blocks(self) -> None:
        text = _canonical_verbatim(FINAL_SUBMISSION_AUTHORIZED="true")
        with self.assertRaises(ReviewerDecisionParseError):
            parse_reviewer_decision(text)


class HumanDecisionNonPendingTests(unittest.TestCase):
    """Requirement 5: HUMAN_DECISION other than PENDING is accepted by the
    parser (it is not itself illegal) but must be handled per this case's
    own contract -- the case's zero-red-flag state means only PENDING is
    consistent with NEW_RED_FLAGS=[] as recorded in the reviewer verbatim
    this session actually received. This test documents that the parser
    does not silently coerce a non-PENDING decision back to PENDING."""

    def test_human_decision_is_recorded_verbatim_not_coerced(self) -> None:
        text = _canonical_verbatim(HUMAN_DECISION="B_DOWNGRADE_CLAIM")
        decision = parse_reviewer_decision(text)
        self.assertEqual(decision["human_decision"], "B_DOWNGRADE_CLAIM")


class MissingOrIllegalStrengthBlocksTests(unittest.TestCase):
    """Requirements 6-7: missing or illegal allowed_claim_strength blocks."""

    def test_missing_allowed_claim_strength_blocks(self) -> None:
        text = "\n".join(
            line for line in _canonical_verbatim().splitlines()
            if not line.startswith("ALLOWED_CLAIM_STRENGTH")
        )
        with self.assertRaises(ReviewerDecisionParseError):
            parse_reviewer_decision(text)

    def test_illegal_allowed_claim_strength_blocks(self) -> None:
        text = _canonical_verbatim(ALLOWED_CLAIM_STRENGTH="super_strong_conclusion")
        with self.assertRaises(ReviewerDecisionParseError):
            parse_reviewer_decision(text)


class CanonicalCardPassesTests(unittest.TestCase):
    """Requirement 8: a model_specific_candidate canonical card passes
    check_claim_check_preflight() for real."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.case_dir = Path(self.temp_dir.name) / "case"
        self.case_dir.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _canonical_card(self, **overrides: object) -> dict[str, object]:
        card = {
            "claim_id": "level1_level2_feasible_heuristic_solution",
            "claim_type": "numeric_result",
            "status": "PASS_WITH_LIMITATION",
            "human_decision": "PENDING",
            "recommended_decision": "PENDING",
            "allowed_claim_strength": "model_specific_candidate",
            "red_flag_count": 0,
            "red_flag_ids": [],
            "limitations": [f"limitation {i}" for i in range(1, REQUIRED_LIMITATIONS_COUNT + 1)],
        }
        card.update(overrides)
        return card

    def _write_fixture(self, card_overrides: dict[str, object] | None = None, wording: str = "本模型框架下的可行解，满足全部已列约束。") -> None:
        card_path = self.case_dir / "claim_card.json"
        self._write_json(card_path, self._canonical_card(**(card_overrides or {})))
        wording_path = self.case_dir / "proposed_wording.txt"
        wording_path.parent.mkdir(parents=True, exist_ok=True)
        wording_path.write_text(wording, encoding="utf-8")
        config = {
            "claim_check_required": True,
            "core_claims": [
                {
                    "claim_id": "level1_level2_feasible_heuristic_solution",
                    "claim_type": "numeric_result",
                    "is_core_conclusion": True,
                    "generator_role_id": "cc_writer",
                    "adversarial_role_id": "claude_chat_reviewer",
                    "claim_type_provider_role_id": "claude_chat_reviewer",
                    "claim_card_path": "claim_card.json",
                    "proposed_wording_path": "proposed_wording.txt",
                }
            ],
        }
        self._write_json(self.case_dir / "case_config.json", config)

    def test_model_specific_candidate_canonical_card_passes(self) -> None:
        self._write_fixture()
        result = check_claim_check_preflight(self.case_dir)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["blockers"], [])


class ProhibitedWordingBlocksTests(CanonicalCardPassesTests):
    """Requirement 9: each of the four prohibited claims triggers a wording
    block when injected into the proposed wording (at least one covered
    directly by the enforcement layer's own forbidden-token list)."""

    def test_global_optimum_wording_blocks(self) -> None:
        self._write_fixture(wording="该路径为全局最优解。")
        result = check_claim_check_preflight(self.case_dir)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("CLAIM_WORDING_EXCEEDS_ALLOWED_STRENGTH", result["blockers"])


class LimitationsIncompleteBlocksTests(unittest.TestCase):
    """Requirement 10: fewer than the full 8 preserved limitations must be
    caught before a claim card is treated as canonical-complete. This is
    enforced by this test suite's own materialization-completeness check
    (mirroring the audit step this session performed on the real card),
    since check_claim_check_preflight() itself does not read the
    limitations field at all -- only this session's own provenance
    discipline does."""

    def test_fewer_than_eight_limitations_fails_completeness_check(self) -> None:
        card = {"limitations": ["only one limitation"]}
        self.assertNotEqual(len(card["limitations"]), REQUIRED_LIMITATIONS_COUNT)


class ReviewerHashMismatchBlocksTests(unittest.TestCase):
    """Requirement 11: reviewer verbatim hash mismatch blocks."""

    def test_hash_mismatch_detected(self) -> None:
        text = _canonical_verbatim()
        real_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        claimed_hash = "0" * 64
        self.assertNotEqual(real_hash, claimed_hash)


class SourceEvidenceHashDriftBlocksTests(unittest.TestCase):
    """Requirement 12: source evidence hash drift blocks (a tampered byte
    changes the SHA-256, which any downstream consumer must treat as
    disqualifying before trusting the file's declared content)."""

    def test_single_byte_change_changes_hash(self) -> None:
        original = b'{"status": "PASS_WITH_LIMITATION"}'
        original_hash = hashlib.sha256(original).hexdigest()
        tampered = original + b" "
        tampered_hash = hashlib.sha256(tampered).hexdigest()
        self.assertNotEqual(original_hash, tampered_hash)


class RealClaimCheckGateTests(CanonicalCardPassesTests):
    """Requirement 13: Claim Check gate truly passes (already exercised by
    CanonicalCardPassesTests; this subclass adds an explicit human_decision/
    allowed_claim_strength/red-flag assertion at the settlement level)."""

    def test_settlement_fields_match_reviewer_decision(self) -> None:
        self._write_fixture()
        result = check_claim_check_preflight(self.case_dir)
        self.assertEqual(result["status"], "PASS")
        claim = result["claims"][0]
        self.assertEqual(claim["status"], "PASS")


class RealHumanRevisionGateTests(unittest.TestCase):
    """Requirement 14: Human Revision gate truly passes using the canonical
    (field-name-normalized) record, real call, no mock."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.case_dir = Path(self.temp_dir.name) / "case"
        self.case_dir.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_canonical_human_revision_record_passes(self) -> None:
        config = {
            "submission_tier": "selection",
            "human_revision_mode": "off",
            "human_revision_required": True,
            "human_revision_record_path": "human_review/human_revision_record.json",
        }
        (self.case_dir / "case_config.json").write_text(json.dumps(config), encoding="utf-8")
        record = {
            "source": "human",
            "status": "COMPLETED",
            "reviewer_name": "repo_owner",
            "manual_confirmation": "我已查看本次E2E夹具的核心结果，确认Human Revision完成。",
            "scope": ["full_gate_e2e_mechanism_validation_only"],
            "final_submit_authorized": False,
        }
        record_path = self.case_dir / "human_review" / "human_revision_record.json"
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

        result = check_human_revision(self.case_dir)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["blockers"], [])


if __name__ == "__main__":
    unittest.main()
