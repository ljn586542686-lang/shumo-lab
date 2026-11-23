# Reference Boolean Gate Template — QG-REF-09

<!-- References use a BOOLEAN gate, NOT the A/B/C three-way decision.
     Any unverified reference blocks formal_submission_allowed. -->

## Gate Status

| Field | Value |
|-------|-------|
| gate_id | `QG-REF-09` |
| case_id | [e.g., p_full_sim_2021B_ethanol_c4] |
| paper_version | [e.g., main_clean_final_refs_restored_no_todo] |
| citation_ready | `false` (until all references pass) |
| formal_reference_allowed | `false` (until all references pass) |
| formal_submission_allowed | `false` (until all references pass) |

**Rule:** ANY unverified or incomplete reference → gate remains CLOSED.
Gate opens only when ALL references pass all required checks.

---

## Required Checks Per Reference

For each reference entry, ALL of the following must be confirmed:

| Check | Required when | Auto-fail condition |
|-------|--------------|---------------------|
| author | always | missing or placeholder |
| title | always | missing or placeholder |
| source (journal/publisher/school) | always | missing or placeholder |
| year | always | missing |
| volume + issue | journal articles | missing when article type |
| pages | journal articles | missing when article type |
| publisher | books | missing when book type |
| school + degree | theses | missing when thesis type |
| in-text citation | always | reference listed but never cited in body |
| not generated/placeholder | always | contains "待核实", "TBD", "[author]", "generated" |

**Automatic red flags:**

```
missing_author               → citation_ready=false
missing_title                → citation_ready=false
missing_source               → citation_ready=false
missing_year                 → citation_ready=false
missing_volume_issue_pages   → citation_ready=false (for journal articles)
missing_publisher_or_school  → citation_ready=false (for books/theses)
missing_in_text_citation     → citation_ready=false
generated_or_placeholder     → citation_ready=false
unverified_without_marker    → citation_ready=false
```

---

## Reference Verification Table

| Ref# | Author | Title | Source | Year | Vol/Issue/Pages | Publisher/School | In-text cite | Status |
|------|--------|-------|--------|------|-----------------|-----------------|--------------|--------|
| R1 | | | | | | | | `UNVERIFIED` |
| R2 | | | | | | | | `UNVERIFIED` |
| ... | | | | | | | | |

---

## External Review Draft Policy

In external review drafts, references MAY be listed as candidates with:

```
参考文献（待人工核验）

以下文献为待核验引用线索，正式提交前须逐条核实作者、题名、
来源、年份、卷期、页码及正文引用位置；核验完成前
formal_reference_allowed=false。
```

This marker must NOT be removed until all references pass human verification.

---

## Gate Decision

| Field | Value |
|-------|-------|
| gate_result | `OPEN` / `CLOSED` |
| verified_count | 0 / N |
| unverified_count | N |
| blocking_refs | [list ref IDs with failures] |
| human_verification_required | `true` |
| network_lookup_used | `false` (network access prohibited) |

**Gate closes formal submission path when `unverified_count > 0`.**
