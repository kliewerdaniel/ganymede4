# ganymede4

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

**Phase 10 — the record shrinks: a verdict is a finding, not a dump.**
373 tests passing on Python 3.12, stdlib-only, offline.

The last full-corpus run died with `database or disk is full` at 4% completion
and a 20.2 GB database. The evaluator was writing **58,345 bytes per
evaluation**, because every `inconclusive` record inlined the full list of its
lexical neighbours. Quadratic in the corpus, and entirely invisible to seven
phases of tests, because every fixture was small enough that inlining a
neighbour cost nothing.

It is now 1,049 bytes — 56× less, and linear. Neighbourhoods are recorded as a
count plus a content digest, never truncated, because "inconclusive with 4,000
neighbours" and "inconclusive with 50" must stay distinguishable. Attestation
sets are stored once, content-addressed, instead of copied into every member's
row. [ADR-013](docs/adr/ADR-013-evaluation-record-size.md).

Two claims made by earlier phases were not true until measured: that
`attested_by` was the culprit (it was ~2% of the bytes), and that ADR-011 had
made the evaluator linear (it had only made *finding* neighbours linear).
Fixing retrieval and ignoring persistence is how you get a 20 GB file and a
green test suite.

**The corpus is 18,930 documents of KonradFreeman's own public record** —
2,178 Reddit comments, 676 submissions, and 562 ChatGPT conversations, read
from their existing location and never committed to the repository. It is the
natural test of the thesis: one person arguing with strangers about whether
models are the intelligence system, while simultaneously doing exactly that
argument with models. 44% of it is a model's own prior output, typed as such
and never presented as testimony.

**A claim must be a proposition** — ADR-012. Seven phases of prose fixtures hid
that 22% of the real corpus was not claims at all but `**6.`, `return None`, and
`except requests.`: perfectly addressed, not assertable. Segments that cannot be
proposed are discarded, and the count of discards is reported on the artifact,
never dropped silently.

**The project is `ganymede4`** — the fourth attempt at the same problem in this
lineage, and the succession is the point. Apache-2.0 and clean-room: it is not
a fork of any predecessor, and nothing has been copied from `ganymede3`, which
remains an unlicensed read-only design reference per ADR-001.

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
| **The runtime** | A bounded proposal loop over a read-only witness. Holds no store, so it cannot write | `runtime/loop.py` |
| Independent auditor | Separate process, stdlib only, imports nothing from the package — walks every claim to a source offset | `scripts/audit_provenance.py` |

### The six rules that carry the thesis

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

**The runtime cannot write, and cannot be made to.** `target-architecture.md`
§2 says *nothing in L4 can write to L1*; that is implemented as a missing
capability rather than a withheld one. `Runtime` takes a witness and a gateway
and **no store parameter exists**, so there is no wiring that connects it to
the write path even by accident, and a test parses the package's AST to prove
it imports no write path. `Proposal` has no `apply` — the absence is the API.

A proposer that fabricates claim ids, cites nothing, and self-attests gets
**zero permitted proposals even under a fully-granting policy**:

```
unknown-citation   cited claim id(s) not in the corpus: clm-00000000000000000
uncited            proposal asserts a state without citing any claim
permitted count: 0
```

That test is the point. If governance lived in the prompt, the guarantees would
change when the proposer changed and the diff would be a string. Here the
`Proposer` is a seam and the loop enforces everything, so a model can be
dropped in without changing a single guarantee — [ADR-009](docs/adr/ADR-009-runtime-proposal-loop.md).

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
malicious proposer       →  0 permitted, under a fully-granting policy
permitted proposal       →  no claim, no evidence, no state written
budget = 0               →  ValueError, not a silent empty run
18,930 real documents    →  422,753 claims, identical root reversed
evaluate_all on it       →  77 of 422,753 in 17 min; killed. See below.
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
| 5 | **Name** | **RATIFIED** — [ADR-010](docs/adr/ADR-010-name-and-corpus.md). **`ganymede4`**, the fourth attempt at this problem in the lineage. Clean-room: not a fork, nothing copied from `ganymede3`. |
| 6 | **Demo corpus** | **RATIFIED** — ADR-010. **18,930 documents of the author's own record** — 2,178 Reddit comments, 676 submissions, 562 ChatGPT conversations. Never committed; read from its existing location. 44% of it is a model's prior output and is typed as such, because ingesting it as testimony would launder a machine's assertion into the historical record with a perfect source span. |

## Resolved: the evaluator now scales to the real corpus

Running the real 18,930-document corpus found a defect that 322 fixture-scale
tests did not — which is the argument for using real data at all.

`Evaluator.evaluate` ended its common case by asking the retriever for
`limit=len(self._index)` — **every document in the corpus** — in order to decide
which claims were neighbours of the claim under judgement. Fine for one claim.
For `evaluate_all` it was the whole corpus, each time. It produced **77
evaluation records out of 422,753 claims in 17 minutes**, and was killed.

The obvious fix — a smaller `limit` — was refused, because it would quietly
turn *"material in the neighbourhood"* from a completeness property into a top-k
approximation, in the one component whose justification is that it does not
over-claim.

The right fix was to notice the evaluator was asking a **membership** question
using a **ranking** method. Nothing downstream ever looked at a neighbour's
score; the answer was only "is it empty, and which ids are in it". So `BM25`
gained an inverted index and `matching_docs`, which returns **exactly the same
set** in time proportional to the postings touched — [ADR-011](docs/adr/ADR-011-bounded-neighbourhood-search.md).

The equivalence is exact rather than approximate because every idf in this
implementation is strictly positive, so `score > 0` means precisely "shares a
term". That precondition is asserted by a test rather than assumed, and
`tests/test_scaling.py` now checks the fast path against the ranking pass on
real vocabulary with adversarial queries.

No document is dropped that was not already dropped, so **no verdict can
change**. Recall was preserved, not spent.

> A test here earned its keep by being *wrong*. The first version pinned the
> buggy line by substring, so after the fix it still passed — the line had
> survived in the comment explaining the replacement. It was guarding a prose
> description of the bug instead of the bug's absence. The assertion is now
> AST-based, so no comment or docstring can satisfy it.

## Resolved: the evaluator wrote 56× more than it needed to

The fix above made *finding* neighbours linear. It did not make *storing* them
linear, and the next full-corpus run proved it by filling the disk.

```
sqlite3.OperationalError: database or disk is full
```

20.2 GB, 17,691 evaluations written out of 324,951. On a 400-document slice:

| | measured |
|---|---|
| after compile (10,148 claims) | 18.2 MB |
| after `evaluate_all()` | 610.3 MB |
| **per evaluation** | **58,345 bytes** |

One line was responsible:

```python
investigated=f"neighbours={sorted(neighbours)}" if neighbours else "no-neighbours"
```

Every lexically-matched claim id, as a set repr, inlined into a string — on the
`inconclusive` path, which ~73% of claims take. The neighbourhood of an
inconclusive claim is not its evidence. The evidence is *that a search ran and
found material that did not establish a conclusion*. How many documents share a
word with a claim is a property of the corpus, not a fact about the claim — and
it is exactly why the verdict is `inconclusive` rather than `attested`.

Neighbourhoods are now a count plus a digest over the sorted ids: 1,049 bytes,
constant-size, still independently recomputable from the index. Scaling
measured at 200/400/800 documents is 0.88×–1.37× per doubling, not the ≥2× of
a quadratic. Attestation sets are additionally stored once, content-addressed,
rather than copied into every member's row.

**Verified on the full corpus.** All 324,951 claims evaluated, end to end:

| | before | after |
|---|---|---|
| per evaluation | 58,345 B | ~390 B |
| largest `meta` | 13,674 B | **71 B** |
| at 4% complete | **20.2 GB, disk full** | 540 MB |
| full run | never finished | **797 MB** |
| evaluations written | 17,691 | **308,935** |

```
claims  : 324,951      inconclusive : 156,907
dropped :  94,480      supported     : 151,382
evidence: 308,935      derived       :  16,016
peer groups: 151,488   contradicted  :    646
```

Zero claims left `unexamined`. A 56× reduction is only interesting because the
thing it made possible was finishing: the previous run never reached 5%.

Three things this cost, all worth recording:

- **The first hypothesis was wrong.** The obvious suspect, `attested_by`, is
  genuinely quadratic and genuinely worth fixing — but it was ~2% of the bytes.
  What broke it open was noticing the *largest* `attested_by` was 8,095 bytes
  while the *average row* was 58,345. The two numbers could not both be true.
- **Three versions of the regression test passed against the broken code.**
  Six sentences attest nothing; `REPEATED * 40` deduplicates to six claims,
  because claims are content-addressed; and padding each claim with the
  proposition it was attesting made every claim `attested`, so none reached the
  broken branch at all. The fixture now grows by adding *distinct* text with
  shared vocabulary, and the tests are confirmed to fail against the old
  representation before being trusted.
- **ADR-011's claim of linearity was narrower than it read.** It made retrieval
  linear. Storing was a separate problem in a separate layer, found only by
  running the thing at the size it was meant to run at.
- **The size ceiling then caught a second copy of the same bug.** After the fix,
  `contradicted_by` was still inlined into `meta` — records of 13,674 bytes
  containing nothing but that list. It was invisible because only 52 of 45,393
  records were contradicted. Fixing the common case and leaving the rare one is
  how the rare one ships; it now has a column, a peer group, and a test
  confirmed to fail when the list is inlined again.

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
