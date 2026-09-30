# ADR-021: the store must describe the beliefs it actually holds

**Status:** Ratified
**Date:** 2026-09-29
**Depends on:** ADR-018 (state in artifact identity), ADR-014 (independent auditor)

## The defect

ADR-018 put claim state inside the artifact identity: a `state_digest` over
every `(claim_id, state)` pair participates in the Merkle root, and a
`state_epoch` is bumped on every transition. The intent was that a version
cannot claim to be "these claims, believed" when it is really "these claims,
contradicted."

The epoch was implemented. The digest was not.

`record_artifact` is called once, at the end of `compile_corpus`. Nothing
recomputes it afterward. So an evaluated store kept advertising the beliefs
it held *before* evaluation, and the recorded identity described an artifact
that no longer existed.

Measured on the first completed full-corpus run (336,190 claims):

```
recorded:  8d87e32393b85f317ec989b16344599a61639514b8b6bc47e1bc79ff1b677d8d
actual:    7652e541773f4715e7677d43919aa14d45d2c0d5271c9419a329a454d9cd29ef
```

The independent auditor refused:

```json
"clean": false,
"state_digest_mismatch": [
  { "detail": "a state was changed without going through the store's
               transition path, so no new version was issued" }
]
```

`EXIT=1`. Everything else was clean — no orphans, no dangling edges, no span
mismatches, no unresolvable attestations. One field, and the store was
disqualified.

## Why the epoch did not catch it

This is the interesting part, and it is the same lesson ADR-018 itself
recorded.

`set_state` bumps the epoch on every transition, correctly and atomically.
On the real run the epoch reached 319,293 — exactly one per state change. The
tripwire worked.

But the tripwire and the digest answer different questions. The epoch answers
"did anything move since this witness was bound?" — a *runtime* question,
asked by a live object holding a reference. The digest answers "does this
recorded identity describe the claims in this table?" — an *audit* question,
asked by an independent process with no reference to anything.

A witness bound before evaluation was correctly refused. A witness bound
after evaluation was correctly accepted — and both witnesses were bound to
the *same version string*, because the version had not moved. The epoch said
"these are different moments"; the version said "these are the same
artifact." Both statements were true and together they were a lie about
identity.

Worse, the epoch being correct is what hid the bug. The store had a
mechanism that provably detected state movement, so the digest's absence from
the update path looked like a deliberate design choice rather than an
omission. It was an omission.

## The decision

**Re-seal after bulk belief changes, in the funnel that performs them.**

`Store.reseal()` re-derives the manifest from current store contents and
records it. Claims, evidence, sources and edges are untouched; only the
identity moves, because identity is a function of belief.

Placement is the part worth arguing about. The obvious fix is to call
`reseal()` in the compile script after `evaluate_all`. That is wrong, and it
is wrong for the same reason ADR-018 inlined its epoch bump rather than
delegating to `bump_state_epoch`: **the defect was reachable by using the API
perfectly correctly.** A correct caller had no way to know a re-seal was
owed. A fix that requires the caller to remember re-implements the bug.

So the re-seal lives in `Evaluator.evaluate_all`, guarded by `apply`, and
`Store.reseal()` remains public for writers that build artifacts by other
means. `evaluate_all(apply=False)` reads nothing and moves nothing, so it
does not re-seal.

## What this does not change

- Claim content ids still exclude state. A reassessed claim is the same claim.
- `record_artifact` still does not touch the epoch. Recording what the store
  *is* remains distinct from the beliefs changing underneath it.
- The auditor is untouched. It re-derives the digest independently and must
  keep doing so — an auditor that called the code it audits would certify
  only self-agreement.
- The manifest schema and all frozen hash-domain strings are unchanged, so
  recompiling unevaluated bytes still yields the same root.

## The re-seal must not become a way to silence the auditor

Worth stating because it is the obvious way to turn this fix into a liability:
"recompute and overwrite" is a mechanism that would also re-seal a store
whose claims had been edited behind the API's back, and the auditor would
then report clean.

That is why the auditor stays independent and the tests pin the *negative*
direction. `test_auditor_still_rejects_a_doctored_digest` writes a bogus
digest directly into the artifact row and requires a failure. A re-seal is
only trustworthy because the auditor can disagree with it.

## Tests

`tests/test_artifact_reseal.py`, 8 tests:

- an evaluated store agrees with its own artifact;
- an *un*evaluated store does too, so the invariant is not one-directional;
- evaluation demonstrably moves the digest (a constant would pass the rest);
- the epoch still bumps once per transition (ADR-018's half keeps working);
- the independent auditor exits 0 on an evaluated store;
- the auditor still exits 1 on a doctored digest;
- a witness bound before evaluation is refused after (the tripwire, and the
  reason the stale digest mattered rather than being bookkeeping);
- re-sealing changes identity and never content.

Both sabotage directions verified: removing the re-seal from `evaluate_all`
fails 2, and making `reseal` record the stale identity fails 3. The second
matters more — a fix that made re-sealing a no-op would satisfy "the auditor
is happy" while the store still misdescribed itself.

The e2e fixture needed a matching change: it adds an `UNRESOLVED` claim
*after* `evaluate_all`, which is a second legitimate path to a changed belief
set, and the fixture now re-seals when it is finished. That is the honest
model — build the artifact, then seal it — rather than sealing at every step.

## Consequence for the corpus

The evaluated corpus now has a different version than its unevaluated
counterpart, and that is the correct outcome rather than an inconvenience:
they *are* different artifacts. The unevaluated root `v1-7e506ea556f63374`
identifies 336,190 claims all `UNEXAMINED`; the evaluated one identifies the
same 336,190 claims under different beliefs.

## The pattern, fourth occurrence

ADR-014: `substr()` stopped at NUL and reported 1,434 false mismatches.
ADR-018: the digest ignored state, so retraction left the root identical.
ADR-020: `frozenset() == frozenset()` made two unrecognisable claims
"identical" and therefore contradictory.
ADR-021: a digest computed once and never refreshed let a store describe
itself as an artifact it had stopped being.

Each is a check that was correct at the moment it was written and wrong
afterwards, because nothing re-ran it. The generalisation is not "add more
checks" — the checks were all present and all correct. It is that **an
invariant which can be invalidated by a later write must be re-established by
that write, in the same funnel, or it is decoration.** The epoch was the one
that got this right in ADR-018, and the digest was the one that did not, in
the same ADR. That is the finding worth carrying forward.
