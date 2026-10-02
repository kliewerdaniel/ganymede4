# ADR-033: an interrupted build must be resumable, and resume must be invisible

**Status:** Ratified
**Date:** 2026-10-01
**Depends on:** ADR-021, ADR-022, ADR-025, ADR-032

## Context

A full-corpus build of the 18,930-document corpus is dominated by
deterministic evaluation of 319,293 claims. It is long enough that the run will
be interrupted — a reaped background job, a closed laptop, a full disk. It
already has been: a run reached 19,485 evaluations (6.1%) and was terminated
with `EXIT=143`, leaving a 560 MB database that was neither a finished artifact
nor a resumable one.

> **Correction, measured during implementation.** This ADR originally said a
> full build takes "roughly eight hours". That figure was never measured, and
> it is wrong by about a factor of five: the resumed run is sustaining ~78
> claims/s, which puts a complete 319,293-claim evaluation at roughly **1.1
> hours**. The estimate appears to have been reasoned from ADR-021-era timings
> rather than observed. The correction does not weaken the decision — an hour
> is still long enough to lose to a closed laptop, and the defect cost two
> interruptions and produced nothing — but the argument should not be inflated
> by a number nobody checked. Left visible here because the wrong figure was
> already quoted as justification.

Before this ADR, `Evaluator.evaluate_all` was a single list comprehension over
every claim with no progress recorded anywhere. An interruption discarded the
entire run, and the next attempt started from zero. Two interruptions cost
sixteen hours and produced nothing.

The same defect had been fixed once already. ADR-021 moved a re-seal out of the
caller and into `evaluate_all` on the reasoning that "a fix which relies on the
caller remembering is the same bug again". That reasoning applies here with
more force: there was no caller to rely on, because no mechanism existed at all.

## The decision

`evaluate_all` gains two keyword arguments, and a sibling method is added.

- `only: Sequence[str] | None` restricts the run to the named claims.
- `progress_every: int` writes a progress marker to stderr. Off by default:
  a library has no business printing, and the build script is what turns eight
  hours into a visible wait.
- `evaluate_ids(only=..., progress_every=...)` runs the same pass and returns
  the claim ids it covered. `contradictions()` reads verdicts per claim, which
  is what it was doing with the discarded list anyway.

**`evaluate_all` still returns `Verdict` objects.** An earlier version of this
work changed it to return claim ids, on the reasoning that the only caller
wanted a count. That reasoning was wrong about the cost: `evaluate_all` has
public callers, and 45 tests across six modules read `.subject_id` off its
result. All 45 broke. The suite caught the regression, but only because
unrelated tests happened to call the method — a consumer outside this repo
would have found out at runtime. The new method is additive;
`test_evaluate_all_still_returns_verdicts` pins the contract so the next
attempt has to argue with it rather than discover it.

The new method also has a memory reason to exist. `evaluate_all` materialises
every `Verdict` in a list to keep 156 contradicted ones, which at 336,190
claims is a half-million objects alive simultaneously.

`scripts/build_artifact.py` gains `--resume`, which keeps an existing `--db`
instead of deleting it, reuses the compiled claims through `rebuild_manifest`,
and evaluates only the claims that hold no verdict. The reversed-order
determinism check is skipped on a resumed run: it recompiles the corpus into
the very store being resumed, and determinism does not become conditional on
how a run was interrupted.

Resume is caller-side deliberately. "Which claims already hold a verdict" means
querying the `evaluations` table, which is the store's business; the build
script holds both halves, so it runs the query and passes `only=`.

### Why skipping is sound, not merely convenient

Two existing properties make a skipped claim indistinguishable from an
evaluated one:

1. `evaluate_all` iterates `sorted(self._norms)` — a deterministic order,
   independent of insertion order and of run history.
2. `Store.record_evaluation` writes a content-addressed `evl-` id with
   `INSERT OR IGNORE`, so re-evaluating a claim that already holds a verdict is
   a no-op rather than a duplicate.

Indistinguishable is the stronger claim and the one that matters: **the
artifact must not record that a resume happened.** A resumed run and an
uninterrupted run must produce the same version, root, and state digest.

## A defect this decision exposed

The first implementation satisfied every unit test and still produced the wrong
artifact. `rebuild_manifest` selected `claim_ids` with `SELECT id FROM claims`,
which includes the miner's own output. The compiler appends source-derived
claims to `claim_ids` *before* mining runs, so a heuristic is a leaf but never
a member of the set it was derived from. Folding them in made a resumed
manifest structurally different from a compiled one:

```
uninterrupted   v1-c005ee6adccc4ce6   2,410 evaluations   314 derived
resumed         v1-4f841bfa7dd2b590   2,724 evaluations     0 derived
```

The bug pre-dated this ADR — `rebuild_manifest` had been validated only against
a fully evaluated artifact, where the two criteria happened to select the same
16,897 claims and the discrepancy stayed invisible. Resume is what made it
observable, by asking a question the old tests never asked.

## Testing, and the tests that failed to be tests

The first sabotage pass found that **three of three sabotages survived**. The
unit tests asserted plumbing — that `only=` was passed, that `--resume` existed
— none of which can observe whether the work was actually skipped.

| Sabotage | Unit tests | End-to-end |
|---|---|---|
| heuristic-exclusion reverted | survived | **caught** |
| `--resume` deletes the db anyway | survived | **caught** |
| resume re-evaluates everything | survived | **survived** |

The fix was tests that build an artifact both ways and compare identity, rather
than tests that check arguments were threaded correctly. That is a slow test
and it is the only kind that would have caught the `rebuild_manifest` defect.

The third sabotage needed a different instrument entirely. Dropping
`only=todo` produces a **correct** artifact — `INSERT OR IGNORE` makes redundant
work a no-op — so no identity comparison can see it, and neither can counting
rows in the database, since both runs write the same rows. It is a performance
defect wearing correctness clothing: on the real corpus it turns an eight-hour
resume back into a full eight-hour restart.

It is now caught by reporting the number of claims the run says it evaluated,
taken from `evaluate_all`'s return value rather than from the build script's own
todo list. A plan and a result are different claims, and only the second is
evidence.

Final matrix, all caught, plus the combined three-way case:

| Sabotage | Tests failing |
|---|---:|
| heuristic-exclusion reverted | 2 |
| `--resume` deletes the db | 3 |
| resume re-evaluates everything | 1 |
| all three combined | 4 |

## Fail-closed on a missing artifact

`--resume` was first implemented as `if db.exists() and args.resume`, with no
statement about the other branch. Given a `--db` that does not exist, it fell
through to an ordinary build: the corpus was compiled from scratch, the flag
was ignored, and the exit code was 0.

That is the failure this ADR exists to prevent, reappearing under the flag
that claims to fix it. The operator asked to save eight hours and spent them
instead, with nothing in the output to say the flag had been dropped. Silent
is the operative word — the run is *successful*, just not what was asked for.

A resume is a claim about an artifact that already exists. If it does not,
the honest answer is refusal:

```
FAILED: --resume needs an existing artifact, but build/x.db does not exist.
  drop --resume to build from scratch.
```

The check runs before the corpus is touched, so a typo in `--db` cannot also
trigger a full compile. `test_resume_refuses_a_database_that_does_not_exist`
deliberately passes no `--corpus`, so a regression that made the check
conditional on the corpus being reachable would fail for the wrong reason
and could be mistaken for a pass.

Sabotaged by replacing the condition with `if False:` — the test fails;
restored, it passes.

## Honest limits

- **Resume is not checkpointing.** Progress lives in the `evaluations` table
  itself, so there is no separate journal to corrupt and nothing to keep in
  sync. The cost is that a claim interrupted *mid-evaluation* is simply not
  there, and is redone.
- **The `progress_every` marker counts, not rates.** An eight-hour run gives no
  ETA; the operator measures a window themselves, as was done here.
- **The signal that killed `rb3.db` was never identified.** `EXIT=143` is
  SIGTERM with no traceback. Resume makes that survivable; it does not explain
  it, and this ADR does not claim to.
- **`rebuild_manifest` remains a reconstruction, not a record.** It infers
  structure from rows. This ADR made one of its inferences load-bearing for
  artifact identity, which is a stronger demand than it previously carried, and
  a future change to claim emission could invalidate it silently.
