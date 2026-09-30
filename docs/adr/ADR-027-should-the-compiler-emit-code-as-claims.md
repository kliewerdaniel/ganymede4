# ADR-027: Should the compiler emit code as claims?

**Status:** PROPOSED — the measurement is done, the decision is not
**Date:** 2026-09-30
**Depends on:** ADR-004, ADR-006, ADR-012, ADR-020, ADR-024
**Decides:** nothing yet. This ADR frames the decision and ships the instrument
that settles it. It is ratified only after the labelled sample is scored.

---

## Context

`docs/open-questions/code-contradictions.md` ended with a list of two things
that would settle the question. The second — "a decision on non-prose
segments" — is a design change with artifact-identity consequences. The first
is "a labelled sample", and it is the precondition for the second.

This ADR ships the first and refuses the second until the sample is labelled.

## The measurement, on the current artifact

`/tmp/p19-t5-real.db`, artifact `v1-49f86ced09fcb43b`, read with raw sqlite3.

The unit is a **pair**: one contradicted subject and one peer from its peer
group. A peer group holds up to nine members, so one contradicted claim
yields several pairs.

| | count |
|---|---:|
| contradicted claims (subjects) | 402 |
| contradicted pairs | 2,854 |
| distinct peer groups involved | 402 |

The subject-level counts from ADR-024 are unchanged by this work: 7,173
supported, 402 contradicted, 16,897 derived, 311,718 inconclusive.

Stratifying all 2,854 pairs with the shipped sampling frame
(`scripts/contradiction_sample.py`):

| stratum | pairs | share |
|---|---:|---:|
| code | 2,550 | 89.3% |
| prose | 302 | 10.6% |
| non-Latin | 2 | 0.1% |

At the claim level, **265 of 402 contradicted subjects (65.9%) are text that
looks like source code.**

That number is a hypothesis, not a finding. It is exactly what
`code-contradictions.md` said would not be good enough to write an ADR on:

> The counts above are my classification by heuristic, not a ground truth,
> and the ADR should not be written on my reading of a sample.

So the frame is used for **sampling**, and never for deciding a label.

## Why not just ship a filter

`CONTRADICTED` is absorbing. Nothing in the epistemic spine returns a
contradicted claim without explicit human action. That asymmetry drives
everything:

- A filter that is too permissive destroys true findings, permanently.
- A filter that is too restrictive only costs a missed finding.

So any policy needs a measured error rate in *both* directions, and the
direction that hurts is the one a heuristic is worst at estimating. This is
why the sheet is stratified rather than sampled proportionally: proportional
sampling of this population is 89% code and would answer only the question
already suspected, leaving the 10.6% prose stratum with an error bar too wide
to act on.

## The instrument

`scripts/contradiction_sample.py`, standard library only, no import of
`ganymede4` — same rule as the auditor, and for the same reason: a tool that
scores the compiler must not inherit the compiler's opinion about what a claim
is.

Two commands:

```
python scripts/contradiction_sample.py build <artifact.db> --out sheet.csv
python scripts/contradiction_sample.py score sheet.csv --policy no-code
```

`build` emits a deterministic stratified sample (122 rows: 60 code, 60 prose,
2 non-Latin — the last stratum is 2 pairs in the entire artifact, so it is
exhausted rather than sampled) with the `label` column **empty**. `score`
reads a filled sheet and reports the label distribution per stratum, the
precision of the sampling frame, and the measured cost of a candidate policy.

Five labels: `real_contradiction`, `code_fragment`, `subset`, `boilerplate`,
`other`.

Three properties the scorer is built to have, each with a sabotage behind it:

1. **It cannot score an unlabelled sheet.** It exits 1 and says so. There is
   no path from "no human has looked at this" to "precision: N%".
2. **It cannot score a label it does not recognise.** A typo fails loudly
   instead of being dropped from the denominator.
3. **It counts what a policy would lose**, not only what it would keep — and
   prints the lost pairs with their text, because "0 real contradictions
   lost" is exactly the number someone would want to believe without
   checking.

A blank label is not a zero label. A partially filled sheet scores the filled
rows and reports how many are missing, rather than quietly reporting a
precision over the rows that happened to be done.

## The options, and what each would cost

Costs below are **structural**, measured. The numbers attached to a specific
policy are not yet available — they come from scoring the filled sheet.

### Option A — emit code as claims, unchanged

- Cost: 89.3% of contradicted pairs are code-shaped and get the absorbing
  label. `CONTRADICTED` on a code fragment is close to the worst available
  label for it.
- Cost: zero artifact churn. Root, version, and every claim id unchanged.
- Risk: the label is absorbing, so this is not free — it is a permanent
  mislabel, repeated on every future corpus that embeds code.

### Option B — compiler stops emitting code as claims

- Cost: **every claim id changes**, because claim ids are content-addressed
  over canonical claim content and provenance. The Merkle root changes. The
  artifact version changes. This is not a patch; it re-identifies the corpus.
- Benefit: removes the largest class at the source, rather than filtering it
  downstream.
- Risk: the classifier must live in segmentation, where ADR-012's warning
  applies hardest — "ignore claims containing newlines" is that rule again,
  fitted to a corpus that is 44% ChatGPT output pasted from a coding
  assistant.

### Option C — evaluator refuses non-prose on a disclosed notion

- Cost: the evaluator gains an explicit, published definition of "this span is
  not a proposition" and applies it at evaluation time only.
- Benefit: claim ids and roots are **unchanged**; the artifact stays
  comparable; the refusal is visible and can be argued with.
- Risk: it is a threshold again, just relocated. ADR-012 does not go away
  because the rule moved files.

### Option D — classify at segmentation time (the principled version)

- Decide at segmentation whether a span is prose or an artifact of another
  kind, and let the evaluator decline non-prose *on principle* rather than by
  heuristic.
- Cost: largest change of the four. Changes segmentation, therefore every
  claim id and the Merkle root.
- This is what `code-contradictions.md` called "a larger design question
  than a guard", and it remains so.

### The asymmetry, stated as the decision criterion

The choice between B, C and D is not "which filter is best". It is:

> How many **real** contradictions is the chosen rule willing to destroy?

because real ones are unrecoverable and false ones are merely noise. The
scorer reports exactly this number, per policy, and names the pairs. If a
policy's loss of real contradictions is greater than zero, that is the
finding — not a reason to soften the policy and re-score until it reads
better.

## Status: awaiting labels

**Nothing in this ADR is ratified, and no compiler or evaluator behaviour has
been changed.** The 265-of-402 figure is the sampling frame's opinion, and
this project has now been wrong five times about a check that was correct when
written. The decision waits on the filled sheet.

## What was learned while building this

**One sabotage test was vacuous and was rewritten.** The first draft pinned the
non-Latin classifier with a sentence containing curly apostrophes, on the
claim that a raw-codepoint check misfiles English prose. Sabotaging the
function back to the raw-codepoint form **did not fail the test**, because
ordinary English with curly apostrophes reaches only ~0.06 non-ASCII
codepoints, far below the 0.2 threshold. The claim turned out to be false:
measured across all 2,854 pair texts in the real artifact, the two formulas
disagree on **zero** texts.

The correct implementation was kept — the category check is the honest one
and costs nothing — but the test now pins the function directly, and its
docstring records that this corpus did **not** prove it necessary. A fifth
occurrence of the same shape: a check that looked like it was verifying
something and was not.

## References

- `scripts/contradiction_sample.py` — build and score the sheet
- `tests/test_contradiction_sample.py` — 15 tests, 6 sabotages
- `docs/open-questions/code-contradictions.md` — the measurement that opened it
- `docs/labelling/CONTRADICTIONS.md` — how to fill in the sheet