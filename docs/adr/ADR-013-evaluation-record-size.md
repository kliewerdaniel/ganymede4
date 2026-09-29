# ADR-013 — An evaluation records a verdict's identity, not its neighbourhood

Status: Ratified (2026-09-29)
Depends on: ADR-006 (evaluator), ADR-011 (evaluator scaling), ADR-012 (claim quality)

## Context

The full-corpus run of Phase 9 died at 4% completion:

```
sqlite3.OperationalError: database or disk is full
```

The database was **20.2 GB**, holding 18,930 sources and 422,753 claims, of which
only **17,691 evaluations** had been written. The machine's data volume went to
100% with 117 MiB free. A 20 GB artifact was deleted to recover the disk.

Measured, on a 400-document slice of the real corpus:

| stage | size | per-claim |
|---|---|---|
| after compile (10,148 claims) | 18.2 MB | 1,792 B |
| after `evaluate_all()` | 610.3 MB | — |
| **growth** | **592.1 MB** | **58,345 B / evaluation** |

Compile scales linearly and cheaply. The evaluator added **58 KB per record**.

This is not ADR-011's defect. ADR-011 fixed the cost of *finding* neighbours.
The neighbours were found efficiently and then written down quadratically —
which is how you get a 20 GB file and a green test suite.

## The actual cause

It was not where I first looked, and the wrong answer is worth recording.

The first hypothesis was `attested_by`: `_attestations()` returns an equivalence
class, and the class was serialized into every member's row. That is real and it
is quadratic, so it was fixed — see decision 1. Measured after that fix:

```
evaluations 10,148   groups 7,053   growth 593.6 MB
```

**No improvement.** 7,053 distinct groups for 10,148 evaluations means the sets
are almost all different, so sharing them saves nothing. The hypothesis was
wrong.

The measurement that broke it open was already in the first run: the *largest*
`attested_by` value was 8,095 bytes while the *average row* was 58,345. The
attestation sets could not account for 86% of the bytes. Something else was.

It was `meta["investigation"]` on the inconclusive path:

```python
investigated=f"neighbours={sorted(neighbours)}" if neighbours else "no-neighbours"
```

Every lexically-matched claim id, as a Python set repr, inlined into a string,
on the record for the ~73% of claims that are `INCONCLUSIVE`. A 400-document
slice already produced neighbourhoods in the hundreds. This one line was ~98% of
the database.

## The invariant being violated

An evaluation record answers *"what established this claim, and can that be
checked?"* It does that by naming its evidence.

But the neighbourhood of an `INCONCLUSIVE` claim is not the evidence. The
evidence is the *finding*: that a search ran, under a named method, over a named
version, and found material that did not establish a conclusion. The size of
that neighbourhood is a property of the corpus, not a fact about the claim. A
claim's epistemic standing does not get stronger because 4,000 documents happen
to share a word with it — that is precisely why the verdict is `INCONCLUSIVE`
and not `ATTESTED`.

Storing the set inline confuses "here is what I found" with "here is what
proves it", and pays linearly-per-claim for a fact that is already recoverable
from the index.

## Decision

1. **Attestation sets are content-addressed and stored once.** A new
   `attestation_groups` table holds the canonical member list under an `agr-`
   id; `evaluations.attested_by` stores that id. The read API is unchanged:
   `Store.evaluations_for()` hydrates members back, so callers still see claim
   ids. On the real corpus this turns N copies of a set into ~0.7N — small here,
   but it removes a genuine quadratic term rather than leaving it to compound on
   a corpus with larger classes.

2. **A neighbourhood is recorded by count and digest, not by membership.**
   `neighbours=<n>;neighbour_digest=<16 hex>`, where the digest is over the
   newline-joined sorted ids. Constant-size, and independently recomputable from
   the index, so the record stays *checkable* rather than merely smaller.

3. **The neighbourhood is never truncated.** Storing the first N ids would make
   "inconclusive with 4,000 neighbours" indistinguishable from "inconclusive
   with 50" in the one field reporting it. If the storage cost is too high, the
   answer is a cheaper representation, never a shorter list. This is the
   project's own rule — evidence magnitude ≠ sufficiency — applied to the
   system's own records.

4. **An unresolvable attestation group is a hard error, not an empty list.**
   Returning `attested_by=()` would make a `SUPPORTED` claim read as
   unattested. A crash is visible; a claim that quietly lost its evidence is
   not.

5. **The auditor resolves the indirection.** An evaluation whose group is absent
   is a provenance failure and exits non-zero.

## Result

| | before | after |
|---|---|---|
| bytes per evaluation | 58,345 | **1,049** |
| 400-doc slice after `evaluate_all()` | 610.3 MB | **28.8 MB** |
| projected 422,753-claim corpus | ~24 GB, filled the disk | **~1.2 GB** |

Scaling measured across 200 / 400 / 800 documents: 768 → 1,049 → 918 bytes per
claim, i.e. **0.88×–1.37× per doubling, not the ≥2× of a quadratic**.

## Alternatives rejected

- **Store a count only, no digest.** Cheaper, but then a recomputed
  neighbourhood with different *membership* at the same size is undetectable.
  The digest is what keeps the record checkable.
- **Drop the neighbourhood from inconclusive records entirely.** Smallest
  possible file, and it makes "I searched and found nothing" indistinguishable
  from "I did not search" — which is the exact defect ADR-002 closed for
  absence, reopened for evidence.
- **Recompute neighbourhoods at read time from the index.** Avoids storage and
  reintroduces ADR-011's per-claim full scan, with the added defect that the
  evidence would depend on retrieval code rather than on a record.
- **Raise the disk quota.** Treats a 56× write amplification as a capacity
  problem. It is a representation problem.
