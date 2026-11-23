"""pipeline_core.model_diagnosis — Model Diagnosis subsystem (X5-F5.5D).

Provides:
  cross_impl_consistency_gate  — H0 gate: are the two implementations consistent?
  model_diagnosis_card         — downstream diagnosis: why is this model weak?

Anti-selection rule: nothing in this package may output recommended_model,
recommended_condition, final_model, final_condition, auto_selected_model, or
auto_selected_condition.  Any such field triggers FAIL / MODEL_DIAGNOSIS_OVERSTEPPED_INTO_MODEL_SELECTION.
"""

from pipeline_core.model_diagnosis.cross_impl_consistency_gate import (
    CrossImplGateResult,
    run_cross_impl_gate,
    validate_gate_result_dict,
    assert_no_forbidden_fields,
    CROSS_IMPL_CONSISTENT,
    CROSS_IMPL_TARGET_SPEC_DIFFERENCE,
    CROSS_IMPL_PREPROCESSING_DIFFERENCE,
    CROSS_IMPL_MODEL_SPEC_DIFFERENCE,
    CROSS_IMPL_BUG_SUSPECTED,
    CROSS_IMPL_UNRESOLVED,
    CROSS_IMPL_BLOCKED_MISSING_ARTIFACTS,
    GATE_PASS,
    GATE_BLOCK,
    GATE_FAIL,
    FORBIDDEN_OUTPUT_FIELDS,
)

__all__ = [
    "CrossImplGateResult",
    "run_cross_impl_gate",
    "validate_gate_result_dict",
    "assert_no_forbidden_fields",
    "CROSS_IMPL_CONSISTENT",
    "CROSS_IMPL_TARGET_SPEC_DIFFERENCE",
    "CROSS_IMPL_PREPROCESSING_DIFFERENCE",
    "CROSS_IMPL_MODEL_SPEC_DIFFERENCE",
    "CROSS_IMPL_BUG_SUSPECTED",
    "CROSS_IMPL_UNRESOLVED",
    "CROSS_IMPL_BLOCKED_MISSING_ARTIFACTS",
    "GATE_PASS",
    "GATE_BLOCK",
    "GATE_FAIL",
    "FORBIDDEN_OUTPUT_FIELDS",
]
