# ADR-025: One command, one console, and no machine-specific paths

Status: Ratified
Date: 2026-09-30
Depends on: ADR-004, ADR-010, ADR-014, ADR-021, ADR-022

## Context

Phase 19 Task 5 asked for four hygiene fixes. Three of them were known defects
that had been written down and deferred. The fourth turned out to be the
interesting one, and it produced ADR-026.

1. There was no single command that built the artifact. The story was spread
   across `compile_corpus.py` (compile + evaluate), `audit_provenance.py` (a
   separate process, on purpose), and commit messages.
2. `README.md` was 1,096 lines, of which roughly 700 was phase-by-phase
   narrative, and it told a reader to run `/tmp/sr312/bin/python` — a path that
   exists on exactly one machine.
3. A zero-byte file named `--help` was tracked at the repository root.
4. `[project.scripts]` declared `ganymede4 = "ganymede4.cli:main"` and
   `src/ganymede4/cli.py` did not exist.

## Decisions

### 1. `scripts/build_artifact.py` — compile, evaluate, reseal, audit, print

One command, and it **fails closed**: a non-zero audit is a non-zero exit, and
the output says which step failed. A build that compiles but does not audit
clean is not a partial success; it is an artifact you should not use.

The auditor is invoked as a **subprocess, never imported**. This is ADR-014's
load-bearing property, and a refactor to `import audit_provenance` would look
like a harmless tidy-up while removing the only adversarial component in the
system. `tests/test_build_artifact.py` asserts it by parsing the AST, not by
grepping for a string, so a rename cannot slip past it.

Paths are arguments. `--db` defaults to `build/…`, `--corpus` to
`$GANYMEDE4_CORPUS_ROOT` or `~/Projects/Chris`, `--python` to the current
interpreter. `CORPUS_ROOT` in `corpus/load.py` became a default rather than a
constant so a clean clone on another machine reads the same artifact. A test
builds from two different corpus roots and asserts the claim counts differ —
a flag that is parsed and then ignored would pass every other test here.

The build does **not** compare against a recorded root. A hardcoded expected
hash would be a corpus-fitted constant wearing a test's clothes. The identity
check that matters is that reversing the input order reproduces the *same*
root on *this* run, which the compile step already performs.

### 2. The console is built, not deleted

Both options were defensible. Deleting the entry would have left `pip install`
succeeding while the command failed — which moves the breakage to a user's
machine and off this one. The console was built.

**Every subcommand except `build` is read-only.** This is a design constraint,
not an oversight: a console with a write path is a second, undocumented way to
change epistemic state that bypasses the policy gateway five phases were built
to construct. If you want to change beliefs, the path is the gateway, and the
gateway is not reachable from here. `build` writes a *new* database and refuses
to overwrite an existing one without `--force`.

`build` delegates to `build_artifact.py` rather than reimplementing the
pipeline. If the CLI grew its own compile path, "what does the console build"
and "what does the script build" would be two questions with two answers, and
a console that disagrees with the pipeline is a defect, not a convenience.

### 3. A reopened store can rebuild its own manifest

Task 5 needed `query` to work on an artifact the console did not compile, and
that was impossible: `Store.recorded_artifact()` returned a dict of strings, and
a `Witness` needs the concrete leaf-id sets to build its index over *that
version's claims only*. Nothing in the codebase could reconstruct a manifest
from stored rows.

`compile/reopen.py` adds `rebuild_manifest` and `manifest_matches_recorded`.
It **recomputes** the root from the rows on disk rather than loading a stored
manifest, so an out-of-band edit shows up as a mismatch instead of being
described back to you. Verified against the real 336,190-claim artifact: the
rebuilt root equals the recorded root exactly.

Two details that would otherwise have been bugs:

- **Heuristics are identified structurally**, by having an outgoing
  `DERIVED_FROM` edge, not by `state = 'derived'`. ADR-022's revision path can
  move a mined claim into `invalidated`, at which point a state-based lookup
  drops it from the leaf set and produces a root matching nothing — so a
  revision would appear to change the artifact's identity purely because a
  label changed.
- **The comparison uses an unflushed reader.** `recorded_artifact()` flushes a
  pending seal; comparing a rebuilt manifest against a *flushed* recording
  would compare the store against a digest just recomputed from it — equal by
  construction, and therefore incapable of detecting anything. This is why
  `Store.recorded_artifact_unflushed()` is public, and
  `test_the_check_does_not_flush_a_pending_seal` pins it.

### 4. The stray `--help` was never produced by anything

The obvious hypothesis was `test_scaling.py`, which shells out to
`compile_corpus.py --help`. **That hypothesis is wrong**, and worth recording:
running the test with the file deleted does not recreate it, and the script's
argparse handles `--help` correctly and creates nothing. The file is zero
bytes, was committed in `35f46b4`, and is referenced by no code, test, or
document. It was a shell redirect, tracked by accident.

It is deleted and untracked. It is **not** added to `.gitignore`, because
`.gitignore` entries are a claim that something generates the file, and adding
one would assert a false provenance about its own absence. A test asserts it
stays gone, since "delete it" is otherwise indistinguishable from "delete it
once".

### 5. README: 1,096 lines → 158

`README.md` now carries thesis, diagram, how to run, the current phase in ~15
lines, and open problems. The 460-line phase history moved to
`docs/status/PHASES.md`, **verbatim** — including the numbers later runs
corrected, annotated in place rather than edited out, because "this was wrong,
and here is what replaced it" is the useful record.

A "which numbers belong to which build" table was added, because three
artifacts are in circulation and conflating them is how a stale number
survives. It names the compile-only root (`7e506ea556f63374`), the current
evaluated artifact (`v1-49f86ced09fcb43b`), and the pre-ADR-024 comparison
baseline (`v1-1b2a81e128eadf5d`), and explains why the compile root is
unchanged across the last two while the version moves: that is ADR-018
working.

## Verification

- `tests/test_build_artifact.py` — 15 tests. 500-test suite, stdlib-only.
- `tests/test_reopen_manifest.py` — 7 tests, including the falsifiable half:
  content edits, state edits, and row deletions must all change the rebuilt
  root.
- Real artifact: rebuilt root `49f86ced09fcb43b…` equals the recorded root.
- `--help` test confirms no code produces the file; the test is not the cause.

## What this ADR does not decide

- Whether the console should ever gain a write path. If it does, that is a
  governance change and needs its own ADR.
- The real-model loop (Task 4) and the contradiction labelling sheet
  (Task 3, blocked on the user).
