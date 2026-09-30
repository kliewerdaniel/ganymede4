# ADR-022: A bulk belief change must re-establish epoch, digest, and decision provenance

Status: Ratified
Date: 2026-09-30
Depends on: ADR-006, ADR-014, ADR-018, ADR-021

## Concern

Phase 19 asked a single question: can a later write invalidate an earlier
invariant? The answer was yes, three times over, and each time the store was
left describing beliefs it did not hold.

## Two occurrences, found by inspection

### The fifth: `Reviser` maintained neither invariant

`Reviser._invalidate` wrote claim state with a raw statement:

```sql
UPDATE claims SET state = ? WHERE id = ?
```

`Reviser` predates ADR-018 and predates `set_state` as the single funnel for
belief changes. It therefore moved 141,910 claims' worth of beliefs without
touching `state_epoch` and without re-sealing `state_digest`.

Measured on the real 336,190-claim artifact, starting from a clean re-sealed
store:

| | before | after `set_state` (my own retraction) | after `Reviser.revise` |
|---|---|---|---|
| `state_epoch` | 319293 | 319294 | 319294 |

The revision moved a claim and contributed nothing to the epoch. The recorded
version did not change, the witness tripwire did not fire, and:

```
clean: False
exit: 1
state_digest_mismatch: 1
stale_dependents: 1
```

### The sixth: invalidation wrote no decision record at all

With the epoch and digest repaired, the store still failed:

```
clean: False
state_digest_mismatch: 0
stale_dependents:     0
orphans:             1
state_without_evaluation: 1
```

This is a different defect from the fifth, and worth separating clearly. The
fifth was a value that went **stale**. This one was a value that was **never
written**.

A corpus measurement explains why it was invisible for so long:

```
DERIVED claims:                         16,897
DERIVED claims with evaluations:             0
DERIVED claims with claim_evidence:          0
DERIVED claims with DERIVED_FROM edges: 16,897
Non-derived claims with no evidence:         0
```

Every mined heuristic is evidence-free by *provenance*, permanently -- its
support is the set of `DERIVED_FROM` edges, not a text span. That is legal
only because the independent auditor exempted the `derived` **state**. Move one
of those claims to `invalidated` and the exemption stops applying, exposing
both `state_without_evaluation` and a false `orphan`.

ADR-006 requires an evaluation behind every state change. The invalidation
path had been writing none.

## Decision

### One bump, one seal, per bulk operation

`Reviser.revise` now re-establishes both invariants at the end of the walk:

```python
if invalidated:
    self._store.bump_state_epoch()
    self._store.reseal()
```

Not one bump and one seal per invalidated claim. Revision can move a transitive
closure of roughly 142,000 claims; `set_state` per dependent would be 142,000
commits, and `reseal()` per dependent would be 142,000 full store scans.

The invariant being protected is *"the digest describes the beliefs this call
left behind"*, and one seal at the end of the operation establishes exactly
that. The epoch is a tripwire, not a ledger: the question it answers is "did
anything move", and the honest answer after N moves is one bump.

A no-op revision moves nothing, so it bumps nothing and seals nothing. Retrying
a completed revision is therefore idempotent rather than merely harmless.

### An invalidation is itself a decision

Every claim the traversal actually moves gets an evaluation:

```
relation:    "invalidated"
method:      "reviser.propagate"
meta:
  trigger:          the original terminal claim
  invalidated_from: the direct dependency that caused this move
  trigger_state:    the trigger's terminal state
```

Both identifiers are recorded because they answer different questions. The
trigger is what the belief originally rested on. The direct dependency is the
step in the chain that actually moved this claim, and a 7-hop closure needs it
to be legible at all.

### The auditor's orphan rule keys on provenance, not state

`EVIDENCE_FREE_OK` was a list of *states* permitted to carry no evidence. That
was wrong for mined heuristics, which are permanently evidence-free and are
exempt only while they remain `derived`.

The rule is now: a claim that requires provenance passes if it has direct
`claim_evidence` **or** at least one outgoing `DERIVED_FROM` edge. A claim with
neither is still an orphan.

This is the most dangerous change in the phase, because the auditor is what
catches everything else. It was sabotaged in both directions:

| auditor variant | result |
|---|---|
| exempt everything | 2 tests fail, including the pre-existing adversarial `test_detects_an_evidence_bearing_claim_with_no_evidence` |
| original state-only exemption | 3 tests fail, including both `test_auditor_clean_after_revision` cases |

The auditor still imports nothing from `ganymede4`. `JUSTIFY_REQUIRED` in
`store.py` is deliberately not imported by it either: a checker that shares the
implementation's idea of what is allowed is not a check.

## Consequence: the store can be mid-update, and now says so

Fixing the fifth occurrence exposed the underlying structural gap. The
invariant is *end-of-operation*: a store that has just moved a belief and not
yet re-derived its digest is legitimately inconsistent, and the lazy seal makes
that state ordinary rather than exceptional.

So the store records it. A new column:

```sql
seal_pending INTEGER NOT NULL DEFAULT 0
```

`set_state` sets it. `reseal` clears it. `recorded_artifact()` flushes before
reading -- it is the question "what do you currently hold?", and answering it
with a digest known to be stale would make that the one method where the store
lies by default. `close()` flushes, so ordinary `with Store(...)` use does not
produce an artifact that fails its own audit.

The flag is persisted rather than held in memory so that a store abandoned
mid-update is *visible*: the independent auditor reads the column and refuses
it, rather than one that crashed while looking clean.

Existing databases gain the column through an `ALTER TABLE` guarded by
`PRAGMA table_info`. The real corpus database is 845 MB and must not need a
rebuild to acquire a column. `CREATE TABLE IF NOT EXISTS` silently does nothing
for a table that already exists, which is the failure mode worth naming.

### Cost

One extra `UPDATE` per `set_state`. The evaluator drives `set_state` 319,293
times on the real corpus. Measured as negligible: the full suite ran in 18.9s,
and the full-corpus artifact audit still completes in 7.8s.

## Verification

On the real 336,190-claim artifact, retracting one source claim whose closure
reaches a derived heuristic:

```
before: v1-47175b83c6232433   epoch 319293
derived claim: clm-7adf9022782a1ce36c063703ac81202ac4153fc7b2820c4d2bdc992b09ed8769
invalidated: 7   refused: 0
after:  v1-30a0b947c2219726   epoch 319295
derived claim state: derived -> invalidated

clean: True     exit: 0     audit: 7.8s
orphans: 0   state_without_evaluation: 0   state_digest_mismatch: 0
stale_dependents: 0   span_mismatches: 0   unresolvable_attestation: 0
seal_pending: False
```

Planting a single state mutation in the resulting database:

```
clean: False   exit: 1   state_digest_mismatch: 1
```

### Sabotage

| sabotage | result |
|---|---|
| `seal_pending` never set | 5 sequence tests fail |
| `recorded_artifact` does not flush | 5 sequence tests fail |
| `Reviser.reseal()` removed | digest, clean-audit, and structural tests fail |
| `bump_state_epoch()` removed | epoch test and structural test fail |
| `add_evaluation` in `_invalidate` removed | invalidation-record tests fail |
| `Reviser` per-claim instead of bulk | bulk-write test fails |

## The randomized sequence test

`tests/test_write_sequence.py` is the executable form of the invariant matrix:
five seeded sequences of legal public-API mutations, with the independent
auditor run as a separate process after every operation.

It is what found the two defects below, and it is the reason this ADR exists.

## Open: ADR-023, the same shape one level up

The sequence test then found a seventh occurrence, which this ADR does **not**
fix.

`set_state` can move a claim into a state that asserts something --
`supported`, `validated`, `contradicted`, `retracted`, `contested`,
`superseded`, `invalidated` -- with no evaluation behind it. The independent
auditor has always reported that as `state_without_evaluation`; the write path
never prevented it.

The fix was written and measured. A `JUSTIFY_REQUIRED` set in `store.py`, a
`justified_by` parameter on `set_state` that names the deciding evaluation and
is validated to belong to that claim, and the evaluator passing the record it
had just written. It **broke 19 existing tests**, because those tests
transition claims into asserting states with no record -- which is the same
defect, sitting in the test suite.

Enforcement was therefore removed before commit rather than landed
half-migrated. A tree where the invariant is claimed in a docstring and absent
from the run is worse than a tree that names the gap.

What is shipped instead:

- `JUSTIFY_REQUIRED` exists with a comment stating plainly that it is **not**
  yet enforced.
- The evaluator passes `justified_by` on every transition it makes. That
  direction is free and correct, so it ships now.
- The sequence test asserts that the ADR-023 gap, when present, is the **only**
  finding -- any other finding fails. When the gap is closed the test can be
  tightened to demand clean.

The gap is real, reproducible on demand, and unenforced.
