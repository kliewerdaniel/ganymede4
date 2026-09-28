# ADR-001 — License, and the vendoring boundary it draws

**Status:** Ratified (2026-09-27) for the Phase 0 vendor set; **open** for the wider estate
**Context:** `docs/architecture/migration-map.md` F1

## Context

The framework is meant to be open source. Four repos carrying load-bearing
epistemic design have **no LICENSE file**:

- `~/Projects/ganymede3`
- `~/authority-epistemic-protocol`
- `~/Projects/authority-graph-compiler`
- `~/Projects/sovereign-intelligence`

Copying or relicensing unlicensed code into a published repository is a legal
problem, not a technical one, and it is the one fork I cannot resolve on the
owner's behalf.

The two best-tested donors are unaffected:

- `~/Documents/Projects/hermes-atlas` — **MIT**, 4.7k LOC
- `~/Documents/Projects/sovereign-worker` — **MIT**, 12.6k LOC, 490 tests

## Decision

**The framework is Apache-2.0.** Matches `right-to-compute`, the most
protocol-oriented repo in the estate.

**Phase 0 vendors from MIT sources only.** `hermes-atlas` and `sworker` carry
license headers and attribution on every lifted file. `NOTICE` records origin,
upstream commit, and license per file.

**`ganymede3` and AEP are read, not vendored, until licensed.** Their *design*
is carried forward — the 10-state vocabulary, the proposal-only model, the
proposal types — but the implementation is written fresh against `data-model.md`
rather than copied. This is more work and it is the correct trade: a design is
not copyrightable in the way an implementation is, and it keeps the repository
publishable regardless of when the licensing question gets settled.

**Full-Apache-2.0 is not claimed for vendored MIT code.** MIT code is
permissively compatible, but the vendored files stay MIT-attributed.

## Consequences

**Positive.** Phase 0 proceeds immediately. The repository is publishable today.
The licensing question becomes a *future optimization* (lift the real
`ganymede3` implementation once licensed) rather than a *blocker*.

**Negative.** The 10-state vocabulary and proposal types are reimplemented rather
than ported — more lines, and a risk of drift from upstream. Mitigation: the
`ganymede3` core suite (163 tests) is the conformance oracle; Phase 0's gate is
that the reimplementation satisfies the same invariants.

**Neutral.** Nothing about this ADR changes the architecture. It changes *provenance
of every line*.

## Alternatives

1. **Block all development until licensed.** Rejected: the MIT donors cover
   Phase 0 entirely. Deferring available, lawful work is not caution, it is waste.
2. **Assume ownership and relicense the unlicensed repos.** Rejected: these are
   the owner's repos, so this is a real option, but it is a *publication*
   decision, and the framework should not be publishable by accident.
3. **Permissive (MIT/BSD) for the framework.** Rejected: Apache-2.0's explicit
   patent grant suits a protocol framework, and matches `right-to-compute`.

## Acceptance

- [x] `LICENSE` is Apache-2.0
- [x] Every vendored file carries an MIT header naming its upstream repo
- [x] `NOTICE` lists upstream repo + commit + license per vendored file
- [x] No file was copied from `ganymede3`, AEP, `authority-graph-compiler`, or
      `sovereign-intelligence`
- [ ] **OPEN:** if the owner later adds a LICENSE to those four repos, a follow-up
      ADR may replace the reimplementation with a true port
