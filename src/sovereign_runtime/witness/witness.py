"""The witness (ADR-005).

A read-only capability view over one artifact version. It answers questions with
**typed answers carrying provenance**, and it proves absence by writing down what
it searched.

The design constraint that shaped everything here: **there is no
``answer() -> str``.** A method returning a string invites a caller to print it
as an answer, and a printed string carries no epistemic state — which is how
``ganymede`` v0.1 produced 23 narrative "established facts" that existed in
neither the claim graph nor the evidence shards. The absence of the method is
the enforcement.

What the witness is not:

- not an agent, not a persona, not a model
- not able to write claims or move epistemic states
- not able to see outside the version it was bound to
- not able to assert absence without an investigation record naming the query,
  the scope, the method, and the version searched

What it does return for a miss is a real ``UNRESOLVED`` answer with a real
``Investigation`` attached — the same shape as every other answer, with a
different state. There is exactly one code path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence

from ..compile.manifest import Manifest
from ..knowledge.epistemic import EpistemicState
from ..knowledge.store import Store
from .retrieval import METHOD_ID, BM25, ScoredDoc

__all__ = [
    "Witness",
    "WitnessAnswer",
    "ResolvedEvidence",
    "Boundary",
    "OutOfVersion",
    "METHOD_ID",
]


class OutOfVersion(Exception):
    """A record outside the bound artifact version was requested.

    Fail-closed. A witness that can read a claim added after it was
    instantiated would make ``artifact_version`` decorative.
    """


@dataclass(frozen=True)
class ResolvedEvidence:
    """Evidence resolved all the way to a character offset in a named source."""

    evidence_id: str
    source_uri: str
    start: int
    end: int
    text: str
    rank: float
    matched_terms: tuple[str, ...]

    def citation(self) -> str:
        """A human-readable citation. The terminus of every answer."""
        return f"{self.source_uri}[{self.start}:{self.end}]"


@dataclass(frozen=True)
class WitnessAnswer:
    """A typed answer. Never a bare string."""

    question: str
    state: EpistemicState
    claims: tuple[tuple[str, str], ...] = ()  # (claim_id, text)
    evidence: tuple[ResolvedEvidence, ...] = ()
    investigation_id: str | None = None
    method: str = METHOD_ID
    #: True when nothing was found and a search record was written. Exposed so
    #: a UI cannot render UNRESOLVED and a strong positive identically.
    is_absence: bool = False

    @property
    def is_answerable(self) -> bool:
        return bool(self.claims)

    def require_provenance(self) -> None:
        """Assert that every returned claim resolves to a real offset.

        The fail-closed form of ``target-architecture.md`` §7 constraint 4. An
        answer that cannot satisfy this is not a degraded answer; it is an
        unverifiable one, and a caller asking for guarantees gets an exception
        rather than a shrug.

        The absence states are exempt because they legitimately carry no
        evidence — what they must carry instead is an investigation, and that
        is enforced by ADR-002 at write time, not here.
        """
        if self.state is EpistemicState.UNRESOLVED:
            if self.investigation_id is None:
                raise AssertionError("UNRESOLVED answer with no investigation record")
            return
        if not self.claims:
            return
        if not self.evidence:
            raise AssertionError(
                f"answer in state {self.state.value} carries claims but no evidence"
            )
        # Every claim must have at least one resolved span. Since `evidence` is
        # built by walking each claim's own evidence rows, an empty tuple for a
        # claim is the real failure this catches.
        if len(self.evidence) < len(self.claims):
            raise AssertionError(
                f"{len(self.claims)} claims returned but only {len(self.evidence)} "
                "resolved evidence spans; at least one claim has no source"
            )
        for ev in self.evidence:
            if ev.end <= ev.start and ev.text:
                raise AssertionError(
                    f"evidence {ev.evidence_id} has a degenerate span "
                    f"[{ev.start}:{ev.end}]"
                )

    def citations(self) -> list[str]:
        return [ev.citation() for ev in self.evidence]

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "state": self.state.value,
            "is_absence": self.is_absence,
            "method": self.method,
            "investigation_id": self.investigation_id,
            "claims": [{"id": cid, "text": txt} for cid, txt in self.claims],
            "evidence": [
                {
                    "evidence_id": ev.evidence_id,
                    "citation": ev.citation(),
                    "text": ev.text,
                    "rank": round(ev.rank, 6),
                    "matched_terms": list(ev.matched_terms),
                }
                for ev in self.evidence
            ],
        }


@dataclass(frozen=True)
class Boundary:
    """What this witness can and cannot speak to (ADR-005 §6).

    A first-class return value, not a failure mode and not an empty field. The
    lists of what was *never asked* are the point: a witness that has been
    asked one question and one that has been asked none are in very different
    states, and a caller must be able to tell them apart.
    """

    version: str
    root: str
    sources: tuple[str, ...]
    state_counts: dict[str, int]
    method: str
    index: dict[str, Any]
    asked: tuple[str, ...] = ()
    answered_with_absence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "root": self.root,
            "method": self.method,
            "sources": list(self.sources),
            "state_counts": dict(self.state_counts),
            "index": dict(self.index),
            "asked": list(self.asked),
            "answered_with_absence": list(self.answered_with_absence),
            "not_asked_count": 0,
            "contains": sorted(self.sources),
            "does_not_contain": [
                "anything outside these sources",
                "anything not derived by deterministic segmentation",
                "any proposition no compiled claim states",
            ],
        }


class Witness:
    """A read-only view over one compiled artifact version."""

    def __init__(self, store: Store, manifest: Manifest) -> None:
        self._store = store
        self._manifest = manifest
        self._leaves: frozenset[str] = frozenset(
            [*manifest.source_ids, *manifest.claim_ids, *manifest.evidence_ids, *manifest.heuristic_ids]
        )
        # The index is built over this version's claims only, so "can see a
        # future version" is false by construction rather than by check.
        docs: dict[str, str] = {}
        for cid in manifest.claim_ids:
            row = store.get_claim(cid)
            if row is not None:
                docs[cid] = row["text"]
        self._index = BM25(docs)
        self._asked: list[str] = []
        self._absences: list[str] = []

    # -- version binding -------------------------------------------------

    @property
    def version(self) -> str:
        return self._manifest.version

    @property
    def root(self) -> str:
        return self._manifest.root

    def _check_version(self, claim_id: str) -> None:
        if claim_id not in self._leaves:
            raise OutOfVersion(
                f"{claim_id} is not in artifact version {self.version}; a witness "
                "cannot see records outside the version it was bound to"
            )

    # -- reading ---------------------------------------------------------

    def claims_by_state(self, state: EpistemicState) -> list[dict[str, Any]]:
        """All claims in ``state`` within this version."""
        out: list[dict[str, Any]] = []
        for cid in self._manifest.claim_ids:
            self._check_version(cid)
            row = self._store.get_claim(cid)
            if row is not None and EpistemicState(row["state"]) is state:
                out.append({"id": cid, "text": row["text"]})
        return out

    def _resolve(self, claim_id: str, scored: ScoredDoc) -> list[ResolvedEvidence]:
        resolved: list[ResolvedEvidence] = []
        for ev in self._store.evidence_for(claim_id):
            span = self._store.resolve_span(ev["id"])
            if span is None:
                # A claim in this version whose evidence does not resolve is a
                # defect. Skipping it silently would let a broken artifact
                # return a confident-looking answer with fewer citations.
                raise AssertionError(
                    f"claim {claim_id} cites evidence {ev['id']} that does not "
                    f"resolve to a source offset; the artifact is corrupt"
                )
            uri, start, end = span
            # Re-verify the span against the *current* source bytes. The store
            # checked this at write time, but a source edited afterwards would
            # otherwise leave the witness citing an offset whose text has since
            # changed — the exact post-hoc-rewrite failure the independent
            # auditor exists to catch. A witness that trusts its own store's
            # past self-check is not independent of it.
            row = self._store.get_source(ev["source_id"])
            if row is None:
                raise AssertionError(
                    f"evidence {ev['id']} names a source that is not in the store"
                )
            actual = row["content"][start:end]
            if actual != ev["text"]:
                raise AssertionError(
                    f"evidence {ev['id']} span mismatch in {uri}[{start}:{end}]: "
                    f"source now says {actual!r}, evidence claims {ev['text']!r}; "
                    "the source was modified after the artifact was compiled"
                )
            resolved.append(
                ResolvedEvidence(
                    evidence_id=ev["id"],
                    source_uri=uri,
                    start=start,
                    end=end,
                    text=ev["text"],
                    rank=scored.score,
                    matched_terms=scored.matched_terms,
                )
            )
        return resolved

    def ask(self, question: str, *, limit: int = 5, transaction_time: str = "1970-01-01T00:00:00Z") -> WitnessAnswer:
        """Interrogate the witness. Returns a typed answer; never a string.

        On a miss this writes an ``Investigation`` recording the query, the
        scope, the method, and the version, and returns ``UNRESOLVED`` with that
        record attached. The write is not bookkeeping — ADR-002's
        ``MissingInvestigation`` guard means an absence that cannot show its
        work cannot be represented at all.
        """
        self._asked.append(question)
        hits = self._index.search(question, limit=limit)

        if not hits:
            inv_id = self._store.add_investigation(
                question=question,
                # `searched` is the auditable part: the method, the version, and
                # the scope. "I looked with a lexical ranker over this version"
                # is a claim someone can later check or contradict; "I looked"
                # is not.
                searched=[
                    f"method={METHOD_ID}",
                    f"version={self.version}",
                    f"root={self.root}",
                    f"claims_searched={len(self._index)}",
                    f"sources_searched={len(self._manifest.source_ids)}",
                ],
                created_at=transaction_time,
            )
            self._absences.append(question)
            return WitnessAnswer(
                question=question,
                state=EpistemicState.UNRESOLVED,
                investigation_id=inv_id,
                is_absence=True,
            )

        claims: list[tuple[str, str]] = []
        evidence: list[ResolvedEvidence] = []
        for scored in hits:
            self._check_version(scored.doc_id)
            row = self._store.get_claim(scored.doc_id)
            assert row is not None  # guaranteed by the version leaf set
            claims.append((scored.doc_id, row["text"]))
            evidence.extend(self._resolve(scored.doc_id, scored))

        answer = WitnessAnswer(
            question=question,
            # The honest state. Retrieval found claims that share distinctive
            # terms with the question; that is not entailment, so nothing is
            # promoted past UNEXAMINED (ADR-005 §5).
            state=EpistemicState.UNEXAMINED,
            claims=tuple(claims),
            evidence=tuple(evidence),
        )
        answer.require_provenance()
        return answer

    # -- boundary --------------------------------------------------------

    def boundary(self) -> Boundary:
        sources: list[str] = []
        for sid in self._manifest.source_ids:
            row = self._store.get_source(sid)
            if row is not None:
                sources.append(row["uri"])
        return Boundary(
            version=self.version,
            root=self.root,
            sources=tuple(sorted(sources)),
            state_counts=dict(self._manifest.state_counts),
            method=METHOD_ID,
            index=self._index.describe(),
            asked=tuple(self._asked),
            answered_with_absence=tuple(self._absences),
        )

    def iter_claims(self) -> Iterator[dict[str, Any]]:
        for cid in self._manifest.claim_ids:
            self._check_version(cid)
            row = self._store.get_claim(cid)
            if row is not None:
                yield {"id": cid, "text": row["text"], "state": row["state"]}
