# ADR-011: Bound the evaluator's neighbourhood search without weakening it

Status: Ratified (2026-09-28)
Depends on: ADR-004, ADR-005, ADR-006, ADR-010

## Context

ADR-010 recorded the defect honestly and declined to fix it blind:

```python
hits = self._index.search(text, limit=len(self._index) or 1)
```

`Evaluator.evaluate` asks the retriever for **every document in the corpus** in
order to decide which claims are *neighbours* of the claim under judgement. On
the real corpus that produced 77 evaluation records out of 422,753 claims in 17
minutes. The obvious repair is a constant `limit`, and ADR-010 refused it for a
specific reason: it would convert a completeness property into a top-k
approximation, in the one component whose entire justification is that it does
not over-claim.

That refusal was right about the *naive* fix and wrong to leave the question
open, because the question turns out to have an exact answer. The right move is
not to choose a smaller `limit`. It is to notice that the evaluator is asking
the wrong question of the index.

## What the evaluator actually needs

Read the call site carefully rather than the method signature. The result is
used for exactly one thing:

```python
neighbours = [h.doc_id for h in hits if h.doc_id != claim_id]
...
investigated=f"neighbours={sorted(neighbours)}" if neighbours else "no-neighbours"
```

It is a **membership question** — "is the neighbourhood empty or not, and which
ids are in it" — not a ranking question. Nothing downstream looks at `score`.
Nothing takes a top-k. The ranking exists only because `BM25.search` returns a
ranked list by construction, and someone reached for the ranked method to ask a
set question.

That reframing is the whole fix. A set question should be answered by a set
structure.

## Decision

1. **`BM25` gains an inverted index: term → posting list of doc ids.** Built
   once in the constructor from the same tokenization, so it cannot disagree
   with the scoring index — there is one tokenizer and one document set.

2. **`BM25.matching_docs(query)` returns the set of documents sharing at least
   one query term, as a sorted tuple.** Cost is proportional to the *postings
   touched*, not to corpus size.

3. **This is exact, not approximate.** The equivalence is: *a document scores
   `> 0` if and only if it shares at least one term with the query.* Every idf
   in this implementation is strictly positive, because
   `log((N - df + 0.5)/(df + 0.5) + 1) > 0` for every legal `df ∈ [1, N]`, and
   every scoring term contributes `idf * tf * (k1 + 1) / denom` with `tf ≥ 1` and
   `denom > 0`. So a document is returned by `search` exactly when it appears in
   at least one posting list. The set is the same set — not a subset, not an
   approximation. A test asserts this equivalence against `search` on real data.

4. **The evaluator calls `matching_docs`.** Same relation, same states, same
   `investigated` string. No verdict changes, because nothing about the
   semantics changed.

5. **`search` is unchanged.** The witness still ranks, because ranking is
   genuinely what the witness needs — it returns the *best* claims to a human.
   The evaluator and the witness have different questions, and this ADR gives
   each the structure that fits it instead of making one method serve both.

## Amendment: the same fix is needed in `_peers`, and the first fix was not enough

The section above landed and was correct, and it did **not** make the evaluator
usable on the real corpus. Measured after the change: **4.8 evaluations per
second**, up from 0.075 — a 64× improvement, and still **24 hours** for 422,753
claims. I killed it again.

The cause was a second, independent full scan that the first fix never touched:

```python
def _peers(self, subject_id):
    for cid, norm in self._norms.items():   # every claim in the version
        if cid == subject_id:
            continue
        yield cid, norm
```

`_contradictions` and `_attestations` both iterate `_peers`, so every claim
scanned the entire corpus **twice**. Fixing the neighbourhood search removed one
of three full-corpus passes per claim, which is why it was 64× faster and still
infeasible.

The same reasoning applies, with the same exactness argument:

- **Contradiction** requires `norm.terms == subject.terms` with flipped
  polarity. That is an exact key lookup on the *term set*. Indexing claims by
  `frozenset(terms)` returns precisely the peers that could contradict — and
  the ones it omits were rejected by the equality test anyway.
- **Attestation** requires `subject.is_contiguous_in(norm)`, i.e. the subject's
  **sequence** appears contiguously in the peer. A contiguous n-gram must share
  its first token, so indexing by first token returns a superset of the true
  attestations, and the contiguity test still runs over that smaller set. This
  one is a *superset* filter rather than an exact one, which is the standard
  and honest shape for a subsequence-style predicate: the index can rule
  candidates out, never in.

So the evaluator gains two indexes at construction: `term-set → claims` and
`first-token → claims`. `_peers` survives for the general case but is no longer
on the hot path.

**The lesson, recorded because it nearly went unrecorded.** "The fix improved
it 64×" is not "the fix worked". A speedup is not a solution, and the
measurement that mattered was the absolute one — 24 hours remaining — not the
ratio. The first fix was correct on its own terms and still left the component
unusable, which is the normal shape of performance work and not a reason to
expect one change to be enough.

## A second lesson: the benchmark that hid the second fix

The cost-curve test used a fixture of sentences drawn from about **eleven
distinct terms**. In that corpus every BM25 posting list *is* the whole corpus,
so no index can help — `matching_docs` degenerates back to a full scan because
the candidate set genuinely is everything.

That made the evaluator look quadratic before either fix and mildly super-linear
after both, and it would have hidden the `_peers` indexes entirely. Measured on
a realistic vocabulary, the same code is flat:

| sources | degenerate fixture | realistic vocabulary |
|---|---|---|
| 200 | 0.29 ms/claim | 0.11 ms/claim |
| 400 | 0.47 | 0.11 |
| 800 | 0.86 | 0.12 |
| 1600 | — | 0.15 |

Both numbers are true. Only the second describes the system. A benchmark whose
vocabulary is degenerate measures the corpus, not the algorithm, and a curve
that refuses to flatten is usually telling you about the fixture before it tells
you about the code.

The fixture is now realistic-vocabulary, and the reason is in its docstring so
the next person does not "simplify" it back.

## Why this is not the "quick fix" ADR-010 refused

ADR-010's objection stands for a constant `limit`: that genuinely changes
semantics, because a doc ranked 401st would stop being a neighbour and a claim
would flip from `INCONCLUSIVE` to `UNRESOLVED` on an arbitrary threshold. Here
**no document is dropped that was not already dropped.** The set is provably
identical. Recall is not traded for speed; the speed comes from not sorting
documents the answer never depended on.

That distinction is the entire content of this ADR, and it is why the
`scaling` test that pins the old line is now inverted rather than deleted: it
should fail *if the unbounded search returns*, and its failure message should
point here.

## Consequences

Accepted:

- The inverted index roughly doubles the retriever's memory. For 422,753 claims
  that is real but affordable, and postings are the smaller structure relative
  to the token lists already held per document.
- `matching_docs` returns ids, not scores. If a future caller needs ranked
  neighbours for evaluation, it must ask `search` and accept the cost — which is
  the correct pressure, because that caller is asking a different question.
- The equivalence proof depends on **every idf being positive**. If a future
  revision reintroduces the floored Robertson form, `score > 0` stops being
  equivalent to "shares a term" and this becomes a bug. That dependency is
  written into `matching_docs`' docstring and asserted by a test, so the
  coupling is visible rather than latent.

Rejected:

- **A constant `limit`.** Changes semantics; the whole reason this ADR exists.
- **A Bloom filter or probabilistic filter.** Approximate, and the project does
  not approximate membership.
- **Sampling the corpus for evaluation.** Would make the artifact's epistemic
  states a function of a random seed, and determinism is non-negotiable.
- **Caching evaluation results across runs.** A cache keyed on content is
  legitimate but is derived state with an invalidation story; better built once
  the underlying pass is fast, not before.
