# Sovereign Knowledge & Agent Runtime

**A local intelligence system is not a model. It is a governed runtime in which a model is
one probabilistic component.**

This repo compiles a corpus into an executable, inspectable, versioned intelligence
artifact, and then lets independent agents interrogate, test, and challenge that artifact
under a policy gate that fails closed — with provenance and uncertainty preserved at every
step.

```
corpus → compiler → versioned artifact → witness → agent interrogation
       → proposal → policy gate → execution → evidence → verification
       → belief revision → next version
```

## Status

**Phase 6 — execution: the first component that does something.** 278 tests
passing on Python 3.12, stdlib-only, offline.

Implemented so far:

| Component | What it does | Where |
|---|---|---|
| RFC 8785 JCS canonicalization | Cross-language stable JSON bytes; UTF-16 key ordering, no non-finite numbers | `core/canonical.py` |
| Content addressing | `sha256` over canonical bytes, volatile fields excluded; id is the primary key so double-insert is structurally impossible | `core/content.py` |
| 13-state epistemic spine | Legal transitions as data; `UNRESOLVED` requires an investigation record; no function reads a confidence value | `knowledge/epistemic.py` |
| Vocabulary crosswalk | The estate's four incompatible claim enums mapped onto the spine; unmapped terms raise rather than guess | `knowledge/crosswalk.py` |
| Substrate store | sqlite3, real foreign keys, evidence spans verified against source text, provenance enforced at write time | `knowledge/store.py` |
| Claim normalization | Conservative stemming and a closed negator list; anything ambiguous resolves toward `INCONCLUSIVE` | `knowledge/normalize.py` |
| The evaluator | The only component that may move an epistemic state — and it returns a relation, never a score | `knowledge/evaluator.py` |
| Sentence segmentation | Deterministic, offset-exact, so every compiled claim is a **verbatim substring** of its source | `compile/segment.py` |
| Merkle manifest | Artifact version = root over sorted content leaves; recompiling unchanged bytes is *not* a new version | `compile/manifest.py` |
| Deterministic compiler | A pure function of source bytes; mines recurrence heuristics as graph claims | `compile/compiler.py` |
| Lexical retriever | Deterministic BM25, stdlib only, with the method id disclosed on every result | `witness/retrieval.py` |
| The witness | A read-only, version-bound view that answers with typed, provenanced answers | `witness/witness.py` |
| **The policy gateway** | Fail-closed authorization; decisions are content-addressed and hash-chained | `policy/gateway.py` |
| **The executor** | Performs authorized actions and records them; re-checks the path against the real filesystem | `execution/executor.py` |
| Sandboxes | Pluggable backends; the default is a no-op that says so | `execution/sandbox.py` |
| Independent auditor | Separate process, stdlib only, imports nothing from the package — walks every claim to a source offset | `scripts/audit_provenance.py` |

### The five rules that carry the thesis

**The compiler cannot assign an epistemic state.** Every claim it writes is
`UNEXAMINED`, enforced by a guard that raises if `COMPILE_PERMITTED_STATES` is
widened — [ADR-004](docs/adr/ADR-004-compiler-authority-and-manifest.md).

**The witness cannot assert absence without showing its work.** It has no
`answer() -> str`; a miss returns a real `UNRESOLVED` answer *plus* an
`Investigation` record naming the query, scope, method, and version searched —
[ADR-005](docs/adr/ADR-005-witness-and-absence.md).

**The evaluator cannot certify what it cannot show.** It returns one of four
*relations* and never a number, so there is nothing to threshold. `SUPPORTED` is
unreachable unless another claim literally states the proposition —
[ADR-006](docs/adr/ADR-006-evaluator.md).

**The gateway cannot permit what nobody granted.** An absent policy, an
unmatched request, a violated constraint, and an absent constrained argument
all produce `DENY`. There is no path that returns ALLOW because nothing
objected — [ADR-007](docs/adr/ADR-007-policy-gateway.md).

> **Absence of restriction is not permission.** A capability exists only where
> a grant names it. An allow-by-default system makes the safe path the one
> nobody remembered to restrict, and a missing rule silently becomes a grant.

**The executor cannot be lied to about where a path lands.** ADR-007's
canonicalization is deliberately *lexical* — it never stats anything, so its
verdict is reproducible — which means it cannot see that `/data/link` is a
symlink to `/etc`. So the executor re-derives the check against the real
filesystem at the last moment before the action, and refuses if the resolved
path escapes the grant. **A request can be allowed by the gateway and denied by
the executor.** That is not a contradiction; it is the deferral being honoured
— [ADR-008](docs/adr/ADR-008-execution-and-audit.md).

```
gateway verdict : allow
executor        : REFUSED — /private/etc/shadow is outside /…/data
```

Every authorized attempt produces a content-addressed `ExecutionRecord`,
including the default no-op sandbox and including a point-of-use refusal. Only a
*denial* produces none — nothing was attempted, and the decision chain already
holds the record. `performed` and `degraded` are stored separately, because a
no-op and a safety refusal are the two things an operator most needs to tell
apart, and conflating them produces audit records that lie precisely when
nobody can check them. `argv` is a list, there is no shell, and a test asserts
`echo "hi; touch PWNED"` prints the semicolon rather than running it.

Every decision is content-addressed and hash-chained, and **the chain covers the
verdict and the reason, not just an id**. With the verdict excluded, flipping a
recorded `DENY` to `ALLOW` and leaving the hash untouched verifies clean — that
was the original implementation, and the test that should have caught it was
itself flipping a verdict that was already `ALLOW`, so it changed nothing and
passed. A tamper test that proves nothing is worse than no tamper test.

Verified by real execution, including adversarial cases:

```
clean artifact           →  clean: True   exit 0
source rewritten after   →  clean: False  exit 1, 3 span mismatches
evaluations deleted      →  clean: False  exit 1, 3 unjustified states
gateway, no policy       →  DENY on every request
/path traversal          →  DENY after canonicalization
flipped verdict          →  chain fails to verify
default executor         →  no-op, and a record saying so
symlink escape           →  gateway ALLOW, executor REFUSED
argv "; touch PWNED"     →  echoed as text; PWNED never created
decision chain broken    →  execution refused before the backend is called
conflict pair compiled   →  2 contradicted, 2 inconclusive, 0 supported
one byte changed         →  new Merkle root
same bytes, reordered    →  identical root
nonsense query           →  UNRESOLVED + investigation record, not a weak claim
```

The full assessment, migration map, target architecture, data model, migration
plan, and test strategy are in `docs/architecture/`:

| Document | What it settles |
|---|---|
| [`00-ecosystem-survey.md`](docs/architecture/00-ecosystem-survey.md) | MCP `2026-07-28`, A2A `1.0.0`, GraphRAG/LightRAG/Graphiti limits, provenance precedents, local inference + sandboxing state of the art |
| [`migration-map.md`](docs/architecture/migration-map.md) | What already exists across 20+ repos, verified by source reading and test runs; per-repo PRESERVE/ADAPT/DEPRECATE verdicts; the push-back on the brief's belief vocabulary; licensing blockers |
| [`target-architecture.md`](docs/architecture/target-architecture.md) | Seven-layer map, the substrate properties, the formalized witness, heuristics, beliefs, protocol boundaries |
| [`data-model.md`](docs/architecture/data-model.md) | Typed records, content addressing, bitemporality, ledger, artifact layout |
| [`migration-plan-and-test-strategy.md`](docs/architecture/migration-plan-and-test-strategy.md) | Phased plan with gates, the vertical slice, invariant tests, evaluation discipline |

## Six blocking forks

Implementation of each fork is gated on its ADR in `docs/adr/`:

| # | Fork | Outcome |
|---|---|---|
| 1 | **License** | **RATIFIED** — [ADR-001](docs/adr/ADR-001-license-and-vendoring.md). Apache-2.0. Phase 0 vendors **MIT donors only** (`hermes-atlas`, `sworker`); `ganymede3`/AEP are *read, not vendored* until licensed, so their design is reimplemented rather than copied. Unblocks development without prejudicing publication. |
| 2 | **Claim-state vocabulary** | **RATIFIED** — [ADR-002](docs/adr/ADR-002-epistemic-state-vocabulary.md). ADR 002/011 spine + `DERIVED`/`ASSUMED` = 13 states. `UNRESOLVED` and `INCONCLUSIVE` stay distinct. The brief's list is rejected: it cannot express "searched and found nothing". |
| 3 | **`ganymede3` duplicate claim models** | **RATIFIED** — [ADR-003](docs/adr/ADR-003-storage-and-single-claim-model.md). One model, content-addressed. The duplicate is not inherited because `ganymede3` is read-not-vendored. |
| 4 | **Storage** | **RATIFIED** — ADR-003. stdlib `sqlite3`, no ORM. Foreign keys enforced. |
| 5 | **Name** | Open — cosmetic, but do it before publication. |
| 6 | **Demo corpus** | Open — smoke corpus used for the vertical slice; the 123k-chunk run is the later demonstration. |

## The principle, stated once

> Agent proposes. Schema decides. Policy authorizes. Executor performs. Evidence records.
> Verifier evaluates. Knowledge state changes.

The model is never the final authority over whether an action occurred or whether an
assertion is supported. "The model says so" is never a sufficient reason. "The corpus does
not contain it" is a *provable* statement, not an absence.

## What is being reused, and from where

| Asset | Source | Verified |
|---|---|---|
| Determinism gate (full equality + delta isolation) | `hermes-atlas` | 4.7k LOC, MIT, 18 test files |
| PermissionEngine, AST risk classifier, DecompositionGuard | `sworker` | **490 tests green in 45s**, 12.6k LOC, stdlib-only |
| 13 deterministic no-LLM verifiers | `sworker/verify.py` | same |
| 8-state claim lifecycle, negative-knowledge taxonomy, epistemic ledger DAG, proposal-only agents | `ganymede3` ADR 002/006/007/008/011 | **163 core tests green in 1.1s** |
| Content-addressing, bitemporal invalidation, hash-chained audit | `ganymede3` + `sovereign-intelligence` | — |
| Evaluation discipline (ResearchWindow, HoldoutWindow, TemporalAuthority, DSR/PBO, null-world controls) | `sovereign-agent-stack` | **3,218 tests green in 21s**, incl. `tests/adversarial/` |
| 11 protocol objects + 13-stage lifecycle, Apache-2.0 | `right-to-compute` | 8.4k LOC |
| Compiled 153 posts → 1,513 facts + 436 decisions, locally, static | `sovereign-knowledge-compiler` | — |

## Honest open problem

The retrieval verifier gap is a **capability gap, not a threshold problem**: deterministic
and hybrid verifiers measure topical relevance, not answer containment, and their score
distributions fully overlap. Only an LLM judge reaches usable recall (76%). The system's
correctness therefore rests on **provenance and structure** — claims resolving to character
offsets, which is mechanically checkable — not on model-judged containment. Stated here so
it is designed around rather than tuned away later.
