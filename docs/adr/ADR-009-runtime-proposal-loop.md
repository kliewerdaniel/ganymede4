# ADR-009: The runtime proposes; it cannot write

Status: Ratified (2026-09-28)
Depends on: ADR-002, ADR-006, ADR-007, ADR-008

## Context

`target-architecture.md` §2 states the rule that turns a stack into a system:

> **Nothing in L4 can write to L1.** Every mutation is a typed `Proposal`
> evaluated by L3 against schema and policy, recorded in the ledger, and — where
> it changes epistemic state — re-derived by L0's lifecycle rules.

Everything built so far has been a component that reads, judges, or records. The
runtime is the first thing that *wants* something, and this is the first place
the project's central claim can be tested rather than asserted. The claim is
that the model is a probabilistic component inside a governed runtime, never the
intelligence system and never the authority. Every phase so far has been
consistent with that claim. None has been capable of violating it.

A runtime that can write directly to the store is not a runtime that consults a
policy — it is a runtime whose policy is advisory, and the difference is not
visible until the one time it matters. A read-only runtime that accumulates a
list of proposed mutations and hands them to L3 is a different object: it
cannot bypass the gate because it never holds the capability the gate controls.

There is a second, subtler temptation. Once a runtime exists, the obvious
design is for it to be useful: the runtime *knows* the corpus contradicts
itself, so the runtime resolves the contradiction, and reports the answer. That
is the failure this ADR exists to make impossible. A runtime that resolves
contradictions is a runtime with a private evaluator, and its answers would be
indistinguishable from evaluated ones — same type, same store, no mark on them.
An operator could not tell which claims passed through evaluation and which
were invented by something that wanted to be helpful.

So the runtime gets no evaluator, and no shortcut. It proposes, L3 disposes.

## Decision

1. **The runtime holds a read-only witness and a proposal sink. It has no
   reference to the `Store`.** Not "does not call" — does not hold. The
   capability is absent rather than withheld, so there is no call site to
   audit and no future refactor that quietly adds one. A test asserts the
   runtime module's AST imports no write path from the store.

2. **Every mutation is a typed `Proposal`, and a proposal is inert data.**
   A proposal carries a proposed claim, a proposed state, the question that
   prompted it, and the claim ids it cites. It cannot be applied, only
   submitted. `Proposal.apply` does not exist — the absence is the API.

3. **L3 disposes, and the disposition is a `Decision`.** The gateway evaluates
   each proposal exactly as it evaluates any other request. The runtime does
   not get a privileged path, and a proposal is not a capability: a runtime that
   is allowed to read the corpus is not thereby allowed to write claims about
   it.

4. **A proposal that asserts a state requires attestation that already exists.**
   The runtime may propose "this claim is SUPPORTED" only by citing claim ids
   that are already in the store. It may not propose the attesting claim in the
   same breath. Self-attestation in a single proposal is the exact shape of a
   model marking its own homework, so it is structurally impossible: the cited
   ids are resolved against the store *before* the proposal is evaluated, and
   an unknown id fails closed.

5. **The loop is bounded by an explicit step budget that fails closed.**
   Termination is not left to the proposing component. A budget of zero or a
   negative budget is a configuration error, not an empty run — it would
   otherwise look identical to "the model had nothing to say", which is a
   different incident.

6. **Every step is recorded, including refusals and exhaustion.** A
   `RunRecord` per proposal, content-addressed, carrying the disposition.
   A run that produced no proposals still produces a record; silence is
   data, and data that is not recorded cannot be distinguished from a run that
   crashed before it began.

7. **The model is not consulted in this phase.** A `Proposer` protocol is the
   seam where one will be installed. The default proposer is a deterministic
   rule-based one, so the runtime's behaviour is reproducible and the tests
   assert governance properties rather than model behaviour. When a model is
   added it will fill this interface and inherit every constraint below,
   because none of the constraints live in the proposer.

## Why the proposer is not where the safety lives

This is the part worth stating explicitly, because getting it backwards is the
usual way projects like this go wrong. If the safety lived in the proposer —
a prompt saying "do not fabricate" — then swapping in a different proposer
would change the system's guarantees, and a model update would be a governance
change. Nobody would notice, because the diff would be a string.

Here the proposer is arbitrary and the guarantees are not. The loop applies the
same rules to any object implementing `Proposer`. A test installs a proposer
that returns deliberately malicious proposals — inventing claim ids, citing
themselves, proposing a state transition the corpus does not support — and
asserts every one is refused. That test is the actual content of this ADR. If
it can be made to pass with a benevolent proposer and fail with a malicious
one, the safety is in the proposer and this ADR has failed.

## Consequences

Accepted:

- The runtime is not useful yet. It proposes almost nothing, and what it
  proposes is usually refused. That is correct at this stage: a runtime whose
  first job is to be *checkable* has to be checkable while it is still weak.
- Proposal ids are content-addressed, so an identical proposal submitted twice
  is the same proposal. Retries are free and deduplication is structural.
- The store is not reachable from the runtime, so a run cannot read the
  evidence for its own proposal. It cites ids and the substrate resolves them.

Rejected:

- **A `runtime.write(claim)`.** Every convenience here is a hole.
- **Letting the runtime resolve contradictions it observed.** It proposes the
  resolution; the evaluator decides. A runtime that resolves its own findings
  produces unmarked claims that look evaluated.
- **A soft step budget that clamps to zero.** A clamp makes misconfiguration
  indistinguishable from an empty run.
- **Persisting proposals to the store directly.** Proposals go to the gateway
  first. A proposal row written before authorization is a row nobody decided on.

## Boundaries

- No model, no inference, no network. The `Proposer` protocol is a seam, not an
  implementation.
- The A2A edge (L5) and the console (L6) are later phases.
- Execution is still ADR-008's; the runtime may *propose* an action and the
  executor still decides whether it happens.
