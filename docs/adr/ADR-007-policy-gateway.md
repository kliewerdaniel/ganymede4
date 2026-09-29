# ADR-007: The policy gateway — no action without a decision, no decision without a record

- Status: **Proposed**
- Date: 2026-09-28
- Depends on: ADR-002, ADR-006

---

## 1. The problem

The architecture's thesis (§14) is that the model is *the only nondeterministic
component* in an otherwise deterministic substrate. Everything built so far
supports that: content addressing, the epistemic spine, the compiler, the
witness, the evaluator.

But the substrate so far only ever **reads**. There is no path by which anything
executes, and therefore no path by which anything could execute *wrongly*. The
gate is missing, and a governance story that has never governed anything is a
story.

The donor for this phase was `sworker` (`policy.py`, `permissions.py`,
`evidence.py` — an `EvidenceLedger`, a content-hashed `PolicyStore`, a state
machine, 490 tests). **It is not present on this machine**, so this is a
clean-room design against the recorded findings, not a lift. Nothing was
copied; `NOTICE` already records that.

## 2. Decision

**A policy decision is a content-addressed, hash-chained record, and it is
written before the action it governs is attempted.** An action with no decision
in front of it is not unauthorized-by-default; it is *unrepresentable*.

Three properties, each testable:

1. **Fail closed.** An absent policy, an unparseable policy, an unmatched
   request, or an explicit `deny` all produce `DENY`. There is no path that
   returns "allow because nothing objected."
2. **No ambient authority.** A capability is granted by an explicit grant naming
   it. A policy that never mentions a capability withholds it. Absence of
   restriction is not permission — this is the specific inversion that turns a
   sandbox into a suggestion.
3. **The decision is the artifact, not the log line.** The decision is
   content-addressed and chained, so a decision cannot be edited after the fact
   without breaking the chain. This reuses `core/content.py` rather than
   inventing a second integrity mechanism.

## 3. Shape

```
Policy      { capabilities: Capability[], default: DENY|ALLOW, version }
Capability  { resource, actions[], constraints{...} }
Request     { actor, action, resource, args, context }
Decision    { request_digest, verdict: ALLOW|DENY, reason, policy_version,
              decided_at, chain_prev, chain_hash }
```

`Decision` is content-addressed over everything **except** `decided_at`, which
is provenance rather than identity — the same identity/provenance split as
`Manifest` (ADR-004 §4). Two identical requests under the same policy produce
the same decision id, so replays are recognizable as replays.

`Request.args` is hashed, not executed. The gateway judges the *request*; it
never performs the action. Execution is a separate concern (ADR-008) precisely
so that the gate can be tested without a sandbox.

## 4. Why decisions come before actions

The ordering is the whole security property, and it is the part a reviewer
should check first.

An audit record emitted *after* a successful action proves only that the action
happened. An audit record emitted *after* a *refused* action proves the gate
works. An audit record emitted *before* either proves the gate was consulted at
all — and that is the claim a governance system actually makes.

So: `decide()` writes the decision, and only then may a caller act. A caller
that acts on a `Request` without a `Decision` has violated the protocol, and the
audit trail is designed so that this is detectable — a run whose actions do not
all trace to a preceding decision fails verification.

## 5. Rejected alternatives

**Allow-by-default with a deny list.** Rejected. It inverts the failure mode:
the safe path is the one nobody remembered to restrict, and a missing rule
becomes a grant. The whole estate's evidence (v0.1's 23 fabricated facts) is
about unstated authority being treated as authorization.

**Consult the model on permission.** Rejected, and not because models are bad at
it — because a nondeterministic authority cannot be a gate. If the model can
veto, the gate is advisory. Tier 3 in `target-architecture.md` §11 is
explicitly a *reported* signal, and this is the boundary: the model may be
consulted and its opinion recorded, but it may not decide.

**Audit-only, no pre-decision.** Rejected per §4.

**A real sandbox in this phase.** Rejected as scope. `(tool, argv, cwd) →
(result, audit)` is ADR-008. The gateway can be fully tested without one, and
building the sandbox first would mean testing the gate through the thing it is
supposed to constrain.

## 6. Consequences

- `sovereign run` can exist only after this, and it will be the first component
  that can *do* something.
- The hash chain is the substrate's first runtime ledger. §10 of the
  architecture doc says the compile-time and run-time ledgers must be the same
  append-only DAG; the chain type is written so the compiler's manifest can
  anchor it later without migration.
- A denied action is a first-class, inspectable outcome — not an exception and
  not a log line. `Gate 4` is testable offline with no model, no sandbox, and
  no network.

## 7. Enforced by

- `decide()` raises rather than defaulting, on any policy it cannot interpret.
- The default verdict is `DENY`; a test asserts it on an empty policy.
- Every decision is content-addressed and chained; a test breaks a chain link
  and requires verification to fail.
- A decision records the policy version that produced it, so a later policy
  cannot retroactively justify an earlier action.
- No module in `policy/` imports a model client, and a test asserts it.
