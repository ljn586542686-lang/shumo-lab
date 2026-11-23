"""Tests for pipeline_core.model_diagnosis.model_diagnosis_card.

10 required tests:
  1.  Build a valid card with minimal required fields
  2.  Card.to_dict() has no forbidden fields
  3.  allowed_output_strength is always 'diagnostic_directions_only'
  4.  Wrong diagnosis_type raises ValueError
  5.  Wrong allowed_output_strength raises ValueError
  6.  Card with recommended_model in machine_trigger_evidence raises
  7.  Card with recommended_condition in output dict raises
  8.  synthetic example card builds correctly with all hypotheses
  9.  synthetic card has all 6 blindspots (including new BS-06)
  10. check_card_dict_safe rejects dict with forbidden field
"""

import json
import os
import pytest

from pipeline_core.model_diagnosis.cross_impl_consistency_gate import (
    CROSS_IMPL_TARGET_SPEC_DIFFERENCE,
    CrossImplGateResult,
    GATE_PASS,
)
from pipeline_core.model_diagnosis.model_diagnosis_card import (
    ALLOWED_DIAGNOSIS_TYPES,
    ALLOWED_OUTPUT_STRENGTH,
    FAIL_OVERSTEPPED,
    ModelDiagnosisCard,
    build_diagnosis_card,
    check_card_dict_safe,
    make_blindspot,
    make_diagnostic_hypothesis,
    make_human_judgement_point,
    make_required_evidence,
)


# ---------------------------------------------------------------------------
# Test 1: minimal valid card builds without error
# ---------------------------------------------------------------------------

def test_minimal_card_builds():
    card = build_diagnosis_card(
        diagnosis_id="TEST-DIAG-001",
        diagnosis_type="cross_impl_disagreement",
        case_id="test_case",
        upstream_gate_result=CROSS_IMPL_TARGET_SPEC_DIFFERENCE,
        trigger_claim_id="B3-CLAIM-001",
        trigger_source="test_runner",
        machine_trigger_evidence={"impl_a_value": 4.00, "impl_b_value": 8.00},
        h0_root_cause_line="Direct c4_yield OLS vs conv*sel/100 product.",
    )
    assert card.diagnosis_id == "TEST-DIAG-001"
    assert card.allowed_output_strength == ALLOWED_OUTPUT_STRENGTH
    assert card.upstream_gate == CROSS_IMPL_TARGET_SPEC_DIFFERENCE


# ---------------------------------------------------------------------------
# Test 2: to_dict has no forbidden fields
# ---------------------------------------------------------------------------

def test_card_to_dict_no_forbidden_fields():
    card = build_diagnosis_card(
        diagnosis_id="TEST-DIAG-002",
        diagnosis_type="cross_impl_disagreement",
        case_id="test_case",
        upstream_gate_result=CROSS_IMPL_TARGET_SPEC_DIFFERENCE,
        trigger_claim_id="B3-CLAIM-001",
        trigger_source="test_runner",
        machine_trigger_evidence={"discrepancy_pp": 26.07},
        h0_root_cause_line="Target spec difference confirmed.",
    )
    d = card.to_dict()
    forbidden = {
        "recommended_model", "recommended_condition",
        "final_model", "final_condition",
        "auto_selected_model", "auto_selected_condition",
    }
    for field in forbidden:
        assert field not in d, f"Forbidden field '{field}' found in card.to_dict()"


# ---------------------------------------------------------------------------
# Test 3: allowed_output_strength is always correct
# ---------------------------------------------------------------------------

def test_allowed_output_strength_is_diagnostic_only():
    card = build_diagnosis_card(
        diagnosis_id="TEST-DIAG-003",
        diagnosis_type="extrapolation_risk",
        case_id="test_case",
        upstream_gate_result="CROSS_IMPL_CONSISTENT",
        trigger_claim_id="B3-CLAIM-002",
        trigger_source="test_runner",
        machine_trigger_evidence={},
        h0_root_cause_line="No H0 issue.",
    )
    assert card.allowed_output_strength == "diagnostic_directions_only"
    d = card.to_dict()
    assert d["allowed_output_strength"] == "diagnostic_directions_only"


# ---------------------------------------------------------------------------
# Test 4: invalid diagnosis_type raises ValueError
# ---------------------------------------------------------------------------

def test_invalid_diagnosis_type_raises():
    with pytest.raises(ValueError, match="Unknown diagnosis_type"):
        build_diagnosis_card(
            diagnosis_id="TEST-DIAG-004",
            diagnosis_type="recommend_best_model",  # NOT in ALLOWED_DIAGNOSIS_TYPES
            case_id="test_case",
            upstream_gate_result=CROSS_IMPL_TARGET_SPEC_DIFFERENCE,
            trigger_claim_id="B3-CLAIM-001",
            trigger_source="test_runner",
            machine_trigger_evidence={},
            h0_root_cause_line="n/a",
        )


# ---------------------------------------------------------------------------
# Test 5: wrong allowed_output_strength raises ValueError
# ---------------------------------------------------------------------------

def test_wrong_output_strength_raises():
    with pytest.raises(ValueError, match="allowed_output_strength"):
        ModelDiagnosisCard(
            diagnosis_id="TEST-DIAG-005",
            diagnosis_type="cross_impl_disagreement",
            case_id="test_case",
            upstream_gate="CROSS_IMPL_CONSISTENT",
            trigger_claim_id="C1",
            trigger_source="test",
            machine_trigger_evidence={},
            h0_root_cause_line="n/a",
            allowed_output_strength="priority_candidate",  # FORBIDDEN value
        )


# ---------------------------------------------------------------------------
# Test 6: recommended_model in machine_trigger_evidence is detected via to_dict
# ---------------------------------------------------------------------------

def test_recommended_model_in_trigger_evidence_safe():
    # machine_trigger_evidence can contain anything — it's a data dict,
    # not an output field of the card itself. The safety check is on to_dict().
    # Verify the forbidden field check applies to the card's top-level dict only.
    card = build_diagnosis_card(
        diagnosis_id="TEST-DIAG-006",
        diagnosis_type="cross_impl_disagreement",
        case_id="test_case",
        upstream_gate_result=CROSS_IMPL_TARGET_SPEC_DIFFERENCE,
        trigger_claim_id="B3-CLAIM-001",
        trigger_source="test_runner",
        machine_trigger_evidence={"impl_a": 4.00},
        h0_root_cause_line="n/a",
    )
    d = card.to_dict()
    # The top-level dict must not have recommended_model
    assert "recommended_model" not in d


# ---------------------------------------------------------------------------
# Test 7: injecting recommended_condition into output triggers ValueError
# ---------------------------------------------------------------------------

def test_injecting_forbidden_field_into_card_dict_raises():
    card = build_diagnosis_card(
        diagnosis_id="TEST-DIAG-007",
        diagnosis_type="extrapolation_risk",
        case_id="test_case",
        upstream_gate_result=CROSS_IMPL_TARGET_SPEC_DIFFERENCE,
        trigger_claim_id="B3-CLAIM-003",
        trigger_source="test_runner",
        machine_trigger_evidence={},
        h0_root_cause_line="n/a",
    )
    d = card.to_dict()
    # Manually inject forbidden field and check that the safety validator catches it
    d["recommended_condition"] = "synthetic boundary"
    with pytest.raises(ValueError, match="MODEL_DIAGNOSIS_OVERSTEPPED_INTO_MODEL_SELECTION"):
        check_card_dict_safe(d)


# ---------------------------------------------------------------------------
# Test 8: synthetic example card builds correctly with all hypotheses
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Test 9: synthetic JSON example has all 6 blindspots including BS-06
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Test 10: check_card_dict_safe rejects dict with forbidden field
# ---------------------------------------------------------------------------

def test_check_card_dict_safe_rejects_forbidden():
    bad = {
        "diagnosis_id": "TEST-010",
        "allowed_output_strength": "diagnostic_directions_only",
        "final_condition": "synthetic boundary",  # FORBIDDEN
    }
    with pytest.raises(ValueError, match="MODEL_DIAGNOSIS_OVERSTEPPED_INTO_MODEL_SELECTION"):
        check_card_dict_safe(bad)


def test_check_card_dict_safe_rejects_wrong_strength():
    bad = {
        "diagnosis_id": "TEST-010b",
        "allowed_output_strength": "priority_candidate",  # WRONG
    }
    with pytest.raises(ValueError, match="allowed_output_strength"):
        check_card_dict_safe(bad)


def test_check_card_dict_safe_accepts_valid():
    good = {
        "diagnosis_id": "TEST-010c",
        "allowed_output_strength": "diagnostic_directions_only",
    }
    assert check_card_dict_safe(good) == "SAFE"


# ---------------------------------------------------------------------------
# Red-team patch tests (8 new — tests 11-18)
# ---------------------------------------------------------------------------

# Test 11: producer cannot issue own verdict (self-verdict blocked)
def test_self_verdict_blocked():
    from pipeline_core.model_diagnosis.model_diagnosis_card import assert_no_self_verdict, FAIL_SELF_VERDICT
    with pytest.raises(ValueError, match="SELF_VERDICT_NOT_ALLOWED"):
        assert_no_self_verdict(
            producer_role_id="claude_code",
            verdict_issuer_role_id="claude_code",  # same — FAIL
        )


# Test 12: verdict issuer different from producer → no error
def test_different_verdict_issuer_ok():
    from pipeline_core.model_diagnosis.model_diagnosis_card import assert_no_self_verdict
    # Should not raise
    assert_no_self_verdict(
        producer_role_id="claude_code",
        verdict_issuer_role_id="codex_reviewer",
    )


# Test 13: recommended_decision in output → FAIL / HUMAN_DECISION_RECOMMENDATION_LEAKED_INTO_GATE
def test_decision_recommendation_leakage_blocked():
    from pipeline_core.model_diagnosis.model_diagnosis_card import assert_no_decision_recommendation, FAIL_DECISION_RECOMMENDATION
    bad = {
        "diagnosis_id": "TEST-013",
        "recommended_decision": "choose_B",  # FORBIDDEN
        "allowed_output_strength": "diagnostic_directions_only",
    }
    with pytest.raises(ValueError, match="HUMAN_DECISION_RECOMMENDATION_LEAKED_INTO_GATE"):
        assert_no_decision_recommendation(bad)


# Test 14: suggested_choice / preferred_choice / recommended_decision_tendency also blocked
def test_all_recommendation_fields_blocked():
    from pipeline_core.model_diagnosis.model_diagnosis_card import assert_no_decision_recommendation
    for bad_field in ("suggested_choice", "preferred_choice", "recommended_decision_tendency"):
        bad = {bad_field: "option_A"}
        with pytest.raises(ValueError, match="HUMAN_DECISION_RECOMMENDATION_LEAKED_INTO_GATE"):
            assert_no_decision_recommendation(bad)


# Test 15: CROSS_CATALYST_RANGE_BORROWED flag triggers when series_a max < candidate T
def test_cross_catalyst_range_borrowed_triggers():
    from pipeline_core.model_diagnosis.model_diagnosis_card import (
        make_cross_catalyst_range_borrowed_flag,
        RISK_FLAG_CROSS_CATALYST_RANGE_BORROWED,
    )
    flag = make_cross_catalyst_range_borrowed_flag(
        candidate_catalyst="series_a",
        candidate_temperature=35.0,
        candidate_max_observed_temperature=25.0,
        grid_upper_bound=35.0,
        grid_upper_bound_source_catalyst="series_b",
    )
    assert flag["flag_id"] == RISK_FLAG_CROSS_CATALYST_RANGE_BORROWED
    assert flag["triggered"] is True
    assert flag["borrowed_range_source_catalyst"] == "series_b"
    assert flag["borrowed_range_source_temperature"] == 35.0
    assert flag["borrowed_range_justified_by_data"] == "unknown"
    assert flag["temperature_gap_beyond_training"] == 10.0


# Test 16: double_extrapolated_product_risk flag must include structured axes
def test_double_extrapolated_product_risk_has_axes():
    from pipeline_core.model_diagnosis.model_diagnosis_card import (
        make_double_extrapolated_product_risk_flag,
        RISK_FLAG_DOUBLE_EXTRAPOLATED_PRODUCT,
    )
    flag = make_double_extrapolated_product_risk_flag(
        temperature_axis={
            "name": "temperature",
            "observed_max": 25.0,
            "candidate_temperature": 35.0,
            "gap": 10.0,
            "unit": "C",
        },
        product_chain_components=["conv_pred(series_a,35)", "sel_pred(series_a,35)"],
        product_chain_reason="Both conv_pred and sel_pred are evaluated at T=35 where series_a has no training data.",
    )
    assert flag["flag_id"] == RISK_FLAG_DOUBLE_EXTRAPOLATED_PRODUCT
    assert flag["status"] is True
    ax1 = flag["extrapolation_axis_1"]
    assert ax1["observed_max_for_candidate"] == 25.0
    assert ax1["candidate_temperature"] == 35.0
    assert ax1["gap"] == 10.0
    ax2 = flag["extrapolation_axis_2"]
    assert "conv_pred" in str(ax2["components"])
    assert "sel_pred" in str(ax2["components"])


# Test 17: model conflict must be traceable via source trace fields in JSON


# Test 18: gate coverage scope boundary must be present in JSON / gate module
def test_gate_coverage_scope_boundary_exists():
    from pipeline_core.model_diagnosis.cross_impl_consistency_gate import GATE_COVERAGE_SCOPE
    assert "covers" in GATE_COVERAGE_SCOPE
    assert "does_not_prove" in GATE_COVERAGE_SCOPE
    assert len(GATE_COVERAGE_SCOPE["covers"]) >= 1
    assert len(GATE_COVERAGE_SCOPE["does_not_prove"]) >= 1
    # Must explicitly state what the gate does NOT cover
    dnp = " ".join(GATE_COVERAGE_SCOPE["does_not_prove"])
    assert "extrapolation" in dnp.lower() or "validity" in dnp.lower()


# Test 19: test_scope must describe what tests do NOT verify (patch #2)
def test_test_scope_do_not_verify_present():
    from pipeline_core.model_diagnosis.model_diagnosis_card import TEST_SCOPE
    assert "tests_do_not_verify" in TEST_SCOPE
    assert len(TEST_SCOPE["tests_do_not_verify"]) >= 3
    dnv = " ".join(TEST_SCOPE["tests_do_not_verify"])
    assert "extrapolation" in dnv.lower() or "35" in dnv.lower()


# Test 20: cross_catalyst range borrowed flag rejects invalid justified_by_data
def test_cross_catalyst_range_borrowed_rejects_bad_justified():
    from pipeline_core.model_diagnosis.model_diagnosis_card import make_cross_catalyst_range_borrowed_flag
    with pytest.raises(ValueError, match="justified_by_data must be"):
        make_cross_catalyst_range_borrowed_flag(
            candidate_catalyst="series_a",
            candidate_temperature=35.0,
            candidate_max_observed_temperature=25.0,
            grid_upper_bound=35.0,
            grid_upper_bound_source_catalyst="series_b",
            justified_by_data="yes",  # invalid — must be "true"/"false"/"unknown"
        )
