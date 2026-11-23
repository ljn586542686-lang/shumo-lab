# Generator Statement Prompt — F5.5 Adversarial Claim Check

**Role:** Paper generator (writer role)

**Task:** Produce a structured statement explaining why the claim below is valid,
based only on evidence already in the structured evidence record.

**Anti-self-certification rules (mandatory):**
1. Do NOT hide unfavorable evidence.
2. Do NOT present diagnostic evidence as primary evidence.
3. Do NOT fabricate quantitative values not present in the evidence record.
4. Do NOT use `citation_ready=true` or `formal_reference_allowed=true` unless
   human verification is complete.
5. Do NOT fill the `adversarial_critique` field — that is the adversarial role's job.
6. Do NOT fill semantic judgement fields (`claim_depends_on_group_response`,
   `claim_relies_on_extrapolation`, etc.) — those require adversarial role.

**Required output structure:**

```
## Generator Statement

### Claim
[Restate the claim in one sentence.]

### Supporting evidence
[List each piece of evidence with: source, value, how it supports the claim.]

### Known limitations in the evidence
[List limitations honestly: which required slots are missing, which metrics
were not computed, where the model was not validated.]

### Boundary conditions acknowledged
[Explicitly state any boundary/extrapolation issues.]

### What this statement does NOT claim
[List at least 2 things: e.g., "This does not claim global optimality."
"This does not guarantee experimental replication."]
```

**Reminder:** Your statement will be evaluated by an independent adversarial
critique. Do not pre-empt or soften the adversarial role's job.
