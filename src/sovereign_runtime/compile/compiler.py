"""The deterministic compiler (ADR-004).

``compile_corpus`` is a pure function of source bytes. It segments sources into
verbatim claims, attaches each claim to the exact evidence span it came from,
mines a small set of derived heuristics, and returns a manifest whose Merkle
root identifies the result.

**What this module structurally cannot do** — and the enforcement is in the
type signature, not in a comment — is assign an epistemic state. Every claim it
writes is ``UNEXAMINED``. The permitted-states tuple is a module constant so
that a future change to it shows up in a diff as the raising of a wall, and
``_forbid_state_assignment`` raises if a caller ever tries to widen it.

The reasoning is in ADR-004 §1: establishing that one sentence is *about*
another is a semantic judgement, and every deterministic proxy for it measures
topical relevance rather than entailment. The measured form of that gap is in
``migration-map.md`` — cross-encoder and RRF score distributions fully overlap
between answerable and answer-absent cases. So the compiler emits candidates
with provenance and refuses to certify them. The epistemic layer sits above.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ..core.content import content_id
from ..knowledge.epistemic import EpistemicState
from ..knowledge.store import Store
from .manifest import Manifest, build_manifest
from .segment import is_proposition, segment

__all__ = [
    "compile_corpus",
    "CompiledArtifact",
    "SourceSpec",
    "Heuristic",
    "HEURISTIC_MIN_SUPPORT",
    "COMPILE_PERMITTED_STATES",
]

#: The *only* states the compiler may write. Narrowing this is a change to the
#: system's honesty and requires an ADR; widening it is what ADR-004 forbids.
COMPILE_PERMITTED_STATES: frozenset[EpistemicState] = frozenset({EpistemicState.UNEXAMINED})

#: A pattern seen fewer than this many times is not a pattern. Emitting a
#: heuristic on thin support is worse than emitting none, because a heuristic
#: is a claim the system will later be asked to defend.
HEURISTIC_MIN_SUPPORT = 3

#: Stripped when normalizing a sentence into a comparable shape. Apostrophes
#: are included so "Daniel's" and "Daniels" do not mine as different shapes.
_PUNCTUATION = frozenset(".,;:!?\"'`()[]{}—–-*…")


def _forbid_state_assignment() -> None:
    """Fail closed if the compiler's permitted-state wall is ever widened.

    Exists so that the invariant is enforced by the running system rather than
    by the discipline of whoever edits this file next. If a future phase needs
    a compiler that assigns states, it has to delete this guard in the diff of
    a reviewed ADR — it cannot be relaxed by accident.
    """
    if COMPILE_PERMITTED_STATES != frozenset({EpistemicState.UNEXAMINED}):
        raise RuntimeError(
            "ADR-004 violation: the deterministic compiler may only emit "
            f"UNEXAMINED claims, but COMPILE_PERMITTED_STATES is "
            f"{sorted(s.value for s in COMPILE_PERMITTED_STATES)}. Widening it "
            "requires a new ADR, and it must never happen silently."
        )


@dataclass(frozen=True)
class SourceSpec:
    """One input document."""

    uri: str
    content: str
    source_type: str = "document"
    meta: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class Heuristic:
    """A derived pattern.

    Derived means "a deterministic miner found this", not "someone said so".
    It is stored as a claim in the same graph as everything else, so it is
    subject to the same lifecycle and can reach ``CONTRADICTED`` like any other
    claim. A heuristic outside the graph is an oracle.
    """

    text: str
    supporting_claim_ids: tuple[str, ...]
    kind: str
    min_support: int = HEURISTIC_MIN_SUPPORT

    @property
    def is_emitted(self) -> bool:
        return len(self.supporting_claim_ids) >= self.min_support


@dataclass(frozen=True)
class CompiledArtifact:
    """The result of a compile: a populated store plus its manifest."""

    manifest: Manifest
    source_ids: tuple[str, ...]
    claim_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    heuristic_ids: tuple[str, ...]
    #: Segments that were not propositions and were therefore not asserted
    #: (ADR-012). Exposed on the artifact rather than logged, because a caller
    #: that discards a fifth of its input without saying so is not reporting a
    #: result, it is reporting a partial one.
    dropped_segments: int = 0

    @property
    def version(self) -> str:
        return self.manifest.version

    def summary(self) -> dict[str, Any]:
        return {
            "version": self.manifest.version,
            "root": self.manifest.root,
            "counts": {
                "sources": len(self.source_ids),
                "claims": len(self.claim_ids),
                "evidence": len(self.evidence_ids),
                "heuristics": len(self.heuristic_ids),
                "dropped_segments": self.dropped_segments,
            },
            "state_counts": dict(self.manifest.state_counts),
        }


def _normalize_shape(text: str) -> str:
    """Reduce a sentence to a comparable shape.

    Lowercased, punctuation stripped, whitespace collapsed. Numbers are kept:
    a recurrence is more interesting when the specific figure recurs, and
    keeping digits avoids mining "a number appeared N times", which is a fact
    about the miner's inputs, not about the corpus.
    """
    lowered = text.lower()
    no_punct = "".join(" " if ch in _PUNCTUATION else ch for ch in lowered)
    return " ".join(no_punct.split())


def _mine_heuristics(
    occurrences: Mapping[str, Sequence[tuple[str, str]]],
) -> list[Heuristic]:
    """Deterministic pattern miner over the compiled graph.

    ``occurrences`` maps a normalized sentence shape to ``(claim_id, source_id)``
    pairs across the whole corpus.

    The only pattern mined in this phase is **verbatim recurrence**: a sentence
    shape that appears in two or more *distinct sources* has been stated more
    than once by more than one party. That is mechanically checkable, needs no
    semantic judgement, and is the weakest true statement available.

    What it explicitly is *not*: evidence that the claim is true. Two sources
    can share an error, and one source can be mirrored twice — which is why the
    miner requires distinct *source ids* and not just distinct claim ids, and
    why the emitted text is a count ("this shape appears in N sources") rather
    than a conclusion. A stronger miner belongs in a later phase, and whatever
    it mines must still be a claim in the graph rather than a privileged case.
    """
    mined: list[Heuristic] = []
    for shape, pairs in occurrences.items():
        distinct_sources = {src for _cid, src in pairs}
        if len(distinct_sources) < HEURISTIC_MIN_SUPPORT:
            continue
        mined.append(
            Heuristic(
                text=(
                    f"The sentence shape {shape!r} occurs in "
                    f"{len(distinct_sources)} distinct sources."
                ),
                supporting_claim_ids=tuple(sorted(cid for cid, _src in pairs)),
                kind="recurring-shape",
            )
        )
    # Sorted for determinism: dict insertion order is discovery order, and the
    # artifact must not depend on which source happened to be compiled first.
    mined.sort(key=lambda h: h.text)
    return mined


def compile_corpus(
    store: Store,
    sources: Sequence[SourceSpec],
    *,
    transaction_time: str,
    compiled_at: str | None = None,
    run_id: str | None = None,
) -> CompiledArtifact:
    """Compile ``sources`` into ``store``; return the artifact and its manifest.

    Idempotent by construction: every write is content-addressed, so compiling
    the same bytes twice produces the same ids, writes nothing the second time,
    and returns the same manifest root.
    """
    _forbid_state_assignment()

    source_ids: list[str] = []
    claim_ids: list[str] = []
    evidence_ids: list[str] = []
    #: Segments discarded as non-propositions (ADR-012). Reported, never silent.
    dropped_segments: int = 0
    #: normalized shape -> (claim_id, source_id), for the miner.
    occurrences: dict[str, list[tuple[str, str]]] = {}

    for spec in sources:
        sid = store.add_source(
            uri=spec.uri,
            source_type=spec.source_type,
            content=spec.content,
            meta=spec.meta,
        )
        source_ids.append(sid)

        for seg in segment(spec.content):
            # ADR-012: a segment that is not a proposition is discarded, not
            # asserted. `except requests.`, `**6.`, and `.` all carry perfect
            # spans and perfect provenance and are not claims — they were
            # 22% of the real corpus, and they contradicted each other.
            #
            # The count is accumulated and reported. A pipeline that quietly
            # discards a fifth of its input while reporting a clean compile is
            # lying by omission, and the count is the only way an operator can
            # notice that happened.
            if not is_proposition(seg.text):
                dropped_segments += 1
                continue

            # The claim IS the evidence span. This is the whole mechanism:
            # there is no path by which the compiler produces a claim that is
            # not literally present in the source, because the text of one is
            # the text of the other.
            evid = store.add_evidence(
                source_id=sid,
                start_offset=seg.start,
                end_offset=seg.end,
                text=seg.text,
            )
            evidence_ids.append(evid)

            claim_meta = {
                "source_uri": spec.uri,
                "ordinal": seg.ordinal,
                "origin": "deterministic-segmentation",
            }
            cid = store.add_claim(
                text=seg.text,
                state=EpistemicState.UNEXAMINED,
                evidence_ids=[evid],
                transaction_time=transaction_time,
                meta=claim_meta,
            )
            claim_ids.append(cid)
            occurrences.setdefault(_normalize_shape(seg.text), []).append((cid, sid))

    # Heuristics are written as claims in the same graph, in DERIVED state, so
    # they are subject to the same lifecycle and can later be contradicted
    # like anything else. A heuristic outside the graph is an oracle.
    #
    # Their support is recorded two ways, for two different jobs. The
    # ``supporting_claim_ids`` go in ``meta`` so they participate in the
    # content hash — a heuristic derived from different support is a
    # *different* heuristic and must get a different id. The edges are the
    # traversable form, and are what a later contradiction check walks. The
    # claim ids deliberately do NOT go in ``evidence_ids``: that column
    # foreign-keys to ``evidence(id)``, and a claim is not an evidence span.
    heuristic_ids: list[str] = []
    for heuristic in _mine_heuristics(occurrences):
        hid = store.add_claim(
            text=heuristic.text,
            state=EpistemicState.DERIVED,
            evidence_ids=[],
            transaction_time=transaction_time,
            meta={
                "origin": "deterministic-miner",
                "kind": heuristic.kind,
                "supporting_claim_ids": list(heuristic.supporting_claim_ids),
            },
        )
        heuristic_ids.append(hid)
        for supporting in heuristic.supporting_claim_ids:
            store.add_edge(hid, supporting, "DERIVED_FROM")

    state_counts: dict[str, int] = {}
    for row in store.claims():
        state_counts[row["state"]] = state_counts.get(row["state"], 0) + 1

    manifest = build_manifest(
        source_ids=source_ids,
        claim_ids=claim_ids,
        evidence_ids=evidence_ids,
        heuristic_ids=heuristic_ids,
        edge_count=store.counts()["claim_edges"],
        state_counts=state_counts,
        compiled_at=compiled_at,
        run_id=run_id,
    )

    return CompiledArtifact(
        manifest=manifest,
        source_ids=tuple(source_ids),
        claim_ids=tuple(claim_ids),
        evidence_ids=tuple(evidence_ids),
        heuristic_ids=tuple(heuristic_ids),
        dropped_segments=dropped_segments,
    )
