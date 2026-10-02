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

Each arrow in that diagram is a component that exists and is tested. Three of
them were, at different times, decorative: `belief revision` did not exist for
eleven phases (ADR-017); once it did, `→ next version` still pointed at
nothing, because the Merkle root could not see a claim's epistemic state change
(ADR-018); and `evidence → verification` had never run on the current artifact
at all, and when it did, 97% of the claims it marked `SUPPORTED` were attested
only by copies of themselves (ADR-019). The diagram is now a description rather
than an aspiration — but the pattern worth keeping is that **each of the three
was found by running the thing, not by reading it**, and each looked like
success while it was broken.

## Running it

Requires Python 3.11+ and nothing else — the core has **no dependencies**, and
no test or script reaches the network.

```bash
git clone https://github.com/kliewerdaniel/ganymede4
cd ganymede4
python -m pytest        # 677 tests, ~40 s (two real-corpus tests dominate)
```

### One command builds and verifies the artifact

```bash
python scripts/build_artifact.py --corpus ~/Projects/Chris
```

That is compile → evaluate → reseal → **independent audit** → print the
identity, in one process, and it exits non-zero if the audit is not clean. A
build that compiles but does not audit clean is not a partial success; it is an
artifact you should not use. Nothing is hardcoded to one machine: `--corpus`
or `GANYMEDE4_CORPUS_ROOT` says where the data is, `--db` says where the
artifact goes, `--python` says which interpreter runs the auditor.

The corpus is **not in this repository**. It is the author's own record, held
locally, and it is deliberately not committed (see *Derived, not committed*).

### Inspecting an artifact

```bash
python -m ganymede4.cli verify  build/ganymede4-corpus.db
python -m ganymede4.cli query   build/ganymede4-corpus.db "is the model the intelligence?"
python -m ganymede4.cli audit   build/ganymede4-corpus.db
python -m ganymede4.cli build   --db build/ganymede4-corpus.db --corpus ~/Projects/Chris
```

`verify` **recomputes** the Merkle root from the rows on disk and compares it
to the recorded one — printing a stored digest would verify nothing.
`query` prints the answer, its claims, its citations, and (with `--boundary`)
everything the witness does *not* know.

Every subcommand except `build` is read-only. That is a design constraint, not
an oversight: a console with a write path is a second, undocumented way to
change epistemic state that bypasses the policy gateway. If you want to change
beliefs, go through the gateway — which is deliberately not reachable from
here.

### Which numbers belong to which build

There are three artifacts floating around and they are not interchangeable.
Confusing them is how a stale number survives for months, so:

| build | command | artifact | what it is |
|---|---|---|---|
| **compile-only** | `build_artifact.py --no-evaluate` | root `7e506ea556f63374` | claims and evidence, **no verdicts**. The Merkle root of the source. |
| **evaluated (current)** | `build_artifact.py` | `v1-49f86ced09fcb43b` | + 319,293 evaluations. 7,173 `SUPPORTED`, 402 `CONTRADICTED`, 311,718 `INCONCLUSIVE`, 16,897 `DERIVED`. |
| **pre-ADR-024** | — | `v1-1b2a81e128eadf5d` | the same corpus with the old evaluator: 8,646 `SUPPORTED`, 402 `CONTRADICTED`. Kept only as a comparison baseline. |

The compile root is **unchanged** across the last two rows while the recorded
version moves. That is ADR-018 working: the Merkle root over *source* cannot
see verdicts, and the artifact version (which roots the state digest) can.
Same corpus, different artifact — not a bug.

**The 596 `CONTRADICTED` figure that appears in the phase history was wrong**
and is annotated where it occurs: it came from a run whose process had
already imported the evaluator before the ADR-020 patch landed, so it still
contained the empty-term contradiction class. Measured after ADR-020: 402.

## Current phase

**Phase 20 — can the pipeline be governed by something other than trust?**
677 tests: 671 pass, 6 skipped. Python 3.12, stdlib-only, offline, auditor
clean.

Phase 19 asked whether the verdicts mean anything and whether a later write
can invalidate an earlier invariant. Phase 20 asks whether a *model* can be
allowed near the write path without becoming the authority — and answers
with three separated authorities (ADR-028, ADR-029).

Task 1 found two write paths that silently broke invariants: `Reviser` used a
raw `UPDATE` and neither bumped `state_epoch` nor re-sealed, and its
invalidation wrote no decision record at all. Both are fixed and pinned
(ADR-022), with the invariant × write-path matrix in
[`docs/architecture/invariants.md`](docs/architecture/invariants.md).

Task 2 asked whether the verdicts mean anything, not whether the artifact is
internally consistent — a question the auditor has now answered wrongly four
separate times, reporting `clean: true` on artifacts whose verdicts were
vacuous. 17.0% of `SUPPORTED` was one claim attesting a shorter copy of
itself; `SUPPORTED` is now 7,173, and **no claim gained support**, which is
the falsifiable prediction (ADR-024, [`read_verdicts.py`](scripts/read_verdicts.py)).

Task 5 made the build one command and the console real. Both are below.

Full narrative, including every number that was later corrected:
[`docs/status/PHASES.md`](docs/status/PHASES.md). Decision records:
[`docs/adr/`](docs/adr/).

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

## Open problems

**Contradictions on a code-heavy corpus.** 402 claims are marked
`CONTRADICTED`, and reading them shows the class is dominated by code
fragments that differ in whitespace or a return value — not prose
disagreement. Genuine prose contradictions are in there too (*"It is also a
pyramid scheme."* against *"THIS IS NOT A PYRAMID SCHEME!"*), so the rule is
not simply wrong, it is too coarse. **No threshold has been fitted to fix
this**, because fitting one to this corpus would be fitting it to the author.
The open question is whether the compiler should emit code as claims at all;
that needs a hand-labelled sample before any rule changes. Tracked in
[`docs/open-questions/code-contradictions.md`](docs/open-questions/code-contradictions.md).

**The mined `DERIVED` heuristics are mostly noise.** All 16,897 are recurrence
summaries, and the corpus's most-repeated "sentences" are file-watcher output:
`"py first seen with mtime 1729088249" occurs in 17 distinct sources`. ADR-012's
proposition gate admits them because they are well-formed sentences. They are
reported, not filtered, for the same reason as above.

**The retrieval verifier gap** is a **capability gap, not a threshold
problem**: deterministic and hybrid verifiers measure topical relevance, not
answer containment, and their score distributions fully overlap. Only an LLM
judge reaches usable recall (76%). The system's correctness therefore rests on
**provenance and structure** — claims resolving to character offsets, which is
mechanically checkable — not on model-judged containment.

**Not done:** no real-model loop has been run (Task 4), and the contradiction
sample is blocked on a human labelling pass (Task 3).

## Honest limits

This is a research prototype with one narrow, unusual property — its claims
resolve to character offsets in source bytes, and an auditor that does not
import the code it checks verifies that. It is not a general retrieval system,
it is not multilingual. It **has** been run against a real local model
(`qwen3:4b`), and that run is the most useful result in the repository: the
model cited a claim ID wrong by a single character out of 68, and the system
refused it (ADR-028). Treat the numbers as measurements of *this* corpus, not
as performance claims.
