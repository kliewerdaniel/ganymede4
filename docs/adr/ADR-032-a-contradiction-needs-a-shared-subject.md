# ADR-032: a contradiction needs a shared subject, not a shared word

Status: Ratified
Date: 2026-10-01
Depends on: ADR-030, ADR-031
Supersedes: the `no-code` premise in ADR-030, not its decision

## Context

ADR-030 measured the contradiction population and found 2,854 subject/peer
pair rows collapse to **70 distinct pairs**. ADR-031 then declined to implement
`no-code`, because the structural detector either misses most code or destroys
a genuine contradiction.

Both ADRs assumed the 402 `CONTRADICTED` claims were, in general, findings that
happened to be noisy — 402 real verdicts polluted by code fragments. That
assumption was never tested. It was inherited from the shape of the data: the
pairs *looked* like contradictions, so they were counted as contradictions, so
the question became "how do we filter them" rather than "should they exist".

A human re-read of the 14 agent-labelled `real_contradiction` pairs found the
premise was wrong.

## The measurement

Every one of the 14 pairs was re-examined with its source URI, byte offsets,
and ~350 characters of surrounding context from the document on each side.

**Twelve of the fourteen span two different documents.**

| pair | side A | side B |
|---|---|---|
| 3 | a thread about AI sapience | *austincirclejerk*, a story about being threatened with a beer bottle |
| 11 | a comment about outsourcing writing to AI | a comment about state-actor-planned insurgencies |
| 13 | a job-search document about hiring patterns | a Neo4j/SteinBot technical writeup |

They are not contradictions. They are unrelated sentences that happen to
normalize to the same bag of words. Normalizing them:

| pair | shared term set |
|---|---|
| 3 | `{'you'}` |
| 11 | `{'point'}` |
| 13 | `{'pattern'}` |
| 5, 6 | `{'i','today'}`, `{'i','want'}` |
| 12 | `{'debate','you'}` |

**"you" contradicting "you."**

## Why: the predicate has no scope

`_contradictions` fires when `norm.terms == subject.terms` and polarity flips.
That is a set-equality test over normalized tokens, and it never asks which
document either claim came from. Across the corpus:

```
contradicted subjects                402
contradiction pair rows            2,854
pair rows spanning two documents    2,676   (93.8%)
pair rows within one document         178   ( 6.2%)
```

Every one of the 402 subjects draws its evidence from **exactly one source**, so
a document-scoping constraint is well defined and needs no fallback rule.
Thread-scoping (reddit `comments/<thread_id>/`) was measured too, and admits
*exactly the same 178 pairs* — each source is already a single comment or a
single chatgpt conversation. So this records the simpler rule and does not
carry a mechanism that changes nothing.

Across all 70 labelled pairs, 31 share two or fewer normalized terms.

## Decision

A contradiction's peer must share a source document with the subject.

`Evaluator._scope` maps every claim to the set of source ids it was extracted
from, built once per version in `_build_scope_index`. `_contradictions` now
requires `self._scope[peer] & self._scope[subject]` to be non-empty.

Three properties of this decision, stated deliberately:

- **Attestation is untouched.** It is a different relation with a different
  justification — it requires the subject's whole token sequence to appear
  contiguously in a peer, which is far stronger than set equality. Scoping it
  would be a separate change, and this ADR does not make it.
- **Fail-closed.** A claim with no evidence has an empty scope, which satisfies
  nothing. Scope unknown means contradiction not established, not established
  anyway.
- **The early return is defence in depth, not load-bearing.** The per-peer
  intersection already rejects an unscoped subject, because an empty set
  intersects nothing. Deleting the explicit empty-scope guard leaves every test
  green. It stays because it states the fail-closed intent where the decision is
  made, and because relying on a downstream filter to uphold a *stated*
  invariant is how a future refactor silently removes it.

## What this does to the corpus

```
CONTRADICTED subjects   402  ->  156
```

Classifying the 318 surviving peer-pairs against the 70 agent labels:

| agent label | surviving pairs | share |
|---|---:|---:|
| `code_fragment` | 249 | 78.3% |
| `other` | 27 | 8.5% |
| `real_contradiction` | 28 | 8.8% |
| `subset` | 14 | 4.4% |

**Scope removed the cross-document noise without destroying a single labelled
real contradiction.** The 14 real-contradiction pairs are all same-document or
unaffected; none was lost.

And the residue is the finding: **78% of what survives scoping is exactly the
code `no-code` was invented to remove.** ADR-031 spent its effort on the wrong
layer. The question was never "how do we detect code" — it was "why is
`return ''` contradicting `return None` in a different file". Scope answers
that. A code detector, at any threshold, cannot: at the safe setting it catches
4.7% of code rows, and at the broad setting it destroys a genuine contradiction
inside a string literal.

## Honest limits

- **The labels are agent-authored.** This ADR re-read them against source
  context and concluded 12 of 14 were cross-document noise; that review is
  evidenced but was not performed by the labelling guide's intended labeller.
  The 28 surviving `real_contradiction` pairs have not been human-adjudicated.
- **The count is a lower bound on suppression.** 252 claims left `CONTRADICTED`.
  `CONTRADICTED` is absorbing, so each of those needs an operator to walk it
  back. Scoping is a correctness fix, not a safe migration.
- **A genuine cross-document contradiction would now be missed.** In a corpus
  of unrelated conversations that may be the right trade; in a corpus where the
  same claim recurs across documents, it is a real loss. This ADR does not
  claim otherwise.
- **The artifact has not been rebuilt.** The 156 figure comes from re-evaluating
  the prior 402 subjects against the canonical artifact with the new rule. A
  full rebuild is required before any claim of the new state distribution.

## What it invalidates

ADR-030's `no-code` decision rested on "removes 87.7% of pair rows, destroys 0
real contradictions." Both halves are now false as stated: most of those rows
were not contradictions in the first place, and scoping removes them with a
principled rule rather than a fitted threshold. ADR-030's *decision* to leave
`no-code` unimplemented still stands — it is right, for a better reason. Its
premise does not.

ADR-031's frontier measurement is unaffected as measurement. It remains true
that indentation has no safe setting; it is simply the wrong instrument.

## Verification

`tests/test_contradiction_scope.py`, 10 tests.

Sabotage matrix, each mutation confirmed to fail the intended tests:

| mutation | caught |
|---|---|
| per-peer scope filter removed | 6 tests |
| empty-scope guard removed | none — redundant, recorded above |
| scope filter added to `_attestations` | 2 tests |
| both scope guards removed | 6 tests |

Two of these needed the tests to be repaired first, which is the more useful
result:

- The compiler silently drops some short segments ("You can run it locally."
  never becomes a claim; "You cannot run it locally." does). Two tests passed
  against the *unfixed* evaluator because their subject never existed.
  `_build_expecting` now fails loudly when an expected claim was not emitted.
- The fail-closed test named only the unscoped claim in its manifest, so it had
  no peer and could not fail for any implementation.