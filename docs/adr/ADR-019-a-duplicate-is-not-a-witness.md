# ADR-019 — A duplicate is not a witness to itself

**Status:** Ratified
**Date:** 2026-09-29
**Depends on:** ADR-002, ADR-003, ADR-006, ADR-012

## Context

The real corpus artifact had never been evaluated. It was compiled with
`--no-evaluate` and carried 319,293 `UNEXAMINED` claims and zero evaluations,
because the last full evaluation run was on the pre-ADR-012 artifact that was
subsequently deleted. The `evidence → verification` arrow in the pipeline
diagram had therefore never executed on the current artifact at all.

Running the evaluator on a 300-document slice produced this:

```
supported      6,075   (65.5% of 9,273 claims)
inconclusive   2,443
derived          755
contradictions     0
```

A 65% support rate from a deterministic lexical evaluator is not a plausible
result. It should have been the first thing that looked wrong.

It was. Every sampled `ATTESTED` verdict looked like this:

```
SUBJECT : 'Looking forward to the conversation!'
ATTESTER: 'Looking forward to the conversation!'
ATTESTER: 'Looking forward to the conversation!'
```

The subject is attested by byte-identical copies of itself. There are **eleven**
claims in the corpus with that exact text, and each one attests the other ten.

## The defect

A claim's content id is a hash over `{text, state, evidence_ids, valid_time,
investigation_id, meta}`. The compiler sets `meta` to
`{source_uri, ordinal, origin}` and gives each claim exactly one evidence span
pointing at its own source. So the same sentence appearing in eleven documents
produces eleven claims with eleven different ids — which is correct. Those are
eleven real occurrences, each with its own provenance, and the store is right to
keep all eleven.

ADR-006 §2.2 states the invariant that is supposed to prevent exactly this:

> a claim may never be its own evidence

and the evaluator implements it as an id comparison — `_token_peers` and
`_term_peers` both filter `c != subject_id`. That filter is *structurally*
sufficient and *semantically* wrong. It excludes the one claim that is literally
the same row, and it does nothing about the other 999,999 rows that say the same
thing.

Self-attestation does not require sharing an id. It requires sharing a
**proposition**. ADR-006 calls `normalize()` "the part of the evaluator that
could quietly lie... this decides whether two sentences are the *same claim*",
and then the attestation path never asked it that question. It asked a weaker
one — *is this a different row?* — and answered it with a string comparison.

`Normalized.is_contiguous_in()` returns `True` for a peer whose sequence is
equal to the subject's, because a sequence is trivially contiguous in itself.
So `SUPPORTED` fired on every duplicate pair in the corpus, and the guard whose
entire purpose was to prevent perfect-recall zero-information verdicts was
bypassed by the one mechanism the compiler uses everywhere.

## Why this is the most serious defect so far

ADR-006's module docstring names the failure it was built to prevent:

> Test containment naively and every claim in every corpus becomes
> `SUPPORTED`, forever, with perfect recall and zero information — the v0.1
> fabrication with a verdict attached.

That is precisely and exactly what happened. The corpus contains a large number
of boilerplate sentences — `Looking forward to the conversation!` is one, and it
appears eleven times because it is a ChatGPT sign-off — and the evaluator
promoted every one of them to `SUPPORTED` on the testimony of its own copies.

The difference from the v0.1 fabrication is that every field is *individually*
defensible. The claim exists. The evidence span is verbatim. The evaluation
record is present. The auditor passes. Each of the 6,075 verdicts is a complete,
well-formed, honestly-recorded finding — of nothing. The graph is not lying in
any one place; it is lying in the relationship between places, which is the only
place a lie can hide that no single check will find.

Measured on the 300-document slice:

| | |
|---|---|
| claims | 9,273 |
| claim rows sharing text with another row | 6,041 (65.1%) |
| distinct texts appearing more than once | 1,355 |
| `SUPPORTED` verdicts | 6,075 (65.5%) |
| `SUPPORTED` whose attesters are **all** identical-text copies | 5,987 (98.6%) |
| `SUPPORTED` with at least one genuine non-duplicate attester | **88** |

97.1% of all `SUPPORTED` claims and 98.6% of duplicate-attested ones are
attested only by copies of themselves. The 65.1% duplicate rate and the 65.5%
support rate are the same number, which is the tell.

Note that the duplicate rate is *itself* legitimate information. Eleven
occurrences of a sign-off line is a real fact about the corpus and the store is
right to record eleven claims with eleven spans. The defect is not that
duplicates exist. The defect is that the evaluator cannot tell a witness from an
echo.

## Decision

**Attestation requires a distinct proposition, not a distinct row id.**

A peer attests the subject only if it says something the subject does not
already say — concretely, if its normalized sequence differs from the subject's.
An identical sequence is the same assertion, however many rows hold it, and is
excluded from attestation exactly as the subject is excluded from itself.

```python
# _attestations
return [
    cid
    for cid in self._token_peers(subject_id, subject)
    if self._norms[cid].sequence != subject.sequence
    and subject.is_contiguous_in(self._norms[cid])
]
```

This is a one-line change with three deliberate properties.

**It is conservative in the ADR-006 direction.** The excluded set grows, so
`SUPPORTED` becomes harder to reach and `INCONCLUSIVE` — the module's stated
default and the honest name for "material was found and it establishes nothing" —
absorbs the difference. No claim moves *up* the spine as a result of this fix.

**It keeps genuine attestation.** A strictly longer claim that contains the
subject's proposition contiguously is a real second witness — that is the case
`is_contiguous_in` was written for, and it is preserved. Only equality is
excluded. After the fix the 300-document slice retains 88 genuinely attested
claims and drops 5,987 echoes.

**It fixes the relation, not the symptom.** Normalized sequence equality is the
same identity question `normalize()` exists to answer, and the attestation path
now asks it. A future predicate that compares propositions should ask it too; the
reason this bug survived eleven phases of fixture-scale testing is that every
test corpus had distinct claims, so the duplicate path was never taken.

### What this deliberately does not do

- **It does not deduplicate the store.** The eleven occurrences remain eleven
  claims with eleven spans. Collapsing them would destroy the provenance
  structure ADR-003 establishes and would change every claim id, every Merkle
  root, and the artifact version — a far larger change with its own ADR, and not
  required to make the verdicts true.
- **It does not make duplicates into evidence of anything.** A claim appearing
  eleven times is now `INCONCLUSIVE` with eleven neighbours recorded, which is
  the honest reading: repetition in a corpus is a property of the corpus, not
  corroboration of the sentence.
- **It does not touch contradiction detection.** `_contradictions` already
  requires a polarity *flip*, so an identical-text peer can never contradict the
  subject; equal-text claims were never a false-contradiction risk. The fix is
  scoped to attestation, which is the only path that could manufacture support.

## Consequences

**The support rate collapses, and that is the finding.** 6,075 `SUPPORTED`
claims on 9,273 becomes 88. On the real 336,190-claim artifact the same change
will move the large majority of currently-`UNEXAMINED` claims to `INCONCLUSIVE`.
This is the evaluator behaving as documented for the first time at scale, and it
is the correct result: a deterministic lexical evaluator that cannot establish
entailment should almost never claim support.

**A previously untested path now runs on every corpus.** Any corpus with
repeated sentences exercises it. The regression test is deliberately built on
duplicate text, because a fixture of distinct claims cannot reach the defect.

**The auditor cannot catch this class.** Every record was well-formed. This is
recorded here because the honest limit of independent verification is now
demonstrated rather than assumed: an auditor checks that records are internally
consistent and true to their sources, and no amount of that checking
distinguishes a witness from an echo. It took reading the *verdicts* to find it.
