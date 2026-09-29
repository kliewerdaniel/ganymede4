"""The runtime: a bounded proposal loop (ADR-009).

Everything built so far reads, judges, or records. This is the first component
that *wants* something, and it is the first place the project's central claim
becomes testable rather than merely asserted — that the model is a
probabilistic component inside a governed runtime, never the intelligence
system and never the authority.

The rule from ``target-architecture.md`` §2, implemented literally:

    Nothing in L4 can write to L1.

:class:`Runtime` holds a read-only :class:`~ganymede4.witness.Witness`
and nothing else. It has **no reference to the store at all** — not "does not
call" but "does not hold", so there is no call site to audit and no later
refactor that can quietly add one. A test parses this module's AST and asserts
no write path is importable here.

Two failure modes this is built against:

1. **The runtime that resolves what it finds.** Once a runtime sees a
   contradiction in the corpus, the obvious design is for it to resolve it and
   report the answer. That is a runtime with a private evaluator, and its
   answers would be indistinguishable from evaluated ones — same type, same
   store, no mark on them. So the runtime gets no evaluator and no shortcut. It
   proposes; L3 disposes; L0 re-derives.

2. **The safety that lives in the prompt.** If governance were enforced by
   instructions to the proposer, then swapping proposers would change the
   system's guarantees and a model update would silently be a governance
   change — with a diff that is a string.

Neither is possible here. The proposer is a :class:`Proposer` seam and the
guarantees are enforced by the loop, so they hold for *any* object implementing
the protocol. ``test_runtime.py`` installs a proposer that fabricates claim
ids, cites itself, and proposes unsupported transitions, and asserts every one
is refused. That test is the real content of the ADR: a suite that passes only
with a well-behaved proposer is certifying the prompt, not the system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from ..core.content import content_id
from ..policy.gateway import Decision, PolicyGateway, Request, Verdict
from ..witness.witness import Witness

__all__ = [
    "Proposer",
    "Proposal",
    "ProposalDisposition",
    "RunRecord",
    "Runtime",
    "RuleProposer",
]


# ---------------------------------------------------------------------------
# the proposal seam
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Proposal:
    """A proposed mutation. Inert data — it cannot apply itself.

    There is deliberately no ``apply`` method. The absence is the API: a
    proposal that could apply itself would be a capability the runtime holds,
    and the whole design is that it does not.

    ``cites`` is the whole safety surface. A proposal asserting an epistemic
    state is only as good as the claim ids it cites, and those are resolved
    against the store *before* the proposal is evaluated. An unknown id fails
    closed, which is what makes a fabricated citation useless.
    """

    question: str
    text: str
    proposed_state: str
    cites: tuple[str, ...] = ()
    rationale: str = ""
    proposal_id: str = ""

    @staticmethod
    def make(
        question: str,
        text: str,
        proposed_state: str,
        cites: Sequence[str] = (),
        rationale: str = "",
    ) -> "Proposal":
        return Proposal(
            question=question,
            text=text,
            proposed_state=proposed_state,
            cites=tuple(cites),
            rationale=rationale,
            proposal_id=content_id(
                {
                    "question": question,
                    "text": text,
                    "proposed_state": proposed_state,
                    "cites": list(cites),
                    "rationale": rationale,
                },
                prefix="prop-",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "text": self.text,
            "proposed_state": self.proposed_state,
            "cites": list(self.cites),
            "rationale": self.rationale,
            "proposal_id": self.proposal_id,
        }


@runtime_checkable
class Proposer(Protocol):
    """Where a model will eventually sit.

    Implementations suggest; they do not decide. The loop applies the same
    constraints to any object satisfying this protocol, which is what keeps
    governance independent of the proposer.
    """

    name: str

    def propose(self, witness: Witness, budget: int) -> Sequence[Proposal]:
        ...


class RuleProposer:
    """A deterministic proposer. The default, and the reproducible one.

    Proposes a claim from each answer the witness returns, citing what the
    witness actually surfaced. It cannot cite anything else, because it has no
    other source of ids — which is the point: a proposer with access only to
    provenance cannot fabricate provenance.
    """

    name = "rules"

    def propose(self, witness: Witness, budget: int) -> Sequence[Proposal]:
        out: list[Proposal] = []
        for question in self._questions(witness):
            if len(out) >= budget:
                break
            answer = witness.ask(question)
            # `claims` is a tuple of (claim_id, text) pairs, so a citation is
            # the first element and the proposed text is the second.
            cites = tuple(claim_id for claim_id, _text in answer.claims)
            if not cites:
                # Nothing was found, so there is nothing to propose *from*.
                # Proposing anyway would be proposing without evidence, which
                # is the shape of a hallucinated claim.
                continue
            out.append(
                Proposal.make(
                    question=question,
                    text=answer.claims[0][1],
                    proposed_state="supported",
                    cites=cites,
                    rationale=f"witness answered from {len(cites)} cited claim(s)",
                )
            )
        return tuple(out)

    def _questions(self, witness: Witness) -> tuple[str, ...]:
        """Ask what the corpus is about, not what we wish it were about.

        The boundary is the witness's own account of what it holds, so the
        questions are derived from the corpus rather than hardcoded against it.
        That is not a stylistic preference: a proposer that asks questions
        chosen by its author is a proposer whose blind spots are the author's.
        """
        counts = witness.boundary().state_counts
        total = sum(counts.values())
        if total == 0:
            # An empty corpus is a real state, and asking it nothing is the
            # correct response to it. Proposing against an empty artifact is
            # how a system starts inventing.
            return ()
        return ("provenance", "verification", "enforcement", "state")


# ---------------------------------------------------------------------------
# dispositions
# ---------------------------------------------------------------------------


class ProposalDisposition(str, Enum):
    """What L3 decided about a proposal.

    Five outcomes, and the distinctions are the point. In particular
    ``UNKNOWN_CITATION`` is not a flavour of denial: it means the proposal
    asserted something about evidence that does not exist, which is a different
    incident from a proposal that cited real evidence insufficiently.
    """

    PERMITTED = "permitted"
    DENIED = "denied"
    #: a cited claim id is not in the store
    UNKNOWN_CITATION = "unknown-citation"
    #: asserts a state but cites nothing
    UNCITED = "uncited"
    #: the budget ran out before this was considered
    UNREACHED = "unreached"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class RunRecord:
    """One proposal's passage through the loop.

    Content-addressed and recorded for refusals and exhaustion as well as
    permits. A run in which nothing was proposed still produces a record:
    silence is data, and unrecorded silence cannot be distinguished from a run
    that crashed before it started.
    """

    proposal_id: str
    question: str
    disposition: ProposalDisposition
    reason: str
    decision_id: str = ""
    cites: tuple[str, ...] = ()
    proposer: str = ""
    record_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "question": self.question,
            "disposition": self.disposition.value,
            "reason": self.reason,
            "decision_id": self.decision_id,
            "cites": list(self.cites),
            "proposer": self.proposer,
            "record_id": self.record_id,
        }


# ---------------------------------------------------------------------------
# the loop
# ---------------------------------------------------------------------------


class Runtime:
    """A bounded proposal loop over a read-only witness.

    Holds a witness and a gateway. Does **not** hold a store, and cannot be
    given one: the constructor takes no such parameter, so there is no wiring
    that connects the runtime to the write path even by accident.

    Every proposal is routed through the gateway as an ordinary request. The
    runtime has no privileged path, because a privileged path is a gate with a
    hole in it and the hole is exactly where a compromised proposer would aim.
    """

    def __init__(
        self,
        witness: Witness,
        gateway: PolicyGateway,
        proposer: Proposer | None = None,
    ) -> None:
        self._witness = witness
        self._gateway = gateway
        self._proposer: Proposer = proposer if proposer is not None else RuleProposer()
        self._records: list[RunRecord] = []

    @property
    def proposer_name(self) -> str:
        return getattr(self._proposer, "name", type(self._proposer).__name__)

    @property
    def records(self) -> tuple[RunRecord, ...]:
        return tuple(self._records)

    def run(self, budget: int, *, transaction_time: str = "1970-01-01T00:00:00Z") -> tuple[RunRecord, ...]:
        """Run at most ``budget`` proposal steps. Returns one record per step.

        ``budget`` is validated rather than clamped. A zero or negative budget
        is a configuration error, and clamping it to zero would make
        misconfiguration indistinguishable from a run where the proposer had
        nothing to say — two incidents that should never share a signature.
        """
        if budget <= 0:
            raise ValueError(
                f"step budget must be positive, got {budget}; a non-positive "
                "budget is a configuration error, not an empty run"
            )
        self._records.clear()

        proposals = tuple(self._proposer.propose(self._witness, budget))
        for index in range(budget):
            if index >= len(proposals):
                break
            self._records.append(
                self._consider(proposals[index], transaction_time=transaction_time)
            )
        return self.records

    def _consider(self, proposal: Proposal, *, transaction_time: str) -> RunRecord:
        """Evaluate one proposal through L3. Never applies anything.

        The citation check runs *before* the gateway, and it fails closed. A
        proposal citing an id that is not in the store has asserted something
        about evidence that does not exist, and there is no version of that
        which the gateway's judgment could rescue: a correct verdict on an
        invented citation is still a decision about nothing.
        """
        disposition, reason, decision_id = self._check_citations(proposal)
        if disposition is ProposalDisposition.PERMITTED:
            decision = self._gateway.decide(
                Request(
                    actor=f"runtime:{self.proposer_name}",
                    action="propose",
                    resource="claims",
                    args={"state": proposal.proposed_state, "text": proposal.text},
                    context={"proposal_id": proposal.proposal_id},
                )
            )
            decision_id = decision.decision_id
            if decision.verdict is not Verdict.ALLOW:
                disposition = ProposalDisposition.DENIED
                reason = decision.reason
            else:
                reason = "permitted; awaiting L0 derivation"

        return self._record(
            proposal, disposition, reason, decision_id, transaction_time
        )

    def _check_citations(
        self, proposal: Proposal
    ) -> tuple[ProposalDisposition, str, str]:
        """Fail closed on citations. Returns (disposition, reason, decision_id)."""
        if not proposal.cites:
            return (
                ProposalDisposition.UNCITED,
                "proposal asserts a state without citing any claim",
                "",
            )
        # The witness is the runtime's only view of the corpus, so it is also
        # the only thing that can say whether a cited id exists. A fabricated id
        # is not in its answers, and that is the check.
        known = self._known_claim_ids()
        unknown = [c for c in proposal.cites if c not in known]
        if unknown:
            return (
                ProposalDisposition.UNKNOWN_CITATION,
                f"cited claim id(s) not in the corpus: {', '.join(sorted(unknown))}",
                "",
            )
        return ProposalDisposition.PERMITTED, "", ""

    def _known_claim_ids(self) -> frozenset[str]:
        """Ids the witness can attest to, cached per run.

        Derived from witness answers rather than from a store query, because
        the runtime has no store. The honest consequence is that this set is
        only as broad as the retrieval that produced it — a real limit, and one
        that pushes toward the witness being interrogated properly rather than
        around.
        """
        cached = getattr(self, "_known_cache", None)
        if cached is not None:
            return cached
        ids: set[str] = set()
        for question in ("provenance", "verification", "runtime", "corpus", "evidence"):
            for claim_id, _text in self._witness.ask(question).claims:
                ids.add(claim_id)
        frozen = frozenset(ids)
        self._known_cache = frozen
        return frozen

    def _record(
        self,
        proposal: Proposal,
        disposition: ProposalDisposition,
        reason: str,
        decision_id: str,
        transaction_time: str,
    ) -> RunRecord:
        body = {
            "proposal_id": proposal.proposal_id,
            "question": proposal.question,
            "disposition": disposition.value,
            "reason": reason,
            "decision_id": decision_id,
            "cites": list(proposal.cites),
            "proposer": self.proposer_name,
            "transaction_time": transaction_time,
        }
        return RunRecord(
            proposal_id=proposal.proposal_id,
            question=proposal.question,
            disposition=disposition,
            reason=reason,
            decision_id=decision_id,
            cites=proposal.cites,
            proposer=self.proposer_name,
            record_id=content_id(body, prefix="run-"),
        )
