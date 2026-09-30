# ADR-026: The root cannot see text, and neither could the auditor

Status: Ratified
Date: 2026-09-30
Depends on: ADR-004, ADR-014, ADR-021, ADR-025

## Context

ADR-025 required a `verify` command that **recomputes** the artifact identity
rather than printing the recorded one. Building that meant asking a question
nothing had asked before with an answer attached: *what happens if I change a
row behind the pipeline's back?*

The answer was that two rows could be edited with no detection anywhere in the
system.

## Measurement

One tamper at a time on a small compiled artifact, checking whether the rebuilt
Merkle root changes and whether the independent auditor reports clean:

| tamper                        | root detects | auditor clean | exit |
|---|---|---|---|
| `claims.text` edited         | **no**  | **true**  | 0 |
| `sources.content` edited     | **no**  | **true**  | 0 |
| `evidence.text` edited       | no         | false      | 1 |
| `claims.state` edited        | yes        | false      | 1 |
| claim row deleted            | yes        | false      | 1 |
| evidence row deleted         | yes        | **true**  | 0 |

Three of those are wrong. The two in bold at the top are this ADR.

## Why the root cannot catch it, and why that is not a bug

The Merkle root is computed over content-addressed claim **ids**. A claim id
is a hash of the claim's content, so editing a row's text *without*
recomputing its id leaves the id — and therefore the root — untouched. That is
what content addressing *is*. A root that detected a text edit would have to
hash the text, which would destroy the property that recompiling unchanged
bytes reproduces the same root (ADR-004) and that reordering sources does not
change identity (Phase 2).

So the root certifies *"these rows are the rows that were compiled"*, not
*"these rows say what they say they say"*. That is a real limit and it is worth
stating plainly rather than discovering later.

## Why that left a hole in the thesis

This project's stated position is that correctness rests on **provenance and
structure** — claims resolving to exact character offsets, which is
mechanically checkable — and not on model-judged containment. The auditor's
`sources.checksum` column is the mechanism that makes "mechanically checkable"
true, and it was checking neither of the two things it existed to check.

`evidence.text` edits were already caught, because the auditor re-slices the
source and compares (ADR-014). So the exact-offset machinery was working for
evidence spans and failing for the source document and the claim text — the
two things it was supposed to be the backstop for. You could rewrite what a
claim says, or rewrite the document it came from, and the artifact would still
report `clean: true`.

## Decision

Two checks, both in `scripts/audit_provenance.py`, both fail-closed:

1. **`source_checksum_mismatches`** — recompute `sha256(sources.content)` and
   compare to the stored `checksum`. The column has existed since Phase 0 and
   was written at insert time; **nothing ever read it back**.
2. **`claim_text_mismatches`** — a claim's text must equal the source span it
   cites. Segments are verbatim substrings by construction (ADR-004), so any
   divergence means the row was altered after compilation. Claims with no
   evidence are skipped: derived heuristics are mined rather than extracted
   and have no source span to disagree with. That exemption is a decision, and
   a test states it rather than leaving it implicit.

Both use **Python slicing, not `substr()`** — ADR-014 had to make exactly this
change because SQLite's `substr()` operates on a NUL-terminated C string and
silently truncates on the real corpus.

Both live in the auditor, not the store. The store is the thing being audited;
a check that lives there is not an independent check. The auditor recomputes
`sha256` locally rather than importing `ganymede4.core.content.digest`,
because importing the code under audit is the failure mode this whole component
exists to avoid.

## Verification

- `tests/test_auditor_text_integrity.py` — 7 tests, including that a clean
  artifact reports **no** mismatches, and that pre-existing detections
  (state tampering, deleted sources) survive.
- **Real artifact, 336,190 claims / 319,293 evidence rows: both new lists
  empty.** No false positives. Audit time 6.5s → 10.4s.
- Planted tampers on the real artifact: one source edit and one claim-text
  edit → `clean: false`, exit 1, each reported with its exact id and both
  strings.
- Sabotage, all three directions: neutering the checksum check fails
  `test_edited_source_content_is_detected`; neutering the claim-text check
  fails `test_edited_claim_text_is_detected`; removing both from the `clean`
  conjunction fails both. Auditor restored and byte-compared afterwards.

## Cost

+3.9s on a 6.5s audit, for two whole-table passes. The first is a hash over
35.8 MB of source text; the second is a slice per evidence row. On the real
corpus that is a linear scan of the two largest tables, and it buys the
detection of the two edits that previously passed silently.

## Not fixed here

**Deleting an `evidence` row is still undetected** (`auditor_clean: true`,
exit 0). It changes the root, so `verify` catches it, but the auditor does not.
The likely cause is that a claim left with fewer evidence rows still satisfies
`require_provenance()`'s per-claim check, and the auditor has no
"this claim should have more evidence" assertion — it can check that evidence
resolves, not that evidence is *complete*. Recorded rather than fixed: a
completeness assertion needs a definition of what a claim *should* cite, and
guessing one would be the threshold-fitting this project keeps declining to do.

## What this ADR does not decide

- Whether claim-evidence completeness should be a checked invariant.
- Whether `verify` should refuse to run against an artifact whose recorded
  root it cannot reproduce, or merely report the mismatch. It currently
  reports and exits 1.
