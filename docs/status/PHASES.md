# Phase history

Moved out of `README.md` (ADR-025). The README answers "what is this, how do I run
it, what is broken". This file answers "what happened, and why did each decision go
the way it did" — which is much longer, changes on every task, and is read far less
often than the first three questions.

Kept verbatim rather than rewritten. The measurements in here are the ones taken at
the time, including several that later runs corrected; where a number was stale it
was annotated in place rather than edited out, because "this was wrong, and here is
what replaced it" is the useful record.

---

**Phase 17 — two claims that share nothing are not a contradiction.**
457 tests passing on Python 3.12, stdlib-only, offline.

Reading the verdicts of the run above surfaced a second evaluator defect,
narrower to fix and more dangerous in kind, because `CONTRADICTED` is
absorbing:

```
'Я - гангстер, я - настоящий босс, ...'
    contradicted by
'But that's not what this was.'
```

Contradiction required a term-set match plus a polarity flip. An empty term
set is a *valid* term set and `frozenset() == frozenset()` is `True`, so two
claims that normalize to nothing were "the same proposition, opposite
meaning." Two causes: `_TOKEN_RE` is `[a-z0-9]+`, so **186 of 221 Cyrillic
claims** produce no terms at all; and `is_proposition()` accepts a
33-character sentence of pure English function words. 193 empty-set claims at
one polarity, 1 at the other — that one sentence was marked `CONTRADICTED` by
all 193.

[ADR-020](docs/adr/ADR-020-empty-sets-do-not-contradict.md) guards both sides:
contradiction requires a *shared* term set. The tokenizer is deliberately
**not** fixed — the stemmer is English-only, and applying it to Cyrillic would
manufacture worse errors than admitting ignorance. Non-Latin claims stay
correctly stored and correctly marked not-evaluable by this method.

**Not fixed, and recorded rather than guessed:**
[docs/open-questions/code-contradictions.md](docs/open-questions/code-contradictions.md)
holds the measurement. After ADR-020, ~75% of remaining contradictions are
not semantic contradictions — two versions of one function, or a URL plus a
stray "No". But the prose class also contains the component working exactly as
designed: the evaluator independently found that the corpus argues both *"It
is also a pyramid scheme"* and *"THIS IS NOT A PYRAMID SCHEME!"*. Any filter
written now would be a threshold fitted to a corpus that is 44% ChatGPT
output pasted from a coding assistant — the failure `MIN_CLAIM_CHARS`' own
comment warns against. Settling it needs a hand-labelled sample and a decision
on whether the compiler should emit code as claims at all.

**Phase 16 — a duplicate is not a witness to itself.**
457 tests passing on Python 3.12, stdlib-only, offline.

The artifact had never been evaluated. It was compiled with `--no-evaluate`
and carried 319,293 `UNEXAMINED` claims and **zero** evaluations, because the
last full evaluation run was on the pre-ADR-012 artifact that was later
deleted. So the `evidence → verification` arrow in the diagram above had never
executed on the current artifact at all — the third arrow to be decorative,
after belief revision and `→ next version`.

Running it on a 300-document slice produced this:

```
supported      6,075   (65.5% of 9,273 claims)
inconclusive   2,443
derived          755
contradictions     0
```

A 65% support rate from a deterministic *lexical* evaluator is not a plausible
result, and it should have been the first thing that looked wrong. It was.
Every sampled verdict looked like this:

```
SUBJECT : 'Looking forward to the conversation!'
ATTESTER: 'Looking forward to the conversation!'
ATTESTER: 'Looking forward to the conversation!'
```

The subject is attested by byte-identical copies of itself. There are eleven
claims in the corpus with that exact text — it is a ChatGPT sign-off — and each
one attests the other ten.

[ADR-019](docs/adr/ADR-019-a-duplicate-is-not-a-witness.md): **97.1% of all
`SUPPORTED` claims were attested only by copies of themselves.** Only **88**
had a genuine attester.

The cause is that "a claim may never be its own evidence" (ADR-006 §2.2) was
implemented as an *id* comparison. A claim's id hashes its source metadata, so
one sentence in eleven documents is eleven claims with eleven ids — correctly,
each with its own provenance span. Excluding the subject by id excluded one row
out of eleven and let the other ten attest it. Self-attestation does not
require sharing an id; it requires sharing a **proposition**, and the
attestation path never asked that question.

The fix is one line — attestation now requires a *different normalized
proposition*, not a different row:

```python
if self._norms[cid].sequence != subject.sequence
and subject.is_contiguous_in(self._norms[cid])
```

Same slice, after: **6,075 → 88**, with zero remaining attestations by an
equivalent claim, and the 88 survivors are genuine substring relationships. The
fix is conservative in the documented direction — the excluded set only grows,
so nothing moves *up* the spine, and duplicates land in `INCONCLUSIVE` with
their neighbourhood recorded rather than being reported as an empty search.

This is the most serious defect in the project so far, and the reason is worth
stating plainly: **every individual record was correct.** The claims existed.
The evidence spans were verbatim. The evaluation records were present. The
auditor passed. Each of the 6,075 verdicts is a complete, well-formed,
honestly-recorded finding — *of nothing*. The graph is not lying in any one
place; it is lying in the relationship between places, which is the only place
a lie can hide that no single check will find. ADR-006's docstring named this
exact outcome as the thing it existed to prevent, and the project reached it
anyway through a different door.

**The auditor cannot catch this class**, and that is now demonstrated rather
than assumed. Independent verification checks that records are internally
consistent and true to their sources; no amount of that distinguishes a witness
from an echo. It took reading the *verdicts* to find it.

The regression tests deliberately use repeated text, and one of them pins *why*
the defect survived eleven phases: the comfortable explanation is "the fixtures
were single-source," and that is wrong — `ordinal` is in the content hash, so
repeating a sentence inside one document already makes two claims. The real
reason is simpler and worse: **no pre-ADR-019 fixture repeated a sentence at
all.** The defect was not hidden by a fixture shape; it was absent from every
example anyone wrote. Six of the nine new tests fail against the pre-fix code.

**Phase 15 — a claim's state is part of what the artifact says.**
448 tests passing on Python 3.12, stdlib-only, offline.

The README's own pipeline diagram claimed `belief revision → next version` for
eleven phases before the component existed, and after building the component
last phase the arrow *still* pointed at nothing. Both halves of that sentence
were defects, and the second was found by measuring rather than by reading.

[ADR-017](docs/adr/ADR-017-belief-revision.md) built the first half: a claim
entering `RETRACTED`, `INVALIDATED`, or `SUPERSEDED` invalidates everything
that transitively depends on it, through derived edges and attestation groups.
Transitive, fail-closed, only ever downward. 13 tests, each checked against a
sabotaged implementation.

[ADR-018](docs/adr/ADR-018-artifact-version-covers-state.md) is about the
second half — and it is the more serious of the two, because the artifact
version could not tell you the beliefs had changed. The Merkle root covered
claim *ids*, and a claim's id deliberately excludes its state: the id is the
hash of what the claim says, so a reassessed claim keeps its id. That is
correct. But `state_counts` was recorded in the manifest and **excluded from
the root**, and the manifest's own docstring already made the argument that had
been missed:

> Claim ids alone do not capture relationships… a version id that could not
> tell them apart would be lying about what it identifies.

That argument had been applied to edges and not to belief. So on the real
corpus:

```
root BEFORE revision: 177a5ee5afaf63e8fc40088764fa8287
true state_counts after: {unexamined: 319292, derived: 16896,
                          retracted: 1, invalidated: 1}
root AFTER  revision: 177a5ee5afaf63e8fc40088764fa8287
ROOTS EQUAL: True
```

Two claims changed state and the root was byte-identical. The consequence is
worse than a stale version string, because the witness is *defined* as a
version-bound view: its guarantee is enforced by a leaf-set membership check,
and a state change adds and removes no claim, so the check cannot see it.

```
witness version      : v1-177a5ee5afaf63e8
witness state_counts : {unexamined: 319293, derived: 16897}   ← false
TRUTH                : {unexamined: 319292, ..., retracted: 1, invalidated: 1}
```

Asked what it contained, it answered with numbers that were false, under a
version id that certified them. Every field it exposed was internally
consistent, and the one field that could have caught it could not change.

Three parts, because the identity fix alone leaves a window and the drift
check alone is bypassable:

- **State enters the root.** A SHA-256 over every sorted `(claim_id, state)`
  pair, as one leaf rather than 336,190. Order-independent, and cheap enough
  to verify: 0.59s on the real corpus. This is what makes
  `belief revision → next version` literal rather than aspirational.
- **A `state_epoch` tripwire.** The store carries a counter bumped inside
  `set_state` — the single funnel every transition passes through — and the
  witness re-reads it before every answer, raising `StaleWitness`. O(1).
  Re-deriving the digest per question was measured at 0.59s and rejected: a
  guard that is disabled under load is not a guard.
- **The auditor re-derives the digest itself.** The epoch is a counter in the
  database it guards, so direct SQL bypasses it. The auditor's copy is a
  second, independent implementation, because an auditor that calls the code
  it audits certifies only that the code agrees with itself.

It fails closed rather than silently refreshing. A witness asked a question
about one version and handed findings from another, without saying so, is the
precise failure this project is built against.

Verified on the real 336,190-claim artifact, in both directions:

```
clean artifact                →  clean: True    exit 0
one claim retracted via SQL    →  clean: False   exit 1
                                  state_digest_mismatch: 1
retract → revise → old witness →  StaleWitness raised
```

Each part was sabotage-tested: removing the `state:` leaf fails one test,
disabling the freshness check fails four, stubbing the auditor's digest fails
one. A test that cannot fail on the property is not testing the property.

The same revision that fixed a lying version string also made the suite
*faster* — 13.8s against a 13.5s baseline, from 136s in the first
implementation, by inlining the epoch bump into the transaction `set_state`
already commits rather than adding a round trip per claim.

### Two defects found in the fix itself

Worth recording because both were the auditor's own failure mode, which is
the one thing an independent auditor must not have.

**It invented a defect.** The first version reported a state-digest mismatch
against stores that never recorded a version — including several tests that
build a store directly through the Store API without compiling. Comparing a
digest against an empty string is not a finding; it is the auditor
fabricating one. *Uncheckable* is now distinct from *wrong*, and a test pins
it.

**It nearly made the guard disappear.** Re-deriving the digest per question
was 0.59s on 336,190 claims. Keeping it in the read path would have meant a
guard nobody could afford to run, which is a guard that is off.

**Phase 14 — a retracted claim takes its dependents with it.**
430 tests passing on Python 3.12, stdlib-only, offline.

Grepping `src/` for `revision` returned two English uses of the word in
comments and nothing else — the diagram had been describing a system with a
hole in it. The hole was not cosmetic. The corpus holds 16,897 `DERIVED`
heuristics, each resting via a `DERIVED_FROM` edge on the claims that produced
it. If a supporting claim is retracted, the heuristic does not get weaker — it
becomes *unsupported*, and nothing noticed. The artifact kept asserting it.

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

Measured on the real corpus, and worth stating because it is not what the edge
count suggests: 100,723 `DERIVED_FROM` edges over 16,897 derived claims resolve
to 100,723 *distinct* supports, each with exactly one dependent, and the
deepest chain is one level. The mined heuristics form a flat bipartite graph,
not a hierarchy — so on this corpus revision is shallow by construction, and
the transitivity guarantee is there for corpora where it is not. An earlier
draft of this README claimed a retraction "has a long way to travel"; measuring
it is what showed that to be false.

The auditor gained the matching `stale_dependents` check at this phase, still
importing nothing from the package.

The honest cost, stated in the ADR rather than discovered later: revision is
**conservative**. Losing one of five supports invalidates a heuristic that
four supports still justify. That is the wrong answer for a human, and the
right one for a system whose claim is that a stale belief must not survive its
own refutation. Relaxing it means an audited policy, not a smarter heuristic.

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
`sovereign-runtime/leaf/v1`, `sovereign-runtime/manifest/v1`,
`sovereign-runtime/state/v1`, and their siblings in `core/content.py` and
`compile/manifest.py`. Those are prefixes fed into the hash, so renaming them
would change every claim id, evidence id, and the Merkle root, invalidating a
336,190-claim artifact that currently audits clean. A hash domain is permanent
identity: it is fixed at first use and cannot be renamed after, exactly as a
database table already written to cannot be. These strings predate the rename
and stay.

`sovereign-runtime/state/v1` was added by ADR-018, in 2026, *after* the
rename — deliberately under the old prefix, for the same reason. The rule is
not "strings older than the rename are exempt"; it is that a domain is chosen
once and then frozen, and writing a new one does not retroactively license
rewriting the old ones.

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

## Phase 19 Task 5 — one-command clean-clone rebuild (COMPLETE)

Committed and pushed: `368c821..ee44de9`. Working tree clean, `0 0`.

### Real-corpus one-command build (the actual deliverable)

One command, from a clean clone, on the real corpus:

    python scripts/build_artifact.py --db build/ganymede4-corpus.db

| | measured |
|---|---|
| documents | 18,930 (35.8 MB) |
| machine turns fenced | 8,377 (44%) |
| compile | 175.7s |
| total build | 4,713.5s |
| compile-only root | `7e506ea556f633747ee9e703e675ef390c870cf141114098d144a6214b70ffce` |
| reversed-order recompile | identical |
| final version | `v1-49f86ced09fcb43b` |
| final root | `49f86ced09fcb43bf9964d686734453895f1f1cf1b89ac01b9e3492f40d1bf67` |
| state epoch | 319,293 |
| state digest | `c67635410714813c61dd36cbe195bace0e5217087486e8a745856d4476dc9355` |
| audit (separate process) | exit 0, clean |

The compile-only root and every final identity value reproduce the canonical
artifact exactly. This is the strongest available statement: the one-command
path and the incremental path that produced `/tmp/v424.db` over five phases are
the same computation.

Verdicts: 311,718 inconclusive / 16,897 derived / 7,173 supported / 402
contradicted, from 319,293 evaluations.

### Clean-clone validation

`git clone` → `python3 -m venv` → `pip install .` → run the console from a
directory with no repository in sight. Build, audit, verify, query all pass;
`--corpus` and `GANYMEDE4_CORPUS_ROOT` produce identical identity; generated
artifacts untracked.

This is where the substantive defects were found. All were invisible from a
source checkout, because in a checkout the scripts are simply present:

- `verify --audit` printed `clean: None` and exited **0**. The auditor path was
  derived from the installed module's location, where no `scripts/` directory
  exists. A verification command that cannot find its own checker reported
  success.
- Neither `audit_provenance.py` nor `build_artifact.py` was in the wheel at
  all, so an install shipped a console unable to do its one job.
- `--force` *added* `--keep-existing`, so it meant "never overwrite". Every
  console build refused to write, citing a flag the user never passed.

Fixed in `913a2eb` and `ee44de9`. ADR-025.

### Tests

**553 passed, 1 skipped, 40.0s** (500 before this task).

### ADR-026: what the root could not see

Two row edits were invisible to the root *and* to the auditor:

| tamper | root | auditor (before) | auditor (now) |
|---|---|---|---|
| `claims.text` | no | clean, exit 0 | **detected, exit 1** |
| `sources.content` | no | clean, exit 0 | **detected, exit 1** |
| `evidence.text` | no | detected | detected |

The root hashes content-addressed claim *ids*, so editing text without
recomputing the id cannot move it. That is content addressing, not a bug — a
root that reacted would have to hash the text, destroying ADR-004's guarantee
that compilation is a pure function of source bytes.

The hole was that `sources.checksum` existed since Phase 0, was written at
insert time, and **nothing ever read it back**.

Verified on all 319,293 real rows: zero false positives, audit 6.5s → 10.4s.
Cost: +3.9s for two whole-table passes. The alternative is a checker that
misses the exact property the project's thesis rests on.

### Still not detected

Deleting an `evidence` row is caught by the root (`verify` exits 1) but not by
the independent auditor. The auditor can check that evidence resolves, not that
it is *complete*. Recorded in ADR-026 rather than fixed: a completeness
assertion needs a definition of what a claim should cite, and guessing one
would be the threshold-fitting this project keeps declining to do.

### ADR-029: the model is not an input to the write

The write path existed only as an argument until this. `Runtime` holding no
store was the guarantee, and it was a guarantee about *one class* — a weaker
thing than "a belief cannot move unless independent evidence says it should".

Three separable authorities now stand between a proposal and a belief:

| | what it is | can it cause a write? |
|---|---|---|
| runtime verdict | citation check + `propose` grant | no |
| `derive` grant | a *separate* capability on the applier's own gateway | no |
| derivation | `Evaluator.evaluate(apply=False)`, knows no proposal exists | yes |

The state written is the derived one. The model's `proposed_state` is read for
exactly one purpose: to detect disagreement.

**A disagreement writes nothing.** Not the model's state, and not the derived
one. Silently writing the derived state would look identical to governing the
model while telling the reader nothing, and the disagreement is the most
informative thing in the run.

The `derive` grant being separate from `propose` is the load-bearing detail.
One capability would mean a policy that let models propose had no way to stop
them writing — and every policy would be wrong in the same direction.

Real artifact, genuinely permitted record, model asserting `supported` on a
claim the corpus contradicts:

```
OUT  : derivation-disagrees | derived: contradicted
STATE: contradicted  (unchanged)
```

319,293 evaluations after, distribution identical, auditor clean. The real
model never got that far — its one-character citation error (`ADR-028`) was
refused at `unknown-citation`, and the applier reported `not-permitted`
without deriving. Two independent failures, each sufficient.

`Applier.bind()` raises `TypeError` for anything but `store` and `gateway`, so
reaching the read-only loop from the write path is loud rather than subtle.

23 tests. Five sabotages, each caught by a different test — including *drop the
agreement gate* (ignores the model) and *write the model's state* (obeys it),
which look alike and are opposite defects.

**614 passed, 6 skipped.**

## Phase 20 — governing the model loop (COMPLETE)

**Thesis:** a model may be near the write path without ever being its
authority.

| ADR | decision |
|---|---|
| ADR-027 | the contradiction sample needs a human, not a heuristic |
| ADR-028 | a model that was nearly right is still wrong |
| ADR-029 | three authorities, and the model is only the first |
| ADR-030 | 2,854 contradiction pairs are 70 pairs |
| ADR-031 | `no-code` has no safe setting |

### The model is not an input to the write

Three separated authorities, none of which is the model:

| authority | can write? |
|---|---:|
| runtime citation validation + `propose` grant | no |
| separate `derive` grant in the `Applier` gateway | no |
| `Evaluator` backed by `Store` | **yes** |

The live `qwen3:4b` run produced one parseable proposal citing a claim ID
wrong by one character in 68 (`6` for `4` at index 15). It was refused as
`unknown-citation`. No prefix matching, no edit-distance repair. A nearly
correct but invalid citation is still invalid.

Where the model *was* permitted and proposed `supported`, independent
evidence derived `contradicted`. The `Applier` reported `derivation-disagrees`
and moved nothing.

### The contradiction population

2,854 subject/peer pair rows collapse to **70 distinct pairs** (40.8x
redundancy). One pair is 1,026 rows. Population-weighted: 87.7% code
fragments, 3.3% real contradictions.

`no-code` was selected on that evidence and then **declined at
implementation** (ADR-031): the structural detector catches 2,478 of 2,504
code rows but destroys a real contradiction living inside a string literal.
There is no setting that is both broad and safe.

### Correction found by review

ADR-031 first claimed the corpus has no fenced code blocks. It does:
**1,380 sources, 7,145 markers, 28,029 evidence spans (8.8%) inside fenced
regions.** The claim conflated ADR-016's fence-dropping with the absence of
fenced content. The correction does not rescue the policy — fences alone flag
59.6% of real contradictions — but the reason the policy fails is now
measured rather than asserted.

**663 passed, 6 skipped.**
