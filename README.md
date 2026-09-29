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

## Running it

Requires Python 3.11+ and nothing else — the core has **no dependencies**, and
no test or script reaches the network.

```bash
git clone https://github.com/kliewerdaniel/ganymede4
cd ganymede4
python -m pytest          # 417 tests, ~14s
```

`pip install -e .` is optional and currently only adds a broken console script
(see *Known defect*, below). To build the real corpus artifact, see *Reproducing
the artifact*.

## Status

**Phase 14 — a retracted claim takes its dependents with it.**
430 tests passing on Python 3.12, stdlib-only, offline.

The README's own pipeline diagram claimed `belief revision → next version` for
eleven phases before the component existed. Grepping `src/` for `revision`
returned two English uses of the word in comments and nothing else — the
diagram had been describing a system with a hole in it.

The hole was not cosmetic. The corpus holds 16,897 `DERIVED` heuristics, each
resting via a `DERIVED_FROM` edge on the claims that produced it. If two of
those supporting claims are retracted, the heuristic does not get weaker — it
becomes *unsupported*, and nothing noticed. The artifact kept asserting it.

[ADR-017](docs/adr/ADR-017-belief-revision.md): a claim entering `RETRACTED`,
`INVALIDATED`, or `SUPERSEDED` now invalidates everything that transitively
depends on it — through derived edges *and* through attestation groups.

Three properties, each load-bearing and each tested against a sabotaged
implementation:

- **Transitive.** One level of propagation moves the staleness rather than
  removing it. A heuristic derived from a heuristic goes too.
- **Fails closed.** A dependent that cannot legally be invalidated is
  *refused* and reported, never dropped. A silently skipped dependent is the
  same defect one level up. Same rule for a dependency that cannot be read: an
  unresolvable attestation group stops the revision rather than being treated
  as an empty one, which would make a `SUPPORTED` claim read as unattested.
- **Only ever downward.** Revision can invalidate but never resurrect, never
  promote to `SUPPORTED`, and never reach `VALIDATED`. A new belief comes from
  a new compile, which is a new artifact version.

`CONTRADICTED` is deliberately **not** a trigger: a contested claim is still a
claim. Including it would invalidate much of the corpus the moment a peer
group formed.

Measured on the real corpus, and worth stating because it is not what the
edge count suggests: 100,723 `DERIVED_FROM` edges over 16,897 derived claims
resolve to 100,723 *distinct* supports, each with exactly one dependent, and
the deepest chain is one level. The mined heuristics form a flat bipartite
graph, not a hierarchy — so on this corpus revision is shallow by
construction, and the transitivity guarantee is there for corpora where it
is not.

The independent auditor gained the matching check and still imports nothing
from the package — it re-derives the invariant from the stored rows, because
an auditor that imports the code it audits cannot catch that code being wrong:

```
clean artifact                →  clean: True    exit 0   4.2s, 336,190 claims
support retracted, no revise  →  clean: False   exit 1
                                  stale_dependents: 1, naming both claims
```

The honest cost, stated in the ADR rather than discovered later: revision is
**conservative**. Losing one of five supports invalidates a heuristic that
four supports still justify. That is the wrong answer for a human, and the
right one for a system whose claim is that a stale belief must not survive its
own refutation. Relaxing it means an audited policy, not a smarter heuristic.

## Earlier status

**Phase 13 — exporter structure is not the author.**
417 tests passing on Python 3.12, stdlib-only, offline.

[ADR-016](docs/adr/ADR-016-exporter-structure.md): a Reddit/ChatGPT export is
markdown, and the exporter writes `## Parent Comment`, `---` rules, and ``` fences
that the author never typed. None is a sentence terminator, so a block whose text
carried no `.` at all came out as **one** claim:

```
'---\n\n## Parent Comment\n\nDM\n\n---\n\n## Parent Comment\n\nWhy?'
```

One claim, two comments by two different people, attributed to a single evidence
span. The witness's top-ranked answer to "parent comment" was the literal string
`## Parent Comment`.

Block-level markdown is now a boundary, and the authored text survives it:
`## Why were libraries burnt?` becomes `Why were libraries burnt?`. Inline markup
is untouched — `#` in `C#`, `*` in `3 * 4`, a mid-line `---`.

```
mid-claim '## ' :  9,096 -> 1,021
'---' rules     :  2,155 ->    53
code fences     :  5,766 ->    868
claims          : 336,190   audit clean, exit 0
```

The residue is *not* missed boundaries. 884 of the 1,021 are mid-line (`f"## Comment"`
inside a Python string), 39 are escaped (`\#`, which is a character the author typed),
and the fences are the same. Verified directly: a real line-start ``` fence is
detected, the fence is excluded from the segment, the body beneath it is captured
verbatim, and **zero** segments open with a line-start fence.

Non-verbatim offsets across all 434,091 segments of the real corpus: **0**.

This one cost more than it should have. Four attempts at a single regex each failed
invisibly — a zero-width branch that looped forever, an optional capture group that
matched empty so the caller could not tell a heading from a fence, an alternation
branch that won with an empty match, and a "rest of line" test that read the rest of
the *text* and so called a `---` at offset 0 prose. None of those is visible in the
pattern; each only shows up as text that merges or disappears. The fix was to stop
and write three explicit cases in Python, which is shorter than the commentary
explaining why the cleverer pattern was wrong.

Profiling then caught a 13× regression — the block scan was rescanning forward from
every terminator, 1.47M calls over the corpus. Carrying the pending boundary forward
made it a single pass, and the full corpus now segments in **3.8s**, faster than the
8.7s baseline before this change.

## Earlier status

**Phase 12 — a control character is a boundary, not content.**
400 tests passing on Python 3.12, stdlib-only, offline.

The real corpus has one NUL byte, at character 242,506 of a 415,353-character
ChatGPT export, inside `⇡ =E⇡ ⇥\x001 +`. `⇡` is the private-use glyph a PDF
extractor emits for ψ — the byte is where a paper's font encoding lost a glyph
index. It produced three claims, all fragments of a figure, all of which
slipped past ADR-012 because that test is *length and letters* and these
fragments are long and alphabetically rich. Being garbage is not the same as
being short.

[ADR-015](docs/adr/ADR-015-control-characters.md): a C0/C1 control is now a
hard segment boundary, and a segment containing one is never a proposition.

Two answers were rejected by name, and the second is the dangerous one:

- **Reject the source.** One bad byte at 58% through the document would discard
  172,847 characters of real record.
- **Strip the byte.** Looks harmless, is not: in a content-addressed store,
  source content *is* the identity. Editing it after the fact invalidates every
  derived content id, invisibly — the store would report a clean audit of bytes
  that no longer exist.

The fence *recovers* text rather than discarding it. The old code made one
giant segment across the NUL that failed the length test and was dropped whole,
taking real text on both sides with it:

```
claims  324,951 -> 324,982   (+31 recovered)
dropped 94,480  -> 94,598
root    973270e4465b2d3c -> 219a5b92284b3ae5
```

Claims containing a control character: **0**. Audit clean, exit 0. Segmentation
is byte-identical on clean input, verified by stashing the change and diffing —
without that check, a boundary added to a segmenter is indistinguishable from a
silent offset shift across the whole corpus.

## Earlier status

**Phase 11 — the auditor runs, and stops lying.**
375 tests passing on Python 3.12, stdlib-only, offline.

The independent auditor had never completed on a real artifact: it looped over
every claim issuing an unindexed query, ~10^11 row visits. It is now set-based
and finishes the 324,951-claim corpus in **7.7 seconds**, clean, exit 0.

The first fast version then reported **1,434 span mismatches on a clean
artifact** — SQLite's `substr()` is NUL-terminated and one export contains a
literal NUL. Correct provenance, broken checker. A noisy auditor is worse than
none: it teaches its readers to ignore it, and they will ignore it when a real
defect arrives. [ADR-014](docs/adr/ADR-014-auditor-must-run.md).

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

The name applies to the repository, the Python package (`ganymede4`), the
`pyproject.toml` project name, and the `ganymede4` console script.

It deliberately does **not** apply to the content-addressing domain strings —
`sovereign-runtime/leaf/v1`, `sovereign-runtime/manifest/v1`, and their siblings
in `core/content.py` and `compile/manifest.py`. Those are prefixes fed into the
hash, so renaming them would change every claim id, evidence id, and the Merkle
root, invalidating a 336,190-claim artifact that currently audits clean. A hash
domain is permanent identity: it is fixed at first use and cannot be renamed
after, exactly as a database table already written to cannot be. These strings
predate the rename and stay.

### Known defect: the console script points at a module that does not exist

`[project.scripts]` in `pyproject.toml` declares:

```toml
ganymede4 = "ganymede4.cli:main"
```

**There is no `src/ganymede4/cli.py`.** The command has never worked — this
predates the rename, which renamed the declaration but did not create the
module behind it. The rename deliberately left it consistent-but-broken rather
than half-fixed, because inventing a CLI surface is a design decision, not a
rename. Until it is built, the entry point is `python -m pytest` and the
`scripts/` directory.

## Reproducing the artifact

The 845 MB corpus database is a **derived output and is not in this
repository**, by design. It is reproduced byte-for-byte from the sources plus
this code, and an artifact you cannot reproduce is an artifact you cannot
check. Committing the `.db` would also put a binary blob in a repository whose
entire thesis is that a derived artifact should never be trusted over the
inputs it came from.

The corpus is read from its existing location and never copied here —
`CORPUS_ROOT = ~/Projects/Chris` (`corpus/load.py`), 18,930 documents, 35.8 MB.
It is the author's own public record, and it is not redistributed with the code.

```bash
# compile: ~135s, prints what came out and verifies the reversed-order root
/tmp/sr312/bin/python scripts/compile_corpus.py --no-evaluate

# independent audit: separate process, imports nothing from the package
/tmp/sr312/bin/python scripts/audit_provenance.py /tmp/ganymede4-corpus.db
```

(`--db PATH` overrides the default `/tmp/ganymede4-corpus.db`; `--limit N`
truncates, and the script warns that a limited run's machine/testimony ratio
describes the sample, not the corpus.)

Verified current output — the numbers this README quotes throughout:

```
version : v1-177a5ee5afaf63e8
root    : 177a5ee5afaf63e8fc40088764fa8287...
sources : 18,930
claims  : 336,190
evidence: 319,293
dropped : 114,798 non-propositions
reversed-order recompile identical : True

auditor: clean True, exit 0, 0 span mismatches, 0 orphans — 4.4s
```

Two things to expect that look like failures and are not:

- **The root is `177a5ee5afaf63e8`, not `973270e4465b2d3c` or `219a5b92284b3ae5`.**
  Those are earlier builds. Any change to segmentation, proposition rules, or
  the manifest schema changes the root, which is the correct behaviour — the
  root is a function of the content.
- **`114,798` dropped segments is the largest number in that output.** 22% of
  the real corpus is not assertable prose. It is counted and reported, never
  dropped silently (ADR-012).

The auditor runs in seconds because ADR-014 made it set-based. On the
pre-ADR-014 code it never completed at all — ~10^11 row visits, killed at
14 minutes.

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
| **The reviser** | Propagates a retraction transitively through derived and attested dependencies; only ever moves claims down | `knowledge/revision.py` |
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
18,930 real documents    →  336,190 claims, identical root reversed
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

**Verified on the full corpus.** All 324,951 claims evaluated, end to end.
(The figures in this section are Phase 10's, measured on the 324,951-claim build
that existed then. ADR-012 and ADR-015/016 later changed what the corpus
segments into, so the current artifact is 336,190 claims — see *Reproducing
the artifact* above. The before/after comparison below is what matters here.)

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

**And then the independent auditor ran on it** —
[ADR-014](docs/adr/ADR-014-auditor-must-run.md) — which is where the last two
defects were, both in the thing that verifies everything else:

```
claims: 324,951   resolved: 308,935   clean: true   7.7s   exit 0
```

The auditor had never completed on a real artifact: it looped over every claim
and issued an unindexed `SELECT` per claim, ~10^11 row visits. It is now
set-based, and the store gained the two indexes it was missing.

Worse, the first set-based version reported **1,434 span mismatches on a clean
artifact**. SQLite's `substr()` is NUL-terminated, one ChatGPT export contains
a literal NUL, and every span past it read as empty. The provenance was correct
in all 1,434; the checker was wrong. Span comparison moved to Python, where a
NUL is just a character.

> A noisy auditor is worse than no auditor. It trains its readers to ignore it,
> and a reader who has learned to ignore it will also ignore the output when a
> real defect appears. It does not degrade to useless — it degrades to
> *anti-useful*. So the tests now assert both directions: the auditor must catch
> a planted defect, and it must not invent one.

Tamper check on the real artifact — one byte changed in one source:

```
exit 1, span_mismatches: 3, resolved 308,935 -> 308,932
```

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
