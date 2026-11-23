"""Tests for pipeline_core.model_diagnosis.cross_impl_consistency_gate.

5 required tests:
  1. Consistent values (within tolerance) → CROSS_IMPL_CONSISTENT, PASS
  2. Missing artifacts → CROSS_IMPL_BLOCKED_MISSING_ARTIFACTS, BLOCK
  3. H0 not run + discrepancy → CROSS_IMPL_UNRESOLVED, BLOCK
  4. 2021B confirmed target_spec_difference → CROSS_IMPL_TARGET_SPEC_DIFFERENCE, PASS (non-blocking)
  5. Forbidden output field raises ValueError
"""

import pytest

from pipeline_core.model_diagnosis.cross_impl_consistency_gate import (
    CROSS_IMPL_BLOCKED_MISSING_ARTIFACTS,
    CROSS_IMPL_CONSISTENT,
    CROSS_IMPL_TARGET_SPEC_DIFFERENCE,
    CROSS_IMPL_UNRESOLVED,
    FORBIDDEN_OUTPUT_FIELDS,
    GATE_BLOCK,
    GATE_PASS,
    assert_no_forbidden_fields,
    run_cross_impl_gate,
    validate_gate_result_dict,
)


# ---------------------------------------------------------------------------
# Test 1: consistent values (within tolerance)
# ---------------------------------------------------------------------------

def test_consistent_within_tolerance():
    result = run_cross_impl_gate(
        case_id="test_case",
        condition_label="A1@350C",
        impl_a_value=15.0,
        impl_b_value=15.0005,
        tolerance=0.01,
    )
    assert result.gate_state == CROSS_IMPL_CONSISTENT
    assert result.verdict == GATE_PASS
    assert result.is_pass
    assert result.discrepancy_abs is not None
    assert result.discrepancy_abs < 0.01
    assert result.disclosure_required is False


# ---------------------------------------------------------------------------
# Test 2: missing artifacts → blocked
# ---------------------------------------------------------------------------

def test_missing_artifacts_blocks():
    result = run_cross_impl_gate(
        case_id="test_case",
        condition_label="input=4",
        impl_a_value=None,
        impl_b_value=None,
        missing_artifacts=["model_fit.m", "result_summary.json"],
    )
    assert result.gate_state == CROSS_IMPL_BLOCKED_MISSING_ARTIFACTS
    assert result.verdict == GATE_BLOCK
    assert result.is_block
    assert result.b3_generation_allowed is False
    assert result.citation_ready is False
    assert len(result.blocking_reasons) > 0


def test_none_values_blocks():
    result = run_cross_impl_gate(
        case_id="test_case",
        condition_label="input=4",
        impl_a_value=None,
        impl_b_value=66.29,
    )
    assert result.gate_state == CROSS_IMPL_BLOCKED_MISSING_ARTIFACTS
    assert result.is_block


# ---------------------------------------------------------------------------
# Test 3: H0 not run + discrepancy → UNRESOLVED, BLOCK
# ---------------------------------------------------------------------------

def test_unresolved_when_h0_not_run():
    result = run_cross_impl_gate(
        case_id="test_case",
        condition_label="input=4",
        impl_a_value=40.22,
        impl_b_value=66.29,
        h0_status="",  # H0 not run
    )
    assert result.gate_state == CROSS_IMPL_UNRESOLVED
    assert result.verdict == GATE_BLOCK
    assert result.is_block
    assert "H0 audit not completed" in result.blocking_reasons[0]


def test_unresolved_when_h0_open():
    result = run_cross_impl_gate(
        case_id="test_case",
        condition_label="input=4",
        impl_a_value=40.22,
        impl_b_value=66.29,
        h0_status="H0_OPEN",
    )
    assert result.gate_state == CROSS_IMPL_UNRESOLVED
    assert result.is_block


# ---------------------------------------------------------------------------
# Test 4: 2021B confirmed target_spec_difference → PASS (non-blocking), disclosure required
# ---------------------------------------------------------------------------

def test_synthetic_target_spec_difference_passes_with_disclosure():
    result = run_cross_impl_gate(
        case_id="synthetic_response",
        condition_label="input=4",
        impl_a_value=10.0,
        impl_b_value=15.0,
        tolerance=0.01,
        h0_status="H0_CLOSED_PYTHON_CONFIRMED",
        h0_root_cause_category="target_spec_difference",
        root_cause_confirmed=True,
        reproduction_error_pp=0.0,
        evidence_tier="E1_python_reimpl",
    )
    assert result.gate_state == CROSS_IMPL_TARGET_SPEC_DIFFERENCE
    assert result.verdict == GATE_PASS
    assert result.is_pass
    assert result.disclosure_required is True
    assert result.claim_strength_ceiling == "implementation_specific_diagnostic_only"
    assert result.b3_generation_allowed is False
    assert result.citation_ready is False
    assert result.formal_reference_allowed is False
    assert result.reproduction_error_pp == 0.0
    assert abs(result.discrepancy_abs - 5.0) < 0.001


def test_synthetic_gate_dict_has_no_forbidden_fields():
    result = run_cross_impl_gate(
        case_id="synthetic_response",
        condition_label="input=4",
        impl_a_value=10.0,
        impl_b_value=15.0,
        h0_status="H0_CLOSED_PYTHON_CONFIRMED",
        h0_root_cause_category="target_spec_difference",
        root_cause_confirmed=True,
        reproduction_error_pp=0.0,
    )
    d = result.to_dict()
    for forbidden in FORBIDDEN_OUTPUT_FIELDS:
        assert forbidden not in d, f"Forbidden field '{forbidden}' found in gate result dict"


# ---------------------------------------------------------------------------
# Test 5: forbidden output field raises ValueError
# ---------------------------------------------------------------------------

def test_forbidden_field_raises():
    bad_dict = {
        "gate_state": CROSS_IMPL_CONSISTENT,
        "verdict": GATE_PASS,
        "recommended_model": "A1",  # FORBIDDEN
    }
    with pytest.raises(ValueError, match="MODEL_DIAGNOSIS_OVERSTEPPED_INTO_MODEL_SELECTION"):
        assert_no_forbidden_fields(bad_dict)


def test_forbidden_field_recommended_condition_raises():
    bad_dict = {
        "gate_state": CROSS_IMPL_TARGET_SPEC_DIFFERENCE,
        "recommended_condition": "input=4",  # FORBIDDEN
    }
    with pytest.raises(ValueError, match="MODEL_DIAGNOSIS_OVERSTEPPED_INTO_MODEL_SELECTION"):
        assert_no_forbidden_fields(bad_dict)


def test_forbidden_field_final_model_raises():
    bad_dict = {"final_model": "conv_x_sel"}
    with pytest.raises(ValueError):
        assert_no_forbidden_fields(bad_dict)


def test_validate_gate_result_dict_rejects_unknown_state():
    d = {
        "gate_state": "CROSS_IMPL_UNKNOWN_BOGUS",
        "verdict": GATE_PASS,
    }
    with pytest.raises(ValueError, match="Unknown gate_state"):
        validate_gate_result_dict(d)


def test_validate_gate_result_dict_accepts_valid():
    d = {
        "gate_state": CROSS_IMPL_CONSISTENT,
        "verdict": GATE_PASS,
    }
    result = validate_gate_result_dict(d)
    assert result == GATE_PASS
