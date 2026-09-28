"""The evaluator (ADR-006).

Until this module, nothing in the system could move a claim out of
``UNEXAMINED``. The compiler refuses to (ADR-004) and the witness refuses to
(ADR-005), both correctly. The consequence was a 13-state spine with one
reachable value — a vocabulary, not a state machine.

This evaluator is what lets a state change happen, and it is deliberately the
weakest component in the system rather than the strongest. It returns a
**relation**, never a score. Its default answer is ``INCONCLUSIVE``, because
``INCONCLUSIVE`` is the true name for the relationship between lexical overlap
and entailment.

**It is sound but incomplete.** Every verdict it returns is correct; it will
miss contradictions a careful reader would notice. That asymmetry is the whole
design. A missed contradiction is a gap an operator closes by hand. A
manufactured one destroys a true claim, and ``CONTRADICTED`` is effectively
absorbing — nothing in the spine brings it back without explicit human action.
So every ambiguous case in here resolves toward ``INCONCLUSIVE``.

The rule that keeps this from being theater: **a claim may never be its own
evidence** (ADR-006 §2.2). The compiler emits claims that are verbatim source
segments, so a candidate always contains the same tokens as its own evidence
span. Test containment naively and every claim in every corpus becomes
``SUPPORTED``, forever, with perfect recall and zero information — the v0.1
fabrication with a verdict attached.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Sequence

from ..compile.manifest import Manifest
from ..knowledge.epistemic import EpistemicState
from ..knowledge.normalize import NEGATION_UNKNOWN, Normalized, normalize
from ..knowledge.store import Store
from ..witness.retrieval import BM25, ScoredDoc

__all__ = [
    "Evaluator",
    "Verdict",
    "Relation",
    "SelfAttestation",
    "EVALUATOR_METHOD",
]

#: Recorded on every evaluation. Not a model name and not a version range —
#: the method is a fixed, total function, so the id is a constant and a change
#: to it is a change to the meaning of every verdict ever written.
EVALUATOR_METHOD = "deterministic-normalized/1"


class Relation(str, Enum):
    """What the evaluator decided. Not a score — a relation."""

    #: another claim in the corpus literally states this proposition
    ATTESTED = "attested"
    #: a claim with the same terms asserts the opposite
    CONTRADICTED = "contradicted"
    #: material was found; it does not establish anything
    INCONCLUSIVE = "inconclusive"
    #: nothing was found; an investigation record was written
    UNRESOLVED = "unresolved"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class SelfAttestation(Exception):
    """An evaluation would have counted the claim as its own evidence.

    Fail-closed. This is the guard from ADR-006 §2.2 expressed as an exception
    rather than a comment, because a comment does not fail a test and this is
    the one invariant whose violation produces a silently perfect result.
    """


@dataclass(frozen=True)
class Verdict:
    """A decision about one claim, with the record that justifies it."""

    subject_id: str
    subject_text: str
    relation: Relation
    state: EpistemicState
    evaluation_id: str
    method: str = EVALUATOR_METHOD
    #: claim ids that bore the subject out. Non-empty iff relation is ATTESTED.
    attested_by: tuple[str, ...] = ()
    #: claim ids found to assert the opposite. Non-empty iff CONTRADICTED.
    contradicted_by: tuple[str, ...] = ()
    investigation_id: str | None = None

    def __post_init__(self) -> None:
        # A SUPPORTED verdict with nothing attesting it is not a weak verdict,
        # it is a meaningless one. Refuse to construct it.
        if self.state is EpistemicState.SUPPORTED and not self.attested_by:
            raise SelfAttestation(
                f"claim {self.subject_id} was marked SUPPORTED with no attesting "
                "claim; a claim cannot be its own evidence, and a state with no "
                "attestation behind it is not a verdict"
            )
        if self.relation is Relation.ATTESTED and not self.attested_by:
            raise SelfAttestation(
                f"claim {self.subject_id} got an ATTESTED verdict naming nothing"
            )
        if self.relation is Relation.CONTRADICTED and not self.contradicted_by:
            raise SelfAttestation(
                f"claim {self.subject_id} got a CONTRADICTED verdict naming no "
                "counterpart; a contradiction with nothing on the other side is "
                "an assertion, not a finding"
            )
        if self.relation is Relation.UNRESOLVED and self.investigation_id is None:
            raise SelfAttestation(
                f"claim {self.subject_id} was found unresolved with no "
                "investigation record; absence must be provable (ADR-002)"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "subject_id": self.subject_id,
            "subject_text": self.subject_text,
            "relation": self.relation.value,
            "state": self.state.value,
            "evaluation_id": self.evaluation_id,
            "method": self.method,
            "attested_by": list(self.attested_by),
            "contradicted_by": list(self.contradicted_by),
            "investigation_id": self.investigation_id,
        }


class Evaluator:
    """Assigns epistemic states, one claim at a time, with a record each time.

    Constructed over a store and a manifest, so it is bound to one artifact
    version by the same mechanism the witness uses. It reads every claim in the
    version, never one outside it.
    """

    def __init__(self, store: Store, manifest: Manifest) -> None:
        self._store = store
        self._manifest = manifest
        self._texts: dict[str, str] = {}
        self._norms: dict[str, Normalized] = {}
        for cid in manifest.claim_ids:
            row = store.get_claim(cid)
            if row is not None:
                self._texts[cid] = row["text"]
                self._norms[cid] = normalize(row["text"])
        self._index = BM25(self._texts)

    @property
    def version(self) -> str:
        return self._manifest.version

    def _peers(self, subject_id: str) -> Iterable[tuple[str, Normalized]]:
        """Every other claim in this version, with its normalized form.

        The subject is excluded here, once, and that single line is what makes
        self-attestation structurally impossible rather than merely discouraged.
        """
        for cid, norm in self._norms.items():
            if cid == subject_id:
                continue
            yield cid, norm

    def _attestations(self, subject_id: str, subject: Normalized) -> list[str]:
        """Claims whose text contains the subject's proposition, contiguously."""
        return [
            cid
            for cid, norm in self._peers(subject_id)
            if subject.is_contiguous_in(norm)
        ]

    def _contradictions(self, subject_id: str, subject: Normalized) -> list[str]:
        """Peers asserting the same terms with flipped polarity.

        Requires a known polarity on *both* sides. An unknown polarity is not
        treated as "not negated" — that would let a claim with two stacked
        negators appear to contradict a plain one, which is the mirror image of
        the defect this module exists to prevent.
        """
        if not subject.polarity_known:
            return []
        out: list[str] = []
        for cid, norm in self._peers(subject_id):
            if not norm.polarity_known:
                continue
            if norm.terms == subject.terms and norm.polarity != subject.polarity:
                out.append(cid)
        return sorted(out)

    def evaluate(
        self,
        claim_id: str,
        *,
        apply: bool = True,
        transaction_time: str = "1970-01-01T00:00:00Z",
    ) -> Verdict:
        """Evaluate one claim, optionally writing the state transition.

        ``apply=False`` is a dry run: the verdict is computed and its record is
        written, but the claim's state is not moved. Recording the verdict
        either way is deliberate — a dry run that leaves no trace cannot be
        audited, and an audit that can be bypassed is not an audit.
        """
        if claim_id not in self._norms:
            raise KeyError(
                f"{claim_id} is not in artifact version {self.version}; the "
                "evaluator does not judge claims outside its version"
            )

        subject = self._norms[claim_id]
        text = self._texts[claim_id]

        contradictions = self._contradictions(claim_id, subject)
        if contradictions:
            return self._record(
                claim_id,
                text,
                Relation.CONTRADICTED,
                EpistemicState.CONTRADICTED,
                contradicted_by=contradictions,
                apply=apply,
                transaction_time=transaction_time,
            )

        attestations = self._attestations(claim_id, subject)
        if attestations:
            return self._record(
                claim_id,
                text,
                Relation.ATTESTED,
                EpistemicState.SUPPORTED,
                attested_by=attestations,
                apply=apply,
                transaction_time=transaction_time,
            )

        # Something is in the neighborhood but it establishes nothing. This is
        # the common case and it is a real answer, not a failure.
        hits: list[ScoredDoc] = self._index.search(text, limit=len(self._index) or 1)
        neighbours = [h.doc_id for h in hits if h.doc_id != claim_id]
        return self._record(
            claim_id,
            text,
            Relation.INCONCLUSIVE,
            EpistemicState.INCONCLUSIVE,
            investigated=f"neighbours={sorted(neighbours)}" if neighbours else "no-neighbours",
            apply=apply,
            transaction_time=transaction_time,
        )

    def _record(
        self,
        claim_id: str,
        text: str,
        relation: Relation,
        state: EpistemicState,
        *,
        attested_by: Sequence[str] = (),
        contradicted_by: Sequence[str] = (),
        investigated: str | None = None,
        apply: bool,
        transaction_time: str,
    ) -> Verdict:
        """Write the evaluation record, then move the state if asked.

        Order matters: the record is written *first*. If the transition then
        fails validation, an evaluation exists showing the attempt, which is
        what you want. The reverse order could leave a state change with no
        explanation of what decided it.
        """
        investigation_id = None
        if relation is Relation.UNRESOLVED:
            investigation_id = self._store.add_investigation(
                question=f"evaluate: {text}",
                searched=[
                    f"method={EVALUATOR_METHOD}",
                    f"version={self.version}",
                    f"root={self._manifest.root}",
                    f"claims_searched={len(self._index)}",
                ],
                created_at=transaction_time,
            )

        evaluation_id = self._store.add_evaluation(
            subject_id=claim_id,
            relation=relation.value,
            method=EVALUATOR_METHOD,
            attested_by=attested_by,
            decided_at=transaction_time,
            meta={"contradicted_by": list(contradicted_by), "investigation": investigated},
        )

        if apply:
            self._store.set_state(claim_id, state, transaction_time=transaction_time)

        return Verdict(
            subject_id=claim_id,
            subject_text=text,
            relation=relation,
            state=state,
            evaluation_id=evaluation_id,
            attested_by=tuple(attested_by),
            contradicted_by=tuple(contradicted_by),
            investigation_id=investigation_id,
        )

    def evaluate_all(
        self, *, apply: bool = True, transaction_time: str = "1970-01-01T00:00:00Z"
    ) -> list[Verdict]:
        """Evaluate every claim in the version, in deterministic id order."""
        return [
            self.evaluate(cid, apply=apply, transaction_time=transaction_time)
            for cid in sorted(self._norms)
        ]

    def contradictions(self) -> list[Verdict]:
        """Every contradicted claim, without applying anything."""
        return [v for v in self.evaluate_all(apply=False) if v.relation is Relation.CONTRADICTED]
