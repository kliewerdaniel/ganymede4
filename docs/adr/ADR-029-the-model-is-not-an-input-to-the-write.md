# ADR-029: The model's opinion is not an input to the write

Status: Ratified
Date: 2026-09-30
Depends on: ADR-002, ADR-004, ADR-006, ADR-007, ADR-009, ADR-023, ADR-028

## Context

ADR-028 established that a model may propose and may not write, and measured
qwen3:4b against the real 336,190-claim artifact. That run ended at
`permitted; awaiting L0 derivation` with `Runtime` holding no store.

That left the phase with a real gap. The write path was *described* but never
*exercised*, and every claim about it was an argument rather than a
measurement. Worse, "the runtime cannot write" is a statement about one class
holding no reference, which is a weaker guarantee than the one the phase
actually needs: that a belief cannot move unless independent evidence says it
should.

The obvious way to close the gap is to give the runtime a store. That is the
wrong move and the reason is worth writing down, because the wrong version is
tempting: it is one constructor parameter, and every test that exists would
still pass.

## Decision

Three separable authorities. **None is sufficient alone, and the first two can
only ever prevent a write.**

1. **The runtime's verdict.** `RunRecord.disposition is PERMITTED`. This
   means the citation check passed and the `propose` capability allowed it. It
   says nothing about truth — a model citing five real claim ids has satisfied
   it completely.

2. **A separate `derive` capability.** The applier holds its own
   `PolicyGateway` and asks for `derive` on `claims`, which is a *different
   grant* from the `propose` the runtime uses. This is the load-bearing
   decision. If permission to speak and permission to move a belief were one
   capability, a policy that wanted models to propose would have no way to
   stop them writing, and every policy would be wrong in the same direction.

3. **An independent derivation.** `Evaluator.evaluate(claim_id, apply=False)`
   re-derived from the artifact, with no knowledge that a proposal exists. The
   state written is this one.

### The model is compared against, never obeyed

`RunRecord` now carries `model_proposed_state` verbatim. The applier reads it
for one purpose: to detect disagreement.

A disagreement is reported as `derivation-disagrees` and **nothing is
written** — not the model's state, and not the derived one either.

The second half matters. Silently writing the derived state would be a system
that ignores the model, and it looks identical to a system that governs it
while telling you nothing. The disagreement is the most informative thing in
the run: it is the model's opinion and the evidence's opinion, both on the
record, neither suppressed. A system that overwrote the model silently would
also have overwritten the *reader*, because the reader would never learn the
model disagreed.

## Rationale for the refusal being terminal

A `RunRecord` that is not `PERMITTED` is never re-derived. Re-deriving it here
would be a second, weaker citation check operating on a record this module
does not own, and the second check would be the one that runs.

## What is recorded

Every applied write records an evaluation whose `meta` names the whole chain:
`authority: independent-derivation`, the `proposer`, the `proposal_id`, the
`decision_id`, the `model_proposed_state`, the `derived_state`, and the
derivation's own `evaluation_id`. The evaluation is written **before** the
transition, so a refused transition still leaves evidence of the attempt.

`authority` is the field that matters to a later reader. It is what
distinguishes "a model said so" from "the evidence said so", and collapsing
the two is precisely how a model becomes an authority without anyone deciding
that it should.

## Measurement

On a copy of the real artifact (`/tmp/p19-apply.db`, 336,190 claims), given a
genuinely `PERMITTED` record for a claim the corpus contradicts, with the model
asserting `supported`:

```
CLAIM: clm-23bed4f28fb5c1c1...   model said: supported
OUT  : derivation-disagrees  | derived: contradicted
WHY  : model proposed 'supported'; independent derivation produced
       'contradicted'. The model does not overrule evidence, and evidence
       does not overrule the model silently.
STATE: contradicted   (unchanged)
```

Artifact after the exercise: 319,293 evaluations, state distribution
identical, independent auditor clean. Nothing was written because nothing
agreed.

With the real model (`qwen3:4b`) the write path was never reached at all: the
one-character citation error made the runtime refuse at `unknown-citation`, and
the applier reported `not-permitted` without deriving. Two independent
failures, each sufficient.

## The seam is not conventional

`Applier.bind(**kwargs)` raises `TypeError` for anything other than `store`
and `gateway`. This exists so that an attempt to reach the read-only loop from
the write path is a loud failure at the call site rather than a working
arrangement discovered later. `loop.py` does not import `apply.py`, and a test
asserts it.

## Alternatives rejected

**Trust the model's state, gated by policy.** Every governance property
survives and the system is one generation from being a model with a database.
Rejected because "permitted" would then mean "true", and those are not the
same word.

**Write the derived state and record the disagreement in the report.** Tempting,
and it makes the write path look productive. Rejected: the disagreement would
live in a log rather than in the artifact, and nothing in the artifact would
show that a model and the evidence had ever disagreed. The artifact is the
thing that outlives the process.

**Filter proposal text for prompt injection, to close the ADR-028 gap.** Still
rejected on the same grounds as ADR-028. The defence here is structural — the
model cannot write, and the derived state does not come from the model at all.
A prompt-injected proposal now fails the *agreement* check, which the text
filter was standing in for.

## Consequences

- A model can no longer make a belief more or less true. It can only be right
  or be refused.
- A model that is right still cannot write alone; the derivation must agree.
- Disagreements are now first-class, countable output rather than an internal
  detail of a log line.
- A write requires two independent policy grants to exist, and either can
  refuse. This is the cost, and it is the point.

## Evidence

`tests/test_derivation.py` — 23 tests. Five sabotages, each caught by a
distinct test:

| Sabotage | Caught by |
|---|---|
| Drop the agreement gate | `test_sabotage_trusting_the_model_is_caught` |
| Write `model_state` instead of the derived state | `test_sabotage_writing_the_model_state_is_caught` |
| Skip the `derive` gateway | `test_sabotage_skipping_the_gateway_is_caught` |
| Drop the terminal-refusal guard | `test_sabotage_deriving_a_refused_proposal_is_caught` |
| Report `authority: model` | `test_the_record_names_the_model_and_claims_no_authority` |

The first two are separate defects and are tested separately. Removing the
agreement gate *ignores* the model while still writing an evidence-grounded
state; writing `model_state` *obeys* it. Only the second moves a belief to
something the derivation did not support, and the shared assertion checks the
landed state rather than the disposition, so each is caught for its own
reason.
