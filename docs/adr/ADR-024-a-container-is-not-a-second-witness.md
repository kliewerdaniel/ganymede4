# ADR-024: A container is not a second witness

Status: Ratified
Date: 2026-09-30
Depends on: ADR-006, ADR-012, ADR-019, ADR-020

## Concern

ADR-019 established that a byte-identical or normalized-identical copy of a
proposition cannot attest it. It did not ask the adjacent question.

So the task in Phase 19 Task 2 was not to run the auditor again. The auditor
answers "is this artifact internally consistent". The question here was
different: **do the verdicts carry any information at all?**

A verdict can be structurally perfect, cleanly audited, and still be vacuous.
That has now happened four separate times — ADR-014, ADR-018, ADR-020 and
ADR-021 — and each time the auditor reported `clean: true` on an artifact that
described something other than what it held. So `scripts/read_verdicts.py`
was written to *read* the verdicts: raw stdlib `sqlite3`, importing nothing
from `ganymede4`, writing nothing, every number counted rather than estimated.

## The verdict: 17% of SUPPORTED was one claim wearing three hats

`Evaluator._attestations` selected peers by two predicates. ADR-019 added
`peer.sequence != subject.sequence`. The pre-existing one was
`subject.is_contiguous_in(peer)` — and its docstring said, in as many words,
that a strictly longer peer still counts, *"the case `is_contiguous_in` was
written for."*

Measured on the real artifact (`/tmp/resealed.db`, 336,190 claims):

| | count | share |
|---|---|---|
| `SUPPORTED` claims | 8,646 | |
| subject's text is a substring of **every** attester | 1,473 | **17.0%** |
| all attesters come from the same source | 121 | 1.4% |
| subject ≤ 40 characters | 5,278 | 61.0% |
| short subjects supported *only* by containing attesters | 609 | 11.5% |

The 11.5% figure is the load-bearing one. Those claims have no independent
witness at all. Their entire evidentiary basis is that some longer claim
happens to swallow them:

```
SUBJECT : 'Use Ollama to summarize text'
ATTESTER: 'Use Ollama to summarize text with contextual metadata'

SUBJECT : 'Extract JSON from the assistant's message'
ATTESTER: 'Extract JSON from the assistant's message using regex'

SUBJECT : 'com/kliewerdaniel/GhostWriter.'
ATTESTER: 'com/kliewerdaniel/GhostWriter](https://github.'
```

A container and its content are not two witnesses. They are one assertion
recorded at two lengths. This is ADR-019's perfect-recall failure arriving
through a different door — and unlike ADR-019 it was **not** an accident: the
code deliberately permitted it, on a stated rationale that measurement has now
disproved.

## Why a narrower rule was rejected

The obvious refinement is to exclude only containers that *add nothing* — a
peer whose extra words are all function words should still count. That rule was
written, and it was discarded before measuring, because the real corpus
containers **do** add content words. `with contextual metadata` adds
`contextual` and `metadata`. The refined rule would have excluded almost
nothing while looking, in the diff, like a fix.

So the rule is structural rather than lexical: containment is not evidence,
full stop.

```python
and not _contains_verbatim(self._texts[subject_id], self._texts[cid])
```

## What the fix costs, measured

The obvious risk is that `SUPPORTED` collapses and the evaluator simply stops
working. It does not. Classifying every attester in a 800-claim sample of the
real corpus:

| attester relation to subject | count | share |
|---|---|---|
| genuine restatement (neither equal nor container) | 15,177 | **94.4%** |
| text container | 903 | 5.6% |
| byte-identical | 0 | 0% |

The admissible shape that survives is a *text* variant of the same assertion —
a URL wrapped in markdown, a word inserted inside the span — which normalizes
to a proper prefix of the peer's sequence:

```
SUBJECT: 'You can download it from Python’s official website.'
ATTESTER: "You can download it from [Python's official website](https://www."

subject sequence: (you, can, download, python, official, website)
attester sequence: (you, can, download, python, official, website, http, www)
```

That is the common case, not a corner. The fix removes 5.6% of attester slots.

## Two tests that could not fail, and what that cost

Two sabotage directions are required. The first two attempts at the
over-broad guard were **vacuous**, and only sabotage testing revealed it:

1. The first fixture only ever added the *subject* claim and never the
   restatement, so there was nothing to attest and the assertion was
   unreachable.
2. The first "over-broad" sabotage deleted the `and subject.is_contiguous_in(...)`
   clause. That was also wrong — and wrong in an instructive way: **removing a
   clause from a list comprehension's filter can only add peers.** It could not
   possibly disable attestation, so the sabotage was structurally incapable of
   testing what it claimed to test. The genuine over-broad sabotage is an
   unconditional empty return.

The fixture that finally worked is the corpus pair above, used verbatim,
including the typographic apostrophe and the unbalanced parenthesis — both
load-bearing, since a tidied-up version stops reproducing the shape.

| sabotage | result |
|---|---|
| remove the containment guard | fails `test_a_claim_containing_the_subject_is_not_its_witness` |
| attestation disabled entirely (`return []`) | fails the over-broad test **and** a pre-existing duplicate-attestation test |
| containment guard made unconditional (`and False`) | fails the same two |
| drop the ADR-019 sequence inequality (regression) | fails `test_punctuation_only_variants_do_not_attest_each_other` |

The third row is the point of the exercise: an over-broad fix that destroys
attestation entirely is caught by a test that predates this ADR.

## The other verdicts

**CONTRADICTED is not affected.** Of 596 contradictions, 5 (0.8%) involve
containment. Near-containment pairs are mostly code fragments differing by an
indented line. No containment rule is applied to contradictions, because there
is no measurement justifying one.

**DERIVED is dominated by noise, and is reported rather than fixed.**
16,897 derived claims over 100,723 edges, whose content is file-watcher
chatter:

```
"The sentence shape 'py first seen with mtime 1729088249' occurs in 17 distinct sources."
"The sentence shape 'mo first seen with mtime 1729088329' occurs in 4 distinct sources."
```

These are mined recurrence summaries, not verdicts about meaning. ADR-012's
proposition gate admits them because they are well-formed sentences. Filtering
them is a separate decision requiring a hand-labelled sample — Task 3 — and no
threshold is fitted here.

**Item 4 passes as implemented.** The five non-author Reddit submissions are
separated by a distinct `source_type` (`reddit_submission_other`), carrying 40
claims, all `inconclusive`, none supported or contradicted. `sources.meta.author`
is populated. No parent-comment text exists anywhere in the corpus.

## Cost of being wrong in this direction

The conservative direction matters. The excluded set only ever grows *into*
"no evidence" — a claim that loses its container attester falls to
`inconclusive`, never to `contradicted`. The evaluator cannot convert this fix
into a stronger claim than the evidence supports; it can only decline to make
one.

The cost is that `SUPPORTED` becomes harder to reach on a corpus with little
independent corroboration, which this corpus largely is. That is the correct
direction for a system whose thesis is that evidence magnitude is not
sufficiency. Replaying the new rule over the existing peer groups on the real
artifact:

| | count | share |
|---|---|---|
| `SUPPORTED` before | 8,646 | |
| lose every attester | 1,473 | 17.0% |
| retain at least one attester | **7,173** | 83.0% |

So the fix costs 17% of `SUPPORTED` and does not collapse it. This is a
projection over the existing peer groups rather than a fresh full evaluation,
and it is exact for this rule: the fix only *removes* attesters, so the
post-fix supported set is the subset that retains one. It is labelled a
projection in `README.md` rather than reported as a measurement.

## What this ADR does not decide

- Whether the compiler should emit mined recurrence shapes as claims at all.
- Whether code fragments should be excluded from contradiction eligibility
  (tracked in `docs/open-questions/code-contradictions.md`).
- Whether a genuine multilingual lexical evaluator is worth building.
