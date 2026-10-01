"""The write path.

A model proposes. The gateway says whether the proposal was admissible. This
module is the third thing, and it is the one that can move a belief.

The design question is whether the model's opinion of the answer is an input
to the write. It is not. The state written is the one an independent
derivation produces; the model's ``proposed_state`` is read only to detect
disagreement, because a system that silently overwrites a model with the
truth has not governed the model, it has ignored it, and the disagreement is
the most informative thing in the run.

Three authorities, separable, none sufficient alone:

  1. ``RunRecord.disposition is PERMITTED`` — the runtime's citation check
     passed and its gateway allowed the proposal. Says nothing about truth.
  2. ``derive`` capability on the applier's own gateway — a *different*
     grant from ``propose``. Model permission and write permission are not the
     same permission, so a policy can allow models to speak and forbid them
     from moving beliefs. If they were one capability, a policy that wanted
     models to propose would have no way to stop them writing.
  3. ``Derivation`` — the evaluator, re-run against the artifact, with no
     knowledge of the proposal at all.

The state written is (3). (1) and (2) can only ever prevent a write, never
cause one, which is what makes "permitted" safe to record.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Sequence

from ganymede4.knowledge.evaluator import Evaluator, Verdict
from ganymede4.knowledge.store import Store
from ganymede4.policy.gateway import PolicyGateway, Request, Verdict as PolicyVerdict
from ganymede4.runtime.loop import ProposalDisposition, RunRecord

#: Recorded on every evaluation this module writes, so a reader can tell an
#: applier write from a compile-time or evaluator write.
APPLIER_METHOD = "applier/independent-derivation"

#: The only authority claim this module makes about a written state.
AUTHORITY = "independent-derivation"

_TT = "1970-01-01T00:00:00Z"


class Disposition(str, Enum):
    """What happened to one record. Every value is terminal — none of them
    leaves the artifact in a state that a later reader must interpret."""

    APPLIED = "applied"
    DERIVATION_DISAGREES = "derivation-disagrees"
    NOT_PERMITTED = "not-permitted"
    TRANSITION_REFUSED = "transition-refused"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class Derivation:
    """An independent judgement about one claim, with the verdict that
    produced it. Content of the artifact, not of any proposal."""

    claim_id: str
    state: str
    relation: str
    evaluation_id: str
    verdict: Verdict | None = None

    @staticmethod
    def of(store: Store, claim_id: str) -> "Derivation":
        """Re-derive ``claim_id`` from the artifact, ignoring any proposal.

        Uses ``apply=False``: the verdict is computed and recorded, and the
        state is *not* moved. The caller decides whether to move it, and only
        after the checks below. Recording the verdict either way is deliberate
        — a derivation that left no trace could not be audited, and an audit
        that can be bypassed is not an audit.
        """
        from ganymede4.compile.reopen import rebuild_manifest

        manifest = rebuild_manifest(store)
        evaluator = Evaluator(store, manifest)
        verdict = evaluator.evaluate(claim_id, apply=False, transaction_time=_TT)
        return Derivation(
            claim_id=claim_id,
            state=verdict.state.value,
            relation=verdict.relation.value,
            evaluation_id=verdict.evaluation_id,
            verdict=verdict,
        )


@dataclass(frozen=True)
class ApplyOutcome:
    """One record's passage through the write path."""

    proposal_id: str
    claim_id: str
    disposition: Disposition
    derived_state: str
    derived: Derivation | None
    reason: str
    model_proposed_state: str
    evaluation_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "claim_id": self.claim_id,
            "disposition": self.disposition.value,
            "derived_state": self.derived_state,
            "model_proposed_state": self.model_proposed_state,
            "reason": self.reason,
            "evaluation_id": self.evaluation_id,
        }


class Applier:
    """Turns permitted proposals into state transitions, or into refusals.

    Holds a ``Store`` and a ``PolicyGateway``. Deliberately does **not**
    accept a ``Runtime`` — see ``bind``.
    """

    def __init__(self, store: Store, gateway: PolicyGateway) -> None:
        self._store = store
        self._gateway = gateway

    # -- the seam is not a convention ------------------------------------

    def bind(self, **kwargs: Any) -> None:
        """Refuse to wire anything but the two authorities already held.

        Exists so that an attempt to reach the read-only loop from the write
        path is a loud ``TypeError`` at the call site rather than a working
        arrangement discovered later.
        """
        unexpected = set(kwargs) - {"store", "gateway"}
        if unexpected:
            raise TypeError(
                f"Applier accepts only store and gateway; refused: "
                f"{sorted(unexpected)}. In particular the write path will not be "
                f"handed a Runtime."
            )
        if not kwargs:
            return None
        raise TypeError("Applier is constructed, not bound; pass to __init__")

    # -- application ------------------------------------------------------

    def apply(
        self, records: Sequence[RunRecord], *, transaction_time: str = _TT
    ) -> tuple[ApplyOutcome, ...]:
        """Apply each record independently. One refusal does not stop the rest.

        Every branch is a refusal except the one that requires derivation
        agreement, so a run of a hundred proposals can write at most the
        hundred that the evidence independently supports.
        """
        return tuple(self._apply_one(r, transaction_time=transaction_time) for r in records)

    def _apply_one(
        self, record: RunRecord, *, transaction_time: str
    ) -> ApplyOutcome:
        claim_id = record.cites[0] if record.cites else ""
        model_state = self._model_state(record)

        # (1) The runtime's own verdict. A refused proposal is terminal: not
        # re-derived, because a second citation check here would be weaker
        # than the one that already refused it and would be operating on a
        # record this module does not own.
        if record.disposition is not ProposalDisposition.PERMITTED:
            return self._refuse(
                record, claim_id, model_state, Disposition.NOT_PERMITTED,
                f"proposal was {record.disposition.value}; not derived",
            )
        if not record.decision_id:
            return self._refuse(
                record, claim_id, model_state, Disposition.NOT_PERMITTED,
                "permitted with no decision id; nothing in the audit can name "
                "who allowed this",
            )
        if not record.cites:
            return self._refuse(
                record, claim_id, model_state, Disposition.NOT_PERMITTED,
                "permitted with no citation; a state change about nothing",
            )

        # Derive before asking permission, so the permission is about a
        # specific derived state rather than an open-ended one. A grant that
        # cannot be evaluated against something concrete is a grant to whatever
        # arrives next.
        derived = Derivation.of(self._store, claim_id)

        # (2) The write grant. Separate capability from `propose`, so policy
        # can allow a model to speak and forbid it from moving beliefs.
        if not self._gateway_permits(record, derived):
            return ApplyOutcome(
                proposal_id=record.proposal_id,
                claim_id=claim_id,
                disposition=Disposition.NOT_PERMITTED,
                derived_state=derived.state,
                derived=derived,
                reason=(
                    f"no capability grants 'derive' on claims for state "
                    f"{derived.state!r}"
                ),
                model_proposed_state=model_state,
            )

        # (3) Agreement. The model's opinion is not an input to the write. It
        # is a claim to be compared against the derivation, and a mismatch is
        # reported rather than quietly resolved in either direction.
        if derived.state != model_state:
            return ApplyOutcome(
                proposal_id=record.proposal_id,
                claim_id=claim_id,
                disposition=Disposition.DERIVATION_DISAGREES,
                derived_state=derived.state,
                derived=derived,
                reason=(
                    f"model proposed {model_state!r}; independent derivation "
                    f"produced {derived.state!r}. The model does not overrule "
                    f"evidence, and evidence does not overrule the model "
                    f"silently."
                ),
                model_proposed_state=model_state,
            )

        return self._write(record, claim_id, model_state, derived, transaction_time)

    # -- internals --------------------------------------------------------

    def _write(
        self,
        record: RunRecord,
        claim_id: str,
        model_state: str,
        derived: Derivation,
        transaction_time: str,
    ) -> ApplyOutcome:
        """Write the derived state, with a record naming the whole chain.

        The evaluation is written *before* the transition, so a refused
        transition still leaves evidence that the attempt happened. The
        reverse order can leave a state change with nothing behind it.
        """
        evaluation_id = self._store.add_evaluation(
            subject_id=claim_id,
            relation=derived.relation,
            method=APPLIER_METHOD,
            attested_by=tuple(derived.verdict.attested_by) if derived.verdict else (),
            contradicted_by=(
                tuple(derived.verdict.contradicted_by) if derived.verdict else ()
            ),
            decided_at=transaction_time,
            meta={
                # The whole chain, so an auditor reading this row alone can
                # tell a model proposed it and the evidence decided it.
                "authority": AUTHORITY,
                "proposer": record.proposer,
                "proposal_id": record.proposal_id,
                "decision_id": record.decision_id,
                "model_proposed_state": model_state,
                "derived_state": derived.state,
                "derivation_evaluation_id": derived.evaluation_id,
            },
        )
        try:
            self._store.set_state(
                claim_id,
                derived.state,
                transaction_time=transaction_time,
                investigation_id=(
                    derived.verdict.investigation_id if derived.verdict else None
                ),
                justified_by=evaluation_id,
            )
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            return ApplyOutcome(
                proposal_id=record.proposal_id,
                claim_id=claim_id,
                disposition=Disposition.TRANSITION_REFUSED,
                derived_state=derived.state,
                derived=derived,
                reason=f"the store refused the transition: {exc}",
                model_proposed_state=model_state,
                evaluation_id=evaluation_id,
            )
        return ApplyOutcome(
            proposal_id=record.proposal_id,
            claim_id=claim_id,
            disposition=Disposition.APPLIED,
            derived_state=derived.state,
            derived=derived,
            reason=(
                f"derivation agreed with the model; wrote {derived.state!r} "
                f"on the evaluator's authority, not the model's"
            ),
            model_proposed_state=model_state,
            evaluation_id=evaluation_id,
        )

    def _gateway_permits(self, record: RunRecord, derived: Derivation) -> bool:
        """Ask the applier's own gateway. Records the answer either way."""
        decision = self._gateway.decide(
            Request(
                actor=f"applier:{record.proposer or 'unknown'}",
                action="derive",
                resource="claims",
                args={"state": derived.state, "claim_id": derived.claim_id},
                context={
                    "decision_id": record.decision_id,
                    "proposal_id": record.proposal_id,
                },
            )
        )
        return decision.verdict is PolicyVerdict.ALLOW

    def _model_state(self, record: RunRecord) -> str:
        """The model's opinion, for comparison only.

        Carried on the ``RunRecord`` by the runtime, which is the component
        that talked to the proposer. A record arriving without it compares as a
        disagreement, so a caller that forgets to carry it gets a refusal
        rather than a silent agreement.
        """
        return record.model_proposed_state

    def _refuse(
        self,
        record: RunRecord,
        claim_id: str,
        model_state: str,
        disposition: Disposition,
        reason: str,
    ) -> ApplyOutcome:
        return ApplyOutcome(
            proposal_id=record.proposal_id,
            claim_id=claim_id,
            disposition=disposition,
            derived_state="",
            derived=None,
            reason=reason,
            model_proposed_state=model_state,
        )


def records_to_dicts(records: Sequence[RunRecord]) -> list[dict[str, Any]]:
    """Small helper for callers writing a JSON report."""
    return [r.to_dict() for r in records]
