# ADR-018 — A claim's state is part of what the artifact says

Status: Ratified
Date: 2026-09-29
Depends on: ADR-004, ADR-005, ADR-017

## The hole

ADR-017 gave the system belief revision. It did not give it a *new version*.

The project separates identity from provenance throughout: a claim's content
id is computed from what the claim **says**, and `state` is deliberately not
hashed, so a claim keeps its id as it moves `UNEXAMINED → SUPPORTED →
CONTRADICTED`. That is correct and it is not what is wrong here.

What is wrong is one level up. `build_manifest` roots over `schema:`,
`edges:`, and the content leaves — source, claim, evidence, heuristic ids.
`state_counts` is *recorded* in the manifest and **excluded from the root**.
`MANIFEST_SCHEMA_VERSION` is 1 and there is no `state:` leaf.

So the artifact version does not move when the artifact's beliefs move. The
same argument the manifest already makes about `edge_count` applies verbatim to
state, and it was not applied:

> Claim ids alone do not capture relationships: two artifacts with identical
> claim sets but different SUPPORTED/CONTRADICTS edges are different artifacts,
> and a version id that could not tell them apart would be lying about what it
> identifies.

Two artifacts whose 336,190 claims sit in *completely different epistemic
states* are different artifacts, and `v1-177a5ee5afaf63e8` cannot tell them
apart.

### Measured, on the real corpus

```
root BEFORE revision: 177a5ee5afaf63e8fc40088764fa8287
true state_counts after: {unexamined: 319292, derived: 16896,
                          retracted: 1, invalidated: 1}
root AFTER  revision: 177a5ee5afaf63e8fc40088764fa8287
ROOTS EQUAL: True
```

Two claims changed state. The root is byte-identical.

### The consequence is worse than a stale version string

ADR-005's witness is *defined* as a version-bound view. Its whole guarantee is
that it cannot describe an artifact other than the one it was built against.
That guarantee is enforced by `_check_version`, which tests membership in the
manifest's leaf set — and the leaf set is unchanged, because a state change
does not add or remove a claim.

So a witness built before a revision keeps answering afterwards, and reports
the state distribution that was true when it was constructed:

```
witness version      : v1-177a5ee5afaf63e8
witness state_counts : {unexamined: 319293, derived: 16897}
TRUTH                : {unexamined: 319292, derived: 16896,
                        retracted: 1, invalidated: 1}
```

It is asked what it contains and answers with numbers that are false, under a
version id that certifies them. A caller has no way to detect this: every
field the witness exposes is internally consistent, and the one field that
would have caught it — the root — cannot change.

This is the same class of defect ADR-014 found in the auditor, one layer up: a
check that looks like it is verifying something and is not.

## The decision

Three parts, because the identity fix alone is not sufficient and the drift
check alone is not sufficient.

### 1. `state:` leaves enter the root

`build_manifest` gains a `state_digest`: a SHA-256 over every
`(claim_id, state)` pair in sorted claim-id order, NUL-separated and
domain-prefixed. The digest is a root leaf, so a state change is a new
artifact version.

The digest rather than 336,190 individual leaves: it is one leaf instead of
336,190, it is order-independent by construction, and it is cheap to verify
(0.59s on the real corpus, versus 336,190 tree insertions).

`MANIFEST_SCHEMA_VERSION` stays at **1**. The state of a claim is not a new
*kind* of leaf — the manifest has always carried `state_counts` — so this
corrects a root that was under-specified rather than introducing an
incompatible meaning. A root computed under the old rule is not comparable to
one computed under the new rule, which is a real cost of this ADR and is
accepted: the corpus is recompiled.

**This closes the pipeline arrow.** `belief revision → next version` is now
literal. Before this ADR the reviser produced new beliefs and no new version,
so the arrow pointed at nothing — the same defect as ADR-017's, one component
down.

### 2. The store keeps a `state_epoch`, and the witness checks it

Fixing the root alone leaves a window: between a revision and the next
recompile, a witness bound to the *old* version is still serving a store that
has moved. It is still lying, just about a version that is now honestly
self-inconsistent.

So the store carries a monotonic `state_epoch` in a new `artifact` table,
incremented by every `set_state` — the single funnel through which epistemic
transitions reach the database. The witness records the epoch at construction
and re-reads it (one indexed row) before every `ask()` and `boundary()`. A
moved epoch raises `StaleWitness`.

The epoch is a cheap tripwire, not a proof. It is a counter in the same
database it guards, so it is bypassable by anyone writing SQL directly — which
is exactly why part 3 exists.

**Why not re-derive the digest on every `ask()`?** Measured: 0.59s per digest
over 336,190 claims. A witness that took half a second per question would not
be used, and a guard that is disabled under load is not a guard. The epoch is
O(1); the digest is O(n) and runs at manifest build.

### 3. The auditor re-derives the digest independently

`audit_provenance.py` recomputes the `state_digest` from the `claims` table
and compares it to the value recorded in `artifact`. This is the check that
catches a direct SQL `UPDATE claims SET state=...` that bypassed `set_state`
and therefore never bumped the epoch.

The auditor still imports nothing from the package. It computes the digest
from the stored rows with its own implementation, which is the only way the
comparison means anything.

## Why fail closed

A witness whose epoch has moved raises rather than answering with a
best-effort refresh. Refreshing would be a silent repair: the caller asked a
question of a specific version and would receive an answer about a different
one, with no indication that anything changed. Raising is the honest response
and it forces the caller to decide whether re-binding is acceptable — which is
a decision the caller is better placed to make than the witness is.

## What this does not do

- It does not make the state transition itself better. ADR-017's downward-only
  conservatism is unchanged.
- It does not re-root on read. A witness does not recompute a manifest; it is
  handed one.
- It does not version the *database file*. Two stores at the same root hold the
  same artifact; the file layout is not identity.
- It does not address a store mutated by direct SQL, except to make the
  auditor catch it. Part 3 is detection after the fact, not prevention.

## Alternatives rejected

**Hash each claim's state into its own content id.** Then a state change
produces a new claim id, and the leaf set changes, and the root moves for free.
Rejected: it destroys the id's meaning. A claim's id is the hash of what it
says; an id that changes when a *different component* reassesses it is no
longer an identity, and every citation to it breaks. This is why `state` is
excluded from the claim hash in the first place, and the fix belongs at the
artifact layer where the reassessment actually happened.

**Recompute the full root inside the witness on every question.** Correct and
unusable at 0.59s per call. Rejected on cost, with the digest check retained
where it is affordable.

**Let the witness silently re-read state and answer anyway.** Rejected: it
converts a detectable lie into an undetectable one. The caller's question was
about a version; answering about a different version without saying so is the
failure mode this whole project is built against.

**Persist a full version history with parent links.** That is the right long-
term design and it is not needed to fix the hole. The `artifact` table is
single-row and records *what this store currently is*, not how it got here.
Recorded here as known-insufficient rather than pretended-complete.
