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

**Phase 2 — deterministic compiler built and verified.** 129 tests passing on
Python 3.12, stdlib-only, offline.

Implemented so far:

| Component | What it does | Where |
|---|---|---|
| RFC 8785 JCS canonicalization | Cross-language stable JSON bytes; UTF-16 key ordering, no non-finite numbers | `core/canonical.py` |
| Content addressing | `sha256` over canonical bytes, volatile fields excluded; id is the primary key so double-insert is structurally impossible | `core/content.py` |
| 13-state epistemic spine | Legal transitions as data; `UNRESOLVED` requires an investigation record; no function reads a confidence value | `knowledge/epistemic.py` |
| Vocabulary crosswalk | The estate's four incompatible claim enums mapped onto the spine; unmapped terms raise rather than guess | `knowledge/crosswalk.py` |
| Substrate store | sqlite3, real foreign keys, evidence spans verified against source text, provenance enforced at write time | `knowledge/store.py` |
| Sentence segmentation | Deterministic, offset-exact, so every compiled claim is a **verbatim substring** of its source | `compile/segment.py` |
| Merkle manifest | Artifact version = root over sorted content leaves; recompiling unchanged bytes is *not* a new version | `compile/manifest.py` |
| Deterministic compiler | A pure function of source bytes; mines recurrence heuristics as graph claims | `compile/compiler.py` |
| Independent auditor | Separate process, stdlib only, imports nothing from the package — walks every claim to a source offset | `scripts/audit_provenance.py` |

### The one thing to understand about the compiler

**It cannot assign an epistemic state.** Every claim it writes is `UNEXAMINED`,
enforced by a guard that raises if `COMPILE_PERMITTED_STATES` is ever widened.
This is [ADR-004](docs/adr/ADR-004-compiler-authority-and-manifest.md), and it
is the thesis in one enforceable rule: establishing that one sentence is *about*
another is a semantic judgement, and every deterministic proxy for it measures
topical relevance rather than entailment. The compiler emits **candidates with
provenance** and refuses to certify them. A test parses the compile package's
AST and fails if any executable node so much as names a confidence value.

`UNRESOLVED` is likewise unreachable from a compile — it asserts a search
happened, and parsing is not searching.

Verified by real execution, including adversarial cases:

```
clean artifact         →  clean: True   exit 0
source rewritten after →  clean: False  exit 1, 3 span mismatches
one byte changed       →  new Merkle root
same bytes, reordered  →  identical root
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
