# Stage Prompt Skeletons

## Problem Stage

Read only the active case problem statement and attachments. Output problem type, task split, assumptions, and human questions. Do not write code or paper text.

## Model Route Stage

Generate at least five model routes. Do not choose the final route. Require `gate_2_model_choice.md`.

## Validation Stage

Check outputs, evidence map, citations, and AI disclosure. Do not repair results by inventing data.

## Writer Entry Stage

Before writing paper text, the formal writer must read and validate the existing
case-local writer handoff and its `MODEL_TO_PAPER_CONTRACT` binding section. The
writer contract is mandatory: `WRITER_CONTRACT_REQUIRED=YES`. Its canonical
source paths and SHA-256 identities are the only authority for numerical
results, model identity, claim strength, uncertainty and limitations. Use that
existing writer handoff and contract as the source for the key state variables, key
constraints, actual grouping/aggregation rules, core objective or loss function,
question-specific assumptions, rules that must not cross questions, frozen results,
and claim boundary. If the contract is missing or inconsistent with the canonical
result artifacts, or if another conflicting result summary is supplied to the
writer, stop with `STALE_RESULT_CONFLICT`, identify the conflicting source paths,
and return the issue to the existing handoff/review process; do not silently
choose, copy, repair or overwrite a result in prose. This is a writer-input
boundary, not a new handoff gate or an independent model audit.

`GPT_REMINDER_REQUIRED_FOR_CANONICAL_RESULT=NO`: the contract requirement is
part of the formal writer execution contract and must hold even when the prompt
does not repeat a reminder to read canonical results.

Use the existing Track C v1 writing constraints in
`pipeline_core/paper_quality/paper_draft_quality_checklist.md` to improve
competition-paper expression only. Track C is a writing constraint, not a new
review stage.

The writer must continue to obey `Frozen Result / Claim Boundary`,
`MODEL_TO_PAPER_CONTRACT`, and `ONLY-NAMED-CHANGE`. The writer only expresses
the frozen model and results faithfully and applies the Track C constraints to
paper language. Do not add a writer gate, re-audit model correctness, create a
Final Artifact state layer, or introduce a state-machine layer.

For optimization, scheduling, stochastic recommendation, or simulation claims,
respect the existing model-building evidence rules when stating claims from the
frozen results, including their bound / `optimality gap`, uncertainty, stability,
and information-availability boundaries. Missing evidence limits the claim
wording; the writer does not re-run model review or force a more complex algorithm.

## Execution Scope Rule

`ONLY-NAMED-CHANGE`: in the current execution, modify only the objects explicitly
named by the user. Suggestions phrased as future work or reminders for the user are
not authorization to write them into the current execution prompt or repository.
Do not add chapters, declarations, explanatory sections, or compliance wording
unless that addition is explicitly requested.

## Final Artifact Inspection (submission completion definition)

The executor's self-reported `PASS` is not evidence. Completion of the submission
package includes inspection of the actual final PDF and actual final supporting-
material package. At minimum inspect margins and overflow, orphan headings, large
invalid whitespace, figure/table collisions, long paths or code-line overflow,
identity information, internal terms such as `pipeline`, `Track`, `Gate`, or
`队友包`, and the declared supporting-material file list against the actual ZIP
entries. This inspection does not re-run or re-review the model and is not a new
state-machine layer.

## Freeze Stage

Freeze only after all validators and human gates pass, and only after the actual
final-artifact inspection passes. `DAY1_SUBMISSION_READY_FREEZE` is not allowed
when only the model and paper body are complete.
