"""Execution downstream of authorization (ADR-008).

ADR-007 built the gate and deliberately performed nothing, which left one risk
deferred rather than solved: the gateway's path canonicalization is **lexical**,
so a symlink satisfies a ``/data/*`` grant as a string and escapes it as a path.
This package is where that risk is closed, at the last moment before the action.

The division of labour is an asymmetry, not an inconsistency:

- The **gateway** is ambient-free. It collapses ``.``/``..`` by string
  manipulation and never stats anything, so its answer depends only on the
  request and the policy.
- The **executor** touches the filesystem, because only the filesystem knows
  where a path actually lands.

So a request can be allowed by the gateway and denied by the executor. That is
the point. A single verdict field would have hidden the difference between
"nobody permitted this" and "permission was granted, but the thing you named is
not the thing you get".

Three properties, each the negation of a specific way a sandbox quietly fails:

1. **Fail closed at the last moment.** A point-of-use recheck that cannot
   resolve a path denies. A backend that raises denies *and records*. There is
   no path where an exception becomes a permission.

2. **A degraded backend cannot masquerade as a working one.** ``degraded`` and
   ``performed`` are separate fields and are tested separately, because
   conflating them produces audit records that lie in exactly the situation
   where the operator is least able to check them.

3. **The record is not contingent on the action.** No-op, backend error, and
   point-of-use refusal all produce an ``ExecutionRecord``. The only thing that
   skips the record is a *denial* — nothing was attempted, so there is nothing
   to record, and the decision chain already holds the denial.
"""

from .executor import ExecutionRecord, Executor, ExecutionRefused
from .sandbox import (
    NullSandbox,
    SandboxBackend,
    SandboxResult,
    SubprocessSandbox,
)

__all__ = [
    "Executor",
    "ExecutionRecord",
    "ExecutionRefused",
    "SandboxBackend",
    "SandboxResult",
    "NullSandbox",
    "SubprocessSandbox",
]
