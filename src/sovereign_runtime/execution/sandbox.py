"""Pluggable sandbox backends (ADR-008).

The contract the roadmap specifies is
``(tool, argv, cwd, policy) -> (result, audit-record)``. This module holds the
backend half; :mod:`.executor` holds the record half and is the only thing that
builds records.

The default backend is :class:`NullSandbox`, which performs nothing. That is not
a placeholder waiting to be filled in — it is the safe default, and the reason
is that a runtime whose out-of-the-box configuration can execute arbitrary
tooling has made its security posture depend on the reader of the config rather
than on the code.

``degraded`` and ``performed`` are separate fields on the result, and the
executor records them separately. A backend that cannot run the tool must say
so structurally, because the failure mode this guards against is a no-op
backend reporting success — the operator then reads an audit record asserting
an action that never happened, and has no way to tell.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

__all__ = [
    "SandboxResult",
    "SandboxBackend",
    "NullSandbox",
    "SubprocessSandbox",
]


@dataclass(frozen=True)
class SandboxResult:
    """What a backend reports happened.

    Two independent facts, deliberately not one:

    ``performed``
        The tool actually ran.
    ``degraded``
        The backend could not do the real thing and substituted a no-op.

    A real run that *failed* is ``performed=True, degraded=False``. A no-op is
    ``performed=False, degraded=True``. Collapsing these into a single
    ``ok`` field loses the distinction between "the tool said no" and "we never
    asked", which is the entire reason the record exists.
    """

    performed: bool
    degraded: bool
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "performed": self.performed,
            "degraded": self.degraded,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "detail": self.detail,
        }


@runtime_checkable
class SandboxBackend(Protocol):
    """A pluggable thing that can attempt an authorized action.

    Implementations perform the action or report that they did not. They do not
    decide whether they are permitted — that already happened upstream, and a
    backend that re-derives permission is a second gate with its own bugs.
    """

    name: str

    def run(
        self,
        tool: str,
        argv: Sequence[str],
        cwd: str,
        policy: Mapping[str, Any],
    ) -> SandboxResult:
        ...


class NullSandbox:
    """The default: performs nothing, and says so.

    Deliberately not a stub that pretends to work. ``run`` returns
    ``performed=False, degraded=True`` unconditionally — the honest answer when
    no backend is installed.
    """

    name = "null"

    def run(
        self,
        tool: str,
        argv: Sequence[str],
        cwd: str,
        policy: Mapping[str, Any],
    ) -> SandboxResult:
        return SandboxResult(
            performed=False,
            degraded=True,
            detail=f"null sandbox: nothing was executed for tool {tool!r}",
        )


class SubprocessSandbox:
    """Runs a real local process, with an explicit tool allow-list.

    Never used by default. When installed, three things still hold:

    - ``tool`` must be in ``allowed``. There is no wildcard and no path
      search, so a PATH-shadowing binary cannot be substituted for the tool
      name.
    - ``argv`` is passed as a list with ``shell=False``. Nothing is formatted
      into a string, so no argument can become a command.
    - A timeout is mandatory. A backend that can hang forever is a backend that
      can deny the operator the ability to reach the record.

    A test asserts no ``shell=True`` anywhere in this package: the difference
    between a list and a string is the whole difference between passing an
    argument and executing it.
    """

    name = "subprocess"

    def __init__(
        self,
        allowed: frozenset[str] | None = None,
        *,
        timeout: float = 30.0,
        env: Mapping[str, str] | None = None,
    ) -> None:
        if allowed is None:
            raise ValueError(
                "SubprocessSandbox requires an explicit tool allow-list; "
                "an empty allow-list would be a sandbox that runs everything"
            )
        if timeout is None or timeout <= 0:
            # Not a defaultable value. A sandbox with no timeout can be made to
            # hang indefinitely, and a hang means the operator never reaches the
            # record — the failure destroys the evidence it would have written.
            raise ValueError("SubprocessSandbox requires a positive timeout")
        self.allowed = frozenset(allowed)
        self.timeout = timeout
        self.env = dict(env) if env is not None else None

    def run(
        self,
        tool: str,
        argv: Sequence[str],
        cwd: str,
        policy: Mapping[str, Any],
    ) -> SandboxResult:
        if tool not in self.allowed:
            # Not an error: an unlisted tool is a tool this sandbox does not
            # have, which is a degraded no-op rather than a crash.
            return SandboxResult(
                performed=False,
                degraded=True,
                detail=f"tool {tool!r} is not in the allow-list",
            )
        try:
            proc = subprocess.run(  # noqa: S603 - list argv, shell=False, allow-listed tool
                [tool, *argv],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                shell=False,
                env=self.env,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                performed=False,
                degraded=True,
                detail=f"tool {tool!r} exceeded {self.timeout}s and was killed",
            )
        except OSError as exc:
            return SandboxResult(
                performed=False,
                degraded=True,
                detail=f"tool {tool!r} could not be started: {exc}",
            )
        return SandboxResult(
            performed=True,
            degraded=False,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
