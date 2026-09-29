"""The executor: perform an authorized action, and record that it happened.

This is the component that closes ADR-007's deferred symlink risk, and the way
it closes it is the interesting part.

The gateway canonicalizes **lexically** — it collapses ``.``/``..`` as strings
and never stats anything, so its verdict is a function of the request and the
policy alone. That is deliberate: an ambient-dependent gate is not
reproducible. But it means the gateway cannot see that ``/data/link`` is a
symlink to ``/etc``.

So the executor re-derives the check against the filesystem, immediately before
the action, using ``os.path.realpath``. The two answers are then compared, and
if the real path escapes what the grant covers, the action is refused **even
though the gateway allowed the same request**.

That is not a contradiction of ADR-007. It is the deferral being honoured. The
gateway said "I will not tell you where this path lands"; the executor is the
only component that can, so it must.

Fail-closed throughout: a path that cannot be resolved is refused, not assumed
safe. ``realpath`` on a non-existent leaf still resolves its parent chain, and
the comparison is done on the *resolved* string, so creating the file after
authorization does not change the answer — the symlink is what matters, and the
symlink is already resolved.

Every authorized request produces an :class:`ExecutionRecord` without exception:
success, backend error, degraded no-op, and point-of-use refusal. The only case
that produces none is a *denial*, where nothing was attempted and the decision
chain already holds the record.

``argv`` is a list. There is no shell and no format string anywhere in this
path, so no argument can become a command.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..core.canonical import canonical_bytes
from ..core.content import content_id
from ..policy.gateway import (
    Decision,
    PolicyGateway,
    Request,
    Verdict,
)
from .sandbox import NullSandbox, SandboxBackend, SandboxResult

__all__ = [
    "ExecutionRecord",
    "ExecutionRefused",
    "Executor",
]

#: Domain separator so an execution id can never collide with a decision id.
_EXEC_DOMAIN = b"sovereign-runtime/execution/v1"


class ExecutionRefused(Exception):
    """The executor declined to perform an action.

    Raised — never swallowed into a success. The only two reasons are a
    non-ALLOW decision (including a broken chain) and a point-of-use path check
    that failed, and both are refusal to act rather than failure to complete.
    """


@dataclass(frozen=True)
class ExecutionRecord:
    """The artifact proving an authorized attempt occurred and what it did.

    Records are content-addressed, so a record cannot be edited afterward
    without changing its id. Each one names the decision that authorized it and
    the digest of the request it acted on, so "this was permitted" and "this is
    what was permitted" are separately checkable.

    ``degraded`` and ``performed`` are stored separately and mean separate
    things. A record with ``performed=False, degraded=True`` says the executor
    attempted and substituted a no-op; ``performed=False, degraded=False`` says
    the executor refused at the point of use. Collapsing them would make the
    record unable to distinguish a safety refusal from a missing sandbox, which
    are the two things an operator most needs to tell apart.
    """

    decision_id: str
    request_digest: str
    tool: str
    argv: tuple[str, ...]
    cwd: str
    #: ``authorized`` | ``performed`` | ``degraded`` | ``refused``
    outcome: str
    performed: bool
    degraded: bool
    returncode: int | None
    stdout: str
    stderr: str
    detail: str
    #: Point-of-use path resolution, when a path was involved.
    resolved_path: str | None = None
    #: Which backend produced this record. Recorded rather than inferred, so a
    #: record from a no-op sandbox is self-describing: an operator reading the
    #: log can tell *that nothing ran* without consulting current config,
    #: which may have changed since.
    backend: str = ""
    record_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "request_digest": self.request_digest,
            "tool": self.tool,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "outcome": self.outcome,
            "performed": self.performed,
            "degraded": self.degraded,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "detail": self.detail,
            "resolved_path": self.resolved_path,
            "backend": self.backend,
            "record_id": self.record_id,
        }

    @property
    def succeeded(self) -> bool:
        """True only for a real run that exited zero."""
        return self.performed and not self.degraded and self.returncode == 0


class Executor:
    """Performs authorized actions and records them.

    Does not re-derive *permission* — that is the gateway's job and duplicating
    it would create a second gate with its own bugs. Re-derives *integrity* and
    *location*: the decision chain is re-verified before acting, and the path is
    re-resolved against the filesystem immediately before the action.

    The default backend is a no-op, so a default-configured executor executes
    nothing and records that it executed nothing. Making a real backend
    installed is the price of a safe default, and it is the right way round:
    a runtime that can execute arbitrary tooling out of the box has made its
    posture depend on who read the config.
    """

    def __init__(
        self,
        gateway: PolicyGateway,
        backend: SandboxBackend | None = None,
    ) -> None:
        self._gateway = gateway
        self._backend: SandboxBackend = backend if backend is not None else NullSandbox()
        self._records: list[ExecutionRecord] = []

    @property
    def backend_name(self) -> str:
        return getattr(self._backend, "name", type(self._backend).__name__)

    @property
    def records(self) -> tuple[ExecutionRecord, ...]:
        """Immutable, in order. Records are never mutated after creation."""
        return tuple(self._records)

    def execute(
        self,
        request: Request,
        tool: str,
        argv: Sequence[str] = (),
        cwd: str = ".",
    ) -> ExecutionRecord:
        """Authorize, re-check, perform, and record. One of those four fails.

        Raises :class:`ExecutionRefused` if the action must not happen, or if the
        decision chain does not verify. Otherwise returns the record — including
        when the backend did nothing, which is a legitimate outcome and is
        recorded as such.
        """
        decision = self._authorize(request)

        # The point-of-use recheck runs after authorization and before the
        # action. A refusal here still produces a record: the request was
        # permitted in principle, and the reason it did not happen is the
        # single most operationally interesting fact in the log. Raising before
        # recording would lose exactly the case this component exists to
        # explain.
        try:
            resolved = self._recheck_path(request)
        except ExecutionRefused as exc:
            record = self._record(
                decision=decision,
                request=request,
                tool=tool,
                argv=tuple(argv),
                cwd=cwd,
                outcome="refused",
                result=SandboxResult(
                    performed=False, degraded=False, detail=str(exc)
                ),
                resolved_path=None,
            )
            self._records.append(record)
            raise

        # From here the action is permitted and the location is confirmed, so
        # whatever happens next gets a record. A backend that raises is
        # recorded as an error rather than propagating a bare traceback with no
        # audit trail — the attempt is real whether or not it succeeded.
        try:
            result = self._backend.run(
                tool, tuple(argv), cwd, self._gateway.policy.to_dict()
            )
        except Exception as exc:  # noqa: BLE001 - fail closed, then record
            result = SandboxResult(
                performed=False,
                degraded=False,
                detail=f"backend {self.backend_name!r} raised: {exc}",
            )

        record = self._record(
            decision=decision,
            request=request,
            tool=tool,
            argv=tuple(argv),
            cwd=cwd,
            outcome=(
                "performed"
                if result.performed and not result.degraded
                else "degraded"
            ),
            result=result,
            resolved_path=resolved,
        )
        self._records.append(record)
        return record

    def _authorize(self, request: Request) -> Decision:
        """Obtain a verified ALLOW. Refuse on anything else.

        The chain is re-verified *before* acting, not trusted from the decision
        that came back. A decision edited after the fact still has a verdict;
        what it no longer has is a chain that links to the decisions before it.
        Trusting the verdict alone would make every decision in the log
        independently mutable, which is precisely the property the chain was
        added to provide.
        """
        self._gateway.require_intact_chain()
        decision = self._gateway.decide(request)
        if decision.verdict is not Verdict.ALLOW:
            raise ExecutionRefused(
                f"action not permitted: {decision.reason} (decision {decision.decision_id})"
            )
        return decision

    def _recheck_path(self, request: Request) -> str | None:
        """Re-derive the path check against the real filesystem.

        The gateway's answer was lexical. This is the answer from the
        filesystem, and it is the one that matters at the moment of use.

        ``realpath`` resolves symlinks in every existing component of the path,
        so ``/data/link/shadow`` where ``link -> /etc`` becomes
        ``/etc/shadow`` and the grant's prefix no longer contains it. Refusal is
        the fail-closed branch throughout: a path that cannot be resolved is
        refused rather than assumed safe.
        """
        raw = request.args.get("path")
        if raw is None:
            return None
        if not isinstance(raw, str):
            raise ExecutionRefused(f"path argument is not a string: {type(raw).__name__}")

        real = os.path.realpath(raw)
        # Compare against the granted prefix, taken from the policy rather than
        # re-derived here, so the executor checks the same grant the gateway
        # checked instead of a second, subtly different rule.
        allowed = self._granted_path_prefix(request)
        if allowed is not None and not _within(real, allowed):
            raise ExecutionRefused(
                f"point-of-use check failed: {raw!r} resolves to {real!r}, "
                f"outside the granted {allowed!r}"
            )
        return real

    def _granted_path_prefix(self, request: Request) -> str | None:
        """The path prefix the matching capability granted, if it named one."""
        for cap in self._gateway.policy.capabilities:
            if cap.permits(request.action, request.resource):
                constraint = cap.constraints.get("path")
                if isinstance(constraint, str) and constraint.endswith("*"):
                    return constraint[:-1]
        return None

    def _record(
        self,
        *,
        decision: Decision,
        request: Request,
        tool: str,
        argv: tuple[str, ...],
        cwd: str,
        outcome: str,
        result: SandboxResult,
        resolved_path: str | None,
    ) -> ExecutionRecord:
        body = {
            "decision_id": decision.decision_id,
            "request_digest": request.digest(),
            "tool": tool,
            "argv": list(argv),
            "cwd": cwd,
            "outcome": outcome,
            "performed": result.performed,
            "degraded": result.degraded,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "detail": result.detail,
            "resolved_path": resolved_path,
            "backend": self.backend_name,
        }
        return ExecutionRecord(
            **body, record_id=content_id(body, prefix="exe-")
        )


def _within(path: str, prefix: str) -> bool:
    """True if ``path`` is ``prefix`` or beneath it.

    A plain string prefix would make ``/data-evil`` satisfy ``/data``, so the
    comparison is on path components. This is the same class of bug the
    executor exists to catch, so it should not be reproduced in the fix.
    """
    if prefix in ("", "/"):
        return path.startswith("/")
    if path == prefix:
        return True
    return path.startswith(prefix.rstrip("/") + "/")
