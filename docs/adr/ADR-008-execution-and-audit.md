# ADR-008: Execution is downstream of authorization, and it is recorded either way

Status: Ratified (2026-09-28)
Depends on: ADR-003, ADR-007

## Context

ADR-007 built the gate. It judges a `Request` and records a `Decision`, and it
deliberately performs nothing. That separation was chosen so the gate could be
tested without a sandbox, and it is correct — but it left one risk explicitly
deferred rather than solved.

The gateway's path canonicalization is **lexical**. It collapses `.` and `..` by
string manipulation and never touches the filesystem, because a gateway that
stats the filesystem is a gateway whose answer depends on ambient state. The
comment in `policy/gateway.py` says so, and names the residual risk: a
symlink. `/data/link -> /etc` satisfies a `/data/*` grant as a string and
escapes the grant as a path. The gateway cannot see this and does not claim to.

So the risk does not disappear. It moves to whoever actually opens the file.
Someone has to close it, and the honest place is the last moment before the
action — the **point of use**.

Second, the roadmap's Gate 4 requires a contract:
`(tool, argv, cwd, policy) -> (result, audit-record)`, with the audit record
emitted **even when the sandbox is a no-op**. That clause is the interesting
one. A degraded sandbox is exactly when an operator most needs to know what was
attempted, and it is also when a lazy implementation skips the record because
"nothing happened". Nothing happened *because the executor chose not to*; the
attempt still happened, and it is still the executor's.

## Decision

1. **The executor is downstream, and refuses to act on anything but a verified
   `ALLOW` decision.** It does not re-derive permission. It re-derives
   *integrity*: before acting it re-verifies the decision chain, and a broken
   chain is a refusal. A decision altered after the fact is not a permission.

2. **The executor does its own path recheck at the point of use, using the
   filesystem.** The gateway is lexical and ambient-free; the executor is the
   opposite, and that is the division. If the resolved real path of a target
   escapes the granted prefix, the action is denied — even though the gateway
   approved the same request. This is not a contradiction of ADR-007. It is
   ADR-007's deferral being honoured.

3. **A pluggable sandbox backend, and the default is a no-op.** `NullSandbox`
   performs nothing and reports that it performed nothing. The executor does
   not know whether a backend actually ran the tool. It records what the
   backend claimed, and separately records whether a real backend was
   installed. A degraded path that reported success without saying it was
   degraded would be indistinguishable from a real one, and that ambiguity is
   the whole risk.

4. **Every authorized request produces an `ExecutionRecord` — no exceptions.**
   Success, backend error, degraded no-op, and point-of-use refusal all produce
   one. Records are content-addressed, carry the `decision_id` and the request
   digest they correspond to, and record `performed` and `degraded`
   separately. A record is not a log line; it is the artifact that makes the
   claim "this was authorized and here is what occurred" checkable later.

5. **Denied requests produce no `ExecutionRecord`.** Nothing was executed, so
   there is nothing to record. The decision chain already holds the denial. A
   record here would imply an attempt that did not occur.

6. **`argv` is data, never a format string.** No shell, no interpolation, no
   `shell=True`. The interpreter is given a list or it is not run.

## The three properties, each the negation of a specific failure

**Fail closed at the last moment.** A point-of-use recheck that cannot resolve
a path denies. A sandbox that raises denies and records. There is no path where
an exception becomes a permission.

**A degraded backend cannot masquerade as a working one.** `degraded` and
`performed` are separate fields and are tested separately, because conflating
them produces a system whose audit records lie in exactly the situation where
the operator is least able to check them.

**The record is not contingent on the action.** No-op, failure, refusal — all
recorded. The only thing that skips the record is a denial, where nothing was
attempted.

## Consequences

Accepted:

- The executor touches the filesystem and therefore is **not** ambient-free. It
  is a different kind of component from the gateway, and the asymmetry is
  deliberate: authorization should be reproducible, use should be careful.
- A `NullSandbox` default means the default configuration executes nothing. A
  real backend must be installed explicitly. Safe by default, and the price is
  that the happy path is not exercised out of the box.
- The lexical/realpath split means a request can be **allowed by the gateway and
  denied by the executor**. This is intended and tested. A single verdict field
  would have hidden the distinction between "nobody permitted this" and
  "permission was granted but the thing you named is not the thing you get".

Rejected:

- **One combined `check_and_run`.** Simpler, and it destroys the ability to test
  the gate without a sandbox and to record a decision independently of its
  execution.
- **Trusting the gateway's canonicalization at the point of use.** That is the
  symlink bug, written down as a design decision.
- **Skipping the record when the sandbox is a no-op.** Explicitly forbidden by
  Gate 4 and, more importantly, by the fact that the attempt is real.

## Boundaries

- Execution is still local and offline. No network fetch, no provider call.
- The model is not consulted here either. A model may *propose* a request; it
  may not influence whether it is performed.
- `SubprocessSandbox` runs a real local process with an explicit tool
  allow-list. It is never the default and never used with a shell.
- The A2A edge, the agent loop, and the console remain later phases.
