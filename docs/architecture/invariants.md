# Invariant × write-path matrix

Built for Phase 19, after the same failure shape appeared four times
(ADR-014, ADR-018, ADR-020, ADR-021) and a fifth and sixth turned up in a
single sitting. Every one of those was a value that was correct when written
and wrong after a later write, found by *running* the thing and reading its
output — never by a structural check. This file is the structural check.

Read it as: for every value that describes the artifact, and every path that
can change it, does that path re-establish what it invalidated?

## The values

| value | where | what invalidates it | who checks |
|---|---|---|---|
| `state_digest` | `artifact.state_digest` | any `claims.state` write | auditor `_audit_state_digest` |
| `state_epoch` | `artifact.state_epoch` | any `claims.state` write | `Witness._check_fresh` |
| `state_counts` | manifest only | any `claims.state` write | nobody (see below) |
| root / version | `artifact.root` | any `claims.state` write | auditor + witness binding |
| evaluation record | `evaluations` | a state change with no record | auditor `state_without_evaluation` |
| direct evidence | `claim_evidence` | — (never changes post-compile) | auditor orphans |
| `peer_groups` | `peer_groups` | written with its evaluation | content-addressed, self-verifying |
| witness binding | in-memory epoch | any later write | `StaleWitness` |
| decision chain head | `policy_chain` | append-only | hash chain |

## The matrix

**Y** = the path re-establishes it. **—** = the path cannot affect it.
**GAP** = the path changes the value without re-establishing it.

| write path | `state_digest` | `state_epoch` | `state_counts` | evaluation record | evidence | verdict |
|---|---|---|---|---|---|---|
| `compile_corpus` | **Y** `compiler.py:319` `record_artifact` | — (records, doesn't move) | **Y** `compiler.py:298-313` | — | **Y** writes it | ok |
| `Store.set_state` | **Y** (sets `seal_pending`) | **Y** inlined | **Y** lazily, on read/close (ADR-022) | **GAP** — no evaluation (ADR-023) | `test_write_sequence.py` | gap asserted, not tolerated |
| `Evaluator.evaluate_all` | **Y** `evaluator.py:460` | **Y** via `set_state` ×N | **Y** via reseal | **Y** `evaluator.py:417` | — | ok (ADR-021) |
| `Reviser.revise` | **Y** `revision.py:272` | **Y** `revision.py:271` | **Y** via reseal | **Y** `revision.py:232` | — | ok (ADR-022) |
| `Reviser._invalidate` | via `revise` | via `revise` | via `revise` | via `revise` | — | bulk, sealed once at the end |
| `Store.reseal` | **Y** (is the mechanism) | — (must not move) | **Y** | — | — | ok (ADR-021) |
| `Store.add_claim` | **GAP** | — (new claim, no state move) | **GAP** | — | **Y** | **open** |
| `scripts/compile_corpus.py` | via evaluator | via evaluator | via evaluator | via evaluator | — | ok |

## What the matrix found

### 1. `Reviser` was missing both invariants (fixed, ADR-022)

`Reviser` (ADR-017) predates `set_state` as the single funnel for belief
changes. `_invalidate` wrote state with a raw `UPDATE`, so it bumped **neither**
the epoch nor the digest. Verified on the real corpus: one revision moved a
claim, the version did not change, the witness tripwire did not fire, and the
store failed its own audit.

Epoch evidence from the real artifact, which is what makes this concrete:

```
before revision:  319293
after set_state:  319294   (+1, from my own retraction)
after revise:     319294   (+0, from the invalidation it performed)
```

### 2. Invalidation wrote no evaluation record (fixed, ADR-022)

Sharper than the stale-value shape: this value was never written at all.
ADR-006 requires an evaluation behind every state change. The auditor caught
it as both `orphans` and `state_without_evaluation` on a real revision.

The reason it had never surfaced: **all 16,897 `DERIVED` claims have zero
evaluations**, and that is legal only because the auditor exempts the
`derived` *state*. A derived claim is evidence-free by *provenance*
permanently — it rests on `DERIVED_FROM` edges (ADR-004). The moment revision
moves one, the state exemption stops applying and the claim is unexplained.

### 3. The auditor's orphan rule keyed on the wrong thing (fixed, ADR-022)

`EVIDENCE_FREE_OK` exempts claims by `state`. That is a proxy for "has no
direct evidence", and it breaks the moment a derived claim changes state —
its provenance is intact, but the exemption no longer names it.

The rule now keys on provenance: a claim is an orphan if its state promises
evidence **and** it has neither `claim_evidence` nor `DERIVED_FROM` edges.

This is the most dangerous fix in the project, because the auditor is what
catches everything else, so weakening it to admit a defect is a fix that
removes the capacity to detect defects. It is therefore sabotage-tested in
both directions, and the negative test found a pre-existing adversarial test
(`test_detects_an_evidence_bearing_claim_with_no_evidence`) that the naive
version of the change would have broken.

### 4. `set_state` alone: SEALED (ADR-022), decision record still OPEN (ADR-023)

**This entry was wrong when first written.** It claimed the digest gap was
acceptable because "no current caller performs a `set_state`-only bulk write".
The randomized sequence test is precisely such a caller, and it failed on step
2 of seed 42: a *single* legal `set_state` left the store failing its own
audit. The reasoning was about batches; the defect was in a single call.

**Fixed (ADR-022).** The store now records that it is mid-update rather than
pretending otherwise:

- `artifact.seal_pending` is set by `set_state`, cleared by `reseal`.
- `recorded_artifact()` flushes before reading. It is the question "what do you
  currently hold?"; answering it with a digest known to be stale would make it
  the one method where the store lies by default.
- `close()` flushes, so `with Store(...)` cannot produce an artifact that fails
  its own audit.
- The independent auditor reports `seal_pending` as unclean, so a store
  abandoned mid-update is visible rather than looking clean.

The flag is persisted, not in memory, precisely so that a crash cannot be
mistaken for a clean store.

**Still open (ADR-023).** `set_state` writes no evaluation, so moving a claim
into an asserting state leaves nothing deciding it. The auditor has always
caught this as `state_without_evaluation`; the write path does not prevent it.
The fix was written and measured, and broke 19 existing tests that encode the
same defect. See ADR-022 for why enforcement was removed rather than landed
half-migrated.

### 5. `add_claim` does not reseal (OPEN)

Adding a claim changes the belief set — a new `(claim_id, state)` pair enters
the digest — and `add_claim` neither bumps the epoch nor re-seals. The e2e
fixture hit exactly this and needed an explicit `reseal()` at the end.

Correct for a compile (which seals once at the end) and wrong for an
incremental writer. Same batching question as #4, so it is recorded here
rather than fixed separately.

### 6. `state_counts` is stored only in the manifest (OPEN)

`state_counts` lives on the `Manifest`, not the `artifact` row, so after
evaluation there is no stored distribution for a reopened database to compare
against. The digest covers the same information — it hashes every
`(claim_id, state)` — so this is a redundancy gap, not a correctness one.
Noted, not fixed.

## The randomized-sequence test

The matrix is a claim about code that can drift. `tests/test_write_sequence.py`
is the executable version: a seeded, stdlib-only `random` sequence of legal
operations (compile, evaluate, revise, set_state, add claim, re-seal) after
each of which the **independent auditor** must report clean.

It is seeded so a failure is reproducible, and it is sabotage-tested — with
any one re-seal disabled, the sequence test fails. A randomized test that
cannot fail is worse than no test, so proving it *can* find something is part
of the deliverable.

## The generalisation

Six occurrences now:

| | shape |
|---|---|
| ADR-014 | `substr()` stopped at NUL → 1,434 false mismatches |
| ADR-018 | digest ignored state → retraction left the root identical |
| ADR-020 | `frozenset() == frozenset()` → two unrecognisable claims "identical" |
| ADR-021 | digest computed once, never refreshed → store described stale beliefs |
| ADR-022a | `Reviser` bumped neither invariant |
| ADR-022b | `Reviser` wrote no evaluation record — the value was never written |

The first four are *stale values*: correct, then invalidated. The last is a
*missing value*. Same family, different severity.

What generalises is not "add more checks" — all of these checks were present
and correct. It is that **an invariant a later write can invalidate must be
re-established by that write, in the same funnel, and a state change must
leave a record of what decided it.** Anything else is decoration that happens
to be true today.

### 5. The sixth occurrence: a value that was never written

Occurrences 1-5 were all values that went **stale**. This one was never
**written**.

Fixing the `Reviser` epoch and digest left the real artifact still failing:

```
clean: False
orphans: 1
state_without_evaluation: 1
```

A corpus measurement explains why it had stayed invisible:

```
DERIVED claims:                         16,897
DERIVED claims with evaluations:             0
DERIVED claims with claim_evidence:          0
DERIVED claims with DERIVED_FROM edges: 16,897
Non-derived claims with no evidence:         0
```

Every mined heuristic is evidence-free by *provenance*, permanently -- its
support is the set of `DERIVED_FROM` edges, not a text span. That is legal
only because the auditor exempted the `derived` **state**. Move one to
`invalidated` and the exemption stops applying.

Two consequences, both fixed (ADR-022):

1. `Reviser` now writes an `invalidated` evaluation for every claim it moves,
   naming both the original trigger and the direct dependency that caused the
   move.
2. The auditor's orphan rule now keys on **provenance** (direct evidence *or* a
   `DERIVED_FROM` edge) rather than on a mutable state. A checker keyed on state
   was one state transition away from reporting 16,897 false orphans.

### 6. The seventh occurrence: still open (ADR-023)

`set_state` writes no evaluation. Moving a claim to `supported` leaves the
store believing something nothing decided. Found by the sequence test, fix
written, enforcement deliberately not landed. See ADR-022.

## Verification status

Every "proven" claim in this matrix maps to a passing test. Specifically:

| claim | test |
|---|---|
| compile output is clean | `test_write_sequence.py` (after `compile`) |
| a legal sequence of mutations stays clean apart from ADR-023 | `test_write_sequence.py` (5 seeds) |
| `Reviser` maintains epoch and digest | `test_bulk_write_invariants.py` (11 tests) |
| a derived claim invalidated is not a false orphan | `test_auditor_orphan_rule.py` (4 tests) |
| a genuine orphan is still caught | `test_auditor_orphan_rule.py` |
| a planted state mutation is detected | `test_e2e_slice.py` (ADR-021) |

**On the real 336,190-claim artifact** (`/tmp/adr022-real.db`, ADR-022
revision applied to a copy of the re-sealed baseline):

```
before: v1-47175b83c6232433   epoch 319293
invalidated: 7   refused: 0
after:  v1-30a0b947c2219726   epoch 319295
derived claim state: derived -> invalidated

clean: True   exit 0   audit 7.8s
orphans 0   state_without_evaluation 0   state_digest_mismatch 0
stale_dependents 0   span_mismatches 0   unresolvable_attestation 0
```

Tamper check: one planted state mutation in the result gives
`clean: False`, `exit 1`, `state_digest_mismatch: 1`.

The sequence test is a real test, not a rubber stamp: with the bulk re-seal
sabotaged it fails, and there is a dedicated
`test_the_sequence_test_can_actually_fail` asserting exactly that.
