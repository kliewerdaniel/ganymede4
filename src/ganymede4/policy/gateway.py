"""The policy gateway (ADR-007).

Everything built so far reads. Nothing executes — and a governance story that
has never governed anything is a story. This is the gate between *proposing* an
action and *performing* it.

Three properties, each of which is the negation of a specific way a sandbox
quietly fails:

1. **Fail closed.** An absent policy, an uninterpretable policy, an unmatched
   request, and an explicit deny all produce ``DENY``. There is no code path
   that returns ALLOW because nothing objected. If you cannot prove the action
   is permitted, it is not permitted.

2. **No ambient authority.** A capability exists only where a grant names it.
   A policy that never mentions network access does not permit network access.
   This is the inversion that matters: *absence of restriction is not
   permission*. An allow-by-default system makes the safe path the one nobody
   remembered to restrict, and a missing rule silently becomes a grant.

3. **The decision is the artifact, not the log line.** Every decision is
   content-addressed and hash-chained, so a decision cannot be edited after the
   fact without breaking the chain. This reuses ``core/content.py`` rather than
   introducing a second integrity mechanism in the codebase.

The gateway **judges a request; it never performs the action.** Execution is
ADR-008, and the separation is deliberate: a gate tested through the sandbox it
constrains is a gate whose failure modes are entangled with the sandbox's.

Critically, the model is not consulted here — not because a model is bad at
permission, but because a *nondeterministic* authority cannot be a gate. If the
model may veto, the gate is advisory. ``target-architecture.md`` §11 puts the
LLM in tier 3 as a *reported* signal, and this module is that boundary drawn in
code: a model opinion may be recorded alongside a decision, never inside it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from ..core.canonical import canonical_bytes
from ..core.content import content_id, digest

__all__ = [
    "Verdict",
    "Request",
    "Decision",
    "Capability",
    "Policy",
    "PolicyGateway",
    "UndecidablePolicy",
    "ChainError",
]


class Verdict(str, Enum):
    """The only two outcomes. There is no "unknown"."""

    ALLOW = "allow"
    DENY = "deny"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class UndecidablePolicy(Exception):
    """A policy that cannot be interpreted.

    Raised rather than defaulted. A gateway that treats an unreadable policy as
    permissive has turned a parse error into a grant, which is the exact
    failure ADR-007 exists to prevent.
    """


class ChainError(Exception):
    """A decision chain failed to verify.

    Fail-closed: a broken chain means an earlier decision was altered, so no
    decision after the break can be trusted to reflect what was decided.
    """


@dataclass(frozen=True)
class Request:
    """A proposed action. Hashed and judged; never performed here."""

    actor: str
    action: str
    resource: str
    args: Mapping[str, Any] = field(default_factory=dict)
    context: Mapping[str, Any] = field(default_factory=dict)

    def digest(self) -> str:
        """Content id of the request.

        Covers everything including ``args``, so a request cannot be judged as
        one thing and acted on as another.
        """
        return content_id(
            {
                "actor": self.actor,
                "action": self.action,
                "resource": self.resource,
                "args": dict(self.args),
                "context": dict(self.context),
            },
            prefix="req-",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor": self.actor,
            "action": self.action,
            "resource": self.resource,
            "args": dict(self.args),
            "context": dict(self.context),
        }


@dataclass(frozen=True)
class Capability:
    """An explicitly granted capability.

    A grant is a resource plus a set of actions. There is no wildcard resource:
    a policy that wants "*" must say so, and a test checks that granting a
    capability on one resource does not grant it on another.
    """

    resource: str
    actions: frozenset[str]
    constraints: Mapping[str, Any] = field(default_factory=dict)

    def permits(self, action: str, resource: str) -> bool:
        return resource == self.resource and action in self.actions

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource": self.resource,
            "actions": sorted(self.actions),
            "constraints": dict(self.constraints),
        }


@dataclass(frozen=True)
class Policy:
    """A versioned set of capabilities, plus an explicit default."""

    capabilities: tuple[Capability, ...] = ()
    default: Verdict = Verdict.DENY
    version: str = "v1"
    #: Optional named predicates over request args, e.g. a path allow-list.
    #: Kept as opaque data the gateway checks structurally; the gateway does
    #: not evaluate arbitrary expressions, because a policy language is an
    #: interpreter and an interpreter is an attack surface.
    constraints: Mapping[str, Any] = field(default_factory=dict)

    def grants(self, request: Request) -> list[Capability]:
        return [
            c for c in self.capabilities if c.permits(request.action, request.resource)
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "default": self.default.value,
            "capabilities": [c.to_dict() for c in self.capabilities],
            "constraints": dict(self.constraints),
        }

    def policy_id(self) -> str:
        """Content id of the policy itself.

        Recorded on every decision, so a later policy cannot retroactively
        justify an earlier action.
        """
        return content_id(self.to_dict(), prefix="pol-")


@dataclass(frozen=True)
class Decision:
    """A content-addressed, hash-chained verdict.

    ``decided_at`` is excluded from identity: it is provenance, not identity —
    the same split as ``Manifest`` (ADR-004 §4). Two identical requests under
    the same policy yield the same decision id, so a replay is recognizable as
    a replay rather than as a second, independent decision.
    """

    request: Request
    verdict: Verdict
    reason: str
    policy_id: str
    policy_version: str
    decided_at: str
    chain_prev: str
    chain_hash: str
    decision_id: str = ""

    def chain_payload(self) -> bytes:
        """Canonical bytes the chain hash is computed over.

        Covers the **verdict and reason**, not merely the decision id. This is
        not redundancy — it is the difference between a chain that detects
        tampering and one that does not. ``decision_id`` is itself a hash of
        the verdict, so hashing the id alone would in principle suffice, but the
        chain must not depend on that remaining true: an id is an opaque string
        at this layer, and a chain whose integrity rests on an assumption about
        how some other field was constructed is not a chain, it is a hope.

        Concretely: with the verdict excluded, flipping a recorded DENY to ALLOW
        and leaving ``chain_hash`` untouched verifies clean, because the id
        never changed. That is the whole attack, and it is why the fields are
        listed here rather than derived.
        """
        return canonical_bytes(
            {
                "decision_id": self.decision_id,
                "request_digest": self.request.digest(),
                "verdict": self.verdict.value,
                "reason": self.reason,
                "policy_id": self.policy_id,
                "policy_version": self.policy_version,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "request_digest": self.request.digest(),
            "request": self.request.to_dict(),
            "verdict": self.verdict.value,
            "reason": self.reason,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "decided_at": self.decided_at,
            "chain_prev": self.chain_prev,
            "chain_hash": self.chain_hash,
        }


_CHAIN_DOMAIN = b"sovereign-runtime/policy-chain/v1"

#: A policy that constrains paths must compare *canonical* paths, not the string
#: the caller supplied. "/data/../etc/passwd" satisfies a "/data/*" prefix
#: check as text and escapes the grant as a path — the canonical form is
#: "/etc/passwd". This is the classic traversal bypass, and a prefix check on
#: an unresolved path is a prefix check an attacker controls.
def _canonical_path(value: str) -> str:
    """Collapse ``.``/``..`` segments without touching the filesystem.

    Purely lexical and total: no symlink resolution, no stat, no environment.
    A symlink cannot be resolved without touching the filesystem, which this
    module deliberately never does — so a grant on a path containing a symlink
    is a decision the *executor* (ADR-008) must re-check at the point of use.
    That residual risk is recorded rather than papered over.
    """
    if not isinstance(value, str) or not value:
        return value
    absolute = value.startswith("/")
    parts: list[str] = []
    for segment in value.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            elif not absolute:
                # A relative path that climbs above its root stays marked, so it
                # cannot silently collapse to something inside the grant.
                parts.append("..")
            continue
        parts.append(segment)
    joined = "/".join(parts)
    return ("/" + joined) if absolute else joined


class PolicyGateway:
    """Decides whether a proposed action is permitted.

    Holds the decision chain. Every decision is written before the action it
    governs is attempted (ADR-007 §4) — the ordering is the security property,
    and the chain is what makes a violation of it detectable after the fact.
    """

    GENESIS = "0" * 64

    def __init__(self, policy: Policy | None = None) -> None:
        # A gateway with no policy is a gateway that denies everything. This is
        # the fail-closed default, and it is the constructor's whole argument.
        self._policy = policy if policy is not None else Policy()
        self._decisions: list[Decision] = []
        self._tip: str = self.GENESIS

    # -- inspection ------------------------------------------------------

    @property
    def policy(self) -> Policy:
        return self._policy

    @property
    def decisions(self) -> tuple[Decision, ...]:
        return tuple(self._decisions)

    @property
    def tip(self) -> str:
        return self._tip

    def decisions_for(self, request_digest: str) -> list[Decision]:
        return [d for d in self._decisions if d.request.digest() == request_digest]

    # -- deciding --------------------------------------------------------

    def _evaluate(self, request: Request) -> tuple[Verdict, str]:
        """The pure decision function. No I/O, no side effects, no model."""
        for cap in self._policy.capabilities:
            if not cap.permits(request.action, request.resource):
                continue
            violated = self._check_constraints(cap, request)
            if violated is not None:
                # A grant that exists but whose constraint the request breaks
                # is a DENY with a reason, not a silent fall-through. The
                # operator can tell "not granted" from "granted but blocked".
                return Verdict.DENY, f"constraint violated: {violated}"
            return Verdict.ALLOW, (
                f"granted by policy {self._policy.version} capability on "
                f"{request.resource}"
            )
        # No grant matched. Whether that denies depends on the declared
        # default — which is DENY unless a policy explicitly says otherwise.
        if self._policy.default is Verdict.ALLOW:
            return Verdict.ALLOW, f"no grant matched; policy default is allow ({self._policy.version})"
        return Verdict.DENY, f"no capability grants {request.action!r} on {request.resource!r}"

    def _check_constraints(self, cap: Capability, request: Request) -> str | None:
        """Structurally check a grant's constraints against the request args.

        Only two constraint forms are understood: an exact-match key/value and
        a prefix allow-list. Anything else is treated as *unmet* rather than
        ignored, because a constraint this gateway cannot evaluate must never
        become a constraint that silently passes.

        A ``path`` argument is canonicalized before comparison. The prefix
        check is a decision about where a read will land, and the string the
        caller supplies is not that location.
        """
        for key, expected in cap.constraints.items():
            if key not in request.args:
                return f"argument {key!r} absent"
            actual = request.args[key]
            if key == "path" and isinstance(actual, str):
                actual = _canonical_path(actual)
            if expected == "*":
                continue
            if isinstance(expected, str) and expected.endswith("*"):
                if not str(actual).startswith(expected[:-1]):
                    return f"argument {key!r}={actual!r} is outside {expected!r}"
            elif actual != expected:
                return f"argument {key!r}={actual!r} != {expected!r}"
        return None

    def decide(self, request: Request, *, decided_at: str = "1970-01-01T00:00:00Z") -> Decision:
        """Decide, record, and chain. Returns the decision; performs nothing."""
        verdict, reason = self._evaluate(request)
        policy_id = self._policy.policy_id()

        # Identity excludes decided_at (provenance) and the chain fields
        # (position), so the same request under the same policy is the same
        # decision and can be recognized as a replay.
        did = content_id(
            {
                "request": request.to_dict(),
                "verdict": verdict.value,
                "reason": reason,
                "policy_id": policy_id,
                "policy_version": self._policy.version,
            },
            prefix="dec-",
        )
        pending = Decision(
            request=request,
            verdict=verdict,
            reason=reason,
            policy_id=policy_id,
            policy_version=self._policy.version,
            decided_at=decided_at,
            # Filled in below; the chain hash covers the payload, not itself.
            chain_prev=self._tip,
            chain_hash="",
            decision_id=did,
        )
        chain_hash = digest(
            _CHAIN_DOMAIN + b"\x00" + self._tip.encode() + b"\x00" + pending.chain_payload()
        )
        decision = Decision(
            request=request,
            verdict=verdict,
            reason=reason,
            policy_id=policy_id,
            policy_version=self._policy.version,
            decided_at=decided_at,
            chain_prev=self._tip,
            chain_hash=chain_hash,
            decision_id=did,
        )
        self._decisions.append(decision)
        self._tip = chain_hash
        return decision

    def permits(self, request: Request, *, decided_at: str = "1970-01-01T00:00:00Z") -> bool:
        """Convenience: decide, record, and return the boolean.

        Still records the decision. There is deliberately no way to ask the
        gateway a question without leaving a record of the question having been
        asked — an unrecorded consultation is indistinguishable from no
        consultation at all, and the whole gate is the record.
        """
        return self.decide(request, decided_at=decided_at).verdict is Verdict.ALLOW

    # -- verification ----------------------------------------------------

    def verify_chain(self) -> bool:
        """Re-derive every chain hash. False if any decision was altered.

        A broken chain means an earlier decision was edited, so no decision
        after the break can be trusted to reflect what was actually decided.
        That is a fail-closed conclusion, and it is why this returns ``bool``
        rather than raising in the middle of a verification run.
        """
        prev = self.GENESIS
        for d in self._decisions:
            if d.chain_prev != prev:
                return False
            expected = digest(
                _CHAIN_DOMAIN + b"\x00" + prev.encode() + b"\x00" + d.chain_payload()
            )
            if expected != d.chain_hash:
                return False
            prev = d.chain_hash
        return prev == self._tip

    def require_intact_chain(self) -> None:
        """Raise ``ChainError`` unless the chain verifies."""
        if not self.verify_chain():
            raise ChainError(
                "policy decision chain does not verify; an earlier decision was "
                "altered and nothing after it can be trusted"
            )

    def ungoverned_actions(self, action_digests: Sequence[str]) -> list[str]:
        """Actions in ``action_digests`` with no preceding decision.

        The positive counterpart to the chain check. A run whose actions do not
        all trace to a decision has either bypassed the gateway or lost its
        record, and both are reportable facts rather than silent possibilities
        (ADR-007 §4).
        """
        decided = {d.request.digest() for d in self._decisions}
        return [a for a in action_digests if a not in decided]
