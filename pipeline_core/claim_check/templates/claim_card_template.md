# Adversarial Claim Card Template — F5.5

<!-- Fill one card per core claim. Delete this comment before submission. -->

## Claim Metadata

| Field | Value |
|-------|-------|
| claim_id | `CLAIM-XXX` |
| claim_title | [short title] |
| claim_type | [one of: model_selection / optimal_condition / experiment_design / sensitivity_claim / stability_claim / evaluation_claim / prediction_claim / classification_claim / model_explanation] |
| case_id | [e.g., p_full_sim_2021B_ethanol_c4] |
| source_section | [section number and title in the paper] |
| generator_role_id | [e.g., claude_code_writer] |
| adversarial_role_id | [MUST be different from generator_role_id, e.g., gpt_adversarial_reviewer] |

## Claim Text

> [State the claim exactly as it would appear in the paper.]

---

## Generator Statement

[Produced by: `generator_role_id` only]

### Supporting evidence

| Evidence field | Value | Source type | Source path |
|----------------|-------|-------------|-------------|
| predicted_value | | | |
| observed_best_value | | | |
| selected_model | | | |
| validation_metrics_by_model | | | |
| ... | | | |

### Known limitations

- [Limitation 1]
- [Limitation 2]

### Boundary conditions acknowledged

- [e.g., candidate is at temperature boundary]

### What this statement does NOT claim

- [e.g., does not claim global optimality]
- [e.g., does not guarantee experimental replication]

---

## Adversarial Critique

[Produced by: `adversarial_role_id` ONLY — generator must NOT fill this section]

### Summary judgment

[strong / weak / borderline — state the most critical risk]

### Deduction risks

1. **[Risk 1]** — Severity: HIGH — [Evidence cited]
2. **[Risk 2]** — Severity: MEDIUM — ...

### Missing evidence

- [What should have been provided but wasn't]

### Verdict

[Would a careful grader accept this claim as written? Yes / No / Conditional]

---

## Machine Red Flags

| Flag ID | Severity | Triggered | Details |
|---------|----------|-----------|---------|
| EDGE_BOUNDARY_FLAG | RED | ? | |
| MODEL_CONFLICT_FLAG | RED | ? | |
| MODEL_SELECTION_FLAG | RED/YELLOW | ? | |
| BASELINE_MISSING_FLAG | RED | ? | |
| IMPROVEMENT_WITHIN_ERROR_FLAG | RED/YELLOW | ? | |
| INTERACTION_MISSING_FLAG | YELLOW | ? | |
| UNCERTAINTY_MISSING_FLAG | YELLOW/RED | ? | |
| MISSING_REQUIRED_EVIDENCE_FLAG | RED | ? | |

---

## Human Decision

> **Required when any RED flag is triggered.**
> Zero red flags → auto-pass (no gate needed).

| Field | Value |
|-------|-------|
| human_decision | `PENDING` / `A_ACCEPT_AS_CORE_CLAIM` / `B_DOWNGRADE_CLAIM` / `C_REJECT_AND_REWORK` |
| recommended_decision | [auto-filled by machine] |
| allowed_claim_strength | [auto-filled: core_conclusion / priority_candidate / model_specific_candidate / trend_evidence_only / diagnostic_only / not_allowed] |
| status | [PENDING / AUTO_PASS / YELLOW_ADVISORY_AUTO_PASS / RED_FLAG_REQUIRES_HUMAN_DECISION] |

### Required revisions (if B or C)

1. [Revision 1]
2. [Revision 2]

---

## Limitations

- F5.5 prevents overclaiming but does not guarantee correct modeling method selection.
- This card does not close the math_core upper-bound open item.
- Formal reference verification remains human-dependent.
